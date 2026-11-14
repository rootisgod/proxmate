import io
import json
import xml.etree.ElementTree as ET
from unittest.mock import Mock

import pycdlib
import pytest

import operations
from provisioning import answer_xml, answer_iso, windows_options, checksum_for
from test_api import service

NS = {'u': 'urn:schemas-microsoft-com:unattend'}
PASSWORD = 'Local-Test&Password42!'


def windows(**extra):
    return dict(accept_eula=True, wipe_disk=True, image_index=2, admin_password=PASSWORD, **extra)


def flow(**extra):
    return dict(action='provision',kind='windows',purpose='vm',node='pve',vmid=9123,
                name='test-wizard',storage='local-lvm',bridge='vmbr0',memory=4096,disk=64,
                source_kind='proxmox',iso='local:iso/windows.iso',iso_storage='local',
                windows=windows(),**extra)


def test_answer_xml_options_and_escaping():
    root = ET.fromstring(answer_xml(windows(hostname='LAB-SERVER',locale='fr-FR',keyboard='de-DE',timezone='UTC',rdp=True)))
    assert root.find('.//u:ComputerName',NS).text == 'LAB-SERVER'
    assert root.find('.//u:UserLocale',NS).text == 'fr-FR'
    assert root.find('.//u:InputLocale',NS).text == 'de-DE'
    assert root.find('.//u:AdministratorPassword/u:Value',NS).text == PASSWORD
    assert root.find('.//u:MetaData/u:Value',NS).text == '2'
    assert root.find('.//u:InstallTo/u:PartitionID',NS).text == '4'
    assert root.find('.//u:UserAuthentication',NS).text == '1'
    assert root.find('.//u:FirewallGroup/u:Profile',NS).text == 'domain,private'
    assert root.find('.//u:AutoLogon',NS) is None


def test_password_preview_redacted_and_iso_round_trip(tmp_path):
    preview=answer_xml(windows(),preview=True)
    assert PASSWORD.encode() not in preview and b'[REDACTED]' in preview
    path=tmp_path/'answer.iso';answer_iso(windows(),path)
    iso=pycdlib.PyCdlib();iso.open(str(path));out=io.BytesIO()
    iso.get_file_from_iso_fp(out,joliet_path='/Autounattend.xml');iso.close()
    assert ET.fromstring(out.getvalue()).find('.//u:AdministratorPassword/u:Value',NS).text == PASSWORD


@pytest.mark.parametrize('update', [
    {'hostname':'bad&<host'}, {'image_index':True}, {'rdp':'yes'},
    {'admin_password':'weak'}, {'admin_password':12}, {'admin_password':[]},
    {'admin_password':'Invalid\x00Password123!'}, {'timezone':'invented'}, {'ui_language':'xx-XX'},
])
def test_invalid_windows_settings_rejected(update):
    with pytest.raises(ValueError):windows_options(dict(windows(),**update))


def test_answer_download_requires_explicit_consent():
    with pytest.raises(ValueError,match='Confirm'):
        answer_xml(dict(windows(),accept_eula=False))
    with pytest.raises(ValueError,match='Confirm'):
        answer_xml(dict(windows(),wipe_disk=False))


def test_plan_is_side_effect_free_and_secrets_encrypted(service,monkeypatch):
    app,client=service
    monkeypatch.setattr(operations,'Proxmox',lambda:pytest.fail('Plan must not contact Proxmox'))
    auth={'Authorization':'Bearer '+app.API_TOKEN}
    result=client.post('/api/provision/plan',json=flow(),headers=auth)
    assert result.status_code==200 and PASSWORD not in result.text
    assert client.post('/api/jobs',json=flow(),headers=auth).status_code==400
    payload=flow(confirmed=True)
    auth['Idempotency-Key']='wizard-test'
    first=client.post('/api/jobs',json=payload,headers=auth)
    second=client.post('/api/jobs',json=payload,headers=auth)
    assert first.status_code==202 and first.json==second.json
    with app.db() as con:
        stored=con.execute('SELECT payload FROM jobs').fetchone()['payload']
    assert stored.startswith('encrypted:') and PASSWORD not in stored
    assert app.decode_payload(stored)['windows']['admin_password']==PASSWORD
    assert PASSWORD not in client.get(first.json['url'],headers=auth).text
    assert client.post('/api/windows/answer-file',json=windows()).status_code==401
    download=client.post('/api/windows/answer-file',json=windows(),headers=auth)
    assert download.status_code==200 and download.headers['Cache-Control']=='no-store'


