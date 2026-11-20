import base64
import ssl
import shutil
import subprocess
import time
from contextlib import nullcontext

import pytest
import machine
from test_api import service
from test_lifecycle import recovery


TARGET=dict(node='pve',vmid=201)
PUBLIC_PEM='-----BEGIN CERTIFICATE-----\nZ3Vlc3Q=\n-----END CERTIFICATE-----'


def test_rdp_listener_uses_local_setting_when_policy_value_is_absent():
    shell=shutil.which('pwsh') or shutil.which('powershell')
    if not shell: pytest.skip('PowerShell is required to exercise the Windows listener probe')
    script="""
function Get-ItemProperty { [PSCustomObject]$script:policy }
function Get-ItemPropertyValue {
    param($Path,$Name)
    if ($Path -like '*Policies*') { throw 'Missing policy value' }
    if ($Name -eq 'PortNumber') { return 3391 }
    if ($Name -eq 'fDenyTSConnections') { return 0 }
    throw 'Unexpected property'
}
function Get-Service { [PSCustomObject]@{Status='Running'} }
function Probe {
"""+machine.RDP_LISTENER+"""
}
$script:policy=@{}
Probe
$script:policy=@{fDenyTSConnections=1}
Probe
"""
    encoded=base64.b64encode(script.encode('utf-16le')).decode()
    result=subprocess.run([shell,'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                          capture_output=True,text=True,timeout=10,check=True)
    assert result.stdout.splitlines()==['3391','disabled']


def details(client,headers):
    reply=client.post('/api/simple/machine',json=TARGET,headers=headers)
    assert reply.status_code==200,reply.json
    return reply.json


def setup_guest(fake,platform):
    fake.guests[201]['status']='running'
    original=fake.call
    def call(method,path,**kwargs):
        if path.endswith('get-osinfo'): return {'result':{'id':'mswindows' if platform=='windows' else 'debian','pretty-name':'Guest OS'}}
        if path.endswith('network-get-interfaces'):
            return {'result':[{'name':'lo','ip-addresses':[{'ip-address':'127.0.0.1'}]},
                {'name':'eth0','ip-addresses':[{'ip-address':'192.0.2.21'},{'ip-address':'192.0.2.22'}]}]}
        return original(method,path,**kwargs)
    fake.call=call


def windows_listener(fake, monkeypatch):
    original = fake.call
    def read(path, **params):
        if path.endswith('/agent/file-read'):
            assert params == {'file': r'C:\ProgramData\Proxmate\winrm.pem'}
            return {'content': PUBLIC_PEM}
        return original('GET', path, **params)
    fake.verified_reader = lambda: nullcontext(read)
    def tls_peer(binding, ca):
        if ca != PUBLIC_PEM:
            raise ssl.SSLCertVerificationError()
        return b'guest'
    monkeypatch.setattr(machine, 'tls_peer', tls_peer)
    return read


@pytest.mark.parametrize('platform',['linux','windows'])
def test_remote_access_uses_live_ip_and_identity_and_returns_no_secret(recovery,monkeypatch,platform):
    app,c,h,fake=recovery
    fake.guests[201]['status']='running'
    fake.configs[201]['ostype']='win11' if platform=='windows' else 'l26'
    if platform=='windows':
        base=fake.call
        fake.call=lambda method,path,**kw: {'result':{'id':'mswindows'}} if path.endswith('get-osinfo') else base(method,path,**kw)
    identity=details(c,h)['identity']
    cred=app.manager.save('credentials',dict(name='Login',platform=platform,username='labuser',password='secret123'))
    binding=app.manager.prepare('connections',dict(TARGET,platform=platform,credential_id=cred['id'],
        host='192.0.2.99',port=2222,fingerprint='SHA256:'+'x'*43))
    app.manager.put('connections',dict(binding,machine_identity=identity))
    monkeypatch.setattr(machine.winrm_setup,'agent_script',lambda *a:'3391')
    route='/api/simple/machine/remote-access'
    body=dict(TARGET,identity=identity)
    result=c.post(route,json=body,headers=h)
    assert result.status_code==200,result.json
    if platform=='linux':
        assert result.json==dict(kind='ssh',host='192.0.2.20',port=2222,command='ssh -p 2222 -l labuser 192.0.2.20')
    else:
        assert result.json==dict(kind='rdp',host='192.0.2.20',port=3391)
        monkeypatch.setattr(machine.winrm_setup,'agent_script',lambda *a:'disabled')
        assert c.post(route,json=body,headers=h).json=={'kind':None}
    assert c.post(route,json=body).status_code==401
    assert c.post(route,json=body,headers=dict(h,**{'X-Proxmate-Server':'old'})).status_code==409
    job=dict(action='simple',server_id=app.server.current()['id'],plan=dict(purpose='vm',
             config=dict(TARGET,name='built',kind='linux-cloud' if platform=='linux' else 'windows',windows={'rdp':True})))
    app.manager.put('builds',dict(TARGET,id='finished',machine_identity=identity))
    with app.db() as con:
        con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',('finished','simple','succeeded',1,2,app.encode_payload(job),'done'))
    summary=c.get('/api/jobs?summary=1',headers=h).json[0]
    assert summary['remote_kind']==('ssh' if platform=='linux' else 'rdp')
    assert c.post(route,json=dict(TARGET,job_id='finished'),headers=h).status_code==200
    fake.configs[201]['smbios1']='uuid=replaced'
    assert c.post(route,json=dict(TARGET,job_id='finished'),headers=h).status_code==400
    assert c.post(route,json=body,headers=h).status_code==400
    assert not fake.writes


def test_shortcut_prefers_proxmox_adapter_over_guest_bridges(recovery):
    _,client,headers,fake=recovery
    setup_guest(fake,'linux')
    original=fake.call
    nic={'name':'ens18','hardware-address':'aa:bb:cc:dd:ee:ff','ip-addresses':[{'ip-address':'192.0.2.21'}]}
    bridge={'name':'docker0','hardware-address':'aa:00:00:00:00:01','ip-addresses':[{'ip-address':'172.17.0.1'}]}
    def read(method,path,**kwargs):
        if path.endswith('/network-get-interfaces'): return {'result':[bridge,nic]}
        return original(method,path,**kwargs)
    fake.call=read
    body=dict(TARGET,identity=details(client,headers)['identity'])
    route='/api/simple/machine/remote-access'
    assert client.post(route,json=body,headers=headers).json['command']=='ssh 192.0.2.21'
    nic['ip-addresses'].append({'ip-address':'192.0.2.22'})
    assert 'select a current guest address' in client.post(route,json=body,headers=headers).json['error']


def test_connection_refresh_rechecks_the_vm_from_the_failed_job(recovery):
    app,client,headers,fake=recovery
    setup_guest(fake,'windows')
    config=fake.call('GET','nodes/pve/qemu/201/config')
    target=dict(TARGET,reviewed_identity=app.manager.identity(config))
    assert client.post('/api/simple/machine',json=target,headers=headers).status_code==200
    fake.configs[201]['smbios1']='uuid=replacement'
    result=client.post('/api/simple/machine',json=target,headers=headers)
    assert result.status_code==400 and 'changed since the failed job' in result.json['error']
    assert not fake.writes


@pytest.mark.parametrize('platform',['linux','windows'])
def test_discover_choose_address_verify_and_reuse_login(recovery,monkeypatch,platform):
    app,c,h,fake=recovery
    setup_guest(fake,platform)
    if platform=='windows': windows_listener(fake,monkeypatch)
    d=details(c,h)
    assert d['platform']==platform and d['os']=='Guest OS' and d['connection']=={} and d['verified_at'] is None
    assert d['addresses']==[{'host':'192.0.2.21','interface':'eth0'},{'host':'192.0.2.22','interface':'eth0'}]
    payload=dict(TARGET,identity=d['identity'],host='192.0.2.22',platform=platform,credential={'username':'guest','password':'TestSecret!'})
    authenticated=[]
    def authenticate(binding,cred,tasks,log,readiness):
        assert not tasks and readiness and binding['verify_tls']
        assert binding['host']=='192.0.2.22' and cred['password']=='TestSecret!'
        assert (binding.get('fingerprint') or binding.get('ca_pem'))
        authenticated.append(cred['id'])
    monkeypatch.setattr(machine,'run_guest',authenticate)
    reply=c.post('/api/simple/machine/connect',json=payload,headers=h)
    assert reply.status_code==200,reply.json
    d=details(c,h)
    assert d['verified_at']==reply.json['verified_at'] and d['logins'][0]['id']==authenticated[0]
    assert 'TestSecret!' not in str(d) and 'BEGIN CERTIFICATE' not in str(d)
    payload.pop('credential')
    payload.update(credential_id=d['logins'][0]['id'],connection_revision=d['connection']['updated'])
    assert c.post('/api/simple/machine/connect',json=payload,headers=h).status_code==200
    assert authenticated[1]==authenticated[0] and len(app.manager.all('credentials'))==1
    cred=app.manager.get('credentials',authenticated[0]);app.manager.save('credentials',dict(cred,password='rotated'))
    assert details(c,h)['verified_at'] is None
    assert not fake.writes


@pytest.mark.parametrize('failure',['agent','wrong-target','stale-ip','host','bad-login','mid-login-replacement','missing-id'])
def test_failed_setup_cannot_save_login_or_binding(recovery,monkeypatch,failure):
    app,c,h,fake=recovery
    setup_guest(fake,'linux')
    d=details(c,h)
    body=dict(TARGET,identity=d['identity'],host='192.0.2.21',platform='linux',credential=dict(username='guest',password='secret-password'))
    if failure=='agent':
        original=fake.call
        def offline(method,path,**kwargs):
            if '/agent/' in path: raise RuntimeError('agent offline')
            return original(method,path,**kwargs)
        fake.call=offline
        assert details(c,h)['addresses']==[]
    if failure=='wrong-target': fake.configs[201]['smbios1']='replacement'
    if failure=='missing-id': body.pop('identity')
    if failure=='stale-ip': body['host']='192.0.2.88'
    if failure=='host': body['host']='192.0.2.10'
    def authenticate(*args,**kwargs):
        if failure=='bad-login': raise ValueError('diagnostic contains secret-password')
        if failure=='mid-login-replacement': fake.configs[201]['smbios1']='replacement'
        else: pytest.fail('Unexpected login attempt')
    monkeypatch.setattr(machine,'run_guest',authenticate)
    reply=c.post('/api/simple/machine/connect',json=body,headers=h)
    assert reply.status_code==400 and 'secret-password' not in reply.text
    assert not app.manager.all('credentials') and not app.manager.all('connections') and not fake.writes


def test_changed_pin_never_receives_existing_password(recovery,monkeypatch):
    app,c,h,fake=recovery
    setup_guest(fake,'linux')
    cred=app.manager.save('credentials',dict(platform='linux',username='guest',name='Saved',password='private'))
    existing=app.manager.save('connections',dict(TARGET,host='192.0.2.21',platform='linux',credential_id=cred['id'],fingerprint='SHA256:'+'x'*43))
    body=dict(TARGET,identity=details(c,h)['identity'],host='192.0.2.21',platform='linux',
              credential_id=cred['id'],connection_revision=existing['updated'])
    monkeypatch.setattr(machine,'run_guest',lambda *a,**kw:pytest.fail('Changed pin received credentials'))
    reply=c.post('/api/simple/machine/connect',json=body,headers=h)
    assert reply.status_code==400 and 'identity changed' in reply.json['error']


def test_machine_backup_and_activity_are_scoped_and_selection_bound(recovery):
    app,c,h,fake=recovery
    with app.db() as con:
        for i,vmid in enumerate([201,201,202]):
            payload=dict(action='manage',plan=dict(operation='backup',targets=[dict(TARGET,vmid=vmid)]))
            con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(str(i),'manage','succeeded',i+1,3,app.encode_payload(payload),'private log'))
        con.execute('INSERT INTO server_archives VALUES (?,?,?)',('old',1.5,app.encode_payload({})))
    d=details(c,h)
    assert [j['id'] for j in d['jobs']]==['1']
    fake.archives.append(dict(volid='local:backup/vzdump-qemu-202-latest.vma.zst',ctime=time.time()+10))
    path='/api/simple/machine/backup'
    result=c.post(path,json=dict(TARGET,identity=d['identity']),headers=h)
    assert result.status_code==200 and result.json['latest']['volid']==fake.archives[-2]['volid']
    for route in ['/api/simple/machine',path,'/api/simple/machine/connect']:
        assert c.post(route,json=TARGET).status_code==401
        assert c.post(route,json=TARGET,headers=dict(h,**{'X-Proxmate-Server':'stale'})).status_code==409
    assert not fake.writes and app.server.machine_requests==0

def test_saved_windows_ca_is_used_for_retest(recovery,monkeypatch):
    app,c,h,fake=recovery
    setup_guest(fake,'windows')
    ca='-----BEGIN CERTIFICATE-----\\ntrusted-ca\\n-----END CERTIFICATE-----'
    cred=app.manager.save('credentials',dict(name='Windows',platform='windows',username='admin',password='pw'))
    binding=app.manager.save('connections',dict(TARGET,platform='windows',credential_id=cred['id'],host='192.0.2.21',ca_pem=ca))
    def authenticate(b,*args,**kwargs): assert b['ca_pem']==ca and b['verify_tls']
    monkeypatch.setattr(machine,'tls_peer',lambda b,saved: b'valid' if saved==ca else pytest.fail('Lost saved CA'))
    monkeypatch.setattr(machine,'run_guest',authenticate)
    reply=c.post('/api/simple/machine/connect',headers=h,json=dict(TARGET,platform='windows',host='192.0.2.21',
        credential_id=cred['id'],identity=details(c,h)['identity'],connection_revision=binding['updated']))
    assert reply.status_code==200
    assert not fake.secret_reads


@pytest.mark.parametrize('outcome', ['success','wrong-peer','wrong-address','changed-identity','untrusted-proxmox','bad-password'])
def test_windows_certificate_refresh_checks_both_ends_before_saving(recovery,monkeypatch,outcome):
    app,c,h,fake=recovery
    setup_guest(fake,'windows')
    read=windows_listener(fake,monkeypatch)
    identity=details(c,h)['identity']
    login=app.manager.save('credentials',dict(name='Current',platform='windows',username='admin',password='private'))
    old=app.manager.save('connections',dict(TARGET,host='192.0.2.21',platform='windows',credential_id=login['id'],
                                            ca_pem=PUBLIC_PEM.replace('Z3Vlc3Q=', 'b2xk')))
    old.update(machine_identity=identity)
    app.manager.put('connections',old)
    def trusted_read(path,**params):
        if outcome=='untrusted-proxmox': raise RuntimeError('host key mismatch')
        if outcome=='wrong-address' and path.endswith('/network-get-interfaces'): return {'result':[]}
        if outcome=='changed-identity' and path.endswith('/config'): return {'vmgenid':'other'}
        return read(path,**params)
    fake.verified_reader=lambda:nullcontext(trusted_read)
    if outcome=='wrong-peer':
        original=machine.tls_peer
        monkeypatch.setattr(machine,'tls_peer',lambda b,ca: b'different' if ca==PUBLIC_PEM else original(b,ca))
    seen=[]
    def authenticate(binding,credential,*args,**kwargs):
        seen.append(credential['id'])
        assert binding['ca_pem']==PUBLIC_PEM and binding['verify_tls']
        if outcome=='bad-password': raise ValueError('private')
    monkeypatch.setattr(machine,'run_guest',authenticate)
    result=c.post('/api/simple/machine/connect',headers=h,json=dict(TARGET,platform='windows',host=old['host'],
                  identity=identity,credential_id=login['id'],connection_revision=old['updated']))
    saved=app.manager.get('connections',old['id'])
    assert seen==([login['id']] if outcome in ('success','bad-password') else [])
    if outcome=='success':
        assert result.status_code==200 and saved['ca_pem']==PUBLIC_PEM and saved['verified_at']
    else:
        assert result.status_code==400 and 'private' not in result.text and saved==old
    assert not fake.writes


@pytest.mark.parametrize('legacy',[False,True])
def test_refresh_uses_the_current_build_login_for_a_reused_vm_id(recovery,monkeypatch,legacy):
    app,c,h,fake=recovery
    setup_guest(fake,'windows')
    windows_listener(fake,monkeypatch)
    identity=details(c,h)['identity']
    def login(name):
        return app.manager.save('credentials',dict(name=name,platform='windows',username='admin',password=name))
    old_login,current_login=login('Old'),login('Current')
    old=app.manager.save('connections',dict(TARGET,host='192.0.2.92',platform='windows',credential_id=old_login['id']))
    if not legacy:
        old['machine_identity']='previous-vm'
        app.manager.put('connections',old)
    for job_id,server,cred in [('origin',app.server.current()['id'],current_login),('archived','other-server',old_login)]:
        app.manager.put('builds',dict(TARGET,id=job_id,machine_identity=identity))
        payload=dict(action='simple',server_id=server,plan=dict(config=dict(TARGET,bootstrap=dict(credential_id=cred['id']))))
        with app.db() as con:
            con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(job_id,'simple','failed',1,2,app.encode_payload(payload),''))
    assert details(c,h)['suggested_credential_id']==current_login['id']
    seen=[]
    monkeypatch.setattr(machine,'run_guest',lambda b,cred,*a,**kw:seen.append(cred['id']))
    body=dict(TARGET,platform='windows',identity=identity,host='192.0.2.21',connection_revision=old['updated'])
    assert c.post('/api/simple/machine/connect',headers=h,json=dict(body,credential_id=old_login['id'])).status_code==400
    assert seen==[] and app.manager.get('connections',old['id'])==old
    assert c.post('/api/simple/machine/connect',headers=h,json=dict(body,credential_id=current_login['id'])).status_code==200
    assert seen==[current_login['id']] and app.manager.get('connections',old['id'])['machine_identity']==identity
