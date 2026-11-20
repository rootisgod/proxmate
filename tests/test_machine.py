import time

import pytest
import machine
from test_api import service
from test_lifecycle import recovery


TARGET=dict(node='pve',vmid=201)


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


@pytest.mark.parametrize('platform',['linux','windows'])
def test_discover_choose_address_verify_and_reuse_login(recovery,monkeypatch,platform):
    app,c,h,fake=recovery
    setup_guest(fake,platform)
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
    monkeypatch.setattr(machine,'run_guest',authenticate)
    reply=c.post('/api/simple/machine/connect',headers=h,json=dict(TARGET,platform='windows',host='192.0.2.21',
        credential_id=cred['id'],identity=details(c,h)['identity'],connection_revision=binding['updated']))
    assert reply.status_code==200
    assert not fake.secret_reads
