"""Targeted machine details and authenticated, guest-agent-assisted connection setup."""
import ipaddress
import re
import shlex
import ssl
import socket

from flask import jsonify, request

import management
import operations
import diagnostics
import winrm_setup
from guest_tasks import run_guest
from job_queue import target_ids
from lifecycle import machine_identity, source_vmid, vm_path


RDP_LISTENER = r"""
$ErrorActionPreference='Stop'
$terminal='HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
$policy=(Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services' -ErrorAction SilentlyContinue).fDenyTSConnections
if ($null -eq $policy) { $policy=Get-ItemPropertyValue -Path $terminal -Name fDenyTSConnections }
if ($policy -ne 0 -or (Get-Service TermService).Status -ne 'Running') { 'disabled'; return }
Get-ItemPropertyValue -Path "$terminal\WinStations\RDP-Tcp" -Name PortNumber
"""


def guest_addresses(p, target, config=None):
    macs = set()
    for key, value in (config or {}).items():
        if re.fullmatch(r'net\d+', key):
            macs.update(re.findall(r'(?:^|=)((?:[0-9a-f]{2}:){5}[0-9a-f]{2})(?:,|$)', str(value).lower()))
    return interface_addresses(p.call('GET', vm_path(target)+'/agent/network-get-interfaces'), macs)


def interface_addresses(data, vm_macs=()):
    addresses = {}
    for interface in data.get('result', []):
        for entry in interface.get('ip-addresses', []):
            try:
                ip = ipaddress.ip_address(entry['ip-address'])
                host = management.address(str(ip))
            except (KeyError, ValueError):
                continue
            address = addresses.setdefault(host, dict(host=host, interface=interface.get('name','')))
            if (interface.get('hardware-address') or '').lower() in vm_macs:
                address['vm_nic'] = True
    return list(addresses.values())


def discover(p, target, resource, config):
    reported = {}
    ips = []
    if resource.get('status')=='running':
        try: ips = guest_addresses(p, target, config)
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


def build_login(manager, target, identity, previous, platform):
    if platform != 'windows' or not identity or previous.get('machine_identity') == identity:
        return None
    candidates = set()
    for build in manager.all('builds'):
        if (build.get('node'), build.get('vmid'), build.get('machine_identity')) != (target['node'], target['vmid'], identity):
            continue
        with manager.db() as con:
            row = con.execute('SELECT payload FROM jobs WHERE id=?', (build['id'],)).fetchone()
        if not row: continue
        job = manager.decode(row['payload'])
        config = job.get('plan', {}).get('config', {})
        if (job.get('action') == 'simple' and job.get('server_id') == operations.connection_provider.current()['id']
                and config.get('node') == target['node'] and config.get('vmid') == target['vmid']):
            login = manager.find('credentials', config.get('bootstrap', {}).get('credential_id'))
            if login and login['platform'] == 'windows':
                candidates.add(login['id'])
    return next(iter(candidates)) if len(candidates) == 1 else None


def tls_peer(binding, ca):
    ctx = ssl.create_default_context(cadata=ca or None)
    with socket.create_connection((binding['host'], binding['port']), timeout=4) as sock:
        with ctx.wrap_socket(sock, server_hostname=binding['host']) as peer:
            return peer.getpeercert(binary_form=True)


def verified_guest_cert(p, target, identity, host):
    path = vm_path(target)
    with p.verified_reader() as read:
        def check_identity():
            config = read(path + '/config')
            if config.get('template') or stable_identity(config) != identity:
                raise ValueError('The selected VM changed. Refresh its details.')
        check_identity()
        addresses = interface_addresses(read(path + '/agent/network-get-interfaces'))
        if host not in {item['host'] for item in addresses}:
            raise ValueError('Select an IP address currently reported by this VM.')
        pem = read(path + '/agent/file-read', file=r'C:\ProgramData\Proxmate\winrm.pem').get('content', '')
        if len(pem) > 20000 or pem.count('-----BEGIN CERTIFICATE-----') != 1:
            raise ValueError('Guest listener certificate unavailable.')
        check_identity()
        return pem


