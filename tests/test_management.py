import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Template

from test_api import service
import management
import guest_tasks


@pytest.fixture
def lab(service, monkeypatch):
    app, client = service
    class FakePVE:
        def __init__(self):
            self.writes = []
            self.status = 'running'
            self.changed = False
            self.fail_snapshot = False
        def resources(self): return [dict(type='qemu',vmid=i,node='pve',name='guest-'+str(i),status=self.status) for i in (201,202)]
        def call(self, method, path, **kw):
            if method=='GET':
                if path.endswith('/config'): return {'smbios1':'uuid='+('new' if self.changed else path),'digest':'abc'}
                if '/agent/' in path: return {'result':[{'ip-addresses':[{'ip-address-type':'ipv4','ip-address':'192.0.2.20'}]}]}
                if path.endswith('/snapshot'): return [{'name':'current'},{'name':'before'}]
                raise AssertionError(path)
            self.writes.append((method,path,kw))
            if path.endswith('/snapshot') and self.fail_snapshot: raise RuntimeError('Snapshot unsupported')
            return 'UPID:fake'
        def wait(self,*args): pass
    fake = FakePVE()
    monkeypatch.setattr(management.operations,'Proxmox',lambda:fake)
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
    monkeypatch.setattr(management.socket,'create_connection',lambda *a,**kw:Connection())
    runs=[]
    monkeypatch.setattr(management,'run_guest',lambda b,c,t,log,readiness=False:runs.append((b,c,t,readiness)))
    return app,client,{'Authorization':'Bearer '+app.API_TOKEN},fake,runs


def setup_guest(app, platform='windows'):
    cred=app.manager.save('credentials',dict(name='Lab guest',platform=platform,username='admin',password='Super-secret-Guest#123'))
    app.manager.save('connections',dict(node='pve',vmid=201,platform=platform,credential_id=cred['id'],host='192.0.2.20',fingerprint='SHA256:'+'a'*43))
    return cred


def plan(app, operation='tasks', **values):
    return app.manager.create_plan(dict(operation=operation,targets=[dict(node='pve',vmid=201)],tasks=[{'id':'install-chocolatey'}],**values))


def test_credentials_are_encrypted_write_only_and_reference_protected(lab):
    app,client,h,_,_=lab
    assert client.get('/api/manage/credentials').status_code==401
    cred=setup_guest(app)
    assert cred['has_password'] and 'password' not in cred
    text=client.get('/api/manage/credentials',headers=h).get_data(as_text=True)
    assert 'Super-secret' not in text
    with app.db() as con:
        raw=''.join(r['value'] for r in con.execute('SELECT value FROM records'))
    assert 'Super-secret' not in raw and '192.0.2.20' not in raw
    edited=app.manager.save('credentials',dict(id=cred['id'],name='Renamed',username='admin',platform='windows',password=''))
    assert edited['has_password']
    assert app.manager.get('credentials',cred['id'])['password']=='Super-secret-Guest#123'
    assert client.post(f'/api/manage/credentials/{cred["id"]}/delete',json={},headers=h).status_code==400


def test_one_off_run_uses_each_machine_login_and_freezes_inputs(lab):
    app,client,headers,fake,runs=lab
    first=setup_guest(app)
    second=app.manager.save('credentials',dict(name='Second guest',platform='windows',username='guest',password='second-password',
        ca_pem='-----BEGIN CERTIFICATE-----\ntest only\n-----END CERTIFICATE-----'))
    source='- name: Manage spooler\n  ansible.windows.win_service:\n    name: "{{ proxmate_inputs.service }}"\n    state: started'
    targets=[dict(node='pve',vmid=i) for i in (201,202)]
    script=dict(kind='ansible',platform='windows',body=source,parameters={'service':'Service name'},params={'service':'Spooler'})
    def review(**changes):
        return client.post('/api/simple/existing-plan',headers=headers,json=dict(targets=targets,one_off={**script,**changes},credential_id=second['id']))
    assert review(body='- hosts: all\n  tasks: []').status_code==400
    assert review(params={}).status_code==400
    assert app.manager.find('connections','pve:202') is None
    result=review()
    assert result.status_code==200,result.json
    plan=result.json
    assert [t['credential_name'] for t in plan['targets']]==['Lab guest','Second guest']
    assert plan['tasks']==[{'name':'One-off script','params':{'service':'Spooler'}}] and plan['dangerous']
    assert app.manager.all('tasks')==[] and not runs and not fake.writes
    assert source not in result.text and 'second-password' not in result.text
    body=dict(plan_id=plan['id'],confirmed=True)
    assert client.post('/api/manage/run',json=body,headers=headers).status_code==400
    assert client.post('/api/manage/run',json={**body,'risk_confirmed':True},headers=headers).status_code==202
    assert app.run_next_job()
    executed=[(cred['id'],tasks[0]['vars']['proxmate_inputs']) for _,cred,tasks,probe in runs if not probe]
    assert executed==[(first['id'],{'service':'Spooler'}),(second['id'],{'service':'Spooler'})]
    assert app.manager.get('connections','pve:201')['credential_id']==first['id']
    assert app.manager.all('tasks')==[]


