import copy

import management
from test_api import service
from test_management import lab, setup_guest
from test_simple_flow import simple_lab, settings


def test_repeat_guided_build_from_review_job_and_legacy_payload(simple_lab):
    app,client,h,_=simple_lab
    def post(path,data): return client.post('/api/simple/'+path,headers=h,json=data)
    draft=settings()
    draft.update(count=1,data_disks=[dict(size=40,label='Data',letter='G')],manual_updates=True)
    first=post('plan',draft).json
    saved=post('recipes',dict(plan_id=first['id'],name='My Windows server'))
    assert saved.status_code==201 and saved.json['password_login']
    stored=app.manager.get('guided-recipes',saved.json['id'])['recipe']
    assert draft['password'] not in repr(stored)
    assert not {'vmid','name','count','password','confirmed'} & stored['settings'].keys()
    assert stored['settings']['tasks']==draft['tasks']
    assert not client.get('/api/jobs',headers=h).json
    job=post('run',dict(plan_id=first['id'],confirmed=True,risk_confirmed=True)).json['jobs'][0]
    detail=client.get(job['url'],headers=h).json
    assert detail['reusable_build']['edition']==first['edition']
    for source in ({'recipe_id':saved.json['id']},{'job_id':job['id']}):
        repeat={'reuse':source,'name':'demo-copy'}
        assert post('plan',repeat).status_code==400
        repeat['password']='Fresh-Setup#456'
        second=post('plan',repeat).json
        assert second['machines'][0]['vmid']!=first['machines'][0]['vmid']
        assert second['tasks']==first['tasks'] and second['data_disks']==first['data_disks']
        assert post('plan',dict(repeat,vmid=202)).status_code==400
    with app.db() as con:
        p=app.decode_payload(con.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
        del p['plan']['recipe']
        con.execute('UPDATE jobs SET payload=? WHERE id=?',(app.encode_payload(p),job['id']))
    assert post('plan',repeat).json['tasks']==first['tasks']
    with app.db() as con:
        p['server_id']='another-server'
        con.execute('UPDATE jobs SET payload=? WHERE id=?',(app.encode_payload(p),job['id']))
    assert post('plan',repeat).status_code==400
    assert 'reusable_build' not in client.get(job['url'],headers=h).json
    assert post('recipes/'+saved.json['id']+'/delete',{}).json['ok']
    assert client.get('/api/simple/recipes',headers=h).json==[]


def test_competing_guided_reviews_cannot_queue_the_same_new_vm(simple_lab):
    _,client,h,_=simple_lab
    def post(path,data): return client.post('/api/simple/'+path,headers=h,json=data)
    draft=dict(settings(),count=1)
    first=post('plan',draft).json
    recipe=post('recipes',dict(name='Runner',plan_id=first['id'])).json
    repeat=dict(reuse={'recipe_id':recipe['id']},name='another',password=draft['password'])
    competing=post('plan',repeat).json
    assert first['machines'][0]['vmid']==competing['machines'][0]['vmid']
    def submit(p): return post('run',dict(plan_id=p['id'],confirmed=True,risk_confirmed=True))
    first_job=submit(first).json
    assert submit(competing).status_code==400 and submit(first).json==first_job
    assert len(client.get('/api/jobs',headers=h).json)==1
    fresh=post('plan',repeat).json
    assert fresh['machines'][0]['vmid']!=competing['machines'][0]['vmid']
    assert submit(fresh).status_code==202


def test_ubuntu_repeat_preserves_key_source_and_policies_without_reusing_login(simple_lab,monkeypatch):
    import linux_access
    app,client,h,_=simple_lab
    handles=[]
    monkeypatch.setattr(linux_access,'github_keys',lambda handle:handles.append(handle) or [])
    draft=dict(os='ubuntu',name='base',github_username='operator',ubuntu_firewall='ssh',
               ubuntu_updates='manual',ubuntu_password='Secret-Ubuntu#1')
    plan=lambda d:client.post('/api/simple/plan',json=d,headers=h)
    first=plan(draft).json
    saved=client.post('/api/simple/recipes',json=dict(plan_id=first['id'],name='Ubuntu dev'),headers=h).json
    second=plan(dict(reuse={'recipe_id':saved['id']},name='new',ubuntu_password='Different-Ubuntu#1')).json
    assert handles==['operator','operator'] and first['tasks']==second['tasks']
    recipe=app.manager.get('guided-recipes',saved['id'])['recipe']
    assert 'ubuntu_password' not in recipe['settings'] and '$6$' not in repr(recipe)
    plans=[app.manager.load_plan(p['id']) for p in (first,second)]
    assert plans[0]['credential']['private_key']!=plans[1]['credential']['private_key']
    assert plans[0]['tasks'][-1]!=plans[1]['tasks'][-1]
    from guided_recipes import specification
    cfg=dict(plans[0]['configs'][0],ssh_key='automation-key\npreviously-imported-key')
    historical=specification(plans[0],cfg)
    assert historical['needs_key_source'] and 'previously-imported-key' not in repr(historical)
    app.manager.put('guided-recipes',dict(id='older',name='Older VM',recipe=historical))
    draft=dict(reuse={'recipe_id':'older'},name='dev-old',ubuntu_password='Fresh-Ubuntu#1')
    assert plan(draft).status_code==400
    assert plan(dict(draft,github_username='operator')).status_code==200


def test_recovery_resumes_original_frozen_suffix_once_and_only_for_failed_vm(lab,monkeypatch):
    app,client,h,host,_=lab
    login=setup_guest(app,'linux')
    app.manager.save('connections',dict(node='pve',vmid=202,platform='linux',credential_id=login['id'],
        host='192.0.2.20',fingerprint='SHA256:'+'a'*43))
    item=app.manager.save('tasks',dict(name='Set up tools',platform='linux',kind='shell',body='echo original'))
    tasks=[dict(id='linux-package',params={'package':'curl'}),dict(id=item['id']),
           dict(id='linux-package',params={'package':'git'})]
    reviewed=app.manager.create_plan(dict(operation='tasks',targets=[dict(node='pve',vmid=i) for i in (201,202)],tasks=tasks))
    def submit(plan):
        return client.post('/api/manage/run',json=dict(plan_id=plan['id'],confirmed=True,risk_confirmed=True),headers=h)
    job=submit(reviewed).json
    executed=[]
    def run(binding,credential,steps,log,readiness=False):
        if readiness: return
        if binding['vmid']==201 and 'original' in str(steps) and not executed.count((201,'failed')):
            executed.append((201,'failed'))
            raise RuntimeError('Guest task failed')
        executed.append((binding['vmid'],str(steps)))
    monkeypatch.setattr(management,'run_guest',run)
    assert app.run_next_job()
    def detail(): return client.get(job['url'],headers=h).json
    target=detail()['task_recovery']['targets']
    assert len(target)==1 and target[0]['completed']==1 and target[0]['remaining']==2 and target[0]['started']
    body=dict(job_id=job['id'],target=target[0]['target'])
    review=lambda:client.post('/api/simple/recovery/plan',json=body,headers=h)
    host.changed=True
    assert review().status_code==400
    host.changed=False
    app.manager.save('tasks',dict(item,body='echo new-version'))
    original,competing=review().json,review().json
    assert original['dangerous'] and original['recovery']['started']
    assert [t['name'] for t in original['tasks']]==[item['name'],reviewed['tasks'][-1]['name']]
    assert client.post('/api/manage/run',json=dict(plan_id=original['id'],confirmed=True),headers=h).status_code==400
    assert client.post('/api/manage/schedules',json=dict(name='repeat',plan_id=original['id'],confirmed=True,risk_confirmed=True),headers=h).status_code==400
    child=submit(original)
    assert child.status_code==202 and submit(original).json==child.json
    assert submit(competing).status_code==400
    assert detail()['task_recovery']['targets'][0]['successor']==child.json['id']
    before=copy.copy(executed)
    assert app.run_next_job()
    assert client.get(child.json['url'],headers=h).json['status']=='succeeded'
    assert [i for i,_ in executed[len(before):]]==[201,201]
    assert 'original' in executed[-2][1] and 'git' in executed[-1][1]
    assert review().status_code==400 and not host.writes


def test_interrupted_task_stays_uncertain_across_a_second_restart(lab):
    import job_queue
    app,client,h,_,_=lab
    setup_guest(app,'linux')
    review=app.manager.create_plan(dict(operation='tasks',targets=[dict(node='pve',vmid=201)],
        tasks=[dict(id='linux-package',params={'package':p}) for p in ('curl','jq')]))
    def submit(p):
        return client.post('/api/manage/run',json=dict(plan_id=p['id'],confirmed=True,risk_confirmed=True),headers=h).json
    job=submit(review)
    app.manager.progress.mark(job['id'],'pve:201',0,completed=False)
    app.manager.progress.mark(job['id'],'pve:201',0,completed=True)
    app.manager.progress.mark(job['id'],'pve:201',1,completed=False)
    with app.db() as con: job_queue.initialize(con)
    def remainder(job):
        return client.post('/api/simple/recovery/plan',json=dict(job_id=job['id'],target='pve:201'),headers=h).json
    first=remainder(job)
    assert first['recovery']['started'] and first['recovery']['completed']==1
    child=submit(first)
    with app.db() as con: job_queue.initialize(con)
    second=remainder(child)
    assert second['recovery']['started'] and second['dangerous']
    assert second['tasks']==first['tasks']==[review['tasks'][1]]


def test_template_recovery_requires_approval_and_finishes_the_original_build(lab,monkeypatch):
    from lifecycle import machine_identity
    import template_finish
    from linux_install import template_builds
    app,client,h,host,runs=lab
    setup_guest(app,'linux')
    cfg=dict(node='pve',vmid=201,kind='ubuntu-iso')
    identity=machine_identity(host.call('GET','nodes/pve/qemu/201/config'))
    origin='template-build'
    app.manager.put('builds',dict(id=origin,**cfg,config=cfg,machine_identity=identity,
                                  created=1,tasks=[],bootstrap_verified=True))
    server=app.server.current()['id']
    payload=dict(action='simple',server_id=server,plan=dict(config=cfg,purpose='template'))
    p=app.manager.create_plan(dict(operation='tasks',targets=[cfg],tasks=[dict(id='linux-package',params={'package':'jq'})]))
    with app.db() as con:
        con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(origin,'simple','failed',1,1,app.encode_payload(payload),'failed'))
        app.manager.progress.initialize(con,origin,dict(app.manager.load_plan(p['id']),template_build=origin),server)
    assert not template_builds(app.manager)
    plan=client.post('/api/simple/recovery/plan',headers=h,json=dict(job_id=origin,target='pve:201')).json
    assert plan['finish_template'] and plan['dangerous']
    seen=[]
    def finish(config,manager,log,recorded_identity):
        assert config==cfg and recorded_identity==identity and any(not probe for _,_,_,probe in runs)
        seen.append(config['vmid'])
    monkeypatch.setattr(template_finish,'finish',finish)
    run=client.post('/api/manage/run',headers=h,json=dict(plan_id=plan['id'],confirmed=True,risk_confirmed=True)).json
    assert app.run_next_job() and client.get(run['url'],headers=h).json['status']=='succeeded'
    assert seen==[201] and [b['template_finished_by'] for b in template_builds(app.manager)]==[run['id']]
