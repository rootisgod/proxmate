"""Author portable labs, export frozen guided builds, and maintain the local pack shelf."""
import base64
import binascii
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import time

from flask import jsonify, request, send_file
import yaml

import diagnostics
import guest_tasks
import lab_pack
import management
import operations


def portable_machine(recipe):
    """Project only operator choices. Private build configs also contain passwords and keys."""
    settings = recipe['settings']
    os_name, kind = settings['os'], settings['source_kind']
    portable = {k: settings[k] for k in (
        'purpose', 'cores', 'memory', 'disk', 'profile', 'username', 'locale', 'timezone',
        'rdp', 'manual_updates', 'ubuntu_firewall', 'ubuntu_updates', 'ubuntu_install_source',
        'windows_settings', 'data_disks', 'clone_mode') if k in settings}
    source = {'kind': kind}
    if kind == 'iso':
        source['hint'] = PurePosixPath(settings['source'].removeprefix('library:')).name
        if os_name == 'windows':
            source['edition'] = recipe['edition']
    elif kind == 'template':
        source['hint'] = recipe['edition']
        portable.pop('disk', None)  # The destination template supplies its own disk size.
    if os_name != 'windows':
        if recipe.get('password_login'):
            portable['password_login'] = True
        if recipe.get('needs_key_source') or settings.get('github_username') or settings.get('ssh_public_key'):
            portable['key_login'] = True

    return dict(os=os_name, source=source, settings=portable, tasks=[])


def public_inputs(item, values):
    # Older scripts could declare a credential-like variable as ordinary text.
    return {k: v for k, v in guest_tasks.public_params({'item': item, 'params': values}).items()
            if k in item.get('parameters', {}) and not lab_pack.SECRET_FIELD.search(k)}


def original_tasks(plan, recipe):
    # The executor adds setup steps that can contain build-specific credentials.
    # Index only the original choices, consuming a frozen occurrence for each one.
    remaining = list(plan.get('tasks', []))
    selected = []
    for choice in recipe['settings'].get('tasks', []):
        for index, step in enumerate(remaining):
            item = step['item']
            if item['id'] == choice['id'] and public_inputs(item, step['params']) == public_inputs(item, choice['params']):
                selected.append(remaining.pop(index))
                break
        else:
            raise ValueError('The original reviewed script is unavailable. Review the build again to export it.')
    return selected


def add_script(path, body, meta, files, metadata):
    if path in files:
        if files[path] != body or metadata[path] != meta:
            raise ValueError('Conflicting embedded script files.')
        return
    if len(files) >= lab_pack.MAX_FILES:
        raise ValueError('A pack supports up to 32 script files.')
    files[path], metadata[path] = body, meta


def portable_task(item, params, metadata, files):
    if item['id'] in lab_pack.PUBLISHED_V1:
        task = {'run': item['id'], 'version': 1}
        for key, spec in lab_pack.BUILTIN_V1[item['id']].get('input_types', {}).items():
            if spec['type'] == 'password':
                params = {**params, key: {'prompt': key}}
    else:
        extension = {'ansible': 'yml', 'shell': 'sh', 'powershell': 'ps1'}.get(item.get('kind'))
        if not extension or not isinstance(item.get('body'), str):
            raise ValueError('This build includes a task without a portable script body.')
        types = item.get('input_types', {})
        if any(lab_pack.SECRET_FIELD.search(k) or types.get(k, {}).get('type') == 'password'
               for k in item.get('parameters', {})):
            raise ValueError('Custom pack scripts support named, non-secret inputs only.')
        meta = {k: item[k] for k in ('name', 'platform', 'kind', 'parameters', 'defaults', 'input_types') if k in item}
        content = json.dumps([meta, item['body']], sort_keys=True).encode()
        digest = hashlib.sha256(content).hexdigest()[:16]
        slug = re.sub(r'[^a-z0-9]+', '-', item['name'].lower()).strip('-')[:32] or 'script'
        path = f'tasks/{slug}-{digest}.{extension}'
        add_script(path, item['body'], meta, files, metadata)
        task = {'file': path}
    if params:
        task['with'] = params
    return task


