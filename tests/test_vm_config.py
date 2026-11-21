"""Compatibility with identities persisted in connections, build records and reviewed plans."""
import pytest

from vm_config import config_identity, machine_identity, reviewed_target_identity, safe_guest_config, stable_identity


CONFIG = {
    'name': 'lab-\u00e9', 'smbios1': 'uuid=original', 'vmgenid': 'gen',
    'scsi0': 'local:vm-100-disk-0', 'ide2': 'none,media=cdrom',
    'net0': 'virtio=aa', 'digest': 'one', 'lock': 'backup',
}


def test_persisted_fingerprints_keep_their_encoding_and_field_scope():
    assert config_identity(CONFIG) == '8118c6346d752e7718d85451fbdb65c286bc801bb7b7e13d68353b2e76d5c228'
    assert machine_identity(CONFIG) == 'cfc5c92eddaacac4973f3ce198c6d2770df4e2f31d4f489ff7228671e5806544'
    assert reviewed_target_identity(CONFIG) == '303d5123ae1fad94b299afc0a9f5da6faba9b9aef42967e3810ee58312cd0ce1'
    assert config_identity(dict(CONFIG, lock='snapshot', digest='two')) == config_identity(CONFIG)
    assert config_identity(dict(CONFIG, memory=4096)) != config_identity(CONFIG)
    assert reviewed_target_identity(dict(CONFIG, memory=4096)) == reviewed_target_identity(CONFIG)
    for change in ({'scsi0': 'local:other'}, {'ide2': 'local:iso/x.iso'}, {'name': 'renamed'}):
        assert reviewed_target_identity(dict(CONFIG, **change)) != reviewed_target_identity(CONFIG)
        assert machine_identity(dict(CONFIG, **change)) == machine_identity(CONFIG)
    assert machine_identity(dict(CONFIG, smbios1='uuid=replacement')) != machine_identity(CONFIG)


def test_missing_identifiers_cannot_bind_a_new_connection():
    historical = 'ada9485641a3b676ecd85855fc1c7b0cc4b2414f91a9da4fbd79d9f7400e63d9'
    assert machine_identity({}) == machine_identity({'vmgenid': None}) == historical
    assert stable_identity({}) is None
    assert stable_identity({'smbios1': '', 'vmgenid': None}) is None
    assert stable_identity({'vmgenid': 'gen'}) == machine_identity({'vmgenid': 'gen'})


@pytest.mark.parametrize(('setting', 'message'), [
    ({'ide2': 'local:iso/proxmate-ubuntu-201.iso'}, 'Remove generated setup media'),
    ({'sata1': 'local:iso/proxmate-unattend-201.iso'}, 'Remove generated setup media'),
    ({'hookscript': 'local:script'}, 'Remove passthrough'),
    ({'hostpci10': '00:02.0'}, 'Remove passthrough'),
    ({'scsi1': '/dev/disk/by-id/example'}, 'Host device paths'),
])
def test_recovery_rejects_host_resources_and_setup_media(setting, message):
    safe_guest_config(CONFIG)
    with pytest.raises(ValueError, match=message):
        safe_guest_config(setting)
