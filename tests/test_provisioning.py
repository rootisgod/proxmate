import io
import json
import xml.etree.ElementTree as ET
from unittest.mock import Mock

import pycdlib
import pytest

import operations
from provisioning import answer_xml, answer_iso, windows_options, checksum_for, windows_setup_key, WINDOWS_SETUP_KEYS
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


@pytest.mark.parametrize('build,edition_id', WINDOWS_SETUP_KEYS)
def test_public_setup_key_matches_media_edition_and_only_sets_setup_pass(build,edition_id,tmp_path):
    key=windows_setup_key(dict(build=build,edition_id=edition_id,name='Windows Server'))
    assert key==WINDOWS_SETUP_KEYS[(build,edition_id)]
    path=tmp_path/'key-answer.iso';answer_iso(windows(setup_key=key),path)
    iso=pycdlib.PyCdlib();iso.open(str(path));out=io.BytesIO()
    iso.get_file_from_iso_fp(out,joliet_path='/Autounattend.xml');iso.close()
    root=ET.fromstring(out.getvalue())
    product=root.find("u:settings[@pass='windowsPE']/u:component[@name='Microsoft-Windows-Setup']/u:UserData/u:ProductKey",NS)
    assert product.find('u:Key',NS).text==key
    assert product.find('u:WillShowUI',NS).text=='OnError'
    assert len(root.findall('.//u:ProductKey',NS))==1
    assert root.find('.//u:MetaData/u:Value',NS).text=='2'


@pytest.mark.parametrize('edition', [
    dict(build='26100',edition_id='ServerStandardEval'),
    dict(build='26100',edition_id='ServerStandard',name='Windows Server Evaluation'),
    dict(build='26100',edition_id='ServerAzureCor'),
    dict(build='99999',edition_id='ServerStandard'),
    dict(build='26100',name='Windows Server Standard'),
])
def test_setup_key_never_guessed_for_unknown_or_evaluation_media(edition):
    assert windows_setup_key(edition) is None


def test_private_or_empty_setup_key_rejected():
    for key in ('XXXXX-XXXXX-XXXXX-XXXXX-XXXXX','',None,[]):
        with pytest.raises(ValueError,match='public Microsoft'):
            windows_options(windows(setup_key=key))


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


@pytest.mark.parametrize('accepted', [True, False])
def test_clone_oobe_honors_the_reviewed_windows_license(accepted):
    settings = dict(windows(), accept_eula=accepted, wipe_disk=False)
    if not accepted:
        with pytest.raises(ValueError, match='Confirm Windows licensing'):
            answer_xml(settings, clone=True)
    root = ET.fromstring(answer_xml(settings, clone=True, preview=not accepted))
    hide = root.find("u:settings[@pass='oobeSystem']/u:component[@name='Microsoft-Windows-Shell-Setup']/u:OOBE/u:HideEULAPage", NS)
    assert (hide is not None and hide.text == 'true') == accepted
    assert root.find("u:settings[@pass='windowsPE']", NS) is None


def test_checksums_exact_filename():
    digest='a'*64
    assert checksum_for(f'{digest} *image.qcow2','image.qcow2')==digest
    assert checksum_for(f'SHA256 (image.qcow2) = {digest}','image.qcow2')==digest
    with pytest.raises(ValueError):checksum_for(f'{digest} image.qcow2.other','image.qcow2')


class FakeProxmox:
    def __init__(self):
        self.calls=[]; self.url='https://example.invalid';self.session=Mock()
        self.session.post.return_value.status_code=200;self.session.post.return_value.json.return_value={'data':'UPID:upload'}
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


@pytest.mark.parametrize('generated', [True, False])
def test_redirect_stops_media_upload(monkeypatch, tmp_path, generated):
    p = FakeProxmox()
    monkeypatch.setattr(operations, 'Proxmox', lambda: p)
    monkeypatch.setenv('ISO_DIR', str(tmp_path))
    (tmp_path / 'library.iso').write_bytes(b'library')
    p.session.post.return_value.status_code = 307
    job = flow() if generated else dict(action='upload-iso', node='pve', storage='local', filename='library.iso')
    with pytest.raises(RuntimeError, match='[Uu]pload failed'):
        operations.execute(job, lambda message: None)
    assert p.session.post.call_args.kwargs['allow_redirects'] is False
    p.session.post.return_value.json.assert_not_called()
    assert all(method == 'GET' for method, _, _ in p.calls)


def test_changed_guest_agent_msi_stops_before_upload_or_vm_creation(monkeypatch):
    import media
    p=FakeProxmox();original=p.call
    def call(method,path,**kw):
        if path.endswith('/content'):return [{'volid':'local:iso/windows.iso'},{'volid':'local:iso/virtio.iso'}]
        return original(method,path,**kw)
    p.call=call;monkeypatch.setattr(operations,'Proxmox',lambda:p)
    monkeypatch.setattr(media,'guest_agent_sha256',lambda *args:'b'*64)
    d=flow(bootstrap=dict(enabled=True,management_cidr='192.0.2.0/24',agent_iso='local:iso/virtio.iso',agent_sha256='a'*64))
    with pytest.raises(ValueError,match='MSI changed since review'):operations.execute(d,lambda _:None)
    p.session.post.assert_not_called()
    assert not any(method!='GET' for method,_,_ in p.calls)


