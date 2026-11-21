"""Read a VM's configuration to seed a portable lab draft."""
import math
import re
from decimal import Decimal

from flask import jsonify, request

import lab_exchange
from lab_pack import HARDWARE_LIMITS
import management
import operations
from vm_config import configured_platform, lab_machine_identity


def boot_disk_gib(config):
    disks = {}
    for slot, spec in config.items():
        if not re.fullmatch(r'(?:ide|sata|scsi|virtio)\d+', slot) or not isinstance(spec, str):
            continue
        volume, *options = spec.split(',')
        if volume and volume != 'none' and 'media=cdrom' not in options and not volume.endswith('cloudinit'):
            disks[slot] = options
    # Order may include an installer CD or NIC. Keep the first configured virtual disk.
    boot = str(config.get('boot', ''))
    if boot.startswith('order='):
        slot = next((s for s in boot[6:].split(';') if s in disks), None)
    elif config.get('bootdisk'):
        slot = config['bootdisk']
    elif len(disks) == 1:
        slot = next(iter(disks))
    else:
        return None
    for option in disks.get(slot, []):
        size = re.fullmatch(r'size=(\d+(?:\.\d+)?)([KMGT])', option)
        if size:
            gib = Decimal(size[1]) * (Decimal(1024) ** ('KMGT'.index(size[2]) - 2))
            return math.ceil(gib)
    return None


def hardware(config, resource):
    def number(value):
        return int(value) if type(value) in (int, str) and str(value).isascii() and str(value).isdigit() else 0

    cores = number(config.get('cores', 1)) * number(config.get('sockets', 1))
    if 'cores' not in config and 'sockets' not in config:
        cores = number(resource.get('maxcpu'))
    memory = number(config.get('memory')) or number(resource.get('maxmem')) // 2**20
    disk = boot_disk_gib(config)
    # Omit hardware outside the pack's supported range so the operator can choose sizes.
    sizing = dict(cores=cores, memory=memory, disk=disk)
    return {key:value for key,value in sizing.items() if value is not None and
            HARDWARE_LIMITS[key][0] <= value <= HARDWARE_LIMITS[key][1]}


def guided_source(manager, vmid, config):
    identity = lab_machine_identity(config)
    if identity is None:
        return None
    with manager.db() as con:
        records = con.execute("""SELECT b.value FROM records b JOIN jobs j ON j.id=b.id
            WHERE b.kind='builds' AND j.action='simple' ORDER BY j.created DESC""")
        for row in records:
            build = manager.decode(row['value'])
            # VM IDs span the cluster. Names and nodes can change during the guest's lifetime.
            if build.get('vmid') == vmid and build.get('machine_identity') == identity:
                try:
                    # The exporter checks the selected server before returning frozen public choices.
                    return lab_exchange.editable_source(manager, build['id'])
                except ValueError:
                    continue
    return None


def register(app, manager, lifecycle):
    @app.post('/api/simple/labs/machine-source')
    def lab_machine_source():
        data = request.get_json()
        if not isinstance(data, dict) or set(data) != {'node', 'vmid'}:
            raise ValueError('Choose the node and VM ID of an existing machine.')
        target = management.target(data)
        client = operations.Proxmox()
        try:
            resource, config = lifecycle.guest(client, target)
        finally:
            client.session.close()
        name = config.get('name') or resource.get('name') or str(target['vmid'])
        source = guided_source(manager, target['vmid'], config)
        if source is not None:
            return jsonify(**source, name=name, basis='guided')
        return jsonify(name=name, basis='hardware', os='windows' if configured_platform(config)=='windows' else None, source=None,
                       settings=hardware(config, resource), tasks=[])
