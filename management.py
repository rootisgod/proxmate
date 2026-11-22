"""Encrypted management records, immutable review plans, guest jobs and schedules."""
import base64
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import ipaddress
import json
import re
import socket
import time
import uuid
from urllib.parse import urlparse

from flask import jsonify, request
import build_credentials
import operations
from guest_tasks import BUILTINS, DOMAIN_CONTROLLER, compile_task, run_guest, script_parameters, input_type_schema, selected_drive_letters, public_params
from script_library import check_script, ScriptDiagnostic
from task_recovery import TaskProgress
from vm_config import configured_platform, lab_machine_identity, machine_identity, reviewed_target_identity

KINDS = {'credentials', 'connections', 'tasks', 'groups', 'recipes', 'schedules'}
OPS = {'tasks', 'readiness', 'start', 'shutdown', 'stop', 'resize', 'backup', 'snapshot', 'restore'}


def next_occurrence(now, schedule):
    candidate = datetime.fromtimestamp(now + schedule['interval_minutes']*60, timezone.utc)
    if candidate.hour < schedule['window_start']:
        candidate = candidate.replace(hour=schedule['window_start'],minute=0,second=0,microsecond=0)
    elif candidate.hour >= schedule['window_end']:
        candidate = (candidate + timedelta(days=1)).replace(hour=schedule['window_start'],minute=0,second=0,microsecond=0)
    return candidate.timestamp()


def word(value, label='value'):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', value):
        raise ValueError('Invalid ' + label)
    return value


def number(value, low, high):
    if isinstance(value, bool) or not str(value).isdigit() or not low <= int(value) <= high:
        raise ValueError(f'Expected a number from {low} to {high}')
    return int(value)


def target(value):
    if not isinstance(value, dict): raise ValueError('Target must have node and vmid')
    return {'node': word(value.get('node'), 'node'), 'vmid': number(value.get('vmid'), 100, 999999999)}


def target_id(value): return value['node'] + ':' + str(value['vmid'])


ADDRESS_MISMATCH = 'Configured endpoint does not match guest-agent IP addresses'


def connection_recovery_targets(plan, log):
    if plan.get('operation') not in ('tasks', 'readiness'):
        return []
    # Older jobs have only the per-VM result line. Resolve it against the frozen targets.
    failed = {key for key, _, reason in (line.partition(' RESULT: failed - ') for line in log.splitlines())
              if reason.startswith(ADDRESS_MISMATCH)}
    return [dict(node=t['node'], vmid=t['vmid'], reviewed_identity=t['identity'])
            for t in plan.get('targets', []) if t.get('identity') and target_id(t) in failed]


