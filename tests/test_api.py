import importlib
import os
import re
import time
from pathlib import Path

import pytest


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    password = tmp_path / 'password'
    password.write_text('test-admin-password')
    monkeypatch.setenv('APP_PASSWORD_FILE', str(password))
    monkeypatch.setenv('PROXMOX_URL', 'https://192.0.2.10:8006')
    monkeypatch.setenv('PROXMOX_PASSWORD_FILE', str(password))
    monkeypatch.setenv('PROXMOX_SSH_FINGERPRINT', '')
    import app
    importlib.reload(app)
    from flask.testing import FlaskClient
    class SelectedServerClient(FlaskClient):
        def open(self,*args,**kwargs):
            headers = dict(kwargs.get('headers') or {})
            headers.setdefault('X-Proxmate-Server',app.server.read().get('id',''))
            kwargs['headers'] = headers
            return super().open(*args,**kwargs)
    app.app.test_client_class = SelectedServerClient
    return app, app.app.test_client()


def test_auth_and_csrf(service):
    app, client = service
    assert client.get('/api/jobs').status_code == 401
    assert client.post('/api/login', json={'password': 'wrong'}).status_code == 401
    login = client.post('/api/login', json={'password': 'test-admin-password'})
    assert login.status_code == 200
    assert client.get('/api/jobs').status_code == 200
    assert client.post('/api/jobs', json={'action': 'start', 'vmid': 9000, 'node': 'pve'}).status_code == 403
    assert client.post('/api/logout', json={}, headers={'X-CSRF-Token': login.json['csrf']}).status_code == 200
    assert client.get('/api/jobs').status_code == 401


def test_editor_styles_have_a_fresh_document_nonce(service):
    _, client = service
    nonces = set()
    for path in ('/', '/', '/static/index.html'):
        page = client.get(path)
        assert page.status_code == 200
        nonce = re.search(r'<link rel="stylesheet"[^>]* nonce="([\w-]+)"', page.text).group(1)
        assert nonce not in nonces
        nonces.add(nonce)
        policy = page.headers['Content-Security-Policy'].split('; ')
        assert f"style-src 'self' 'nonce-{nonce}'" in policy and "script-src 'self'" in policy
        assert page.cache_control.no_store
    assert 'nonce-' not in client.get('/static/style.css').headers['Content-Security-Policy']


def test_empty_secrets_fail_closed(service):
    app, client = service
    Path(os.environ['APP_PASSWORD_FILE']).write_text('  \n')
    with pytest.raises(RuntimeError, match='password file is empty'):
        app.admin_password()
    assert client.post('/api/login', json={'password': ''}).status_code >= 400
    assert client.get('/api/jobs').status_code == 401
    (app.DATA / 'session-key').write_text('')
    with pytest.raises(RuntimeError, match='Empty secret file'):
        app.persistent_secret('session-key')


def test_token_idempotency_and_cross_origin(service):
    app, client = service
    headers = {'Authorization': 'Bearer ' + app.API_TOKEN, 'Idempotency-Key': 'test-start'}
    payload = {'action': 'start', 'vmid': 9000, 'node': 'pve'}
    first = client.post('/api/jobs', json=payload, headers=headers)
    assert first.status_code == 202
    assert client.post('/api/jobs', json=payload, headers=headers).json == first.json
    assert len(client.get('/api/jobs', headers=headers).json) == 1
    assert client.post('/api/jobs', json=dict(payload, vmid=9001), headers=headers).status_code == 409
    assert client.post('/api/jobs', json=payload, headers=dict(headers, Origin='https://evil.example')).status_code == 403


def test_job_summaries_and_logs_are_readable_while_host_lock_is_held(service):
    import concurrent.futures
    app, client = service
    auth = {'Authorization':'Bearer '+app.API_TOKEN}
    queued = client.post('/api/jobs', json={'action':'start','node':'lab','vmid':105}, headers=auth)
    job_id = queued.json['id']
    detail = client.get('/api/jobs/'+job_id, headers=auth).json
    assert detail['log'] and client.get('/api/jobs', headers=auth).json == [detail]
    def read_progress():
        other = app.app.test_client()
        assert other.get('/api/server', headers=auth).status_code == 200
        assert other.get('/api/jobs/'+job_id, headers=auth).json == detail
        return other.get('/api/jobs?summary=1', headers=auth).json
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        with app.server.lock:
            summaries = pool.submit(read_progress).result(timeout=3)
    assert summaries == [{key:value for key,value in detail.items() if key!='log'}]


