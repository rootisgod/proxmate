import base64
import copy
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pycdlib
import pytest
import yaml

import lifecycle
import bootstrap
import operations
from provisioning import answer_iso,answer_xml
from test_api import service


@pytest.fixture
def recovery(service,monkeypatch):
    app,client=service
    class Fake:
        def __init__(self):
            self.guests={201:{'status':'stopped','template':False}}
            self.configs={201:{'name':'source','smbios1':'uuid=original','vmgenid':'original','onboot':1,'scsi0':'local-lvm:vm-201-disk-0','net0':'virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0','digest':'test'}}
            self.archives=[{'volid':f'local:backup/vzdump-qemu-201-2026_01_0{i}.vma.zst','vmid':201,'ctime':time.time()-(50-i)*86400,'size':100+i,'format':'vma','protected':i==1} for i in range(1,5)]
            self.writes=[];self.isolate=True;self.fail_agent=False;self.secret_reads=[]
        def resources(self): return [{'type':'node','node':'pve','status':'online'}]+[dict(type='qemu',node='pve',vmid=i,name=self.configs[i]['name'],**g) for i,g in self.guests.items()]
        def call(self,method,path,**kw):
            parts=path.split('/');vmid=int(parts[3]) if len(parts)>3 and parts[2]=='qemu' else None
            if method=='GET':
                if path=='nodes':return [{'node':'pve','status':'online'}]
                if path.endswith('/storage'):return [{'storage':'local-lvm','active':1,'content':'images'},{'storage':'local','active':1,'content':'iso,backup,snippets'}]
                if path.endswith('/storage/local/content'):return copy.deepcopy(self.archives)
                if path.endswith('/vzdump/extractconfig'):return '\n'.join(k+': '+str(v) for k,v in self.configs[201].items() if k!='digest')
                if path.endswith('/config'):return copy.deepcopy(self.configs[vmid])
                if path.endswith('/agent/get-osinfo'):
                    if self.fail_agent:raise RuntimeError('Agent unavailable')
                    return {'result':{'id':'linux','name':'Test OS'}}
                if path.endswith('/agent/file-read'):
                    self.secret_reads.append(kw['file'])
                    if kw['file'].endswith('bootstrap-ready'):return {'content':'ready'}
                    if kw['file'].endswith('winrm.pem'):return {'content':'-----BEGIN CERTIFICATE-----\npublic-test-cert\n-----END CERTIFICATE-----'}
                    return {'content':'ssh-ed25519 '+base64.b64encode(b'test-public-key').decode()}
                if path.endswith('/agent/network-get-interfaces'):return {'result':[{'ip-addresses':[{'ip-address':'192.0.2.20','ip-address-type':'ipv4'}]}]}
                raise AssertionError((method,path,kw))
            self.writes.append((method,path,copy.deepcopy(kw)))
            if method=='POST' and (path=='nodes/pve/qemu' or path.endswith('/clone')):
                new=kw.get('vmid',kw.get('newid'));assert new not in self.guests
                self.guests[new]={'status':'stopped','template':False}
                self.configs[new]=copy.deepcopy(self.configs[vmid or 201]);self.configs[new].update(smbios1='uuid='+str(new),vmgenid=str(new),name=kw.get('name','restored'))
                if 'onboot' in kw:self.configs[new]['onboot']=kw['onboot']
            elif path.endswith('/config'):
                for k,v in kw.items():
                    if k not in ('digest','delete'):self.configs[vmid][k]=v
                if self.isolate:
                    for key in kw.get('delete','').split(','):self.configs[vmid].pop(key,None)
            elif path.endswith('/status/start'):self.guests[vmid]['status']='running'
            elif path.endswith('/status/shutdown'):self.guests[vmid]['status']='stopped'
            elif path.endswith('/template'):self.guests[vmid]['template']=True;self.configs[vmid]['template']=1
            elif method=='DELETE':self.archives=[a for a in self.archives if lifecycle.quote(a['volid'],safe='')!=parts[-1]]
            return 'UPID:test'
        def wait(self,*args):pass
    fake=Fake();monkeypatch.setattr(operations,'Proxmox',lambda:fake)
    monkeypatch.setattr(lifecycle.time,'sleep',lambda _:None)
    ticks=[0]
    def monotonic():ticks[0]+=10;return ticks[0]
    monkeypatch.setattr(lifecycle.time,'monotonic',monotonic)
    return app,client,{'Authorization':'Bearer '+app.API_TOKEN},fake


