"""Read-only comparison of a lab's recorded guests with the selected Proxmox server."""
from collections import Counter

from requests import RequestException

from management import target_id
from vm_config import lab_machine_identity, vm_path


def _role_evidence(manager, run):
    """Read the original login and identity from the jobs belonging to this run."""
    roles = {role['job_id']: role for role in run['machines']}
    evidence = {}
    with manager.db() as con:
        for row in con.execute("""
            SELECT j.id, j.action, j.payload, b.value AS build
            FROM lab_jobs l JOIN jobs j ON j.id=l.job_id
            LEFT JOIN records b ON b.kind='builds' AND b.id=j.id
            WHERE l.run_id=?
        """, (run['id'],)):
            payload = manager.decode(row['payload'])
            role = roles.get(row['id'])
            if not role or payload.get('server_id') != run['server_id']:
                continue
            plan = payload.get('plan', {})
            location = (role['node'], role['vmid'])
            if row['action'] == 'simple' and role['mode'] == 'create':
                config = plan.get('config', {})
                if (config.get('node'), config.get('vmid')) != location:
                    continue
                build = manager.decode(row['build']) if row['build'] else {}
                login = config.get('bootstrap', {}).get('credential_id')
                recorded = (build.get('node'), build.get('vmid')) == location
                evidence[row['id']] = (recorded, build.get('machine_identity') if recorded else None, login)
            elif row['action'] == 'manage' and role['mode'] == 'existing' and len(plan.get('targets', [])) == 1:
                target = plan['targets'][0]
                if (target.get('node'), target.get('vmid')) == location:
                    binding = target.get('connection', {})
                    evidence[row['id']] = (False, binding.get('machine_identity'), binding.get('credential_id'))
    return evidence


def _assessment(role, resource, config, creation_recorded, identity_matches, deployment_active):
    if not resource:
        return False, 'No QEMU guest is visible at the recorded node and VM ID.'
    if config is None:
        return False, 'The current VM configuration could not be read.'
    if role['mode'] == 'existing':
        return False, 'This role ran scripts on an existing VM.'
    if not creation_recorded:
        return False, 'No matching creation record is available for this role.'
    if not identity_matches:
        return False, 'The current VM identity could not be matched to the recorded build.'
    if deployment_active:
        return False, 'This deployment still has unfinished jobs.'
    if config.get('template') or resource.get('template'):
        return False, 'This guest is a template and may be in use by other builds.'
    if config.get('lock') or config.get('protection'):
        return False, 'Proxmox reports a lock or removal protection on this guest.'
    return True, 'The current VM identity matches the build recorded for this role.'


def preview(manager, run, client):
    evidence = _role_evidence(manager, run)
    resources = {(r['node'], r['vmid']): r for r in client.resources() if r.get('type') == 'qemu'}
    connections = {c['id']: c for c in manager.all('connections')}
    usage = Counter(c.get('credential_id') for c in connections.values())
    active = any(m['status'] in ('queued', 'pending', 'running', 'waiting') for m in run['machines'])
    machines = []
    for role in run['machines']:
        recorded, expected, login = evidence.get(role['job_id'], (False, None, None))
        resource = resources.get((role['node'], role['vmid']))
        config = None
        if resource:
            try:
                config = client.call('GET', vm_path(role) + '/config')
            except (RequestException, RuntimeError, ValueError):
                pass  # Keep other roles inspectable when a guest disappears or access is denied.
        identity_matches = bool(expected and config and lab_machine_identity(config) == expected)
        candidate, reason = _assessment(role, resource, config, recorded, identity_matches, active)
        binding = connections.get(target_id(role), {})
        credential = manager.find('credentials', login) if login else None
        verified_at = None
        saved_login = None
        if credential:
            used_elsewhere = usage[login] > int(binding.get('credential_id') == login)
            saved_login = dict(name=credential['name'], protected=True,
                               reason='Also assigned to another VM.' if used_elsewhere else 'Saved logins are retained.')
            if (identity_matches and binding.get('machine_identity') == expected and
                    binding.get('credential_id') == login and credential.get('updated') is not None and
                    binding.get('credential_revision') == credential['updated']):
                verified_at = binding.get('verified_at')
        machines.append(dict(key=role['key'], mode=role['mode'], name=role['name'], node=role['node'], vmid=role['vmid'],
                             current_name=(config or {}).get('name') or (resource or {}).get('name'),
                             power=(resource or {}).get('status'), identity_matches=identity_matches,
                             creation_recorded=recorded,
                             candidate=candidate, reason=reason, credential=saved_login, verified_at=verified_at))
    return dict(id=run['id'], name=run['name'], server_id=run['server_id'], machines=machines)