def targets(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 100: raise ValueError('Select 1-100 VMs')
    return list({target_id(t): t for t in map(target, values)}.values())


def name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100 or any(ord(c)<32 for c in value):
        raise ValueError('Name must contain 1-100 printable characters')
    return value.strip()


def boolean(value):
    if not isinstance(value, bool): raise ValueError('Expected true or false')
    return value


def address(value):
    if not isinstance(value, str) or len(value)>253: raise ValueError('Enter a guest IP address or DNS name')
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', value): raise ValueError('Invalid guest address')
    if value.lower() in ('localhost', 'localhost.localdomain'): raise ValueError('Use a guest address, not localhost')
    try: ip = ipaddress.ip_address(value)
    except ValueError: ip = None
    if ip and (ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local):
        raise ValueError('Use a routable guest address')
    if value.lower() == (urlparse(operations.configured_url()).hostname or '').lower():
        raise ValueError('Guest connections cannot target the Proxmox host')
    return value


def endpoint_addresses(binding):
    addresses = {ipaddress.ip_address(row[4][0].split('%')[0]) for row in socket.getaddrinfo(binding['host'],binding['port'],type=socket.SOCK_STREAM)}
    if not addresses or any(ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local for ip in addresses):
        raise ValueError('Guest endpoint resolves to an unsuitable address')
    pve_host = urlparse(operations.configured_url()).hostname
    pve_addresses = {ipaddress.ip_address(row[4][0].split('%')[0]) for row in socket.getaddrinfo(pve_host,8006,type=socket.SOCK_STREAM)}
    if addresses & pve_addresses: raise ValueError('Guest endpoint resolves to the Proxmox host; refusing guest tasks')
    return addresses


class TrustChanged(ValueError):
    pass


def agent_trust(p, path, platform, *, rocky=False):
    if platform == 'windows':
        cert = p.call('GET', path+'/agent/file-read', file=r'C:\ProgramData\Proxmate\winrm.pem').get('content', '')
        if len(cert) > 20000 or '-----BEGIN CERTIFICATE-----' not in cert:
            raise ValueError('Guest HTTPS certificate unavailable')
        return {'ca_pem': cert}
    def read_key(file):
        return p.call('GET',path+'/agent/file-read',file=file).get('content','').split()
    published='/etc/proxmate/ssh-host.pub'
    if rocky:
        key=read_key(published)
    else:
        try:
            key=read_key('/etc/ssh/ssh_host_ed25519_key.pub')
        except RuntimeError as exc:
            if 'permission denied' not in str(exc).lower() or p.call('GET',path+'/agent/get-osinfo').get('result',{}).get('id')!='rocky':
                raise
            key=read_key(published)
    if len(key)<2 or key[0]!='ssh-ed25519': raise ValueError('Guest public SSH key unavailable')
    fingerprint = hashlib.sha256(base64.b64decode(key[1], validate=True)).digest()
    return {'fingerprint': 'SHA256:'+base64.b64encode(fingerprint).decode().rstrip('=')}


def check_saved_trust(previous, trust, identity):
    if previous.get('machine_identity') and previous['machine_identity'] != identity:
        raise TrustChanged('The machine identity changed. Review the saved connection before reconnecting.')
    for field in ('fingerprint','ca_pem'):
        if previous.get(field) and trust.get(field) and previous[field].strip()!=trust[field].strip():
            raise TrustChanged('Guest transport identity changed. Verify its identity in the console and update the saved trust.')


def verify_guest_access(client, guest, binding, credential, log, *, recovering=False, lab_existing=False):
    label = target_id(guest)
    resolved = endpoint_addresses(binding)
    reported = set()
    try:
        interfaces = client.call('GET',f"nodes/{guest['node']}/qemu/{guest['vmid']}/agent/network-get-interfaces")
        addresses = (ipaddress.ip_address(a['ip-address']) for i in interfaces.get('result',[]) for a in i.get('ip-addresses',[]))
        reported = {ip for ip in addresses if not ip.is_loopback and not ip.is_link_local}
        log(label+' guest agent available; reported IPs: '+(', '.join(sorted(map(str,reported))) or 'none'))
    except Exception:
        log(label+' guest agent unavailable; checking configured address instead.')
    insecure_winrm = binding['platform']=='windows' and not binding['verify_tls']
    if lab_existing and (not binding.get('machine_identity') or insecure_winrm):
        if len(resolved)!=1 or not resolved.issubset(reported):
            raise ValueError('The guest agent must report the single resolved address for this lab target. Refresh its connection.')
        if insecure_winrm:
            # Keep both readiness and task connections on the verified IP even if DNS changes.
            binding=dict(binding,host=str(next(iter(resolved))))
    if recovering and binding['platform']=='windows' and not binding['verify_tls'] and not reported:
        raise ValueError('Restore guest-agent address discovery or verify the WinRM certificate before recovering tasks.')
    if reported and not resolved & reported:
        raise ValueError(ADDRESS_MISMATCH + '. Choose Refresh connection, then test and save the current address.')
    if lab_existing:
        # Address discovery can take a transport timeout; confirm the target still owns that report.
        current=client.call('GET',f"nodes/{guest['node']}/qemu/{guest['vmid']}/config")
        if current.get('template') or reviewed_target_identity(current)!=guest['identity'] or not lab_machine_identity(current):
            raise ValueError('The selected machine changed during connection discovery. Review the lab target again.')
    with socket.create_connection((binding['host'],binding['port']),timeout=10): pass
    log(label+' configured transport port reachable.')
    run_guest(binding,credential,[],log,readiness=True)
    return binding


class Manager:
    def __init__(self, db, encode, decode):
        self.db, self.encode, self.decode = db, encode, decode
        self.progress = TaskProgress(self)
        with db() as con:
            con.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, value TEXT, PRIMARY KEY(kind,id))')
            con.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, created REAL, value TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS build_credential_defaults (platform TEXT PRIMARY KEY, credential_id TEXT NOT NULL)')

    def skip_missed_occurrences(self, con, now):
        reports = {'schedules': 'Missed occurrence skipped on service restart.',
                   'backup-policies': 'Missed occurrence skipped on restart.'}
        for kind, report in reports.items():
            rows = con.execute('SELECT id,value FROM records WHERE kind=?', (kind,)).fetchall()
            for ident, value in rows:
                record = self.decode(value)
                if record['next_run'] < now:
                    record['next_run'] = next_occurrence(now, record)
                    record['last_report'] = report
                    con.execute('UPDATE records SET value=? WHERE kind=? AND id=?', (self.encode(record), kind, ident))

    def all(self, kind):
        with self.db() as con: rows = con.execute('SELECT value FROM records WHERE kind=? ORDER BY id', (kind,)).fetchall()
        return [self.decode(row['value']) for row in rows]

    def find(self, kind, ident):
        with self.db() as con: row = con.execute('SELECT value FROM records WHERE kind=? AND id=?', (kind, ident)).fetchone()
        return self.decode(row['value']) if row else None

    def get(self, kind, ident):
        record = self.find(kind, ident)
        if record is None: raise ValueError(f'{kind} record not found')
        return record

    def put(self, kind, value):
        with self.db() as con:
            if kind == 'credentials':
                con.execute('BEGIN IMMEDIATE')
                pinned = con.execute('SELECT platform FROM build_credential_defaults WHERE credential_id=?',(value['id'],)).fetchone()
                if pinned: build_credentials.setup_key(value,pinned['platform'])
            con.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind, value['id'], self.encode(value)))
        return self.public(kind, value)

    def save_verified(self, con, binding, credential, identity, previous, *, new_login=False):
        # Compare revisions and publish the login and binding in the same transaction.
        for kind, record, revision in [('credentials', credential, None if new_login else credential['updated']),
                                       ('connections', binding, previous.get('updated'))]:
            row = con.execute('SELECT value FROM records WHERE kind=? AND id=?',(kind,record['id'])).fetchone()
            if (self.decode(row['value'])['updated'] if row else None) != revision:
                raise ValueError('Guest login or connection changed during verification. Review it and retry.')
        binding.update(machine_identity=identity,verified_at=time.time(),credential_revision=credential['updated'])
        if new_login:
            con.execute('INSERT INTO records VALUES (?,?,?)',('credentials',credential['id'],self.encode(credential)))
        con.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',('connections',binding['id'],self.encode(binding)))

    def public(self, kind, value):
        value = copy.deepcopy(value)
        if kind == 'credentials':
            for key in ('password', 'private_key', 'become_password', 'ca_pem'):
                value['has_' + key] = bool(value.pop(key, ''))
        if kind == 'schedules': value.pop('plan', None)
        return value

    def task(self, ident):
        builtin = next((t for t in BUILTINS if t['id'] == ident), None)
        return copy.deepcopy(builtin) if builtin else self.get('tasks', ident)

    def task_selection(self, values):
        if not isinstance(values, list) or not 1 <= len(values) <= 50: raise ValueError('Choose 1-50 tasks in order')
        result = []
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get('id'), str): raise ValueError('Choose a library task')
            result.append(self.select_task(self.task(value['id']), value.get('params', {})))
        return result

    @staticmethod
    def select_task(item, supplied):
        if not isinstance(supplied, dict): raise ValueError('Task inputs must be an object')
        params = {**item.get('defaults', {}), **supplied}
        compile_task(item, params)
        return {'item': item, 'params': params}

    def save(self, kind, d):
        return self.put(kind, self.prepare(kind, d))

    def prepare(self, kind, d, credential=None):
        if kind not in KINDS or not isinstance(d, dict): raise ValueError('Unknown record type')
        ident = d.get('id') or uuid.uuid4().hex
        if kind == 'connections': ident = target_id(target(d))
        elif not re.fullmatch(r'[a-f0-9]{32}', ident): raise ValueError('Invalid record ID')
        old = self.find(kind, ident) or {}
        if kind == 'connections':
            platform = d.get('platform')
            if platform not in ('windows', 'linux'): raise ValueError('Select Windows or Linux')
            credential = credential or self.get('credentials', d.get('credential_id'))
            if credential['platform'] != platform: raise ValueError('Credential platform does not match guest')
            value = dict(id=ident, **target(d), platform=platform, credential_id=credential['id'], host=address(d.get('host')),
                         port=number(d.get('port', 5986 if platform=='windows' else 22), 1, 65535),
                         verify_tls=boolean(d.get('verify_tls', True)), become=boolean(d.get('become', True)))
            if platform == 'linux':
                pin = d.get('fingerprint', '')
                if not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', pin): raise ValueError('Supply the guest SSH SHA256 fingerprint from a trusted source')
                value['fingerprint'] = pin
            if d.get('clear_ca') is True:
                if d.get('ca_pem'): raise ValueError('Clear the certificate field when removing per-VM trust')
            elif d.get('ca_pem'):
                if not isinstance(d['ca_pem'],str) or len(d['ca_pem'])>20000 or '-----BEGIN CERTIFICATE-----' not in d['ca_pem']: raise ValueError('Invalid connection TLS certificate')
                value['ca_pem']=d['ca_pem']
            elif old.get('ca_pem'): value['ca_pem']=old['ca_pem']
        else:
            value = dict(id=ident, name=name(d.get('name')))
            if kind == 'credentials':
                platform = d.get('platform')
                if platform not in ('windows', 'linux'): raise ValueError('Select Windows or Linux')
                if old and old['platform'] != platform: raise ValueError('Create a new credential to change its platform')
                username = d.get('username')
                if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.@\\-]{1,128}', username): raise ValueError('Invalid guest username')
                value.update(platform=platform, username=username)
                for key in ('password', 'private_key', 'become_password', 'ca_pem'):
                    v = d.get(key) or old.get(key, '')
                    if not isinstance(v, str) or len(v)>20000: raise ValueError('Invalid credential material')
                    value[key] = v
                if not value['password'] and (platform=='windows' or not value['private_key']): raise ValueError('Supply a password or Linux private key')
                if value['private_key'] and 'PRIVATE KEY-----' not in value['private_key']: raise ValueError('Supply a PEM/OpenSSH private key')
                if value['ca_pem'] and '-----BEGIN CERTIFICATE-----' not in value['ca_pem']: raise ValueError('Supply a PEM CA certificate')
            if kind == 'tasks':
                platform, script_kind, body = d.get('platform'), d.get('kind'), d.get('body')
                check_script(platform, script_kind, body)
                if script_kind == 'shell':
                    body = body.replace('\r\n', '\n')
                schema,defaults=script_parameters(d.get('parameters',{}),d.get('defaults',{}))
                retained_types={k:v for k,v in old.get('input_types',{}).items() if k in schema}
                types=input_type_schema(schema,defaults,d.get('input_types',retained_types),platform)
                defaults={**{k:'false' for k,s in types.items() if s['type']=='boolean'},**defaults}
                if schema and script_kind!='ansible': raise ValueError('Named inputs require an Ansible task list')
                value.update(platform=platform, kind=script_kind, body=body, parameters=schema, defaults=defaults, input_types=types, dangerous=True,
                             description='Saved administrator script. Review the code before use.')
            if kind == 'groups':
                value['targets'] = targets(d.get('targets'))
                value['credential_id'] = d.get('credential_id') or ''
                if value['credential_id']: self.get('credentials', value['credential_id'])
            if kind == 'recipes':
                config = copy.deepcopy(d.get('config', {}))
                if not isinstance(config, dict): raise ValueError('Recipe config must be an object')
                if config.get('data_disks'): raise ValueError('Specify new data disks in a reviewed Windows build')
                if config.get('windows', {}).get('admin_password'): raise ValueError('Remove the Windows setup password before saving; recipes use credential references')
                config.pop('confirmed', None)
                config.pop('vmid', None)
                # Validate a reusable build config with a harmless placeholder ID.
                checked = operations.validate(dict(config, action='provision', vmid=999999999))
                checked.pop('vmid', None); checked.pop('confirmed', None)
                value['config'] = checked
                value['tasks'] = d.get('tasks', [])
                if value['tasks']:
                    for selection in self.task_selection(value['tasks']):
                        if public_params(selection)!=selection['params']:
                            raise ValueError('Use a guided saved build or supply the restore password in Operations. Advanced recipes cannot store secret task inputs.')
                value['credential_id'] = d.get('credential_id') or ''
                if value['credential_id']: self.get('credentials', value['credential_id'])
            if kind == 'schedules':
                plan = self.load_plan(d['plan_id']) if d.get('plan_id') else old.get('plan')
                if plan and plan.get('recovery'): raise ValueError('Recovery applies once to the original machine.')
                if plan and any(s['item']['id']==DOMAIN_CONTROLLER for s in plan.get('tasks',[])):
                    raise ValueError('Review domain controller promotion as a one-time operation.')
                if not plan or plan['operation'] not in ('tasks', 'backup', 'shutdown', 'start', 'readiness'): raise ValueError('Review a task, backup, start, shutdown or readiness plan first')
                if d.get('confirmed') is not True: raise ValueError('Confirm the exact scheduled targets and operation')
                if plan['dangerous'] and d.get('risk_confirmed') is not True: raise ValueError('Confirm risky tasks explicitly')
                value.update(plan=plan, interval_minutes=number(d.get('interval_minutes'), 15, 525600),
                             window_start=number(d.get('window_start', 0), 0, 23), window_end=number(d.get('window_end', 24), 1, 24),
                             enabled=boolean(d.get('enabled', False)))
                if value['window_start'] >= value['window_end']: raise ValueError('Use a same-day UTC window with end after start')
                value['next_run'] = next_occurrence(time.time(), value)
                value['last_job'] = old.get('last_job'); value['last_report'] = old.get('last_report', 'No runs yet.')
                value['summary'] = self.summary(plan)
        value['updated'] = time.time()
        return value

    def delete(self, kind, ident):
        self.get(kind, ident)
        # Do not leave dangling references in saved configurations.
        with self.db() as con: kinds={r['kind'] for r in con.execute('SELECT DISTINCT kind FROM records')}
        for other in kinds:
            for record in self.all(other):
                if other==kind and record['id']==ident: continue
                if ident in json.dumps(record): raise ValueError('Record is referenced by a connection, group, recipe or schedule; update those first')
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE')
            if kind=='credentials' and con.execute('SELECT 1 FROM build_credential_defaults WHERE credential_id=?',(ident,)).fetchone():
                raise ValueError('Choose another build default or clear it before deleting this credential.')
            if kind=='credentials':
                active=con.execute("SELECT payload FROM jobs WHERE action='simple' AND status IN ('queued','running','waiting')")
                if any(self.decode(row['payload'])['plan']['config'].get('bootstrap',{}).get('credential_id')==ident for row in active):
                    raise ValueError('An active machine build uses this credential. Wait for it to finish before deleting it.')
            con.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, ident))

    def create_plan(self, d, *, selections=None, recovery=None):
        plan = self.review_operation(d,selections=selections,recovery=recovery)
        with self.db() as con:
            return self.store_plan(con, plan)

    def store_plan(self, con, plan):
        ident = uuid.uuid4().hex
        con.execute('DELETE FROM plans WHERE created < ?', (time.time()-86400,))
        con.execute('INSERT INTO plans VALUES (?,?,?)', (ident,time.time(),self.encode(plan)))
        return dict(id=ident, **self.summary(plan))

    def review_operation(self, d, *, selections=None, discovered=None, recovery=None, lab_existing=False):
        if not isinstance(d, dict): raise ValueError('Plan must be a JSON object')
        op = d.get('operation')
        if op not in OPS: raise ValueError('Unknown management operation')
        chosen = list(d.get('targets', []))
        group_ids = d.get('group_ids', [])
        if not isinstance(group_ids, list) or len(group_ids)>100: raise ValueError('Invalid groups')
        group_credentials = {}
        for ident in group_ids:
            group = self.get('groups', ident)
            chosen.extend(group['targets'])
            for t in group['targets']:
                if group.get('credential_id'):
                    previous = group_credentials.get(target_id(t))
                    if previous and previous != group['credential_id']: raise ValueError('Overlapping groups have different credentials; select VMs directly')
                    group_credentials[target_id(t)] = group['credential_id']
        chosen = targets(chosen)
        if selections is None: selections = self.task_selection(d.get('tasks')) if op=='tasks' else []
        if any(s['item']['id']==DOMAIN_CONTROLLER for s in selections) and len(chosen)!=1:
            raise ValueError('Choose one Windows Server for the first domain controller.')
        drive_letters=selected_drive_letters(selections)
        discovered = discovered or {}
        p = operations.Proxmox()
        resources = p.resources()
        plan = {'operation': op, 'targets': [], 'tasks': selections, 'snapshot_before': boolean(d.get('snapshot_before', False)),
                'dangerous': op in ('stop', 'restore') or any(t['item'].get('dangerous') for t in selections)}
        lab_existing=bool(lab_existing or recovery and recovery.get('lab_existing'))
        if lab_existing: plan['lab_existing']=True
        if recovery:
            plan.update(recovery=recovery, template_build=recovery['template_build'])
            plan['dangerous'] |= bool(recovery['started'] or recovery['template_build'] or
                                      any(s['item']['id']=='prepare-created-volume' for s in selections))
        if op=='resize': plan.update(cores=number(d.get('cores'),1,128), memory=number(d.get('memory'),512,1048576))
        if op=='backup': plan['backup_storage'] = word(d.get('backup_storage'), 'backup storage')
        if op=='restore': plan['snapshot'] = word(d.get('snapshot'), 'snapshot name')
        if op=='snapshot': plan['snapshot'] = 'proxmate-' + uuid.uuid4().hex[:16]
        for t in chosen:
            guest = next((r for r in resources if r.get('vmid')==t['vmid'] and r.get('node')==t['node'] and r.get('type')=='qemu' and not r.get('template')), None)
            if not guest: raise ValueError('Choose existing QEMU VMs, not templates or containers: ' + target_id(t))
            cfg = p.call('GET', f'nodes/{t["node"]}/qemu/{t["vmid"]}/config')
            if lab_existing and (guest.get('status')!='running' or cfg.get('template') or not lab_machine_identity(cfg)):
                raise ValueError('Choose a running non-template QEMU VM with a stable UUID.')
            t.update(name=guest.get('name',''), identity=self.identity(cfg))
            if lab_existing: t['name']=cfg.get('name') or t['name']
            if recovery:
                if not (cfg.get('smbios1') or cfg.get('vmgenid')) or t['identity'] != recovery['identity']:
                    raise ValueError('The original VM identity or attached disks changed. Inspect the machine before running new tasks.')
                if guest.get('status') != 'running':
                    raise ValueError('Start the original machine before reviewing its remaining tasks.')
            if op=='resize' and guest.get('status')!='stopped': raise ValueError('Shut down VMs before changing CPU/RAM; new values apply on next start')
            if op=='restore' and guest.get('status')!='stopped': raise ValueError('Shut down VMs before restoring a snapshot')
            if op in ('tasks', 'readiness'):
                binding = discovered.get(('connections', target_id(t))) or self.get('connections', target_id(t))
                if binding.get('machine_identity'):
                    if binding['machine_identity'] != machine_identity(cfg):
                        raise ValueError('The saved connection belongs to another machine. Review its identity and reconnect.')
                if group_credentials.get(target_id(t)): binding['credential_id'] = group_credentials[target_id(t)]
                credential = discovered.get(('credentials', binding['credential_id'])) or self.get('credentials', binding['credential_id'])
                if credential['platform'] != binding['platform']: raise ValueError('Group credential does not match guest platform')
                if any(s['item']['platform'] != binding['platform'] for s in selections): raise ValueError('All selected tasks must match each guest platform')
                if lab_existing and configured_platform(cfg) not in (None,binding['platform']):
                    raise ValueError('The selected login and tasks do not match the configured guest operating system.')
                if recovery: verify_guest_access(p,t,binding,credential,lambda message: None,recovering=True,lab_existing=lab_existing)
                if drive_letters:
                    from winrm_setup import check_letters, used_drive_letters
                    check_letters(drive_letters,used_drive_letters(p,t,cfg))
                t['connection'] = binding
                t['credential_name'] = credential['name']
                t['credential_revision'] = credential['updated']
            plan['targets'].append(t)
        return plan

    identity = staticmethod(reviewed_target_identity)

    @staticmethod
    def summary(plan):
        tasks=[]
        for selection in plan['tasks']:
            item=selection['item']
            summary=dict(name=item['name'],params=public_params(selection))
            if plan.get('recovery') and item['id']=='prepare-created-volume':
                summary['data_disk']={key:item['disk'][key] for key in ('size','label','letter')}
            tasks.append(summary)
        return dict(operation=plan['operation'], targets=[dict({k:t[k] for k in ('node','vmid','name')},
                    **({'guest_host':t['connection']['host'],'port':t['connection']['port'],'platform':t['connection']['platform'],
                        'credential_name':t.get('credential_name',''),'verify_tls':t['connection']['verify_tls']} if 'connection' in t else {})) for t in plan['targets']],
                    tasks=tasks,
                    snapshot_before=plan['snapshot_before'], dangerous=plan['dangerous'],
                    **({'recovery': {k:plan['recovery'][k] for k in ('job_id','completed','started')},
                        'finish_template':bool(plan.get('template_build'))} if plan.get('recovery') else {}),
                    settings={k:plan[k] for k in ('cores','memory','backup_storage','snapshot') if k in plan},
                    notes=['CPU/RAM changes require stopped guests and apply on next start.', 'Snapshots are retained; restoration is a separate reviewed action.',
                           'Guest scripts are trusted administrator code. Script output is withheld from logs.', 'Failures are reported per VM; no automatic rollback or force shutdown.'])

    def load_plan(self, ident):
        with self.db() as con: row = con.execute('SELECT * FROM plans WHERE id=?', (ident,)).fetchone()
        if not row or row['created'] < time.time()-1800: raise ValueError('Review plan expired; review again')
        return self.decode(row['value'])

    def enqueue(self, plan, job_id):
        server_id = operations.connection_provider.current()['id']
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE')
            return self.queue_operation(con,plan,job_id,server_id)

    def queue_operation(self, con, plan, job_id, server_id, *, lab_run_id=None):
        if plan.get('operation') not in OPS: raise ValueError('Choose a management operation plan')
        payload = {'action': 'manage', 'plan': plan, 'server_id':server_id}
        if lab_run_id:
            payload['lab_run_id']=lab_run_id
        if plan.get('lab_existing'):
            if not lab_run_id and not plan.get('recovery'):
                raise ValueError('Confirm this existing target through its reviewed lab.')
            for target in plan['targets']:
                binding=target['connection']
                for kind,ident,revision in [('connections',binding['id'],binding['updated']),
                                            ('credentials',binding['credential_id'],target['credential_revision'])]:
                    row=con.execute('SELECT value FROM records WHERE kind=? AND id=?',(kind,ident)).fetchone()
                    if not row or self.decode(row['value'])['updated']!=revision:
                        raise ValueError('The guest login or connection changed after review. Review the lab again.')
        inserted=con.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?)', (job_id,'manage','queued',time.time(),time.time(),self.encode(payload),'Queued reviewed '+plan['operation']+' operation.'))
        if inserted.rowcount and plan['operation']=='tasks':
            if plan.get('recovery'): self.progress.claim(con,job_id,plan,server_id)
            self.progress.initialize(con,job_id,plan,server_id)
        return {'id': job_id, 'url': '/api/jobs/'+job_id}

    def tick(self, now=None):
        now = time.time() if now is None else now
        # Claim and enqueue atomically, so repeated ticks cannot duplicate a run.
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE')
            rows = con.execute("SELECT * FROM records WHERE kind='schedules'").fetchall()
            for row in rows:
                s = self.decode(row['value'])
                if not s['enabled'] or s['next_run'] > now: continue
                due = s['next_run']; s['next_run'] = next_occurrence(now, s)
                active = con.execute("SELECT id FROM jobs WHERE id=? AND status IN ('queued','running','waiting')",(s.get('last_job'),)).fetchone()
                hour = datetime.fromtimestamp(now,timezone.utc).hour
                if active: s['last_report'] = 'Occurrence skipped: previous run still active.'
                elif now-due > 120: s['last_report'] = 'Missed occurrence skipped; no catch-up run.'
                elif not s['window_start'] <= hour < s['window_end']: s['last_report'] = 'Occurrence skipped outside UTC maintenance window.'
                else:
                    ident = uuid.uuid4().hex
                    scheduled_plan = dict(s['plan'], execution_window=[s['window_start'],s['window_end']])
                    con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(ident,'manage','queued',now,now,self.encode({'action':'manage','plan':scheduled_plan}),'Scheduled '+s['name']))
                    s['last_job'] = ident; s['last_report'] = 'Queued. See job for per-VM results.'
                con.execute('UPDATE records SET value=? WHERE kind=? AND id=?',(self.encode(s),'schedules',s['id']))

    def execute(self, plan, log, job_id=None):
        track = job_id and plan['operation']=='tasks'
        if track:
            with self.db() as con:
                self.progress.initialize(con,job_id,plan,operations.connection_provider.current()['id'])
        p = operations.Proxmox()
        failures = 0
        for t in plan['targets']:
            label = target_id(t)
            prefix = f'nodes/{t["node"]}/qemu/{t["vmid"]}'
            try:
                if plan.get('execution_window'):
                    start, end = plan['execution_window']
                    if not start <= datetime.fromtimestamp(time.time(),timezone.utc).hour < end:
                        raise ValueError('Maintenance window closed; target skipped without changes')
                resource = next((r for r in p.resources() if r.get('vmid')==t['vmid'] and r.get('node')==t['node'] and r.get('type')=='qemu' and not r.get('template')),None)
                if not resource: raise ValueError('Target missing, migrated or converted to template; review again')
                cfg = p.call('GET',prefix+'/config')
                if self.identity(cfg) != t['identity']: raise ValueError('VM identity or disks changed since review; review again')
                if plan.get('lab_existing') and (cfg.get('template') or not lab_machine_identity(cfg)):
                    raise ValueError('The existing machine no longer has its reviewed VM identity.')
                op = plan['operation']
                def task(path, **args): p.wait(t['node'],p.call('POST',prefix+path,**args),log)
                if op in ('tasks','readiness'):
                    b = t['connection']; cred = self.get('credentials',b['credential_id'])
                    if (plan.get('recovery') or plan.get('lab_existing')) and self.get('connections',label)['updated'] != b['updated']:
                        raise ValueError('Guest connection changed since review. Review the remaining tasks again.')
                    if cred['updated'] != t['credential_revision']: raise ValueError('Credential changed since review; review again')
                    if resource.get('status')!='running': raise ValueError('Guest is not running')
                    b=verify_guest_access(p,t,b,cred,log,recovering=bool(plan.get('recovery')),lab_existing=bool(plan.get('lab_existing')))
                    if op=='tasks':
                        if plan['snapshot_before']:
                            snap = 'proxmate-'+uuid.uuid4().hex[:16]
                            task('/snapshot',snapname=snap,description='Proxmate pre-task snapshot',vmstate=0)
                            log(label+' retained pre-task snapshot: '+snap)
                        for index, selection in enumerate(plan['tasks']):
                            log(label+' running: '+selection['item']['name'])
                            letters=selected_drive_letters([selection])
                            if letters:
                                from winrm_setup import check_letters, used_drive_letters
                                check_letters(letters,used_drive_letters(p,t,cfg))
                            steps=compile_task(selection['item'],selection['params'])
                            if track: self.progress.mark(job_id,label,index,completed=False)
                            run_guest(b,cred,steps,log)
                            if track: self.progress.mark(job_id,label,index,completed=True)
                elif op in ('start','shutdown','stop'):
                    task('/status/'+op, **({'timeout':120,'forceStop':0} if op=='shutdown' else {}))
                elif op=='resize':
                    if resource.get('status')!='stopped': raise ValueError('Guest must remain stopped for CPU/RAM changes')
                    p.wait(t['node'],p.call('PUT',prefix+'/config',cores=plan['cores'],sockets=1,memory=plan['memory'],digest=cfg['digest']),log)
                    log(label+' CPU/RAM saved; effective on next start.')
                elif op=='backup':
                    p.wait(t['node'],p.call('POST',f'nodes/{t["node"]}/vzdump',vmid=t['vmid'],storage=plan['backup_storage'],mode='snapshot',compress='zstd'),log)
                elif op=='snapshot': task('/snapshot',snapname=plan['snapshot'],vmstate=0)
                elif op=='restore':
                    if resource.get('status')!='stopped': raise ValueError('Guest must remain stopped before snapshot restore')
                    snapshots = p.call('GET',prefix+'/snapshot')
                    if plan['snapshot'] not in {s['name'] for s in snapshots if s['name']!='current'}: raise ValueError('Snapshot does not exist')
                    task('/snapshot/'+plan['snapshot']+'/rollback')
                log(label+' RESULT: succeeded')
            except Exception as exc:
                failures += 1
                # Never publish arbitrary Ansible exception text (may include credentials).
                reason = str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
                if 'Proxmox HTTP' in reason: reason = 'Proxmox rejected the operation; inspect the VM task history.'
                log(label+' RESULT: failed - '+reason)
        log(f'Report: {len(plan["targets"])-failures} succeeded, {failures} failed.')
        if failures: raise RuntimeError('One or more VMs failed; inspect per-VM results before retrying.')