def restore(app,fake,**kw):
    return app.lifecycle.review(dict(operation='backup-restore',node='pve',vmid=901,storage='local-lvm',name='restore-test',archive=fake.archives[-1]['volid'],**kw))


def test_restore_collision_and_archive_identity_prevent_writes(recovery):
    app,_,_,fake=recovery
    p=restore(app,fake)
    assert not fake.writes
    fake.guests[901]={'status':'stopped','template':False};fake.configs[901]=copy.deepcopy(fake.configs[201])
    with pytest.raises(ValueError,match='occupied'):app.lifecycle.execute(app.lifecycle.load(p['id']),lambda _:None,p['id'])
    assert not fake.writes
    fake.guests.pop(901);fake.configs.pop(901);fake.archives[-1]['size']+=1
    with pytest.raises(ValueError,match='Archive changed'):app.lifecycle.execute(app.lifecycle.load(p['id']),lambda _:None,p['id'])
    assert not fake.writes


def test_restore_unique_stopped_and_isolated_before_boot(recovery):
    app,_,_,fake=recovery
    p=restore(app,fake,boot_test=True);logs=[]
    app.lifecycle.execute(app.lifecycle.load(p['id']),logs.append,p['id'])
    create=fake.writes[0][2]
    assert create['force']==0 and create['unique']==1 and create['start']==0 and create['onboot']==0
    assert '/config' in fake.writes[1][1] and fake.writes[1][2]['delete']=='net0'
    assert fake.writes[2][1].endswith('/status/start')
    assert fake.writes[-1][2]['forceStop']==0
    assert fake.guests[901]['status']=='stopped' and 901 in fake.configs
    assert not any(recovery[3].writes[i][0]=='DELETE' for i in range(len(fake.writes)))
    assert app.manager.all('restore-reports')[0]['boot_verified'] is True


def test_failed_isolation_never_starts_copy(recovery):
    app,_,_,fake=recovery;p=restore(app,fake,boot_test=True);fake.isolate=False
    with pytest.raises(ValueError,match='isolation'):app.lifecycle.execute(app.lifecycle.load(p['id']),lambda _:None,p['id'])
    assert not any(w[1].endswith('/status/start') for w in fake.writes)
    assert 901 in fake.guests and not app.manager.all('restore-reports')


@pytest.mark.parametrize('key,value',[('hookscript','local:snippets/hook.pl'),('hostpci0','0000:01:00'),('scsi1','/dev/sda'),('args','-net user'),('sata1','local:iso/proxmate-unattend-201-abc.iso')])
def test_external_devices_and_setup_media_rejected_before_restore(recovery,key,value):
    app,_,_,fake=recovery;fake.configs[201][key]=value
    with pytest.raises(ValueError):restore(app,fake,boot_test=True)
    assert not fake.writes


def test_boot_timeout_retains_copy_and_does_not_claim_success(recovery):
    app,_,_,fake=recovery;fake.fail_agent=True;p=restore(app,fake,boot_test=True,timeout=30)
    with pytest.raises(RuntimeError,match='timed out'):app.lifecycle.execute(app.lifecycle.load(p['id']),lambda _:None,p['id'])
    assert fake.guests[901]['status']=='stopped' and not app.manager.all('restore-reports')
    assert fake.writes[-1][2]['forceStop']==0


def test_retention_keeps_newest_and_protected_and_requires_exact_confirmation(recovery):
    app,client,h,fake=recovery
    p=app.lifecycle.review(dict(operation='backup-retention',node='pve',backup_storage='local',source_vmid=201,keep=1,older_than_days=1))
    assert {a['volid'] for a in p['candidates']}=={a['volid'] for a in fake.archives[1:3]}
    assert client.post('/api/lifecycle/run',json={'plan_id':p['id'],'confirmed':True},headers=h).status_code==400
    data={'plan_id':p['id'],'confirmed':True,'risk_confirmed':True}
    first=client.post('/api/lifecycle/run',json=data,headers=h)
    assert first.status_code==202 and client.post('/api/lifecycle/run',json=data,headers=h).json==first.json
    with app.db() as con:assert con.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
    fake.archives[1]['protected']=True
    with pytest.raises(ValueError,match='protected'):app.lifecycle.execute(app.lifecycle.load(p['id']),lambda _:None,p['id'])
    assert not fake.writes


