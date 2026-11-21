import copy
import io
import os
from pathlib import Path
import subprocess
import zipfile
from xml.etree import ElementTree as ET

import pycdlib
import pytest

import guest_tasks
import lifecycle
import operations
import windows_build
import winrm_setup
from test_api import service
from test_simple_flow import simple_lab


NS={'u':'urn:schemas-microsoft-com:unattend'}


@pytest.mark.skipif(os.name!='nt',reason='Windows Shell is required to create and read real .lnk files')
def test_domain_console_shortcuts_open_the_correct_msc_and_are_idempotent(tmp_path):
    item=next(t for t in guest_tasks.BUILTINS if t['id']==guest_tasks.DOMAIN_CONTROLLER)
    steps=guest_tasks.compile_task(item,dict(domain_name='lab.example.test',dsrm_password='Fixture restore 123!'))
    shortcut=steps[-1]['ansible.windows.win_powershell']['script']
    # Redirect just the two Windows special folders into the test directory.
    shortcut=shortcut.replace("[Environment]::GetFolderPath('CommonDesktopDirectory')","(Get-Location).Path")
    shortcut=shortcut.replace('[Environment]::SystemDirectory',"(Get-Location).Path")
    for name in ('mmc.exe','gpmc.msc','dsa.msc'):
        (tmp_path/name).touch()
    harness=tmp_path/'verify.ps1'
    harness.write_text("$ErrorActionPreference='Stop'\n$Ansible=@{}\nfunction Install-Links {\n"+shortcut+"\n}\n"+r"""
Install-Links
if (!$Ansible.Changed) { throw 'Initial shortcuts missing' }
Install-Links
if ($Ansible.Changed) { throw 'Repeated installation rewrote the shortcuts' }
$shell=New-Object -ComObject WScript.Shell
foreach ($pair in @{'Group Policy Management'='gpmc.msc'; 'Active Directory Users and Computers'='dsa.msc'}.GetEnumerator()) {
    $link=$shell.CreateShortcut((Join-Path $PWD ($pair.Key+'.lnk')))
    if ($link.TargetPath -ne (Join-Path $PWD 'mmc.exe') -or $link.Arguments -ne ('"{0}"' -f (Join-Path $PWD $pair.Value))) {
        throw 'Unexpected MMC target or console'
    }
}
""",encoding='utf-8-sig')
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(harness)],
                   cwd=tmp_path,check=True,capture_output=True,text=True,timeout=30)


def xml_from_iso(path):
    iso=pycdlib.PyCdlib()
    iso.open(str(path))
    output=io.BytesIO()
    try: iso.get_file_from_iso_fp(output,joliet_path='/Autounattend.xml')
    finally: iso.close()
    return output.getvalue()