def snapshot(plan, recipe):
    machine = portable_machine(recipe)
    metadata, files = {}, {}
    for step in original_tasks(plan, recipe):
        item = step['item']
        machine['tasks'].append(portable_task(item, public_inputs(item, step['params']), metadata, files))
    return dict(machine=machine, metadata=metadata, files=files)


def job_payload(manager, job_id):
    management.word(job_id, 'job ID')
    with manager.db() as con:
        row = con.execute('SELECT payload FROM jobs WHERE id=? AND action=?', (job_id, 'simple')).fetchone()
    if row:
        payload = manager.decode(row['payload'])
        if payload.get('server_id') == operations.connection_provider.current()['id']:
            return payload
    raise ValueError('Choose a guided build job on the selected server.')


def editable_build(manager, job_id):
    plan = job_payload(manager, job_id).get('plan')
    if not isinstance(plan, dict) or not isinstance(plan.get('recipe'), dict):
        raise ValueError('This historical build lacks its original guided choices.')
    recipe = plan['recipe']
    try:
        return portable_machine(recipe), original_tasks(plan, recipe)
    except (KeyError, TypeError, AttributeError):
        raise ValueError('The original guided choices for this historical build are incomplete.') from None


def editable_source(manager, job_id):
    """Metadata for editing a captured build. Only the server may retrieve each indexed body."""
    machine, originals = editable_build(manager, job_id)
    lab_pack.validate_machine_settings(machine)
    return dict(machine, tasks=editable_steps(originals), base_job_id=job_id)


def editable_steps(originals):
    tasks = []
    for step in originals:
        item = step['item']
        types = dict(item.get('input_types', {}))
        parameters = item.get('parameters', {})
        for key in parameters:
            if lab_pack.SECRET_FIELD.search(key) or types.get(key, {}).get('type') == 'password':
                types[key] = {'type': 'password'}
        tasks.append(dict(name=item.get('name') or item['id'], parameters=parameters,
                                     defaults=public_inputs(item, item.get('defaults', {})),
                                     input_types=types, params=public_inputs(item, step['params'])))
    return tasks


def local_pack(manager, ident):
    record = manager.get('lab-packs', management.word(ident, 'pack ID'))
    if record['server_id'] != operations.connection_provider.current()['id']:
        raise ValueError('Choose a pack imported on the selected server.')
    return record


def saved_source(manager, ident):
    record = manager.get('guided-recipes', management.word(ident, 'saved build ID'))
    if not record.get('pack_snapshot'):
        raise ValueError(record.get('pack_export_error') or
                         'This saved build has no portable script snapshot. Export its original job or open the saved build and export a fresh review.')
    return record['pack_snapshot']


def authored_machine(manager, entry):
    allowed = {'key', 'os', 'source', 'settings', 'after', 'base_job_id', 'base_recipe_id', 'base_pack_id', 'base_role', 'tasks'}
    if set(entry) - allowed or not {'os', 'source'} <= set(entry):
        raise ValueError('Supply the operating system, portable source and supported machine settings.')
    bases = {'base_job_id', 'base_recipe_id', 'base_pack_id'}.intersection(entry)
    if len(bases) > 1 or ('base_pack_id' in entry) != ('base_role' in entry):
        raise ValueError('Choose one original source. A lab pack also needs its machine key.')
    originals, frozen = [], None
    if 'base_job_id' in entry:
        _, originals = editable_build(manager, entry['base_job_id'])
    if 'base_recipe_id' in entry:
        frozen = saved_source(manager, entry['base_recipe_id'])
    if 'base_pack_id' in entry:
        pack = local_pack(manager, entry['base_pack_id'])
        role = management.word(entry['base_role'], 'original machine key')
        if role not in pack['manifest']['machines']:
            raise ValueError('Choose a machine from the original lab pack.')
        frozen = dict(machine=pack['manifest']['machines'][role], files=pack['scripts'],
                      metadata=pack['manifest'].get('scripts', {}))
    if frozen:
        originals = lab_pack.portable_steps(frozen['machine'], frozen['metadata'], frozen['files'])
    machine = {k: entry[k] for k in ('os', 'source', 'settings') if k in entry}
    metadata, files = {}, {}
    machine['tasks'] = []
    for choice in entry.get('tasks', []):
        if (not isinstance(choice, dict) or set(choice) - {'id', 'original', 'params'} or
                ('id' in choice) == ('original' in choice)):
            raise ValueError('Choose one library task ID or original task index for each task.')
        if 'original' in choice:
            index = choice['original']
            if type(index) is not int or not 0 <= index < len(originals):
                raise ValueError('Choose an original task index from the selected guided build.')
            step = originals[index]
            item = step['item']
            params = public_inputs(item, step['params'])
        else:
            item = manager.task(management.word(choice['id'], 'library task ID'))
            params = public_inputs(item, item.get('defaults', {}))
        overrides = choice.get('params', {})
        if not isinstance(overrides, dict) or set(overrides) - set(item.get('parameters', {})):
            raise ValueError('Supply only the declared task inputs.')
        if set(overrides) != set(public_inputs(item, overrides)):
            raise ValueError('Leave secret inputs out of the lab. Enter fresh values at deployment.')
        if frozen and 'original' in choice:
            task = dict(frozen['machine']['tasks'][index])
            if overrides:
                task['with'] = {**task.get('with', {}), **overrides}
            if 'file' in task:
                path = task['file']
                add_script(path, frozen['files'][path], frozen['metadata'].get(path, {}), files, metadata)
        else:
            task = portable_task(item, {**params, **overrides}, metadata, files)
        machine['tasks'].append(task)
    return dict(machine=machine, metadata=metadata, files=files)


