import copy
import io
import json
import struct
import time
from types import SimpleNamespace

import pycdlib
import pytest
import yaml

import bootstrap
import media
import operations
import simple_flow
import template_finish
from test_api import service


@pytest.fixture
def simple_lab(service,monkeypatch):
    app,client=service
    with app.db() as con:
        config=app.server.read();config['ssh_fingerprint']='SHA256:'+'a'*43;app.server.write(con,config)
    inv={'nodes':[{'node':'lab','status':'online'}], 'next_id':202,'local_isos':[{'name':'server.iso','bytes':1}],
        'resources':[{'type':'node','node':'lab','status':'online'},{'type':'qemu','node':'lab','vmid':201,'name':'existing'}],
        'details':{'lab':{'storage':[{'storage':'disks','active':1,'content':'images','avail':10**12},{'storage':'local','active':1,'content':'iso','avail':10**11}],
            'network':[{'type':'bridge','iface':'vmbr0','cidr':'192.0.2.10/24'}],
            'isos':[{'volid':'local:iso/server.iso'},{'volid':'local:iso/virtio-win-0.1.266.iso'}]}}}
    def build_inventory(*, include_backups, only_node=None):
        assert not include_backups
        return copy.deepcopy(inv)
    monkeypatch.setattr(operations,'inventory',build_inventory)
    monkeypatch.setattr(media,'inspect_source',lambda *a:dict(identity='media-v1',editions=[dict(index=7,name='Windows Server Standard',label='Standard (Desktop Experience)',desktop=True,language='en-US',build='26100',edition_id='ServerStandard')]))
    monkeypatch.setattr(media,'review_guest_agent',lambda *a:'a'*64)
    monkeypatch.setattr(media,'guest_agent_sha256',lambda *a:'a'*64)
    monkeypatch.setattr(media,'verify_ssh_node',lambda *a:None)
    def no_host(*a,**kw): raise AssertionError('No live Proxmox call permitted')
    monkeypatch.setattr(operations,'Proxmox',no_host)
    return app,client,{'Authorization':'Bearer '+app.API_TOKEN},inv


def settings(**changes):
    return dict(os='windows',name='demo',count=3,purpose='vm',source='local:iso/server.iso',edition=7,password='Secret-Setup#123',tasks=[{'id':'install-chocolatey','params':{}},{'id':'chocolatey-package','params':{'package':'7zip'}},{'id':'windows-feature','params':{'feature':'Web-Server'}}],**changes)


def test_discovery_selects_resources_and_real_edition_index(simple_lab):
    app,c,h,inv=simple_lab
    options=c.get('/api/simple/options',headers=h).json
    chosen=options['destinations'][0]
    assert (chosen['storage'],chosen['bridge'],chosen['iso_storage'])==('disks','vmbr0','local')
    assert chosen['management_cidr']=='192.0.2.0/24'
    r=c.post('/api/simple/plan',json=settings(),headers=h)
    assert r.status_code==200,r.json
    assert [m['vmid'] for m in r.json['machines']]==[202,203,204]
    assert [m['name'] for m in r.json['machines']]==['demo-01','demo-02','demo-03']
    assert r.json['edition']=='Standard (Desktop Experience)'
    assert 'Secret-Setup' not in r.text and 'password' not in r.text
    plan=app.manager.load_plan(r.json['id'])
    assert all(cfg['windows']['setup_key']=='TVRH6-WHNXV-R9WG3-9XRFY-MY832' for cfg in plan['configs'])
    assert any('not a licence' in note for note in r.json['notes'])
    assert all(cfg['windows']['image_index']==7 and cfg['bootstrap']['verify'] and cfg['start'] for cfg in plan['configs'])
    assert app.manager.all('credentials')==[]  # Planning does not create a saved login.