@pytest.fixture
def clone_lab(simple_lab,monkeypatch):
    app,c,h,inv=simple_lab
    template=dict(type='qemu',node='lab',vmid=9000,status='stopped',template=1,name='win-base',maxdisk=64*2**30)
    inv['resources'].append(template)
    source=dict(template=1,ostype='win11',smbios1='uuid=source',vmgenid='source',name='win-base',digest='abc',
                net0='e1000=AA:BB:CC:DD:EE:FF,bridge=vmbr0',sata0='disks:base-9000-disk-0')
    class Fake:
        def __init__(self): self.guests={9000:source};self.events=[];self.started=False;self.xml=None
        def resources(self):
            return inv['resources']+[dict(type='qemu',vmid=vmid,node='lab',name=cfg['name'],status='running' if self.started else 'stopped')
                                     for vmid,cfg in self.guests.items() if vmid!=9000]
        def wait(self,*args): pass
        def call(self,method,path,**kw):
            self.events.append((method,path,kw))
            vmid=int(path.split('/')[3]) if '/qemu/' in path else None
            if path.endswith('/clone') and method=='POST':
                self.guests[kw['newid']]=dict(source,template=0,smbios1='uuid=clone',vmgenid='clone',name=kw['name']);return 'UPID:clone'
            if path.endswith('/config') and method=='GET': return dict(self.guests[vmid])
            if path.endswith('/config') and method=='PUT':
                assert kw.pop('digest')==self.guests[vmid]['digest']
                for k,v in kw.items():
                    if isinstance(v,str) and ',serial=' in v: v='disks:vm-202-disk-5,'+v.split(',',1)[1]
                    self.guests[vmid][k]=v
                return None
            if path.endswith('/status/current'): return dict(status='running' if self.started else 'stopped')
            if path.endswith('/status/start'): self.started=True;return 'UPID:start'
            if path.endswith('/agent/get-osinfo'): return dict(result={'id':'mswindows'})
            if path.endswith('/agent/file-read'):
                filename=kw['file'].split('\\')[-1]
                if filename=='clone-ready': return dict(content=self.proof)
                if filename=='bootstrap-ready':return dict(content='ready')
                if filename=='winrm.pem':return dict(content='-----BEGIN CERTIFICATE-----\npublic\n-----END CERTIFICATE-----')
                return dict(content='{}')
            if path.endswith('/agent/get-host-name'): return dict(result={'host-name':self.hostname})
            if path.endswith('/agent/network-get-interfaces'):return dict(result=[{'ip-addresses':[{'ip-address-type':'ipv4','ip-address':'192.0.2.22'}]}])
            if method=='DELETE' and '/content/' in path: return 'UPID:delete-media'
            raise AssertionError((method,path,kw))
    fake=Fake()
    def upload(p,node,storage,path,log):
        fake.xml=xml_from_iso(path);return storage+':iso/'+path.name
    monkeypatch.setattr(operations,'Proxmox',lambda:fake)
    monkeypatch.setattr(operations,'upload_generated',upload)
    options=dict(os='windows',source_kind='template',template_id=9000,generalized=True,name='files-01',
                 password='New-Secret#1234',data_disks=[dict(size=80,label='Data',letter='F')],manual_updates=True)
    return app,c,h,fake,options


@pytest.mark.parametrize('mode',['full','linked'])
def test_clone_to_verified_software_and_exact_archived_media(clone_lab,monkeypatch,mode):
    app,c,h,fake,opts=clone_lab
    opts['clone_mode']=mode
    before=copy.deepcopy(fake.guests[9000])
    result=c.post('/api/simple/plan',json=opts,headers=h)
    assert result.status_code==200,result.json
    assert result.json['clone_mode']==mode and result.json['data_disks']==opts['data_disks']
    assert all(m=='GET' for m,p,k in fake.events)
    cfg=app.manager.load_plan(result.json['id'])['configs'][0]
    submit=c.post('/api/simple/run',json=dict(plan_id=result.json['id'],confirmed=True),headers=h)
    assert submit.status_code==202
    job=submit.json['jobs'][0]['id']
    assert app.run_next_job()
    detail=lambda:c.get('/api/jobs/'+job,headers=h).json
    assert detail()['status']=='waiting',detail()['log']
    assert detail()['has_answer'] and opts['password'] not in str(detail())
    args=next(k for m,p,k in fake.events if p.endswith('/clone'))
    assert args['full']==int(mode=='full') and ('storage' in args)==(mode=='full')
    assert fake.guests[9000]==before and fake.started
    xml=ET.fromstring(fake.xml)
    assert {node.attrib['pass'] for node in xml}=={'specialize','oobeSystem'}
    assert xml.find('.//u:ComputerName',NS).text=='files-01'
    assert xml.find('.//u:DiskConfiguration',NS) is None
    path='/api/jobs/'+job+'/answer'
    with zipfile.ZipFile(io.BytesIO(c.get(path,headers=h).data)) as z:
        assert opts['password'].encode() not in z.read('Autounattend.xml')
        assert cfg['bootstrap']['clone_token'].encode() in z.read('ProxmateBootstrap.ps1')
    assert c.get(path).status_code==401
    assert c.post(path,json={},headers=h).status_code==400
    with zipfile.ZipFile(io.BytesIO(c.post(path,json={'include_secrets':True},headers=h).data)) as z:
        assert z.read('Autounattend.xml')==fake.xml
    with app.db() as con:
        raw=con.execute('SELECT value FROM windows_answers WHERE job_id=?',(job,)).fetchone()[0]
        assert raw.startswith('encrypted:') and opts['password'] not in raw
    fake.proof='stale|files-01';fake.hostname='files-01'
    auth=[]
    monkeypatch.setattr(lifecycle,'run_guest',lambda *a,**kw:auth.append('login'))
    assert app.run_next_job() and detail()['status']=='waiting' and not auth
    fake.proof=cfg['bootstrap']['clone_token']+'|FILES-01';fake.hostname='old-name'
    with app.db() as con: con.execute('UPDATE job_waits SET next_check=0')
    assert app.run_next_job() and detail()['status']=='waiting' and not auth
    monkeypatch.setattr(winrm_setup,'agent_script',lambda *args:'{"optical":[],"used":["C"]}')
    fake.hostname='files-01'
    applied=[]
    def execute(plan,log,job_id):
        assert job_id==job
        applied.extend(guest_tasks.compile_task(t['item'],t['params']) for t in plan['tasks'])
    monkeypatch.setattr(app.manager,'execute',execute)
    with app.db() as con: con.execute('UPDATE job_waits SET next_check=0')
    assert app.run_next_job() and detail()['status']=='succeeded',detail()['log']
    assert auth==['login']
    disk = cfg['data_disks'][0]
    assert applied[0] == [{'ansible.windows.win_powershell': {
        'script': Path(windows_build.__file__).with_name('windows_volume.ps1').read_text(encoding='utf8'),
        'parameters': {'Serial': disk['serial'], 'SizeGiB': disk['size'], 'Label': disk['label'], 'Letter': disk['letter']},
    }}]
    assert applied[1][0]['ansible.windows.win_regedit']['name']=='NoAutoUpdate'
    assert not app.run_next_job()
    console='/api/jobs/'+job+'/console'
    opened=c.get(console,headers=h)
    assert opened.status_code==302 and 'vmid=202' in opened.location
    fake.guests[202]['smbios1']='uuid=another-guest'
    assert c.get(console,headers=h).status_code==400


