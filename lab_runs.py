"""Review a portable pack against one server and commit its guided builds atomically."""
import time
import uuid

from flask import jsonify, request

import diagnostics
import guest_review
import lab_pack
import management
import operations


def fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError('Supply only the expected fields for ' + label + '.')
    return value


def machine_draft(key, machine, binding, login):
    os_name = machine['os']
    source_kind = machine['source']['kind']
    allowed = {'name', 'node', 'storage', 'bridge', 'iso_storage'}
    if source_kind == 'template':
        allowed.add('template_id')
        if os_name == 'windows':
            allowed.add('generalized')
    if source_kind == 'iso':
        allowed.add('source')
        if os_name == 'windows':
            allowed.update(('edition', 'edition_identity', 'agent_iso'))
    if os_name == 'windows':
        allowed.add('management_cidr')
    fields(binding, allowed, key + ' mapping')
    fields(login, {'inputs', 'password', 'build_credential_id'} if os_name == 'windows' else
           {'inputs', 'linux_password', 'ssh_public_key', 'github_username', 'build_credential_id'}, key + ' login')
    for field, value in binding.items():
        if field == 'generalized':
            if not isinstance(value, bool):
                raise ValueError('Confirm whether the Windows template was generalized.')
        elif field in ('edition', 'template_id'):
            if type(value) not in (str, int):
                raise ValueError('Choose a mapped edition or template.')
        elif not isinstance(value, str):
            raise ValueError('Supply text for ' + field + '.')
    if any(not isinstance(v, str) for k, v in login.items() if k != 'inputs'):
        raise ValueError('Enter guest login fields as text.')
    if source_kind == 'iso' and not binding.get('source'):
        raise ValueError('Map the installer ISO for ' + key + ' to media on this server.')
    if source_kind == 'template' and not binding.get('template_id'):
        raise ValueError('Map the source template for ' + key + ' on this server.')
    settings = dict(machine.get('settings', {}))
    requires_password = settings.pop('password_login', False)
    requires_key = settings.pop('key_login', False)
    has_login = login.get('password') or login.get('linux_password') or (os_name == 'windows' and login.get('build_credential_id'))
    if requires_password and not has_login:
        raise ValueError('Enter a fresh guest password for ' + key + '.')
    if requires_key and not any(login.get(k, '').strip() for k in ('ssh_public_key', 'github_username')):
        raise ValueError('Supply a workstation public key or GitHub username for ' + key + '.')
    return {**settings, 'os': os_name, 'name': key, 'source_kind': source_kind, **binding,
            'agent_manual': bool(binding.get('agent_iso')), 'scope_edited': bool(binding.get('management_cidr')),
            **{k: v for k, v in login.items() if k != 'inputs'}}


