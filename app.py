"""Single-admin LAN service. Launch one process; jobs are serialized."""
import hashlib
import base64
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, request, session, send_from_directory, g
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
    return path.read_text().strip()


app = Flask(__name__, static_url_path='/static')
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
    con.execute("UPDATE jobs SET status='interrupted', log=log || '\nService restarted. Inspect Proxmox before retrying.', updated=? WHERE status IN ('queued','running')", (time.time(),))

login_attempts = {}
login_lock = threading.Lock()


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
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
    if request.path.startswith('/api/'): response.headers['Cache-Control'] = 'no-store'
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
    if isinstance(exc, HTTPException): return jsonify(error=exc.description), exc.code
    if isinstance(exc, (ValueError, KeyError)): return jsonify(error=redact(str(exc))), 400
    return jsonify(error=redact(str(exc))), 502


@app.get('/')
def index(): return send_from_directory('static', 'index.html')


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
    expected = secret(os.getenv('APP_PASSWORD_FILE', '.env.admin'))
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
        rows = con.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100').fetchall()
    return jsonify([job_view(r) for r in rows])


def job_view(row):
    result = dict(row)
    payload = decode_payload(result.pop('payload'))
    result['target'] = {k: payload[k] for k in ('node', 'vmid', 'source', 'name', 'filename') if k in payload}
    if payload['action'] in ('manage','lifecycle'):
        plan=payload.get('plan',{})
        result['operation']=plan.get('operation',payload['action'])
        result['target']={k:plan[k] for k in ('node','vmid','name','label','archive') if k in plan}
    return result


@app.get('/api/jobs/<job_id>')
def job(job_id):
    with db() as con:
        row = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    return (jsonify(job_view(row)), 200) if row else (jsonify(error='Job not found'), 404)


@app.post('/api/jobs')
def submit():
    payload = validate(request.get_json())
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


def worker():
    while True:
        with server.lock, db() as con:
            row = con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row: con.execute("UPDATE jobs SET status='running',updated=? WHERE id=?", (time.time(), row['id']))
        if not row:
            time.sleep(1); continue
        password = ''
        def log(message):
            message = str(message)
            if password: message = message.replace(password, '[redacted]')
            with db() as con:
                con.execute('UPDATE jobs SET log=substr(log || ?, -150000),updated=? WHERE id=?', ('\n' + redact(message), time.time(), row['id']))
        try:
            payload = decode_payload(row['payload'])
            password = payload.get('windows', {}).get('admin_password', '')
            if payload['action'] == 'manage':
                manager.execute(payload['plan'], log)
                if payload['plan']['operation']=='tasks': lifecycle.record_tasks(payload['plan'],row['id'])
            elif payload['action'] == 'lifecycle':
                password=payload['plan'].get('config',{}).get('windows',{}).get('admin_password','')
                lifecycle.execute(payload['plan'],log,row['id'])
            else:
                result=execute(payload, log)
                if payload['action']=='provision': lifecycle.after_provision(payload,result,row['id'],log)
            status = 'succeeded'
        except Exception as exc:
            log(f'ERROR: {exc}'); status = 'failed'
        with db() as con:
            con.execute('UPDATE jobs SET status=?,updated=? WHERE id=?', (status, time.time(), row['id']))


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


from management import register
manager = register(app, db, encode_payload, decode_payload)
from lifecycle import register as register_lifecycle
lifecycle = register_lifecycle(app,manager)

from server_connection import ServerConnection
import operations
server = ServerConnection(db,encode_payload,decode_payload)
operations.connection_provider = server


@app.before_request
def connection_boundary():
    if not request.path.startswith('/api/') or request.path in ('/api/health','/api/login','/api/docs','/api/openapi.json'):
        return
    server.lock.acquire()
    g.connection_locked = True
    host_write = request.method == 'POST' and (request.path == '/api/jobs' or
        request.path.startswith('/api/manage/') and request.path.split('/')[3] not in ('credentials','tasks') or
        request.path in ('/api/lifecycle/plan','/api/lifecycle/run','/api/lifecycle/policies','/api/lifecycle/families'))
    if host_write:
        config = server.read()
        if not config.get('connected'): return jsonify(error='Connect a server from the Server page first'),409
        if request.headers.get('X-Proxmate-Server') != config['id']:
            return jsonify(error='Server selection changed. Reload and review the operation for the current server'),409


@app.teardown_request
def release_connection_boundary(exc):
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
    secret(os.getenv('APP_PASSWORD_FILE', '.env.admin'))  # Fail startup if unset.
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    serve(app, host='0.0.0.0', port=int(os.getenv('PORT', '8080')), threads=8)
