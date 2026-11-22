"""Rocky Linux 10 installer discovery and unattended Kickstart installation."""
import configparser
import hashlib
import re

import paramiko
import pycdlib

import linux_install
import media


LABEL = 'Rocky Linux 10'
DEFAULT_PROFILE = 'rocky-10'
ISO_NAME = re.compile(r'Rocky-(10(?:\.\d+)*)(?:-latest|-\d{8}\.\d+)?-x86_64-(dvd1?|minimal|boot)\.iso', re.I)
BOOT_FILES = ('/images/pxeboot/vmlinuz', '/images/pxeboot/initrd.img')
PACKAGES = [('jq','jq'), ('git','Git'), ('tmux','tmux'), ('rsync','rsync'), ('wget','wget')]
V3_FLAGS = frozenset('lm cx16 lahf_lm popcnt pni ssse3 sse4_1 sse4_2 avx avx2 bmi1 bmi2 f16c fma abm movbe xsave'.split())


def check_cpu(client, node):
    flags = set(client.call('GET', f'nodes/{node}/status').get('cpuinfo', {}).get('flags', '').lower().split())
    if 'lzcnt' in flags:
        flags.add('abm')
    if not V3_FLAGS.issubset(flags):
        raise ValueError('Rocky Linux 10 needs x86-64-v3. Choose a node that reports the required CPU features.')


def candidates(items):
    matches = []
    for item in items:
        match = ISO_NAME.fullmatch(item['id'].split('/')[-1].removeprefix('library:'))
        if match:
            version = tuple(int(part) for part in match[1].split('.'))
            preference = {'dvd': 2, 'dvd1': 2, 'minimal': 1, 'boot': 0}[match[2].lower()]
            matches.append((version, preference, not item['id'].startswith('library:'), item))
    matches.sort(key=lambda row: row[:3], reverse=True)
    return [row[3] for row in matches]


def _metadata(iso, path, invalid):
    entry = media.image_entry(iso, path, open_file=True)
    if not entry:
        raise ValueError(invalid)
    with entry:
        raw = entry.read(65537)
    if not raw or len(raw) > 65536:
        raise ValueError(invalid)
    return raw


def inspect(source, node):
    invalid = 'Choose an official Rocky Linux 10 x86_64 DVD, Minimal or Boot ISO matching its filename.'
    match = ISO_NAME.fullmatch(source.split('/')[-1].removeprefix('library:'))
    if not match:
        raise ValueError(invalid)
    kind = match[2].lower().removesuffix('1')
    try:
        with media.open_iso(source, node) as iso:
            label = iso.pvd.volume_identifier.decode('ascii').strip()
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', label):
                raise ValueError(invalid)
            files = [*BOOT_FILES, '/images/install.img']
            if kind == 'boot':
                # The official network boot disc has no .treeinfo and reuses the DVD label.
                raw = _metadata(iso, '/.discinfo', invalid)
                lines = raw.decode().splitlines()
                if len(lines) != 3 or lines[2] != 'x86_64' or media.image_entry(iso, '/.treeinfo'):
                    raise ValueError(invalid)
                version = lines[1]
                grub = _metadata(iso, '/EFI/BOOT/grub.cfg', invalid)
                if (label != 'Rocky-'+version.replace('.', '-')+'-x86_64-dvd'
                        or f"menuentry 'Install Rocky Linux {version}'".encode() not in grub):
                    raise ValueError(invalid)
                raw += grub
            else:
                raw = _metadata(iso, '/.treeinfo', invalid)
                tree = configparser.ConfigParser(interpolation=None)
                tree.read_string(raw.decode())
                version = tree.get('release', 'version')
                family = 'Rocky Linux Minimal' if kind == 'minimal' else 'Rocky Linux'
                if (tree.get('release', 'name') != family or tree.get('tree', 'arch') != 'x86_64'
                        or tree.get('stage2', 'mainimage') != 'images/install.img'):
                    raise ValueError(invalid)
                repos = ('Minimal',) if kind == 'minimal' else ('BaseOS','AppStream')
                files.extend('/'+repo+'/repodata/repomd.xml' for repo in repos)
            if not re.fullmatch(r'10(?:\.\d+)*', version) or match[1] not in ('10', version):
                raise ValueError(invalid)
            identity = hashlib.sha256(raw + label.encode())
            for file in files:
                record = media.image_entry(iso, file)
                if not record or not record.get_data_length():
                    raise ValueError(invalid)
                identity.update(f'/{record.extent_location()}:{record.get_data_length()}'.encode())
            return dict(release='Rocky Linux '+version, label=label, identity=identity.hexdigest(), kind=kind, version=version)
    except paramiko.SSHException as exc:
        raise ValueError(media.ssh_error(exc)) from None
    except (configparser.Error, UnicodeError, pycdlib.pycdlibexception.PyCdlibException, OSError):
        raise ValueError(invalid) from None


def review_iso(source, node, draft):
    media_info = inspect(source, node)
    settings = dict(dvd_label=media_info['label'], media_kind=media_info['kind'], rocky_version=media_info['version'])
    return linux_install.ReviewedISO(profile=DEFAULT_PROFILE, edition=media_info['release'], identity=media_info['identity'],
                                     answer_key='kickstart', render_answer=kickstart, settings=settings)


