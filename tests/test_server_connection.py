import json
import threading
import time
from types import SimpleNamespace

import pytest

from test_api import service
import operations


def draft(app, **changes):
    return dict(url='https://192.0.2.10:8006',username='root@pam',password='draft-secret',verify_tls=True,
                ssh_user='root',ssh_fingerprint='',expected_id=app.server.read().get('id'),**changes)


def auth(app, **extra): return {'Authorization':'Bearer '+app.API_TOKEN,**extra}


def fake_probe(monkeypatch):
    calls=[]
    class Fake:
        def __init__(self,config):
            calls.append(('auth',config['url']))
            self.session=SimpleNamespace(close=lambda:None)
        def call(self,method,path):
            calls.append((method,path))
            return {'version':'9.0'} if path=='version' else [{'node':'lab'}]
    monkeypatch.setattr(operations,'Proxmox',Fake)
    return calls


def test_first_setup_test_save_restart_and_write_only_password(service,monkeypatch):
    app,c=service
    with app.db() as con: con.execute('DELETE FROM server_connection')
    assert c.get('/api/server').status_code==401
    assert c.get('/api/server',headers=auth(app)).json=={'configured':False,'connected':False,'has_password':False}
    calls=fake_probe(monkeypatch)
    d=draft(app)
    result=c.post('/api/server/test',json=d,headers=auth(app))
    assert result.status_code==200 and result.json['nodes']==['lab']
    assert app.server.read()=={}  # Testing never persists credentials or enables operations.
    result=c.post('/api/server/connect',json=d,headers=auth(app))
    assert result.status_code==200 and result.json['connected']
    assert 'password' not in result.json and 'draft-secret' not in result.text
    assert calls==[('auth',d['url']),('GET','version'),('GET','nodes')]*2
    with app.db() as con:
        stored=con.execute('SELECT value FROM server_connection').fetchone()['value']
        assert 'draft-secret' not in stored and stored.startswith('encrypted:')
    from server_connection import ServerConnection
    reopened=ServerConnection(app.db,app.encode_payload,app.decode_payload)
    assert reopened.current()['password']=='draft-secret'


def test_disconnect_reconnect_and_stale_client(service,monkeypatch):
    app,c=service;fake_probe(monkeypatch);ident=app.server.read()['id']
    r=c.post('/api/server/disconnect',json={'expected_id':ident},headers=auth(app))
    assert r.status_code==200 and not r.json['connected']
    with pytest.raises(ValueError,match='Connect'): app.server.current()
    p={'action':'start','node':'pve','vmid':201}
    assert c.post('/api/jobs',json=p,headers=auth(app)).status_code==409
    d=draft(app);d['password']=''
    assert c.post('/api/server/connect',json=d,headers=auth(app)).status_code==200
    assert app.server.read()['id']==ident
    assert c.post('/api/jobs',json=p,headers=auth(app,**{'X-Proxmate-Server':'stale'})).status_code==409
    assert c.post('/api/jobs',json=p,headers=auth(app,**{'X-Proxmate-Server':''})).status_code==409
    assert c.post('/api/jobs',json=p,headers=auth(app)).status_code==202


@pytest.mark.parametrize('blocking',['queued','running','schedules','backup-policies'])
def test_connection_change_refuses_pending_work(service,monkeypatch,blocking):
    app,c=service;calls=fake_probe(monkeypatch);old=app.server.read()
    with app.db() as con:
        if blocking in ('queued','running'):
            con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',('pending','start',blocking,0,0,app.encode_payload({'action':'start'}),''))
        else: con.execute('INSERT INTO records VALUES (?,?,?)',(blocking,'policy',app.encode_payload({'enabled':True})))
    for path,data in [('disconnect',{'expected_id':old['id']}),('connect',draft(app))]:
        r=c.post('/api/server/'+path,json=data,headers=auth(app))
        assert r.status_code==400 and ('finish' in r.json['error'] or 'Disable' in r.json['error'])
    assert app.server.read()==old and calls==[]