def test_batch_submission_is_confirmed_idempotent_and_encrypted(simple_lab):
    app,c,h,inv=simple_lab;r=c.post('/api/simple/plan',json=settings(),headers=h)
    body={'plan_id':r.json['id'],'confirmed':True}
    assert c.post('/api/simple/run',json={'plan_id':body['plan_id']},headers=h).status_code==400
    first=c.post('/api/simple/run',json=body,headers=h)
    assert first.status_code==202
    assert first.json==c.post('/api/simple/run',json=body,headers=h).json
    assert len(app.manager.all('credentials'))==1
    assert len(c.get('/api/jobs',headers=h).json)==3
    with app.db() as con:
        rows=con.execute('SELECT payload FROM jobs').fetchall()
        assert all('Secret-Setup' not in row['payload'] for row in rows)
        payload=app.decode_payload(rows[0]['payload'])
        assert payload['plan']['tasks'][2]['item']['id']=='windows-feature'
    assert 'Secret-Setup' not in c.get('/api/jobs',headers=h).text
    next_plan=c.post('/api/simple/plan',json={**settings(),'name':'second'},headers=h).json
    assert [m['vmid'] for m in next_plan['machines']]==[205,206,207]


def test_build_defaults_follow_verified_node(simple_lab, monkeypatch):
    app, client, headers, inv = simple_lab
    inv['nodes'].insert(0, {'node':'other', 'status':'online'})
    inv['details']['other'] = copy.deepcopy(inv['details']['lab'])
    monkeypatch.setattr(media, 'ssh_node', lambda: 'lab')
    assert client.get('/api/simple/options', headers=headers).json['destinations'][0]['node'] == 'lab'
    plan = client.post('/api/simple/plan', json=settings(), headers=headers)
    assert all(machine['node'] == 'lab' for machine in plan.json['machines'])


@pytest.mark.parametrize(('ostype','expected'), [('win11','windows'), ('l26','linux'), ('other',None)])
def test_guest_platform_hint_is_read_only(simple_lab, monkeypatch, ostype, expected):
    app, client, headers, inv = simple_lab
    calls = []
    def read(method, path):
        calls.append((method, path))
        return {'ostype':ostype, 'description':'private config data'}
    monkeypatch.setattr(operations, 'Proxmox', lambda: SimpleNamespace(call=read,session=SimpleNamespace(close=lambda: None)))
    response = client.post('/api/simple/software', json={'targets':[{'node':'lab','vmid':201},{'node':'lab','vmid':202}]}, headers=headers)
    assert response.json['platforms'] == {'lab:201':expected,'lab:202':expected}
    assert 'private config data' not in response.text
    assert calls == [('GET', 'nodes/lab/qemu/201/config'),('GET','nodes/lab/qemu/202/config')]


def test_saved_scripts_are_frozen_and_require_trust_confirmation(simple_lab):
    app,c,h,inv=simple_lab
    script=app.manager.save('tasks',dict(name='Example',platform='windows',kind='powershell',body="Write-Output 'first'"))
    r=c.post('/api/simple/plan',json={**settings(),'tasks':[{'id':script['id'],'params':{}}]},headers=h)
    assert r.json['dangerous']
    app.manager.save('tasks',dict(script,body="Write-Output 'changed'"))
    assert c.post('/api/simple/run',json={'plan_id':r.json['id'],'confirmed':True},headers=h).status_code==400
    r2=c.post('/api/simple/run',json={'plan_id':r.json['id'],'confirmed':True,'risk_confirmed':True},headers=h)
    assert r2.status_code==202
    with app.db() as con:
        job=app.decode_payload(con.execute('SELECT payload FROM jobs LIMIT 1').fetchone()['payload'])
    assert job['plan']['tasks'][0]['item']['body']=="Write-Output 'first'"


def test_ubuntu_uses_generated_key_and_seed_iso_without_snippets(simple_lab):
    app,c,h,inv=simple_lab
    r=c.post('/api/simple/plan',json=dict(os='ubuntu',name='ubuntu',count=1,username='ubuntu',tasks=[]),headers=h)
    assert r.status_code==200,r.json
    plan=app.manager.load_plan(r.json['id']);cfg=plan['configs'][0]
    assert cfg['bootstrap']['seed_storage']=='local' and 'snippet_storage' not in cfg['bootstrap']
    assert cfg['ssh_key'].startswith('ssh-ed25519 ')
    assert 'PRIVATE KEY' not in r.text and 'PRIVATE KEY' in plan['credential']['private_key']


