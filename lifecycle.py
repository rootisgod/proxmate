"""Reviewed recovery, guest bootstrap completion and versioned template lifecycle."""
import copy
import hashlib
import ipaddress
import json
import re
import time
import uuid
from datetime import datetime,timezone
from urllib.parse import quote

from flask import jsonify,request
import operations
import management
from bootstrap import linux_script,windows_script,options as bootstrap_options
from guest_tasks import run_guest


def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
def vm_path(t): return f'nodes/{t["node"]}/qemu/{t["vmid"]}'
def config_identity(config): return digest({k:v for k,v in config.items() if k not in ('digest','lock')})
def machine_identity(config): return digest({k:config.get(k) for k in ('smbios1','vmgenid')})


class WaitForGuest(Exception):
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint


class BootstrapPending(Exception):
    """Only a read-only guest check can be repeated."""


class BootstrapFailed(RuntimeError):
    """A terminal bootstrap failure, not a guest readiness delay."""


class BootstrapTargetChanged(BootstrapFailed):
    """A terminal target change, not a guest readiness delay."""


def protected(archive): return str(archive.get('protected',False)).lower() in ('1','true')
def archive_identity(a): return digest({k:a.get(k) for k in ('volid','size','ctime','vmid','format')})


def source_vmid(a):
    vol=a.get('volid','')
    match=re.search(r':backup/(?:vzdump-qemu-(\d+)-|vm/(\d+)/)',vol)
    if not match: return None
    return int(match[1] or match[2])


def safe_guest_config(config):
    for key,value in config.items():
        if 'proxmate-unattend-' in str(value): raise ValueError('Remove generated answer media before backup recovery or template tests')
        if key in ('args','hookscript','ivshmem') or re.fullmatch(r'(hostpci|usb|virtiofs)\d+',key):
            raise ValueError('Remove passthrough, hooks and custom QEMU arguments before using this recovery/test workflow')
        if '/dev/' in str(value): raise ValueError('Host device paths are not allowed in recovery/test guests')


def provenance(config):
    fields=('kind','purpose','node','vmid','name','storage','bridge','profile','source_kind','filename','iso','cores','memory','disk','network_mode','address','gateway','dns','username','ssh_key')
    result={k:config[k] for k in fields if k in config}
    if config.get('windows'): result['windows']={k:v for k,v in config['windows'].items() if k!='admin_password'}
    if config.get('bootstrap'): result['bootstrap']={k:v for k,v in config['bootstrap'].items() if k not in ('credential_id',)}
    return result