def register(app, db, encode, decode):
    manager = Manager(db,encode,decode)
    manager.progress.register(app)

    @app.get('/api/manage/<kind>')
    def list_records(kind):
        if kind not in KINDS: raise ValueError('Unknown record type')
        values = [manager.public(kind,r) for r in manager.all(kind)]
        if kind=='credentials':
            defaults=build_credentials.defaults(manager)
            for value in values:
                value['is_build_default']=(defaults[value['platform']] or {}).get('id')==value['id']
        if kind=='tasks': values = BUILTINS + values
        if kind=='schedules':
            with db() as con:
                for value in values:
                    row = con.execute('SELECT status,log FROM jobs WHERE id=?',(value.get('last_job'),)).fetchone()
                    value['last_status'] = row['status'] if row else None
        return jsonify(values)

    @app.route('/api/manage/credentials/defaults', methods=['GET','POST'])
    def credential_defaults():
        if request.method=='POST': return jsonify(build_credentials.set_default(manager,request.get_json()))
        return jsonify(build_credentials.defaults(manager))

    @app.post('/api/manage/tasks/check')
    def check_guest_script():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Supply a guest script')
        try:
            check_script(data.get('platform'), data.get('kind'), data.get('body'))
        except ScriptDiagnostic as exc:
            return jsonify(valid=False, message=exc.message, line=exc.line, column=exc.column)
        except ValueError as exc:
            return jsonify(valid=False, message=str(exc))
        return jsonify(valid=True)

    @app.post('/api/manage/<kind>')
    def save_record(kind): return jsonify(manager.save(kind,request.get_json()))

    @app.post('/api/manage/<kind>/<ident>/delete')
    def delete_record(kind,ident):
        if kind not in KINDS: raise ValueError('Unknown record type')
        manager.delete(kind,ident)
        return jsonify(ok=True)

    @app.post('/api/manage/plan')
    def review(): return jsonify(manager.create_plan(request.get_json()))

    @app.post('/api/manage/run')
    def run():
        d = request.get_json()
        if not isinstance(d, dict): raise ValueError('Submission must be a JSON object')
        plan = manager.load_plan(d.get('plan_id'))
        if d.get('confirmed') is not True: raise ValueError('Confirm the exact reviewed targets')
        if plan['dangerous'] and d.get('risk_confirmed') is not True: raise ValueError('Explicitly confirm the destructive operation or trusted script')
        return jsonify(manager.enqueue(plan,d['plan_id'])),202

    @app.get('/api/manage/snapshots/<node>/<int:vmid>')
    def snapshots(node,vmid):
        t=target(dict(node=node,vmid=vmid)); p=operations.Proxmox()
        if not any(r.get('node')==node and r.get('vmid')==vmid and r.get('type')=='qemu' for r in p.resources()): raise ValueError('VM not found')
        return jsonify(p.call('GET',f'nodes/{t["node"]}/qemu/{t["vmid"]}/snapshot'))

    return manager
