"""Debian 13 amd64 installer discovery, preseed answers and boot media."""
import hashlib
import re
import shlex

import paramiko
import pycdlib

import linux_install
import media


LABEL = 'Debian 13'
ISO_NAME = re.compile(r'debian-(13(?:\.\d+){0,2})-amd64-(DVD-1|netinst)\.iso', re.I)
ISO_INFO = re.compile(r'Debian GNU/Linux 13(?:\.\d+)* "Trixie" - Official amd64 (?:DVD Binary-1|NETINST)\b', re.I)
BOOT_FILES = ('/install.amd/vmlinuz', '/install.amd/initrd.gz')
GRUB = """set timeout=0
menuentry 'Install Debian 13 automatically' {
    linux /boot/proxmate/vmlinuz auto=true priority=critical ---
    initrd /boot/proxmate/initrd newc:preseed.cfg:/preseed.cfg
}
"""


def candidates(items):
    matches = []
    for item in items:
        filename = item['id'].split('/')[-1].removeprefix('library:')
        match = ISO_NAME.fullmatch(filename)
        if match:
            version = tuple(int(n) for n in match[1].split('.'))
            matches.append((version, not item['id'].startswith('library:'), item))
    matches.sort(key=lambda row: row[:2], reverse=True)
    return [row[2] for row in matches]


def inspect(source, node):
    try:
        with media.open_iso(source, node) as iso:
            info = media.image_entry(iso, '/.disk/info', open_file=True)
            if not info:
                raise ValueError('Debian release information is missing from this ISO.')
            with info:
                release = info.read(1024).decode('utf-8', errors='replace').strip()
            records = [media.image_entry(iso, name) for name in BOOT_FILES]
            if not ISO_INFO.match(release) or not all(records) or not media.image_entry(iso, '/dists/trixie/Release'):
                raise ValueError('Choose an official Debian 13 amd64 DVD-1 or netinst installer.')
            identity = hashlib.sha256(release.encode())
            for record in records:
                identity.update(f'/{record.extent_location()}:{record.get_data_length()}'.encode())
            return {'release': release, 'identity': identity.hexdigest()}
    except paramiko.SSHException as exc:
        raise ValueError(media.ssh_error(exc)) from None
    except (OSError, pycdlib.pycdlibexception.PyCdlibException):
        raise ValueError('Cannot read the Debian installer. Check the ISO and Server connection.') from None


def preseed(cfg, *, reusable=False):
    user = cfg['username']
    host = cfg['name'].lower()
    serial = 'REPLACE_WITH_SYSTEM_DISK_SERIAL' if reusable else cfg['disk_serial']
    keys = ['REPLACE_WITH_OPENSSH_PUBLIC_KEY'] if reusable else cfg['ssh_key'].splitlines()
    # d-i reads one line per answer. All account names and public keys were validated in review.
    authorized = f'/target/home/{user}/.ssh/authorized_keys'
    # The preseed parser interprets backslash escapes before running a shell.
    # Each validated OpenSSH key is one line, so write each with echo.
    write_keys = [f'echo {shlex.quote(key)} {">" if index==0 else ">>"} {authorized}'
                  for index,key in enumerate(keys)]
    late = ' && '.join([
        f'in-target install -d -m 0700 -o {user} -g {user} /home/{user}/.ssh',
        *write_keys,
        f'in-target chown {user}:{user} /home/{user}/.ssh/authorized_keys',
        f'chmod 0600 /target/home/{user}/.ssh/authorized_keys',
        f"echo '{user} ALL=(ALL:ALL) NOPASSWD:ALL' > /target/etc/sudoers.d/90-proxmate",
        'chmod 0440 /target/etc/sudoers.d/90-proxmate',
        "echo 'PasswordAuthentication no' > /target/etc/ssh/sshd_config.d/50-proxmate.conf",
        'mkdir -p /target/etc/cloud /target/var/lib/proxmate',
        'touch /target/etc/cloud/cloud-init.disabled',
        'echo ready > /target/var/lib/proxmate/bootstrap-ready',
    ])
    # Refuse to hand a disk to partman until it matches the unique serial created on the reviewed VM.
    # apt-cdrom scans every optical drive. Once GRUB has loaded the installer,
    # retire only our boot disc inside the guest so APT indexes the real Debian DVD.
    choose_disk = (
        'udevadm settle; for optical in /sys/class/block/sr*; do '
        'dev=/dev/$(basename "$optical"); '
        'if [ "$(blkid -s LABEL -o value "$dev")" = PROXMATE_DEBIAN ]; '
        'then echo 1 > "$optical/device/delete" || exit 1; fi; done; '
        'if [ "$(list-devices disk)" = /dev/sda ] && '
        f'udevadm info -q property -n /dev/sda | grep -Fxq ID_SCSI_SERIAL={serial}; '
        'then debconf-set partman-auto/disk /dev/sda; '
        'else logger -t proxmate "System disk does not match review"; poweroff -f; exit 1; fi')
    return f"""#_preseed_V1
d-i debian-installer/locale string en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us
d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string {host}
d-i netcfg/hostname string {host}
d-i netcfg/get_domain string localdomain
d-i mirror/country string manual
d-i mirror/http/hostname string deb.debian.org
d-i mirror/http/directory string /debian
d-i mirror/http/proxy string
d-i passwd/root-login boolean false
d-i passwd/user-fullname string {user}
d-i passwd/username string {user}
d-i passwd/user-password-crypted password !
d-i clock-setup/utc boolean true
d-i time/zone string UTC
d-i partman-auto/disk string /dev/proxmate-unverified
d-i partman/early_command string {choose_disk}
d-i partman-auto/method string regular
d-i partman-auto/choose_recipe select atomic
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true
d-i apt-setup/cdrom/set-first boolean false
d-i apt-setup/cdrom/set-next boolean false
d-i apt-setup/disable-cdrom-entries boolean true
d-i apt-setup/use_mirror boolean true
tasksel tasksel/first multiselect standard
d-i pkgsel/include string openssh-server qemu-guest-agent python3 sudo cloud-init
d-i pkgsel/upgrade select none
popularity-contest popularity-contest/participate boolean false
d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string /dev/sda
d-i preseed/late_command string {late}
d-i cdrom-detect/eject boolean false
d-i finish-install/reboot_in_progress note
"""


def boot_iso(path, cfg, job_id, source):
    linux_install.boot_disc(path, cfg['node'], source, BOOT_FILES, GRUB,
                            {'preseed.cfg': cfg['preseed']}, 'PROXMATE_DEBIAN')
