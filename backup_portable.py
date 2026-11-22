"""Portable configuration contains definitions only. All machine bindings belong to deployment."""
import base64
import hashlib
import json

import lab_exchange
import lab_pack


SCRIPT_FIELDS = ('name', 'platform', 'kind', 'body', 'parameters', 'defaults', 'input_types')


def script_definition(task):
    draft = {k: task[k] for k in SCRIPT_FIELDS if k in task}
    draft['parameters'] = task.get('parameters', {})
    draft['defaults'] = lab_exchange.public_inputs(task, task.get('defaults', {}))
    draft['input_types'] = {k: spec for k, spec in task.get('input_types', {}).items()
                            if not lab_pack.SECRET_FIELD.search(k) and spec.get('type') != 'password'}
    return draft


def records(con, manager, kind):
    return (manager.decode(row[0]) for row in con.execute(
        'SELECT value FROM records WHERE kind=? ORDER BY id', (kind,)))


def export(con, manager, server_id):
    entries, omitted = [], []
    for task in records(con, manager, 'tasks'):
        entries.append(dict(kind='script', definition=script_definition(task)))
    for pack in records(con, manager, 'lab-packs'):
        if pack['server_id'] == server_id:
            entries.append(dict(kind='lab', archive=pack['archive']))
    for saved in records(con, manager, 'guided-recipes'):
        snap = saved.get('pack_snapshot')
        if not snap:
            omitted.append(saved['name'])
            continue
        manifest = dict(version=1, name=saved['name'], machines={'machine': snap['machine']},
                        scripts=snap['metadata'])
        try:
            raw = lab_pack.write_pack(manifest, snap['files'])
        except ValueError:
            omitted.append(saved['name'])
            continue
        entries.append(dict(kind='saved build', archive=base64.b64encode(raw).decode('ascii')))
    return dict(entries=entries, omitted=omitted)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_definitions(manager, content):
    """Run file and script validation before taking a destination write lock."""
    entries = content['entries']
    omitted = content['omitted']
    if not isinstance(entries, list) or not isinstance(omitted, list) or not all(isinstance(n, str) for n in omitted):
        raise ValueError('Invalid portable configuration.')
    definitions = []
    for entry in entries:
        kind = entry['kind']
        if kind == 'script':
            draft = entry['definition']
            if not isinstance(draft, dict) or set(draft) - set(SCRIPT_FIELDS) or script_definition(draft) != draft:
                raise ValueError('Invalid portable script definition.')
            script = manager.prepare('tasks', draft)
            name = script['name']
            key = ('script', script['platform'], name.casefold())
            identity = digest(script_definition(script))
            value = script
        elif kind in ('lab', 'saved build'):
            raw = base64.b64decode(entry['archive'], validate=True)
            manifest, files = lab_pack.read_pack(raw)
            name = manifest['name']
            key = ('lab', name.casefold())
            identity = lab_pack.definition_digest(manifest, files)
            value = (manifest, files, raw)
        else:
            raise ValueError('Unknown portable item type.')
        definitions.append((kind, name, key, identity, value))
    return definitions


def plan(con, manager, server_id, definitions):
    """Compare validated definitions against a consistent destination snapshot."""
    names = {}
    for task in records(con, manager, 'tasks'):
        key = ('script', task['platform'], task['name'].casefold())
        names.setdefault(key, set()).add(digest(script_definition(task)))
    for pack in records(con, manager, 'lab-packs'):
        if pack['server_id'] == server_id:
            key = ('lab', pack['manifest']['name'].casefold())
            identity = lab_pack.definition_digest(pack['manifest'], pack['scripts'], stored_order=pack.get('order'))
            names.setdefault(key, set()).add(identity)
    items, pending = [], {}
    seen = set()
    for index, (kind, name, key, identity, value) in enumerate(definitions):
        ident = str(index)
        known = names.get(key, set())
        if identity in known:
            status = 'already present'
        elif known:
            status = 'name conflict'
        elif kind != 'script' and not server_id:
            status = 'connect a server'
        elif key in seen:
            status = 'repeated name in backup'
        else:
            status = 'new'
            if kind == 'script':
                rows = [('tasks', value)]
            else:
                pack, summary = lab_exchange.pack_records(server_id, *value)
                rows = [('lab-packs', pack), ('lab-pack-index', summary)]
            pending[ident] = rows
            seen.add(key)
        items.append(dict(id=ident, kind=kind, name=name, status=status))
    return items, pending