def test_retention_deletes_only_reviewed_candidates(recovery):
    app,_,_,fake=recovery
    p=app.lifecycle.review(dict(operation='backup-retention',node='pve',backup_storage='local',source_vmid=201,keep=2,older_than_days=1))
    assert len(p['candidates'])==1
    app.lifecycle.execute(app.lifecycle.load(p['id']),lambda _:None,p['id'])
    assert len(fake.writes)==1 and fake.writes[0][0]=='DELETE'
    assert len(fake.archives)==3 and any(a['protected'] for a in fake.archives)


def register_version(app,client,h):
    f=client.post('/api/lifecycle/families',json={'name':'Ubuntu base'},headers=h).json
    p=app.lifecycle.review(dict(operation='version-register',node='pve',vmid=201,family_id=f['id'],label='v1',prepared=True))
    app.lifecycle.execute(app.lifecycle.load(p['id']),lambda _:None,p['id'])
    return f,app.manager.get('template-versions',p['id'])


def test_version_promotion_requires_successful_test_and_unchanged_template(recovery):
    app,client,h,fake=recovery
    f,v=register_version(app,client,h)
    assert fake.guests[201]['template'] and fake.configs[201]['onboot']==0
    with pytest.raises(ValueError,match='successful'):app.lifecycle.review({'operation':'version-promote','version_id':v['id']})
    p=app.lifecycle.review(dict(operation='template-test',node='pve',vmid=902,storage='local-lvm',name='version-test',version_id=v['id']))
    app.lifecycle.execute(app.lifecycle.load(p['id']),lambda _:None,p['id'])
    promoted=app.lifecycle.review({'operation':'version-promote','version_id':v['id']})
    app.lifecycle.execute(app.lifecycle.load(promoted['id']),lambda _:None,promoted['id'])
    assert app.manager.get('template-families',f['id'])['preferred_id']==v['id']
    assert fake.guests[201]['template'] and fake.guests[902]['status']=='stopped'
    fake.configs[201]['memory']=8192
    with pytest.raises(ValueError,match='changed'):app.lifecycle.review({'operation':'version-promote','version_id':v['id']})


def test_registration_requires_generalization_and_unique_label(recovery):
    app,client,h,fake=recovery;f,v=register_version(app,client,h)
    with pytest.raises(ValueError,match='label'):app.lifecycle.review(dict(operation='version-register',node='pve',vmid=201,family_id=f['id'],label='v1',prepared=True))
    with pytest.raises(ValueError,match='generalization'):app.lifecycle.review(dict(operation='version-register',node='pve',vmid=201,family_id=f['id'],label='v2'))


def test_build_provenance_never_stores_setup_password_and_matches_machine(recovery):
    app,client,h,fake=recovery
    d={'node':'pve','vmid':201,'kind':'windows','name':'source','windows':{'admin_password':'private-setup-password','hostname':'DEMO'}}
    app.lifecycle.after_provision(d,{'source':'trusted.iso'},'build-job',lambda _:None)
    serialized=json.dumps(app.manager.all('builds'))
    assert 'private-setup-password' not in serialized and 'trusted.iso' in serialized
    fake.configs[201]['vmgenid']='reused-id-different-machine'
    f,v=register_version(app,client,h)
    assert 'unavailable' in v['provenance']['source']


def policy(app,**kwargs):
    return app.lifecycle.save_policy(dict(name='Recovery',mode='restore-check',node='pve',backup_storage='local',source_vmid=201,storage='local-lvm',range_start=9500,range_end=9502,confirmed=True,interval_minutes=15,**kwargs))


