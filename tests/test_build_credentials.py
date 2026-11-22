import io
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from test_api import service
from test_simple_flow import simple_lab, settings
from test_lab_runs import import_pack
from test_windows_workflows import clone_lab


DEFAULTS='/api/manage/credentials/defaults'


def pin(app, client, headers, platform, **fields):
    record=app.manager.save('credentials', dict(platform=platform,name='Lab '+platform,**fields))
    response=client.post(DEFAULTS,json=dict(platform=platform,credential_id=record['id']),headers=headers)
    assert response.status_code==200, response.json
    assert response.json[platform]=={k:record[k] for k in ('id','name','username')}
    return record


def test_shared_linux_key_wires_the_installer_without_new_credentials(simple_lab):
    app,c,h,_=simple_lab
    key=ed25519.Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH).decode()
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption()).decode()
    assert c.get(DEFAULTS,headers=h).json==dict(linux=None,windows=None)
    record=pin(app,c,h,'linux',username='labadmin',private_key=private)
    assert c.get('/api/manage/credentials',headers=h).json[0]['is_build_default']
    assert c.get('/api/simple/options',headers=h).json['build_defaults']['linux']['id']==record['id']
    draft=dict(os='ubuntu',source_kind='cloud',name='shared',build_credential_id=record['id'],count=2)
    review=c.post('/api/simple/plan',json=draft,headers=h)
    assert review.status_code==200,review.json
    assert review.json['build_login']==dict(username='labadmin',name='Lab linux',shared=True)
    plan=app.manager.load_plan(review.json['id'])
    assert private not in str(plan) and record['id'] not in str(plan['recipe'])
    for cfg in plan['configs']:
        assert (cfg['username'],cfg['ssh_key'],cfg['bootstrap']['credential_id'])==('labadmin',public,record['id'])
    assert c.post('/api/simple/plan',json=dict(draft,username='different'),headers=h).status_code==400
    assert c.post('/api/simple/plan',json=dict(settings(),build_credential_id=record['id']),headers=h).status_code==400
    run=dict(plan_id=review.json['id'],confirmed=True)
    assert len(c.post('/api/simple/run',json=run,headers=h).json['jobs'])==2
    assert len(app.manager.all('credentials'))==1
    assert c.post('/api/manage/credentials/'+record['id']+'/delete',json={},headers=h).status_code==400
    assert c.post(DEFAULTS,json=dict(platform='linux',credential_id=None),headers=h).status_code==200
    blocked=c.post('/api/manage/credentials/'+record['id']+'/delete',json={},headers=h)
    assert blocked.status_code==400 and 'active machine build' in blocked.json['error']
    app.manager.save('credentials',dict(record,name='Renamed login'))
    assert app.run_next_job()
    failed=next(j for j in c.get('/api/jobs',headers=h).json if j['status']=='failed')
    assert 'credential changed after review' in c.get('/api/jobs/'+failed['id'],headers=h).json['log']
    assert c.post('/api/simple/run',json=run,headers=h).status_code==202
    separate=c.post('/api/simple/plan',json=dict(os='ubuntu',name='other'),headers=h)
    assert separate.status_code==200 and not separate.json['build_login']['shared']
    assert app.manager.load_plan(separate.json['id'])['configs'][0]['ssh_key']!=public


def test_credential_eligibility_and_stale_windows_review(simple_lab):
    app,c,h,_=simple_lab
    for platform,username,fields in [('windows','Administrator',dict(password='short')),('windows','domainuser',dict(password='Valid pass #125')),
                                     ('linux','svc',dict(password='password')),('linux','root',dict(password='password')),
                                     ('linux','svc',dict(private_key='-----BEGIN PRIVATE KEY-----'))]:
        credential=app.manager.save('credentials',dict(name='Ops only',platform=platform,username=username,**fields))
        assert c.post(DEFAULTS,json=dict(platform=platform,credential_id=credential['id']),headers=h).status_code==400
    win=pin(app,c,h,'windows',username='Administrator',password='Valid admin #124')
    assert c.post(DEFAULTS,json={'platform':'windows'},headers=h).status_code==400
    assert c.post('/api/manage/credentials',json=dict(win,password='short'),headers=h).status_code==400
    draft=dict(settings(),build_credential_id=win['id'])
    assert c.post('/api/simple/plan',json=draft,headers=h).status_code==400
    del draft['password']
    review=c.post('/api/simple/plan',json=draft,headers=h)
    assert review.status_code==200 and 'Valid admin' not in review.text
    app.manager.save('credentials',dict(win,password='Rotated admin #342'))
    assert c.post('/api/simple/run',json=dict(plan_id=review.json['id'],confirmed=True),headers=h).status_code==400
    clear=c.post(DEFAULTS,json=dict(platform='windows',credential_id=None),headers=h)
    assert clear.json['windows'] is None
    assert c.post('/api/simple/plan',json=draft,headers=h).status_code==400 and not c.get('/api/jobs',headers=h).json
    assert c.post('/api/manage/credentials/'+win['id']+'/delete',json={},headers=h).status_code==200
    assert c.post('/api/simple/run',json=dict(plan_id=review.json['id'],confirmed=True),headers=h).status_code==400


def test_shared_windows_clone_and_portable_repeat(clone_lab):
    app,c,h,fake,draft=clone_lab
    password=draft.pop('password')
    record=pin(app,c,h,'windows',username='Administrator',password=password)
    draft['build_credential_id']=record['id']
    review=c.post('/api/simple/plan',json=draft,headers=h)
    assert review.status_code==200,review.json
    saved=c.post('/api/simple/recipes',json=dict(plan_id=review.json['id'],name='Repeat clone'),headers=h).json
    repeat=c.post('/api/simple/plan',json=dict(name='repeat',reuse={'recipe_id':saved['id']},generalized=True,
                                             build_credential_id=record['id']),headers=h)
    assert repeat.status_code==200 and repeat.json['build_login']['shared']
    run=c.post('/api/simple/run',json=dict(plan_id=review.json['id'],confirmed=True),headers=h)
    job=run.json['jobs'][0]['url']
    assert app.run_next_job() and fake.started
    assert c.get(job,headers=h).json['reusable_answer_only']
    assert password.encode() in fake.xml
    with zipfile.ZipFile(io.BytesIO(c.get(job+'/answer',headers=h).data)) as z:
        assert all(password.encode() not in z.read(name) for name in z.namelist())
    assert c.post(job+'/answer',json={'include_secrets':True},headers=h).status_code==400


def test_lab_resolves_the_destination_default_at_review(simple_lab):
    app,c,h,_=simple_lab
    ident=pin(app,c,h,'windows',username='Administrator',password='Separate lab #456')['id']
    pack=import_pack(c,h,{'dc':dict(os='windows',source={'kind':'iso'})})
    review=c.post('/api/simple/labs/plan',headers=h,json=dict(pack_id=pack,bindings={'dc':dict(source='local:iso/server.iso',edition=7)},
                                                            secrets={'dc':dict(build_credential_id=ident)}))
    assert review.status_code==200,review.json
    assert review.json['machines'][0]['plan']['build_login']['shared']
    assert ident not in str(app.manager.get('lab-packs',pack)['manifest'])
