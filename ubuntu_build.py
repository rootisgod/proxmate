"""Ubuntu Server 24.04 media, autoinstall and first-boot handoff."""
import base64
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

import paramiko
import pycdlib
import requests
import yaml
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization

import media
import operations


LABEL = 'Ubuntu Server 24.04 LTS'
KINDS = ('linux-cloud', 'ubuntu-iso', 'ubuntu-clone')
ISO_NAME = re.compile(r'ubuntu-24\.04(?:\.\d+)?-live-server-amd64\.iso', re.I)
ISO_INFO = re.compile(r'Ubuntu-Server 24\.04(?:\.\d+)? LTS .*\bamd64\b', re.I)
PACKAGES = [('curl', 'curl'), ('git', 'Git'), ('htop', 'htop'), ('jq', 'jq'),
            ('vim', 'Vim'), ('tmux', 'tmux'), ('rsync', 'rsync'), ('unzip', 'Unzip'),
            ('dnsutils', 'DNS tools'), ('build-essential', 'Build tools')]
FIREWALL = {'default': 'Ubuntu default', 'ssh': 'Enable UFW and allow SSH', 'off': 'Disable UFW'}
UPDATES = {'default': 'Ubuntu default', 'security': 'Enable automatic security updates', 'manual': 'Manual updates'}

BOOT_CONFIG = """set timeout=0
set default=0
menuentry 'Install Ubuntu Server automatically' {
    linux /boot/proxmate/vmlinuz autoinstall noprompt ---
    initrd /boot/proxmate/initrd
}
"""


def candidates(items):
    return [item for item in items if ISO_NAME.fullmatch(item['id'].split('/')[-1].removeprefix('library:'))]


def inspect(source, node):
    try:
        with media.open_iso(source, node) as iso:
            info = media.image_entry(iso, '/.disk/info', open_file=True)
            if not info:
                raise ValueError('This ISO does not contain Ubuntu Server media information.')
            with info:
                release = info.read(1024).decode('utf-8', errors='replace').strip()
            records = [media.image_entry(iso, name) for name in ('/casper/vmlinuz', '/casper/initrd')]
            if not ISO_INFO.match(release) or not all(records):
                raise ValueError('Choose an Ubuntu Server 24.04 LTS amd64 live installer ISO.')
            # Bind review to the release and boot records without transferring a multi-GB ISO.
            identity = hashlib.sha256(json.dumps([release, *[(r.extent_location(), r.get_data_length()) for r in records]]).encode()).hexdigest()
            return {'release': release, 'identity': identity}
    except paramiko.SSHException as exc:
        raise ValueError(media.ssh_error(exc)) from None
    except (OSError, pycdlib.pycdlibexception.PyCdlibException):
        raise ValueError('The selected Ubuntu ISO could not be read. Check the media and Server connection.') from None


def public_key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 8192 or len(value.splitlines()) != 1:
        raise ValueError('Enter one OpenSSH public key.')
    try:
        key = serialization.load_ssh_public_key(value.encode())
        if value.startswith('sk-'):
            # Preserve the security-key application and wire key type after validation.
            return ' '.join(value.split()[:2])
        return key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValueError('Enter a valid OpenSSH public key.') from None


