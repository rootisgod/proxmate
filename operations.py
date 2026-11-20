"""Proxmox operations. All writes execute in the single durable job worker."""
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, quote

import paramiko
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
import yaml
import diagnostics
from provisioning import PROFILES, linux_options, windows_options, answer_iso, checksum_for
from bootstrap import options as bootstrap_options, cloud_vendor, cloud_seed


def secret(path):
    value = Path(path).read_text().strip()
    if '=' in value and value.split('=', 1)[0].upper() in ('PASSWORD', 'PROXMOX_PASSWORD', 'PVE_PASSWORD'):
        value = value.split('=', 1)[1].strip().strip('\"\'')
    return value


connection_provider = None


def configured_url():
    return connection_provider.read().get('url','') if connection_provider else os.getenv('PROXMOX_URL','')


class ManagementTrustError(ValueError):
    pass


class Proxmox:
    def __init__(self, config=None):
        if config is None:
            if connection_provider is None: raise ValueError('No Proxmox connection configured')
            config = connection_provider.current()
        self.url = config['url']
        self.password = config['password']
        self.ssh_user = config['ssh_user']
        self.ssh_fingerprint = config['ssh_fingerprint']
        self.session = requests.Session()
        self.session.verify = config['verify_tls']
        if not self.session.verify:
            requests.packages.urllib3.disable_warnings()
        data = self.call('POST', 'access/ticket', username=config['username'], password=self.password)
        self.session.cookies.set('PVEAuthCookie', data['ticket'])
        self.session.headers['CSRFPreventionToken'] = data['CSRFPreventionToken']

    def call(self, method, path, **data):
        start=time.monotonic()
        # Paths and upstream error bodies can contain tickets, filenames or guest commands.
        leaf=path.rsplit('/',1)[-1]
        known={'ticket','nodes','resources','nextid','storage','content','network','config','status','current','qemu',
               'start','shutdown','clone','template','get-osinfo','get-host-name','file-read','exec','exec-status','network-get-interfaces'}
        operation=leaf if leaf in known else 'other'
        try:
            r = self.session.request(method, self.url + '/api2/json/' + path,
                                     params=data if method == 'GET' else None,
                                     data=data if method != 'GET' else None, timeout=60, allow_redirects=False)
        except requests.RequestException as exc:
            diagnostics.event('proxmox.error',operation=operation,method=method,elapsed_ms=round((time.monotonic()-start)*1000),**diagnostics.failure(exc))
            raise
        elapsed=round((time.monotonic()-start)*1000)
        if not 200 <= r.status_code < 300 or elapsed>=1000:
            diagnostics.event('proxmox.response',operation=operation,method=method,status=r.status_code,elapsed_ms=elapsed)
        if not 200 <= r.status_code < 300:
            raise RuntimeError(f'Proxmox HTTP {r.status_code}: {r.text[:1500]}')
        return r.json()['data']

    def wait(self, node, upid, log, timeout=7200):
        if not isinstance(upid, str) or not upid.startswith('UPID:'):
            return
        log('Proxmox task: ' + upid)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            state = self.call('GET', f'nodes/{node}/tasks/{quote(upid, safe="")}/status')
            if state['status'] == 'stopped':
                if state.get('exitstatus') != 'OK':
                    raise RuntimeError('Proxmox task failed: ' + state.get('exitstatus', 'unknown'))
                log('Proxmox task completed successfully.')
                return
            time.sleep(2)
        raise RuntimeError('Timed out waiting. The task may still be running on Proxmox; inspect before retrying.')

    def resources(self):
        return self.call('GET', 'cluster/resources')

    @contextmanager
    def verified_reader(self):
        """Read guest identity material through a verified management channel."""
        if self.session.verify:
            yield lambda path, **params: self.call('GET', path, **params)
            return
        try:
            client = self.ssh()
        except (ValueError, paramiko.SSHException, OSError):
            raise ManagementTrustError('On the Server page, verify the Proxmox HTTPS connection or check its saved SSH fingerprint and login. Then refresh this VM.') from None
        with client:
            def read(path, **params):
                args = ['pvesh', 'get', '/' + path, '--output-format', 'json']
                for name, value in params.items():
                    args.extend(['--' + name, value])
                _, stdout, _ = client.exec_command(shlex.join(args), timeout=12)
                data = stdout.read(131073)
                if len(data) > 131072 or stdout.channel.recv_exit_status() != 0:
                    raise RuntimeError('Proxmox could not read the guest details.')
                return json.loads(data)
            yield read

    def ssh(self):
        expected = self.ssh_fingerprint
        if not expected:
            raise ValueError('Add a verified SSH host fingerprint on the Server page before using SSH operations.')
        class PinnedPolicy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                actual = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                if actual != expected:
                    raise paramiko.SSHException('Proxmox SSH host fingerprint mismatch')
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedPolicy())
        client.connect(urlparse(self.url).hostname, username=self.ssh_user,
                       password=self.password, look_for_keys=False, allow_agent=False, timeout=15)
        return client

    def shell(self, script, log):
        with self.ssh() as client:
            _, stdout, stderr = client.exec_command('bash -se', timeout=7200)
            stdout.channel.set_combine_stderr(True)
            stdout.channel.sendall(script.encode())
            stdout.channel.shutdown_write()
            for line in stdout:
                log(line.rstrip())
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError('Remote operation failed; see job log. Partial resources are retained.')


