"""Reusable operator choices for guided builds. Execution secrets belong to one build."""
import uuid

from flask import jsonify, request

import management
import operations
import linux_access
import linux_install
import ubuntu_build


def specification(plan, cfg, choices=None):
    """Project a reviewed build onto the public inputs accepted by the guided planner."""
    windows = cfg['kind'] == 'windows'
    settings = {k: cfg[k] for k in ('node', 'storage', 'bridge', 'cores', 'memory', 'disk')}
    # Historical Ubuntu cloud builds were stored as linux-cloud.
    os_name = 'ubuntu' if cfg['kind']=='linux-cloud' else cfg['kind'].split('-',1)[0]
    settings.update(os=os_name, purpose=plan['purpose'], tasks=[])
    boot = cfg['bootstrap']
    settings['iso_storage'] = cfg.get('iso_storage') or boot.get('seed_storage')
    template = cfg.get('template')
    if template:
        settings.update(source_kind='template', template_id=template['source'], clone_mode=template['mode'])
    elif windows or cfg['kind'] in linux_install.ISO_KINDS:
        settings.update(source_kind='iso', source=plan['source'])
    else:
        settings['source_kind'] = 'cloud'
    needs_password = windows
    needs_keys = False
    if windows:
        win = cfg['windows']
        settings.update(locale=win['locale'], timezone=win['timezone'], rdp=win['rdp'],
                        management_cidr=boot['management_cidr'], scope_edited=True, windows_settings=[],
                        data_disks=[{k: v for k, v in d.items() if k in ('size', 'letter', 'label')}
                                    for d in cfg.get('data_disks', [])])
        if not template:
            settings.update(edition=win['image_index'], edition_identity=plan['media_identity'],
                            agent_iso=boot['agent_iso'], agent_manual=True)
    else:
        settings.update(profile=cfg['profile'], username=cfg['username'])
        if choices is None:
            # Old jobs retained resolved keys but lost their source. Ask again so a
            # key later removed from GitHub cannot silently grant a new login.
            needs_keys = len(cfg['ssh_key'].splitlines()) > 1
            key_sources = {}
        else:
            key_sources = {k: choices[k].strip() for k in ('github_username','ssh_public_key') if choices.get(k)}
            if key_sources.get('ssh_public_key'):
                key_sources['ssh_public_key']=linux_access.public_key(key_sources['ssh_public_key'])
        settings.update(key_sources)
    if choices is not None:
        settings['tasks'] = choices['tasks']
        fields = ('windows_settings','manual_updates') if windows else ('ubuntu_firewall','ubuntu_updates')
        settings.update({k:choices[k] for k in fields if k in choices})
        needs_password = windows or bool(linux_access.requested_password(choices,os_name))
    else:
        needs_password |= restore_historical_tasks(settings, plan['tasks'])
    return dict(settings=settings, password_login=needs_password, needs_key_source=needs_keys, edition=plan.get('edition', ''),
                template_identity=template['identity'] if template else None,
                media_identity=plan.get('media_identity'))


def restore_historical_tasks(settings, selections):
    needs_password = False
    for selection in selections:
        item = selection['item']
        ident = item['id']
        if ident in ('debian-password','ubuntu-password'):
            needs_password = True
        elif item.get('kind') == 'ubuntu-setup':
            policies = {'ubuntu-firewall': ('ubuntu_firewall', ubuntu_build.FIREWALL),
                        'ubuntu-updates': ('ubuntu_updates', ubuntu_build.UPDATES)}
            field, choices = policies[ident]
            settings[field] = next(k for k, label in choices.items() if label == item['name'])
        elif item.get('kind') == 'windows-setup':
            settings['windows_settings'].append(ident.removeprefix('setup-'))
        else:
            settings['tasks'].append({'id': ident, 'params': selection['params']})
    return needs_password


def from_job(payload):
    if payload.get('action') != 'simple':
        return None
    plan = payload['plan']
    if 'recipe' in plan:
        return plan['recipe']
    try:
        return specification(plan, plan['config'])
    except (KeyError, TypeError, StopIteration):
        return None  # This historical payload lacks the original guided settings.


def description(recipe):
    settings = recipe['settings']
    return dict(os=settings['os'], purpose=settings['purpose'], source_kind=settings['source_kind'],
                edition=recipe['edition'], password_login=recipe['password_login'],needs_key_source=recipe['needs_key_source'])


class GuidedRecipes:
    def __init__(self, manager):
        self.manager = manager

    def lookup(self, source):
        if not isinstance(source, dict) or len(source) != 1:
            raise ValueError('Choose one saved build or guided build job.')
        management.word(next(iter(source.values())), 'saved build or job ID')
        if 'recipe_id' in source:
            return self.manager.get('guided-recipes', source['recipe_id'])['recipe']
        if 'job_id' in source:
            with self.manager.db() as con:
                row = con.execute('SELECT payload FROM jobs WHERE id=?', (source['job_id'],)).fetchone()
            payload = self.manager.decode(row['payload']) if row else {}
            if payload.get('server_id') == operations.connection_provider.current()['id']:
                recipe = from_job(payload)
                if recipe:
                    return recipe
        raise ValueError('Reusable settings are unavailable for this build on the selected server.')

    def expand(self, draft):
        recipe = self.lookup(draft['reuse'])
        allowed = {'name', 'count', 'password', 'ubuntu_password', 'linux_password', 'generalized'}
        if recipe['needs_key_source']:
            allowed.update(('github_username','ssh_public_key'))
            if not any(isinstance(draft.get(k),str) and draft[k].strip() for k in ('github_username','ssh_public_key')):
                raise ValueError('Supply the GitHub username or workstation public key for this historical build.')
        if set(draft) - allowed - {'reuse'}:
            raise ValueError('Supply a new name, quantity and login for this saved build.')
        settings = dict(recipe['settings'], **{k: v for k, v in draft.items() if k in allowed})
        password = settings.get('password') if settings['os']=='windows' else linux_access.requested_password(settings,settings['os'])
        if recipe['password_login'] and not password:
            raise ValueError('Enter a fresh guest password for this build.')
        return settings, recipe

    def register(self, app):
        @app.get('/api/simple/recipes')
        def guided_recipe_list():
            return jsonify([dict(id=r['id'], name=r['name'], **description(r['recipe']))
                            for r in sorted(self.manager.all('guided-recipes'), key=lambda r:r['name'].casefold())])

        @app.post('/api/simple/recipes')
        def guided_recipe_save():
            data = request.get_json()
            if not isinstance(data,dict) or ('plan_id' in data)==('job_id' in data):
                raise ValueError('Choose one build review or job to save.')
            name = management.name(data.get('name'))
            if 'plan_id' in data:
                management.word(data['plan_id'], 'plan ID')
                plan = self.manager.load_plan(data['plan_id'])
                if plan.get('operation') != 'simple-build' or not plan.get('recipe'):
                    raise ValueError('Choose a reviewed guided build.')
                recipe = plan['recipe']
            else:
                recipe = self.lookup({'job_id': data.get('job_id')})
            ident = uuid.uuid4().hex
            self.manager.put('guided-recipes', dict(id=ident, name=name, recipe=recipe))
            return jsonify(id=ident, name=name, **description(recipe)), 201

        @app.post('/api/simple/recipes/<ident>/delete')
        def guided_recipe_delete(ident):
            self.manager.get('guided-recipes', ident)
            with self.manager.db() as con:
                con.execute("DELETE FROM records WHERE kind='guided-recipes' AND id=?", (ident,))
            return jsonify(ok=True)
