import importlib
import time

import pytest
import lifecycle
import operations
from test_api import service
from test_lifecycle import recovery


def insert(app, ident, payload):
    now=time.time()
    with app.db() as con:
        con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(ident,payload['action'],'queued',now,now,app.encode_payload(payload),'Queued.'))


def status(app, ident='build'):
    with app.db() as con: return dict(con.execute('SELECT status,log FROM jobs WHERE id=?',(ident,)).fetchone())


def due(app):
    with app.db() as con: con.execute('UPDATE job_waits SET next_check=0')


def build(app, fake, monkeypatch, action='provision', tasks=()):
    cred=app.manager.save('credentials',dict(name='Linux login',platform='linux',username='test',password='GuestSecret!'))
    cfg=dict(action='provision',node='pve',vmid=201,name='vm',kind='linux-cloud',
             bootstrap=dict(verify=True,credential_id=cred['id'],timeout=600))
    payload=cfg
    if action=='simple':
        payload=dict(action=action,plan=dict(config=cfg,tasks=list(tasks),purpose='template' if tasks else 'vm'))
    if action=='lifecycle':
        payload=dict(action=action,plan=dict(operation='version-build',config=cfg,family_id='family',
                                            label='one',recipe_id='recipe',recipe_name='test',recipe_tasks=[],frozen_tasks=list(tasks)))
    calls=[]
    def execute(d,log):
        calls.append((d['action'],d['vmid']))
        return {}
    monkeypatch.setattr(operations,'execute',execute)
    monkeypatch.setattr(app,'execute',execute)
    monkeypatch.setattr(lifecycle,'run_guest',lambda *a,**kw:None)
    insert(app,'build',payload)
    assert app.run_next_job() and status(app)['status']=='waiting'
    return calls,cred


@pytest.mark.parametrize('action',['simple','lifecycle','provision'])
def test_wait_releases_worker_and_survives_restart(recovery,monkeypatch,action):
    app,_,_,fake=recovery
    calls,_=build(app,fake,monkeypatch,action)
    fake.fail_agent=True
    # The actual read-only probe yields, with a bounded log frequency.
    assert app.run_next_job() and 'guest agent' in status(app)['log']
    due(app)
    assert app.run_next_job() and status(app)['log'].count('Waiting for setup:')==1
    insert(app,'same',dict(action='start',node='pve',vmid=201))
    insert(app,'other',dict(action='start',node='pve',vmid=202))
    assert app.run_next_job() and status(app,'other')['status']=='succeeded'
    assert status(app,'same')['status']=='queued' and not app.run_next_job()
    with app.db() as con:
        deadline=con.execute('SELECT deadline FROM job_waits').fetchone()[0]
        with pytest.raises(ValueError,match='waiting'): app.server.idle(con)
    importlib.reload(app)
    assert status(app)['status']=='waiting' and status(app,'same')['status']=='interrupted'
    with app.db() as con: assert con.execute('SELECT deadline FROM job_waits').fetchone()[0]==deadline
    due(app)
    fake.fail_agent=False
    assert app.run_next_job() and status(app)['status']=='succeeded'
    assert calls==[('provision',201),('start',202)]
    assert app.manager.get('connections','pve:201')['verified_at']
    assert app.manager.get('builds','build')['bootstrap_verified']
    with app.db() as con: assert not con.execute('SELECT 1 FROM job_waits').fetchone()
    assert not app.run_next_job()


@pytest.mark.parametrize('change',['deadline','server','credential','connection','identity','template','moved'])
def test_stale_wait_fails_before_auth_or_mutations(recovery,monkeypatch,change):
    app,_,_,fake=recovery
    calls,cred=build(app,fake,monkeypatch)
    if change=='deadline':
        with app.db() as con: con.execute('UPDATE job_waits SET deadline=0')
    if change=='server':
        with app.db() as con: con.execute("UPDATE job_waits SET server_id='old'")
    if change=='credential': app.manager.save('credentials',dict(cred,password='rotated'))
    if change=='connection': app.manager.put('connections',dict(id='pve:201',updated=time.time()))
    if change=='identity': fake.configs[201]['smbios1']='uuid=new'
    if change=='template': fake.guests[201]['template']=True
    if change=='moved':
        original=fake.resources
        fake.resources=lambda:[dict(r,node='other') if r['type']=='qemu' else r for r in original()]
    def forbidden(*args,**kwargs): pytest.fail('Stale wait tried to authenticate')
    monkeypatch.setattr(lifecycle,'run_guest',forbidden)
    assert app.run_next_job() and status(app)['status']=='failed'
    assert calls==[('provision',201)] and not fake.writes
    with app.db() as con: assert not con.execute('SELECT 1 FROM job_waits').fetchone()


def test_crash_on_each_side_of_followup_boundary(recovery,monkeypatch):
    app,_,_,fake=recovery
    calls,_=build(app,fake,monkeypatch,'simple')
    class PowerLoss(BaseException): pass
    def crash_probe(*args,**kwargs): raise PowerLoss
    monkeypatch.setattr(lifecycle,'run_guest',crash_probe)
    with pytest.raises(PowerLoss): app.run_next_job()
    importlib.reload(app)
    assert status(app)['status']=='waiting'
    monkeypatch.setattr(lifecycle,'run_guest',lambda *a,**kw:None)
    def crash_in_software(*args):
        calls.append(('software',201))
        raise PowerLoss
    monkeypatch.setattr(app.simple,'finish_build',crash_in_software)
    with pytest.raises(PowerLoss): app.run_next_job()
    assert status(app)['status']=='running'
    importlib.reload(app)
    assert status(app)['status']=='interrupted' and not app.run_next_job()
    assert calls==[('provision',201),('software',201)]


def test_changed_login_during_probe_cannot_publish_verified_connection(recovery,monkeypatch):
    app,_,_,fake=recovery
    _,cred=build(app,fake,monkeypatch)
    monkeypatch.setattr(lifecycle,'run_guest',lambda *a,**kw:app.manager.save('credentials',dict(cred,password='replacement')))
    assert app.run_next_job() and status(app)['status']=='failed'
    assert not app.manager.all('connections')

@pytest.mark.parametrize('action',['simple','lifecycle'])
def test_frozen_software_runs_once_after_restart(recovery,monkeypatch,action):
    import template_finish
    app,_,_,fake=recovery
    tasks=app.manager.task_selection([{'id':'linux-package','params':{'package':'curl'}}])
    calls,_=build(app,fake,monkeypatch,action,tasks)
    importlib.reload(app)
    def software(plan,log):
        assert plan['tasks']==tasks
        assert plan['targets'][0]['connection']['machine_identity']==lifecycle.machine_identity(fake.configs[201])
        calls.append(('software',201))
    monkeypatch.setattr(app.manager,'execute',software)
    monkeypatch.setattr(template_finish,'finish',lambda config,m,log,i:calls.append(('template',config['vmid'])))
    assert app.run_next_job() and status(app)['status']=='succeeded'
    assert calls==[('provision',201),('software',201)]+([('template',201)] if action=='simple' else [])
    assert app.manager.get('builds','build')['tasks'][0]['tasks'][0]['params']=={'package':'curl'}
    importlib.reload(app)
    assert not app.run_next_job()

def test_only_executor_target_fields_can_release_a_queued_job():
    from job_queue import target_ids
    assert target_ids(dict(action='start',vmid=201,plan={'config':{'vmid':202}}))=={201}
    assert target_ids(dict(action='clone',source=201,vmid=202))=={201,202}
    assert target_ids(dict(action='ansible',vmid=202))==set()