def github_keys(username):
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', username):
        raise ValueError('Enter a GitHub username.')
    try:
        with requests.get('https://api.github.com/users/'+username+'/keys', params={'per_page': 30},
                          headers={'Accept': 'application/vnd.github+json'}, stream=True,
                          timeout=(4, 6), allow_redirects=False) as response:
            if response.status_code == 404:
                raise ValueError('GitHub account not found. Check the username.')
            if response.status_code != 200:
                raise ValueError('GitHub key lookup failed. Try again or paste your public key.')
            if 'next' in response.links:
                raise ValueError('This GitHub account has more than 30 public keys. Paste the key you want to use.')
            data = bytearray()
            for chunk in response.iter_content(8192):
                data.extend(chunk)
                if len(data) > 100_000:
                    raise ValueError('Too many GitHub keys. Paste one public key.')
            keys = json.loads(data)
            if not isinstance(keys, list) or not keys:
                raise ValueError('This GitHub account has no public SSH keys.')
            return list(dict.fromkeys(public_key(entry['key']) for entry in keys))
    except (requests.RequestException, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('GitHub key lookup failed. Try again or paste your public key.') from None


def fingerprint(key):
    digest = hashlib.sha256(base64.b64decode(key.split()[1])).digest()
    return 'SHA256:'+base64.b64encode(digest).decode().rstrip('=')


def setting_tasks(d):
    firewall = d.get('ubuntu_firewall', 'default')
    updates = d.get('ubuntu_updates', 'default')
    if firewall not in tuple(FIREWALL) or updates not in tuple(UPDATES):
        raise ValueError('Choose an Ubuntu firewall and update policy.')
    selected = []
    if firewall != 'default':
        steps = [{'ansible.builtin.apt': {'name': 'ufw', 'update_cache': True, 'cache_valid_time': 3600, 'lock_timeout': 180}}]
        if firewall == 'ssh':
            steps.append({'ansible.builtin.command': {'argv': ['ufw', 'allow', '22/tcp']}})
        steps.append({'ansible.builtin.command': {'argv': ['ufw', '--force', 'enable' if firewall == 'ssh' else 'disable']}})
        selected.append(setting('ubuntu-firewall', FIREWALL[firewall], steps, firewall == 'off'))
    if updates != 'default':
        enable = updates == 'security'
        steps = []
        if enable:
            steps.append({'ansible.builtin.apt': {'name': 'unattended-upgrades', 'update_cache': True, 'cache_valid_time': 3600, 'lock_timeout': 180}})
        flag = int(enable)
        steps.append({'ansible.builtin.copy': {'dest': '/etc/apt/apt.conf.d/99-proxmate-periodic',
            'content': f'APT::Periodic::Update-Package-Lists "{flag}";\nAPT::Periodic::Unattended-Upgrade "{flag}";\n',
            'owner': 'root', 'group': 'root', 'mode': '0644'}})
        selected.append(setting('ubuntu-updates', UPDATES[updates], steps, not enable))
    return selected


def setting(ident, name, steps, dangerous):
    return {'item': {'id': ident, 'name': name, 'platform': 'linux', 'kind': 'ubuntu-setup',
                     'steps': steps, 'dangerous': dangerous}, 'params': {}}


def password_login(username, password):
    from passlib.hash import sha512_crypt

    if (not isinstance(password, str) or not 12 <= len(password) <= 128 or not password.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in password)):
        raise ValueError('Use an Ubuntu password of 12–128 characters without control characters.')
    hashed = sha512_crypt.using(rounds=120_000).hash(password)
    return setting('ubuntu-password', 'Enable console and SSH password sign-in', [
        {'ansible.builtin.user': {'name': username, 'password': hashed}},
        {'ansible.builtin.blockinfile': {
            'path': '/etc/ssh/sshd_config', 'marker': '# {mark} PROXMATE PASSWORD LOGIN',
            'block': f'Match User {username}\n    PasswordAuthentication yes',
            'validate': '/usr/sbin/sshd -t -f %s',
        }},
        {'ansible.builtin.service': {'name': 'ssh', 'state': 'reloaded'}},
    ], False)


def user_data(cfg, *, reusable=False):
    username = cfg['username']
    serial = cfg['disk_serial'] if not reusable else 'REPLACE_WITH_SYSTEM_DISK_SERIAL'
    # Subiquity's serial filter reads ID_SERIAL, which QEMU gives the drive slot name.
    # Check the unique Proxmox serial exposed as ID_SCSI_SERIAL before matching the path.
    verify_disk = ('udevadm settle --timeout=30 && '
                   'udevadm info --query=property --name=/dev/sda | grep -Fxq '+
                   shlex.quote('ID_SCSI_SERIAL='+serial))
    keys = cfg['ssh_key'].splitlines() if not reusable else []
    setup = dict(version=1, **{'refresh-installer': {'update': False}},
                 identity={'hostname': cfg['name'].lower(), 'username': username, 'password': '!'},
                 ssh={'install-server': True, 'allow-pw': False, 'authorized-keys': keys},
                 storage={'layout': {'name': 'direct', 'match': {'path': '/dev/sda'}}},
                 kernel={'package': 'linux-virtual'},
                 packages=['qemu-guest-agent', 'python3', 'sudo'],
                 **{'early-commands': [['sh', '-ec', verify_disk]], 'late-commands': [['sh', '-ec',
                     f'echo "{username} ALL=(ALL:ALL) NOPASSWD:ALL" >/target/etc/sudoers.d/90-proxmate; '
                     'chmod 0440 /target/etc/sudoers.d/90-proxmate; '
                     'install -d /target/var/lib/proxmate; echo ready >/target/var/lib/proxmate/bootstrap-ready']]})
    return '#cloud-config\n'+yaml.safe_dump({'autoinstall': setup}, sort_keys=False)