class Lifecycle:
    def __init__(self,manager):
        self.m=manager
        with self.m.db() as con: con.execute('CREATE TABLE IF NOT EXISTS lifecycle_plans (id TEXT PRIMARY KEY, created REAL, value TEXT)')
        for policy in self.m.all('backup-policies'):
            if policy['next_run'] < time.time():
                policy['next_run']=management.next_occurrence(time.time(),policy)
                policy['last_report']='Missed occurrence skipped on restart.'
                self.m.put('backup-policies',policy)

    def guest(self,p,t,template=None):
        r=next((r for r in p.resources() if r.get('type')=='qemu' and r.get('node')==t['node'] and r.get('vmid')==t['vmid']),None)
        if not r or (template is not None and bool(r.get('template')) != template): raise ValueError('Expected VM/template is missing or moved')
        return r,p.call('GET',vm_path(t)+'/config')

    def unused(self,p,node,vmid,storage):
        if any(r.get('vmid')==vmid for r in p.resources()): raise ValueError('Destination VM ID is occupied; nothing will be overwritten')
        nodes=p.call('GET','nodes')
        if not any(n['node']==node and n.get('status')=='online' for n in nodes): raise ValueError('Destination node must be online')
        stores=p.call('GET',f'nodes/{node}/storage')
        if not any(s['storage']==storage and s.get('active') and 'images' in s.get('content','').split(',') for s in stores): raise ValueError('Choose active VM disk storage')

    def archives(self,p,node,storage):
        return [a for a in p.call('GET',f'nodes/{node}/storage/{storage}/content',content='backup') if source_vmid(a) is not None]

    def archive(self,p,node,volid):
        if not isinstance(volid,str) or ':' not in volid: raise ValueError('Select a backup archive')
        storage=management.word(volid.split(':')[0],'backup storage')
        a=next((a for a in self.archives(p,node,storage) if a['volid']==volid),None)
        if not a: raise ValueError('QEMU backup archive not found on the selected node')
        return a

    def backup_config(self,p,node,volid):
        raw=p.call('GET',f'nodes/{node}/vzdump/extractconfig',volume=volid)
        if not isinstance(raw,str): raise ValueError('Cannot inspect archived VM configuration')
        result={}
        for line in raw.splitlines():
            if line.startswith('['): break
            if ':' in line and not line.startswith('#'):
                k,v=line.split(':',1);result[k.strip()]=v.strip()
        if not result: raise ValueError('Backup configuration is empty')
        safe_guest_config(result)
        return result

    def retention_candidates(self,archives,keep,days,now):
        ordered=sorted(archives,key=lambda a:(float(a.get('ctime',0)),a['volid']),reverse=True)
        return [a for a in ordered[keep:] if not protected(a) and a.get('ctime') and float(a['ctime']) < now-days*86400]

    def review(self,d):
        if not isinstance(d,dict): raise ValueError('Supply a JSON object')
        op=d.get('operation');p=operations.Proxmox();plan={'operation':op}
        if op in ('backup-restore','template-test','version-clone'):
            plan.update(management.target(d),storage=management.word(d.get('storage'),'storage'),name=management.word(d.get('name'),'VM name'),
                        boot_test=management.boolean(d.get('boot_test',op=='template-test')),disconnected=management.boolean(d.get('disconnected',True)),timeout=management.number(d.get('timeout',300),30,1800))
            if plan['boot_test'] and not plan['disconnected']: raise ValueError('Test boot requires disconnected networking')
            self.unused(p,plan['node'],plan['vmid'],plan['storage'])
            if op=='backup-restore':
                a=self.archive(p,plan['node'],d.get('archive'))
                plan.update(archive=a['volid'],archive_identity=archive_identity(a),source_vmid=source_vmid(a))
                config=self.backup_config(p,plan['node'],a['volid'])
                plan['backup_config_identity']=digest(config)
            else:
                v=self.m.get('template-versions',d.get('version_id'))
                _,config=self.guest(p,v,True)
                if plan['node']!=v['node']: raise ValueError('Test clones must use the template node')
                if config_identity(config)!=v['identity']: raise ValueError('Template changed since registration; register a new version')
                safe_guest_config(config)
                plan.update(version_id=v['id'],source=v['vmid'],source_identity=v['identity'])
                if op=='template-test': plan['boot_test']=True;plan['disconnected']=True
        elif op=='backup-retention':
            plan.update(node=management.word(d.get('node')),backup_storage=management.word(d.get('backup_storage')),source_vmid=management.number(d.get('source_vmid'),100,999999999),keep=management.number(d.get('keep',3),1,10000),older_than_days=management.number(d.get('older_than_days',30),1,36500))
            archives=[a for a in self.archives(p,plan['node'],plan['backup_storage']) if source_vmid(a)==plan['source_vmid']]
            chosen=self.retention_candidates(archives,plan['keep'],plan['older_than_days'],time.time())
            plan['candidates']=[dict(volid=a['volid'],identity=archive_identity(a),ctime=a.get('ctime'),size=a.get('size')) for a in chosen]
        elif op=='version-register':
            family=self.m.get('template-families',d.get('family_id'))
            plan.update(management.target(d),family_id=family['id'],label=management.name(d.get('label')))
            r,config=self.guest(p,plan)
            safe_guest_config(config)
            if r.get('status')!='stopped': raise ValueError('Generalize and shut down before registering a template version')
            if d.get('prepared') is not True: raise ValueError('Confirm guest generalization and removal of setup credentials')
            if any('proxmate-unattend-' in str(v) for v in config.values()): raise ValueError('Remove generated answer media with Finish setup first')
            if any(v['family_id']==family['id'] and v['label']==plan['label'] for v in self.m.all('template-versions')): raise ValueError('Version label already exists in this family')
            plan.update(identity=config_identity(config),already_template=bool(r.get('template')))
            build=next((b for b in sorted(self.m.all('builds'),key=lambda b:b['created'],reverse=True) if b['node']==plan['node'] and b['vmid']==plan['vmid'] and b.get('machine_identity')==machine_identity(config)),None)
            plan['provenance']=build or {'source':'Adopted existing guest; build provenance unavailable','tasks':[]}
        elif op=='version-promote':
            v=self.m.get('template-versions',d.get('version_id'))
            _,config=self.guest(p,v,True)
            if config_identity(config)!=v['identity']: raise ValueError('Template changed after registration')
            evidence=v.get('test')
            if not evidence or not evidence.get('passed') or evidence['identity']!=v['identity']: raise ValueError('Run a successful isolated template test before promotion')
            plan.update(version_id=v['id'],identity=v['identity'],family_id=v['family_id'],label=v['label'])
        elif op=='version-build':
            family=self.m.get('template-families',d.get('family_id'));recipe=self.m.get('recipes',d.get('recipe_id'))
            config=copy.deepcopy(recipe['config']);config.update(vmid=management.number(d.get('vmid'),100,999999999),name=management.word(d.get('name')),purpose='vm',start=management.boolean(d.get('start',False)))
            if config.get('windows'):
                if d.get('accept_eula') is not True or d.get('wipe_disk') is not True: raise ValueError('Confirm Windows licensing and new-disk formatting for this build')
                config['windows'].update(admin_password=d.get('admin_password',''),accept_eula=True,wipe_disk=True)
            config=operations.validate(dict(config,action='provision'))
            if config.get('bootstrap',{}).get('verify'):
                c=self.m.get('credentials',config['bootstrap']['credential_id'])
                if c['platform']!=('windows' if config['kind']=='windows' else 'linux'): raise ValueError('Bootstrap credential platform mismatch')
            self.unused(p,config['node'],config['vmid'],config['storage'])
            plan.update(config=config,family_id=family['id'],recipe_id=recipe['id'],recipe_name=recipe['name'],recipe_tasks=copy.deepcopy(recipe.get('tasks',[])),label=management.name(d.get('label')))
            if plan['recipe_tasks']: plan['frozen_tasks']=self.m.task_selection(plan['recipe_tasks'])
        else: raise ValueError('Unknown lifecycle operation')
        ident=uuid.uuid4().hex
        with self.m.db() as con:
            con.execute('DELETE FROM lifecycle_plans WHERE created<?',(time.time()-86400,))
            con.execute('INSERT INTO lifecycle_plans VALUES (?,?,?)',(ident,time.time(),self.m.encode(plan)))
        return dict(id=ident,**self.summary(plan))

    def summary(self,plan):
        result={k:copy.deepcopy(v) for k,v in plan.items() if k not in ('config','provenance','frozen_tasks','backup_config_identity')}
        if plan.get('config'): result['build']=provenance(plan['config'])
        if plan.get('provenance'): result['provenance']=plan['provenance']
        result['dangerous']=plan['operation']=='backup-retention' or any(s['item'].get('dangerous') for s in plan.get('frozen_tasks',[]))
        if plan.get('frozen_tasks'): result['tasks']=[{'name':s['item']['name'],'params':s['params'],'body':s['item'].get('body','')} for s in plan['frozen_tasks']]
        result['notes']=['Existing VMs are never overwritten. Failed resources are retained for inspection.',
                         'Restored/test guests have onboot disabled. Test boot requires disconnected NICs and no host passthrough.',
                         'A boot check verifies guest-agent OS responsiveness, not application/data correctness.',
                         'Previous template versions are retained. Promotion only changes the preferred pointer.']
        return result

    def load(self,ident):
        if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident): raise ValueError('Invalid lifecycle plan ID')
        with self.m.db() as con: row=con.execute('SELECT * FROM lifecycle_plans WHERE id=?',(ident,)).fetchone()
        if not row or row['created']<time.time()-1800: raise ValueError('Review plan expired; review again')
        return self.m.decode(row['value'])

    def enqueue(self,plan,ident):
        with self.m.db() as con:
            con.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?)',(ident,'lifecycle','queued',time.time(),time.time(),self.m.encode({'action':'lifecycle','plan':plan}),'Queued reviewed '+plan['operation']))
        return {'id':ident,'url':'/api/jobs/'+ident}

    def configure_copy(self,p,plan,log):
        path=vm_path(plan);cfg=p.call('GET',path+'/config');safe_guest_config(cfg)
        changes={'name':plan['name'],'onboot':0,'digest':cfg['digest']}
        if plan['disconnected']:
            remove=[k for k in cfg if re.fullmatch(r'net\d+',k)]
            if remove: changes['delete']=','.join(remove)
        p.wait(plan['node'],p.call('PUT',path+'/config',**changes),log)
        check=p.call('GET',path+'/config')
        if int(check.get('onboot',0))!=0 or (plan['disconnected'] and any(re.fullmatch(r'net\d+',k) for k in check)):
            raise ValueError('Copy isolation was not confirmed; refusing to start')
        log(f'Retained VM {plan["node"]}:{plan["vmid"]}; onboot disabled; networking '+('disconnected.' if plan['disconnected'] else 'retained. VM remains stopped.'))

    def boot_check(self,p,plan,log):
        path=vm_path(plan)
        config=p.call('GET',path+'/config');safe_guest_config(config)
        if any(re.fullmatch(r'net\d+',k) for k in config): raise ValueError('Network interfaces remain; refusing test boot')
        p.wait(plan['node'],p.call('POST',path+'/status/start'),log)
        deadline=time.monotonic()+plan['timeout'];ready=False
        try:
            while time.monotonic()<deadline:
                try:
                    osinfo=p.call('GET',path+'/agent/get-osinfo')
                    if not isinstance(osinfo,dict) or not osinfo.get('result'): raise ValueError('No guest OS response')
                    ready=True;break
                except Exception: time.sleep(5)
            if not ready: raise RuntimeError('Test boot timed out waiting for guest-agent OS information; restore alone is not a successful boot test')
            log('Guest agent returned OS information; isolated boot check passed.')
        finally:
            # Never force stop and never delete a test VM, even after a failed check.
            p.wait(plan['node'],p.call('POST',path+'/status/shutdown',timeout=120,forceStop=0),log)
        log('Test VM shut down and retained. Inspect it before manual cleanup.')

    def execute(self,plan,log,job_id):
        p=operations.Proxmox();op=plan['operation']
        if plan.get('execution_window'):
            start,end=plan['execution_window']
            if not start<=datetime.fromtimestamp(time.time(),timezone.utc).hour<end: raise ValueError('Maintenance window closed; operation skipped')
        if op in ('backup-restore','template-test','version-clone'):
            self.unused(p,plan['node'],plan['vmid'],plan['storage'])
            if op=='backup-restore':
                archive=self.archive(p,plan['node'],plan['archive'])
                if archive_identity(archive)!=plan['archive_identity']: raise ValueError('Archive changed since review')
                if digest(self.backup_config(p,plan['node'],plan['archive']))!=plan['backup_config_identity']: raise ValueError('Archived configuration changed since review')
                p.wait(plan['node'],p.call('POST',f'nodes/{plan["node"]}/qemu',vmid=plan['vmid'],archive=plan['archive'],storage=plan['storage'],unique=1,force=0,start=0,onboot=0),log)
            else:
                version=self.m.get('template-versions',plan['version_id']);_,cfg=self.guest(p,version,True)
                if config_identity(cfg)!=plan['source_identity']: raise ValueError('Template changed since test review')
                safe_guest_config(cfg)
                p.wait(plan['node'],p.call('POST',vm_path(version)+'/clone',newid=plan['vmid'],name=plan['name'],storage=plan['storage'],full=1),log)
            self.configure_copy(p,plan,log)
            if plan['boot_test']: self.boot_check(p,plan,log)
            report={'id':job_id,'operation':op,'node':plan['node'],'vmid':plan['vmid'],'archive':plan.get('archive'),'boot_verified':plan['boot_test'],'created':time.time()}
            self.m.put('restore-reports',report)
            if op=='template-test':
                version=self.m.get('template-versions',plan['version_id']);_,cfg=self.guest(p,version,True)
                if config_identity(cfg)!=version['identity']: raise ValueError('Template changed during test; evidence not recorded')
                version['test']={'passed':True,'identity':version['identity'],'job_id':job_id,'vmid':plan['vmid'],'created':time.time()}
                self.m.put('template-versions',version)
        elif op=='backup-retention':
            archives=[a for a in self.archives(p,plan['node'],plan['backup_storage']) if source_vmid(a)==plan['source_vmid']]
            eligible={a['volid']:a for a in self.retention_candidates(archives,plan['keep'],plan['older_than_days'],time.time())}
            for candidate in plan['candidates']:
                current=eligible.get(candidate['volid'])
                if not current or protected(current) or archive_identity(current)!=candidate['identity']: raise ValueError('Retention candidates changed or became protected; review again')
            for candidate in plan['candidates']:
                # Recheck protection immediately before each deletion.
                remaining=[a for a in self.archives(p,plan['node'],plan['backup_storage']) if source_vmid(a)==plan['source_vmid']]
                current=next((a for a in self.retention_candidates(remaining,plan['keep'],plan['older_than_days'],time.time()) if a['volid']==candidate['volid']),None)
                if not current or protected(current) or archive_identity(current)!=candidate['identity']: raise ValueError('Archive changed/protected or is now retained; retention stopped')
                p.wait(plan['node'],p.call('DELETE',f'nodes/{plan["node"]}/storage/{plan["backup_storage"]}/content/{quote(candidate["volid"],safe="")}'),log)
                log('Deleted reviewed archive: '+candidate['volid'])
            log(f'Retention finished: {len(plan["candidates"])} reviewed archives deleted.')
        elif op=='version-register':
            r,cfg=self.guest(p,plan)
            if config_identity(cfg)!=plan['identity'] or r.get('status')!='stopped': raise ValueError('Candidate changed since registration review')
            if any(v['family_id']==plan['family_id'] and v['label']==plan['label'] for v in self.m.all('template-versions')): raise ValueError('Version label already registered')
            if not plan['already_template']:
                if cfg.get('onboot'):
                    p.wait(plan['node'],p.call('PUT',vm_path(plan)+'/config',onboot=0,digest=cfg['digest']),log)
                operations.execute({'action':'convert-template','node':plan['node'],'vmid':plan['vmid'],'prepared':True},log)
            elif cfg.get('onboot'):
                p.wait(plan['node'],p.call('PUT',vm_path(plan)+'/config',onboot=0,digest=cfg['digest']),log)
            _,cfg=self.guest(p,plan,True)
            version={'id':job_id,'family_id':plan['family_id'],'label':plan['label'],'node':plan['node'],'vmid':plan['vmid'],'created':time.time(),'identity':config_identity(cfg),'provenance':plan['provenance'],'test':None}
            self.m.put('template-versions',version)
            log('Template version registered. Test a full isolated clone before promoting.')
        elif op=='version-promote':
            v=self.m.get('template-versions',plan['version_id']);_,cfg=self.guest(p,v,True)
            if config_identity(cfg)!=plan['identity'] or not (v.get('test') or {}).get('passed'): raise ValueError('Template or test evidence changed; review again')
            with self.m.db() as con:
                con.execute('BEGIN IMMEDIATE')
                row=con.execute("SELECT value FROM records WHERE kind='template-families' AND id=?",(v['family_id'],)).fetchone()
                family=self.m.decode(row['value']);family['preferred_id']=v['id'];family['promoted_at']=time.time()
                con.execute("UPDATE records SET value=? WHERE kind='template-families' AND id=?",(self.m.encode(family),family['id']))
            log('Preferred version updated. All previous versions retained.')
        elif op=='version-build':
            result=operations.execute(plan['config'],log)
            self.after_provision(plan['config'],result,job_id,log,{'family_id':plan['family_id'],'label':plan['label'],'recipe_id':plan['recipe_id'],'recipe_name':plan['recipe_name'],'planned_tasks':plan['recipe_tasks']})
            self.finish_version_build(plan,log,job_id)

    def finish_version_build(self,plan,log,job_id):
        if plan.get('frozen_tasks'):
            if plan['config'].get('bootstrap',{}).get('verify'):
                taskplan=self.verified_task_plan(plan['config'],plan['frozen_tasks'],job_id)
                self.m.execute(taskplan,log)
                self.record_tasks(taskplan,job_id)
            else: log('Recipe tasks are recorded as planned, not applied. Finish installation/connection setup, then apply the recipe tasks before generalizing.')
        log('Candidate built. Generalize, shut down, remove setup media, then register this version.')

    def after_provision(self,d,result,job_id,log,extra=None):
        p=operations.Proxmox();cfg=p.call('GET',vm_path(d)+'/config')
        record=dict(id=job_id,node=d['node'],vmid=d['vmid'],machine_identity=machine_identity(cfg),created=time.time(),config=provenance(d),source=result or {},tasks=[],**(extra or {}))
        self.m.put('builds',record)
        if d.get('bootstrap',{}).get('verify'):
            if not (cfg.get('smbios1') or cfg.get('vmgenid')):
                raise BootstrapFailed('VM identity unavailable. Inspect the created machine before continuing.')
            credential = self.m.get('credentials', d['bootstrap']['credential_id'])
            previous = self.m.find('connections', management.target_id(d)) or {}
            raise WaitForGuest(dict(server_id=operations.connection_provider.current()['id'],
                node=d['node'], vmid=d['vmid'], identity=record['machine_identity'],
                credential_revision=credential['updated'], connection_revision=previous.get('updated'),
                deadline=time.time()+d['bootstrap']['timeout']))

    def verified_task_plan(self, cfg, tasks, job_id):
        reviewed=self.m.create_plan(dict(operation='readiness',targets=[management.target(cfg)]))
        plan=self.m.load_plan(reviewed['id'])
        target=plan['targets'][0]
        build=self.m.get('builds',job_id)
        binding=target['connection']
        if (not build.get('bootstrap_verified') or binding.get('machine_identity')!=build['machine_identity'] or
            binding['updated']!=build['connection_revision'] or
            binding.get('credential_revision')!=target['credential_revision']):
            raise ValueError('Verified guest connection changed before software installation.')
        if any(t['item']['platform']!=binding['platform'] for t in tasks):
            raise ValueError('Software does not match the verified guest platform')
        plan.update(operation='tasks',tasks=tasks)
        return plan

    def record_tasks(self,plan,job_id):
        for t in plan['targets']:
            p=operations.Proxmox();cfg=p.call('GET',vm_path(t)+'/config')
            builds=sorted([b for b in self.m.all('builds') if b['node']==t['node'] and b['vmid']==t['vmid'] and b.get('machine_identity')==machine_identity(cfg)],key=lambda b:b['created'])
            if builds:
                b=builds[-1];b['tasks'].append({'job_id':job_id,'created':time.time(),'tasks':[{'id':s['item']['id'],'name':s['item']['name'],'params':s['params'],'script_sha256':digest(s['item'].get('body',''))} for s in plan['tasks']]});self.m.put('builds',b)

    def probe_bootstrap(self, d, expected_identity, credential, previous, log):
        opts=d['bootstrap']
        platform='windows' if d['kind']=='windows' else 'linux'
        if credential['platform']!=platform: raise BootstrapFailed('Bootstrap credential platform mismatch')
        path=vm_path(d)
        waiting='Proxmox connection not ready'
        try:
            p=operations.Proxmox()
            resources=p.resources()
            node=next((r for r in resources if r.get('type')=='node' and r.get('node')==d['node']),None)
            if not node or node.get('status')!='online': raise ValueError('Proxmox node is offline or unavailable')
            target=next((r for r in resources if r.get('vmid')==d['vmid']),None)
            if not target: raise BootstrapTargetChanged('Created VM no longer exists; setup wait stopped. No VM was deleted by Proxmate.')
            if target.get('node')!=d['node'] or target.get('type')!='qemu' or target.get('template'): raise BootstrapTargetChanged('Created VM moved or was replaced; setup wait stopped.')
            if machine_identity(p.call('GET',path+'/config'))!=expected_identity:
                raise BootstrapTargetChanged('Created VM identity changed; setup wait stopped before guest access.')
            waiting='Waiting for QEMU guest agent discovery; remote access has not yet been verified'
            p.call('GET',path+'/agent/get-osinfo')
            if platform=='windows':
                try:
                    progress=p.call('GET',path+'/agent/file-read',file=r'C:\ProgramData\Proxmate\bootstrap-status.json')
                    stage=json.loads(progress.get('content','')).get('stage')
                except Exception: stage=None  # Older generated helpers have no status file.
                if stage=='failed-retry-limit': raise BootstrapFailed('Windows automatic setup exhausted its retries. VM retained; software was not run.')
                stages={'configuring-winrm':'Configuring HTTPS WinRM', 'waiting-windows-setup':'Waiting for Windows Setup to finish; no login is required',
                        'installing-guest-tools':'Installing verified guest tools', 'retry-guest-tools':'WinRM configured; retrying guest-tool installation',
                        'retry-winrm':'Guest tools running; retrying HTTPS WinRM setup', 'retry-winrm-and-guest-tools':'Retrying HTTPS WinRM and guest-tool setup'}
                if stage in stages: raise ValueError(stages[stage])
            waiting='Guest agent is running; waiting for completed remote-access setup'
            marker=p.call('GET',path+'/agent/file-read',file=r'C:\ProgramData\Proxmate\bootstrap-ready' if platform=='windows' else '/var/lib/proxmate/bootstrap-ready')
            if marker.get('content','').strip()!='ready': raise ValueError('Bootstrap marker is not ready')
            interfaces=p.call('GET',path+'/agent/network-get-interfaces')
            addresses={str(ipaddress.ip_address(a['ip-address'])) for i in interfaces.get('result',[]) for a in i.get('ip-addresses',[]) if a.get('ip-address-type')=='ipv4' and not ipaddress.ip_address(a['ip-address']).is_loopback and not ipaddress.ip_address(a['ip-address']).is_link_local}
            host=opts.get('host')
            if not host:
                if len(addresses)!=1: raise ValueError('Multiple/no guest IPs; specify the verification host')
                host=next(iter(addresses))
            binding=dict(node=d['node'],vmid=d['vmid'],platform=platform,host=management.address(host),port=5986 if platform=='windows' else 22,credential_id=credential['id'],verify_tls=True,become=True)
            resolved=management.endpoint_addresses(binding)
            if not set(map(str,resolved)).issubset(addresses): raise ValueError('Configured address does not match guest-agent addresses')
            waiting='Waiting for guest transport identity'
            trust = management.agent_trust(p, path, platform)
            management.check_saved_trust(previous, trust, expected_identity)
            binding.update(trust)
            waiting='Authenticated HTTPS WinRM check has not passed' if platform=='windows' else 'Authenticated SSH check has not passed'
            before_auth=p.call('GET',path+'/config')
            if before_auth.get('template') or machine_identity(before_auth)!=expected_identity:
                raise BootstrapTargetChanged('Created VM identity changed before authentication.')
            run_guest(binding,credential,[],log,readiness=True)
            after_auth=p.call('GET',path+'/config')
            if after_auth.get('template') or machine_identity(after_auth)!=expected_identity:
                raise BootstrapTargetChanged('Created VM identity changed during authentication.')
            return binding
        except BootstrapFailed:
            raise
        except management.TrustChanged as exc:
            raise BootstrapFailed(str(exc)) from None
        except Exception as exc:
            raise BootstrapPending(str(exc) if isinstance(exc,ValueError) else waiting) from None

    def save_policy(self,d):
        if not isinstance(d,dict): raise ValueError('Policy must be a JSON object')
        ident=d.get('id') or uuid.uuid4().hex
        if not re.fullmatch(r'[a-f0-9]{32}',ident): raise ValueError('Invalid policy ID')
        mode=d.get('mode')
        if mode not in ('restore-check','retention'): raise ValueError('Choose restore-check or retention policy')
        value=dict(id=ident,name=management.name(d.get('name')),mode=mode,node=management.word(d.get('node')),backup_storage=management.word(d.get('backup_storage')),source_vmid=management.number(d.get('source_vmid'),100,999999999),enabled=management.boolean(d.get('enabled',False)),interval_minutes=management.number(d.get('interval_minutes',10080),15,525600),window_start=management.number(d.get('window_start',0),0,23),window_end=management.number(d.get('window_end',24),1,24))
        if value['window_start']>=value['window_end']: raise ValueError('Window end must follow start')
        if d.get('confirmed') is not True: raise ValueError('Confirm policy scope')
        if mode=='retention':
            value.update(keep=management.number(d.get('keep',3),1,10000),older_than_days=management.number(d.get('older_than_days',30),1,36500),auto_delete=management.boolean(d.get('auto_delete',False)))
            if value['auto_delete'] and d.get('risk_confirmed') is not True: raise ValueError('Explicitly approve recurring deletion')
        else:
            value.update(storage=management.word(d.get('storage')),range_start=management.number(d.get('range_start'),100,999999999),range_end=management.number(d.get('range_end'),100,999999999),timeout=management.number(d.get('timeout',300),30,1800))
            if not 0<=value['range_end']-value['range_start']<=999: raise ValueError('Choose a scratch range of 1-1000 VM IDs')
        value['updated']=time.time()
        value['next_run']=management.next_occurrence(time.time(),value);value['last_report']='No runs yet.'
        existing=next((r for r in self.m.all('backup-policies') if r['id']==ident),None)
        if existing: value['last_job']=existing.get('last_job')
        return self.m.put('backup-policies',value)

    def policy_report(self,policy):
        with self.m.db() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute("SELECT value FROM records WHERE kind='backup-policies' AND id=?",(policy['id'],)).fetchone()
            if not row: return
            current=self.m.decode(row['value'])
            for key in ('last_report','last_job'):
                if key in policy: current[key]=policy[key]
            con.execute("UPDATE records SET value=? WHERE kind='backup-policies' AND id=?",(self.m.encode(current),policy['id']))

    def tick(self,now=None):
        now=time.time() if now is None else now
        for policy in self.m.all('backup-policies'):
            if not policy['enabled'] or policy['next_run']>now: continue
            # Claim before network inspection; a crash skips this occurrence, never replays.
            with self.m.db() as con:
                con.execute('BEGIN IMMEDIATE')
                row=con.execute("SELECT value FROM records WHERE kind='backup-policies' AND id=?",(policy['id'],)).fetchone()
                current=self.m.decode(row['value'])
                if current['next_run']>now or not current['enabled']: continue
                policy=current;due=policy['next_run'];policy['next_run']=management.next_occurrence(now,policy)
                active=con.execute("SELECT status FROM jobs WHERE id=? AND status IN ('queued','running','waiting')",(policy.get('last_job'),)).fetchone()
                policy['last_report']='Occurrence claimed.'
                con.execute("UPDATE records SET value=? WHERE kind='backup-policies' AND id=?",(self.m.encode(policy),policy['id']))
            try:
                hour=datetime.fromtimestamp(now,timezone.utc).hour
                if active or now-due>120 or not policy['window_start']<=hour<policy['window_end']: raise ValueError('Skipped missed, overlapping or outside-window occurrence')
                if policy['mode']=='retention':
                    reviewed=self.review(dict(policy,operation='backup-retention'))
                    if not policy['auto_delete']:
                        policy['last_report']=f'Preview only: {len(reviewed["candidates"])} eligible archives. No deletion.'
                        self.policy_report(policy);continue
                else:
                    p=operations.Proxmox();archives=[a for a in self.archives(p,policy['node'],policy['backup_storage']) if source_vmid(a)==policy['source_vmid']]
                    if not archives: raise ValueError('No backup archives available')
                    archive=max(archives,key=lambda a:float(a.get('ctime',0)))
                    occupied={r.get('vmid') for r in p.resources()}
                    vmid=next((i for i in range(policy['range_start'],policy['range_end']+1) if i not in occupied),None)
                    if vmid is None: raise ValueError('Scratch range full; inspect and clean up retained test VMs manually')
                    reviewed=self.review(dict(operation='backup-restore',node=policy['node'],vmid=vmid,storage=policy['storage'],name='restore-check-'+str(vmid),archive=archive['volid'],boot_test=True,disconnected=True,timeout=policy['timeout']))
                plan=self.load(reviewed['id']);plan['execution_window']=[policy['window_start'],policy['window_end']]
                with self.m.db() as con:
                    con.execute('BEGIN IMMEDIATE')
                    current=self.m.decode(con.execute("SELECT value FROM records WHERE kind='backup-policies' AND id=?",(policy['id'],)).fetchone()['value'])
                    if not current['enabled'] or current['updated']!=policy['updated']: raise ValueError('Policy changed during review; occurrence skipped')
                    ident=reviewed['id']
                    con.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?)',(ident,'lifecycle','queued',now,now,self.m.encode({'action':'lifecycle','plan':plan}),'Scheduled '+policy['name']))
                policy['last_job']=ident;policy['last_report']='Queued. Test VMs are retained.'
            except Exception as exc: policy['last_report']=str(exc) if isinstance(exc,ValueError) else 'Policy inspection failed; no operation queued.'
            self.policy_report(policy)