@pytest.mark.parametrize('payload', [
    {'action': 'start', 'node': 'pve;reboot', 'vmid': 9000},
    {'action': 'start', 'node': 'pve', 'vmid': True},
    {'action': 'upload-iso', 'node': 'pve', 'storage': 'local', 'filename': '../secret.iso'},
    {'action': 'convert-template', 'node': 'pve', 'vmid': 9000},
    {'action': 'ansible', 'node': 'pve', 'inventory': 'localhost', 'playbook': '!!python/object:bad {}'},
])
def test_unsafe_input_rejected(service, payload):
    app, client = service
    r = client.post('/api/jobs', json=payload, headers={'Authorization': 'Bearer ' + app.API_TOKEN})
    assert r.status_code == 400
    assert client.get('/api/jobs', headers={'Authorization': 'Bearer ' + app.API_TOKEN}).json == []


def test_existing_guest_never_overwritten(monkeypatch):
    import operations
    class Fake:
        def resources(self): return [{'vmid': 9000, 'node': 'pve', 'type': 'qemu'}]
        def call(self, *a, **kw): pytest.fail('Must not mutate existing guest')
    monkeypatch.setattr(operations, 'Proxmox', Fake)
    with pytest.raises(ValueError, match='already exists'):
        operations.execute({'action': 'linux-template', 'node': 'pve', 'vmid': 9000}, lambda x: None)


def test_failed_task_not_success():
    from operations import Proxmox
    p = object.__new__(Proxmox)
    p.call = lambda *a, **kw: {'status': 'stopped', 'exitstatus': 'disk full'}
    with pytest.raises(RuntimeError, match='disk full'):
        p.wait('pve', 'UPID:test', lambda x: None)


def test_only_worker_startup_reconciles_jobs_and_overdue_schedules(service):
    app, client = service
    auth = {'Authorization': 'Bearer ' + app.API_TOKEN}
    first = client.post('/api/jobs', json={'action': 'start', 'vmid': 9000, 'node': 'pve'}, headers=auth)
    overdue = dict(id='overdue', next_run=1, interval_minutes=15, window_start=0, window_end=24)
    for kind in ('schedules', 'backup-policies'):
        app.manager.put(kind, overdue)
    importlib.reload(app)
    reopened = app.app.test_client()
    assert reopened.get(first.json['url'], headers=auth).json['status'] == 'queued'
    for kind in ('schedules', 'backup-policies'):
        assert app.manager.get(kind, 'overdue') == overdue

    app.recover_worker_state()
    status = reopened.get(first.json['url'], headers=auth).json
    assert status['status'] == 'interrupted'
    assert 'Inspect Proxmox' in status['log']
    for kind in ('schedules', 'backup-policies'):
        record = app.manager.get(kind, 'overdue')
        assert record['next_run'] > time.time()
        assert 'restart' in record['last_report']


def test_job_response_omits_inventory_credentials(service):
    app, client = service
    auth = {'Authorization': 'Bearer ' + app.API_TOKEN}
    job = client.post('/api/jobs', json={'action': 'ansible', 'node': 'pve',
        'inventory': 'host ansible_password=private-test-value',
        'playbook': '- hosts: all\n  tasks: []'}, headers=auth)
    assert job.status_code == 202
    result = client.get(job.json['url'], headers=auth)
    assert 'private-test-value' not in result.text
    assert result.json['target'] == {'node': 'pve'}


def test_running_guest_cannot_be_converted(monkeypatch):
    import operations
    class Fake:
        def resources(self): return [
            {'type': 'node', 'node': 'pve', 'status': 'online'},
            {'type': 'qemu', 'node': 'pve', 'vmid': 9001, 'status': 'running'}]
        def call(self, method, path, **kw):
            assert (method, path) == ('GET', 'nodes/pve/qemu/9001/status/current')
            return {'status':'running'}
    monkeypatch.setattr(operations, 'Proxmox', Fake)
    with pytest.raises(ValueError, match='must be stopped'):
        operations.execute({'action': 'convert-template', 'node': 'pve', 'vmid': 9001, 'prepared': True}, lambda x: None)