def boot_iso(path, cfg, job_id, source):
    with tempfile.TemporaryDirectory(prefix='proxmate-grub-') as temp:
        root = Path(temp)
        grub = root/'boot/grub'
        grub.mkdir(parents=True)
        boot_files = root/'boot/proxmate'
        boot_files.mkdir()
        # BIOS GRUB can read its boot disc. The Linux installer discovers the original DVD after it starts.
        with media.open_iso(source, cfg['node']) as iso:
            for name in ('vmlinuz', 'initrd'):
                record = media.image_entry(iso, '/casper/'+name)
                if not record or not 0 < record.get_data_length() <= 512*1024**2:
                    raise ValueError('The selected Ubuntu ISO has unusable installer boot files.')
                with media.image_entry(iso, '/casper/'+name, open_file=True) as original, (boot_files/name).open('wb') as output:
                    shutil.copyfileobj(original, output, length=1024**2)
        (grub/'grub.cfg').write_text(BOOT_CONFIG, encoding='utf-8')
        (root/'user-data').write_text(cfg.get('autoinstall') or user_data(cfg), encoding='utf-8')
        (root/'meta-data').write_text(yaml.safe_dump({'instance-id': 'proxmate-installer-'+job_id}), encoding='utf-8')
        try:
            subprocess.run(['grub-mkrescue', '-d', '/usr/lib/grub/i386-pc', '-o', str(path),
                            str(root), '--', '-volid', 'CIDATA'], capture_output=True, check=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('Could not generate Ubuntu autoinstall boot media.') from None


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


def install(cfg, source, identity, job_id, log):
    client = operations.Proxmox()
    node, vmid = cfg['node'], cfg['vmid']
    preflight(client, cfg)
    if inspect(source, node)['identity'] != identity:
        raise ValueError('Ubuntu installer media changed since review.')
    installer = source
    if source.startswith('library:'):
        filename = source.removeprefix('library:')
        storage = cfg['iso_storage']
        installer = storage+':iso/'+operations.upload_iso_name(filename)
        volumes = client.call('GET', f'nodes/{node}/storage/{storage}/content', content='iso')
        if not any(v['volid'] == installer for v in volumes):
            operations.execute(dict(action='upload-iso', node=node, storage=storage, filename=filename), log)
        if inspect(installer, node)['identity'] != identity:
            raise ValueError('The ISO on Proxmox differs from the reviewed media.')
    log('Preparing Ubuntu boot media from the selected installer and unattended settings.')
    with tempfile.TemporaryDirectory(prefix='proxmate-ubuntu-') as temp:
        path = Path(temp)/f'proxmate-ubuntu-{vmid}-{job_id}.iso'
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
    if (current.get('smbios1') != smbios or current.get('ide2', '').split(',')[0] != boot
            or current.get('sata1', '').split(',')[0] != installer
            or 'serial='+cfg['disk_serial'] not in current.get('scsi0', '').split(',')
            or any(re.fullmatch(r'(scsi|sata|virtio|ide)\d+', key) and key not in ('scsi0', 'ide2', 'sata1') for key in current)):
        raise ValueError('New Ubuntu VM disk or boot media changed before installation.')
    client.wait(node, client.call('POST', f'{base}/{vmid}/status/start'), log)
    log('Ubuntu installer started. Open the VM console to see Subiquity progress.')
    return {'created_smbios': smbios, 'boot_volume': boot, 'installer_volume': installer}


def detach(cfg, build, log):
    from lifecycle import machine_identity, vm_path

    client = operations.Proxmox()
    path = vm_path(cfg)
    current = client.call('GET', path+'/config')
    boot = build['source']['boot_volume']
    installer = build['source']['installer_volume']
    if (machine_identity(current) != build['machine_identity'] or current.get('template')
            or current.get('ide2', '').split(',')[0] != boot or current.get('sata1', '').split(',')[0] != installer):
        raise ValueError('Ubuntu boot media changed. Inspect the VM before removing media.')
    client.wait(cfg['node'], client.call('PUT', path+'/config', delete='ide2,sata1',
                                       boot='order=scsi0', digest=current['digest']), log)
    client.wait(cfg['node'], client.call('DELETE', f'nodes/{cfg["node"]}/storage/{boot.split(":")[0]}/content/{quote(boot, safe="")}'), log)
    log('Detached Ubuntu installation media and removed the generated boot image.')


def template_builds(manager):
    with manager.db() as db:
        rows = db.execute("""SELECT records.value, jobs.payload FROM records JOIN jobs ON records.id=jobs.id
                             WHERE records.kind='builds' AND jobs.action='simple' AND jobs.status='succeeded'""").fetchall()
    builds = []
    for row in rows:
        build = manager.decode(row['value'])
        if (build.get('config', {}).get('kind') == 'ubuntu-iso' and build.get('bootstrap_verified')
                and manager.decode(row['payload']).get('plan', {}).get('purpose') == 'template'):
            builds.append(build)
    return builds


def templates(manager, resources):
    recorded = {(b['node'], b['vmid']) for b in template_builds(manager)}
    return [dict(node=r['node'], vmid=r['vmid'], name=r.get('name') or str(r['vmid']))
            for r in resources if r.get('type') == 'qemu' and r.get('template') and
            (r['node'], r['vmid']) in recorded]


def review_template(life, dest, draft):
    from lifecycle import config_identity, machine_identity, safe_guest_config
    from management import number

    target = dict(node=dest['node'], vmid=number(draft.get('template_id'), 100, 999999999))
    resource, cfg = life.guest(operations.Proxmox(), target, True)
    build_identity = machine_identity(cfg)
    if (resource.get('status') != 'stopped' or cfg.get('ostype') != 'l26'
            or not cfg.get('ide2', '').split(',')[0].endswith('-cloudinit')
            or not any((b['node'], b['vmid'], b['machine_identity']) == (dest['node'], target['vmid'], build_identity)
                       for b in template_builds(life.m))):
        raise ValueError('Choose a stopped Ubuntu ISO template prepared by Proxmate.')
    safe_guest_config(cfg)
    match = re.search(r'(?:^|,)bridge=([^,]+)', cfg.get('net0', ''))
    if not match or match[1] not in dest['bridges']:
        raise ValueError('The Ubuntu template needs an available network bridge.')
    mode = draft.get('clone_mode', 'full')
    if mode not in ('full', 'linked'):
        raise ValueError('Choose a full or linked copy.')
    storage = cfg.get('scsi0', '').partition(':')[0]
    if mode == 'linked' and storage not in dest['storage_choices']:
        raise ValueError('The template disk storage is not available for linked copies.')
    return dict(source=target['vmid'], bridge=match[1], name=resource.get('name') or str(target['vmid']),
                identity=config_identity(cfg), mode=mode, storage=storage,
                disk_gib=max(1, (resource.get('maxdisk', 0)+2**30-1)//2**30))


def clone(cfg, log):
    from lifecycle import config_identity, machine_identity, vm_path

    p = operations.Proxmox()
    preflight(p, cfg)
    template = cfg['template']
    node, vmid = cfg['node'], cfg['vmid']
    source = vm_path(dict(node=node, vmid=template['source']))
    current = p.call('GET', source+'/config')
    if not current.get('template') or config_identity(current) != template['identity']:
        raise ValueError('The Ubuntu template changed since review. Review a new clone.')
    settings = dict(newid=vmid, name=cfg['name'], full=int(template['mode'] == 'full'))
    if settings['full']:
        settings['storage'] = cfg['storage']
    log('Cloning the reviewed Ubuntu template to a new VM.')
    p.wait(node, p.call('POST', source+'/clone', **settings), log)
    path = vm_path(cfg)
    cloned = p.call('GET', path+'/config')
    identity = machine_identity(cloned)
    if (cloned.get('template') or cloned.get('name') != cfg['name'] or identity == machine_identity(current)
            or not (cloned.get('smbios1') or cloned.get('vmgenid'))
            or p.call('GET', path+'/status/current').get('status') != 'stopped'):
        raise ValueError('Cannot verify the new stopped Ubuntu clone.')
    display = {'vga': 'virtio'} if current.get('bios') == 'ovmf' and not cloned.get('vga') else {}
    p.wait(node, p.call('PUT', path+'/config', agent=1, onboot=0, cores=cfg['cores'], sockets=1,
                        memory=cfg['memory'], ciuser=cfg['username'], ipconfig0='ip=dhcp',
                        sshkeys=quote(cfg['ssh_key'], safe=''), digest=cloned['digest'], **display), log)
    if machine_identity(p.call('GET', path+'/config')) != identity:
        raise ValueError('The Ubuntu clone changed before startup.')
    p.wait(node, p.call('POST', path+'/status/start'), log)
    log('Ubuntu clone started with its own cloud-init login and hostname.')
    return {'machine_identity': identity}
