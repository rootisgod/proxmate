import base64
import io
import json
import zipfile

import dashboard_backup
from test_api import service
from test_lab_runs import import_pack, linux
from test_simple_flow import simple_lab, settings


PASS = 'a long private passphrase'
ADMIN = 'test-admin-password'
PREFIX = '/api/dashboard-backup/'


def export(client, headers, mode):
    r = client.post(PREFIX+'export', headers=headers, json=dict(mode=mode, passphrase=PASS, admin_password=ADMIN))
    assert r.status_code == 200, r.json
    assert r.mimetype == 'application/octet-stream' and r.headers['Cache-Control'] == 'no-store'
    return dict(archive=base64.b64encode(r.data).decode(), passphrase=PASS)


def preview(client, headers, upload):
    response = client.post(PREFIX+'preview', headers=headers, json=upload)
    assert response.status_code == 200, response.json
    return response.json


def destination(tmp_path, monkeypatch):
    directory = tmp_path/'destination'
    directory.mkdir()
    app, client = service.__wrapped__(directory, monkeypatch)
    return app, client, {'Authorization': 'Bearer '+app.API_TOKEN}


def store_credential(app):
    return app.manager.save('credentials', dict(name='Backup proof', platform='windows',
                            username='Administrator', password='Guest-test-restore-2991'))


def test_full_recovery_on_new_keys_with_disabled_automation(service,tmp_path,monkeypatch):
    app, source = service
    h = {'Authorization':'Bearer '+app.API_TOKEN}
    cred = store_credential(app)
    old_key, old_token, source_id = app.app.secret_key, app.API_TOKEN, app.server.current()['id']
    # Real nested archive structure created when switching server connections.
    encoded = app.encode_payload(dict(id='old', name='Former group'))
    archive = dict(server=app.server.read(), records=[dict(id='old',kind='groups',value=encoded)],plans=[],lifecycle_plans=[])
    with app.db() as con:
        con.execute('INSERT INTO server_archives VALUES (?,?,?)', ('old', 1, app.encode_payload(archive)))
        con.execute('INSERT INTO build_credential_defaults VALUES (?,?)', ('windows',cred['id']))
        con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)', ('pending','start','queued',1,1,app.encode_payload({'action':'start','vmid':101}),'Queued'))
        con.execute('INSERT INTO plans VALUES (?,?,?)',('approved',1,app.encode_payload({'operation':'tasks'})))
        for kind in ('schedules','backup-policies'):
            con.execute('INSERT INTO records VALUES (?,?,?)',(kind,'automation',app.encode_payload(dict(id='automation',enabled=True,next_run=9999999999))))
    upload = export(source,h,'full')
    assert b'Guest-test' not in base64.b64decode(upload['archive'])
    app, target, headers = destination(tmp_path, monkeypatch)
    assert app.app.secret_key != old_key and app.API_TOKEN != old_token
    other = store_credential(app)
    before = preview(target,headers,upload)
    assert before['counts']['unfinished jobs']==1 and 'Guest-test' not in json.dumps(before)
    args = dict(**upload, review=before['review'], confirmed=True, admin_password=ADMIN)
    denied=target.post(PREFIX+'restore',headers=headers,json={**args,'admin_password':'wrong'})
    assert denied.status_code==400 and app.manager.find('credentials',other['id'])
    result=target.post(PREFIX+'restore',headers=headers,json=args)
    assert result.status_code==200,result.json
    assert app.server.read()['id']==source_id and not app.server.read()['connected']
    assert app.manager.get('credentials',cred['id'])['password']=='Guest-test-restore-2991'
    assert not app.manager.find('credentials',other['id'])
    assert not app.manager.get('schedules','automation')['enabled'] and not app.manager.get('backup-policies','automation')['enabled']
    with app.db() as con:
        assert con.execute('SELECT credential_id FROM build_credential_defaults').fetchone()[0]==cred['id']
        assert con.execute('SELECT status FROM jobs').fetchone()[0]=='interrupted'
        assert not con.execute('SELECT * FROM plans').fetchall()
        historical=app.decode_payload(con.execute('SELECT value FROM server_archives').fetchone()[0])
        assert app.decode_payload(historical['records'][0]['value'])['name']=='Former group'
    assert target.get('/api/jobs',headers={'Authorization':'Bearer '+old_token}).status_code==401
    assert app.run_next_job() is False