@pytest.mark.parametrize('change',[{'edition':2},{'count':0},{'count':21},{'name':'existing','count':1},{'storage':'missing'},{'bridge':'missing'},{'password':''},{'purpose':'template','count':3}])
def test_invalid_choices_fail_before_queue(simple_lab,change):
    app,c,h,inv=simple_lab
    r=c.post('/api/simple/plan',json={**settings(),**change},headers=h)
    assert r.status_code==400,r.json
    assert c.get('/api/jobs',headers=h).json==[]


def test_low_storage_and_stale_server_fail_before_queue(simple_lab):
    app,c,h,inv=simple_lab
    inv['details']['lab']['storage'][0]['avail']=1024
    assert c.post('/api/simple/plan',json=settings(),headers=h).status_code==400
    assert c.post('/api/simple/plan',json=settings(),headers={**h,'X-Proxmate-Server':'old-server'}).status_code==409


def test_missing_guest_driver_blocks_before_creation(simple_lab,monkeypatch):
    app,c,h,inv=simple_lab
    monkeypatch.setattr(media,'review_guest_agent',lambda *a:None)
    response=c.post('/api/simple/plan',json=settings(),headers=h)
    assert response.status_code==400 and 'serial driver' in response.json['error']
    assert c.get('/api/jobs',headers=h).json==[]


@pytest.mark.parametrize('flavour,include_agent,expected',[('2k25',True,True),('2k22',True,False),('2k25',False,False)])
def test_virtio_rock_ridge_filenames_are_inspected(tmp_path,monkeypatch,flavour,include_agent,expected):
    path=tmp_path/'virtio.iso'
    iso=pycdlib.PyCdlib();iso.new(rock_ridge='1.09')
    iso.add_directory(iso_path='/GUEST_AG',rr_name='guest-agent')
    iso.add_directory(iso_path='/SERIAL',rr_name='vioserial')
    iso.add_directory(iso_path='/SERIAL/WINDOWS',rr_name=flavour)
    iso.add_directory(iso_path='/SERIAL/WINDOWS/AMD64',rr_name='amd64')
    if include_agent: iso.add_fp(io.BytesIO(b'msi'),3,iso_path='/GUEST_AG/QEMUGA.MSI;1',rr_name='qemu-ga-x86_64.msi')
    iso.add_fp(io.BytesIO(b'inf'),3,iso_path='/SERIAL/WINDOWS/AMD64/SERIAL.INF;1',rr_name='vioser.inf')
    iso.write(str(path));iso.close()
    monkeypatch.setattr(media,'source_file',lambda *args:path.open('rb'))
    assert bool(media.review_guest_agent('local:iso/virtio.iso','lab',26100)) is expected
    if include_agent:
        import hashlib
        assert media.guest_agent_sha256('local:iso/virtio.iso','lab')==hashlib.sha256(b'msi').hexdigest()
    else:
        with pytest.raises(ValueError,match='x64 guest agent'):media.guest_agent_sha256('local:iso/virtio.iso','lab')


def test_existing_vm_connection_is_discovered_without_guest_writes(simple_lab,monkeypatch):
    app,c,h,inv=simple_lab;calls=[]
    class Fake:
        def resources(self):return inv['resources']
        def call(self,method,path,**kwargs):
            calls.append((method,path))
            assert method=='GET'
            if path.endswith('/network-get-interfaces'):return {'result':[{'ip-addresses':[{'ip-address':'192.0.2.20','ip-address-type':'ipv4'}]}]}
            if path.endswith('/file-read'):return {'content':'-----BEGIN CERTIFICATE-----\npublic-cert\n-----END CERTIFICATE-----'}
            if path.endswith('/config'):return {'smbios1':'uuid=existing'}
            raise AssertionError(path)
    monkeypatch.setattr(operations,'Proxmox',Fake)
    payload=dict(targets=[{'node':'lab','vmid':201}],tasks=[{'id':'install-chocolatey','params':{}}],credential={'platform':'windows','username':'Administrator','password':'Guest-Password#123'})
    r=c.post('/api/simple/existing-plan',json=payload,headers=h)
    assert r.status_code==200,r.json
    assert r.json['targets'][0]['guest_host']=='192.0.2.20'
    assert 'Guest-Password' not in r.text
    assert app.manager.get('connections','lab:201')['verify_tls'] is True
    assert calls and all(method=='GET' for method,path in calls)