def test_linux_versions_and_static_network_validation():
    d=dict(action='linux-template',node='pve',name='linux',vmid=9123,storage='local-lvm',bridge='vmbr0',profile='fedora-44',network_mode='static',address='192.168.1.50/24',gateway='192.168.1.1',dns='192.168.1.1 1.1.1.1')
    assert operations.validate(d)['profile']=='fedora-44'
    for update in [{'profile':'made-up'},{'gateway':'10.0.0.1'},{'address':'bad'},{'dns':'1.1.1.1; reboot'},{'outcome':'template','start':True}]:
        with pytest.raises(ValueError):operations.validate(dict(d,**update))


def test_checksums_exact_filename():
    digest='a'*64
    assert checksum_for(f'{digest} *image.qcow2','image.qcow2')==digest
    assert checksum_for(f'SHA256 (image.qcow2) = {digest}','image.qcow2')==digest
    with pytest.raises(ValueError):checksum_for(f'{digest} image.qcow2.other','image.qcow2')


class FakeProxmox:
    def __init__(self):
        self.calls=[]; self.url='https://example.invalid';self.session=Mock()
        self.session.post.return_value.ok=True;self.session.post.return_value.json.return_value={'data':'UPID:upload'}
    def resources(self):return [{'type':'node','node':'pve','status':'online'}]
    def wait(self,*args):pass
    def call(self,method,path,**kw):
        self.calls.append((method,path,kw))
        if path.endswith('/storage'):return [{'storage':'local-lvm','content':'images','active':1},{'storage':'local','content':'iso','active':1}]
        if path.endswith('/network'):return [{'iface':'vmbr0','type':'bridge'}]
        if path.endswith('/content'):return [{'volid':'local:iso/windows.iso'}]
        return 'UPID:test'


def test_windows_flow_attaches_answer_iso_and_does_not_start_by_default(monkeypatch):
    p=FakeProxmox();monkeypatch.setattr(operations,'Proxmox',lambda:p)
    logs=[];operations.execute(flow(),logs.append)
    creation=[c for c in p.calls if c[0]=='POST' and c[1]=='nodes/pve/qemu']
    assert len(creation)==1
    args=creation[0][2]
    assert args['sata1'].startswith('local:iso/proxmate-unattend-9123-')
    assert args['ide2']=='local:iso/windows.iso,media=cdrom'
    assert not any(c[1].endswith('/start') for c in p.calls)
    assert PASSWORD not in '\n'.join(logs)
    assert any('not installation verified' in s for s in logs)


def test_flow_collision_prevents_even_media_upload(monkeypatch):
    p=FakeProxmox();p.resources=lambda:[{'vmid':9123}]
    monkeypatch.setattr(operations,'Proxmox',lambda:p)
    with pytest.raises(ValueError,match='already exists'):operations.execute(flow(),lambda s:None)
    p.session.post.assert_not_called();assert p.calls==[]


def test_finish_setup_never_deletes_arbitrary_media(monkeypatch):
    p=FakeProxmox()
    p.resources=lambda:[{'type':'node','node':'pve','status':'online'},{'type':'qemu','node':'pve','vmid':9123,'status':'stopped'}]
    p.call=Mock(return_value={'sata1':'local:iso/important.iso,media=cdrom'})
    monkeypatch.setattr(operations,'Proxmox',lambda:p)
    with pytest.raises(ValueError,match='No Proxmate-generated'):operations.execute({'action':'finish-setup','node':'pve','vmid':9123,'prepared':True},lambda s:None)
    assert p.call.call_count==1