def test_changed_template_fails_before_clone(clone_lab):
    app,c,h,fake,opts=clone_lab
    plan=app.simple.plan(opts)
    submitted=app.simple.enqueue(dict(plan_id=plan['id'],confirmed=True))
    fake.guests[9000]['sata0']='disks:replaced'
    assert app.run_next_job()
    detail=c.get(submitted['jobs'][0]['url'],headers=h).json
    assert detail['status']=='failed' and all(method=='GET' for method,path,args in fake.events)


def test_disks_require_exact_serial_and_owned_volume():
    cfg=dict(storage='disks',data_disks=windows_build.disks_requested([{'size':32}]))
    disk=cfg['data_disks'][0]
    with pytest.raises(ValueError):windows_build.disk_volumes(cfg,{disk['slot']:'disks:wrong,serial=unknown'})
    with pytest.raises(ValueError):windows_build.disks_requested([{'size':64,'letter':'C'}])
    with pytest.raises(ValueError):windows_build.disks_requested([{'size':32,'letter':'F'},{'size':32,'letter':'F'}])
    with pytest.raises(ValueError):windows_build.drive_options(dict(cfg,data_disks=[dict(disk,slot='sata0')]))


def test_script_inputs_freeze_values_and_use_namespaced_vars(simple_lab):
    app,c,h,inv=simple_lab
    task=app.manager.save('tasks',dict(kind='ansible',name='Share',platform='windows',parameters={'share_name':'Share name'},
        defaults={'share_name':'Lab'},body='- ansible.windows.win_file:\n    path: "C:/Shares/{{ proxmate_inputs.share_name }}"\n    state: directory'))
    selection=app.manager.task_selection([{'id':task['id']}])[0]
    assert selection['params']=={'share_name':'Lab'}
    assert guest_tasks.compile_task(selection['item'],selection['params'])[0]['vars']=={'proxmate_inputs':{'share_name':'Lab'}}
    with pytest.raises(ValueError): app.manager.task_selection([{'id':task['id'],'params':{'extra':'unexpected'}}])


