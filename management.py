"""Encrypted management records, immutable review plans, guest jobs and schedules."""
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import ipaddress
import json
import re
import socket
import time
import uuid
import os
from urllib.parse import urlparse

from flask import jsonify, request
import operations
from guest_tasks import BUILTINS, compile_task, run_guest, validate_script

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


class Manager:
    def __init__(self, db, encode, decode):
        self.db, self.encode, self.decode = db, encode, decode
        with db() as con:
            con.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, value TEXT, PRIMARY KEY(kind,id))')
            con.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, created REAL, value TEXT)')
        # Deliberately skip missed schedule occurrences on restart.
        now = time.time()
        for record in self.all('schedules'):
            if record['next_run'] < now:
                record['next_run'] = next_occurrence(now, record)
                record['last_report'] = 'Missed occurrence skipped on service restart.'
                self.put('schedules', record)

    def all(self, kind):
        with self.db() as con: rows = con.execute('SELECT value FROM records WHERE kind=? ORDER BY id', (kind,)).fetchall()
        return [self.decode(row['value']) for row in rows]

    def get(self, kind, ident):
        with self.db() as con: row = con.execute('SELECT value FROM records WHERE kind=? AND id=?', (kind, ident)).fetchone()
        if not row: raise ValueError(f'{kind} record not found')
        return self.decode(row['value'])

    def put(self, kind, value):
        with self.db() as con: con.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind, value['id'], self.encode(value)))
        return self.public(kind, value)

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
            item = self.task(value['id'])
            params = value.get('params', {})
            compile_task(item, params)
            result.append({'item': item, 'params': params})
        return result

    def save(self, kind, d):
        if kind not in KINDS or not isinstance(d, dict): raise ValueError('Unknown record type')
        ident = d.get('id') or uuid.uuid4().hex
        if kind == 'connections': ident = target_id(target(d))
        elif not re.fullmatch(r'[a-f0-9]{32}', ident): raise ValueError('Invalid record ID')
        old = next((r for r in self.all(kind) if r['id'] == ident), {})
        if kind == 'connections':
            platform = d.get('platform')
            if platform not in ('windows', 'linux'): raise ValueError('Select Windows or Linux')
            credential = self.get('credentials', d.get('credential_id'))
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
                if platform not in ('linux', 'windows') or script_kind not in ('powershell', 'shell', 'ansible'): raise ValueError('Choose a platform and script type')
                if (script_kind=='powershell' and platform!='windows') or (script_kind=='shell' and platform!='linux'): raise ValueError('Script type does not match platform')
                if not isinstance(body, str) or not body.strip() or len(body)>100000: raise ValueError('Script must contain 1-100000 characters')
                if script_kind=='ansible': validate_script(body)
                value.update(platform=platform, kind=script_kind, body=body, parameters={}, dangerous=True,
                             description='Saved administrator script. Review the code before use.')
            if kind == 'groups':
                value['targets'] = targets(d.get('targets'))
                value['credential_id'] = d.get('credential_id') or ''
                if value['credential_id']: self.get('credentials', value['credential_id'])
            if kind == 'recipes':
                config = copy.deepcopy(d.get('config', {}))
                if not isinstance(config, dict): raise ValueError('Recipe config must be an object')
                if config.get('windows', {}).get('admin_password'): raise ValueError('Remove the Windows setup password before saving; recipes use credential references')
                config.pop('confirmed', None)
                config.pop('vmid', None)
                # Validate a reusable build config with a harmless placeholder ID.
                checked = operations.validate(dict(config, action='provision', vmid=999999999))
                checked.pop('vmid', None); checked.pop('confirmed', None)
                value['config'] = checked
                value['tasks'] = d.get('tasks', [])
                if value['tasks']: self.task_selection(value['tasks'])
                value['credential_id'] = d.get('credential_id') or ''
                if value['credential_id']: self.get('credentials', value['credential_id'])
            if kind == 'schedules':
                plan = self.load_plan(d['plan_id']) if d.get('plan_id') else old.get('plan')
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
        return self.put(kind, value)

    def delete(self, kind, ident):
        self.get(kind, ident)
        # Do not leave dangling references in saved configurations.
        with self.db() as con: kinds={r['kind'] for r in con.execute('SELECT DISTINCT kind FROM records')}
        for other in kinds:
            for record in self.all(other):
                if other==kind and record['id']==ident: continue
                if ident in json.dumps(record): raise ValueError('Record is referenced by a connection, group, recipe or schedule; update those first')
        with self.db() as con: con.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, ident))

    def create_plan(self, d):
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
        selections = self.task_selection(d.get('tasks')) if op=='tasks' else []
        p = operations.Proxmox()
        resources = p.resources()
        plan = {'operation': op, 'targets': [], 'tasks': selections, 'snapshot_before': boolean(d.get('snapshot_before', False)),
                'dangerous': op in ('stop', 'restore') or any(t['item'].get('dangerous') for t in selections)}
        if op=='resize': plan.update(cores=number(d.get('cores'),1,128), memory=number(d.get('memory'),512,1048576))
        if op=='backup': plan['backup_storage'] = word(d.get('backup_storage'), 'backup storage')
        if op=='restore': plan['snapshot'] = word(d.get('snapshot'), 'snapshot name')
        if op=='snapshot': plan['snapshot'] = 'proxmate-' + uuid.uuid4().hex[:16]
        for t in chosen:
            guest = next((r for r in resources if r.get('vmid')==t['vmid'] and r.get('node')==t['node'] and r.get('type')=='qemu' and not r.get('template')), None)
            if not guest: raise ValueError('Choose existing QEMU VMs, not templates or containers: ' + target_id(t))
            cfg = p.call('GET', f'nodes/{t["node"]}/qemu/{t["vmid"]}/config')
            t.update(name=guest.get('name',''), identity=self.identity(cfg))
            if op=='resize' and guest.get('status')!='stopped': raise ValueError('Shut down VMs before changing CPU/RAM; new values apply on next start')
            if op=='restore' and guest.get('status')!='stopped': raise ValueError('Shut down VMs before restoring a snapshot')
            if op in ('tasks', 'readiness'):
                binding = self.get('connections', target_id(t))
                if group_credentials.get(target_id(t)): binding['credential_id'] = group_credentials[target_id(t)]
                credential = self.get('credentials', binding['credential_id'])
                if credential['platform'] != binding['platform']: raise ValueError('Group credential does not match guest platform')
                if any(s['item']['platform'] != binding['platform'] for s in selections): raise ValueError('All selected tasks must match each guest platform')
                t['connection'] = binding
                t['credential_name'] = credential['name']
                t['credential_revision'] = credential['updated']
            plan['targets'].append(t)
        ident = uuid.uuid4().hex
        with self.db() as con:
            con.execute('DELETE FROM plans WHERE created < ?', (time.time()-86400,))
            con.execute('INSERT INTO plans VALUES (?,?,?)', (ident,time.time(),self.encode(plan)))
        return dict(id=ident, **self.summary(plan))

    @staticmethod
    def identity(config):
        # SMBIOS UUID and disk volume identity protect against reusing a deleted VM ID.
        value = {k:v for k,v in config.items() if k in ('smbios1','vmgenid','name') or re.fullmatch(r'(scsi|sata|virtio|ide)\d+',k)}
        return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

    @staticmethod
    def summary(plan):
        return dict(operation=plan['operation'], targets=[dict({k:t[k] for k in ('node','vmid','name')},
                    **({'guest_host':t['connection']['host'],'port':t['connection']['port'],'platform':t['connection']['platform'],
                        'credential_name':t.get('credential_name',''),'verify_tls':t['connection']['verify_tls']} if 'connection' in t else {})) for t in plan['targets']],
                    tasks=[{'name': t['item']['name'], 'params': t['params']} for t in plan['tasks']],
                    snapshot_before=plan['snapshot_before'], dangerous=plan['dangerous'],
                    settings={k:plan[k] for k in ('cores','memory','backup_storage','snapshot') if k in plan},
                    notes=['CPU/RAM changes require stopped guests and apply on next start.', 'Snapshots are retained; restoration is a separate reviewed action.',
                           'Guest scripts are trusted administrator code. Script output is withheld from logs.', 'Failures are reported per VM; no automatic rollback or force shutdown.'])

    def load_plan(self, ident):
        with self.db() as con: row = con.execute('SELECT * FROM plans WHERE id=?', (ident,)).fetchone()
        if not row or row['created'] < time.time()-1800: raise ValueError('Review plan expired; review again')
        return self.decode(row['value'])

    def enqueue(self, plan, job_id):
        payload = {'action': 'manage', 'plan': plan}
        with self.db() as con:
            con.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?)', (job_id,'manage','queued',time.time(),time.time(),self.encode(payload),'Queued reviewed '+plan['operation']+' operation.'))
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
                active = con.execute("SELECT id FROM jobs WHERE id=? AND status IN ('queued','running')",(s.get('last_job'),)).fetchone()
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

    def execute(self, plan, log):
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
                op = plan['operation']
                def task(path, **args): p.wait(t['node'],p.call('POST',prefix+path,**args),log)
                if op in ('tasks','readiness'):
                    b = t['connection']; cred = self.get('credentials',b['credential_id'])
                    if cred['updated'] != t['credential_revision']: raise ValueError('Credential changed since review; review again')
                    if resource.get('status')!='running': raise ValueError('Guest is not running')
                    resolved = endpoint_addresses(b)
                    reported = set()
                    try:
                        interfaces = p.call('GET',prefix+'/agent/network-get-interfaces')
                        reported = {ipaddress.ip_address(a['ip-address']) for i in interfaces.get('result',[]) for a in i.get('ip-addresses',[])}
                        reported = {ip for ip in reported if not ip.is_loopback and not ip.is_link_local}
                        log(label+' guest agent available; reported IPs: '+(', '.join(sorted(map(str,reported))) or 'none'))
                    except Exception: log(label+' guest agent unavailable; checking configured address instead.')
                    if reported and not resolved & reported:
                        raise ValueError('Configured endpoint does not match guest-agent IP addresses; check VM connection settings')
                    # No port scanning or discovery: only the configured guest endpoint.
                    with socket.create_connection((b['host'],b['port']),timeout=10): pass
                    log(label+' configured transport port reachable.')
                    run_guest(b,cred,[],log,readiness=True)
                    if op=='tasks':
                        if plan['snapshot_before']:
                            snap = 'proxmate-'+uuid.uuid4().hex[:16]
                            task('/snapshot',snapname=snap,description='Proxmate pre-task snapshot',vmstate=0)
                            log(label+' retained pre-task snapshot: '+snap)
                        for selection in plan['tasks']:
                            log(label+' running: '+selection['item']['name'])
                            run_guest(b,cred,compile_task(selection['item'],selection['params']),log)
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

    @app.get('/api/manage/<kind>')
    def list_records(kind):
        if kind not in KINDS: raise ValueError('Unknown record type')
        values = [manager.public(kind,r) for r in manager.all(kind)]
        if kind=='tasks': values = BUILTINS + values
        if kind=='schedules':
            with db() as con:
                for value in values:
                    row = con.execute('SELECT status,log FROM jobs WHERE id=?',(value.get('last_job'),)).fetchone()
                    value['last_status'] = row['status'] if row else None
        return jsonify(values)

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
