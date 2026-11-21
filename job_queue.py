"""Single-worker scheduling. Only a recorded, read-only readiness probe can resume."""
import time


def initialize(con):
    con.execute('CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status,created,id)')
    con.execute('CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created)')
    con.execute('CREATE TABLE IF NOT EXISTS lab_jobs (job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL)')
    con.execute('CREATE INDEX IF NOT EXISTS lab_jobs_by_run ON lab_jobs(run_id)')
    con.execute('''CREATE TABLE IF NOT EXISTS job_dependencies (
        job_id TEXT NOT NULL, prerequisite TEXT NOT NULL, PRIMARY KEY (job_id,prerequisite))''')
    con.execute('CREATE INDEX IF NOT EXISTS jobs_by_prerequisite ON job_dependencies(prerequisite)')
    con.execute('''CREATE TABLE IF NOT EXISTS job_waits (
        job_id TEXT PRIMARY KEY, server_id TEXT NOT NULL, node TEXT NOT NULL, vmid INTEGER NOT NULL,
        identity TEXT NOT NULL, credential_revision REAL NOT NULL, connection_revision REAL,
        deadline REAL NOT NULL, next_check REAL NOT NULL, last_report REAL NOT NULL, reason TEXT NOT NULL)''')
    # A confirmed lab job still queued has never been claimed. Its approval, prerequisite
    # edges, reservations and credentials were committed together and remain valid.
    now=time.time()
    con.execute("""UPDATE jobs SET status='interrupted', updated=?,
        log=log || char(10) || 'Service restarted. Inspect Proxmox before retrying.'
        WHERE status='running' OR (status='queued' AND id NOT IN (SELECT job_id FROM lab_jobs))
        OR (status='waiting' AND id NOT IN (SELECT job_id FROM job_waits))""",(now,))
    block_dependents(con,now)
    con.execute("DELETE FROM job_waits WHERE job_id NOT IN (SELECT id FROM jobs WHERE status='waiting')")


def job_config(payload):
    if payload['action'] in ('simple','lifecycle'):
        return payload['plan'].get('config',{})
    return payload


def target_ids(payload):
    # Only the fields used by this executor establish its scope.
    action=payload['action']
    if action=='manage':
        values=[t['vmid'] for t in payload['plan']['targets']]
    elif action=='lifecycle':
        plan=payload['plan']
        values=[job_config(payload).get('vmid'),plan.get('vmid'),plan.get('source'),plan.get('source_vmid')]
    elif action in ('winrm-setup','simple','provision','start','backup','clone','convert-template','finish-setup','installer','linux-template'):
        values=[job_config(payload).get('vmid')]
        if action=='clone': values.append(payload.get('source'))
        if action=='simple': values.append(job_config(payload).get('template',{}).get('source'))
    else:
        return set()
    return {int(v) for v in values if str(v).isdigit()}


def claim(con, decode, now, prefer_probe):
    con.execute('BEGIN IMMEDIATE')
    due = con.execute("SELECT * FROM job_waits WHERE next_check<=? ORDER BY next_check,job_id LIMIT 1",(now,)).fetchone()
    reserved = {r['vmid'] for r in con.execute('SELECT vmid FROM job_waits')}
    for row in con.execute("""SELECT id,action,payload,created FROM jobs
            WHERE status='queued' AND NOT EXISTS (
                SELECT 1 FROM job_dependencies d LEFT JOIN jobs p ON p.id=d.prerequisite
                WHERE d.job_id=jobs.id AND p.status IS NOT 'succeeded')
            ORDER BY created,id"""):
        if due and prefer_probe: break
        if reserved:
            ids = target_ids(decode(row['payload']))
            if not ids or reserved & ids: continue
        con.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(now,row['id']))
        return row, None
    if due:
        # Keep status waiting during the read-only check. A crash here repeats just that check.
        return con.execute('SELECT id,action,payload,created FROM jobs WHERE id=?',(due['job_id'],)).fetchone(), due
    return None, None


def finish(con, ident, status, now):
    con.execute('DELETE FROM job_waits WHERE job_id=?',(ident,))
    con.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(status,now,ident))
    if status=='failed':
        block_dependents(con,now)


def block_dependents(con, now):
    # Release reservations even several levels downstream of a failed or interrupted machine.
    con.execute("""WITH RECURSIVE blocked(id) AS (
            SELECT j.id FROM jobs j JOIN job_dependencies d ON j.id=d.job_id
            LEFT JOIN jobs p ON d.prerequisite=p.id
            WHERE j.status='queued' AND (p.id IS NULL OR p.status IN ('failed','interrupted','blocked'))
            UNION SELECT d.job_id FROM job_dependencies d JOIN blocked p ON d.prerequisite=p.id
            JOIN jobs j ON d.job_id=j.id WHERE j.status='queued')
        UPDATE jobs SET status='blocked',updated=?,log=log || char(10) ||
            'A prerequisite failed or was interrupted. This job did not run.'
        WHERE id IN (SELECT id FROM blocked)""",(now,))


def park(con, ident, checkpoint):
    now=time.time()
    con.execute('''INSERT INTO job_waits VALUES (:job_id,:server_id,:node,:vmid,:identity,
        :credential_revision,:connection_revision,:deadline,:now,0,'Waiting for guest setup')''',
        dict(checkpoint,job_id=ident,now=now))
    con.execute("""UPDATE jobs SET status='waiting',updated=?,log=substr(log || char(10) ||
        'Waiting for guest setup. Other machines can run while this guest starts.', -150000) WHERE id=?""",(now,ident))


def postpone(con, wait, reason):
    now=time.time()
    report=now-wait['last_report']>=60
    con.execute('UPDATE job_waits SET next_check=?,last_report=?,reason=? WHERE job_id=?',
                (min(now+10,wait['deadline']),now if report else wait['last_report'],reason,wait['job_id']))
    if report:
        msg=f"Waiting for setup: {reason}. About {max(0,int((wait['deadline']-now+59)//60))} minutes remain."
        con.execute('UPDATE jobs SET updated=?,log=substr(log || char(10) || ?, -150000) WHERE id=?',(now,msg,wait['job_id']))
