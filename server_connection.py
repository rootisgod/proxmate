"""One saved Proxmox connection; local encrypted storage and replacement isolation."""
import os
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

import operations


class ServerConnection:
    def __init__(self, db, encode, decode):
        self.db, self.encode, self.decode = db, encode, decode
        self.lock = threading.RLock()
        self.machine_requests = 0
        with db() as con:
            con.execute('CREATE TABLE IF NOT EXISTS server_connection (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS server_archives (id TEXT PRIMARY KEY, created REAL, value TEXT)')
            if not con.execute('SELECT 1 FROM server_connection').fetchone() and os.getenv('PROXMOX_URL'):
                try:
                    password = operations.secret(os.getenv('PROXMOX_PASSWORD_FILE', '.env.password'))
                except OSError:
                    password = ''
                if password:
                    config = self.validate(dict(url=os.environ['PROXMOX_URL'], username=os.getenv('PROXMOX_USER','root@pam'),
                        password=password, verify_tls=os.getenv('PROXMOX_VERIFY_TLS','true').lower()=='true',
                        ssh_user=os.getenv('PROXMOX_SSH_USER','root'), ssh_fingerprint=os.getenv('PROXMOX_SSH_FINGERPRINT','')))
                    config.update(id=uuid.uuid4().hex, connected=True)
                    self.write(con, config)

    def read(self):
        with self.db() as con:
            row = con.execute('SELECT value FROM server_connection WHERE id=1').fetchone()
        return self.decode(row['value']) if row else {}

    def write(self, con, value):
        con.execute('INSERT OR REPLACE INTO server_connection VALUES (1,?)', (self.encode(value),))

    def public(self):
        config = self.read()
        return {**{k:v for k,v in config.items() if k != 'password'}, 'configured':bool(config),
                'has_password':bool(config.get('password')), 'connected':config.get('connected',False)}

    def current(self):
        config = self.read()
        if not config.get('connected'): raise ValueError('Connect a Proxmox server from the Server page first')
        return config

    @staticmethod
    def validate(data, old=None):
        if not isinstance(data,dict): raise ValueError('Supply a JSON object')
        allowed = {'url','username','password','verify_tls','ssh_user','ssh_fingerprint','replace_confirmed','expected_id'}
        if set(data)-allowed: raise ValueError('Unknown server connection option')
        url = data.get('url','')
        if not isinstance(url,str) or len(url)>2048: raise ValueError('Enter an HTTPS Proxmox URL')
        try:
            parsed = urlsplit(url)
            port = parsed.port or 8006
            host = parsed.hostname
            if (parsed.scheme!='https' or not host or parsed.username or parsed.password or
                parsed.path not in ('','/') or parsed.query or parsed.fragment or
                not re.fullmatch(r'[a-zA-Z0-9.:-]+',host) or not 1<=port<=65535): raise ValueError()
        except ValueError: raise ValueError('Use an HTTPS host URL, for example https://192.168.1.10:8006') from None
        host = host.lower()
        url = f'https://{"["+host+"]" if ":" in host else host}:{port}'
        username = data.get('username','root@pam')
        ssh_user = data.get('ssh_user','root')
        fingerprint = data.get('ssh_fingerprint','')
        verify = data.get('verify_tls',True)
        if not isinstance(username,str) or not re.fullmatch(r'[\w.@-]{1,128}',username): raise ValueError('Enter a Proxmox username including its realm')
        if '@' not in username: raise ValueError('Include the username realm, for example root@pam')
        if not isinstance(ssh_user,str) or not re.fullmatch(r'[a-zA-Z_][\w-]{0,63}',ssh_user): raise ValueError('Invalid SSH username')
        if not isinstance(verify,bool): raise ValueError('TLS verification must be true or false')
        if not isinstance(fingerprint,str) or (fingerprint and not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}',fingerprint)): raise ValueError('Enter a verified SHA256 SSH fingerprint or leave it blank')
        password = data.get('password','')
        if not isinstance(password,str) or len(password)>4096: raise ValueError('Invalid password')
        if not password and old and old.get('url')==url and old.get('username')==username:
            password = old.get('password','')
        if not password: raise ValueError('Enter the password for this server and user')
        return dict(url=url,username=username,password=password,verify_tls=verify,ssh_user=ssh_user,ssh_fingerprint=fingerprint)

    def probe(self, config):
        # Authentication plus read-only API calls. Never invoke SSH or change guests.
        try:
            p = operations.Proxmox(config)
            try:
                version = p.call('GET','version')
                nodes = p.call('GET','nodes')
                if not nodes: raise ValueError('No accessible Proxmox nodes')
                return dict(version=version.get('version','unknown'), nodes=[n['node'] for n in nodes])
            finally:
                p.session.close()
        except Exception:
            # Remote HTTP errors can echo draft credentials; never return their body.
            raise ValueError('Connection test failed. Check the address, username/realm, password, certificate trust and node permissions.') from None

    def idle(self, con):
        if self.machine_requests: raise ValueError("Wait for the current machine request before changing the server.")
        if con.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','waiting') LIMIT 1").fetchone():
            raise ValueError('Wait for queued, waiting and running jobs to finish before changing the server connection')
        for row in con.execute("SELECT value FROM records WHERE kind IN ('schedules','backup-policies')"):
            if self.decode(row['value']).get('enabled'):
                raise ValueError('Disable enabled schedules and backup policies before changing the server connection')

    def change(self, data, disconnect=False):
        with self.lock:
            old = self.read()
            if disconnect and not old: raise ValueError('No server is configured')
            if not isinstance(data,dict) or data.get('expected_id') != old.get('id'):
                raise ValueError('Server settings changed. Reload the page before continuing')
            config = old if disconnect else self.validate(data,old)
            replacing = bool(old) and (old['url']!=config['url'] or data.get('replace_confirmed') is True)
            if replacing and data.get('replace_confirmed') is not True:
                raise ValueError('Confirm replacement to archive settings belonging to the previous server')
            with self.db() as con:
                con.execute('BEGIN IMMEDIATE')
                self.idle(con)
                if not disconnect: self.probe(config)
                if replacing:
                    archive = {'server':old}
                    for table in ('records','plans','lifecycle_plans'):
                        where = " WHERE kind NOT IN ('credentials','tasks')" if table=='records' else ''
                        archive[table] = [dict(r) for r in con.execute('SELECT * FROM '+table+where)]
                        con.execute('DELETE FROM '+table+where)
                    con.execute('INSERT INTO server_archives VALUES (?,?,?)',(uuid.uuid4().hex,time.time(),self.encode(archive)))
                config = dict(config,id=uuid.uuid4().hex if replacing or not old else old['id'],connected=not disconnect)
                self.write(con,config)
            return self.public()