def test_restore_policy_disabled_default_and_skips_missed_runs(recovery):
    app,_,_,fake=recovery;s=policy(app)
    assert not s['enabled'];app.lifecycle.tick(s['next_run'])
    with app.db() as con:assert con.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    s=policy(app,enabled=True);app.lifecycle.tick(s['next_run']+200)
    with app.db() as con:assert con.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    s=policy(app,enabled=True);app.lifecycle.tick(s['next_run']);app.lifecycle.tick(s['next_run'])
    with app.db() as con:
        rows=con.execute('SELECT payload FROM jobs').fetchall();assert len(rows)==1
        queued=app.decode_payload(rows[0]['payload'])['plan'];assert queued['vmid']==9500 and queued['boot_test'] and queued['disconnected']
    assert not fake.writes


def test_retention_policy_defaults_to_preview_not_deletion(recovery):
    app,_,_,fake=recovery
    s=app.lifecycle.save_policy(dict(name='Retention',mode='retention',node='pve',backup_storage='local',source_vmid=201,keep=1,older_than_days=1,enabled=True,confirmed=True))
    app.lifecycle.tick(s['next_run'])
    assert 'Preview only' in app.manager.get('backup-policies',s['id'])['last_report']
    with app.db() as con:assert con.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    assert not fake.writes


def test_policy_report_cannot_reenable_concurrently_disabled_policy(recovery):
    app,_,_,_=recovery;s=policy(app,enabled=True)
    updated=dict(s,enabled=False,confirmed=True);app.lifecycle.save_policy(updated)
    s['last_report']='Old tick completed';app.lifecycle.policy_report(s)
    assert app.manager.get('backup-policies',s['id'])['enabled'] is False


def test_bootstrap_generators_use_vendor_data_and_restricted_https(tmp_path):
    vendor=yaml.safe_load(bootstrap.cloud_vendor())
    assert 'users' not in vendor and 'ssh_authorized_keys' not in vendor
    assert 'qemu-guest-agent' in vendor['packages']
    config=bootstrap.options({'enabled':True,'management_cidr':'192.0.2.0/24','agent_iso':'local:iso/virtio.iso'},'windows')
    script=bootstrap.windows_script(config)
    assert '-LocalPort 5986' in script and "-RemoteAddress '192.0.2.0/24'" in script
    assert 'AllowUnencrypted -Value $false' in script and 'Get-AuthenticodeSignature' in script
    assert len(bootstrap.WINDOWS_COMMAND)<260
    settings={'accept_eula':True,'wipe_disk':True,'image_index':1}
    xml=answer_xml(settings,bootstrap=config)
    root=ET.fromstring(xml);assert any(e.tag.endswith('RunSynchronousCommand') for e in root.iter())
    path=tmp_path/'answer.iso';answer_iso(settings,path,bootstrap=config)
    iso=pycdlib.PyCdlib();iso.open(str(path));entry=iso.get_record(joliet_path='/ProxmateBootstrap.ps1');assert entry.data_length>1000;iso.close()


@pytest.mark.parametrize('config,kind',[
    ({'enabled':True,'management_cidr':'0.0.0.0/0','agent_iso':'local:iso/virtio.iso'},'windows'),
    ({'enabled':True,'management_cidr':'192.0.2.1;evil','agent_iso':'local:iso/virtio.iso'},'windows'),
    ({'enabled':True,'snippet_storage':'local;evil'},'linux-cloud'),
    ({'enabled':True},'linux-iso'),
])
def test_bootstrap_rejects_unsafe_scope_and_paths(config,kind):
    with pytest.raises(ValueError):bootstrap.options(config,kind)


def probe(app, d, expected=None):
    cred=app.manager.get('credentials',d['bootstrap']['credential_id'])
    if expected is None: expected=lifecycle.machine_identity(operations.Proxmox().call('GET',lifecycle.vm_path(d)+'/config'))
    return app.lifecycle.probe_bootstrap(d,expected,cred,{},lambda _:None)


@pytest.mark.parametrize('platform',['windows','linux'])
def test_bootstrap_reads_only_public_guest_identity_and_verifies_before_saving(recovery,monkeypatch,platform):
    app,_,_,fake=recovery
    credential=app.manager.save('credentials',dict(name='Guest',platform=platform,username='admin',password='Guest-private-password'))
    calls=[];monkeypatch.setattr(lifecycle,'run_guest',lambda b,c,t,l,readiness=False:calls.append((b,c,readiness)))
    d=dict(node='pve',vmid=201,kind='windows' if platform=='windows' else 'linux-cloud',bootstrap={'verify':True,'credential_id':credential['id'],'timeout':60})
    logs=[];saved=probe(app,d)
    assert calls[0][2] is True and calls[0][0]['verify_tls'] is True
    assert saved['host']=='192.0.2.20'
    assert all(not p.endswith('_key') and 'password' not in p.lower() for p in fake.secret_reads)
    assert 'Guest-private-password' not in '\n'.join(logs)
    if platform=='windows':assert 'BEGIN CERTIFICATE' in saved['ca_pem']
    else:assert saved['fingerprint'].startswith('SHA256:')


