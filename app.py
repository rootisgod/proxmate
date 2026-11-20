"""Single-admin LAN service. Launch one process; jobs are serialized."""
import hashlib
import base64
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import diagnostics
import job_queue
import windows_artifacts
import winrm_setup
from provisioning import answer_recorder
from lifecycle import BootstrapPending, WaitForGuest
from flask import Flask, jsonify, request, session, send_from_directory, render_template, g
from werkzeug.exceptions import HTTPException
from cryptography.fernet import Fernet
from operations import ACTIONS, execute, inventory, secret, validate
from provisioning import PROFILES, LOCALES, TIMEZONES, answer_xml

DATA = Path(os.getenv('DATA_DIR', 'data'))
DATA.mkdir(parents=True, exist_ok=True)


def persistent_secret(name):
    path = DATA / name
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
    value = path.read_text().strip()
    if not value:
        raise RuntimeError(f'Empty secret file: {name}')
    return value


def admin_password():
    password = secret(os.getenv('APP_PASSWORD_FILE', '.env.admin'))
    if not password:
        raise RuntimeError('Administrator password file is empty')
    return password


app = Flask(__name__, static_url_path='/static', template_folder='static')
app.secret_key = persistent_secret('session-key')
API_TOKEN = persistent_secret('api-token')
PAYLOAD_CIPHER = Fernet(base64.urlsafe_b64encode(hashlib.sha256(app.secret_key.encode()).digest()))


def encode_payload(payload):
    return 'encrypted:' + PAYLOAD_CIPHER.encrypt(json.dumps(payload, sort_keys=True).encode()).decode()


def decode_payload(value):
    if value.startswith('encrypted:'):
        value = PAYLOAD_CIPHER.decrypt(value.removeprefix('encrypted:').encode()).decode()
    return json.loads(value)  # Read older jobs without rewriting their history.
app.config.update(MAX_CONTENT_LENGTH=200000, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE='Strict', PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                  SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', 'false') == 'true')


def db():
    con = sqlite3.connect(DATA / 'jobs.sqlite', timeout=15)
    con.row_factory = sqlite3.Row
    return con


with db() as con:
    con.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, action TEXT, status TEXT, created REAL, updated REAL, payload TEXT, log TEXT)')
    job_queue.initialize(con)
    windows_artifacts.initialize(con)

login_attempts = {}
login_lock = threading.Lock()


@app.before_request
def start_trace():
    if not request.path.startswith('/api/'): return
    candidate=request.headers.get('X-Request-ID','')
    g.trace_token=diagnostics.trace.set(candidate if re.fullmatch('[a-f0-9]{32}',candidate) else uuid.uuid4().hex)
    g.request_start=time.monotonic()
    if request.method=='POST':
        diagnostics.event('request.start',route=request.url_rule.rule if request.url_rule else 'unknown')


@app.before_request
def protect():
    if request.path in ('/', '/api/health', '/api/openapi.json', '/api/docs') or request.path.startswith('/static/'):
        return
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
            return jsonify(error='Cross-origin requests are not allowed'), 403
        if not request.is_json:
            return jsonify(error='Use application/json'), 415
    if request.path == '/api/login': return
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if token and hmac.compare_digest(token, API_TOKEN): return
    if not session.get('admin'): return jsonify(error='Sign in required'), 401
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', 'missing')):
        return jsonify(error='Invalid CSRF token'), 403


@app.after_request
def headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    style_sources = "'self'"
    if g.get('style_nonce'):
        style_sources += f" 'nonce-{g.style_nonce}'"
        response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = f"default-src 'self'; style-src {style_sources}; script-src 'self'; frame-ancestors 'none'"
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Request-ID'] = diagnostics.trace.get() or ''
        elapsed=round((time.monotonic()-g.request_start)*1000)
        if request.method=='POST' or response.status_code>=400 or elapsed>=1000:
            diagnostics.event('request.finished',route=request.url_rule.rule if request.url_rule else 'unknown',
                              status=response.status_code,elapsed_ms=elapsed)
    return response