@pytest.mark.parametrize('action', ['finish-setup', 'convert-template'])
@pytest.mark.parametrize('local_status,cluster_status', [('stopped','running'),('running','stopped')])
def test_template_preparation_uses_node_status_at_the_time_of_action(monkeypatch, action, local_status, cluster_status):
    host = FakeProxmox()
    host.resources = lambda: [{'type':'node','node':'pve','status':'online'},
                              {'type':'qemu','node':'pve','vmid':9123,'status':cluster_status}]
    generated = 'local:iso/proxmate-unattend-9123-' + 'a'*32 + '.iso'
    def call(method, path, **args):
        host.calls.append((method,path,args))
        if path.endswith('/status/current'): return {'status':local_status}
        if path.endswith('/config') and method == 'GET':
            return {'sata1':generated+',media=cdrom'} if action == 'finish-setup' else {}
        return 'UPID:test'
    host.call = call
    monkeypatch.setattr(operations,'Proxmox',lambda:host)
    job = dict(action=action,node='pve',vmid=9123,prepared=True)
    if local_status == 'running':
        with pytest.raises(ValueError,match='Shut down|must be stopped'):
            operations.execute(job,lambda _:None)
        assert all(method == 'GET' for method,_,_ in host.calls)
    else:
        operations.execute(job,lambda _:None)
        writes = [method for method,_,_ in host.calls if method != 'GET']
        assert writes == (['PUT','DELETE'] if action == 'finish-setup' else ['POST'])


def test_finish_setup_never_deletes_arbitrary_media(monkeypatch):
    p=FakeProxmox()
    p.resources=lambda:[{'type':'node','node':'pve','status':'online'},{'type':'qemu','node':'pve','vmid':9123,'status':'stopped'}]
    p.call=Mock(side_effect=[{'status':'stopped'},{'sata1':'local:iso/important.iso,media=cdrom'}])
    monkeypatch.setattr(operations,'Proxmox',lambda:p)
    with pytest.raises(ValueError,match='No Proxmate-generated'):operations.execute({'action':'finish-setup','node':'pve','vmid':9123,'prepared':True},lambda s:None)
    assert all(call.args[0]=='GET' for call in p.call.call_args_list)


@pytest.mark.parametrize('reason', ['normal', 'changed-vm', 'changed-media', 'stopped', 'old-boot', 'late-start', 'api-error'])
def test_windows_boot_input_is_bounded_and_checks_target(monkeypatch, reason):
    now = [25 if reason == 'late-start' else 0]
    monkeypatch.setattr(operations.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(operations.time, 'sleep', lambda n: now.__setitem__(0, now[0] + n))
    calls = []
    def call(method, path, **args):
        calls.append((method, path, args))
        if reason == 'api-error':
            raise RuntimeError('sensitive remote error')
        if path.endswith('/config'):
            return {'smbios1': 'uuid=other' if reason == 'changed-vm' else 'uuid=new',
                    'sata1': ('local:iso/other.iso' if reason == 'changed-media' else 'local:iso/answer.iso') + ',media=cdrom'}
        if path.endswith('/status/current'):
            return {'status': 'stopped' if reason == 'stopped' else 'running',
                    'uptime': 50 if reason == 'old-boot' else now[0]}
    p = Mock();p.call = call
    logs = []
    operations.windows_first_boot(p, 'nodes/pve/qemu/9123', 'uuid=new', 'local:iso/answer.iso', 0, logs.append)
    keys = [c for c in calls if c[0] == 'PUT']
    assert len(keys) == (20 if reason == 'normal' else 0)
    assert all(c[1] == 'nodes/pve/qemu/9123/sendkey' and c[2] == {'key': 'spc'} for c in keys)
    assert 'sensitive remote error' not in '\n'.join(logs)


@pytest.mark.parametrize('os,answer,start,expected', [('windows',True,True,True), ('windows',True,False,False), ('windows',False,True,False), ('linux',False,True,False)])
def test_boot_input_only_for_new_started_unattended_windows(monkeypatch, os, answer, start, expected):
    p = FakeProxmox()
    monkeypatch.setattr(operations, 'Proxmox', lambda:p)
    boot = Mock();monkeypatch.setattr(operations, 'windows_first_boot', boot)
    d = dict(action='installer',node='pve',vmid=9123,name='new',storage='local-lvm',bridge='vmbr0',iso='local:iso/windows.iso',os=os,start=start)
    if answer: d['_answer_volume'] = 'local:iso/answer.iso'
    operations.execute(d, lambda _:None)
    assert boot.called == expected
    creation = next(c[2] for c in p.calls if c[1] == 'nodes/pve/qemu')
    if os == 'windows' and answer:
        assert creation['boot'] == 'order=sata0;ide2'
        assert creation['smbios1'].startswith('uuid=')
    if expected:
        assert p.calls[-1][1].endswith('/status/start')
        assert boot.call_args.args[2] == creation['smbios1']


def test_existing_vm_start_never_sends_boot_keys(monkeypatch):
    p = FakeProxmox()
    p.resources = lambda:[{'type':'node','node':'pve','status':'online'}, {'type':'qemu','node':'pve','vmid':9123}]
    monkeypatch.setattr(operations, 'Proxmox', lambda:p)
    boot = Mock();monkeypatch.setattr(operations, 'windows_first_boot', boot)
    operations.execute(dict(action='start',node='pve',vmid=9123), lambda _:None)
    boot.assert_not_called()
