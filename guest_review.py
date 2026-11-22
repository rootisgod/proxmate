"""Freeze work for existing guests and publish discovered logins in the caller's transaction."""
import ipaddress
import time
import uuid

import management
import operations
from vm_config import lab_machine_identity, vm_path


def _discover_connections(manager, targets, selections, login, lab_existing):
    bindings = {row['id'] for row in manager.all('connections')}
    missing = [target for target in targets if management.target_id(target) not in bindings]
    if not missing:
        if lab_existing and ('credential_id' in login or 'credential' in login):
            raise ValueError('This machine already has a saved guest connection. Use its configured login.')
        return {}

    discovered = {}
    if login.get('credential_id'):
        credential = manager.get('credentials', login['credential_id'])
    elif isinstance(login.get('credential'), dict):
        fields = {k: login['credential'].get(k) for k in
                  ('platform', 'username', 'password', 'private_key', 'become_password', 'ca_pem')}
        credential = manager.prepare('credentials', dict(fields, name='Guest login ' + time.strftime('%Y-%m-%d %H:%M')))
        discovered['credentials', credential['id']] = credential
    else:
        raise ValueError('Choose a saved login or enter a username and password for these machines')

    client = operations.Proxmox()
    resources = client.resources()
    for target in missing:
        guest = next((r for r in resources if r.get('node') == target['node'] and r.get('vmid') == target['vmid']
                      and r.get('type') == 'qemu' and not r.get('template')), None)
        if not guest:
            raise ValueError('Choose existing virtual machines')
        if any(s['item']['platform'] != credential['platform'] for s in selections):
            raise ValueError('The login and scripts must use the same operating system')
        path = vm_path(target)
        if lab_existing:
            if guest.get('status') != 'running':
                raise ValueError('Choose a running guest for existing-machine tasks.')
            config = client.call('GET', path + '/config')
            identity = lab_machine_identity(config)
            if config.get('template') or not identity:
                raise ValueError('Choose a non-template QEMU VM with a stable UUID.')
        try:
            interfaces = client.call('GET', path + '/agent/network-get-interfaces')
            hosts = set()
            for interface in interfaces.get('result', []):
                for entry in interface.get('ip-addresses', []):
                    if entry.get('ip-address-type') == 'ipv4':
                        address = ipaddress.ip_address(entry['ip-address'])
                        if not address.is_loopback and not address.is_link_local:
                            hosts.add(entry['ip-address'])
            if len(hosts) != 1:
                raise ValueError('More than one address')
            binding = dict(target, host=management.address(hosts.pop()), credential_id=credential['id'],
                           platform=credential['platform'], verify_tls=True, become=True)
            if credential['platform'] == 'linux' or not credential.get('ca_pem'):
                try:
                    binding.update(management.agent_trust(client, path, credential['platform']))
                except RuntimeError:
                    if credential['platform'] == 'linux':
                        raise
                    # Windows listeners may chain to a system-trusted CA.
            record = manager.prepare('connections', binding, credential)
            if lab_existing:
                record['machine_identity'] = identity
            discovered['connections', record['id']] = record
        except Exception as exc:
            raise ValueError(f"Could not detect the connection for VM {target['vmid']}. Check its guest agent or use "
                             "Inventory > Refresh connection to verify its address and login.") from exc
    return discovered


def prepare(manager, targets, selections, login, *, lab_existing):
    discovered = _discover_connections(manager, targets, selections, login, lab_existing)
    operation = manager.review_operation(dict(operation='tasks', targets=targets), selections=selections,
                                         discovered=discovered, lab_existing=lab_existing)
    # This shape is also embedded in encrypted lab reviews until confirmation.
    return dict(operation=operation, discoveries=[(kind, record) for (kind, _), record in discovered.items()])


def _publish(con, manager, work):
    for kind, record in work['discoveries']:
        inserted = con.execute('INSERT OR IGNORE INTO records VALUES (?,?,?)',
                               (kind, record['id'], manager.encode(record)))
        if not inserted.rowcount:
            raise ValueError('Guest connection changed during discovery. Review the run again.')


def save_standalone(manager, targets, selections, login):
    work = prepare(manager, targets, selections, login, lab_existing=False)
    with manager.db() as con:
        _publish(con, manager, work)
        return manager.store_plan(con, work['operation'])


def queue_lab_role(con, manager, work, server_id, run_id):
    _publish(con, manager, work)
    return manager.queue_operation(con, work['operation'], uuid.uuid4().hex, server_id, lab_run_id=run_id)
