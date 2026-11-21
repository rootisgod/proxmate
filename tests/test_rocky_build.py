from contextlib import contextmanager
import io
from types import SimpleNamespace

import pytest

import rocky_build
import operations
import management
import linux_install
from vm_config import machine_identity
from test_api import service
from test_simple_flow import simple_lab


def test_discovery_ranks_version_then_offline_media_then_node():
    names=['library:Rocky-10.3-x86_64-dvd1.iso','ssd:iso/Rocky-10.2-x86_64-dvd.iso',
           'ssd:iso/Rocky-10.3-x86_64-dvd1.iso','library:Rocky-9.6-x86_64-minimal.iso',
           'library:Rocky-10.3-x86_64-boot.iso','library:Rocky-10.3-aarch64-dvd.iso',
           'ssd:iso/Rocky-10.3-20261113.0-x86_64-minimal.iso']
    assert [i['id'] for i in rocky_build.candidates([{'id':n} for n in names])]==[names[i] for i in (2,0,6,4,1)]


def test_inspection_requires_matching_release_and_install_tree(monkeypatch):
    files={name:b'fixture' for name in (*rocky_build.BOOT_FILES,'/images/install.img',
                                        '/BaseOS/repodata/repomd.xml','/AppStream/repodata/repomd.xml')}
    @contextmanager
    def disc(*args):
        yield SimpleNamespace(pvd=SimpleNamespace(volume_identifier=b'Rocky-10-3-x86_64-dvd'))
    def entry(iso,name,*,open_file=False):
        if name not in files: return None
        if open_file: return io.BytesIO(files[name])
        return SimpleNamespace(extent_location=lambda:128,get_data_length=lambda:len(files[name]))
    monkeypatch.setattr(rocky_build.media,'open_iso',disc)
    monkeypatch.setattr(rocky_build.media,'image_entry',entry)
    tree=b'[release]\nname=Rocky Linux\nversion=10.3\n[tree]\narch=x86_64\n[stage2]\nmainimage=images/install.img\n'
    files['/.treeinfo']=tree
    def inspect(kind):
        return rocky_build.inspect('local:iso/Rocky-10.3-x86_64-'+kind+'.iso','lab')
    assert inspect('dvd')['release']=='Rocky Linux 10.3'
    for old,new in ((b'x86_64',b'aarch64'),(b'10.3',b'9.6'),(b'Rocky Linux',b'Other')):
        files['/.treeinfo']=tree.replace(old,new)
        with pytest.raises(ValueError): inspect('dvd')
    files['/.treeinfo']=tree
    del files['/AppStream/repodata/repomd.xml']
    with pytest.raises(ValueError): inspect('dvd')
    with pytest.raises(ValueError): inspect('minimal')
    files['/Minimal/repodata/repomd.xml']=b'minimal'
    with pytest.raises(ValueError): inspect('minimal')
    files['/.treeinfo']=tree.replace(b'name=Rocky Linux',b'name=Rocky Linux Minimal')
    assert inspect('minimal')['kind']=='minimal'
    files['/.discinfo']=b'123456.0\n10.3\nx86_64\n'
    files['/EFI/BOOT/grub.cfg']=b"menuentry 'Install Rocky Linux 10.3' {"
    with pytest.raises(ValueError): inspect('boot')
    del files['/.treeinfo']
    assert inspect('boot')['kind']=='boot'
    for path in ('/images/install.img','/EFI/BOOT/grub.cfg'):
        original=files.pop(path)
        with pytest.raises(ValueError): inspect('boot')
        files[path]=original
    files['/.discinfo']=b'123456.0\n10.3\naarch64\n'
    with pytest.raises(ValueError): inspect('boot')


def test_cpu_check_fails_closed():
    def node(flags):
        return SimpleNamespace(call=lambda *args: {'cpuinfo':{'flags':' '.join(flags)}})
    rocky_build.check_cpu(node(rocky_build.V3_FLAGS),'lab')
    for missing in (set(),rocky_build.V3_FLAGS-{'bmi2'}):
        with pytest.raises(ValueError,match='x86-64-v3'): rocky_build.check_cpu(node(missing),'lab')