def redact(value):
    if 'server' in globals():
        password = server.read().get('password','')
        if password: value = value.replace(password, '[redacted]')
    for path in (os.getenv('PROXMOX_PASSWORD_FILE', '.env.password'), os.getenv('APP_PASSWORD_FILE', '.env.admin')):
        try:
            s = secret(path)
            if s: value = value.replace(s, '[redacted]')
        except OSError: pass
    return value.replace(API_TOKEN, '[redacted]')


@app.errorhandler(Exception)
def error(exc):
    diagnostics.event('request.failed',route=request.url_rule.rule if request.url_rule else 'unknown',**diagnostics.failure(exc))
    if isinstance(exc, HTTPException): return jsonify(error=exc.description), exc.code
    if isinstance(exc, (ValueError, KeyError)): return jsonify(error=redact(str(exc))), 400
    return jsonify(error=redact(str(exc))), 502


@app.get('/')
@app.get('/static/index.html')
def index():
    g.style_nonce = secrets.token_urlsafe(24)
    return render_template('index.html', style_nonce=g.style_nonce)


@app.get('/api/health')
def health(): return jsonify(status='ok')


@app.post('/api/login')
def login():
    ip = request.remote_addr
    with login_lock:
        recent = [t for t in login_attempts.get(ip, []) if t > time.time() - 300]
        login_attempts[ip] = recent
        if len(recent) >= 10: return jsonify(error='Too many attempts. Retry in five minutes.'), 429
        recent.append(time.time())
    password = request.get_json().get('password', '')
    expected = admin_password()
    if not isinstance(password, str) or not hmac.compare_digest(password.encode(), expected.encode()):
        return jsonify(error='Incorrect password'), 401
    session.clear(); session['admin'] = True; session['csrf'] = secrets.token_urlsafe(32); session.permanent = True
    return jsonify(csrf=session['csrf'])


@app.get('/api/session')
def current_session(): return jsonify(csrf=session.get('csrf'), authenticated=True)


@app.post('/api/logout')
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get('/api/state')
def state(): return jsonify(inventory())


@app.get('/api/jobs')
def jobs():
    with db() as con:
        columns = 'id, action, status, created, updated, payload' if request.args.get('summary') == '1' else 'jobs.*'
        rows = con.execute(f'SELECT {columns}, EXISTS(SELECT 1 FROM windows_answers a WHERE a.job_id=jobs.id) AS has_answer FROM jobs ORDER BY created DESC LIMIT 100').fetchall()
    return jsonify([job_view(r) for r in rows])


def job_view(row, detail_db=None):
    result = dict(row)
    if 'has_answer' in result: result['has_answer']=bool(result['has_answer'])
    payload = decode_payload(result.pop('payload'))
    result['target'] = {k: payload[k] for k in ('node', 'vmid', 'source', 'name', 'filename') if k in payload}
    if payload['action']=='simple': result['server_id']=payload.get('server_id')
    if payload['action'] in ('manage','lifecycle','simple'):
        plan=payload.get('plan',{})
        result['operation']=plan.get('operation',payload['action'])
        target=plan.get('config',plan)
        result['target']={k:target[k] for k in ('node','vmid','name','label','archive') if k in target}
        if payload['action']=='simple' and result['status']=='succeeded' and plan.get('purpose')=='vm':
            if target.get('kind')=='linux-cloud': result['remote_kind']='ssh'
            elif target.get('windows',{}).get('rdp') or any(t.get('item',{}).get('id')=='enable-rdp' for t in plan.get('tasks',[])):
                result['remote_kind']='rdp'
        if detail_db is not None and payload['action']=='manage' and result['status']=='failed':
            targets = connection_recovery_targets(plan, result['log'])
            if targets:
                # Read the server ID and archive boundary in one snapshot for historical jobs.
                selected = detail_db.execute(
                    'SELECT value FROM server_connection WHERE id=1 AND NOT EXISTS '
                    '(SELECT 1 FROM server_archives WHERE created >= ?)', (result['created'],)
                ).fetchone()
                config = decode_payload(selected['value']) if selected else {}
                if config.get('connected'):
                    result['connection_recovery'] = dict(server_id=config['id'], targets=targets)
    return result


