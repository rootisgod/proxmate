import base64
import concurrent.futures
import importlib
import time
from contextlib import nullcontext
import ipaddress

import pytest

import lab_pack
import management
import operations
from vm_config import lab_machine_identity
from test_api import service
from test_simple_flow import simple_lab


LAB = '/api/simple/labs'
SOURCE = 'local:iso/server.iso'
EDITION = 'Standard (Desktop Experience)'


def import_pack(client, headers, machines, scripts=None, metadata=None, *, version=1):
    manifest = dict(version=version, name='Training room', machines=machines, scripts=metadata or {})
    encoded = base64.b64encode(lab_pack.write_pack(manifest, scripts or {})).decode()
    imported = client.post(LAB+'/import', json={'archive': encoded}, headers=headers)
    assert imported.status_code == 200, imported.json
    return imported.json['id']


def linux(**changes):
    return dict(os='ubuntu', source={'kind': 'cloud'}, **changes)


def deploy(client, headers, review_id):
    response = client.post(LAB+'/run', json=dict(plan_id=review_id, confirmed=True, risk_confirmed=True), headers=headers)
    assert response.status_code == 202, response.json
    return response.json


def test_import_review_and_confirm_freeze_full_lab(simple_lab):
    app, client, headers, _ = simple_lab
    restore_password = 'Fresh Recovery #456!'
    admin_password = 'Fresh Administrator #456!'
    machines = {
        'app01': linux(after=['dc01'], tasks=[
            {'run':'linux-package','version':1,'with':{'package':'curl'}}, {'file':'tasks/tools.yml'}]),
        'dc01': dict(os='windows', source=dict(kind='iso', edition=EDITION), tasks=[
            {'run':'windows-domain-controller','version':1,
             'with':{'domain_name':'training.example.test','dsrm_password':{'prompt':'restore'}}}])}
    body = '- name: Make the training directory\n  ansible.builtin.file:\n    path: /opt/training\n    state: directory\n'
    pack_id = import_pack(client, headers, machines, {'tasks/tools.yml':body},
                          {'tasks/tools.yml':dict(name='Training directory', platform='linux', kind='ansible')})
    draft = dict(pack_id=pack_id, bindings={'dc01':dict(source=SOURCE, edition=7), 'app01':dict(name='web')},
                 secrets={'dc01':dict(password=admin_password, inputs={'restore': restore_password})})
    response = client.post(LAB+'/plan', json=draft, headers=headers)
    assert response.status_code == 200, response.json
    review = response.json
    assert [m['key'] for m in review['machines']] == ['dc01','app01']
    dc, web = [m['plan'] for m in review['machines']]
    assert dc['source'] == {'kind':'iso','value':SOURCE} and dc['iso_storage']=='local'
    assert dc['tasks'][0]['params'] == {'domain_name':'training.example.test'}
    assert web['machines'][0]['name']=='web'
    assert [plan['machines'][0]['vmid'] for plan in (dc, web)] == [202,203]
    assert [t['name'] for t in web['tasks']] == ['Install a Linux package','Training directory']
    assert 'id' not in dc and 'id' not in web
    assert review['dangerous'] and all(s not in response.text for s in (admin_password,restore_password))
    assert client.get('/api/jobs',headers=headers).json==[] and not app.manager.all('credentials')
    for path, data in [('/api/simple/run', dict(plan_id=review['id'], confirmed=True, risk_confirmed=True)),
                       (LAB+'/run', dict(plan_id=review['id'])),
                       (LAB+'/run', dict(plan_id=review['id'], confirmed=True))]:
        assert client.post(path,json=data,headers=headers).status_code==400
    assert not app.manager.all('credentials') and not client.get('/api/jobs',headers=headers).json

    def submit(_):
        return deploy(app.app.test_client(), headers, review['id'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        submitted = list(pool.map(submit, range(3)))
    assert submitted[0]==submitted[1]==submitted[2]
    run = submitted[0]
    assert run['status']=='queued' and [m['status'] for m in run['machines']]==['queued','pending']
    assert all(not m['owned'] for m in run['machines'])
    assert len(app.manager.all('credentials'))==2
    with app.db() as con:
        con.execute("UPDATE plans SET created=0 WHERE id=?", (review['id'],))
        frozen = [app.decode_payload(con.execute('SELECT payload FROM jobs WHERE id=?',(m['job_id'],)).fetchone()[0])
                  for m in run['machines']]
    assert frozen[0]['plan']['tasks'][0]['params']['dsrm_password'] == restore_password
    assert frozen[1]['plan']['tasks'][-1]['item']['body']==body
    assert deploy(client,headers,review['id'])==run
    assert client.get(LAB+'/runs', headers=headers).json==[run]
    assert client.get(LAB+'/runs/'+run['id'], headers=headers).json==run
    assert len(client.get('/api/jobs', headers=headers).json)==2
    assert 'reusable_build' not in client.get(run['machines'][1]['url'],headers=headers).json


@pytest.mark.parametrize('failed',[True, False])
def test_worker_honors_dependencies_and_tracks_owned_builds(simple_lab, monkeypatch, failed):
    app, client, headers, _ = simple_lab
    pack_id = import_pack(client,headers,{'base':linux(), 'child':linux(after=['base']),
                                         'grandchild':linux(after=['child']), 'other':linux()})
    review = client.post(LAB+'/plan',json={'pack_id':pack_id},headers=headers).json
    run = deploy(client,headers,review['id'])
    seen = []
    def build(plan, log, job_id):
        cfg=plan['config']
        seen.append(cfg['name'])
        app.manager.put('builds',dict(id=job_id, node=cfg['node'], vmid=cfg['vmid'], machine_identity='owned'))
        if failed and cfg['name']=='base':
            raise ValueError('Post-create setup failed')
    monkeypatch.setattr(app.simple,'execute',build)
    while app.run_next_job():
        pass
    result=client.get(LAB+'/runs/'+run['id'],headers=headers).json
    states={m['key']:(m['status'],m['owned']) for m in result['machines']}
    if failed:
        assert set(seen)=={'base','other'}
        assert result['status']=='failed'
        assert states==dict(base=('failed',True),child=('blocked',False),grandchild=('blocked',False),other=('succeeded',True))
    else:
        assert seen.index('base') < seen.index('child') < seen.index('grandchild')
        assert result['status']=='succeeded'
        assert set(states.values())=={('succeeded',True)}
    assert deploy(client,headers,review['id'])==result and not app.run_next_job()


def test_pending_lab_rolls_back_on_destination_conflict_and_never_replays_on_restart(simple_lab):
    app, client, h, _ = simple_lab
    pack_id=import_pack(client,h,{'first':linux(),'second':linux(after=['first'])})
    review=client.post(LAB+'/plan',json={'pack_id':pack_id},headers=h).json
    # A competing new binding appeared after the entire pack was reviewed.
    app.manager.put('connections',{'id':'lab:203'})
    conflict=client.post(LAB+'/run',json={'plan_id':review['id'],'confirmed':True},headers=h)
    assert conflict.status_code==400 and 'saved guest connection' in conflict.json['error']
    assert not app.manager.all('credentials') and not client.get('/api/jobs',headers=h).json
    assert not client.get(LAB+'/runs',headers=h).json
    fresh=client.post(LAB+'/plan',json={'pack_id':pack_id},headers=h).json
    run=deploy(client,h,fresh['id'])
    # Crash after the root's durable claim but before its outcome was written.
    with app.db() as con:
        claimed,_=app.job_queue.claim(con,app.decode_payload,time.time(),False)
        assert claimed['id']==run['machines'][0]['job_id']
    importlib.reload(app)
    app.recover_worker_state()
    result=client.get(LAB+'/runs/'+run['id'],headers=h).json
    assert result['status']=='interrupted'
    assert [(m['vmid'],m['status']) for m in result['machines']]==[(202,'interrupted'),(204,'blocked')]
    assert deploy(client,h,fresh['id'])==result and not app.run_next_job()


def test_restart_preserves_only_unclaimed_lab_jobs(simple_lab,monkeypatch):
    app,client,h,_=simple_lab
    pack_id=import_pack(client,h,{'first':linux(),'child':linux(after=['first']),'other':linux()})
    review=client.post(LAB+'/plan',json={'pack_id':pack_id},headers=h).json
    run=deploy(client,h,review['id'])
    regular=client.post('/api/simple/plan',json=dict(os='ubuntu',name='ordinary'),headers=h).json
    ordinary=client.post('/api/simple/run',json={'plan_id':regular['id'],'confirmed':True},headers=h).json['jobs'][0]
    app.recover_worker_state()
    reloaded=client.get(LAB+'/runs/'+run['id'],headers=h).json
    assert [m['status'] for m in reloaded['machines']]==['queued','pending','queued']
    assert client.get(ordinary['url'],headers=h).json['status']=='interrupted'
    seen=[]
    monkeypatch.setattr(app.simple,'execute',lambda plan,log,job_id:seen.append(plan['config']['name']))
    while app.run_next_job():
        pass
    assert sorted(seen)==['child','first','other'] and seen.index('first')<seen.index('child')
    assert client.get(LAB+'/runs/'+run['id'],headers=h).json['status']=='succeeded'


def test_restarted_worker_rejects_changed_host_before_mutation(simple_lab,monkeypatch):
    app,client,h,_=simple_lab
    pack_id=import_pack(client,h,{'first':linux(),'child':linux(after=['first']),'last':linux(after=['child'])})
    review=client.post(LAB+'/plan',json={'pack_id':pack_id},headers=h).json
    run=deploy(client,h,review['id'])
    with app.db() as con:
        app.server.write(con,dict(app.server.read(),id='a-different-server'))
    app.recover_worker_state()
    monkeypatch.setattr(app.simple,'execute',lambda *args:pytest.fail('Old approval executed against another server'))
    assert app.run_next_job() and not app.run_next_job()
    assert client.get(LAB+'/runs/'+run['id'],headers=h).status_code==404
    assert [client.get(m['url'],headers=h).json['status'] for m in run['machines']]==['failed','blocked','blocked']


def test_parked_prerequisite_keeps_its_dependents_out_of_worker(simple_lab, monkeypatch):
    app,client,h,_=simple_lab
    pack_id=import_pack(client,h,{'base':linux(), 'child':linux(after=['base']), 'other':linux()})
    review=client.post(LAB+'/plan',json={'pack_id':pack_id},headers=h).json
    run=deploy(client,h,review['id'])
    seen=[]
    def build(plan, log, job_id):
        cfg=plan['config']
        seen.append(cfg['name'])
        if cfg['name']=='base':
            revision=app.manager.get('credentials',cfg['bootstrap']['credential_id'])['updated']
            app.manager.put('builds',dict(id=job_id,node=cfg['node'],vmid=cfg['vmid'],machine_identity='owned'))
            raise app.WaitForGuest(dict(server_id=run['server_id'],node=cfg['node'],vmid=cfg['vmid'],
                                        identity='owned',credential_revision=revision,connection_revision=None,
                                        deadline=time.time()+600))
    monkeypatch.setattr(app.simple,'execute',build)
    assert app.run_next_job()
    with app.db() as con:
        con.execute('UPDATE job_waits SET next_check=?',(time.time()+60,))
    assert app.run_next_job() and not app.run_next_job()
    current=client.get(LAB+'/runs/'+run['id'],headers=h).json
    assert {m['key']:m['status'] for m in current['machines']}==dict(base='waiting',child='pending',other='succeeded')
    with app.db() as con:
        con.execute('UPDATE job_waits SET next_check=0,deadline=0')
    assert app.run_next_job() and not app.run_next_job()
    result=client.get(LAB+'/runs/'+run['id'],headers=h).json
    assert {m['key']:m['status'] for m in result['machines']}==dict(base='failed',child='blocked',other='succeeded')
    assert seen==['base','other']


def test_lab_wait_survives_restart_and_unlocks_approved_child(simple_lab,monkeypatch):
    app,client,h,_=simple_lab
    pack_id=import_pack(client,h,{'base':linux(),'child':linux(after=['base'])})
    review=client.post(LAB+'/plan',json={'pack_id':pack_id},headers=h).json
    run=deploy(client,h,review['id'])
    seen=[]
    def create(plan, log, job_id):
        cfg=plan['config']
        seen.append(cfg['name'])
        revision=app.manager.get('credentials',cfg['bootstrap']['credential_id'])['updated']
        app.manager.put('builds',dict(id=job_id,node=cfg['node'],vmid=cfg['vmid'],machine_identity='owned'))
        raise app.WaitForGuest(dict(server_id=run['server_id'],node=cfg['node'],vmid=cfg['vmid'],
                                    identity='owned',credential_revision=revision,connection_revision=None,
                                    deadline=time.time()+600))
    monkeypatch.setattr(app.simple,'execute',create)
    assert app.run_next_job()
    app.recover_worker_state()
    status=lambda: client.get(LAB+'/runs/'+run['id'],headers=h).json
    assert [m['status'] for m in status()['machines']]==['waiting','pending']
    def verified(cfg,identity,credential,previous,log):
        assert identity=='owned'
        return dict(node=cfg['node'], vmid=cfg['vmid'], platform='linux', host='192.0.2.11',
                    credential_id=credential['id'],fingerprint='SHA256:'+'a'*43)
    monkeypatch.setattr(app.lifecycle,'probe_bootstrap',verified)
    monkeypatch.setattr(app.simple,'execute',lambda plan,log,j:seen.append(plan['config']['name']))
    while app.run_next_job():
        pass
    assert status()['status']=='succeeded' and seen==['base','child']


def test_fresh_mappings_required_and_pinned_to_host(simple_lab):
    app, client, headers, inv=simple_lab
    pack_id=import_pack(client,headers,{'desktop':dict(os='windows',source=dict(kind='iso',edition='A different edition'))})
    draft=dict(pack_id=pack_id, bindings={'desktop':dict(source=SOURCE,edition=7)},
               secrets={'desktop':dict(password='Fresh Training #456!')})
    for edits, expected in [({'bindings':{}},'Map the installer'),
                            ({'bindings':{'desktop':dict(node='removed',source=SOURCE)}},'server is no longer available'),
                            ({},'must match the pack edition'),
                            ({'bindings':{'desktop':dict(vmid=201,source=SOURCE)}}, 'expected fields')]:
        res=client.post(LAB+'/plan', json={**draft,**edits},headers=headers)
        assert res.status_code==400 and expected in res.json['error']
    assert client.post(LAB+'/plan',json=draft,headers={**headers,'X-Proxmate-Server':'old'}).status_code==409
    assert not app.manager.all('credentials') and not client.get('/api/jobs',headers=headers).json
    assert client.get(LAB+'/runs/missing',headers=headers).status_code==404


def test_whole_lab_review_checks_shared_capacity_and_key_requirement(simple_lab):
    _,client,h,inv=simple_lab
    pack_id=import_pack(client,h,{'first':linux(),'second':linux()})
    inv['details']['lab']['storage'][0]['avail']=48*2**30
    res=client.post(LAB+'/plan',json={'pack_id':pack_id},headers=h)
    assert res.status_code==400 and 'free disk space' in res.json['error']
    key_pack=import_pack(client,h,{'work':linux(settings={'key_login':True})})
    res=client.post(LAB+'/plan',json={'pack_id':key_pack},headers=h)
    assert res.status_code==400 and 'workstation public key' in res.json['error']


@pytest.fixture
def existing_lab(simple_lab,monkeypatch):
    app,client,h,inv=simple_lab
    vm=inv['resources'][1]
    vm['status']='running'
    cfg=dict(name=vm['name'],vmgenid='eef61809-792e-43fb-a9f5-a46135c63fd0',scsi0='disks:vm-201-disk-0')
    class Reader:
        def resources(self): return inv['resources']
        def call(self,method,path,**kw):
            assert method=='GET'
            if path.endswith('/config'): return dict(cfg)
            if path.endswith('/agent/network-get-interfaces'):
                return {'result':[{'ip-addresses':[{'ip-address-type':'ipv4','ip-address':'192.0.2.20'}]}]}
            if path.endswith('/agent/file-read'):
                return {'content':'ssh-ed25519 '+base64.b64encode(b'test-host-key').decode()}
            pytest.fail('Unexpected guest access: '+path)
    monkeypatch.setattr(operations,'Proxmox',Reader)
    monkeypatch.setattr(management.socket,'create_connection',lambda *a,**kw:nullcontext())
    runs=[]
    monkeypatch.setattr(management,'run_guest',lambda b,c,t,log,readiness=False:runs.append((b,c,t,readiness)))
    return app,client,h,cfg,vm,runs


def existing_role(**options):
    return dict(os='linux',source={'kind':'existing'},tasks=[{'run':'linux-package','version':1,'with':{'package':'curl'}}],
                **options)


def existing_input(pack_id, *, credentials=True):
    data=dict(pack_id=pack_id,bindings={'tools':dict(node='lab',vmid=201)})
    if credentials:
        data['secrets']={'tools':{'credential':dict(platform='linux',username='operator',password='Fresh password #123!')}}
    return data


def approve_existing(client, headers, ident, **extra):
    return client.post(LAB+'/run',json=dict(plan_id=ident,confirmed=True,existing_confirmed=True,**extra),headers=headers)


def test_cleanup_preview_checks_current_ownership_and_keeps_shared_logins(existing_lab, monkeypatch):
    app, client, headers, existing_config, existing_vm, guest_calls = existing_lab
    pack = import_pack(client, headers, {'web': linux(), 'tools': existing_role(after=['web'])}, version=2)
    plan = client.post(LAB+'/plan', json=existing_input(pack), headers=headers).json
    run = approve_existing(client, headers, plan['id']).json
    web, tools = run['machines']
    web_config = dict(name='renamed-web', vmgenid='20ea2de9-69b2-47a8-a518-52de9e3a5555')
    def record_build(plan, log, job_id):
        app.manager.put('builds', dict(id=job_id, node=web['node'], vmid=web['vmid'],
                                      machine_identity=lab_machine_identity(web_config)))
    monkeypatch.setattr(app.simple, 'execute', record_build)
    while app.run_next_job():
        pass
    login = app.manager.get('connections', 'lab:201')['credential_id']
    app.manager.put('connections', dict(id='lab:999', credential_id=login))
    resources = [existing_vm, dict(type='qemu', node='lab', vmid=web['vmid'], name='renamed-web', status='stopped')]
    unreadable = set()
    class Inventory:
        def resources(self):
            return resources
        def call(self, method, path):
            assert method == 'GET'
            if path in unreadable:
                raise RuntimeError('upstream failure with private diagnostics')
            return {f"nodes/lab/qemu/{web['vmid']}/config": web_config,
                    'nodes/lab/qemu/201/config': existing_config}[path]
    monkeypatch.setattr(operations, 'Proxmox', Inventory)
    url = LAB+'/runs/'+run['id']+'/cleanup-preview'
    before = client.get('/api/jobs', headers=headers).json
    calls_before = len(guest_calls)
    def preview():
        response = client.get(url, headers=headers)
        assert response.status_code == 200, response.json
        assert 'Fresh password' not in response.text and 'private_key' not in response.text
        return response.json['machines']
    created, existing = preview()
    assert (created['key'], created['current_name'], created['power'], created['candidate']) == ('web', 'renamed-web', 'stopped', True)
    assert existing['key'] == tools['key'] and not existing['candidate'] and 'existing' in existing['reason'].lower()
    assert existing['credential']['protected'] and 'another VM' in existing['credential']['reason']
    config_path = f"nodes/lab/qemu/{web['vmid']}/config"
    unreadable.add(config_path)
    unavailable, existing = preview()
    assert not unavailable['candidate'] and not unavailable['identity_matches'] and not existing['candidate']
    assert 'private diagnostics' not in str(unavailable)
    unreadable.clear()
    for setting in ('protection', 'lock', 'template'):
        web_config[setting] = 1
        assert not preview()[0]['candidate']
        del web_config[setting]
    web_config['vmgenid'] = '1d15bac0-2bba-4b5b-b00b-b87b67eee555'
    replaced, _ = preview()
    assert not replaced['candidate'] and 'identity' in replaced['reason'].lower()
    resources.pop()
    missing, _ = preview()
    assert not missing['candidate'] and missing['power'] is None
    assert client.get('/api/jobs', headers=headers).json == before and len(guest_calls) == calls_before
    assert client.get(LAB+'/runs/unknown/cleanup-preview', headers=headers).status_code == 404


def test_cleanup_preview_protects_unproven_and_unfinished_builds(simple_lab, monkeypatch):
    app, client, headers, _ = simple_lab
    pack = import_pack(client, headers, {'new': linux()})
    plan = client.post(LAB+'/plan', json={'pack_id':pack}, headers=headers).json
    run = deploy(client, headers, plan['id'])
    role, = run['machines']
    config = dict(vmgenid='a091308c-63a6-4fba-a130-258626c26b6f')
    class Reader:
        def resources(self):
            return [dict(type='qemu', node=role['node'], vmid=role['vmid'], status='running')]
        def call(self, method, path):
            assert method == 'GET'
            return config
    monkeypatch.setattr(operations, 'Proxmox', Reader)
    url = LAB+'/runs/'+run['id']+'/cleanup-preview'
    def first():
        return client.get(url, headers=headers).json['machines'][0]
    assert not first()['creation_recorded'] and not first()['candidate']
    build = dict(id=role['job_id'], node=role['node'], vmid=role['vmid'], machine_identity=lab_machine_identity(config))
    app.manager.put('builds', build)
    pending = first()
    assert pending['creation_recorded'] and pending['identity_matches'] and not pending['candidate']
    assert 'unfinished' in pending['reason']
    app.manager.put('builds', dict(build, vmid=999))
    assert not first()['creation_recorded'] and not first()['identity_matches']
    app.manager.put('builds', dict(build, machine_identity=None))
    assert not first()['candidate'] and not first()['identity_matches']
    record = app.manager.get('lab-runs', run['id'])
    app.manager.put('lab-runs', dict(record, server_id='previous-server'))
    assert client.get(url, headers=headers).status_code == 404


def test_mixed_v2_pack_authenticates_and_runs_on_exact_existing_machine(existing_lab,monkeypatch):
    app,c,h,_,_,calls=existing_lab
    pack=import_pack(c,h,{'new':linux(),'tools':existing_role(after=['new'])},version=2)
    reviewed=c.post(LAB+'/plan',json=existing_input(pack),headers=h)
    assert reviewed.status_code==200,reviewed.json
    plan=reviewed.json
    assert plan['existing'] and not plan['dangerous'] and [r['mode'] for r in plan['machines']]==['create','existing']
    existing=plan['machines'][1]['plan']
    assert existing['operation']=='tasks' and existing['targets'][0]['vmid']==201
    assert existing['tasks']==[dict(name='Install a Linux package',params={'package':'curl'})]
    assert not existing['snapshot_before'] and 'id' not in existing and 'Fresh password' not in reviewed.text
    assert not app.manager.all('credentials') and not app.manager.all('connections') and not calls
    for uri in [LAB+'/run','/api/manage/run','/api/simple/run']:
        assert c.post(uri,json=dict(plan_id=plan['id'],confirmed=True,risk_confirmed=True),headers=h).status_code==400
    assert not c.get('/api/jobs',headers=h).json
    run=approve_existing(c,h,plan['id'])
    assert run.status_code==202,run.json
    rows=run.json['machines']
    assert [(m['mode'],m['status']) for m in rows]==[('create','queued'),('existing','pending')]
    assert c.get(rows[1]['url'],headers=h).json['action']=='manage'
    monkeypatch.setattr(app.simple,'execute',lambda plan,log,ident:None)
    while app.run_next_job(): pass
    assert [(b['id'],s[0]['ansible.builtin.package']['name']) for b,_,s,probe in calls if not probe]==[('lab:201','curl')]
    assert [probe for _,_,_,probe in calls]==[True,False]
    status=c.get(LAB+'/runs/'+plan['id'],headers=h).json
    assert status['status']=='succeeded' and not status['machines'][1]['owned']
    assert approve_existing(c,h,plan['id']).json==status and not app.run_next_job()


@pytest.mark.parametrize('change',['identity','disk','login','connection','stopped','template','server'])
def test_existing_target_drift_after_approval_prevents_guest_login_and_tasks(existing_lab, change):
    app,c,h,cfg,vm,calls=existing_lab
    pack=import_pack(c,h,{'tools':existing_role()},version=2)
    review=c.post(LAB+'/plan',json=existing_input(pack),headers=h).json
    job=approve_existing(c,h,review['id']).json['machines'][0]
    if change=='identity': cfg['vmgenid']='f3cde3a6-7c71-4344-8caa-d5adf6dd21b2'
    elif change=='disk': cfg['scsi0']='disks:replaced'
    elif change=='login': app.manager.save('credentials',dict(app.manager.all('credentials')[0],password='Rotated #1234!'))
    elif change=='connection': app.manager.put('connections',dict(app.manager.all('connections')[0],updated=0))
    elif change=='stopped': vm['status']='stopped'
    elif change=='template': vm['template']=1
    else:
        with app.db() as con: app.server.write(con,dict(app.server.read(),id='replaced'))
    assert app.run_next_job() and not calls and not app.run_next_job()
    assert c.get(job['url'],headers=h).json['status']=='failed'


def test_v2_fails_whole_review_without_weak_identity_duplicate_targets_or_discovery_writes(existing_lab):
    app,c,h,cfg,vm,calls=existing_lab
    pack=import_pack(c,h,{'tools':existing_role(),'also':existing_role(after=['tools'])},version=2)
    draft=existing_input(pack)
    draft['bindings']['also']=dict(node='lab',vmid=201)
    draft['secrets']['also']=draft['secrets']['tools']
    for expected in ('different VM ID','stable UUID','running'):
        if expected=='stable UUID': cfg.update(vmgenid='',smbios1='manufacturer=shared')
        if expected=='running': vm['status']='stopped'
        res=c.post(LAB+'/plan',json=draft,headers=h)
        assert res.status_code==400, res.json
        assert expected in res.json['error']
        assert not calls and not app.manager.all('credentials') and not app.manager.all('connections')
    assert not c.get('/api/jobs',headers=h).json


def test_mixed_lab_confirmation_rolls_back_if_guest_connection_was_claimed(existing_lab):
    app,c,h,_,_,calls=existing_lab
    pack=import_pack(c,h,{'new':linux(),'tools':existing_role(after=['new'])},version=2)
    review=c.post(LAB+'/plan',json=existing_input(pack),headers=h).json
    app.manager.put('connections',dict(id='lab:201',updated=7))
    rejected=approve_existing(c,h,review['id'])
    assert rejected.status_code==400 and 'changed during discovery' in rejected.json['error']
    assert app.manager.all('connections')==[dict(id='lab:201',updated=7)]
    assert not app.manager.all('credentials') and not calls
    assert not c.get('/api/jobs',headers=h).json and not c.get(LAB+'/runs',headers=h).json
    with app.db() as con:
        for table in ('lab_jobs','job_dependencies','task_runs'):
            assert not con.execute('SELECT 1 FROM '+table).fetchone()


def test_saved_login_unchanged_and_mandatory_single_address_for_insecure_winrm(existing_lab,monkeypatch):
    app,c,h,_,_,calls=existing_lab
    login=app.manager.save('credentials',dict(name='Existing login',platform='windows',username='Operator',password='Saved #123!'))
    app.manager.save('connections',dict(node='lab',vmid=201,platform='windows',credential_id=login['id'],
                                        host='guest.example.test',verify_tls=False))
    role=dict(os='windows',source={'kind':'existing'},tasks=[{'run':'install-chocolatey','version':1}])
    pack=import_pack(c,h,{'tools':role},version=2)
    draft=existing_input(pack,credentials=False)
    override=c.post(LAB+'/plan',json=dict(draft,secrets={'tools':{'credential_id':login['id']}}),headers=h)
    assert override.status_code==400
    review=c.post(LAB+'/plan',json=draft,headers=h).json
    assert review['machines'][0]['plan']['targets'][0]['credential_name']=='Existing login'
    changed=app.manager.save('credentials',dict(login,password='Rotated #456!'))
    assert changed['updated']!=login['updated']
    rejected=approve_existing(c,h,review['id'])
    assert rejected.status_code==400 and not c.get('/api/jobs',headers=h).json
    monkeypatch.setattr(management,'endpoint_addresses',lambda b:{ipaddress.ip_address(v) for v in ('192.0.2.20','192.0.2.22')})
    next_review=c.post(LAB+'/plan',json=draft,headers=h).json
    job=approve_existing(c,h,next_review['id']).json['machines'][0]
    assert app.run_next_job() and not calls
    assert 'single resolved address' in c.get(job['url'],headers=h).json['log']
    retry=dict(job_id=job['job_id'],target='lab:201')
    failed_retry=c.post('/api/simple/recovery/plan',json=retry,headers=h)
    assert failed_retry.status_code==400 and 'single resolved address' in failed_retry.json['error'] and not calls
    monkeypatch.setattr(management,'endpoint_addresses',lambda b:{ipaddress.ip_address('192.0.2.20')})
    reviewed_retry=c.post('/api/simple/recovery/plan',json=retry,headers=h)
    assert reviewed_retry.status_code==200 and calls[-1][3]
    confirmed=c.post('/api/manage/run',json=dict(plan_id=reviewed_retry.json['id'],confirmed=True),headers=h)
    assert confirmed.status_code==202
    assert app.run_next_job() and [b['host'] for b,_,_,_ in calls]==['192.0.2.20']*3