def test_readonly_winrm_review_and_idempotent_setup_job(simple_lab,monkeypatch):
    app,c,h,inv=simple_lab
    cfg={'ostype':'win11','smbios1':'uuid=review','net0':'e1000=x,bridge=vmbr0'}
    class Fake:
        def resources(self): return [dict(type='qemu',node='lab',vmid=201,status='running')]
        def call(self,m,path,**kw):
            if path.endswith('/config'):return cfg
            if path.endswith('/network'):return inv['details']['lab']['network']
            raise AssertionError(path)
    monkeypatch.setattr(operations,'Proxmox',Fake)
    scripts=[]
    def run(p,t,i,script):
        scripts.append(script)
        return 'no-listener' if script==winrm_setup.INSPECT else 'An HTTPS listener already exists.'
    monkeypatch.setattr(winrm_setup,'agent_script',run)
    target=dict(node='lab',vmid=201,identity=lifecycle.machine_identity(cfg))
    route='/api/simple/machine/winrm/'
    review=c.post(route+'review',json=target,headers=h)
    assert review.status_code==200,review.json
    assert review.json['status']=='no-listener' and review.json['scope']=='192.0.2.0/24' and scripts==[winrm_setup.INSPECT]
    assert review.json['commands']==winrm_setup.SETUP.replace('__SCOPE__','192.0.2.0/24').strip()
    assert c.post(route+'review',json=dict(target,scope='0.0.0.0/0'),headers=h).status_code==400
    submit=dict(plan_id=review.json['plan_id'],confirmed=True)
    assert c.post(route+'run',json=dict(submit,confirmed=False),headers=h).status_code==400
    for _ in range(2): assert c.post(route+'run',json=submit,headers=h).status_code==202
    assert app.run_next_job() and not app.run_next_job()
    assert len(scripts)==2 and '192.0.2.0/24' in scripts[1]
    inv['details']['lab']['network']=[]
    inspection=c.post(route+'review',json=target,headers=h).json
    assert inspection['status']=='no-listener' and inspection['plan_id'] is None and inspection['scope'] is None

def test_failed_upload_still_has_downloadable_answer(clone_lab,monkeypatch):
    app,c,h,fake,options=clone_lab
    def fail(*args): raise RuntimeError('ISO storage is full')
    monkeypatch.setattr(operations,'upload_generated',fail)
    plan=app.simple.plan(options)
    job=app.simple.enqueue(dict(plan_id=plan['id'],confirmed=True))['jobs'][0]
    assert app.run_next_job()
    assert c.get(job['url'],headers=h).json['status']=='failed'
    archive=c.get(job['url']+'/answer',headers=h)
    assert archive.status_code==200
    with zipfile.ZipFile(io.BytesIO(archive.data)) as z:
        assert b'REPLACE_WITH_PASSWORD' in z.read('Autounattend.xml')
    assert not fake.started


def test_clone_firewall_follows_template_bridge(clone_lab):
    app,c,h,fake,options=clone_lab
    fake.guests[9000]['net0']='e1000=x,bridge=vmbr1'
    dest={'node':'lab','bridge':'vmbr0','bridges':['vmbr0','vmbr1'],'management_cidr':'192.0.2.0/24',
          'bridge_subnets':{'vmbr1':'198.51.100.0/24'}}
    settings=dict(options,management_cidr=dest['management_cidr'])
    assert windows_build.review_template(app.lifecycle,dest,settings)['management_cidr']=='198.51.100.0/24'
    assert windows_build.review_template(app.lifecycle,dest,dict(settings,scope_edited=True))['management_cidr']=='192.0.2.0/24'


def test_windows_library_names_can_use_spaces_and_uppercase_extension(simple_lab):
    app,c,h,inv=simple_lab
    settings=dict(os='windows',purpose='vm',name='fresh',edition=7,source='library:Windows Server 2025.ISO',password='Lab-Setup#1234')
    reviewed=app.simple.plan(settings)
    cfg=app.manager.load_plan(reviewed['id'])['configs'][0]
    assert cfg['filename']=='Windows Server 2025.ISO' and cfg['iso']=='local:iso/Windows_Server_2025.iso'
    with pytest.raises(ValueError): operations.upload_iso_name('../Windows Server.ISO')