@app.get('/api/jobs/<job_id>')
def job(job_id):
    with db() as con:
        row = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        has_answer=bool(con.execute('SELECT 1 FROM windows_answers WHERE job_id=?',(job_id,)).fetchone())
        if row: return jsonify(dict(job_view(row,con),has_answer=has_answer))
    return jsonify(error='Job not found'),404


@app.get('/api/jobs/<job_id>/console')
def build_console(job_id):
    from flask import redirect
    from urllib.parse import urlencode
    from machine import verify_identity
    with db() as con: row=con.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row: return jsonify(error='Job not found'),404
    payload=decode_payload(row['payload'])
    selected=server.current()
    build=manager.find('builds',job_id)
    if payload.get('action')!='simple' or payload.get('server_id')!=selected['id'] or not build:
        return jsonify(error='No recorded machine on this server for this build'),404
    verify_identity(operations.Proxmox(),build,build['machine_identity'])
    query=urlencode(dict(console='kvm',novnc=1,node=build['node'],vmid=build['vmid']))
    return redirect(selected['url'].rstrip('/')+'/?'+query)


@app.route('/api/jobs/<job_id>/answer',methods=['GET','POST'])
def job_answer(job_id):
    original=request.method=='POST'
    if original and (request.get_json() or {}).get('include_secrets') is not True:
        raise ValueError('Confirm the download includes original setup credentials')
    return windows_artifacts.download(db,decode_payload,job_id,original)


@app.post('/api/jobs')
def submit():
    payload = validate(request.get_json())
    if 'data_disks' in payload: raise ValueError('Use the reviewed Windows build wizard to allocate data disks')
    if payload.get('bootstrap') and payload['action']!='provision': raise ValueError('Use the provision action for guest bootstrap')
    if payload.get('bootstrap',{}).get('verify'):
        credential=manager.get('credentials',payload['bootstrap']['credential_id'])
        if credential['platform']!=('windows' if payload.get('kind')=='windows' else 'linux'): raise ValueError('Bootstrap credential platform mismatch')
    if payload['action'] == 'provision' and payload.get('confirmed') is not True:
        raise ValueError('Review and explicitly confirm the provisioning plan before submission')
    key = request.headers.get('Idempotency-Key')
    if key and (len(key) > 128 or not key.isascii()): raise ValueError('Invalid Idempotency-Key')
    job_id = hashlib.sha256(key.encode()).hexdigest()[:32] if key else uuid.uuid4().hex
    with db() as con:
        encoded = encode_payload(payload)
        con.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?)', (job_id, payload['action'], 'queued', time.time(), time.time(), encoded, 'Queued.'))
        old = con.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
        if decode_payload(old['payload']) != payload: return jsonify(error='Idempotency key already used with another payload'), 409
    return jsonify(id=job_id, url=f'/api/jobs/{job_id}'), 202