def test_failed_mixed_platform_review_does_not_save_discovered_login(lab):
    app,client,headers,_,_=lab
    setup_guest(app,'linux')
    payload=dict(targets=[dict(node='pve',vmid=i) for i in (201,202)],tasks=[{'id':'install-chocolatey'}],
        credential=dict(platform='windows',username='guest',password='unused',ca_pem='-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----'))
    reply=client.post('/api/simple/existing-plan',headers=headers,json=payload)
    assert reply.status_code==400
    assert len(app.manager.all('credentials'))==1 and app.manager.find('connections','pve:202') is None


def test_review_freezes_targets_and_task_body_and_requires_confirmation(lab):
    app,client,h,fake,runs=lab
    setup_guest(app)
    script=app.manager.save('tasks',dict(name='Custom',kind='powershell',platform='windows',body='Write-Output "original"'))
    p=app.manager.create_plan(dict(operation='tasks',targets=[dict(node='pve',vmid=201)],tasks=[{'id':script['id']}]))
    assert p['dangerous'] and not fake.writes
    assert client.post('/api/manage/run',json={'plan_id':p['id'],'confirmed':True},headers=h).status_code==400
    body={'plan_id':p['id'],'confirmed':True,'risk_confirmed':True}
    first=client.post('/api/manage/run',json=body,headers=h)
    assert first.status_code==202
    assert client.post('/api/manage/run',json=body,headers=h).json==first.json
    assert len(client.get('/api/jobs',headers=h).json)==1
    app.manager.save('tasks',dict(script,body='Write-Output "changed"'))
    log=[];app.manager.execute(app.manager.load_plan(p['id']),log.append)
    assert runs[-1][2][0]['ansible.windows.win_powershell']['script']=='Write-Output "original"'
    assert 'Super-secret' not in '\n'.join(log)
    assert runs[0][3] is True


def test_snapshot_failure_aborts_tasks_but_reports_other_vm(lab):
    app,_,_,fake,runs=lab
    setup_guest(app);p=plan(app,snapshot_before=True)
    fake.fail_snapshot=True
    log=[]
    with pytest.raises(RuntimeError,match='VMs failed'): app.manager.execute(app.manager.load_plan(p['id']),log.append)
    assert len(runs)==1 and runs[0][3] is True  # Readiness only; no task after failed snapshot.
    assert any('RESULT: failed' in s for s in log)


def test_changed_identity_and_rotated_credentials_abort_before_guest_tasks(lab):
    app,_,_,fake,runs=lab
    cred=setup_guest(app);p=plan(app)
    fake.changed=True
    with pytest.raises(RuntimeError): app.manager.execute(app.manager.load_plan(p['id']),lambda _:None)
    assert not runs and not fake.writes
    fake.changed=False
    app.manager.save('credentials',dict(cred,password='rotated',username='admin'))
    with pytest.raises(RuntimeError): app.manager.execute(app.manager.load_plan(p['id']),lambda _:None)
    assert not runs


def test_bulk_shutdown_never_forces_and_continues_after_failure(lab,monkeypatch):
    app,_,_,fake,_=lab
    p=app.manager.create_plan({'operation':'shutdown','targets':[{'node':'pve','vmid':201},{'node':'pve','vmid':202}]})
    original=fake.call
    def fail(method,path,**kw):
        value=original(method,path,**kw)
        if path.endswith('201/status/shutdown'): raise RuntimeError('Timed out')
        return value
    monkeypatch.setattr(fake,'call',fail)
    logs=[]
    with pytest.raises(RuntimeError):app.manager.execute(app.manager.load_plan(p['id']),logs.append)
    assert len(fake.writes)==2
    assert all(w[1].endswith('/shutdown') and w[2]['forceStop']==0 for w in fake.writes)
    assert logs[-1]=='Report: 1 succeeded, 1 failed.'


