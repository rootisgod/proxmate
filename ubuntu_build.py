"""Ubuntu Server LTS media, autoinstall and first-boot handoff."""
import hashlib
import json
import re
import shlex

import paramiko
import pycdlib
import yaml

import media
import linux_install


LABEL = 'Ubuntu Server LTS'
DEFAULT_PROFILE = 'ubuntu-24.04'
LTS_SERIES = ('22.04', '24.04', '26.04')
INSTALL_SOURCES = {'ubuntu-server': 'Standard', 'ubuntu-server-minimal': 'Minimized'}
ISO_NAME = re.compile(r'ubuntu-(\d{2}\.\d{2})(?:\.(\d+))?-live-server-amd64\.iso', re.I)
ISO_INFO = re.compile(r'Ubuntu-Server (\d{2}\.\d{2})(?:\.(\d+))?(?: LTS)? "[^"]+" - Release amd64\b', re.I)
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
    ranked = []
    for item in items:
        match = ISO_NAME.fullmatch(item['id'].split('/')[-1].removeprefix('library:'))
        if match and match[1] in LTS_SERIES:
            ranked.append((match[1], int(match[2] or 0), not item['id'].startswith('library:'), item))
    ranked.sort(key=lambda row: row[:3], reverse=True)
    return [row[3] for row in ranked]


def install_sources(iso):
    entry = media.image_entry(iso, '/casper/install-sources.yaml', open_file=True)
    if not entry:
        raise ValueError('Ubuntu installer source catalog is missing.')
    with entry:
        raw = entry.read(65537)
    if len(raw) > 65536:
        raise ValueError('Ubuntu installer source catalog is too large.')
    catalog = yaml.safe_load(raw)
    # Jammy and Noble use a list. Resolute puts it under sources in a versioned mapping.
    entries = catalog.get('sources') if isinstance(catalog, dict) else catalog
    sources = {}
    if isinstance(entries, list):
        for row in entries:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] not in INSTALL_SOURCES:
                continue
            path = row.get('path')
            if not isinstance(path, str) or not re.fullmatch(r'[\w.-]+\.squashfs', path):
                continue
            record = media.image_entry(iso, '/casper/'+path)
            if record and record.get_data_length():
                sources[row['id']] = (record.extent_location(), record.get_data_length())
    if not sources:
        raise ValueError('Ubuntu ISO has no supported Server installation image.')
    return sources


def inspect(source, node):
    try:
        with media.open_iso(source, node) as iso:
            info = media.image_entry(iso, '/.disk/info', open_file=True)
            if not info:
                raise ValueError('This ISO does not contain Ubuntu Server media information.')
            with info:
                release = info.read(1024).decode('utf-8', errors='replace').strip()
            records = [media.image_entry(iso, name) for name in ('/casper/vmlinuz', '/casper/initrd')]
            found = ISO_INFO.match(release)
            name = ISO_NAME.fullmatch(source.split('/')[-1].removeprefix('library:'))
            if (not found or not name or found.groups() != name.groups() or found[1] not in LTS_SERIES
                    or not all(r and r.get_data_length() for r in records)):
                raise ValueError('Choose matching Ubuntu Server 22.04, 24.04 or 26.04 LTS amd64 live installer media.')
            sources = install_sources(iso)
            # Bind review to the release and boot records without transferring a multi-GB ISO.
            identity = hashlib.sha256(json.dumps([release, *[(r.extent_location(), r.get_data_length()) for r in records]]).encode()).hexdigest()
            return {'release': release, 'identity': identity, 'profile': 'ubuntu-'+found[1],
                    'install_sources': sources}
    except paramiko.SSHException as exc:
        raise ValueError(media.ssh_error(exc)) from None
    except (OSError, yaml.YAMLError, pycdlib.pycdlibexception.PyCdlibException):
        raise ValueError('The selected Ubuntu ISO could not be read. Check the media and Server connection.') from None


def review_iso(source, node, draft):
    media_info = inspect(source, node)
    profile = media_info['profile']
    if draft.get('profile', profile) != profile:
        raise ValueError('The requested Ubuntu release does not match the selected ISO.')
    image = draft.get('ubuntu_install_source', 'ubuntu-server')
    if not isinstance(image, str) or image not in media_info['install_sources']:
        raise ValueError('Choose a Server installation image available on the selected Ubuntu ISO.')
    settings = dict(ubuntu_install_source=image, ubuntu_source_record=list(media_info['install_sources'][image]))
    return linux_install.ReviewedISO(profile=profile, edition=media_info['release'] + ' · ' + INSTALL_SOURCES[image],
                                     identity=media_info['identity'], answer_key='autoinstall', render_answer=user_data,
                                     settings=settings)


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
    if cfg.get('ubuntu_install_source'):
        setup['source'] = {'id': cfg['ubuntu_install_source']}
    return '#cloud-config\n'+yaml.safe_dump({'autoinstall': setup}, sort_keys=False)


def boot_iso(path, cfg, job_id, source):
    files = {'user-data': cfg.get('autoinstall') or user_data(cfg),
             'meta-data': yaml.safe_dump({'instance-id': 'proxmate-installer-'+job_id})}
    linux_install.boot_disc(path, cfg['node'], source, ('/casper/vmlinuz', '/casper/initrd'),
                            BOOT_CONFIG, files, 'CIDATA')
