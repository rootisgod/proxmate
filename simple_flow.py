"""A reviewed, single-wizard build plus software flow for new Windows/Ubuntu guests."""
import copy
import hashlib
import ipaddress
import re
import time
import uuid
from urllib.parse import quote

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from flask import jsonify,request

import management
import media
import operations
from provisioning import PROFILES,LOCALES,windows_setup_key
from guest_tasks import run_guest


FEATURES=[('Web-Server','Web server (IIS)'),('NET-Framework-45-Features','.NET Framework 4.x'),
          ('FS-FileServer','File server'),('Windows-Server-Backup','Windows Server Backup'),('RSAT','Remote administration tools')]


def choose(items,capability):
    choices=[s for s in items if s.get('active') and capability in s.get('content','').split(',')]
    if not choices: raise ValueError('No available '+capability+' storage. Check Server settings or Proxmox storage.')
    return max(choices,key=lambda s:s.get('avail',0))['storage']


def discovery(inv):
    candidates=[]
    for n in inv['nodes']:
        if n.get('status')!='online' or n['node'] not in inv['details']: continue
        d=inv['details'][n['node']]
        try: disk=choose(d['storage'],'images');iso=choose(d['storage'],'iso')
        except ValueError: continue
        bridges=[b for b in d['network'] if b.get('type')=='bridge']
        if not bridges: continue
        bridge=next((b for b in bridges if b.get('iface')=='vmbr0'),bridges[0])
        subnet=''
        try: subnet=str(ipaddress.ip_interface(bridge.get('cidr') or bridge['address']+'/'+str(bridge.get('netmask',24))).network)
        except (ValueError,KeyError): pass
        tools=[i['volid'] for i in d['isos'] if 'virtio' in i['volid'].lower() and i['volid'].lower().endswith('.iso')]
        # Prefer explicitly versioned media; guest bootstrap verifies signed installers.
        tools.sort(key=lambda v:tuple(int(x) for x in re.findall(r'\d+',v)),reverse=True)
        candidates.append(dict(node=n['node'],storage=disk,iso_storage=iso,bridge=bridge['iface'],management_cidr=subnet,
            agent_iso=tools[0] if tools else '',storage_choices=[s['storage'] for s in d['storage'] if s.get('active') and 'images' in s.get('content','').split(',')],
            iso_storages=[s['storage'] for s in d['storage'] if s.get('active') and 'iso' in s.get('content','').split(',')],bridges=[b['iface'] for b in bridges],
            driver_isos=tools,isos=[{'id':i['volid'],'label':i['volid'].split('/')[-1]} for i in d['isos'] if i['volid'].lower().endswith('.iso')]))
    if not candidates: raise ValueError('No online server has disk storage, ISO storage and a network bridge available')
    local=[{'id':'library:'+i['name'],'label':i['name']+' (ISO library)'} for i in inv['local_isos']]
    return dict(destinations=candidates,local_isos=local,profiles={k:v['label'] for k,v in PROFILES.items() if k.startswith('ubuntu-')},
                features=[{'id':key,'name':name} for key,name in FEATURES])