def capture(manager, entry):
    source_fields = {'plan_id', 'job_id', 'recipe_id'}.intersection(entry)
    if len(source_fields) != 1 or set(entry) - source_fields - {'key', 'machine_name', 'after'}:
        raise ValueError('For each machine choose one reviewed build, saved build or guided job.')
    field = next(iter(source_fields))
    ident = management.word(entry[field], 'build source ID')
    if field == 'recipe_id':
        return saved_source(manager, ident)
    if field == 'plan_id':
        plan = manager.load_plan(ident)
        if plan.get('operation') != 'simple-build':
            raise ValueError('Choose a guided build review for export.')
        names = [c['name'] for c in plan['configs']]
        requested = entry.get('machine_name', entry.get('key') if len(names) > 1 else names[0])
        if requested not in names:
            raise ValueError('Select a machine_name from this reviewed batch.')
    else:
        plan = job_payload(manager, ident)['plan']
    if not plan.get('recipe'):
        raise ValueError('This historical build lacks the original guided choices. Make a fresh review to export it.')
    return snapshot(plan, plan['recipe'])


def archive_response(data, name):
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:80] or 'lab'
    return send_file(io.BytesIO(data), mimetype='application/zip', as_attachment=True, download_name=slug+'.zip')


def assemble(manager, data, machine_source):
    if not isinstance(data, dict) or set(data) != {'name', 'machines'}:
        raise ValueError('Supply a lab name and its machines.')
    name = management.name(data['name'])
    entries = data['machines']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
        raise ValueError('Choose 1 to 20 machines for the pack.')
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Supply a machine key and settings for each machine.')
        tasks = entry.get('tasks', [])
        if not isinstance(tasks, list) or len(tasks) > 50:
            raise ValueError('Choose up to 50 ordered tasks per machine.')

    manifest = dict(version=1, name=name, machines={}, scripts={})
    files = {}
    for entry in entries:
        key = management.word(entry.get('key'), 'machine key')
        if key in manifest['machines']:
            raise ValueError('Give each machine a different key.')
        captured = machine_source(manager, entry)
        # Authored entries and SQLite snapshots may arrive with different mapping
        # insertion order. Machine and task lists keep their deliberate order.
        machine = json.loads(json.dumps(captured['machine'], sort_keys=True))
        if isinstance(machine.get('source'), dict) and machine['source'].get('kind') == 'existing':
            manifest['version'] = 2
        if 'after' in entry:
            machine['after'] = entry['after']
        manifest['machines'][key] = machine
        for path, body in captured['files'].items():
            meta = json.loads(json.dumps(captured['metadata'][path], sort_keys=True))
            add_script(path, body, meta, files, manifest['scripts'])
    return manifest, files, lab_pack.write_pack(manifest, files)