def test_portable_selective_merge_two_servers(simple_lab,tmp_path,monkeypatch):
    app,source,h,_ = simple_lab
    source_id=app.server.current()['id']
    store_credential(app)
    script=app.manager.save('tasks',dict(name='System report',platform='linux',kind='shell',body='date -u'))
    raw=source.get('/api/simple/labs/example',headers=h).data
    source.post('/api/simple/labs/import',headers=h,json={'archive':base64.b64encode(raw).decode()})
    plan=source.post('/api/simple/plan',headers=h,json=settings()).json
    saved=source.post('/api/simple/recipes',headers=h,json=dict(plan_id=plan['id'],name='Reusable DC'))
    assert saved.status_code==201,saved.json
    app.manager.put('guided-recipes',dict(id='legacy',name='Old build'))
    upload=export(source,h,'portable')
    document,_ = dashboard_backup.open_archive(upload)
    plain=json.dumps(document)
    assert source_id not in plain and 'Guest-test' not in plain and script['id'] not in plain
    app,target,headers=destination(tmp_path,monkeypatch)
    dest=app.server.current()
    assert dest['id'] != source_id
    before=preview(target,headers,upload)
    assert before['omitted']==['Old build']
    assert {i['kind'] for i in before['items']}=={'script','lab','saved build'}
    args=dict(**upload,review=before['review'],confirmed=True,selected=[i['id'] for i in before['items'] if i['kind']!='lab'])
    result=target.post(PREFIX+'restore',headers=headers,json=args)
    assert result.status_code==200 and result.json['imported']==2
    assert not app.manager.all('credentials') and app.server.read()==dest
    packs=target.get('/api/simple/labs/packs',headers=headers).json
    assert [r['name'] for r in packs]==['Reusable DC'] and packs[0]['server_id']==dest['id']
    assert app.manager.all('tasks')[0]['body']=='date -u'
    assert not target.get('/api/jobs',headers=headers).json
    assert target.post(PREFIX+'restore',headers=headers,json=args).status_code==400
    assert [i['status'] for i in preview(target,headers,upload)['items']].count('already present')==2
    local=app.manager.all('tasks')[0]
    app.manager.save('tasks',{**local,'body':'uname'})
    conflict=preview(target,headers,upload)
    assert next(i for i in conflict['items'] if i['name']=='System report')['status']=='name conflict'
    r=target.post(PREFIX+'restore',headers=headers,json={**args,'review':conflict['review']})
    assert r.status_code==400 and app.manager.get('tasks',local['id'])['body']=='uname'


def test_portable_preview_compares_deployment_order_across_dashboards(simple_lab, tmp_path, monkeypatch):
    _, source, headers, _ = simple_lab
    roles = {'web': linux(), 'db': linux()}
    pack = import_pack(source, headers, roles)
    upload = export(source, headers, 'portable')
    archive = source.get('/api/simple/labs/packs/' + pack + '/export', headers=headers).data

    _, target, headers = destination(tmp_path, monkeypatch)
    import_pack(target, headers, {'db': linux(), 'web': linux()})
    conflict, = preview(target, headers, upload)['items']
    assert conflict['status'] == 'name conflict'

    # A ZIP comment changes archive bytes without changing the deployed lab.
    repacked = io.BytesIO(archive)
    with zipfile.ZipFile(repacked, 'a') as z:
        z.comment = b'Copy for the second dashboard'
    imported = target.post('/api/simple/labs/import', headers=headers,
                           json={'archive': base64.b64encode(repacked.getvalue()).decode()})
    assert imported.status_code == 200, imported.json
    match, = preview(target, headers, upload)['items']
    assert match['status'] == 'already present'
    assert target.get('/api/jobs', headers=headers).json == []


def test_auth_tampering_and_active_restore_rollback(service):
    app,c=service
    h={'Authorization':'Bearer '+app.API_TOKEN}
    assert c.post(PREFIX+'export',json={}).status_code==401
    assert c.post('/api/login',json=dict(password=ADMIN)).status_code==200
    assert c.post(PREFIX+'export',json={}).status_code==403
    assert c.post(PREFIX+'export',headers=h,json=dict(mode='full',passphrase=PASS)).status_code==400
    upload=export(c,h,'full')
    raw=bytearray(base64.b64decode(upload['archive']));raw[-1]^=1
    for bad in ({**upload,'passphrase':'wrong long passphrase'}, {**upload,'archive':base64.b64encode(raw).decode()}):
        assert c.post(PREFIX+'preview',headers=h,json=bad).status_code==400
    first=preview(c,h,upload)
    credential=store_credential(app)
    args=dict(**upload,review=first['review'],confirmed=True,admin_password=ADMIN)
    assert c.post(PREFIX+'restore',headers=h,json=args).status_code==400
    with app.db() as con:
        con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',('active','start','running',1,1,app.encode_payload({'action':'start'}),''))
    args['review']=preview(c,h,upload)['review']
    assert c.post(PREFIX+'restore',headers=h,json=args).status_code==400
    assert app.manager.find('credentials',credential['id'])
    with app.db() as con:
        con.execute("UPDATE jobs SET status='succeeded'")
    doc,_=dashboard_backup.open_archive(upload)
    doc['content']['server_connection']*=2  # Fails the destination primary key after replacement starts.
    malformed=dict(archive=base64.b64encode(dashboard_backup.seal(doc,PASS)).decode(),passphrase=PASS)
    review=preview(c,h,malformed)
    r=c.post(PREFIX+'restore',headers=h,json=dict(**malformed,review=review['review'],confirmed=True,admin_password=ADMIN))
    assert r.status_code==400 and app.manager.find('credentials',credential['id'])
    assert c.get('/api/jobs',headers=h).json[0]['status']=='succeeded'