def register(app,manager):
    lifecycle=Lifecycle(manager)
    def body():
        value=request.get_json()
        if not isinstance(value,dict): raise ValueError('Supply a JSON object')
        return value
    @app.get('/api/lifecycle/state',endpoint='lifecycle_state')
    def state():
        policies=manager.all('backup-policies')
        with manager.db() as con:
            for p in policies:
                row=con.execute('SELECT status FROM jobs WHERE id=?',(p.get('last_job'),)).fetchone();p['last_status']=row['status'] if row else None
        return jsonify(families=manager.all('template-families'),versions=manager.all('template-versions'),builds=manager.all('builds'),policies=policies,reports=manager.all('restore-reports'))
    @app.post('/api/lifecycle/plan',endpoint='lifecycle_plan')
    def plan(): return jsonify(lifecycle.review(body()))
    @app.post('/api/lifecycle/run',endpoint='lifecycle_run')
    def run():
        d=body();plan=lifecycle.load(d.get('plan_id'))
        if d.get('confirmed') is not True: raise ValueError('Confirm the reviewed lifecycle plan')
        if lifecycle.summary(plan)['dangerous'] and d.get('risk_confirmed') is not True: raise ValueError('Confirm the listed archive deletion or reviewed custom scripts')
        return jsonify(lifecycle.enqueue(plan,d['plan_id'])),202
    @app.post('/api/lifecycle/families')
    def family():
        d=body();value=dict(id=uuid.uuid4().hex,name=management.name(d.get('name')),preferred_id=None,created=time.time())
        return jsonify(manager.put('template-families',value))
    @app.post('/api/lifecycle/policies')
    def policy(): return jsonify(lifecycle.save_policy(body()))
    @app.post('/api/bootstrap/helper')
    def helper():
        d=body()
        if d.get('platform')=='linux': content=linux_script();filename='proxmate-bootstrap.sh'
        elif d.get('platform')=='windows':
            config=bootstrap_options(dict(d,enabled=True),'windows');content=windows_script(config);filename='ProxmateBootstrap.ps1'
        else: raise ValueError('Choose Windows or Linux')
        return app.response_class(content,mimetype='text/plain',headers={'Content-Disposition':f'attachment; filename="{filename}"'})
    return lifecycle