def test_replacement_archives_bound_data_and_invalidates_plans(service,monkeypatch):
    app,c=service;fake_probe(monkeypatch);old=app.server.read()
    with app.db() as con:
        for kind in ('credentials','tasks','connections','groups','recipes','schedules','template-versions','backup-policies'):
            con.execute('INSERT INTO records VALUES (?,?,?)',(kind,'test',app.encode_payload({'id':'test','enabled':False})))
        for table in ('plans','lifecycle_plans'):
            con.execute('INSERT INTO '+table+' VALUES (?,?,?)',('old-plan',time.time(),app.encode_payload({'vmid':201})))
    d=draft(app);d['url']='https://192.0.2.11:8006'
    assert c.post('/api/server/connect',json=d,headers=auth(app)).status_code==400
    d['replace_confirmed']=True
    r=c.post('/api/server/connect',json=d,headers=auth(app))
    assert r.status_code==200 and r.json['id']!=old['id']
    with app.db() as con:
        assert {row['kind'] for row in con.execute('SELECT * FROM records')}=={'credentials','tasks'}
        assert not con.execute('SELECT * FROM plans').fetchall()
        assert not con.execute('SELECT * FROM lifecycle_plans').fetchall()
        archive=app.decode_payload(con.execute('SELECT value FROM server_archives').fetchone()['value'])
        assert archive['server']==old and len(archive['records'])==6
    assert c.post('/api/server/disconnect',json={'expected_id':old['id']},headers=auth(app)).status_code==400
    assert c.post('/api/manage/run',json={'plan_id':'old-plan','confirmed':True},headers=auth(app,**{'X-Proxmate-Server':old['id']})).status_code==409
    assert operations.configured_url()==d['url']


def test_new_host_never_inherits_old_password_and_failed_probe_is_redacted(service,monkeypatch):
    app,c=service;old=app.server.read();d=draft(app)
    d.update(url='https://192.0.2.11:8006',password='',replace_confirmed=True)
    r=c.post('/api/server/test',json=d,headers=auth(app))
    assert r.status_code==400 and 'password' in r.json['error']
    def fail(config): raise RuntimeError('server echoed '+config['password'])
    monkeypatch.setattr(operations,'Proxmox',fail)
    d['password']='new-secret'
    for path in ('test','connect'):
        r=c.post('/api/server/'+path,json=d,headers=auth(app))
        assert r.status_code==400 and 'new-secret' not in r.text
    assert app.server.read()==old


@pytest.mark.parametrize('url',['http://host:8006','https://user:secret@host:8006','https://host/path','https://host/?a=1','https://host:bad','https://host/#fragment'])
def test_invalid_connection_url_never_contacts_host(service,monkeypatch,url):
    app,c=service;calls=fake_probe(monkeypatch);d=draft(app);d['url']=url
    assert c.post('/api/server/test',json=d,headers=auth(app)).status_code==400
    assert calls==[]


def test_auth_csrf_and_cross_origin_apply_to_settings(service):
    app,c=service
    assert c.post('/api/server/connect',json=draft(app)).status_code==401
    c.post('/api/login',json={'password':'test-admin-password'})
    assert c.post('/api/server/disconnect',json={'expected_id':app.server.read()['id']}).status_code==403
    assert c.post('/api/server/test',json=draft(app),headers=auth(app,Origin='https://other.example')).status_code==403


def test_hypervisor_exclusion_uses_saved_server(service,monkeypatch):
    app,c=service;fake_probe(monkeypatch);d=draft(app);d.update(url='https://192.0.2.11:8006',replace_confirmed=True)
    assert c.post('/api/server/connect',json=d,headers=auth(app)).status_code==200
    import management
    with pytest.raises(ValueError,match='Proxmox host'): management.address('192.0.2.11')


def test_changes_wait_for_request_lock(service,monkeypatch):
    app,c=service;fake_probe(monkeypatch);completed=threading.Event();started=threading.Event()
    def change():
        started.set()
        app.server.change({'expected_id':app.server.read()['id']},disconnect=True)
        completed.set()
    with app.server.lock:
        thread=threading.Thread(target=change);thread.start();assert started.wait(1)
        assert not completed.wait(.05)
    thread.join(2)
    assert completed.is_set()
