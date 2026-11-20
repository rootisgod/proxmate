"""Targeted machine details and authenticated, guest-agent-assisted connection setup."""
import ipaddress
import ssl
import socket

from flask import jsonify, request

import management
import operations
from guest_tasks import run_guest
from job_queue import target_ids
from lifecycle import machine_identity, source_vmid, vm_path


def guest_addresses(p, target):
    data = p.call('GET', vm_path(target)+'/agent/network-get-interfaces')
    addresses = {}
    for interface in data.get('result', []):
        for entry in interface.get('ip-addresses', []):
            try:
                ip = ipaddress.ip_address(entry['ip-address'])
                host = management.address(str(ip))
            except (KeyError, ValueError):
                continue
            addresses.setdefault(host, dict(host=host, interface=interface.get('name','')))
    return list(addresses.values())


def discover(p, target, resource, config):
    reported = {}
    ips = []
    if resource.get('status')=='running':
        try: ips = guest_addresses(p, target)
        except RuntimeError: pass
        try: reported = p.call('GET', vm_path(target)+'/agent/get-osinfo').get('result',{})
        except RuntimeError: pass
    guest_id = (reported.get('id') or '').lower()
    ostype = config.get('ostype','')
    if guest_id in ('mswindows','windows','win32'): platform='windows'
    elif guest_id in ('ubuntu','debian','fedora','rhel','centos','alpine','arch','linux','rocky','almalinux','opensuse','sles'): platform='linux'
    elif ostype.startswith('win') or ostype in ('w2k','w2k3','w2k8','wvista','wxp'): platform='windows'
    else: platform='linux' if ostype in ('l24','l26') else None
    return dict(addresses=ips, os=reported.get('pretty-name') or reported.get('name'), platform=platform)


def stable_identity(config):
    return machine_identity(config) if config.get('smbios1') or config.get('vmgenid') else None


def verify_identity(p, target, expected):
    config=p.call('GET',vm_path(target)+'/config')
    if not expected or config.get('template') or stable_identity(config)!=expected:
        raise ValueError('Machine identity changed or is unavailable. Refresh and review this machine.')


def scoped_jobs(manager, target):
    result=[]
    with manager.db() as con:
        # Job history is retained across server replacements. Only display the current era.
        cutoff=con.execute('SELECT COALESCE(max(created),0) FROM server_archives').fetchone()[0]
        rows=con.execute('SELECT id,action,status,created,payload FROM jobs WHERE created>=? ORDER BY created DESC LIMIT 100',(cutoff,))
        for row in rows:
            payload=manager.decode(row['payload'])
            if target['vmid'] in target_ids(payload):
                result.append(dict(id=row['id'],operation=payload.get('plan',{}).get('operation',row['action']),
                                   status=row['status'],created=row['created']))
                if len(result)==5: break
    return result


def login_failure(binding,credential):
    if binding['platform']=='linux': return 'SSH login failed. Check the service, host fingerprint and credentials.'
    try:
        ctx=ssl.create_default_context(cadata=binding.get('ca_pem') or credential.get('ca_pem') or None)
        with socket.create_connection((binding['host'],binding['port']),timeout=4) as sock:
            with ctx.wrap_socket(sock,server_hostname=binding['host']): pass
    except ssl.SSLError: return 'WinRM TLS verification failed. Check the listener certificate, trusted CA and address.'
    except OSError: return 'The WinRM endpoint is unreachable. Check the address, service and firewall scope.'
    return 'WinRM HTTPS is reachable. The authenticated runtime check failed. Check the guest login and remote permissions.'