def test_guest_tasks_follow_authenticated_bootstrap_and_never_run_after_failure(simple_lab,monkeypatch):
    app,c,h,inv=simple_lab
    plan_id=c.post('/api/simple/plan',json=settings(),headers=h).json['id']
    full=app.manager.load_plan(plan_id);plan={k:full[k] for k in ('source','media_identity','tasks','purpose')};plan['config']=full['configs'][0]
    events=[]
    monkeypatch.setattr(operations,'execute',lambda *a:events.append('create'))
    monkeypatch.setattr(app.lifecycle,'after_provision',lambda *a:events.append('authenticate'))
    monkeypatch.setattr(app.manager,'create_plan',lambda *a:{'id':'readiness'})
    monkeypatch.setattr(app.manager,'load_plan',lambda *a:{})
    monkeypatch.setattr(app.manager,'execute',lambda *a:events.append('software'))
    monkeypatch.setattr(app.lifecycle,'record_tasks',lambda *a:None)
    monkeypatch.setattr(app.simple,'assert_owned',lambda *a:None)
    monkeypatch.setattr(app.lifecycle,'verified_task_plan',lambda *a:{})
    app.simple.execute(plan,lambda *a:None,'job')
    assert events==['create','authenticate','software']
    events.clear()
    def failed(*a):raise RuntimeError('Guest not ready')
    monkeypatch.setattr(app.lifecycle,'after_provision',failed)
    with pytest.raises(RuntimeError):app.simple.execute(plan,lambda *a:None,'job')
    assert events==['create']


def test_reused_vm_id_cannot_receive_build_followup_tasks(simple_lab,monkeypatch):
    app,c,h,inv=simple_lab
    from lifecycle import machine_identity
    app.manager.put('builds',{'id':'build-id','machine_identity':machine_identity({'smbios1':'uuid=created','vmgenid':'created'})})
    monkeypatch.setattr(operations,'Proxmox',lambda:SimpleNamespace(call=lambda *a,**k:{'smbios1':'uuid=replaced','vmgenid':'new'}))
    with pytest.raises(ValueError,match='replaced'):
        app.simple.assert_owned({'node':'lab','vmid':202},'build-id')


def test_cloud_seed_contains_user_key_tools_and_dhcp(tmp_path):
    path=tmp_path/'seed.iso';bootstrap.cloud_seed(path,'ubuntu','ssh-ed25519 PUBLIC','ubuntu-01')
    iso=pycdlib.PyCdlib();iso.open(str(path))
    try:
        out=io.BytesIO();iso.get_file_from_iso_fp(out,joliet_path='/user-data');d=yaml.safe_load(out.getvalue())
        assert d['users'][0]['ssh_authorized_keys']==['ssh-ed25519 PUBLIC']
        assert 'qemu-guest-agent' in d['packages'] and not d['ssh_pwauth']
        out=io.BytesIO();iso.get_file_from_iso_fp(out,joliet_path='/network-config')
        assert yaml.safe_load(out.getvalue())['ethernets']['lan']['dhcp4'] is True
    finally:iso.close()


def wim(xml):
    data=xml.encode('utf-16');header=bytearray(208);header[:8]=b'MSWIM\0\0\0';struct.pack_into('<QQQ',header,72,len(data),208,len(data))
    return io.BytesIO(bytes(header)+data)


