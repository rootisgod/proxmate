"""Reviewed Linux installer media, template provenance and cloud-init cloning."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote
from requests import RequestException

import media
import operations
from vm_config import config_identity, machine_identity, safe_guest_config, vm_path

ISO_KINDS = ('ubuntu-iso', 'debian-iso', 'rocky-iso')
CLONE_KINDS = ('ubuntu-clone', 'debian-clone', 'rocky-clone')


def boot_disc(path, node, source, kernel_initrd, grub_config, files, label):
    with tempfile.TemporaryDirectory(prefix='proxmate-grub-') as temp:
        root = Path(temp)
        grub = root/'boot/grub'
        grub.mkdir(parents=True)
        boot_files = root/'boot/proxmate'
        boot_files.mkdir()
        with media.open_iso(source, node) as iso:
            for entry, name in zip(kernel_initrd, ('vmlinuz', 'initrd'), strict=True):
                record = media.image_entry(iso, entry)
                if not record or not 0 < record.get_data_length() <= 512*1024**2:
                    raise ValueError('The selected ISO has unusable installer boot files.')
                with media.image_entry(iso, entry, open_file=True) as original, (boot_files/name).open('wb') as output:
                    shutil.copyfileobj(original, output, length=1024**2)
        (grub/'grub.cfg').write_text(grub_config, encoding='utf-8')
        for name, text in files.items():
            (root/name).write_text(text, encoding='utf-8')
        try:
            subprocess.run(['grub-mkrescue', '-d', '/usr/lib/grub/i386-pc', '-o', str(path),
                            str(root), '--', '-volid', label], capture_output=True, check=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('Could not generate unattended installer boot media.') from None


def preflight(client, cfg):
    node, vmid = cfg['node'], cfg['vmid']
    resources = client.resources()
    if any(r.get('vmid') == vmid for r in resources):
        raise ValueError('VM ID is already in use. Review the build again.')
    if not any(r.get('type') == 'node' and r['node'] == node and r.get('status') == 'online' for r in resources):
        raise ValueError('The selected node is offline.')
    stores = client.call('GET', f'nodes/{node}/storage')
    for name, content in [('storage', 'images'), ('iso_storage', 'iso')]:
        if not any(s['storage'] == cfg[name] and s.get('active') and content in s.get('content', '').split(',') for s in stores):
            raise ValueError('The selected '+content+' storage is no longer available.')
    if not any(n['iface'] == cfg['bridge'] and n.get('type') == 'bridge' for n in client.call('GET', f'nodes/{node}/network')):
        raise ValueError('The selected network bridge is no longer available.')


def install(cfg, source, identity, job_id, log, *, installer_module):
    inspect, boot_iso = installer_module.inspect, installer_module.boot_iso
    distro = cfg["kind"].split("-")[0]
    client = operations.Proxmox()
    node, vmid = cfg['node'], cfg['vmid']
    preflight(client, cfg)
    def matches(volume):
        current = inspect(volume, node)
        if current['identity'] != identity:
            return False
        if 'ubuntu_source_record' in cfg:
            record = current['install_sources'].get(cfg['ubuntu_install_source'])
            return record is not None and list(record) == cfg['ubuntu_source_record']
        return True

    if not matches(source):
        raise ValueError('Linux installer media changed since review.')
    installer = source
    if source.startswith('library:'):
        filename = source.removeprefix('library:')
        storage = cfg['iso_storage']
        installer = storage+':iso/'+operations.upload_iso_name(filename)
        volumes = client.call('GET', f'nodes/{node}/storage/{storage}/content', content='iso')
        if not any(v['volid'] == installer for v in volumes):
            operations.execute(dict(action='upload-iso', node=node, storage=storage, filename=filename), log)
        if not matches(installer):
            raise ValueError('The ISO on Proxmox differs from the reviewed media.')
    log('Preparing '+distro.capitalize()+' boot media from the selected installer and unattended settings.')
    with tempfile.TemporaryDirectory(prefix='proxmate-installer-') as temp:
        path = Path(temp)/f'proxmate-{distro}-{vmid}-{job_id}.iso'
        boot_iso(path, cfg, job_id, source)
        boot = operations.upload_generated(client, node, cfg['iso_storage'], path, log)
    base = f'nodes/{node}/qemu'
    smbios = 'uuid='+cfg['install_uuid']
    client.wait(node, client.call('POST', base, vmid=vmid, name=cfg['name'], memory=cfg['memory'], cores=cfg['cores'],
        cpu='host', ostype='l26', agent=1, smbios1=smbios,
        scsihw='virtio-scsi-single', scsi0=f'{cfg["storage"]}:{cfg["disk"]},discard=on,serial={cfg["disk_serial"]}',
        net0='virtio,bridge='+cfg['bridge'], ide2=boot+',media=cdrom', sata1=installer+',media=cdrom',
        # SeaBIOS initializes the devices listed in the Proxmox boot order.
        boot='order=scsi0;ide2;sata1'), log)
    current = client.call('GET', f'{base}/{vmid}/config')
    if cfg['kind']=='rocky-iso' and current.get('cpu')!='host':
        raise ValueError('Rocky Linux 10 requires the reviewed host CPU configuration.')
    if (current.get('smbios1') != smbios or current.get('ide2', '').split(',')[0] != boot
            or current.get('sata1', '').split(',')[0] != installer
            or 'serial='+cfg['disk_serial'] not in current.get('scsi0', '').split(',')
            or any(re.fullmatch(r'(scsi|sata|virtio|ide)\d+', key) and key not in ('scsi0', 'ide2', 'sata1') for key in current)):
        raise ValueError('New Linux VM disk or boot media changed before installation.')
    client.wait(node, client.call('POST', f'{base}/{vmid}/status/start'), log)
    log(distro.capitalize()+' installer started. Installation progress is available in the VM console.')
    if cfg.get('media_kind') in ('minimal', 'boot') and distro=='rocky':
        log('Rocky '+cfg['media_kind'].capitalize()+' uses online packages. If package setup stalls, check guest DNS and HTTPS access to the Rocky mirror list and its mirrors in the console.')
    return {'created_smbios': smbios, 'boot_volume': boot, 'installer_volume': installer}


def detach(cfg, build, log):

    client = operations.Proxmox()
    path = vm_path(cfg)
    current = client.call('GET', path+'/config')
    boot = build['source']['boot_volume']
    installer = build['source']['installer_volume']
    if (machine_identity(current) != build['machine_identity'] or current.get('template')
            or current.get('ide2', '').split(',')[0] != boot or current.get('sata1', '').split(',')[0] != installer):
        raise ValueError('Linux boot media changed. Inspect the VM before removing media.')
    client.wait(cfg['node'], client.call('PUT', path+'/config', delete='ide2,sata1',
                                       boot='order=scsi0', digest=current['digest']), log)
    client.wait(cfg['node'], client.call('DELETE', f'nodes/{cfg["node"]}/storage/{boot.split(":")[0]}/content/{quote(boot, safe="")}'), log)
    log('Detached installation media and removed the generated boot image.')


def template_builds(manager):
    builds = []
    with manager.db() as db:
        rows = db.execute("""SELECT records.value, jobs.id, jobs.status FROM records JOIN jobs ON records.id=jobs.id
                             WHERE records.kind='builds' AND jobs.action='simple'""")
        for row in rows:
            build = manager.decode(row['value'])
            if build.get('config', {}).get('kind') not in ISO_KINDS or not build.get('bootstrap_verified'):
                continue
            if build.get('template_finished_by'):
                builds.append(build)
            elif row['status']=='succeeded':
                payload=db.execute('SELECT payload FROM jobs WHERE id=?',(row['id'],)).fetchone()['payload']
                if manager.decode(payload).get('plan', {}).get('purpose')=='template':
                    builds.append(build)
    return builds


def template_identities(builds):
    return {(b['node'], b['vmid'], b['machine_identity']):b['config']['kind'].split('-')[0]
            for b in builds if b.get('machine_identity')}


def templates(builds, resources):
    recorded=template_identities(builds)
    ids={(node,vmid) for node,vmid,_ in recorded}
    candidates=[r for r in resources if r.get('type')=='qemu' and r.get('template') and
                (r['node'],r['vmid']) in ids]
    result={kind.split('-')[0]+'_templates':[] for kind in ISO_KINDS}
    if not candidates:
        return result
    client=operations.Proxmox()
    try:
        for row in candidates:
            try:
                current=client.call('GET',vm_path(row)+'/config')
            except (RequestException, RuntimeError):
                continue
            distro=recorded.get((row['node'],row['vmid'],machine_identity(current)))
            if distro and current.get('template'):
                result[distro+'_templates'].append(dict(node=row['node'],vmid=row['vmid'],name=current.get('name') or str(row['vmid'])))
    finally:
        client.session.close()
    return result


def review_template(life, dest, draft, distro):
    from management import number

    target = dict(node=dest['node'], vmid=number(draft.get('template_id'), 100, 999999999))
    resource, cfg = life.guest(operations.Proxmox(), target, True)
    build_identity = machine_identity(cfg)
    source_build = next((b for b in template_builds(life.m) if b['config']['kind']==distro+'-iso'
                         and (b['node'], b['vmid'], b['machine_identity'])==(dest['node'], target['vmid'], build_identity)), None)
    if (resource.get('status') != 'stopped' or cfg.get('ostype') != 'l26'
            or not cfg.get('ide2', '').split(',')[0].endswith('-cloudinit')
            or not source_build):
        raise ValueError('Choose a stopped '+distro.capitalize()+' ISO template prepared by Proxmate.')
    safe_guest_config(cfg)
    if distro=='rocky' and cfg.get('cpu')!='host':
        raise ValueError('Choose a Rocky template using the host CPU configuration.')
    match = re.search(r'(?:^|,)bridge=([^,]+)', cfg.get('net0', ''))
    if not match or match[1] not in dest['bridges']:
        raise ValueError('The Linux template needs an available network bridge.')
    mode = draft.get('clone_mode', 'full')
    if mode not in ('full', 'linked'):
        raise ValueError('Choose a full or linked copy.')
    storage = cfg.get('scsi0', '').partition(':')[0]
    if mode == 'linked' and storage not in dest['storage_choices']:
        raise ValueError('The template disk storage is not available for linked copies.')
    return dict(source=target['vmid'], bridge=match[1], name=resource.get('name') or str(target['vmid']),
                **({'profile': source_build['config'].get('profile','ubuntu-24.04')} if distro=='ubuntu' else {}),
                identity=config_identity(cfg), mode=mode, storage=storage,
                disk_gib=max(1, (resource.get('maxdisk', 0)+2**30-1)//2**30))


def clone(cfg, log):

    p = operations.Proxmox()
    preflight(p, cfg)
    template = cfg['template']
    node, vmid = cfg['node'], cfg['vmid']
    source = vm_path(dict(node=node, vmid=template['source']))
    current = p.call('GET', source+'/config')
    if not current.get('template') or config_identity(current) != template['identity']:
        raise ValueError('The Linux template changed since review. Review a new clone.')
    settings = dict(newid=vmid, name=cfg['name'], full=int(template['mode'] == 'full'))
    if settings['full']:
        settings['storage'] = cfg['storage']
    log('Cloning the reviewed Linux template to a new VM.')
    p.wait(node, p.call('POST', source+'/clone', **settings), log)
    path = vm_path(cfg)
    cloned = p.call('GET', path+'/config')
    identity = machine_identity(cloned)
    if (cloned.get('template') or cloned.get('name') != cfg['name'] or identity == machine_identity(current)
            or not (cloned.get('smbios1') or cloned.get('vmgenid'))
            or p.call('GET', path+'/status/current').get('status') != 'stopped'):
        raise ValueError('Cannot verify the new stopped Linux clone.')
    display = {'vga': 'virtio'} if current.get('bios') == 'ovmf' and not cloned.get('vga') else {}
    p.wait(node, p.call('PUT', path+'/config', agent=1, onboot=0, cores=cfg['cores'], sockets=1,
                        memory=cfg['memory'], ciuser=cfg['username'], ipconfig0='ip=dhcp',
                        sshkeys=quote(cfg['ssh_key'], safe=''), digest=cloned['digest'], **display), log)
    if machine_identity(p.call('GET', path+'/config')) != identity:
        raise ValueError('The Linux clone changed before startup.')
    p.wait(node, p.call('POST', path+'/status/start'), log)
    log('Linux clone started with its own cloud-init login and hostname.')
    return {'machine_identity': identity}