def test_new_routes_require_authentication(recovery):
    _,client,_,_=recovery
    assert client.get('/api/lifecycle/state').status_code==401
    for route in ('lifecycle/plan','lifecycle/run','lifecycle/families','lifecycle/policies','bootstrap/helper'):
        assert client.post('/api/'+route,json={}).status_code==401


def test_windows_build_plan_redacts_password_but_encrypts_execution_payload(recovery):
    app,client,h,fake=recovery
    family=client.post('/api/lifecycle/families',json={'name':'Windows'},headers=h).json
    config=dict(action='provision',kind='windows',purpose='vm',node='pve',name='windows',storage='local-lvm',bridge='vmbr0',memory=4096,disk=64,source_kind='proxmox',iso='local:iso/windows.iso',iso_storage='local',windows={'accept_eula':True,'wipe_disk':True,'image_index':1})
    recipe=app.manager.save('recipes',dict(name='Windows base',config=config))
    data=dict(operation='version-build',family_id=family['id'],recipe_id=recipe['id'],vmid=901,name='candidate',label='v2',accept_eula=True,wipe_disk=True,admin_password='Only-In-Encrypted-Storage9!')
    reviewed=client.post('/api/lifecycle/plan',json=data,headers=h)
    assert reviewed.status_code==200 and 'Only-In-Encrypted' not in reviewed.text
    with app.db() as con:
        raw=con.execute('SELECT value FROM lifecycle_plans WHERE id=?',(reviewed.json['id'],)).fetchone()['value']
    assert raw.startswith('encrypted:') and 'Only-In-Encrypted' not in raw
    submitted=client.post('/api/lifecycle/run',json={'plan_id':reviewed.json['id'],'confirmed':True},headers=h)
    assert submitted.status_code==202 and not fake.writes
    assert 'Only-In-Encrypted' not in client.get('/api/jobs',headers=h).text


def test_restore_policy_full_scratch_range_never_reuses_or_deletes_vm(recovery):
    app,_,_,fake=recovery
    for i in range(9500,9503):fake.guests[i]={'status':'stopped','template':False};fake.configs[i]=copy.deepcopy(fake.configs[201])
    s=policy(app,enabled=True);app.lifecycle.tick(s['next_run'])
    assert 'range full' in app.manager.get('backup-policies',s['id'])['last_report']
    with app.db() as con:assert con.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    assert not fake.writes


def test_linux_cloud_bootstrap_preserves_generated_user_data(monkeypatch):
    captured=[]
    class Fake:
        def resources(self):return [{'type':'node','node':'pve','status':'online'}]
        def shell(self,script,log):captured.append(script)
    class Manifest:
        text='a'*64+' noble-server-cloudimg-amd64.img'
        def raise_for_status(self):pass
    monkeypatch.setattr(operations,'Proxmox',Fake)
    monkeypatch.setattr(operations.requests,'get',lambda *a,**k:Manifest())
    operations.execute(dict(action='linux-template',node='pve',vmid=901,name='new-cloud',storage='local-lvm',bridge='vmbr0',profile='ubuntu-24.04',outcome='vm',start=True,bootstrap={'enabled':True,'snippet_storage':'local'},ssh_key='ssh-ed25519 AAAA'),lambda _:None)
    assert '--cicustom vendor=' in captured[0] and '--cicustom user=' not in captured[0]
    assert captured[0].index('qm cloudinit update')<captured[0].index('qm start')
    assert 'test ! -e "$snippet"' in captured[0]