def test_editions_are_read_from_metadata_and_not_guessed():
    result=media.wim_editions(wim('<WIM><IMAGE INDEX="7"><NAME>Windows Server Standard</NAME><WINDOWS><ARCH>9</ARCH><INSTALLATIONTYPE>Server Core</INSTALLATIONTYPE><EDITIONID>ServerStandard</EDITIONID><VERSION><BUILD>26100</BUILD></VERSION></WINDOWS></IMAGE><IMAGE INDEX="9"><NAME>Windows Server Datacenter</NAME><WINDOWS><ARCH>9</ARCH><INSTALLATIONTYPE>Server</INSTALLATIONTYPE></WINDOWS></IMAGE></WIM>'))
    assert [(e['index'],e['desktop']) for e in result['editions']]==[(7,False),(9,True)]
    assert result['editions'][0]['edition_id']=='ServerStandard'
    assert result['editions'][0]['build']=='26100'
    assert 'no desktop' in result['editions'][0]['label'] and 'Desktop Experience' in result['editions'][1]['label']
    with pytest.raises(ValueError):media.wim_editions(io.BytesIO(b'not a WIM'))
    with pytest.raises(ValueError):media.wim_editions(wim('<!DOCTYPE WIM><WIM/>'))


def test_ssh_key_mismatch_points_to_server_settings_without_bypassing_verification(monkeypatch):
    import paramiko
    def wrong_key(*args):raise paramiko.SSHException('Proxmox SSH host fingerprint mismatch')
    monkeypatch.setattr(media,'source_file',wrong_key)
    with pytest.raises(ValueError,match='fingerprint verified from its Proxmox console'):
        media.inspect_source('local:iso/server.iso','lab')


def test_local_proxmox_storage_is_not_confused_with_library(simple_lab,monkeypatch):
    app,c,h,inv=simple_lab
    with pytest.raises(AssertionError,match='live Proxmox'):
        with media.source_file('local:iso/server.iso','lab'):pass


@pytest.mark.parametrize('exitcode',[0,1])
def test_template_conversion_only_follows_successful_generalization(monkeypatch,exitcode):
    cfg=dict(kind='windows',node='lab',vmid=202,storage='disks');events=[]
    config={'smbios1':'uuid=test','vmgenid':'test'}
    class Fake:
        def call(self,method,path,**kw):
            events.append((method,path,kw))
            if path.endswith('/config'):return config
            if path.endswith('/agent/exec'):return {'pid':4}
            if path.endswith('/agent/exec-status'):return {'exited':True,'exitcode':exitcode}
            if path.endswith('/status/current'):return {'status':'stopped'}
        def wait(self,*a):pass
    monkeypatch.setattr(operations,'Proxmox',Fake)
    monkeypatch.setattr(operations,'execute',lambda d,log:events.append(d['action']))
    class DB:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,*a):pass
    manager=SimpleNamespace(all=lambda *a:[dict(node='lab',vmid=202,created=time.time(),machine_identity=template_finish.machine_identity(config))],db=DB)
    if exitcode:
        with pytest.raises(RuntimeError):template_finish.finish(cfg,manager,lambda *a:None,template_finish.machine_identity(config))
        assert 'convert-template' not in events
    else:
        template_finish.finish(cfg,manager,lambda *a:None,template_finish.machine_identity(config))
        assert events.index('finish-setup')<events.index('convert-template')

def test_review_cache_uses_server_observed_revisions_and_fresh_worker_check(simple_lab,monkeypatch):
    app,c,h,inv=simple_lab
    os_iso,driver=inv['details']['lab']['isos']
    for item in (os_iso,driver): item.update(ctime=100,size=9000)
    inspect=media.inspect_source
    calls=[]
    def read(source,node):
        calls.append('os')
        return dict(inspect(source,node),revision=media.volume_revision(os_iso))
    def driver_pin(*args):
        calls.append('driver')
        return 'a'*64
    monkeypatch.setattr(media,'inspect_source',read)
    monkeypatch.setattr(media,'review_guest_agent',driver_pin)
    c.post('/api/simple/editions',json={'source':os_iso['volid'],'node':'lab'},headers=h)
    for _ in range(2): assert c.post('/api/simple/plan',json=settings(),headers=h).status_code==200
    assert calls==['os','driver']
    driver['ctime']+=1
    assert c.post('/api/simple/plan',json=settings(),headers=h).status_code==200
    assert calls==['os','driver','driver']
    os_iso['ctime']+=1
    plan=c.post('/api/simple/plan',json=settings(),headers=h).json
    assert calls[-1]=='os' and calls.count('os')==2
    submission=c.post('/api/simple/run',json={'plan_id':plan['id'],'confirmed':True},headers=h)
    monkeypatch.setattr(media,'inspect_source',lambda *a:{'identity':'replaced'})
    assert app.run_next_job()
    assert c.get(submission.json['jobs'][0]['url'],headers=h).json['status']=='failed'