class LabRuns:
    def __init__(self, manager, simple):
        self.manager = manager
        self.simple = simple

    def existing_operation(self, key, binding, login, selections):
        fields(binding, ('node','vmid'), key+' target')
        target=management.target(binding)
        fields(login, ('inputs','credential_id','credential'), key+' login')
        if not selections:
            raise ValueError('Select at least one guest task for an existing machine.')
        if 'credential' in login and 'credential_id' in login:
            raise ValueError('Choose one saved or new login for the target.')
        if 'credential_id' in login:
            management.word(login['credential_id'],'saved guest login')
        if 'credential' in login:
            fields(login['credential'], ('platform','username','password','private_key','become_password','ca_pem'),
                   'guest credential')
        return guest_review.prepare(self.manager, [target], selections, login, lab_existing=True)

    def review(self, data):
        fields(data, ('pack_id', 'bindings', 'secrets'), 'lab review')
        pack_id = management.word(data.get('pack_id'), 'imported lab ID')
        pack = self.manager.get('lab-packs', pack_id)
        server_id = operations.connection_provider.current()['id']
        if pack['server_id'] != server_id:
            raise ValueError('Import the pack for the selected server.')
        manifest = pack['manifest']
        machines = manifest['machines']
        bindings = fields(data.get('bindings', {}), machines, 'machine mappings')
        secrets = fields(data.get('secrets', {}), machines, 'machine logins')
        inventory = None
        reviews, summaries, configs = [], [], []
        existing_ids = set()
        for key in lab_pack.ordered_vms(manifest, stored_order=pack.get('order')):
            machine = machines[key]
            login = secrets.get(key, {})
            try:
                if not isinstance(login, dict): raise ValueError('Supply guest login fields as an object.')
                selections = lab_pack.resolve_tasks(machine, manifest, pack['scripts'], login.get('inputs', {}))
                binding = bindings.get(key,{})
                after = machine.get('after', [])
                if machine['source']['kind']=='existing':
                    mode='existing'
                    work=self.existing_operation(key,binding,login,selections)
                    summary=self.manager.summary(work['operation'])
                    target,=work['operation']['targets']
                    if target['vmid'] in existing_ids: raise ValueError('Map each existing machine to a different VM ID.')
                    existing_ids.add(target['vmid'])
                    reviews.append(dict(key=key, after=after, mode=mode, **work))
                else:
                    mode='create'
                    draft = machine_draft(key, machine, binding, login)
                    if inventory is None: inventory=operations.inventory(include_backups=False)
                    plan, summary = self.simple.review_build(draft,selections=selections,inventory=inventory,other_configs=configs)
                    edition = machine['source'].get('edition')
                    if edition and edition != summary['edition']:
                        raise ValueError('The selected Windows edition must match the pack edition: ' + edition)
                    cfg, = plan['configs']
                    configs.append(cfg)
                    reviews.append(dict(key=key, after=after, mode=mode, build=plan))
                    summary['source'] = dict(kind=machine['source']['kind'],
                                             value=cfg.get('template', {}).get('source', plan['source']))
                    summary['iso_storage'] = cfg.get('iso_storage') or cfg['bootstrap'].get('seed_storage')
                summaries.append(dict(key=key, after=after, mode=mode, plan=summary))
            except ValueError as exc:
                raise ValueError(key + ': ' + str(exc)) from None
        if any(cfg['vmid'] in existing_ids for cfg in configs):
            raise ValueError('An existing target occupied a proposed new VM ID. Review the lab again.')
        ident = uuid.uuid4().hex
        dangerous = any(row['plan']['dangerous'] for row in summaries)
        plan = dict(operation='lab-build', name=manifest['name'], server_id=server_id, pack_id=pack_id,
                    machines=reviews, dangerous=dangerous, existing=bool(existing_ids))
        with self.manager.db() as con:
            con.execute('INSERT INTO plans VALUES (?,?,?)', (ident, time.time(), self.manager.encode(plan)))
        return dict(id=ident, name=manifest['name'], machines=summaries, dangerous=dangerous, existing=bool(existing_ids))

    def confirm(self, data):
        fields(data, ('plan_id', 'confirmed', 'risk_confirmed','existing_confirmed'), 'lab confirmation')
        ident = management.word(data.get('plan_id'), 'reviewed lab ID')
        if data.get('confirmed') is not True:
            raise ValueError('Confirm the reviewed machines, source mappings, software and licensing.')
        server_id = operations.connection_provider.current()['id']
        with self.manager.db() as con:
            con.execute('BEGIN IMMEDIATE')
            saved = con.execute("SELECT value FROM records WHERE kind='lab-runs' AND id=?", (ident,)).fetchone()
            plan = self.manager.decode(saved['value']) if saved else self.manager.load_plan(ident)
            if plan.get('server_id') != server_id or (not saved and plan.get('operation') != 'lab-build'):
                raise ValueError('Review the lab on the selected server before deploying it.')
            if plan['dangerous'] and data.get('risk_confirmed') is not True:
                raise ValueError('Confirm the reviewed sensitive settings or trusted scripts.')
            if plan.get('existing') and data.get('existing_confirmed') is not True:
                raise ValueError('Confirm that the listed tasks will change the selected existing machines.')
            if saved:
                return self.status(con, plan)
            jobs = {}
            machines = []
            for row in plan['machines']:
                key = row['key']
                mode = row.get('mode','create')
                if mode=='existing':
                    job=guest_review.queue_lab_role(con,self.manager,row,server_id,ident)
                    cfg, = row['operation']['targets']
                else:
                    build=row['build']
                    job, = self.simple.queue_build(con, build, ident + ':' + key, server_id, lab_run_id=ident)
                    cfg, = build['configs']
                jobs[key] = job['id']
                con.execute('INSERT INTO lab_jobs VALUES (?,?)', (job['id'], ident))
                con.executemany('INSERT INTO job_dependencies VALUES (?,?)',
                                [(job['id'], jobs[p]) for p in row['after']])
                machines.append(dict(key=key, mode=mode, after=row['after'], name=cfg['name'], node=cfg['node'],
                                     vmid=cfg['vmid'], job_id=job['id'], url=job['url']))
            run = dict(id=ident, server_id=server_id, pack_id=plan['pack_id'], name=plan['name'],
                       dangerous=plan['dangerous'], existing=plan.get('existing', False), created=time.time(), machines=machines)
            con.execute("INSERT INTO records VALUES ('lab-runs',?,?)", (ident, self.manager.encode(run)))
            result = self.status(con, run)
        for row in machines:
            diagnostics.event('job.queued', job_id=row['job_id'], plan_id=ident,
                              action='manage' if row['mode']=='existing' else 'simple')
        return result

    @staticmethod
    def status(con, run):
        machines = []
        states = {}
        jobs = {r['id']:r for r in con.execute("""SELECT j.id,j.status,
                    EXISTS(SELECT 1 FROM records WHERE kind='builds' AND id=j.id) AS owned
                FROM lab_jobs l JOIN jobs j ON l.job_id=j.id WHERE l.run_id=?""", (run['id'],))}
        for row in run['machines']:
            job = jobs[row['job_id']]
            status = job['status']
            if status == 'queued' and any(states[p] != 'succeeded' for p in row['after']):
                status = 'pending'
            states[row['key']] = status
            mode=row.get('mode','create')
            machines.append(dict(row, mode=mode, status=status, owned=mode=='create' and bool(job['owned'])))
        observed = set(states.values())
        status = 'succeeded' if observed == {'succeeded'} else 'failed'
        for candidate in ('running', 'waiting', 'queued', 'pending', 'failed', 'interrupted'):
            if candidate in observed:
                status = 'queued' if candidate == 'pending' else candidate
                break
        return dict(run, machines=machines, status=status)

    def history(self, ident=None):
        server_id = operations.connection_provider.current()['id']
        with self.manager.db() as con:
            con.execute('BEGIN')
            query = "SELECT value FROM records WHERE kind='lab-runs'"
            if ident is not None:
                rows = con.execute(query+' AND id=?', (ident,))
            else:
                rows = con.execute(query)
            runs = [self.manager.decode(r['value']) for r in rows]
            return [self.status(con, r) for r in sorted(runs, key=lambda r: r['created'], reverse=True)
                    if r['server_id'] == server_id]


def register(app, manager, simple):
    labs = LabRuns(manager, simple)

    @app.post('/api/simple/labs/plan')
    def lab_plan():
        return jsonify(labs.review(request.get_json()))

    @app.post('/api/simple/labs/run')
    def lab_confirm():
        return jsonify(labs.confirm(request.get_json())), 202

    @app.get('/api/simple/labs/runs')
    def lab_history():
        return jsonify(labs.history())

    @app.get('/api/simple/labs/runs/<ident>')
    def lab_run(ident):
        result = labs.history(ident)
        if not result:
            return jsonify(error='Lab deployment not found on the selected server.'), 404
        return jsonify(result[0])