def test_bootstrap_auth_failure_does_not_save_connection(recovery,monkeypatch):
    app,_,_,_=recovery
    c=app.manager.save('credentials',dict(name='Wrong login',platform='linux',username='test',password='not-the-guest-password'))
    def fail(*a,**kw):raise RuntimeError('Authentication failed')
    monkeypatch.setattr(lifecycle,'run_guest',fail)
    d=dict(node='pve',vmid=201,kind='linux-cloud',bootstrap={'verify':True,'credential_id':c['id'],'timeout':60})
    logs=[]
    with pytest.raises(lifecycle.BootstrapPending,match='SSH check'):probe(app,d)
    assert app.manager.all('connections')==[] and 'not-the-guest-password' not in '\n'.join(logs)


@pytest.mark.parametrize('stage,expected', [('retry-winrm','retrying HTTPS WinRM'),('retry-guest-tools','retrying guest-tool'),('waiting-windows-setup','no login is required'),('failed-retry-limit','exhausted its retries')])
def test_windows_bootstrap_reports_safe_stage_and_blocks_tasks(recovery,monkeypatch,stage,expected):
    app,_,_,fake=recovery
    c=app.manager.save('credentials',dict(name='Guest',platform='windows',username='Administrator',password='test-password'))
    original=fake.call
    def call(method,path,**kw):
        if kw.get('file','').endswith('bootstrap-status.json'):
            return {'content':json.dumps({'stage':stage,'error':'private diagnostic must not be shown'})}
        return original(method,path,**kw)
    fake.call=call
    def forbidden(*a,**kw):pytest.fail('No authentication/tasks before guest setup completes')
    monkeypatch.setattr(lifecycle,'run_guest',forbidden)
    d=dict(node='pve',vmid=201,kind='windows',bootstrap={'verify':True,'credential_id':c['id'],'timeout':60})
    logs=[]
    with pytest.raises((lifecycle.BootstrapPending,lifecycle.BootstrapFailed),match=expected):probe(app,d)
    assert app.manager.all('connections')==[]
    assert 'private diagnostic' not in '\n'.join(logs)


def test_missing_agent_is_distinct_from_winrm_authentication_failure(recovery,monkeypatch):
    app,_,_,fake=recovery;fake.fail_agent=True
    c=app.manager.save('credentials',dict(name='Guest',platform='windows',username='Administrator',password='test-password'))
    d=dict(node='pve',vmid=201,kind='windows',bootstrap={'verify':True,'credential_id':c['id'],'timeout':60})
    with pytest.raises(lifecycle.BootstrapPending,match='Waiting for QEMU guest agent discovery'):
        probe(app,d)


def test_bootstrap_cannot_be_silently_ignored_by_legacy_job_route(recovery):
    app,client,h,fake=recovery
    result=client.post('/api/jobs',json=dict(action='linux-template',node='pve',vmid=901,name='legacy',storage='local-lvm',bridge='vmbr0',bootstrap={'enabled':True,'snippet_storage':'local'}),headers=h)
    assert result.status_code==400 and 'provision action' in result.json['error']


@pytest.mark.parametrize('change', ['deleted', 'replaced', 'moved', 'offline'])
def test_bootstrap_target_changes_do_not_wait_for_guest_or_use_credentials(recovery, monkeypatch, change):
    app,_,_,fake=recovery
    credential=app.manager.save('credentials',dict(name='Guest',platform='windows',username='admin',password='test-secret'))
    expected=lifecycle.machine_identity(fake.configs[201])
    if change=='deleted': fake.guests.pop(201)
    if change=='replaced': fake.configs[201]['smbios1']='uuid=replacement'
    resources=fake.resources
    if change in ('moved','offline'):
        def changed_resources():
            rows=resources()
            rows[0 if change=='offline' else 1].update({'status':'offline'} if change=='offline' else {'node':'other'})
            return rows
        fake.resources=changed_resources
    def forbidden(*args,**kwargs): raise AssertionError('Guest login must not run')
    monkeypatch.setattr(lifecycle,'run_guest',forbidden)
    d=dict(node='pve',vmid=201,kind='windows',bootstrap={'verify':True,'credential_id':credential['id'],'timeout':60})
    logs=[]
    with pytest.raises((lifecycle.BootstrapPending,lifecycle.BootstrapFailed),match='offline' if change=='offline' else 'setup wait stopped'):
        probe(app,d,expected)
    assert fake.secret_reads==[] and fake.writes==[] and app.manager.all('connections')==[]
    assert not fake.writes