def test_resize_and_restore_require_stopped_vms_and_force_confirmation(lab):
    app,client,h,fake,_=lab
    with pytest.raises(ValueError,match='Shut down'):plan(app,'resize',cores=4,memory=8192)
    fake.status='stopped';p=plan(app,'resize',cores=4,memory=8192)
    app.manager.execute(app.manager.load_plan(p['id']),lambda _:None)
    assert fake.writes[-1][2]=={'cores':4,'sockets':1,'memory':8192,'digest':'abc'}
    p=plan(app,'restore',snapshot='before')
    assert client.post('/api/manage/run',json={'plan_id':p['id'],'confirmed':True},headers=h).status_code==400
    p=plan(app,'stop')
    assert p['dangerous']


def test_group_membership_is_frozen_and_platform_mismatch_rejected(lab):
    app,_,_,fake,_=lab
    group=app.manager.save('groups',dict(name='Web',targets=[{'node':'pve','vmid':201}]))
    p=app.manager.create_plan(dict(operation='start',group_ids=[group['id']]))
    app.manager.save('groups',dict(group,targets=[{'node':'pve','vmid':202}]))
    app.manager.execute(app.manager.load_plan(p['id']),lambda _:None)
    assert fake.writes[-1][1].endswith('/201/status/start')
    setup_guest(app,'linux')
    with pytest.raises(ValueError,match='platform'):plan(app)


def test_schedule_disabled_then_atomic_no_overlap_and_no_catch_up(lab):
    app,_,_,_,_=lab
    p=plan(app,'shutdown')
    s=app.manager.save('schedules',dict(name='Night',plan_id=p['id'],confirmed=True,interval_minutes=15))
    due=s['next_run'];app.manager.tick(due)
    with app.db() as con:assert con.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    s=app.manager.save('schedules',dict(id=s['id'],name='Night',confirmed=True,interval_minutes=15,enabled=True))
    due=s['next_run'];app.manager.tick(due);app.manager.tick(due)
    with app.db() as con:assert con.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
    app.manager.tick(due+900)
    assert 'still active' in app.manager.get('schedules',s['id'])['last_report']
    with app.db() as con:con.execute("UPDATE jobs SET status='succeeded'")
    app.manager.tick(due+2100)
    assert 'Missed' in app.manager.get('schedules',s['id'])['last_report']
    with app.db() as con:assert con.execute('SELECT count(*) FROM jobs').fetchone()[0]==1


def test_schedule_window_and_restart_skip(lab,monkeypatch):
    app,_,_,_,_=lab
    p=plan(app,'backup',backup_storage='local')
    s=app.manager.save('schedules',dict(name='Backups',plan_id=p['id'],confirmed=True,interval_minutes=15,enabled=True,window_start=1,window_end=2))
    raw=app.manager.get('schedules',s['id']);raw['next_run']=36000;app.manager.put('schedules',raw)
    app.manager.tick(36000)
    assert 'outside' in app.manager.get('schedules',s['id'])['last_report']
    raw=app.manager.get('schedules',s['id']);raw['next_run']=39000;app.manager.put('schedules',raw)
    monkeypatch.setattr(management.time,'time',lambda:40000)
    manager=management.Manager(app.db,app.encode_payload,app.decode_payload)
    assert manager.get('schedules',s['id'])['next_run']==90000
    assert 'restart' in manager.get('schedules',s['id'])['last_report']


def test_daily_schedule_aligns_to_window_instead_of_skipping_forever():
    # 10:00 UTC + one day is outside 01:00-02:00; move to the next opening.
    assert management.next_occurrence(36000,dict(interval_minutes=1440,window_start=1,window_end=2)) == 176400


def test_queued_maintenance_cannot_start_after_window_closes(lab,monkeypatch):
    app,_,_,fake,_=lab
    p=app.manager.load_plan(plan(app,'shutdown')['id'])
    p['execution_window']=[1,2]
    monkeypatch.setattr(management.time,'time',lambda:36000)
    log=[]
    with pytest.raises(RuntimeError):app.manager.execute(p,log.append)
    assert not fake.writes and any('window closed' in line for line in log)


def test_recipe_omits_unique_id_and_rejects_embedded_password(lab):
    app,_,_,_,_=lab
    config=dict(kind='linux-cloud',node='pve',name='ubuntu',storage='local-lvm',bridge='vmbr0',profile='ubuntu-24.04',ssh_key='ssh-ed25519 AAAA',purpose='vm')
    r=app.manager.save('recipes',dict(name='Ubuntu tools',config=config,tasks=[{'id':'linux-package','params':{'package':'curl'}}]))
    assert 'vmid' not in r['config']
    with pytest.raises(ValueError,match='password'):app.manager.save('recipes',dict(name='Bad',config={'windows':{'admin_password':'secret'}}))