def resume_guest(row, wait, payload, log):
    cfg=job_queue.job_config(payload)
    record=manager.get('builds',row['id'])
    if (server.current()['id']!=wait['server_id'] or
        any(cfg[k]!=wait[k] or record[k]!=wait[k] for k in ('node','vmid')) or
        record['machine_identity']!=wait['identity']):
        raise ValueError('Guest checkpoint no longer matches the created machine or server.')
    if time.time()>=wait['deadline']:
        raise RuntimeError('Bootstrap verification timed out: '+wait['reason']+'. The VM is retained.')
    credential=manager.get('credentials',cfg['bootstrap']['credential_id'])
    previous=manager.find('connections',str(cfg['node'])+':'+str(cfg['vmid'])) or {}
    if credential['updated']!=wait['credential_revision'] or previous.get('updated')!=wait['connection_revision']:
        raise ValueError('Guest login or connection changed while waiting. Inspect the guest before continuing.')
    try:
        binding=lifecycle.probe_bootstrap(cfg,wait['identity'],credential,previous,log)
    except BootstrapPending as pending:
        if time.time()-wait['last_report']>=60: diagnostics.event('job.waiting',stage=getattr(pending,'phase','guest_readiness'),seconds_remaining=max(0,int(wait['deadline']-time.time())))
        with db() as con: job_queue.postpone(con,wait,redact(str(pending)))
        return False
    if time.time()>=wait['deadline']: raise RuntimeError('Guest verification deadline expired. The VM is retained.')
    verified=manager.prepare('connections',binding,credential)
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        manager.save_verified(con,verified,credential,wait['identity'],previous)
        record.update(bootstrap_verified=True, verified_at=verified['verified_at'],connection_revision=verified['updated'])
        con.execute("UPDATE records SET value=? WHERE kind='builds' AND id=?",(encode_payload(record),row['id']))
        con.execute('DELETE FROM job_waits WHERE job_id=?',(row['id'],))
        con.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(time.time(),row['id']))
    log('Guest login verified and connection saved.')
    if payload['action']=='simple': simple.finish_build(payload['plan'],log,row['id'])
    elif payload['action']=='lifecycle': lifecycle.finish_version_build(payload['plan'],log,row['id'])
    return True


# Alternate between eligible jobs and due probes when both have a backlog.
prefer_probe = True


def run_next_job():
    global prefer_probe
    with server.lock, db() as con:
        row,wait=job_queue.claim(con,decode_payload,time.time(),prefer_probe)
    if row is None: return False
    prefer_probe=wait is None
    password=''
    def log(message):
        message=str(message)
        if password: message=message.replace(password,'[redacted]')
        with db() as con:
            con.execute('UPDATE jobs SET log=substr(log || ?, -150000),updated=? WHERE id=?',
                        ('\n'+redact(message),time.time(),row['id']))
    job_token=diagnostics.job.set(row['id'])
    started=time.monotonic()
    recording=answer_recorder.set(lambda xml,helper:windows_artifacts.save(db,encode_payload,row['id'],xml,helper))
    try:
        payload=decode_payload(row['payload'])
        config=job_queue.job_config(payload)
        if not wait: diagnostics.event('job.started',action=row['action'],vmid=config.get('vmid'),queued_ms=round((time.time()-row['created'])*1000))
        password=config.get('windows',{}).get('admin_password','')
        revision=None
        if not wait and config.get('bootstrap',{}).get('verify'):
            revision=manager.get('credentials',config['bootstrap']['credential_id'])['updated']
        if wait:
            if not resume_guest(row,wait,payload,log): return True
        elif payload['action']=='winrm-setup': winrm_setup.execute(manager,payload,log)
        elif payload['action']=='manage':
            manager.execute(payload['plan'],log)
            if payload['plan']['operation']=='tasks': lifecycle.record_tasks(payload['plan'],row['id'])
        elif payload['action'] in ('lifecycle','simple'):
            (simple if payload['action']=='simple' else lifecycle).execute(payload['plan'],log,row['id'])
        else:
            result=execute(payload,log)
            if payload['action']=='provision': lifecycle.after_provision(payload,result,row['id'],log)
        status='succeeded'
    except WaitForGuest as paused:
        if paused.checkpoint['credential_revision']==revision:
            with db() as con: job_queue.park(con,row['id'],paused.checkpoint)
            diagnostics.event('job.waiting',stage='guest_readiness',vmid=paused.checkpoint['vmid'])
            return True
        log('Guest login changed during creation. Inspect the retained VM before continuing.')
        status='failed'
    except Exception as exc:
        diagnostics.event('job.error',**diagnostics.failure(exc))
        log(f'ERROR: {exc}')
        status='failed'
    finally:
        answer_recorder.reset(recording)
        diagnostics.job.reset(job_token)
    with db() as con:
        con.execute('DELETE FROM job_waits WHERE job_id=?',(row['id'],))
        con.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(status,time.time(),row['id']))
    diagnostics.event('job.finished',job_id=row['id'],status=status,total_ms=round((time.time()-row['created'])*1000),
                      step_ms=round((time.monotonic()-started)*1000))
    return True