class SimpleFlow:
    def __init__(self,manager,lifecycle): self.m,self.life=manager,lifecycle

    def options(self): return discovery(operations.inventory())

    def existing_plan(self,d):
        targets=management.targets(d.get('targets'))
        selections=self.m.task_selection(d.get('tasks'))
        bindings={r['id']:r for r in self.m.all('connections')}
        missing=[t for t in targets if management.target_id(t) not in bindings]
        if missing:
            if d.get('credential_id'): credential=self.m.get('credentials',d['credential_id'])
            elif isinstance(d.get('credential'),dict):
                credential=self.m.save('credentials',dict(d['credential'],name='Guest login '+time.strftime('%Y-%m-%d %H:%M')))
                credential=self.m.get('credentials',credential['id'])
            else: raise ValueError('Choose a saved login or enter a username and password for these machines')
            p=operations.Proxmox();resources=p.resources();new=[]
            for t in missing:
                if not any(r.get('node')==t['node'] and r.get('vmid')==t['vmid'] and r.get('type')=='qemu' and not r.get('template') for r in resources): raise ValueError('Choose existing virtual machines')
                if any(s['item']['platform']!=credential['platform'] for s in selections): raise ValueError('The login and scripts must use the same operating system')
                path=f'nodes/{t["node"]}/qemu/{t["vmid"]}/agent/'
                try:
                    interfaces=p.call('GET',path+'network-get-interfaces')
                    hosts={a['ip-address'] for i in interfaces.get('result',[]) for a in i.get('ip-addresses',[]) if a.get('ip-address-type')=='ipv4' and not ipaddress.ip_address(a['ip-address']).is_loopback and not ipaddress.ip_address(a['ip-address']).is_link_local}
                    if len(hosts)!=1: raise ValueError('More than one address')
                    binding=dict(t,host=management.address(next(iter(hosts))),credential_id=credential['id'],platform=credential['platform'],verify_tls=True,become=True)
                    if credential['platform']=='linux':
                        import base64
                        public=p.call('GET',path+'file-read',file='/etc/ssh/ssh_host_ed25519_key.pub')['content'].split()
                        if len(public)<2 or public[0]!='ssh-ed25519': raise ValueError('Public SSH key missing')
                        binding['fingerprint']='SHA256:'+base64.b64encode(hashlib.sha256(base64.b64decode(public[1],validate=True)).digest()).decode().rstrip('=')
                    else:
                        try: binding['ca_pem']=p.call('GET',path+'file-read',file=r'C:\ProgramData\Proxmate\winrm.pem')['content']
                        except Exception: pass # Existing enterprise-trusted WinRM certificates use system/credential trust.
                    new.append(binding)
                except Exception as exc:
                    raise ValueError('Could not detect the connection for VM '+str(t['vmid'])+'. Check its guest agent or use Machines > Connection once to enter its address and trust settings.') from exc
            for binding in new: self.m.save('connections',binding)
        return self.m.create_plan(dict(operation='tasks',targets=targets,tasks=d['tasks']))

    def plan(self,d):
        if not isinstance(d,dict): raise ValueError('Supply wizard settings')
        os=d.get('os','windows')
        if os not in ('windows','ubuntu'): raise ValueError('Choose Windows Server or Ubuntu')
        count=management.number(d.get('count',1),1,20);purpose=d.get('purpose','vm')
        if purpose not in ('vm','template'): raise ValueError('Choose machines or a template')
        if purpose=='template' and count!=1: raise ValueError('Create one reusable template at a time')
        prefix=d.get('name','')
        if not isinstance(prefix,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,11}',prefix) or prefix.endswith('-'):
            raise ValueError('Use a name beginning with a letter, up to 12 letters, numbers or hyphens')
        inv=operations.inventory();options=discovery(inv)
        dest=next((n for n in options['destinations'] if n['node']==d.get('node',options['destinations'][0]['node'])),None)
        if not dest: raise ValueError('The chosen server is no longer available')
        dest=copy.deepcopy(dest)
        for key,choices in [('storage','storage_choices'),('bridge','bridges'),('iso_storage','iso_storages')]:
            if d.get(key):
                if d[key] not in dest[choices]: raise ValueError('Selected '+key+' is no longer available')
                dest[key]=d[key]
        if not operations.connection_provider.current().get('ssh_fingerprint'):
            raise ValueError('Finish SSH setup on the Server page so Proxmate can read media and prepare images')
        requests=d.get('tasks',[])
        if not isinstance(requests,list): raise ValueError('Choose software or saved scripts')
        selections=self.m.task_selection(requests) if requests else []
        if any(t['item']['platform']!=('windows' if os=='windows' else 'linux') for t in selections):
            raise ValueError('Choose scripts for the selected operating system')
        credential=dict(id=uuid.uuid4().hex,name=prefix+' login',platform='windows' if os=='windows' else 'linux')
        public_key='';inspected=None;edition=None
        if os=='windows':
            credential.update(username='Administrator',password=d.get('password',''))
            if not credential['password']: raise ValueError('Set an Administrator password so software can be installed automatically')
            source=d.get('source','');inspected=media.inspect_source(source,dest['node'])
            edition=next((e for e in inspected['editions'] if str(e['index'])==str(d.get('edition'))),None)
            if not edition: raise ValueError('Choose a Windows Server edition from this ISO')
            if int(edition.get('build') or 0)<17763: raise ValueError('Automatic setup supports Windows Server 2019 or newer. Use Advanced for older images.')
            agent=d.get('agent_iso') or dest['agent_iso']
            if not agent or agent not in dest['driver_isos']: raise ValueError('Add a trusted VirtIO guest-tools ISO to Proxmox, then refresh. It supplies the Windows guest agent.')
            candidates=[agent] if d.get('agent_manual') else [agent]+[v for v in dest['driver_isos'] if v!=agent]
            agent=next((v for v in candidates if media.supports_guest_agent(v,dest['node'],edition['build'])),None)
            if not agent: raise ValueError('The available VirtIO media does not contain the guest agent and serial driver for this Windows version. Add a current VirtIO ISO, then refresh.')
            subnet=d.get('management_cidr') or dest['management_cidr']
            dest.update(agent_iso=agent,management_cidr=subnet,agent_sha256=media.guest_agent_sha256(agent,dest['node']))
        else:
            profile=d.get('profile','ubuntu-24.04')
            if profile not in options['profiles']: raise ValueError('Choose a supported Ubuntu release')
            media.verify_ssh_node(dest['node'])
            if not isinstance(d.get('username','ubuntu'),str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',d.get('username','ubuntu')): raise ValueError('Choose an Ubuntu login name using lowercase letters, numbers, underscores or hyphens')
            key=ed25519.Ed25519PrivateKey.generate()
            public_key=key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH).decode()
            credential.update(username=d.get('username','ubuntu'),private_key=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption()).decode(),password='')
        used={r['vmid'] for r in inv['resources'] if r.get('vmid')}
        names={r.get('name','').lower() for r in inv['resources']}
        with self.m.db() as con:
            for row in con.execute("SELECT payload FROM jobs WHERE status IN ('queued','running')"):
                payload=self.m.decode(row['payload']);cfg=payload.get('plan',{}).get('config',payload)
                if cfg.get('vmid'): used.add(int(cfg['vmid']))
                if cfg.get('name'): names.add(cfg['name'].lower())
        vmid=max(100,int(inv['next_id']));configs=[]
        for i in range(count):
            while vmid in used: vmid+=1
            name=prefix if count==1 else f'{prefix}-{i+1:02}'
            if name.lower() in names: raise ValueError('A machine named '+name+' already exists. Choose a different name.')
            cfg=dict(action='provision',kind='windows' if os=='windows' else 'linux-cloud',purpose='vm',node=dest['node'],vmid=vmid,name=name,
                storage=dest['storage'],bridge=dest['bridge'],cores=d.get('cores',2),memory=d.get('memory',4096 if os=='windows' else 2048),
                disk=d.get('disk',64 if os=='windows' else 32),start=True)
            b=dict(enabled=True,verify=True,credential_id=credential['id'],timeout=3600)
            if os=='windows':
                locale=d.get('locale','en-GB');language=edition['language']
                if language not in LOCALES: raise ValueError('This image language is not supported by the automatic answer file')
                cfg.update(source_kind='local' if source.startswith('library:') else 'proxmox',iso_storage=dest['iso_storage'],
                    windows=dict(image_index=edition['index'],hostname=name,admin_password=credential['password'],ui_language=language,
                        locale=locale,keyboard=locale,timezone=d.get('timezone','GMT Standard Time'),accept_eula=True,wipe_disk=True,rdp=bool(d.get('rdp',False))))
                cfg['filename' if source.startswith('library:') else 'iso']=source[8:] if source.startswith('library:') else source
                setup_key=windows_setup_key(edition)
                if setup_key: cfg['windows']['setup_key']=setup_key
                b.update(agent_iso=dest['agent_iso'],management_cidr=dest['management_cidr'],agent_sha256=dest['agent_sha256'])
            else:
                cfg.update(profile=profile,username=credential['username'],ssh_key=public_key,network_mode='dhcp')
                b.update(seed_storage=dest['iso_storage'])
            cfg['bootstrap']=b;configs.append(operations.validate(cfg));used.add(vmid);vmid+=1
        available=next(s for s in inv['details'][dest['node']]['storage'] if s['storage']==dest['storage']).get('avail')
        if available is not None and sum(c['disk'] for c in configs)*1073741824>available:
            raise ValueError('Not enough free disk space for this batch. Reduce the size/count or choose another storage under Advanced.')
        plan=dict(operation='simple-build',configs=configs,credential=credential,tasks=selections,purpose=purpose,
            source=d.get('source','') if os=='windows' else profile,media_identity=inspected['identity'] if inspected else None,
            edition=edition['label'] if edition else PROFILES[profile]['label'],dangerous=any(t['item'].get('dangerous') for t in selections))
        ident=uuid.uuid4().hex
        with self.m.db() as con: con.execute('INSERT INTO plans VALUES (?,?,?)',(ident,time.time(),self.m.encode(plan)))
        return dict(id=ident,machines=[{k:c[k] for k in ('vmid','name','node','memory','cores','disk','storage','bridge')} for c in configs],
            edition=plan['edition'],purpose=purpose,tasks=[{'name':t['item']['name'],'params':t['params']} for t in selections],dangerous=plan['dangerous'],
            remote_access='HTTPS WinRM' if os=='windows' else 'SSH',management_cidr=dest['management_cidr'] if os=='windows' else None,
            guest_tools=dest['agent_iso'] if os=='windows' else 'Installed automatically',
            notes=['The new machines start automatically. Each gets its own progress report.',
                'Guest tools come from your selected VirtIO ISO. The MSI checksum is frozen and verified before installation; Windows enforces driver signatures.' if os=='windows' else 'Guest tools come from the operating system package repositories.',
                ('Windows setup uses Microsoft\'s public KMS client key for the selected edition. This is not a licence or retail activation key; use your organisation\'s KMS or your own appropriate activation method afterwards.' if windows_setup_key(edition) else 'No generic setup key is supplied for this Windows image. Evaluation media uses its own evaluation terms; other media may ask for a product key in the console.') if os=='windows' else 'Ubuntu does not need a product key.',
                'Proxmate attempts the Windows DVD boot key automatically. If setup does not start, open the Proxmox console; the job waits for installation.' if os=='windows' else 'Ubuntu uses the official cloud image, with SSH and guest tools configured automatically.',
                'Software may restart the new guest when required. Failed machines are retained for inspection.',
                'Templates are generalized and shut down after software installation.' if purpose=='template' else 'Saved guest connections make later scripts a choose-and-run operation.'])

    def enqueue(self,d):
        plan=self.m.load_plan(d.get('plan_id'))
        if plan.get('operation')!='simple-build': raise ValueError('Choose a build plan')
        if d.get('confirmed') is not True: raise ValueError('Confirm the listed new machines, software and Windows licensing before creating')
        if plan['dangerous'] and d.get('risk_confirmed') is not True: raise ValueError('Confirm that you trust the selected scripts')
        credential=plan['credential']
        if not any(c['id']==credential['id'] for c in self.m.all('credentials')): self.m.save('credentials',credential)
        jobs=[]
        with self.m.db() as con:
            con.execute('BEGIN IMMEDIATE')
            for i,cfg in enumerate(plan['configs']):
                ident=hashlib.sha256((d['plan_id']+':'+str(i)).encode()).hexdigest()[:32]
                execution={k:plan[k] for k in ('tasks','purpose','source','media_identity')};execution.update(operation='build '+cfg['name'],config=cfg)
                payload=dict(action='simple',plan=execution,name=cfg['name'],node=cfg['node'],vmid=cfg['vmid'])
                con.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?)',(ident,'simple','queued',time.time(),time.time(),self.m.encode(payload),'Queued: create, prepare remote access, install software.'))
                jobs.append({'id':ident,'name':cfg['name'],'url':'/api/jobs/'+ident})
        return dict(jobs=jobs)

    def execute(self,plan,log,job_id):
        cfg=copy.deepcopy(plan['config'])
        if plan.get('media_identity'):
            if media.inspect_source(plan['source'],cfg['node'])['identity']!=plan['media_identity']: raise ValueError('Windows media changed since review')
            if cfg['source_kind']=='local':
                p=operations.Proxmox();volume=cfg['iso_storage']+':iso/'+cfg['filename']
                existing=p.call('GET',f'nodes/{cfg["node"]}/storage/{cfg["iso_storage"]}/content',content='iso')
                if any(i['volid']==volume for i in existing):
                    if media.inspect_source(volume,cfg['node'])['identity']!=plan['media_identity']: raise ValueError('A different ISO already uses this filename; nothing overwritten')
                    cfg.update(source_kind='proxmox',iso=volume)
        log('Creating '+cfg['name']+'; preparing '+('WinRM' if cfg['kind']=='windows' else 'SSH')+' automatically.')
        result=operations.execute(cfg,log)
        self.life.after_provision(cfg,result,job_id,log)
        if plan['tasks']:
            review=self.m.create_plan(dict(operation='readiness',targets=[management.target(cfg)]))
            taskplan=self.m.load_plan(review['id']);taskplan.update(operation='tasks',tasks=plan['tasks'])
            self.assert_owned(cfg,job_id)
            self.m.execute(taskplan,log);self.life.record_tasks(taskplan,job_id)
        if plan['purpose']=='template': self.finalize_template(cfg,log)
        log('Ready: '+cfg['name']+(' template.' if plan['purpose']=='template' else '. Run more saved scripts from Machines whenever you need them.'))

    def assert_owned(self,cfg,job_id):
        from lifecycle import machine_identity
        built=self.m.get('builds',job_id)
        current=operations.Proxmox().call('GET',f'nodes/{cfg["node"]}/qemu/{cfg["vmid"]}/config')
        if built['machine_identity']!=machine_identity(current): raise ValueError('The newly created VM was replaced; software was not applied')

    def finalize_template(self,cfg,log):
        from template_finish import finish
        finish(cfg,self.m,log)


def register(app,manager,lifecycle):
    flow=SimpleFlow(manager,lifecycle)
    @app.get('/api/simple/software')
    def software(): return jsonify(features=[{'id':key,'name':name} for key,name in FEATURES])
    @app.get('/api/simple/options')
    def options(): return jsonify(flow.options())
    @app.post('/api/simple/editions')
    def editions():
        d=request.get_json();node=management.word(d.get('node'))
        return jsonify(media.inspect_source(d.get('source'),node))
    @app.post('/api/simple/plan',endpoint='simple_plan')
    def plan(): return jsonify(flow.plan(request.get_json()))
    @app.post('/api/simple/run',endpoint='simple_run')
    def run(): return jsonify(flow.enqueue(request.get_json())),202
    @app.post('/api/simple/existing-plan')
    def existing_plan(): return jsonify(flow.existing_plan(request.get_json()))
    return flow