def test_new_listener_saves_public_trust_only_for_its_machine(simple_lab,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    app,c,h,inv=simple_lab
    key=ec.generate_private_key(ec.SECP256R1())
    subject=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'test-guest')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
          .serial_number(7).not_valid_before(now).not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256()))
    pem=cert.public_bytes(serialization.Encoding.PEM).decode()
    monkeypatch.setattr(operations,'Proxmox',lambda:object())
    monkeypatch.setattr(winrm_setup,'agent_script',lambda *args:pem)
    target=dict(node='lab',vmid=201,identity='original',scope='192.0.2.10')
    winrm_setup.execute(app.manager,target,lambda _:None)
    trust=app.manager.find('winrm-trust','lab:201')
    assert trust['ca_pem']==pem and trust['identity']=='original'
    monkeypatch.setattr(winrm_setup,'agent_script',lambda *args:'invalid certificate')
    with pytest.raises(ValueError,match='public certificate'):winrm_setup.execute(app.manager,target,lambda _:None)
    assert app.manager.find('winrm-trust','lab:201')==trust

def test_fresh_windows_options_are_frozen_and_sensitive_choices_need_review(simple_lab):
    from test_simple_flow import settings
    app,c,h,inv=simple_lab
    catalog=c.get('/api/simple/options',headers=h).json
    assert len(catalog['common_programs'])==8
    assert {'NET-Framework-Core','Telnet-Client'} <= {i['id'] for i in catalog['features']}
    chosen=[o['id'] for o in catalog['windows_settings']]
    payload={**settings(),'windows_settings':chosen,'count':1,'rdp':True}
    review=c.post('/api/simple/plan',json=payload,headers=h)
    assert review.status_code==200,review.json
    assert review.json['dangerous'] and review.json['rdp']
    frozen=app.manager.load_plan(review.json['id'])['tasks']
    assert frozen[-1]['item']['id']=='setup-console-sign-in'
    compiled=guest_tasks.compile_task(frozen[-1]['item'],{})
    login=compiled[0]['ansible.windows.win_auto_logon']
    assert login['logon_count']==1 and login['password']=='{{ ansible_password }}'
    assert payload['password'] not in str(frozen) and payload['password'] not in str(review.json)
    ssh=next(t['item'] for t in frozen if t['item']['id']=='setup-openssh')
    steps=guest_tasks.compile_task(ssh,{})
    assert steps[-1]['ansible.windows.win_service']['name']=='sshd'
    assert steps[-2]['ansible.windows.win_powershell']['parameters']=={'Subnet':'192.0.2.0/24'}
    assert c.post('/api/simple/run',json={'plan_id':review.json['id'],'confirmed':True},headers=h).status_code==400
    assert c.get('/api/jobs',headers=h).json==[]
    assert c.post('/api/simple/run',json={'plan_id':review.json['id'],'confirmed':True,'risk_confirmed':True},headers=h).status_code==202


@pytest.mark.parametrize('values',[
    {'purpose':'template','windows_settings':['console-sign-in']},
    {'os':'ubuntu','windows_settings':['explorer']},
    {'windows_settings':['arbitrary']},
    {'windows_settings':[{'steps':[]}]}])
def test_fresh_settings_validate_guest_purpose_and_option_ids(simple_lab,values):
    from test_simple_flow import settings
    app,c,h,inv=simple_lab
    r=c.post('/api/simple/plan',json={**settings(),'count':1,'tasks':[],**values},headers=h)
    assert r.status_code==400 and not c.get('/api/jobs',headers=h).json


def test_core_edition_and_no_selections(simple_lab,monkeypatch):
    from test_simple_flow import settings
    import media
    app,c,h,inv=simple_lab
    data=media.inspect_source('local:iso/server.iso','lab')
    data['editions'][0].update(desktop=False,label='Server Core')
    monkeypatch.setattr(media,'inspect_source',lambda *args:data)
    blank={**settings(),'tasks':[]}
    assert app.simple.plan(blank)['tasks']==[]
    assert c.post('/api/simple/plan',json=dict(blank,windows_settings=['explorer']),headers=h).status_code==400
    terminal=[{'id':'chocolatey-package','params':{'package':'microsoft-windows-terminal'}}]
    assert c.post('/api/simple/plan',json=dict(blank,tasks=terminal),headers=h).status_code==400
    template=app.simple.plan(dict(blank,count=1,purpose='template',windows_settings=['openssh']))
    assert len(template['tasks'])==1 and not template['dangerous']


