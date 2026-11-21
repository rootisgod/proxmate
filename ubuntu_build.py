"""Ubuntu Server 24.04 media, autoinstall and first-boot handoff."""
import hashlib
import json
import re
import shlex

import paramiko
import pycdlib
import yaml

import media
import linux_install


LABEL = 'Ubuntu Server 24.04 LTS'
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
    return '#cloud-config\n'+yaml.safe_dump({'autoinstall': setup}, sort_keys=False)


def boot_iso(path, cfg, job_id, source):
    files = {'user-data': cfg.get('autoinstall') or user_data(cfg),
             'meta-data': yaml.safe_dump({'instance-id': 'proxmate-installer-'+job_id})}
    linux_install.boot_disc(path, cfg['node'], source, ('/casper/vmlinuz', '/casper/initrd'),
                            BOOT_CONFIG, files, 'CIDATA')