def upload_generated(p,node,storage,path,log):
    with path.open('rb') as stream:
        enc=MultipartEncoder(fields={'content':'iso','filename':(path.name,stream,'application/octet-stream')})
        response=p.session.post(p.url+f'/api2/json/nodes/{node}/storage/{storage}/upload',data=enc,
            headers={'Content-Type':enc.content_type},timeout=120,allow_redirects=False)
        if not 200<=response.status_code<300: raise RuntimeError('Generated setup media upload failed')
        p.wait(node,response.json()['data'],log)
    return storage+':iso/'+path.name


def inventory(*, include_backups=True, only_node=None):
    p = Proxmox()
    nodes = p.call('GET', 'nodes')
    details = {}
    for n in nodes:
        node = n['node']
        if n['status'] != 'online' or (only_node and node!=only_node):
            continue
        stores = p.call('GET', f'nodes/{node}/storage')
        isos, backups = [], []
        for s in stores:
            if 'iso' in s.get('content', '').split(',') and s.get('active'):
                isos += p.call('GET', f'nodes/{node}/storage/{s["storage"]}/content', content='iso')
            if include_backups and 'backup' in s.get('content', '').split(',') and s.get('active'):
                backups += p.call('GET', f'nodes/{node}/storage/{s["storage"]}/content', content='backup')
        details[node] = {'storage': stores, 'network': p.call('GET', f'nodes/{node}/network'), 'isos': isos, 'backups': backups}
    root = Path(os.getenv('ISO_DIR', 'D:/ISOs'))
    return {'nodes': nodes, 'resources': p.resources(), 'details': details,
            'next_id': int(p.call('GET', 'cluster/nextid')),
            'local_isos': [{'name': f.name, 'bytes': f.stat().st_size,'mtime': f.stat().st_mtime_ns} for f in sorted(root.glob('*')) if f.suffix.lower()=='.iso' and f.is_file()]}


ACTIONS = {'provision', 'finish-setup', 'linux-template', 'installer', 'clone', 'backup', 'convert-template', 'ansible', 'upload-iso', 'start'}


def upload_iso_name(filename):
    if not isinstance(filename,str) or not re.fullmatch(r'[A-Za-z0-9_. -]+\.iso',filename,re.I):
        raise ValueError('Choose an ISO filename without directory components')
    return filename[:-4].replace(' ','_')+'.iso'


