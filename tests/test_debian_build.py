import io

import pycdlib
import pytest

import debian_build
import linux_install
from test_api import service
from test_simple_flow import simple_lab


def test_discovery_sorts_locality_and_releases_and_excludes_other_media():
    files = ['library:debian-13.7.0-amd64-DVD-1.iso', 'ssd:iso/debian-13.1.0-amd64-DVD-1.iso',
             'ssd:iso/debian-13.7.0-amd64-netinst.iso', 'ssd:iso/debian-live-13.7.0-amd64.iso',
             'library:debian-12.5.0-amd64-DVD-1.iso', 'library:debian-13.7.0-arm64-netinst.iso']
    assert [r['id'] for r in debian_build.candidates([{'id': f} for f in files])] == [files[2], files[0], files[1]]


@pytest.mark.parametrize('release,valid', [('Debian GNU/Linux 13.7.0 "Trixie" - Official amd64 DVD Binary-1',True),
                                         ('Debian GNU/Linux 12.5.0 "Bookworm" - Official amd64 DVD Binary-1',False)])
def test_inspection_checks_release_inside_real_iso(tmp_path,monkeypatch,release,valid):
    image=pycdlib.PyCdlib()
    image.new(rock_ridge='1.09')
    for path,rr in [('/DISK','.disk'),('/INSTALL','install.amd'),('/DISTS','dists'),('/DISTS/TRIXIE','trixie')]:
        image.add_directory(iso_path=path,rr_name=rr)
    for path,rr,data in [('/DISK/INFO.;1','info',release.encode()),('/INSTALL/VMLINUZ.;1','vmlinuz',b'kernel'),
                         ('/INSTALL/INITRD.GZ;1','initrd.gz',b'initrd'),('/DISTS/TRIXIE/RELEASE.;1','Release',b'trixie')]:
        image.add_fp(io.BytesIO(data),len(data),iso_path=path,rr_name=rr)
    filename='debian-13.7.0-amd64-DVD-1.iso'
    image.write(str(tmp_path/filename));image.close()
    monkeypatch.setenv('ISO_DIR',str(tmp_path))
    if valid:
        assert debian_build.inspect('library:'+filename,'lab')['release']==release
    else:
        with pytest.raises(ValueError,match='official Debian 13'):
            debian_build.inspect('library:'+filename,'lab')


def test_debian_review_and_reusable_preseed(simple_lab,monkeypatch):
    app,client,h,inv=simple_lab
    source='local:iso/debian-13.7.0-amd64-DVD-1.iso'
    inv['details']['lab']['isos'].append({'volid':source})
    monkeypatch.setattr(debian_build,'inspect',lambda *a:{'release':'Debian 13.7.0','identity':'reviewed'})
    draft=dict(os='debian',name='qa-debian',linux_password='Fixture password 123!',tasks=[dict(id='linux-package',params={'package':'jq'})])
    review=client.post('/api/simple/plan',json=draft,headers=h)
    assert review.status_code==200,review.json
    assert review.json['password_login'] and draft['linux_password'] not in review.text
    plan=app.manager.load_plan(review.json['id']);cfg=plan['configs'][0]
    assert plan['source']==source and cfg['kind']=='debian-iso' and plan['recipe']['settings']['os']=='debian'
    assert cfg['username']=='debian' and cfg['disk_serial'] in cfg['preseed'] and cfg['ssh_key'] in cfg['preseed']
    late=next(line for line in cfg['preseed'].splitlines() if line.startswith('d-i preseed/late_command string '))
    assert '\\' not in late and 'echo ready > /target/var/lib/proxmate/bootstrap-ready' in late
    assert 'blkid -s LABEL' in cfg['preseed'] and '= PROXMATE_DEBIAN' in cfg['preseed']
    assert cfg['preseed'].index('device/delete') < cfg['preseed'].index('debconf-set partman-auto/disk /dev/sda')
    assert 'partman-auto/disk string /dev/proxmate-unverified' in cfg['preseed']
    assert draft['linux_password'] not in str(cfg)
    assert client.get('/api/jobs',headers=h).json==[]
    job=client.post('/api/simple/run',json=dict(plan_id=review.json['id'],confirmed=True),headers=h).json['jobs'][0]
    assert client.get(job['url'],headers=h).json['has_preseed']
    download=client.get(job['url']+'/preseed',headers=h)
    assert download.text==cfg['reusable_preseed'] and 'REPLACE_WITH_OPENSSH_PUBLIC_KEY' in download.text
    assert cfg['ssh_key'] not in download.text and cfg['disk_serial'] not in download.text
    assert client.post('/api/simple/plan',json=dict(draft,purpose='template'),headers=h).status_code==400
    assert client.post('/api/simple/plan',json=dict(draft,source_kind='cloud'),headers=h).status_code==400
    reuse={'reuse':{'job_id':job['id']},'name':'debian-copy'}
    assert client.post('/api/simple/plan',json=dict(reuse,ubuntu_password='Wrong field 123!'),headers=h).status_code==400
    repeat=client.post('/api/simple/plan',json=dict(reuse,linux_password='Fresh password 123!'),headers=h)
    assert repeat.status_code==200 and repeat.json['password_login']


def test_changed_media_fails_before_creating_vm(monkeypatch):
    import operations
    monkeypatch.setattr(operations,'Proxmox',object)
    monkeypatch.setattr(linux_install,'preflight',lambda *_:None)
    monkeypatch.setattr(debian_build,'inspect',lambda *_:dict(identity='different'))
    with pytest.raises(ValueError,match='changed since review'):
        linux_install.install(dict(kind='debian-iso',node='lab',vmid=204),'ssd:iso/debian.iso','reviewed',
                              'job',print,installer_module=debian_build)