def register(app, manager, lifecycle):
    @app.post('/api/simple/machine')
    def machine_details():
        target=management.target(request.get_json())
        p=operations.Proxmox()
        resource,config=lifecycle.guest(p,target,False)
        identity=stable_identity(config)
        discovery=discover(p,target,resource,config)
        existing=manager.find('connections',management.target_id(target)) or {}
        credential=manager.find('credentials',existing.get('credential_id')) or {}
        verified=(identity and existing.get('machine_identity')==identity and
                  credential.get('updated')==existing.get('credential_revision'))
        return jsonify(**target, identity=identity,name=resource.get('name') or config.get('name'),
            status=resource.get('status'),cores=resource.get('maxcpu'),memory=resource.get('maxmem'),**discovery,
            connection={k:existing[k] for k in ('platform','host','port','credential_id','updated') if k in existing},
            verified_at=existing.get('verified_at') if verified else None,
            logins=[{k:c[k] for k in ('id','name','platform')} for c in manager.all('credentials')],
            jobs=scoped_jobs(manager,target))

    @app.post('/api/simple/machine/backup')
    def machine_backup():
        data=request.get_json()
        target=management.target(data)
        p=operations.Proxmox()
        _,config=lifecycle.guest(p,target,False)
        if stable_identity(config)!=data.get('identity'): raise ValueError('Machine changed. Refresh its details.')
        latest=None
        for store in p.call('GET',f"nodes/{target['node']}/storage",content='backup',enabled=1):
            if not store.get('active') or 'backup' not in store.get('content','').split(','): continue
            path=f"nodes/{target['node']}/storage/{management.word(store['storage'])}/content"
            for entry in p.call('GET',path,content='backup',vmid=target['vmid']):
                if source_vmid(entry)==target['vmid'] and (latest is None or entry['ctime']>latest['ctime']):
                    latest={k:entry.get(k) for k in ('volid','ctime','size')}
        return jsonify(latest=latest)

    @app.post('/api/simple/machine/connect')
    def machine_connect():
        data=request.get_json()
        target=management.target(data)
        p=operations.Proxmox()
        resource,config=lifecycle.guest(p,target,False)
        identity=stable_identity(config)
        if not identity or identity!=data.get('identity') or resource.get('status')!='running':
            raise ValueError('Refresh the machine and confirm it is running before connecting.')
        discovery=discover(p,target,resource,config)
        previous=manager.find('connections',management.target_id(target)) or {}
        if data.get('connection_revision')!=previous.get('updated'):
            raise ValueError('Connection settings changed. Refresh and retry.')
        platform=data.get('platform')
        if platform not in ('linux','windows') or discovery['platform'] not in (None,platform):
            raise ValueError('Choose the operating system reported by this machine.')
        host=management.address(data.get('host'))
        reported={i['host'] for i in discovery['addresses']}
        if host not in reported and host!=previous.get('host'):
            raise ValueError('Choose an address currently reported by this guest agent.')
        new_login=not data.get('credential_id')
        if new_login:
            draft=data.get('credential') or {}
            credential=manager.prepare('credentials',dict(name=(resource.get('name') or str(target['vmid']))[:64]+' login',
                platform=platform,username=draft.get('username'),password=draft.get('password'),private_key=draft.get('private_key')))
        else:
            credential=manager.get('credentials',data['credential_id'])
        if credential['platform']!=platform: raise ValueError('Choose a login for this operating system.')
        binding=dict(target,host=host,platform=platform,credential_id=credential['id'],verify_tls=True,
                     port=previous.get('port') if previous.get('platform')==platform else (22 if platform=='linux' else 5986),
                     become=previous.get('become',True))
        if not set(map(str,management.endpoint_addresses(binding))).issubset(reported):
            raise ValueError('Address no longer matches this guest. Refresh its network details.')
        # Keep the existing Windows trust anchor, including a system-trusted listener.
        if previous and previous['platform']!=platform:
            raise ValueError('The saved connection has a different guest OS. Review its manual settings.')
        trust={}
        if platform=='linux' or not (previous or credential.get('ca_pem')):
            try: trust=management.agent_trust(p,vm_path(target),platform)
            except RuntimeError:
                if platform=='linux' and not previous.get('fingerprint'):
                    raise ValueError('Read the SSH host fingerprint in the VM console and enter it under Manual settings.') from None
        approved=manager.find('winrm-trust',management.target_id(target)) if platform=='windows' else None
        if approved and approved['identity']==identity and not (previous.get('ca_pem') or credential.get('ca_pem')):
            trust={'ca_pem':approved['ca_pem']}
        management.check_saved_trust(previous,trust,identity)
        binding.update(trust)
        if platform=='linux' and not binding.get('fingerprint'): binding['fingerprint']=previous.get('fingerprint')
        binding=manager.prepare('connections',binding,credential)
        verify_identity(p,target,identity)
        try: run_guest(binding,credential,[],lambda _msg:None,readiness=True)
        except Exception:
            raise ValueError(login_failure(binding,credential)) from None
        verify_identity(p,target,identity)
        with manager.db() as con:
            con.execute('BEGIN IMMEDIATE')
            manager.save_verified(con,binding,credential,identity,previous,new_login=new_login)
        return jsonify(verified_at=binding['verified_at'])
