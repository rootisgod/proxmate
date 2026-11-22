"""Authenticated, versioned dashboard backups. Restores never submit infrastructure work."""
import base64
import hashlib
import hmac
import io
import json
import os
import sqlite3
import threading
import time
from contextlib import nullcontext
from functools import wraps

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from flask import jsonify, request, send_file

import backup_portable
import diagnostics


MAGIC = b'PROXMATE-BACKUP\x00\x01'
LIMIT = 64 * 1024 * 1024
REQUEST_LIMIT = 4 * ((LIMIT + 2) // 3) + 8192
# Version 1 never accepts schema or SQL supplied by a file. Fail closed on schema drift.
TABLES = {
    'jobs': 'id action status created updated payload log',
    'job_dependencies': 'job_id prerequisite',
    'job_waits': 'job_id server_id node vmid identity credential_revision connection_revision deadline next_check last_report reason',
    'lab_jobs': 'job_id run_id',
    'records': 'kind id value',
    'plans': 'id created value',
    'lifecycle_plans': 'id created value',
    'build_credential_defaults': 'platform credential_id',
    'server_connection': 'id value',
    'server_archives': 'id created value',
    'task_runs': 'job_id server_id total settings',
    'task_progress': 'job_id target completed started successor detail',
    'windows_answers': 'job_id value',
}
SECRET_COLUMNS = {'jobs': 'payload', 'records': 'value', 'plans': 'value', 'lifecycle_plans': 'value',
                  'server_connection': 'value', 'server_archives': 'value', 'task_runs': 'settings',
                  'task_progress': 'detail', 'windows_answers': 'value'}
ACTIVE = ('queued', 'running', 'waiting')


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def cipher(passphrase, salt):
    if not isinstance(passphrase, str) or not 12 <= len(passphrase) <= 256:
        raise ValueError('Use a backup passphrase between 12 and 256 characters.')
    key = Scrypt(salt=salt, length=32, n=2**16, r=8, p=1).derive(passphrase.encode())
    return AESGCM(key)


def seal(document, passphrase):
    salt, nonce = os.urandom(16), os.urandom(12)
    body = json_bytes(document)
    if len(body) + len(MAGIC) + 44 > LIMIT:
        raise ValueError('Backup exceeds 64 MiB. Use an offline data-volume backup.')
    return MAGIC + salt + nonce + cipher(passphrase, salt).encrypt(nonce, body, MAGIC)


def open_archive(data):
    encoded = data.get('archive')
    if not isinstance(encoded, str) or len(encoded) > 4 * ((LIMIT + 2) // 3):
        raise ValueError('Choose a Proxmate backup up to 64 MiB.')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ValueError('Invalid backup file encoding.') from None
    if not raw.startswith(MAGIC) or not len(MAGIC) + 44 < len(raw) <= LIMIT:
        raise ValueError('Unrecognized or unsupported Proxmate backup file.')
    i = len(MAGIC)
    aes = cipher(data.get('passphrase'), raw[i:i+16])
    try:
        doc = json.loads(aes.decrypt(raw[i+16:i+28], raw[i+28:], MAGIC))
    except InvalidTag:
        raise ValueError('Cannot open the backup. Check the passphrase and file integrity.') from None
    except (ValueError, RecursionError):
        raise ValueError('Invalid backup contents.') from None
    if (not isinstance(doc, dict) or doc.get('version') != 1 or doc.get('mode') not in ('full', 'portable')
            or type(doc.get('created')) is not int or not isinstance(doc.get('content'), dict)):
        raise ValueError('Unsupported backup contents or version.')
    return doc, hashlib.sha256(raw).digest()


def check_schema(con):
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables != TABLES.keys() or any(
        [row[1] for row in con.execute('PRAGMA table_info('+table+')')] != columns.split()
        for table, columns in TABLES.items()
    ):
        raise ValueError('The database layout differs from backup format 1. Use a compatible version or an offline volume backup.')


def archived_values(archive, transform):
    # A former server retains already-encoded rows nested inside its encrypted record.
    for table in ('records', 'plans', 'lifecycle_plans'):
        for row in archive[table]:
            row['value'] = transform(row['value'])
    return archive


def complete_content(con, manager):
    check_schema(con)
    tables = {}
    size = 0
    for table in TABLES:
        rows = []
        for record in con.execute('SELECT * FROM '+table+' ORDER BY rowid'):
            row = dict(record)
            field = SECRET_COLUMNS.get(table)
            if field:
                row[field] = manager.decode(row[field])
                if table == 'server_archives':
                    archived_values(row[field], manager.decode)
            size += len(json_bytes(row)) + 1
            if size > LIMIT:
                raise ValueError('Backup exceeds 64 MiB. Use an offline data-volume backup.')
            rows.append(row)
        tables[table] = rows
    return tables


def full_summary(tables):
    if set(tables) != TABLES.keys():
        raise ValueError('The complete backup has an incompatible table set.')
    for table, columns in TABLES.items():
        if not isinstance(tables[table], list):
            raise ValueError('Invalid table in complete backup.')
        keys = set(columns.split())
        for row in tables[table]:
            if not isinstance(row, dict) or set(row) != keys:
                raise ValueError('Invalid record layout in complete backup.')
            if table in SECRET_COLUMNS and not isinstance(row[SECRET_COLUMNS[table]], dict):
                raise ValueError('Invalid protected record in complete backup.')
    records = tables['records']
    counts = {label: sum(r['kind'] == kind for r in records)
              for label, kind in [('scripts','tasks'), ('labs','lab-packs'), ('saved builds','guided-recipes'),
                                  ('credentials','credentials'), ('schedules','schedules'), ('backup policies','backup-policies')]}
    counts['jobs'] = len(tables['jobs'])
    counts['unfinished jobs'] = sum(j['status'] in ACTIVE for j in tables['jobs'])
    return counts


def restore_complete(con, manager, tables):
    for table in TABLES:
        con.execute('DELETE FROM '+table)
    for table, columns in TABLES.items():
        if table in ('plans', 'lifecycle_plans', 'job_waits'):
            continue  # New reviews and explicit retries belong to the recovered dashboard.
        fields = columns.split()
        sql = 'INSERT INTO '+table+' ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')'
        for row in tables[table]:
            row = dict(row)
            if table == 'jobs' and row['status'] in ACTIVE:
                row.update(status='interrupted', updated=time.time(), log=row['log']+'\nDashboard restored. Inspect the guest before retrying.')
            if table == 'records' and row['kind'] in ('schedules','backup-policies'):
                row['value'] = dict(row['value'], enabled=False)
            if table == 'server_connection':
                row['value'] = dict(row['value'], connected=False)
            if table == 'server_archives':
                archived_values(row['value'], manager.encode)
            if table in SECRET_COLUMNS:
                field = SECRET_COLUMNS[table]
                row[field] = manager.encode(row[field])
            con.execute(sql, [row[col] for col in fields])


def fingerprint(con, mode):
    result = hashlib.sha256()
    tables = TABLES if mode == 'full' else ('server_connection', 'records')
    for table in tables:
        result.update(table.encode())
        where = " WHERE kind IN ('tasks','lab-packs','lab-pack-index')" if mode == 'portable' and table == 'records' else ''
        for row in con.execute('SELECT * FROM '+table+where+' ORDER BY rowid'):
            result.update(json_bytes(list(row)))
    return result.digest()


def register(app, manager, server, admin_password):
    # Bound concurrent KDF and archive memory without blocking ordinary dashboard requests.
    transfer_lock = threading.Lock()

    def one_transfer(handler):
        @wraps(handler)
        def guarded():
            if not transfer_lock.acquire(blocking=False):
                return jsonify(error='Another dashboard backup or restore is in progress. Retry when it finishes.'), 409
            try:
                return handler()
            finally:
                transfer_lock.release()
        return guarded

    def selected_server(con):
        row = con.execute('SELECT value FROM server_connection WHERE id=1').fetchone()
        return manager.decode(row[0]) if row else {}

    def verify_admin(data):
        given = data.get('admin_password')
        if not isinstance(given, str) or not hmac.compare_digest(given.encode(), admin_password().encode()):
            raise ValueError('Enter the current dashboard administrator password.')

    @app.post('/api/dashboard-backup/export')
    @one_transfer
    def backup_export():
        data = request.get_json()
        if not isinstance(data, dict) or data.get('mode') not in ('full', 'portable'):
            raise ValueError('Choose a complete or portable backup.')
        mode = data['mode']
        if mode == 'full':
            verify_admin(data)
        with manager.db() as con:
            con.execute('BEGIN')
            if mode == 'full':
                content = complete_content(con, manager)
            else:
                content = backup_portable.export(con, manager, selected_server(con).get('id'))
        created = int(time.time())
        raw = seal(dict(version=1, mode=mode, created=created, content=content), data.get('passphrase'))
        diagnostics.event('dashboard.backup_created', mode=mode, size_bytes=len(raw))
        stamp = time.strftime('%Y%m%d-%H%M%SZ', time.gmtime(created))
        return send_file(io.BytesIO(raw), mimetype='application/octet-stream', as_attachment=True,
                         download_name=f'proxmate-{mode}-{stamp}.proxmate')

    @app.post('/api/dashboard-backup/preview')
    @app.post('/api/dashboard-backup/restore')
    @one_transfer
    def backup_restore():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Supply a backup file and passphrase.')
        apply = request.path.endswith('/restore')
        doc, file_hash = open_archive(data)
        mode, content = doc['mode'], doc['content']
        if apply and data.get('confirmed') is not True:
            raise ValueError('Review this backup and confirm the restore.')
        if apply and mode == 'full':
            verify_admin(data)
        try:
            definitions = backup_portable.read_definitions(manager, content) if mode == 'portable' else None
        except (KeyError, TypeError, AttributeError, RecursionError):
            raise ValueError('Invalid portable configuration.') from None
        with server.lock if apply else nullcontext(), manager.db() as con:
            con.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
            if mode == 'full':
                check_schema(con)
            current = selected_server(con)
            destination = current.get('url') or 'Unconfigured dashboard'
            review = hmac.new(app.secret_key.encode(), file_hash + fingerprint(con, mode), hashlib.sha256).hexdigest()
            if apply and data.get('review') != review:
                raise ValueError('The file or destination changed. Preview this backup again.')
            result = dict(mode=mode, created=doc['created'], destination=destination, review=review)
            try:
                if mode == 'full':
                    result['counts'] = full_summary(content)
                    result['source'] = next((r['value']['url'] for r in content['server_connection']), 'Unconfigured dashboard')
                    result['destination_counts'] = dict(con.execute('SELECT kind,count(*) FROM records GROUP BY kind'))
                    result['destination_counts']['jobs'] = con.execute('SELECT count(*) FROM jobs').fetchone()[0]
                    if apply:
                        server.idle(con)
                        restore_complete(con, manager, content)
                else:
                    items, pending = backup_portable.plan(con, manager, current.get('id') if current.get('connected') else None, definitions)
                    result.update(items=items, omitted=content['omitted'])
                    if apply:
                        selected = data.get('selected')
                        if (not isinstance(selected, list) or not selected or not all(isinstance(i,str) for i in selected)
                                or len(set(selected)) != len(selected) or set(selected) - pending.keys()):
                            raise ValueError('Select new items from the current preview.')
                        for ident in selected:
                            con.executemany('INSERT INTO records VALUES (?,?,?)',
                                            [(kind, r['id'], manager.encode(r)) for kind, r in pending[ident]])
                        result['imported'] = len(selected)
            except (KeyError, TypeError, IndexError, OverflowError, sqlite3.IntegrityError, sqlite3.ProgrammingError, RecursionError):
                raise ValueError('Invalid or inconsistent backup records. The destination was kept intact.') from None
        if apply:
            diagnostics.event('dashboard.backup_restored', mode=mode)
        return jsonify(result)