def inspected(record):
    manifest = record['manifest']
    machines = []
    for key in record['order']:
        machine = manifest['machines'][key]
        prompts = {}
        for task in machine.get('tasks', []):
            for input_name, value in task.get('with', {}).items():
                if isinstance(value, dict) and 'prompt' in value:
                    prompts[value['prompt']] = input_name.replace('_', ' ')
        steps = lab_pack.portable_steps(machine, manifest.get('scripts', {}), record['scripts'])
        machines.append(dict(key=key, **machine, editable_tasks=editable_steps(steps),
                             secret_inputs=[dict(key=k, label=v) for k,v in prompts.items()]))
    return dict(id=record['id'], name=manifest['name'], manifest=manifest, scripts=record['scripts'], machines=machines)


def save_pack(manager, manifest, files, raw):
    """Persist already validated contents and the matching original archive atomically."""
    server_id = operations.connection_provider.current()['id']
    ident = hashlib.sha256(server_id.encode() + b'\0' + raw).hexdigest()[:32]
    record = dict(id=ident, server_id=server_id, manifest=manifest, scripts=files,
                  archive=base64.b64encode(raw).decode(), order=lab_pack.ordered_vms(manifest))
    summary = dict(id=ident, server_id=server_id, name=manifest['name'],
                   machine_count=len(record['order']), created=time.time())
    with manager.db() as con:
        con.executemany('INSERT OR IGNORE INTO records VALUES (?,?,?)',
                        [('lab-packs', ident, manager.encode(record)),
                         ('lab-pack-index', ident, manager.encode(summary))])
    return inspected(record)


def register(app, manager):
    @app.get('/api/simple/recipes/<ident>/lab-source')
    def recipe_lab_source(ident):
        frozen = saved_source(manager, ident)
        originals = lab_pack.portable_steps(frozen['machine'], frozen['metadata'], frozen['files'])
        return jsonify(dict(frozen['machine'], tasks=editable_steps(originals), base_recipe_id=ident))

    @app.get('/api/simple/labs/packs')
    def lab_packs():
        server_id = operations.connection_provider.current()['id']
        summaries = sorted(manager.all('lab-pack-index'), key=lambda r: r['created'], reverse=True)
        return jsonify([r for r in summaries if r['server_id'] == server_id])

    @app.post('/api/simple/labs/packs')
    def lab_create_pack():
        manifest, files, archive = assemble(manager, request.get_json(), authored_machine)
        result = save_pack(manager, manifest, files, archive)
        diagnostics.event('lab.created', pack_id=result['id'], machines=len(manifest['machines']),
                          files=len(files), size_bytes=len(archive))
        return jsonify(result), 201

    @app.get('/api/simple/labs/packs/<ident>')
    def lab_inspection(ident):
        return jsonify(inspected(local_pack(manager, ident)))

    @app.get('/api/simple/labs/packs/<ident>/export')
    def lab_download(ident):
        record = local_pack(manager, ident)
        return archive_response(base64.b64decode(record['archive']), record['manifest']['name'])

    @app.get('/api/simple/labs/example')
    def lab_example():
        folder = Path(app.static_folder) / 'lab-example'
        manifest = yaml.safe_load((folder / 'lab.yaml').read_text(encoding='utf-8'))
        files = {'tasks/tools.yml': (folder / 'tasks/tools.yml').read_text(encoding='utf-8')}
        return archive_response(lab_pack.write_pack(manifest, files), manifest['name'])

    @app.post('/api/simple/labs/export')
    def lab_export():
        manifest, files, archive = assemble(manager, request.get_json(), capture)
        diagnostics.event('lab.exported', machines=len(manifest['machines']), files=len(files), size_bytes=len(archive))
        return archive_response(archive, manifest['name'])

    @app.post('/api/simple/labs/import')
    def lab_import():
        data = request.get_json()
        if not isinstance(data, dict) or set(data) != {'archive'} or not isinstance(data['archive'], str):
            raise ValueError('Supply a ZIP archive encoded as base64 in archive.')
        try:
            raw = base64.b64decode(data['archive'], validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('The archive must be a base64-encoded ZIP.') from None
        manifest, files = lab_pack.read_pack(raw)
        result = save_pack(manager, manifest, files, raw)
        diagnostics.event('lab.imported', pack_id=result['id'], machines=len(manifest['machines']),
                          files=len(files), size_bytes=len(raw))
        return jsonify(result)