def test_template_discovery_checks_the_current_owner_of_a_reused_vm_id(monkeypatch):
    current=dict(template=1,vmgenid='replacement',name='rocky-base')
    old=dict(node='lab',vmid=201,config={'kind':'debian-iso'},machine_identity=machine_identity({'vmgenid':'old'}))
    new=dict(old,config={'kind':'rocky-iso'},machine_identity=machine_identity(current))
    row=dict(node='lab',vmid=201,template=1,type='qemu')
    client=SimpleNamespace(call=lambda *a:current,session=SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(operations,'Proxmox',lambda:client)
    result=linux_install.templates([old,new],[row])
    assert result['debian_templates']==[] and result['rocky_templates']==[dict(node='lab',vmid=201,name='rocky-base')]


def test_refresh_reads_the_published_key_only_for_a_rocky_guest():
    calls=[]
    class Agent:
        distro='rocky'
        def call(self,method,path,**kwargs):
            if path.endswith('/get-osinfo'):
                return {'result':{'id':self.distro}}
            calls.append(kwargs['file'])
            if kwargs['file']=='/etc/ssh/ssh_host_ed25519_key.pub': raise RuntimeError('Permission denied')
            assert kwargs['file']=='/etc/proxmate/ssh-host.pub'
            return {'content':'ssh-ed25519 dGVzdA=='}
    agent=Agent()
    assert management.agent_trust(agent,'guest','linux')['fingerprint'].startswith('SHA256:')
    assert calls==['/etc/ssh/ssh_host_ed25519_key.pub','/etc/proxmate/ssh-host.pub']
    calls.clear()
    agent.distro='debian'
    with pytest.raises(RuntimeError,match='Permission denied'): management.agent_trust(agent,'guest','linux')
    assert calls==['/etc/ssh/ssh_host_ed25519_key.pub']


@pytest.mark.parametrize('kind',['dvd','minimal','boot'])
def test_review_run_and_redacted_kickstart(simple_lab,monkeypatch,kind):
    app,client,h,inv=simple_lab
    source=f'local:iso/Rocky-10.3-x86_64-{kind}.iso'
    inv['details']['lab']['isos'].append({'volid':source})
    monkeypatch.setattr(rocky_build,'inspect',lambda *args:dict(release='Rocky Linux 10.3',version='10.3',kind=kind,label='Rocky-DVD',identity='iso'))
    monkeypatch.setattr(operations,'Proxmox',lambda:SimpleNamespace(call=lambda *args:{'cpuinfo':{'flags':' '.join(rocky_build.V3_FLAGS)}}))
    draft=dict(os='rocky',name='test-rocky',linux_password='Test fixture credentials 321!')
    review=client.post('/api/simple/plan',json=draft,headers=h)
    assert review.status_code==200,review.json
    plan=app.manager.load_plan(review.json['id']);cfg=plan['configs'][0]
    assert plan['source']==source and cfg['dvd_label']=='Rocky-DVD' and cfg['username']=='rocky'
    assert any('mirror list' in note for note in review.json['notes'])==(kind!='dvd')
    for answer in (cfg['kickstart'],cfg['reusable_kickstart']):
        assert ('\ncdrom\n' in answer)==(kind!='boot')
        assert ('\nurl --mirrorlist=' in answer)==(kind=='boot')
        assert ('\nrepo --name=proxmate-appstream' in answer)==(kind!='dvd')
        assert ('\nrepo --name=proxmate-baseos' in answer)==(kind=='minimal')
        for repo in ('BaseOS','AppStream'):
            assert (f'https://mirrors.rockylinux.org/mirrorlist?arch=x86_64&protocol=https&repo={repo}-10.3' in answer)==(kind!='dvd')
    assert cfg['disk_serial'] in cfg['kickstart'] and cfg['ssh_key'] in cfg['kickstart']
    assert 'guest-file-open,guest-file-read,guest-file-close' in cfg['kickstart']
    assert 'guest-exec' not in cfg['kickstart'] and 'guest-file-write' not in cfg['kickstart']
    assert '/etc/proxmate/ssh-host.pub' in cfg['kickstart'] and 'PathChanged=/etc/ssh/ssh_host_ed25519_key.pub' in cfg['kickstart']
    assert draft['linux_password'] not in str(cfg) and draft['linux_password'] not in review.text
    assert plan['tasks'][-1]['item']['steps'][-1]['ansible.builtin.service']['name']=='sshd'
    assert client.get('/api/jobs',headers=h).json==[]
    job,=client.post('/api/simple/run',json=dict(plan_id=review.json['id'],confirmed=True),headers=h).json['jobs']
    assert client.get(job['url'],headers=h).json['has_kickstart']
    answer=client.get(job['url']+'/kickstart',headers=h)
    assert answer.text==cfg['reusable_kickstart'] and 'REPLACE_WITH_OPENSSH_PUBLIC_KEY' in answer.text
    assert cfg['ssh_key'] not in answer.text and cfg['disk_serial'] not in answer.text
    assert client.get(job['url']+'/preseed',headers=h).status_code==404
    for override in ({'source_kind':'cloud'},{'purpose':'template'},{'profile':'debian-13'}):
        assert client.post('/api/simple/plan',json=dict(draft,**override),headers=h).status_code==400
