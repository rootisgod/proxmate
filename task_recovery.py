"""Per-target guest task checkpoints and reviewed, single-use continuations."""
from flask import jsonify, request

import operations
import diagnostics


class TaskProgress:
    def __init__(self, manager):
        self.manager = manager
        with manager.db() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS task_runs (
                job_id TEXT PRIMARY KEY, server_id TEXT NOT NULL, total INTEGER NOT NULL, settings TEXT NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS task_progress (
                job_id TEXT NOT NULL, target TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0, started INTEGER NOT NULL DEFAULT 0,
                successor TEXT, detail TEXT NOT NULL,
                PRIMARY KEY(job_id, target))""")

    def initialize(self, con, job_id, plan, server_id):
        if con.execute('SELECT 1 FROM task_runs WHERE job_id=?',(job_id,)).fetchone():
            return
        settings=dict(tasks=plan['tasks'], snapshot_before=plan['snapshot_before'],
                      template_build=plan.get('template_build'))
        if plan.get('lab_existing'): settings['lab_existing']=True
        con.execute('INSERT INTO task_runs VALUES (?,?,?,?)',
                    (job_id,server_id,len(plan['tasks']),self.manager.encode(settings)))
        for t in plan['targets']:
            detail = {k:t[k] for k in ('node','vmid','name','identity')}
            con.execute("""INSERT INTO task_progress
                (job_id,target,started,detail) VALUES (?,?,?,?)""",
                (job_id, f"{t['node']}:{t['vmid']}",
                 int(plan.get('recovery',{}).get('started',False)), self.manager.encode(detail)))

    def mark(self, job_id, target, index, *, completed):
        with self.manager.db() as con:
            updated = con.execute("""UPDATE task_progress SET completed=?, started=?
                WHERE job_id=? AND target=? AND completed=? AND successor IS NULL""",
                (index + int(completed), int(not completed), job_id, target, index))
            if updated.rowcount != 1:
                raise ValueError('Task progress changed. Inspect this run before continuing.')

    def report(self, con, job_id, server_id):
        run=con.execute('SELECT settings,total FROM task_runs WHERE job_id=? AND server_id=?',(job_id,server_id)).fetchone()
        if not run: return []
        tasks=self.manager.decode(run['settings'])['tasks']
        rows = con.execute('SELECT * FROM task_progress WHERE job_id=? AND completed<? ORDER BY target',
                           (job_id,run['total']))
        result = []
        for row in rows:
            detail = self.manager.decode(row['detail'])
            result.append(dict(target=row['target'], name=detail['name'], completed=row['completed'],
                               remaining=len(tasks)-row['completed'], next_task=tasks[row['completed']]['item']['name'],
                               started=bool(row['started']), successor=row['successor']))
        return result

    def review(self, job_id, target):
        server_id = operations.connection_provider.current()['id']
        with self.manager.db() as con:
            row = con.execute("""SELECT p.*, r.settings, r.total, j.status FROM task_progress p
                JOIN task_runs r ON r.job_id=p.job_id JOIN jobs j ON j.id=p.job_id
                WHERE p.job_id=? AND p.target=? AND r.server_id=?""", (job_id, target, server_id)).fetchone()
        if (not row or row['status'] not in ('failed','interrupted') or row['successor']
                or row['completed'] >= row['total']):
            raise ValueError('No remaining tasks are available to review for this run and machine.')
        detail = self.manager.decode(row['detail'])
        settings = self.manager.decode(row['settings'])
        origin = dict(job_id=job_id, target=target, completed=row['completed'], identity=detail['identity'],
                      started=bool(row['started']), template_build=settings['template_build'])
        if settings.get('lab_existing'): origin['lab_existing']=True
        plan=self.manager.create_plan(dict(operation='tasks', targets=[detail], snapshot_before=settings['snapshot_before']),
                                      selections=settings['tasks'][row['completed']:], recovery=origin)
        diagnostics.event('tasks.recovery_reviewed',job_id=job_id,plan_id=plan['id'],vmid=detail['vmid'],
                          completed=row['completed'],remaining=row['total']-row['completed'])
        return plan

    def claim(self, con, job_id, plan, server_id):
        source = plan['recovery']
        updated = con.execute("""UPDATE task_progress SET successor=?
            WHERE job_id=? AND target=? AND completed=? AND successor IS NULL
            AND EXISTS(SELECT 1 FROM jobs j JOIN task_runs r ON r.job_id=j.id
                       WHERE j.id=? AND r.server_id=? AND j.status IN ('failed','interrupted'))""",
            (job_id, source['job_id'], source['target'], source['completed'], source['job_id'], server_id))
        if updated.rowcount != 1:
            raise ValueError('Another recovery already claimed these tasks. Open the latest run.')

    def register(self, app):
        @app.post('/api/simple/recovery/plan')
        def guest_recovery_plan():
            data = request.get_json()
            if not isinstance(data,dict) or not all(isinstance(data.get(k),str) for k in ('job_id','target')):
                raise ValueError('Choose a failed job and its recorded machine.')
            return jsonify(self.review(data.get('job_id'), data.get('target')))
