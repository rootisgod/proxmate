"""QEMU configuration checks shared by review, guest access and execution."""
from collections.abc import Mapping
import hashlib
import json
import re
import uuid


def vm_path(target: Mapping[str, object]) -> str:
    return f'nodes/{target["node"]}/qemu/{target["vmid"]}'


def json_digest(value: object) -> str:
    """Keep the JSON encoding compatible with fingerprints in saved records."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def config_identity(config: Mapping[str, object]) -> str:
    """A frozen configuration review ignores Proxmox's transient metadata."""
    return json_digest({key: value for key, value in config.items() if key not in ('digest', 'lock')})


def machine_identity(config: Mapping[str, object]) -> str:
    """Build ownership survives changes to the guest's name and hardware."""
    return json_digest({key: config.get(key) for key in ('smbios1', 'vmgenid')})


def stable_identity(config: Mapping[str, object]) -> str | None:
    """A new guest connection requires at least one actual identity field."""
    if config.get('smbios1') or config.get('vmgenid'):
        return machine_identity(config)
    return None


def lab_machine_identity(config: Mapping[str, object]) -> str | None:
    """Require a concrete VM UUID before importing provenance or targeting an existing guest."""
    smbios = re.search(r'(?:^|,)uuid=([^,]+)', str(config.get('smbios1', '')), re.I)
    for value in (config.get('vmgenid'), smbios[1] if smbios else None):
        try:
            if uuid.UUID(str(value)).int:
                return machine_identity(config)
        except ValueError:
            continue
    return None


def configured_platform(config: Mapping[str, object]) -> str | None:
    ostype = str(config.get('ostype', ''))
    if ostype.startswith(('win','w2k')) or ostype in ('wvista','wxp'):
        return 'windows'
    return 'linux' if ostype in ('l24','l26') else None


def reviewed_target_identity(config: Mapping[str, object]) -> str:
    """Management plans also pin the reviewed name and attached disk slots."""
    return json_digest({
        key: value for key, value in config.items()
        if key in ('smbios1', 'vmgenid', 'name') or re.fullmatch(r'(scsi|sata|virtio|ide)\d+', key)
    })


def safe_guest_config(config: Mapping[str, object]) -> None:
    """Reject a recovery or test guest that references setup media or host resources."""
    for key, value in config.items():
        if any(prefix in str(value) for prefix in ('proxmate-unattend-', 'proxmate-ubuntu-', 'proxmate-debian-', 'proxmate-rocky-')):
            raise ValueError('Remove generated setup media before backup recovery or template tests')
        if key in ('args', 'hookscript', 'ivshmem') or re.fullmatch(r'(hostpci|usb|virtiofs)\d+', key):
            raise ValueError('Remove passthrough, hooks and custom QEMU arguments before using this recovery/test workflow')
        if '/dev/' in str(value):
            raise ValueError('Host device paths are not allowed in recovery/test guests')
