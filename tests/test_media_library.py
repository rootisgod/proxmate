from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

import media
import media_library
import operations
import ubuntu_build
import debian_build
import rocky_build
from test_api import service
from test_simple_flow import simple_lab


BASE='/api/simple/media'
UBUNTU='local:iso/ubuntu-24.04.3-live-server-amd64.iso'
DEBIAN='local:iso/debian-13.1.0-amd64-netinst.iso'
ROCKY='local:iso/Rocky-10.1-x86_64-boot.iso'
WINDOWS='local:iso/windows-server.iso'
TOOLS='local:iso/virtio-win-0.1.266.iso'


@pytest.fixture
def library(service,monkeypatch):
    app,client=service
    with app.db() as con:
        app.server.write(con,dict(app.server.read(),ssh_fingerprint='SHA256:'+'a'*43))
    log=[]
    volumes=[dict(volid=v,ctime=10,size=2**30) for v in (UBUNTU,DEBIAN,ROCKY,WINDOWS,TOOLS)]
    class Reader:
        session=SimpleNamespace(close=lambda:None)
        def call(self,method,path,**params):
            assert method=='GET'
            log.append(path)
            if path=='nodes': return [dict(node=n,status='online') for n in ('one','two')]
            if path in ('nodes/one/storage','nodes/two/storage'):
                return [dict(storage='local',active=1,content='iso,backup'),dict(storage='disk',active=1,content='images')]
            if path=='nodes/one/storage/local/content' and params==dict(content='iso'): return volumes
            if path=='nodes/two/storage/local/content': raise RuntimeError('upstream internal detail')
            if path=='nodes/one/status': return dict(cpuinfo=dict(flags=' '.join(rocky_build.V3_FLAGS)))
            pytest.fail('Unexpected storage or machine operation '+path)
    monkeypatch.setattr(operations,'Proxmox',Reader)
    monkeypatch.setattr(operations,'local_iso_inventory',lambda:[dict(name='install.iso',bytes=1000,mtime=3000)])
    monkeypatch.setattr(media,'verify_ssh_node',lambda node:log.append('ssh '+node))
    return app,client,{'Authorization':'Bearer '+app.API_TOKEN},volumes,log


def test_scoped_listing_uses_only_inventory_and_shows_partial_failures(library):
    app,c,h,_,log=library
    assert c.get(BASE).status_code==401
    response=c.get(BASE,headers=h)
    assert response.status_code==200
    listing=response.json
    assert listing['node']=='one' and listing['driver_isos']==[TOOLS]
    assert [f['family'] for f in listing['files']]==['ubuntu','debian','rocky','windows','virtio','other']
    assert listing['files'][-1]==dict(id='library:install.iso',name='install.iso',location='library',
                                       storage=None,bytes=1000,revision='3000-1000',family='other')
    assert log==['nodes','nodes/one/storage','nodes/one/storage/local/content']
    partial=c.get(BASE+'?node=two',headers=h).json
    assert len(partial['files'])==1 and partial['warnings'] and 'upstream' not in repr(partial)
    assert c.get(BASE+'?node=missing',headers=h).status_code==400
    assert not c.get('/api/jobs',headers=h).json


def test_windows_filename_labels_match_both_discovery_locations_and_the_media_library(simple_lab):
    _, client, headers, inv = simple_lab
    cases = {'win11-x64.iso': 'Windows 11', 'Windows_10.iso': 'Windows 10', 'WIN_2022.iso': 'Windows Server 2022',
             '2016_EVAL.iso': 'Windows Server 2016', 'server.iso': 'Windows Server', 'winsrv.iso': 'Windows installation media',
             'sql-server-2022.iso': None, 'Exchange_Server_2019.iso': None, 'virtio-win-2025.iso': None,
             'ubuntu-24.04-live-server-amd64.iso': None, 'proxmate-unattend-2025.iso': None, 'install.iso': None, 'archlinux-2025.08.01-x86_64.iso': None,
             'VeeamBackup&Replication_12.3.1.1139_20250331.iso': None}
    inv['details']['lab']['isos'] = [dict(volid='windows-storage:iso/' + name, size=123, ctime=5) for name in cases]
    inv['local_isos'] = [dict(name=name, bytes=123, mtime=5) for name in cases]
    response = client.get('/api/simple/options', headers=headers)
    assert response.status_code == 200, response.json
    node, local = response.json['destinations'][0]['isos'], response.json['local_isos']
    assert {row['label']: row['windows_label'] for row in node} == cases
    assert {row['label'].removesuffix(' (ISO library)'): row['windows_label'] for row in local} == cases
    for row in node + local:
        name = row['label'].removesuffix(' (ISO library)')
        assert (media_library.family(row['id'], name) == 'windows') == bool(cases[name])
        assert row['revision'] == '5-123'


def test_recommended_checks_prefer_server_installers_and_native_distro_order(library):
    _,client,headers,volumes,_=library
    for filename in ('Win11_23H2.iso','server2025.iso','ubuntu-26.04-live-server-amd64.iso'):
        volumes.append(dict(volid='local:iso/'+filename,ctime=10,size=2**30))
    listing=client.get(BASE,headers=headers).json
    assert listing['recommended']['windows']=='local:iso/server2025.iso'
    assert listing['recommended']['ubuntu']=='local:iso/ubuntu-26.04-live-server-amd64.iso'