def test_new_clone_uses_reviewed_setup_and_orders_sign_in_last(clone_lab):
    app,c,h,fake,options=clone_lab
    review=app.simple.plan(dict(options,windows_settings=['console-sign-in','explorer','openssh']))
    frozen=app.manager.load_plan(review['id'])['tasks']
    assert frozen[-1]['item']['id']=='setup-console-sign-in'
    explorer=next(t for t in frozen if t['item']['id']=='setup-explorer')
    keys=[step['ansible.windows.win_regedit'] for step in guest_tasks.compile_task(explorer['item'],{})]
    assert {k['name'] for k in keys}=={'Hidden','HideFileExt'}
    assert sum('hive' in k for k in keys)==2


def test_netfx_uses_the_installer_on_iso_builds(simple_lab, clone_lab):
    from test_simple_flow import settings
    feature = {'id':'windows-feature', 'params':{'feature':'NET-Framework-Core'}}
    for app, payload, has_iso in [(simple_lab[0], settings(), True), (clone_lab[0], clone_lab[-1], False)]:
        plan = app.simple.plan(dict(payload, count=1, manual_updates=False, tasks=[feature]))
        selected, = app.manager.load_plan(plan['id'])['tasks']
        tasks = guest_tasks.compile_task(selected['item'], selected['params'])
        options = next(t['ansible.windows.win_feature'] for t in tasks if 'ansible.windows.win_feature' in t)
        assert ('source' in options) == has_iso
        assert ('ansible.windows.win_powershell' in tasks[0]) == has_iso

def test_optical_letter_conflict_reports_first_free_letter_before_preparing_disks(monkeypatch):
    cfg=dict(vmid=106,data_disks=windows_build.disks_requested([dict(size=8,label='Data1',letter='E')]))
    monkeypatch.setattr(winrm_setup,'agent_script',lambda *args:'{"optical":["D","E","F"],"used":["C","D","E","F"]}')
    with pytest.raises(ValueError,match='Data1 requested E:.*virtual DVD.*Automatic or G:'):
        windows_build.check_optical_letters(None,cfg,'verified-identity')
    cfg['data_disks'][0]['letter']=''
    monkeypatch.setattr(winrm_setup,'agent_script',lambda *args:pytest.fail('Automatic needs no optical probe'))
    windows_build.check_optical_letters(None,cfg,'verified-identity')


@pytest.mark.parametrize('letter', ['D', 'e', 'F'])
def test_iso_rejects_setup_letters_before_media_discovery(simple_lab, monkeypatch, letter):
    from test_simple_flow import settings
    app, client, headers, _ = simple_lab
    assert client.get('/api/simple/options',headers=headers).json['iso_setup_letters'] == ['D','E','F']
    monkeypatch.setattr(operations, 'inventory', lambda **kw: pytest.fail('Should reject this letter before discovery'))
    review = client.post('/api/simple/plan', headers=headers, json=settings(data_disks=[dict(size=8,letter=letter)]))
    assert review.status_code == 400 and letter.upper()+': is reserved' in review.json['error']


def test_iso_allows_automatic_and_g_and_rechecks_old_reviews(simple_lab, monkeypatch):
    from test_simple_flow import settings
    app, client, headers, _ = simple_lab
    def plan(letter):
        return app.simple.plan(settings(data_disks=[dict(size=8),dict(size=8,letter=letter)]))
    def run(review):
        return client.post('/api/simple/run', headers=headers, json=dict(plan_id=review['id'], confirmed=True))
    allowed = plan('g')
    assert [d['letter'] for d in allowed['data_disks']] == ['', 'G']
    with monkeypatch.context() as legacy:
        legacy.setattr(windows_build,'ISO_SETUP_LETTERS',())
        stale = plan('E')
        submitted = plan('D')
        first = run(submitted)
        assert first.status_code == 202
    assert run(submitted).json == first.json
    assert run(stale).status_code == 400
    assert len(client.get('/api/jobs',headers=headers).json) == 3
    assert len(app.manager.all('credentials')) == 1