def worker():
    while True:
        if not run_next_job(): time.sleep(1)


@app.get('/api/catalog')
def catalog():
    return jsonify(profiles=PROFILES, locales=LOCALES, timezones=TIMEZONES)


@app.post('/api/provision/plan')
def plan():
    """Pure validation/rendering: deliberately no Proxmox client or network access."""
    d = validate(dict(request.get_json(), action='provision'))
    steps = ['Check VM ID, node, storage and bridge before any changes']
    if d['kind'] == 'linux-cloud':
        steps += ['Download ' + PROFILES[d.get('profile', 'ubuntu-24.04')]['label'] + ' and verify published SHA256', 'Create VM disk and cloud-init configuration', 'Convert to template' if d.get('purpose') == 'template' else 'Leave as a VM']
    else:
        steps += ['Upload local ISO (never overwrite an existing file)' if d['source_kind'] == 'local' else 'Use the selected Proxmox ISO']
        if d['kind'] == 'windows': steps += ['Generate and upload a separate Autounattend answer ISO', 'Create a new UEFI/TPM VM; the answer file formats only its new disk 0']
        else: steps += ['Create a new VM for interactive Linux installation']
    steps += ['Start the new VM' if d.get('start') else 'Leave the VM stopped'] if d.get('purpose') != 'template' or d['kind'] != 'linux-cloud' else []
    notes = ['No existing VM will be overwritten.', 'Actual creation happens only after you submit this plan.']
    if d['kind'] == 'windows': notes += ['Image index and display language must match the supplied ISO.', 'Windows Setup may still need a DVD boot key, activation or account prompts.', 'Answer media and Windows cached answer files can contain the administrator password. Remove them and rotate the password before sharing a template.', 'RDP is off unless selected; selected RDP retains NLA and only opens domain/private firewall profiles.']
    if d.get('purpose') == 'template' and d['kind'] != 'linux-cloud': notes += ['Template conversion is a later step: install, generalize, shut down, remove answer media, then convert.']
    if d.get('bootstrap',{}).get('enabled'):
        steps += ['Prepare guest agent and '+('HTTPS WinRM restricted to '+d['bootstrap']['management_cidr'] if d['kind']=='windows' else 'SSH/Python through cloud-init vendor data')]
        if d['bootstrap'].get('verify'):
            credential=manager.get('credentials',d['bootstrap']['credential_id'])
            if credential['platform'] != ('windows' if d['kind']=='windows' else 'linux'): raise ValueError('Bootstrap credential platform mismatch')
            steps += ['Wait for guest agent, retrieve public TLS/SSH trust material and verify guest authentication']
        notes += ['Bootstrap preparation is not guest readiness. Package repositories/selected signed VirtIO drivers must be usable inside the guest.']
    from bootstrap import windows_script,cloud_vendor
    bootstrap_preview=(windows_script(d['bootstrap']) if d['kind']=='windows' else cloud_vendor()) if d.get('bootstrap',{}).get('enabled') else None
    return jsonify(steps=steps, notes=notes, bootstrap_preview=bootstrap_preview, answer_preview=answer_xml(d['windows'], preview=True,bootstrap=d.get('bootstrap')).decode() if d['kind']=='windows' else None)


@app.post('/api/windows/answer-file')
def download_answer():
    data=dict(request.get_json());bootstrap=data.pop('bootstrap',None)
    from bootstrap import options as bootstrap_options
    xml = answer_xml(data,bootstrap=bootstrap_options(bootstrap,'windows'))
    return app.response_class(xml, mimetype='application/xml', headers={'Content-Disposition':'attachment; filename="Autounattend.xml"'})


@app.get('/api/openapi.json')
def openapi():
    return send_from_directory('static', 'openapi.json')