def test_explicit_windows_check_reports_each_edition_and_pins_driver_revision(library,monkeypatch):
    _,c,h,volumes,log=library
    def editions(*args):
        log.append('editions')
        return dict(editions=[dict(index=i,label=label,build=b,language=lang) for i,label,b,lang in
                              [(1,'2016 Core','14393','en-US'),(2,'2025 Desktop','26100','en-GB'),
                               (3,'2022 Core','20348','en-US'),(4,'Other locale','26100','xx')]])
    def drivers(source,node,builds):
        assert source==TOOLS and node=='one' and builds==['26100','20348']
        log.append('drivers');return {'26100':'digest'}
    monkeypatch.setattr(media,'inspect_source',editions)
    monkeypatch.setattr(media,'review_guest_agent_builds',drivers)
    data=dict(node='one',source='library:install.iso',os='windows')
    def check(): return c.post(BASE+'/verify',json=data,headers=h).json
    result=check()
    assert result['status']=='verified' and result['agent_iso']==TOOLS
    assert [e['compatible'] for e in result['editions']]==[False,True,False,False]
    files=lambda:c.get(BASE,headers=h).json['files']
    assert files()[-1]['check']==result and log.count('editions')==log.count('drivers')==1
    volumes[-1]['ctime']+=1
    assert 'check' not in files()[-1]
    check()
    assert log.count('editions')==2
    volumes.pop()
    result=check()
    assert result['status']=='missing' and 'VirtIO' in result['detail'] and result['editions']
    assert not c.get('/api/jobs',headers=h).json


@pytest.mark.parametrize('os_name,source,installer,metadata',[
    ('ubuntu',UBUNTU,ubuntu_build,{'release':'Ubuntu 24.04 LTS','install_sources':{'ubuntu-server':[1,256]}}),
    ('debian',DEBIAN,debian_build,{'release':'Debian 13 amd64 netinst'}),
    ('rocky',ROCKY,rocky_build,{'release':'Rocky Linux 10.1','kind':'boot'}),
])
def test_linux_check_reuses_installer_and_node_preflight(library,monkeypatch,os_name,source,installer,metadata):
    _,c,h,_,log=library
    def inspect(src,node):
        assert (src,node)==(source,'one')
        log.append('installer');return metadata
    monkeypatch.setattr(installer,'inspect',inspect)
    params=dict(node='one',source=source,os=os_name)
    result=c.post(BASE+'/verify',json=params,headers=h).json
    assert result['status']=='verified' and result['release']==metadata['release']
    assert ('nodes/one/status' in log)==(os_name=='rocky') and 'ssh one' in log
    def fail(*args): raise ValueError('Installer boot files missing.')
    monkeypatch.setattr(installer,'inspect',fail)
    result=c.post(BASE+'/verify',json=params,headers=h).json
    assert result['status']=='failed' and result['detail']=='Installer boot files missing.'
    assert log.count('ssh one')==1
    assert not c.get('/api/jobs',headers=h).json


def test_verification_rejects_other_hosts_volumes_and_missing_ssh(library,monkeypatch):
    app,c,h,_,_=library
    for diff in [dict(node='other'),dict(source='local:iso/missing.iso'),dict(os='debian'),
                 dict(agent_iso='library:install.iso'),dict(os={}),dict(node='../one')]:
        res=c.post(BASE+'/verify',json={**dict(node='one',os='ubuntu',source=UBUNTU),**diff},headers=h)
        assert res.status_code==400,res.json
    with app.db() as con: app.server.write(con,dict(app.server.read(),ssh_fingerprint=''))
    params=dict(node='one',os='ubuntu',source=UBUNTU)
    res=c.post(BASE+'/verify',json=params,headers=h)
    assert res.status_code==200 and res.json['status']=='missing' and 'SSH' in res.json['detail']
    assert c.post(BASE+'/verify',json=params,headers={**h,'X-Proxmate-Server':'old'}).status_code==409


def test_explicit_checks_pin_the_selected_host_without_holding_worker_lock(library,monkeypatch):
    app,client,h,_,_=library
    entered=threading.Barrier(3)
    release=threading.Event()
    def inspect(*args):
        entered.wait(timeout=4)
        assert release.wait(timeout=4)
        return {'release':'Debian 13'}
    monkeypatch.setattr(debian_build,'inspect',inspect)
    params=dict(node='one',os='debian',source=DEBIAN)
    def check():
        return app.app.test_client().post(BASE+'/verify',json=params,headers=h)
    with ThreadPoolExecutor(max_workers=2) as pool:
        workers=[pool.submit(check) for _ in range(2)]
        try:
            entered.wait(timeout=4)
            assert app.server.lock.acquire(timeout=1)
            try: assert app.server.machine_requests==2
            finally: app.server.lock.release()
            assert check().status_code==429
            response=client.post('/api/server/disconnect',json={'expected_id':app.server.read()['id']},headers=h)
            assert response.status_code==400 and 'current machine request' in response.json['error']
            assert not client.get('/api/jobs',headers=h).json
        finally:
            release.set()
        assert [w.result().json['status'] for w in workers]==['verified','verified']
        assert app.server.machine_requests==0