def test_async_review_releases_http_and_records_diagnostics(simple_lab,monkeypatch):
    import logging
    import threading
    import diagnostics
    app,c,h,inv=simple_lab
    entered,release=threading.Event(),threading.Event()
    def slow_driver(*args):
        entered.set()
        assert release.wait(5)
        return 'a'*64
    monkeypatch.setattr(media,'review_guest_agent',slow_driver)
    output=io.StringIO()
    handler=logging.StreamHandler(output)
    diagnostics.log.addHandler(handler)
    ident='d'*32
    try:
        ticket=c.post('/api/simple/plan',json=settings(),headers={**h,'Prefer':'respond-async','X-Request-ID':ident})
        assert ticket.status_code==202 and ticket.headers['X-Request-ID']==ident
        url='/api/simple/reviews/'+ticket.json['review_id']
        assert entered.wait(2)
        assert c.get(url,headers=h).json['stage']=='drivers'
        assert c.get('/api/jobs',headers=h).json==[]
        assert app.server.machine_requests==1
        release.set()
        for _ in range(100):
            result=c.get(url,headers=h).json
            if result['state']!='checking':break
            time.sleep(.01)
        assert result['state']=='ready' and app.server.machine_requests==0
        records=[json.loads(line) for line in output.getvalue().splitlines()]
        assert any(r.get('event')=='stage.finished' and r.get('stage')=='drivers' and r.get('request_id')==ident and 'elapsed_ms' in r for r in records)
        assert settings()['password'] not in output.getvalue()
    finally:
        release.set()
        diagnostics.log.removeHandler(handler)


def test_async_review_failure_keeps_reference_and_queue_empty(simple_lab):
    app,c,h,inv=simple_lab
    ticket=c.post('/api/simple/plan',json={**settings(),'count':-1},headers={**h,'Prefer':'respond-async'})
    assert ticket.status_code==202
    for _ in range(100):
        result=c.get('/api/simple/reviews/'+ticket.json['review_id'],headers=h).json
        if result['state']!='checking':break
        time.sleep(.01)
    assert result['state']=='failed' and result['error'] and result['request_id']==ticket.json['request_id']
    assert c.get('/api/jobs',headers=h).json==[]


def test_buffered_iso_reads_only_nearby_sectors_and_handles_eof():
    contents=bytes(range(256))*4096
    image=media.RemoteISO(io.BytesIO(contents),len(contents))
    for index in range(120):
        image.seek(index*2048)
        assert image.read(8)==contents[index*2048:index*2048+8]
    assert image.block_reads==2 and image.bytes_fetched<len(contents)
    image.seek(-7,2)
    assert image.read(8)==contents[-7:] and image.read(1)==b''

def test_upstream_failure_logs_status_and_omits_paths_payload_and_error_body(monkeypatch):
    import logging
    import diagnostics
    p=operations.Proxmox.__new__(operations.Proxmox)
    p.url='https://example.invalid'
    secret='EXAMPLE-PRIVATE-PAYLOAD'
    p.session=SimpleNamespace(request=lambda *args,**kwargs:SimpleNamespace(status_code=503,text=secret))
    output=io.StringIO()
    handler=logging.StreamHandler(output)
    diagnostics.log.addHandler(handler)
    try:
        with pytest.raises(RuntimeError):
            p.call('POST','nodes/lab/storage/'+secret,token=secret)
    finally:
        diagnostics.log.removeHandler(handler)
    record=json.loads(output.getvalue())
    assert record['event']=='proxmox.response' and record['status']==503 and record['method']=='POST'
    assert secret not in output.getvalue()