def windows_trust(p, target, identity, binding, credential, previous, replaced, build_credential):
    ca = binding.get('ca_pem') or credential.get('ca_pem')
    try:
        if not replaced:
            tls_peer(binding, ca)
            return
    except ssl.SSLError:
        pass
    except OSError:
        raise ValueError('WinRM is unreachable. Open Check WinRM setup and check the guest firewall.') from None
    if (previous.get('credential_id') == credential['id'] and not previous.get('machine_identity')
            and credential['id'] != build_credential):
        raise ValueError("Proxmate cannot tie this older login to the current VM. Choose another login or enter this VM's credentials.")
    try:
        pem = verified_guest_cert(p, target, identity, binding['host'])
    except operations.ManagementTrustError:
        raise
    except Exception as exc:
        diagnostics.event('winrm.guest_discovery_failed', **target, **diagnostics.failure(exc))
        raise ValueError('Proxmox could not confirm this VM, its address and its exported Windows listener. '
                         'Refresh its network details and check the guest agent. For a custom listener, use Manual settings.') from None
    try:
        # A configured credential CA must still validate this exact guest listener.
        peer = tls_peer(binding, credential.get('ca_pem') or pem)
        if peer != ssl.PEM_cert_to_DER_cert(pem):
            raise ValueError('The listener differs from the guest export.')
    except Exception as exc:
        diagnostics.event('winrm.trust_refresh_failed', **target, **diagnostics.failure(exc))
        raise ValueError('The Windows listener could not be verified for this address. On a Proxmate-prepared VM, '
                         'rerun the Windows bootstrap helper for its current address and then refresh the connection.') from None
    if credential.get('ca_pem'):
        binding.pop('ca_pem', None)
    else:
        binding['ca_pem'] = pem
    diagnostics.event('winrm.guest_listener_verified', **target)