def validate(data):
    if not isinstance(data, dict) or data.get('action') not in ACTIONS:
        raise ValueError('Unknown action')
    d = dict(data)
    if 'bootstrap' in d:
        kind = d.get('kind') or ('linux-cloud' if d['action']=='linux-template' else d.get('os'))
        d['bootstrap'] = bootstrap_options(d['bootstrap'],kind)
        if d['bootstrap'].get('verify') and (not d.get('start') or d.get('purpose',d.get('outcome'))=='template'):
            raise ValueError('Automatic bootstrap verification requires a VM purpose and start enabled; verify template clones separately')
    if any(str(key).startswith('_') for key in d): raise ValueError('Internal options are not accepted')
    if d['action'] == 'provision':
        if d.get('kind') not in ('windows', 'linux-cloud', 'linux-iso'):
            raise ValueError('Choose Windows, Linux cloud image or Linux ISO')
        if d.get('purpose', 'vm') not in ('vm', 'template'): raise ValueError('Choose a VM or template purpose')
        translated = dict(d, action='linux-template' if d['kind'] == 'linux-cloud' else 'installer')
        if d['kind'] == 'linux-cloud':
            translated['outcome'] = d.get('purpose', 'vm')
            if not d.get('ssh_key'): raise ValueError('Add an SSH public key for cloud-image login')
        else:
            if d.get('source_kind') not in ('local', 'proxmox'): raise ValueError('Choose a media source')
            if d['source_kind'] == 'local':
                validate(dict(action='upload-iso', node=d.get('node'), storage=d.get('iso_storage'), filename=d.get('filename')))
                translated['iso'] = f'{d["iso_storage"]}:iso/{upload_iso_name(d["filename"])}'
            translated['os'] = 'windows' if d['kind'] == 'windows' else 'linux'
        checked = validate(translated)
        checked['action'] = 'provision'
        if d['kind'] == 'windows':
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', str(d.get('iso_storage', ''))): raise ValueError('Choose ISO storage for answer media')
            checked['windows'] = windows_options(d.get('windows', {}))
            if not checked['windows']['accept_eula'] or not checked['windows']['wipe_disk']:
                raise ValueError('Confirm Windows licensing and formatting the new VM disk')
            if checked.get('disk', 64) < 64 or checked.get('memory', 4096) < 4096:
                raise ValueError('Windows wizard requires at least 64 GB disk and 4096 MB memory')
        return checked
    for flag in ('start', 'prepared'):
        if flag in d and not isinstance(d[flag], bool):
            raise ValueError(f'{flag} must be a boolean')
    for field in ('node', 'storage', 'bridge', 'name', 'backup_storage', 'username'):
        if field in d and (not isinstance(d[field], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', d[field])):
            raise ValueError(f'Invalid {field}')
    for field, low, high in [('vmid', 100, 999999999), ('source', 100, 999999999), ('cores', 1, 128), ('memory', 512, 1048576), ('disk', 8, 4096)]:
        if field in d:
            if isinstance(d[field], bool) or not str(d[field]).isdigit() or not low <= int(d[field]) <= high:
                raise ValueError(f'Invalid {field}: expected {low}–{high}')
            d[field] = int(d[field])
    required = {'linux-template': ['node', 'vmid', 'name', 'storage', 'bridge'],
                'installer': ['node', 'vmid', 'name', 'storage', 'bridge', 'iso'],
                'clone': ['node', 'source', 'vmid', 'name', 'storage'],
                'backup': ['node', 'vmid', 'backup_storage'], 'convert-template': ['node', 'vmid'],
                'start': ['node', 'vmid'], 'upload-iso': ['node', 'storage', 'filename'],
                'finish-setup': ['node', 'vmid'],
                'ansible': ['node', 'playbook']}
    for key in required[d['action']]:
        if key not in d or d[key] == '':
            raise ValueError(f'{key} is required')
    if d['action'] == 'installer':
        if d.get('os', 'linux') not in ('linux', 'windows'):
            raise ValueError('os must be linux or windows')
        if not isinstance(d['iso'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+:iso/[A-Za-z0-9_. -]+\.iso', d['iso'], re.I):
            raise ValueError('Select an ISO already on Proxmox')
    if d['action'] == 'linux-template': linux_options(d)
    if d['action'] == 'upload-iso':
        upload_iso_name(d['filename'])
    if d['action'] == 'ansible':
        try:
            parsed = yaml.safe_load(d['playbook']) if isinstance(d['playbook'], str) else None
        except yaml.YAMLError as exc:
            raise ValueError('Invalid playbook YAML') from exc
        if not isinstance(d['playbook'], str) or len(d['playbook']) > 100000 or not isinstance(parsed, list) or not parsed:
            raise ValueError('Playbook must be a YAML list, up to 100 KB')
        if d.get('target', 'custom') not in ('custom', 'proxmox'):
            raise ValueError('Ansible target must be custom or proxmox')
        if d.get('target') == 'proxmox' and d.get('host_confirmed') is not True:
            raise ValueError('Explicitly confirm running this playbook on the Proxmox hypervisor')
        if d.get('target', 'custom') == 'custom' and (not isinstance(d.get('inventory'), str) or not d['inventory'].strip() or len(d['inventory']) > 20000):
            raise ValueError('Inventory must be text, up to 20 KB')
    if d['action'] in ('convert-template', 'finish-setup') and d.get('prepared') is not True:
        raise ValueError('Confirm that the guest has been generalized and shut down')
    if 'ssh_key' in d and (not isinstance(d['ssh_key'], str) or len(d['ssh_key']) > 16000 or not d['ssh_key'].startswith(('ssh-', 'ecdsa-'))):
        raise ValueError('Enter a valid SSH public key')
    return d


def windows_first_boot(p, base, identity, answer_volume, started, log):
    """Best-effort DVD prompt input, only during a new unattended VM's first boot."""
    deadline = started + 20
    sent = 0
    try:
        for _ in range(20):
            if time.monotonic() >= deadline:
                break
            config = p.call('GET', f'{base}/config')
            if config.get('smbios1') != identity or config.get('sata1', '').split(',')[0] != answer_volume:
                break
            state = p.call('GET', f'{base}/status/current')
            if state.get('status') != 'running' or not 0 <= state.get('uptime', 20) < 20:
                break
            if time.monotonic() >= deadline:
                break
            p.call('PUT', f'{base}/sendkey', key='spc')
            sent += 1
            time.sleep(1)
    except Exception:
        # Missing console permission or a slow boot must not abandon installation.
        # Do not log remote error bodies, which can contain sensitive arguments.
        log('Automatic DVD boot input stopped. Use the Proxmox console if Windows setup has not started.')
    log(f'First-boot input finished ({sent} space keys sent). This does not verify installation. If the DVD prompt timed out, open the console, press Enter for Boot Manager, select the Windows DVD and immediately press a key.')


def execute(d, log):
    if d['action'] == 'provision':
        return execute_flow(d, log)
    p = Proxmox()
    a, node = d['action'], d['node']
    resources = p.resources()
    guests = {int(r['vmid']): r for r in resources if 'vmid' in r}
    vmid = d.get('vmid')
    if a in ('linux-template', 'installer', 'clone') and vmid in guests:
        raise ValueError(f'VM ID {vmid} already exists. Nothing was changed.')
    if a in ('backup', 'convert-template', 'start', 'finish-setup'):
        if vmid not in guests or guests[vmid]['node'] != node or guests[vmid]['type'] != 'qemu':
            raise ValueError('Choose an existing QEMU VM on this node')
    if node not in {r['node'] for r in resources if r.get('type') == 'node' and r.get('status') == 'online'}:
        raise ValueError('Choose an online Proxmox node')
    base = f'nodes/{node}/qemu'
    def task(path, **args):
        p.wait(node, p.call('POST', path, **args), log)
    if a == 'clone':
        src = guests.get(d['source'])
        if not src or src['node'] != node or not src.get('template') or src['type'] != 'qemu':
            raise ValueError('Source must be a QEMU template on the selected node')
        task(f'{base}/{d["source"]}/clone', newid=vmid, name=d['name'], full=1, storage=d['storage'])
        config = {k: d[k] for k in ('memory', 'cores') if k in d}
        if d.get('username'): config.update(ciuser=d['username'], ipconfig0='ip=dhcp')
        if d.get('ssh_key'): config['sshkeys'] = quote(d['ssh_key'], safe='')
        if config: p.call('PUT', f'{base}/{vmid}/config', **config)
        if d.get('start'): task(f'{base}/{vmid}/status/start')
    elif a == 'backup':
        task(f'nodes/{node}/vzdump', vmid=vmid, storage=d['backup_storage'], mode='snapshot', compress='zstd')
    elif a == 'start':
        task(f'{base}/{vmid}/status/start')
    elif a == 'convert-template':
        if guests[vmid]['status'] != 'stopped' or guests[vmid].get('template'):
            raise ValueError('Guest must be stopped and not already a template')
        if 'proxmate-unattend-' in str(p.call('GET', f'{base}/{vmid}/config').get('sata1', '')):
            raise ValueError('Use Finish setup to remove answer media before converting')
        task(f'{base}/{vmid}/template')
    elif a == 'finish-setup':
        if guests[vmid]['status'] != 'stopped': raise ValueError('Shut down the VM before removing setup media')
        config = p.call('GET', f'{base}/{vmid}/config')
        volume = config.get('sata1', '').split(',')[0]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+:iso/proxmate-unattend-' + str(vmid) + r'-[a-f0-9]{32}\.iso', volume):
            raise ValueError('No Proxmate-generated answer ISO is attached to this VM')
        p.call('PUT', f'{base}/{vmid}/config', delete='sata1')
        p.wait(node, p.call('DELETE', f'nodes/{node}/storage/{volume.split(":")[0]}/content/{quote(volume, safe="")}'), log)
        log('Detached and deleted the generated answer ISO. Windows may cache answer files; remove cached copies and change the setup password before sharing a template.')
    elif a == 'installer':
        windows = d.get('os') == 'windows'
        config = dict(vmid=vmid, name=d['name'], memory=d.get('memory', 4096), cores=d.get('cores', 2),
                      cpu='host', ostype='win11' if windows else 'l26', bios='ovmf', machine='q35',
                      efidisk0=f'{d["storage"]}:0,efitype=4m,pre-enrolled-keys=1',
                      sata0=f'{d["storage"]}:{d.get("disk", 64)}', ide2=d['iso']+',media=cdrom',
                      net0=f'{"e1000" if windows else "virtio"},bridge={d["bridge"]}', boot='order=ide2;sata0', agent=1)
        if d.get('_data_disks'):
            from windows_build import drive_options
            config.update(drive_options({'storage':d['storage'],'data_disks':d['_data_disks']}))
        if windows: config['tpmstate0'] = f'{d["storage"]}:0,version=v2.0'
        if d.get('_answer_volume'): config['sata1'] = d['_answer_volume'] + ',media=cdrom'
        if d.get('bootstrap',{}).get('agent_iso'): config['sata2'] = d['bootstrap']['agent_iso']+',media=cdrom'
        unattended = windows and bool(d.get('_answer_volume'))
        if unattended:
            config['boot'] = 'order=sata0;ide2'
            config['smbios1'] = f'uuid={uuid.uuid4()}'
        task(base, **config)
        allocated={}
        if unattended:
            allocated['created_smbios']=config['smbios1']
            if d.get('_data_disks'):
                from windows_build import disk_volumes
                actual=p.call('GET',f'{base}/{vmid}/config')
                if actual.get('smbios1')!=config['smbios1']: raise ValueError('New VM identity changed before boot')
                allocated['data_volumes']=disk_volumes(dict(storage=d['storage'],data_disks=d['_data_disks']),actual)
        if d.get('start'):
            started = time.monotonic()
            task(f'{base}/{vmid}/status/start')
            if unattended:
                windows_first_boot(p, f'{base}/{vmid}', config['smbios1'], d['_answer_volume'], started, log)
        log('Installer VM ready. Open its Proxmox console, install the OS, then generalize before converting to a template.')
        if unattended: return allocated
    elif a == 'upload-iso':
        root = Path(os.getenv('ISO_DIR', 'D:/ISOs')).resolve()
        f = (root / d['filename']).resolve()
        if f.parent != root or not f.is_file(): raise ValueError('ISO not found in configured library')
        existing = p.call('GET', f'nodes/{node}/storage/{d["storage"]}/content', content='iso')
        upload_name=upload_iso_name(f.name)
        if any(x['volid'].endswith('/' + upload_name) for x in existing): raise ValueError('ISO already exists; refusing to overwrite')
        with f.open('rb', buffering=8 * 1024 * 1024) as stream:
            encoder = MultipartEncoder(fields={'content': 'iso', 'filename': (upload_name, stream, 'application/octet-stream')})
            progress = [-1]
            def report(monitor):
                step = int(100 * monitor.bytes_read / monitor.len) // 5
                if step != progress[0]:
                    progress[0] = step
                    log(f'Uploading {f.name}: {min(step * 5, 100)}%')
            monitor = MultipartEncoderMonitor(encoder, report)
            r = p.session.post(p.url + f'/api2/json/nodes/{node}/storage/{d["storage"]}/upload', data=monitor,
                               headers={'Content-Type': encoder.content_type}, timeout=7200, allow_redirects=False)
            if not 200 <= r.status_code < 300: raise RuntimeError(f'Upload failed: HTTP {r.status_code}')
            p.wait(node, r.json()['data'], log)
    elif a == 'linux-template':
        profile = PROFILES[d.get('profile', 'ubuntu-24.04')]
        manifest = requests.get(profile['base'] + profile['checksums'], timeout=30)
        manifest.raise_for_status()
        digest = checksum_for(manifest.text, profile['image'])
        q = shlex.quote
        storage, bridge, name = map(q, (d['storage'], d['bridge'], d['name']))
        script = f'''set -euo pipefail
test "$(hostname -s)" = {q(node)} || {{ echo 'SSH host does not match selected node'; exit 1; }}
work=$(mktemp -d /var/tmp/proxmate.XXXXXX)
trap 'rm -rf "$work"' EXIT
cd "$work"
echo {q('Downloading ' + profile['label'] + ' cloud image')}
curl --fail --location --silent --show-error --connect-timeout 20 --max-time 3600 --speed-time 60 --speed-limit 1024 -o image.qcow2 {q(profile['base'] + profile['image'])}
echo '{digest}  image.qcow2' | sha256sum -c -
qm create {vmid} --name {name} --memory {d.get('memory', 2048)} --cores {d.get('cores', 2)} --cpu host --ostype l26 --agent 1 --scsihw virtio-scsi-single --net0 virtio,bridge={bridge} --serial0 socket --vga serial0
qm set {vmid} --scsi0 {storage}:0,import-from="$work/image.qcow2",discard=on
qm resize {vmid} scsi0 {d.get('disk', 32)}G
qm set {vmid} {('--ide2 '+storage+':cloudinit') if not d.get('bootstrap',{}).get('seed_storage') else ''} --boot order=scsi0 --ipconfig0 {q('ip=' + d['address'] + ',gw=' + d['gateway'] if d.get('network_mode') == 'static' else 'ip=dhcp')} --ciuser {q(d.get('username', profile['user']))}
'''
        if d.get('dns'): script += f'qm set {vmid} --nameserver {q(d["dns"])}\n'
        if d.get('ssh_key'):
            script += f"printf '%s\\n' {q(d['ssh_key'])} > key.pub\nqm set {vmid} --sshkeys key.pub\n"
        if d.get('bootstrap',{}).get('seed_storage'):
            with tempfile.TemporaryDirectory(prefix='proxmate-seed-') as temp:
                seed=Path(temp)/f'proxmate-seed-{vmid}-{uuid.uuid4().hex}.iso'
                cloud_seed(seed,d['username'],d['ssh_key'],d['name'])
                volume=upload_generated(p,node,d['bootstrap']['seed_storage'],seed,log)
            script+=f'qm set {vmid} --ide2 {q(volume+",media=cdrom")}\n'
        elif d.get('bootstrap',{}).get('enabled'):
            volume = d['bootstrap']['snippet_storage']+':snippets/proxmate-bootstrap-'+str(vmid)+'-'+uuid.uuid4().hex+'.yaml'
            encoded = base64.b64encode(cloud_vendor().encode()).decode()
            script += f'''snippet=$(pvesm path {q(volume)})
test ! -e "$snippet" || {{ echo 'Bootstrap snippet already exists'; exit 1; }}
test -d "$(dirname "$snippet")" || {{ echo 'Create the storage snippets directory first'; exit 1; }}
( set -C; umask 077; printf '%s' {q(encoded)} | base64 -d > "$snippet" )
qm set {vmid} --cicustom vendor={q(volume)}
qm cloudinit update {vmid}
'''
        if d.get('outcome', 'template') == 'template': script += f'qm template {vmid}\n'
        elif d.get('start'): script += f'qm start {vmid}\n'
        script += f'echo "Cloud image provisioning finished for {vmid}"\n'
        p.shell(script, log)
        return {'source':profile['base']+profile['image'],'sha256':digest}
    elif a == 'ansible':
        # Deliberately an administrator code-execution feature. Never run automatically.
        with tempfile.TemporaryDirectory(prefix='proxmate-ansible-') as temp:
            directory = Path(temp)
            (directory / 'playbook.yml').write_text(d['playbook'])
            inventory_path = directory / 'inventory'
            if d.get('target') == 'proxmox':
                with p.ssh() as client:
                    _, hostname, _ = client.exec_command('hostname -s', timeout=15)
                    if hostname.read().decode().strip() != node:
                        raise ValueError('Connected SSH host does not match selected node')
                    key = client.get_transport().get_remote_server_key()
                host = urlparse(p.url).hostname
                known = directory / 'known_hosts'
                known.write_text(f'{host} {key.get_name()} {key.get_base64()}\n')
                inventory_path = directory / 'inventory.yml'
                inventory_path.write_text(yaml.safe_dump({'all': {'hosts': {d['node']: {
                    'ansible_host': host, 'ansible_user': p.ssh_user,
                    'ansible_password': p.password,
                    'ansible_ssh_common_args': f'-o UserKnownHostsFile={known} -o StrictHostKeyChecking=yes',
                    'ansible_python_interpreter': '/usr/bin/python3'}}}}))
                inventory_path.chmod(0o600)
            else:
                inventory_path.write_text(d['inventory'])
            env = dict(os.environ, ANSIBLE_NOCOLOR='1', ANSIBLE_HOST_KEY_CHECKING='True')
            log('Executing administrator-supplied playbook. SSH host verification is enabled.')
            output = directory / 'output.log'
            with output.open('w') as stream:
                proc = subprocess.Popen(['ansible-playbook', '-i', str(inventory_path), str(directory / 'playbook.yml')],
                                        stdout=stream, stderr=subprocess.STDOUT, env=env, cwd=temp)
                try: code = proc.wait(timeout=3600)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(); raise RuntimeError('Ansible timed out after one hour')
            log(output.read_text(errors='replace')[-100000:])
            if code: raise RuntimeError(f'Ansible exited with code {code}')
    log('Operation finished.')


def execute_flow(d, log):
    """Explicitly submitted flow only. Preparation is never reported as OS installation."""
    d = validate(d)
    p = Proxmox()
    resources = p.resources()
    if any(r.get('vmid') == d['vmid'] for r in resources): raise ValueError('VM ID already exists; no resources changed')
    if not any(r.get('type') == 'node' and r['node'] == d['node'] and r.get('status') == 'online' for r in resources): raise ValueError('Node is not online')
    stores = p.call('GET', f'nodes/{d["node"]}/storage')
    for name, capability in [('storage', 'images')] + ([('iso_storage', 'iso')] if d['kind'] != 'linux-cloud' and (d['kind'] == 'windows' or d['source_kind'] == 'local') else []):
        if not any(s['storage'] == d.get(name) and s.get('active') and capability in s.get('content', '').split(',') for s in stores): raise ValueError(f'Choose active {capability} storage')
    networks = p.call('GET', f'nodes/{d["node"]}/network')
    if not any(n['iface'] == d['bridge'] and n.get('type') == 'bridge' for n in networks): raise ValueError('Choose an existing bridge')
    log('Preflight passed: unused VM ID, online node, storage and bridge.')
    if d['kind'] == 'linux-cloud':
        b=d.get('bootstrap',{})
        if b.get('enabled') and not any(s['storage']==(b.get('seed_storage') or b.get('snippet_storage')) and s.get('active') and ('iso' if b.get('seed_storage') else 'snippets') in s.get('content','').split(',') for s in stores):
            raise ValueError('Bootstrap requires active snippets-enabled storage')
        result = execute(dict(d, action='linux-template', outcome=d.get('purpose', 'vm')), log)
        log('Next: boot the VM or clone the template and verify SSH/network access.')
        return result
    if d.get('bootstrap',{}).get('agent_iso'):
        vol=d['bootstrap']['agent_iso']
        drivers=p.call('GET',f'nodes/{d["node"]}/storage/{vol.split(":")[0]}/content',content='iso')
        if not any(m['volid']==vol for m in drivers): raise ValueError('Selected guest agent ISO is not present')
        from media import guest_agent_sha256
        with diagnostics.stage('verify_guest_tools'):
            actual=guest_agent_sha256(vol,d['node'])
        if d['bootstrap'].get('agent_sha256') and d['bootstrap']['agent_sha256']!=actual:
            raise ValueError('Guest agent MSI changed since review; create a new plan')
        d['bootstrap']['agent_sha256']=actual
    if d['source_kind'] == 'local':
        execute(dict(action='upload-iso', node=d['node'], storage=d['iso_storage'], filename=d['filename']), log)
    else:
        media = p.call('GET', f'nodes/{d["node"]}/storage/{d["iso"].split(":")[0]}/content', content='iso')
        if not any(m['volid'] == d['iso'] for m in media): raise ValueError('Selected ISO is not present')
    guest = dict(d, action='installer')
    if d['kind'] == 'windows':
        guest['_data_disks']=d.get('data_disks',[])
        name = f'proxmate-unattend-{d["vmid"]}-{uuid.uuid4().hex}.iso'
        with tempfile.TemporaryDirectory(prefix='proxmate-answer-') as temp:
            path = Path(temp) / name
            answer_iso(d['windows'], path, bootstrap=d.get('bootstrap'))
            path.chmod(0o600)
            log('Uploading generated answer media. It contains setup credentials if supplied; restrict storage access.')
            guest['_answer_volume'] = upload_generated(p, d['node'], d['iso_storage'], path, log)
            log('Answer media: ' + guest['_answer_volume'])
    allocated=execute(guest, log)
    log('Installer prepared, not installation verified. Open the console; Windows may require the DVD boot key or activation/account prompts. After installation shut down and use Finish setup to remove answer media.' if d['kind'] == 'windows' else 'Install Linux through the Proxmox console. ISO installs are interactive.')
    if d.get('purpose') == 'template': log('Template flow: finish installation, generalize the guest, shut it down, then use Convert to template. No automatic conversion has been performed.')
    return allocated