@app.get('/api/docs')
def docs(): return send_from_directory('static', 'api.html')


from management import connection_recovery_targets, register
manager = register(app, db, encode_payload, decode_payload)
from lifecycle import register as register_lifecycle
lifecycle = register_lifecycle(app,manager)
from simple_flow import register as register_simple
simple = register_simple(app,manager,lifecycle)

from server_connection import ServerConnection
import operations
server = ServerConnection(db,encode_payload,decode_payload)
operations.connection_provider = server
from machine import register as register_machine
register_machine(app,manager,lifecycle)
winrm_setup.register(app,manager,lifecycle)


@app.before_request
def connection_boundary():
    if not request.path.startswith('/api/') or request.path in ('/api/health','/api/login','/api/docs','/api/openapi.json'):
        return
    # These authenticated local reads are valid across server replacements.
    # Host-bound plans, inventory and writes still serialize with connection changes.
    if request.endpoint in ('job_answer','check_guest_script') or request.method in ('GET', 'HEAD') and request.endpoint in ('jobs', 'job', 'server_settings', 'current_session'):
        return
    started=time.monotonic()
    server.lock.acquire()
    lock_ms=round((time.monotonic()-started)*1000)
    if lock_ms>=250: diagnostics.event('server.lock_wait',elapsed_ms=lock_ms)
    g.connection_locked = True
    host_write = request.method == 'POST' and (request.path == '/api/jobs' or request.path.startswith('/api/simple/') or
        request.path.startswith('/api/manage/') and request.path.split('/')[3] not in ('credentials','tasks') or
        request.path in ('/api/lifecycle/plan','/api/lifecycle/run','/api/lifecycle/policies','/api/lifecycle/families'))
    if host_write:
        config = server.read()
        if not config.get('connected'): return jsonify(error='Connect a server from the Server page first'),409
        if request.headers.get('X-Proxmate-Server') != config['id']:
            return jsonify(error='Server selection changed. Reload and review the operation for the current server'),409

    # A guided login may take a transport timeout. Keep the selected server pinned
    # while allowing unrelated inventory, job and record requests to proceed.
    if request.endpoint in ('machine_details','machine_connect','machine_backup','machine_remote_access','discover_drive_letters','winrm_review','editions','options') or (request.endpoint=='simple_plan' and request.headers.get('Prefer')!='respond-async'):
        server.machine_requests += 1
        g.machine_request = True
        g.connection_locked = False
        server.lock.release()


@app.teardown_request
def release_connection_boundary(exc):
    if getattr(g,'trace_token',None) is not None:
        diagnostics.trace.reset(g.trace_token)
        g.trace_token=None
    if getattr(g,'machine_request',False):
        with server.lock: server.machine_requests -= 1
        g.machine_request = False
    if getattr(g,'connection_locked',False):
        g.connection_locked = False
        server.lock.release()


@app.get('/api/server')
def server_settings(): return jsonify(server.public())


@app.post('/api/server/test')
def server_test():
    data = request.get_json()
    if not isinstance(data,dict) or data.get('expected_id') != server.read().get('id'):
        raise ValueError('Server settings changed. Reload the page before continuing')
    return jsonify(ok=True,**server.probe(server.validate(data,server.read())))


@app.post('/api/server/connect')
def server_connect(): return jsonify(server.change(request.get_json()))


@app.post('/api/server/disconnect')
def server_disconnect(): return jsonify(server.change(request.get_json(),disconnect=True))


def scheduler():
    while True:
        try:
            with server.lock:
                if server.read().get('connected'):
                    manager.tick()
                    lifecycle.tick()
        except Exception:
            app.logger.error('Schedule tick failed; no immediate retry. Check data volume availability.')
        time.sleep(15)


if __name__ == '__main__':
    from waitress import serve
    admin_password()
    diagnostics.event('service.started',workers=1,http_threads=8)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    serve(app, host='0.0.0.0', port=int(os.getenv('PORT', '8080')), threads=8)