@pytest.mark.parametrize('body',[
    '- hosts: localhost\n  tasks: []',
    '- ansible.builtin.command: whoami\n  delegate_to: localhost',
    '- ansible.builtin.command: whoami\n  vars: {ansible_connection: local}',
    '- ansible.builtin.include_tasks: /secret',
    '!!python/object:bad {}',
])
def test_custom_ansible_rejects_accidental_controller_execution(body):
    with pytest.raises(ValueError):guest_tasks.validate_script(body)


def test_builtins_parameter_validation_and_firewall_separation():
    rd=guest_tasks.compile_task(next(t for t in guest_tasks.BUILTINS if t['id']=='enable-rdp'),{})
    assert 'Set-NetFirewallProfile' not in json.dumps(rd)
    assert 'UserAuthentication' in json.dumps(rd)
    with pytest.raises(ValueError):guest_tasks.compile_task(next(t for t in guest_tasks.BUILTINS if t['id']=='chocolatey-package'),{'package':'a; evil'})


def test_ansible_runner_temporary_secrets_no_output_or_proxmox_environment(monkeypatch):
    seen={}
    monkeypatch.setenv('PROXMOX_PASSWORD','must-not-inherit')
    def run(args,**kwargs):
        root=Path(kwargs['cwd']);seen['root']=root
        assert 'must-not-inherit' not in json.dumps(kwargs['env'])
        assert 'secret-password' in (root/'inventory.yml').read_text()
        assert 'no_log: true' in (root/'playbook.yml').read_text()
        assert kwargs['stdout']==guest_tasks.subprocess.DEVNULL
        assert '--extra-vars' not in args
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(guest_tasks.subprocess,'run',run)
    logs=[]
    guest_tasks.run_guest(dict(host='192.0.2.20',port=5986,platform='windows',verify_tls=True),dict(username='admin',password='secret-password'),[],logs.append,readiness=True)
    assert not seen['root'].exists()
    assert 'secret-password' not in ''.join(logs)


def test_api_contract_references_and_management_auth(service):
    app,client=service
    spec=client.get('/api/openapi.json').json
    def check(value):
        if isinstance(value,dict):
            if '$ref' in value:
                ref=value['$ref'];assert ref.startswith('#/')
                node=spec
                for part in ref[2:].split('/'):node=node[part]
            for item in value.values():check(item)
        elif isinstance(value,list):
            for item in value:check(item)
    check(spec)
    for kind in management.KINDS:
        assert client.get('/api/manage/'+kind).status_code==401
        assert client.post('/api/manage/'+kind,json={}).status_code==401
    assert client.post('/api/manage/plan',json={}).status_code==401
    assert client.post('/api/manage/run',json={}).status_code==401
    assert client.get('/api/manage/snapshots/pve/201').status_code==401


def test_hypervisor_playbook_requires_explicit_confirmation():
    with pytest.raises(ValueError,match='hypervisor'):
        management.operations.validate(dict(action='ansible',node='pve',target='proxmox',playbook='- hosts: all\n  tasks: []'))


def test_review_shows_guest_endpoint_and_mismatch_prevents_wrong_vm_tasks(lab):
    app,client,headers,fake,runs=lab
    setup_guest(app)
    binding=app.manager.get('connections','pve:201')
    app.manager.save('connections',dict(binding,host='192.0.2.30'))
    p=plan(app)
    assert p['targets'][0]['guest_host']=='192.0.2.30'
    assert p['targets'][0]['credential_name']=='Lab guest'
    assert 'Super-secret' not in json.dumps(p)
    job=client.post('/api/manage/run',json=dict(plan_id=p['id'],confirmed=True),headers=headers).json
    assert app.run_next_job()
    detail=client.get(job['url'],headers=headers).json
    assert not runs and not fake.writes
    assert detail['status']=='failed' and 'does not match' in detail['log']
    expected=dict(server_id=app.server.read()['id'],targets=[dict(
        node='pve',vmid=201,reviewed_identity=app.manager.load_plan(p['id'])['targets'][0]['identity'])])
    assert detail['connection_recovery']==expected
    assert 'connection_recovery' not in client.get('/api/jobs?summary=1',headers=headers).json[0]
    # Existing logs use the earlier wording. Only targets in the frozen job can be offered.
    old_log='pve:201 RESULT: failed - Configured endpoint does not match guest-agent IP addresses; check VM connection settings'
    with app.db() as con:
        con.execute('UPDATE jobs SET log=? WHERE id=?',(old_log+'\n'+old_log.replace('201','222'),job['id']))
    assert client.get(job['url'],headers=headers).json['connection_recovery']==expected
    with app.db() as con:
        con.execute('INSERT INTO server_archives VALUES (?,?,?)',('replacement',detail['created']+1,app.encode_payload({})))
    assert 'connection_recovery' not in client.get(job['url'],headers=headers).json