def kickstart(cfg, *, reusable=False):
    user = cfg['username']
    serial = 'REPLACE_WITH_SYSTEM_DISK_SERIAL' if reusable else cfg['disk_serial']
    keys = ['REPLACE_WITH_OPENSSH_PUBLIC_KEY'] if reusable else cfg['ssh_key'].splitlines()
    source = 'cdrom'
    if cfg.get('media_kind') in ('boot', 'minimal'):
        mirrors = 'https://mirrors.rockylinux.org/mirrorlist?arch=x86_64&protocol=https&repo='
        baseos = mirrors+'BaseOS-'+cfg['rocky_version']
        appstream = mirrors+'AppStream-'+cfg['rocky_version']
        # Minimal has a local package set but lacks some of our guest tools.
        if cfg['media_kind'] == 'boot':
            source = f'url --mirrorlist="{baseos}"'
        else:
            source += f'\nrepo --name=proxmate-baseos --mirrorlist="{baseos}" --cost=2000'
        source += f'\nrepo --name=proxmate-appstream --mirrorlist="{appstream}" --cost=2000'
    return f"""#version=RHEL10
cmdline
{source}
lang en_US.UTF-8
keyboard us
timezone Etc/UTC --utc
network --device=link --bootproto=dhcp --activate --hostname={cfg['name']}
rootpw --lock !
user --name={user} --groups=wheel --lock
selinux --enforcing
firewall --enabled --service=ssh
services --enabled=sshd,qemu-guest-agent,NetworkManager,firewalld
firstboot --disable
reboot

%pre --interpreter=/bin/bash --erroronfail
set -euo pipefail
udevadm settle
# Anaconda allocates zram swap in RAM. Count every other whole disk, including unexpected disks.
disks=$(lsblk -dn -o NAME,TYPE | while read -r name type; do
    [[ "$type" == disk ]] || continue
    if [[ "$name" =~ ^zram[0-9]+$ && "$(readlink -f "/sys/class/block/$name")" == "/sys/devices/virtual/block/$name" ]]; then
        continue
    fi
    echo "$name"
done)
[[ "$disks" == sda ]]
test "$(blockdev --getro /dev/sda)" = 0
udevadm info -q property -n /dev/sda | grep -Fxq ID_SCSI_SERIAL={serial}
cat > /tmp/proxmate-storage.ks <<'STORAGE'
ignoredisk --only-use=sda
clearpart --all --initlabel --drives=sda
autopart --type=lvm
bootloader --boot-drive=sda
STORAGE
%end
%include /tmp/proxmate-storage.ks

%packages
@^minimal-environment
openssh-server
qemu-guest-agent
cloud-init
python3
python3-dnf
sudo
firewalld
%end

%post --interpreter=/bin/bash --erroronfail
set -eu
install -d -m 0700 -o {user} -g {user} /home/{user}/.ssh
cat > /home/{user}/.ssh/authorized_keys <<'PROXMATE_KEYS'
{chr(10).join(keys)}
PROXMATE_KEYS
chown {user}:{user} /home/{user}/.ssh/authorized_keys
chmod 0600 /home/{user}/.ssh/authorized_keys
restorecon -RF /home/{user}/.ssh
echo '{user} ALL=(ALL:ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-proxmate
chmod 0440 /etc/sudoers.d/90-proxmate
visudo -cf /etc/sudoers.d/90-proxmate
echo 'PasswordAuthentication no' > /etc/ssh/sshd_config.d/01-proxmate.conf
# Keep Rocky's packaged RPC allowlist. The dashboard reads the guest's SSH host key
# over the hypervisor channel before it trusts an SSH connection.
grep -Eq '^FILTER_RPC_ARGS="--allow-rpcs=[a-z0-9,-]+"$' /etc/sysconfig/qemu-ga
sed -i '/^FILTER_RPC_ARGS="--allow-rpcs=/s/"$/,guest-file-open,guest-file-read,guest-file-close"/' /etc/sysconfig/qemu-ga
# Publish only the public key in a root-owned directory the confined agent can read.
install -d -m 0755 /etc/proxmate
cat > /etc/systemd/system/proxmate-ssh-key.service <<'SERVICE'
[Unit]
Description=Publish the public SSH host key for Proxmate
After=sshd.service
ConditionPathExists=/etc/ssh/ssh_host_ed25519_key.pub
[Service]
Type=oneshot
ExecStart=/usr/bin/install -Z -m 0644 /etc/ssh/ssh_host_ed25519_key.pub /etc/proxmate/ssh-host.pub
[Install]
WantedBy=multi-user.target
SERVICE
cat > /etc/systemd/system/proxmate-ssh-key.path <<'WATCH'
[Unit]
Description=Refresh the public SSH key when cloud-init generates a new identity
[Path]
PathChanged=/etc/ssh/ssh_host_ed25519_key.pub
[Install]
WantedBy=multi-user.target
WATCH
systemctl enable proxmate-ssh-key.service proxmate-ssh-key.path
mkdir -p /etc/cloud
touch /etc/cloud/cloud-init.disabled
%end
"""


def boot_iso(path, cfg, job_id, source):
    grub = f"""set timeout=0
menuentry 'Install Rocky Linux 10 automatically' {{
    linux /boot/proxmate/vmlinuz inst.stage2=hd:LABEL={cfg['dvd_label']} inst.ks=hd:LABEL=PROXMATE_ROCKY:/ks.cfg inst.cmdline
    initrd /boot/proxmate/initrd
}}
"""
    linux_install.boot_disc(path, cfg['node'], source, BOOT_FILES, grub, {'ks.cfg': cfg['kickstart']}, 'PROXMATE_ROCKY')