def register(app, manager, lifecycle):
    @app.post('/api/simple/machine/remote-access')
    def machine_remote_access():
        data=request.get_json()
        target=management.target(data)
        expected=data.get('identity')
        if 'job_id' in data:
            with manager.db() as con:
                row=con.execute('SELECT status,payload FROM jobs WHERE id=?',(data['job_id'],)).fetchone()
            job=manager.decode(row['payload']) if row else {}
            build=manager.find('builds',data['job_id'])
            if (not row or row['status']!='succeeded' or job.get('action')!='simple' or
                    job.get('server_id')!=request.headers['X-Proxmate-Server'] or job['plan'].get('purpose')!='vm' or
                    not build or management.target(build)!=target):
                raise ValueError('This build has no finished machine on the selected server.')
            expected=build['machine_identity']
        p=operations.Proxmox()
        resource,cfg=lifecycle.guest(p,target,False)
        if not expected or expected!=stable_identity(cfg) or resource.get('status')!='running':
            raise ValueError('The machine changed or stopped. Refresh it before connecting.')
        details=discover(p,target,resource,cfg)
        binding=manager.find('connections',management.target_id(target)) or {}
        if binding.get('machine_identity')!=expected or binding.get('platform')!=details['platform']: binding={}
        ips=[a['host'] for a in details['addresses']]
        candidates=[a['host'] for a in details['addresses'] if a.get('vm_nic')] or ips
        candidates=[ip for ip in candidates if ':' not in ip] or candidates
        host=binding['host'] if binding.get('host') in ips else candidates[0] if len(candidates)==1 else None
        if not host: raise ValueError('Refresh the connection and select a current guest address.')
        if details['platform']=='windows':
            port=winrm_setup.agent_script(p,target,expected,RDP_LISTENER)
            if port=='disabled': return jsonify(kind=None)
            port=management.number(port,1,65535)
            result=dict(kind='rdp',host=host,port=port)
        elif details['platform']=='linux':
            credential=manager.find('credentials',binding.get('credential_id')) or {}
            port=binding.get('port',22)
            command=['ssh']
            if port!=22: command+=['-p',str(port)]
            if credential.get('username'): command+=['-l',credential['username']]
            result=dict(kind='ssh',command=shlex.join([*command,host]),host=host,port=port)
        else: return jsonify(kind=None)
        management.endpoint_addresses(result)
        if host not in {entry['host'] for entry in guest_addresses(p,target)}:
            raise ValueError('The guest address changed. Refresh the machine before connecting.')
        verify_identity(p,target,expected)
        return jsonify(**result)

    @app.post('/api/simple/drive-letters')
    def discover_drive_letters():
        targets=management.targets(request.get_json().get('targets'))
        p=operations.Proxmox()
        occupied=set()
        for target in targets:
            resource,cfg=lifecycle.guest(p,target,False)
            if resource.get('status')!='running': raise ValueError('Start the selected machines to discover drive letters.')
            occupied.update(winrm_setup.used_drive_letters(p,target,cfg))
        return jsonify(occupied=sorted(occupied))

    @app.post('/api/simple/machine')
    def machine_details():
        data=request.get_json()
        target=management.target(data)
        p=operations.Proxmox()
        resource,config=lifecycle.guest(p,target,False)
        if 'reviewed_identity' in data and data['reviewed_identity']!=manager.identity(config):
            raise ValueError('This VM changed since the failed job. Open the current VM from Operations to review its connection.')
        identity=stable_identity(config)
        discovery=discover(p,target,resource,config)
        existing=manager.find('connections',management.target_id(target)) or {}
        suggested=existing.get('credential_id')
        if discovery['platform']=='windows' and existing.get('machine_identity')!=identity:
            current_login=build_login(manager,target,identity,existing,'windows')
            suggested=current_login or (None if existing.get('machine_identity') else suggested)
        credential=manager.find('credentials',existing.get('credential_id')) or {}
        verified=(identity and existing.get('machine_identity')==identity and
                  credential.get('updated')==existing.get('credential_revision'))
        return jsonify(**target, identity=identity,name=resource.get('name') or config.get('name'),
            status=resource.get('status'),cores=resource.get('maxcpu'),memory=resource.get('maxmem'),**discovery,
            connection={k:existing[k] for k in ('platform','host','port','credential_id','updated') if k in existing},
            suggested_credential_id=suggested,
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
        if previous and previous['platform']!=platform:
            raise ValueError('The saved connection has a different guest OS. Review its manual settings.')
        trust={}
        if platform=='linux':
            try: trust=management.agent_trust(p,vm_path(target),platform)
            except RuntimeError:
                if not previous.get('fingerprint'):
                    raise ValueError('Read the SSH host fingerprint in the VM console and enter it under Manual settings.') from None
            management.check_saved_trust(previous,trust,identity)
        else:
            current_login=build_login(manager,target,identity,previous,platform)
            replaced=bool(previous.get('machine_identity') and previous['machine_identity']!=identity
                          or current_login and previous and current_login!=previous.get('credential_id'))
            if replaced and credential['id']==previous.get('credential_id') and credential['id']!=current_login:
                raise ValueError('This saved login belongs to an earlier connection. Refresh and choose a login for the current VM.')
            approved=manager.find('winrm-trust',management.target_id(target))
            if approved and approved['identity']==identity and not (previous.get('ca_pem') or credential.get('ca_pem')):
                trust={'ca_pem':approved['ca_pem']}
        binding.update(trust)
        if platform=='linux' and not binding.get('fingerprint'): binding['fingerprint']=previous.get('fingerprint')
        binding=manager.prepare('connections',binding,credential)
        if platform=='windows':
            windows_trust(p,target,identity,binding,credential,previous,replaced,current_login)
        verify_identity(p,target,identity)
        try: run_guest(binding,credential,[],lambda _msg:None,readiness=True)
        except Exception:
            if platform=='windows':
                raise ValueError('Windows listener verified. Login failed. Check the guest username, password and remote permissions.') from None
            raise ValueError('SSH login failed. Check the service, host fingerprint and credentials.') from None
        verify_identity(p,target,identity)
        with manager.db() as con:
            con.execute('BEGIN IMMEDIATE')
            manager.save_verified(con,binding,credential,identity,previous,new_login=new_login)
        return jsonify(verified_at=binding['verified_at'])