@pytest.mark.parametrize('host',['127.0.0.1','::1','169.254.1.2','0.0.0.0','192.168.1.244','localhost'])
def test_guest_connection_rejects_local_and_hypervisor_addresses(host,monkeypatch):
    monkeypatch.setattr(management.operations,'configured_url',lambda:'https://192.168.1.244:8006')
    with pytest.raises(ValueError):management.address(host)


def test_dns_alias_cannot_route_guest_tasks_to_hypervisor(monkeypatch):
    monkeypatch.setattr(management.operations,'configured_url',lambda:'https://192.168.1.244:8006')
    monkeypatch.setattr(management.socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('192.168.1.244',5986))])
    with pytest.raises(ValueError,match='Proxmox host'):management.endpoint_addresses({'host':'guest.example','port':5986})


@pytest.mark.parametrize('platform,kind,body,line', [
    ('windows','powershell',"Write-Output ok\nif (",2),
    ('linux','powershell',"if (1) {",1),
    ('linux','shell',"echo ok\nif true; then\n",3),
    ('windows','ansible',"- name: demo\n  ansible.windows.win_copy: [",2),
    ('linux','ansible',"- ansible.builtin.command: uptime\n- hosts: all\n",2),
])
def test_script_diagnostics_match_save_and_preserve_library(lab,platform,kind,body,line):
    app,c,h,_,_=lab
    draft=dict(name='Validation test',platform=platform,kind=kind,body=body)
    before=app.manager.all('tasks')
    assert c.post('/api/manage/tasks/check',json=draft).status_code==401
    check=c.post('/api/manage/tasks/check',headers=h,json=draft)
    assert check.status_code==200 and not check.json['valid']
    assert check.json['line']==line and check.json['column']>=1
    saved=c.post('/api/manage/tasks',headers=h,json=draft)
    assert saved.status_code==400 and 'Line ' in saved.json['error']
    assert app.manager.all('tasks')==before


def test_starters_can_be_checked_copied_and_compiled_on_each_guest(lab,tmp_path):
    app,c,h,_,_=lab
    originals={t['id']:t for t in c.get('/api/manage/tasks',headers=h).json if t.get('example')}
    assert {(t['platform'],t['kind']) for t in originals.values()}=={
        ('windows','ansible'),('windows','powershell'),('linux','ansible'),('linux','powershell'),('linux','shell')}
    for ident,task in originals.items():
        assert c.post('/api/manage/tasks/check',headers=h,json=task).json=={'valid':True}
        assert c.post('/api/manage/tasks',headers=h,json=task).status_code==400
        draft={k:v for k,v in task.items() if k!='id'}
        copy=c.post('/api/manage/tasks',headers=h,json=draft).json
        assert copy['id']!=ident and not copy.get('example')
        selected=app.manager.task_selection([{'id':copy['id']}])[0]
        assert selected['item']['dangerous']
        compiled=guest_tasks.compile_task(selected['item'],selected['params'])
        if ident=='example-win-folder':
            assert task['parameters']=={'name':'Folder name'} and selected['params']=={'name':'LabTools'}
            override=app.manager.task_selection([{'id':ident,'params':{'name':'Utilities'}}])[0]
            custom=guest_tasks.compile_task(override['item'],override['params'])
            for steps,expected in [(compiled,r'C:\LabTools'),(custom,r'C:\Utilities')]:
                args=steps[0]['ansible.windows.win_file']
                assert args['state']=='directory'
                assert Template(args['path']).render(steps[0]['vars'])==expected
        if task['platform']=='linux' and task['kind']=='powershell':
            command=compiled[0]['ansible.builtin.command']
            assert command['argv'][0]=='pwsh' and command['stdin']==task['body']
        assert app.manager.task(ident)==task
    marker=tmp_path/'must-remain-absent'
    for kind,body in [('shell',f"echo checked > '{marker.as_posix()}'"),('powershell',f"Set-Content '{marker.as_posix()}' 'checked'")]:
        assert c.post('/api/manage/tasks/check',json=dict(platform='linux',kind=kind,body=body),headers=h).json['valid']
        assert not marker.exists()

def test_powershell_parser_accepts_native_storage_and_pipeline_syntax():
    from script_library import check_script
    check_script('windows','powershell',"Get-Disk | Select-Object Number,SerialNumber,Size\nif (2GB -gt 1KB) { Get-Volume }")
