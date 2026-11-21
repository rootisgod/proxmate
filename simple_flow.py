"""A reviewed, single-wizard build plus software flow for new Windows and Linux guests."""
import copy
import hashlib
import ipaddress
import re
import time
import threading
import uuid
from requests import RequestException

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from flask import jsonify,request

import management
import media
import diagnostics
from guided_recipes import GuidedRecipes, specification
from reviews import Reviews
import operations
import debian_build
import rocky_build
import linux_access
import linux_install
import ubuntu_build
import windows_build
import windows_setup
from provisioning import PROFILES,LOCALES,windows_setup_key,windows_options
from guest_tasks import DOMAIN_CONTROLLER, public_params, selected_drive_letters
from vm_config import machine_identity, vm_path

LINUX_INSTALLERS = {'ubuntu': ubuntu_build, 'debian': debian_build, 'rocky': rocky_build}
ISO_PROFILES = {'ubuntu': 'ubuntu-24.04', 'debian': 'debian-13', 'rocky': 'rocky-10'}


FEATURES=[('Web-Server','Web server (IIS)'),('NET-Framework-45-Features','.NET Framework 4.x'),
          ('FS-FileServer','File server'),('Windows-Server-Backup','Windows Server Backup'),('RSAT','Remote administration tools'),
          ('NET-Framework-Core','.NET Framework 3.5'),('Telnet-Client','Telnet client')]


def require_unbound_target(con, config):
    key=management.target_id(config)
    if con.execute("SELECT 1 FROM records WHERE kind='connections' AND id=?",(key,)).fetchone():
        raise ValueError(f'{key} already has a saved guest connection. Review the build again to choose a free VM ID.')


def reserved_targets(con,decode):
    ids,names=set(),set()
    for row in con.execute("SELECT payload FROM jobs WHERE status IN ('queued','running','waiting')"):
        payload=decode(row['payload'])
        cfg=payload.get('plan',{}).get('config',payload)
        if cfg.get('vmid'): ids.add(int(cfg['vmid']))
        if cfg.get('name'): names.add(cfg['name'].lower())
    return ids,names


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
        subnets={}
        for item in bridges:
            try: subnets[item['iface']]=str(ipaddress.ip_interface(item.get('cidr') or item['address']+'/'+str(item['netmask'])).network)
            except (ValueError, KeyError): continue
        subnet=subnets.get(bridge['iface'],'')
        tools=[i['volid'] for i in d['isos'] if 'virtio' in i['volid'].lower() and i['volid'].lower().endswith('.iso')]
        # Prefer explicitly versioned media; guest bootstrap verifies signed installers.
        tools.sort(key=lambda v:tuple(int(x) for x in re.findall(r'\d+',v)),reverse=True)
        candidates.append(dict(node=n['node'],storage=disk,iso_storage=iso,bridge=bridge['iface'],management_cidr=subnet,bridge_subnets=subnets,
            agent_iso=tools[0] if tools else '',storage_choices=[s['storage'] for s in d['storage'] if s.get('active') and 'images' in s.get('content','').split(',')],
            iso_storages=[s['storage'] for s in d['storage'] if s.get('active') and 'iso' in s.get('content','').split(',')],bridges=[b['iface'] for b in bridges],
            driver_isos=tools,isos=[{'id':i['volid'],'label':i['volid'].split('/')[-1],'revision':media.volume_revision(i)} for i in d['isos'] if i['volid'].lower().endswith('.iso')]))
    if not candidates: raise ValueError('No online server has disk storage, ISO storage and a network bridge available')
    local=[{'id':'library:'+i['name'],'label':i['name']+' (ISO library)','revision':str(i['mtime'])+'-'+str(i['bytes']) if i.get('mtime') else None} for i in inv['local_isos']]
    for dest in candidates:
        for distro, installer in LINUX_INSTALLERS.items():
            dest[distro+'_isos'] = installer.candidates(dest['isos'] + local)
    return dict(destinations=candidates,local_isos=local,windows_settings=windows_setup.OPTIONS,
                rocky=dict(release=rocky_build.LABEL,profile=ISO_PROFILES['rocky'],packages=[dict(id=k,name=n) for k,n in rocky_build.PACKAGES]),
                debian=dict(release=debian_build.LABEL,packages=[dict(id=k,name=n) for k,n in ubuntu_build.PACKAGES]),
                ubuntu=dict(release=ubuntu_build.LABEL,install_sources=ubuntu_build.INSTALL_SOURCES,firewall=ubuntu_build.FIREWALL,updates=ubuntu_build.UPDATES,
                            packages=[dict(id=key,name=name) for key,name in ubuntu_build.PACKAGES]),
                iso_setup_letters=windows_build.ISO_SETUP_LETTERS,
                common_programs=[dict(id=key,name=name) for key,name in windows_setup.PROGRAMS],profiles={k:v['label'] for k,v in PROFILES.items() if k.startswith('ubuntu-')},
                features=[{'id':key,'name':name} for key,name in FEATURES])


class SimpleFlow:
    def __init__(self,manager,lifecycle):
        self.m,self.life=manager,lifecycle
        self.recipes=GuidedRecipes(manager)
        self.media_cache={}
        self.cache_lock=threading.Lock()

    def reviewed_media(self,source,node,revision=None,build=None):
        server=operations.connection_provider.current()
        key=(server['id'],server.get('ssh_fingerprint'),node,source,build)
        with self.cache_lock:
            saved=self.media_cache.get(key)
            if revision and saved and saved[0]==revision and saved[1]>time.monotonic():
                diagnostics.event('media.cache',kind='drivers' if build else 'editions',hit=True)
                return saved[2]
        diagnostics.event('media.cache',kind='drivers' if build else 'editions',hit=False)
        if build:
            result=media.review_guest_agent(source,node,build)
        else:
            result=media.inspect_source(source,node)
            revision=result.get('revision')
        if revision:
            with self.cache_lock:
                if len(self.media_cache)>=64: self.media_cache.pop(next(iter(self.media_cache)))
                self.media_cache[key]=(revision,time.monotonic()+600,result)
        return result

    def options(self, inv=None, *, include_templates=True):
        if inv is None: inv=operations.inventory(include_backups=False)
        options=discovery(inv)
        if include_templates:
            builds=linux_install.template_builds(self.m)
            options.update(linux_install.templates(builds,inv['resources']))
        destinations=options['destinations']
        if len(destinations)>1 and operations.connection_provider.current().get('ssh_fingerprint'):
            try: connected=media.ssh_node()
            except (ValueError,OSError,RequestException): connected=None
            if connected: destinations.sort(key=lambda dest:dest['node']!=connected)
        return options

    def existing_plan(self,d):
        if not isinstance(d,dict): raise ValueError('Supply guest run settings')
        targets=management.targets(d.get('targets'))
        if 'one_off' in d:
            draft=d['one_off']
            if not isinstance(draft,dict) or 'tasks' in d:
                raise ValueError('Choose library tasks or one pasted script for this run')
            item=self.m.prepare('tasks',dict(name='One-off script',platform=draft.get('platform'),
                kind=draft.get('kind'),body=draft.get('body'),parameters=draft.get('parameters',{}),
                input_types=draft.get('input_types',{}),defaults=draft.get('defaults',{})))
            selections=[self.m.select_task(item,draft.get('params',{}))]
        else:
            selections=self.m.task_selection(d.get('tasks'))
        bindings={r['id']:r for r in self.m.all('connections')}
        missing=[t for t in targets if management.target_id(t) not in bindings]
        discovered={}
        if missing:
            if d.get('credential_id'): credential=self.m.get('credentials',d['credential_id'])
            elif isinstance(d.get('credential'),dict):
                fields={k:d['credential'].get(k) for k in ('platform','username','password','private_key','become_password','ca_pem')}
                credential=self.m.prepare('credentials',dict(fields,name='Guest login '+time.strftime('%Y-%m-%d %H:%M')))
                discovered['credentials',credential['id']]=credential
            else: raise ValueError('Choose a saved login or enter a username and password for these machines')
            p=operations.Proxmox();resources=p.resources()
            for t in missing:
                if not any(r.get('node')==t['node'] and r.get('vmid')==t['vmid'] and r.get('type')=='qemu' and not r.get('template') for r in resources): raise ValueError('Choose existing virtual machines')
                if any(s['item']['platform']!=credential['platform'] for s in selections): raise ValueError('The login and scripts must use the same operating system')
                path=f'nodes/{t["node"]}/qemu/{t["vmid"]}/agent/'
                try:
                    interfaces=p.call('GET',path+'network-get-interfaces')
                    hosts={a['ip-address'] for i in interfaces.get('result',[]) for a in i.get('ip-addresses',[]) if a.get('ip-address-type')=='ipv4' and not ipaddress.ip_address(a['ip-address']).is_loopback and not ipaddress.ip_address(a['ip-address']).is_link_local}
                    if len(hosts)!=1: raise ValueError('More than one address')
                    binding=dict(t,host=management.address(next(iter(hosts))),credential_id=credential['id'],platform=credential['platform'],verify_tls=True,become=True)
                    if credential['platform']=='linux' or not credential.get('ca_pem'):
                        try: binding.update(management.agent_trust(p,path.removesuffix('/agent/'),credential['platform']))
                        except RuntimeError:
                            if credential['platform']=='linux': raise
                            # Existing Windows listeners may chain to a system-trusted CA.
                    record=self.m.prepare('connections',binding,credential)
                    discovered['connections',record['id']]=record
                except Exception as exc:
                    raise ValueError('Could not detect the connection for VM '+str(t['vmid'])+'. Check its guest agent or use Operations > Refresh connection to verify its address and login.') from exc
        return self.m.create_plan(dict(operation='tasks',targets=targets),selections=selections,discovered=discovered)

    def plan(self,d,progress=lambda stage:None):
        if not isinstance(d,dict): raise ValueError('Supply wizard settings')
        reused=None
        if 'reuse' in d:
            d,reused=self.recipes.expand(d)
        os=d.get('os','windows')
        if not isinstance(os,str) or (os!='windows' and os not in LINUX_INSTALLERS):
            raise ValueError('Choose Windows Server, Ubuntu, Debian or Rocky Linux')
        count=management.number(d.get('count',1),1,20);purpose=d.get('purpose','vm')
        if purpose not in ('vm','template'): raise ValueError('Choose machines or a template')
        if purpose=='template' and count!=1: raise ValueError('Create one reusable template at a time')
        prefix=d.get('name','')
        if not isinstance(prefix,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,11}',prefix) or prefix.endswith('-'):
            raise ValueError('Use a name beginning with a letter, up to 12 letters, numbers or hyphens')
        if os == 'windows' and d.get('source_kind') != 'template':
            windows_build.check_iso_disk_letters(d.get('data_disks', []))
        progress('inventory')
        with diagnostics.stage('inventory'):
            inv=operations.inventory(include_backups=False,only_node=d.get('node'))
            options=discovery(inv) if d.get('node') else self.options(inv,include_templates=False)
        dest=next((n for n in options['destinations'] if n['node']==d.get('node',options['destinations'][0]['node'])),None)
        if not dest: raise ValueError('The chosen server is no longer available')
        dest=copy.deepcopy(dest)
        for key,choices in [('storage','storage_choices'),('bridge','bridges'),('iso_storage','iso_storages')]:
            if d.get(key):
                if d[key] not in dest[choices]: raise ValueError('Selected '+key+' is no longer available')
                dest[key]=d[key]
        cloning=os=='windows' and d.get('source_kind')=='template'
        source_kind=d.get('source_kind','iso' if os in ('debian','rocky') else None)
        linux_iso=os!='windows' and source_kind=='iso'
        linux_clone=os!='windows' and source_kind=='template'
        if os in ('debian','rocky') and source_kind not in ('iso','template'):
            raise ValueError('Choose a '+os.capitalize()+' installer ISO or prepared '+os.capitalize()+' template.')
        if os=='ubuntu' and d.get('source_kind') not in (None,'cloud','iso','template'):
            raise ValueError('Choose an Ubuntu ISO, cloud image or template.')
        if 'ubuntu_install_source' in d and not (os=='ubuntu' and linux_iso):
            raise ValueError('Ubuntu installation images apply to Ubuntu ISO builds.')
        if (cloning or linux_clone) and purpose!='vm': raise ValueError('Choose a working VM for a template clone')
        if not (cloning or linux_clone) and not operations.connection_provider.current().get('ssh_fingerprint'):
            raise ValueError('Finish SSH setup on the Server page so Proxmate can read media and prepare images')
        requests=d.get('tasks',[])
        if not isinstance(requests,list): raise ValueError('Choose software or saved scripts')
        selections=self.m.task_selection(requests) if requests else []
        if any(s['item']['id']==DOMAIN_CONTROLLER for s in selections) and (count!=1 or purpose!='vm'):
            raise ValueError('Create one working Windows VM for the first domain controller.')
        requested_tasks=[dict(id=s['item']['id'],params=public_params(s)) for s in selections]
        if any(t['item']['platform']!=('windows' if os=='windows' else 'linux') for t in selections):
            raise ValueError('Choose scripts for the selected operating system')
        credential=dict(id=uuid.uuid4().hex,name=prefix+' login',platform='windows' if os=='windows' else 'linux')
        public_key='';inspected=None;edition=None;personal_keys=[];password_task=None
        if os=='windows':
            if management.boolean(d.get('manual_updates',False)):
                selections=[{'item':self.m.task('windows-manual-updates'),'params':{}}]+selections
            credential.update(username='Administrator',password=d.get('password',''))
            if not credential['password']: raise ValueError('Set an Administrator password so software can be installed automatically')
            if cloning:
                template=windows_build.review_template(self.life,dest,d)
                dest.update(management_cidr=template['management_cidr'],bridge=template['bridge'])
            else:
                source=d.get('source','')
                if not isinstance(source,str): raise ValueError('Choose a Windows ISO')
                revisions={i['id']:i.get('revision') for i in options['local_isos']+dest['isos']}
                progress('editions')
                with diagnostics.stage('editions'):
                    inspected=self.reviewed_media(source,dest['node'],revisions.get(source))
                if d.get('edition_identity') and d['edition_identity']!=inspected['identity']: raise ValueError('ISO editions changed. Refresh the source before reviewing.')
                edition=next((e for e in inspected['editions'] if str(e['index'])==str(d.get('edition'))),None)
                if not edition: raise ValueError('Choose a Windows Server edition from this ISO')
                if int(edition.get('build') or 0)<17763: raise ValueError('Automatic setup supports Windows Server 2019 or newer. Use Advanced for older images.')
                agent=d.get('agent_iso') or dest['agent_iso']
                if not agent or agent not in dest['driver_isos']: raise ValueError('Add a trusted VirtIO guest-tools ISO to Proxmox, then refresh. It supplies the Windows guest agent.')
                candidates=[agent] if d.get('agent_manual') else [agent]+[v for v in dest['driver_isos'] if v!=agent]
                progress('drivers')
                with diagnostics.stage('drivers',candidates=len(candidates)):
                    for agent in candidates:
                        checksum=self.reviewed_media(agent,dest['node'],revisions.get(agent),edition['build'])
                        if checksum: break
                if not checksum: raise ValueError('The available VirtIO media does not contain the guest agent and serial driver for this Windows version. Add a current VirtIO ISO, then refresh.')
                subnet=d.get('management_cidr') or dest['management_cidr']
                dest.update(agent_iso=agent,management_cidr=subnet,agent_sha256=checksum)
        else:
            distro=os.capitalize()
            installer=LINUX_INSTALLERS[os]
            profile=d.get('profile',ISO_PROFILES[os])
            if profile not in (options['profiles'] if os=='ubuntu' else (ISO_PROFILES[os],)):
                raise ValueError('Choose a supported '+distro+' release.')
            if not linux_clone: media.verify_ssh_node(dest['node'])
            if os=='rocky': rocky_build.check_cpu(operations.Proxmox(),dest['node'])
            if linux_clone:
                template=linux_install.review_template(self.life,dest,d,os)
                if os=='ubuntu':
                    profile=template['profile']
                    if d.get('profile',profile)!=profile:
                        raise ValueError('The requested Ubuntu release does not match the selected template.')
                dest['bridge']=template['bridge']
                if template['mode']=='linked': dest['storage']=template['storage']
            if linux_iso:
                isos=dest[os+'_isos']
                source=d.get('source') or (isos[0]['id'] if os!='ubuntu' and isos else None)
                if not any(source==item['id'] for item in isos):
                    raise ValueError('Choose a discovered '+installer.LABEL+' installer ISO.')
                progress(os+'_media')
                with diagnostics.stage(os+'_media'):
                    inspected=installer.inspect(source,dest['node'])
                if os=='ubuntu':
                    profile=inspected['profile']
                    if d.get('profile',profile)!=profile:
                        raise ValueError('The requested Ubuntu release does not match the selected ISO.')
                    install_source=d.get('ubuntu_install_source','ubuntu-server')
                    if not isinstance(install_source,str) or install_source not in inspected['install_sources']:
                        raise ValueError('Choose a Server installation image available on the selected Ubuntu ISO.')
            if not isinstance(d.get('username',os),str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',d.get('username',os)): raise ValueError('Choose a Linux login name using lowercase letters, numbers, underscores or hyphens')
            if d.get('username') in ('root','daemon','bin','sys','sync','games','man','lp','mail','news','uucp','proxy','www-data','backup','list','irc','_apt','nobody','messagebus','systemd-network','systemd-timesync','sshd'):
                raise ValueError('Choose a Linux login name other than a system account.')
            password=linux_access.requested_password(d,os)
            if password != '':
                if purpose!='vm':
                    raise ValueError('Choose a password for a working Linux VM or when cloning its template.')
                password_task=linux_access.password_login(d.get('username',os),password,os)
            personal=d.get('ssh_public_key','')
            if not isinstance(personal,str) or len(personal)>8192:
                raise ValueError('Enter one OpenSSH public key from your workstation.')
            if personal.strip():
                personal_keys.append(linux_access.public_key(personal.strip()))
            github=d.get('github_username','')
            if not isinstance(github,str): raise ValueError('Enter a GitHub username.')
            if github.strip():
                progress('github_keys')
                personal_keys.extend(linux_access.github_keys(github.strip()))
            personal_keys=list(dict.fromkeys(personal_keys))
            key=ed25519.Ed25519PrivateKey.generate()
            public_key=key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH).decode()
            public_key='\n'.join([public_key,*personal_keys])
            if len(public_key)>16000: raise ValueError('Too many SSH public keys. Paste one key.')
            credential.update(username=d.get('username',os),private_key=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption()).decode(),password='')
            if os=='ubuntu': selections=ubuntu_build.setting_tasks(d)+selections
            if password_task: selections.append(password_task)
        selections=windows_setup.select(d.get('windows_settings',[]),selections,self.m,platform=os,purpose=purpose,
                                        subnet=dest['management_cidr'],desktop=edition.get('desktop') if edition else None)
        if edition:
            for selected in selections:
                task_id, params = selected['item']['id'], selected['params']
                if task_id==DOMAIN_CONTROLLER and edition.get('desktop') is False:
                    raise ValueError('Choose Desktop Experience for the domain controller and its desktop consoles.')
                if task_id == 'windows-feature' and params['feature'] == 'NET-Framework-Core':
                    selected['item']['installer_sxs'] = True
                if task_id == 'chocolatey-package' and params['package'].lower() == 'microsoft-windows-terminal':
                    if edition.get('desktop') is False or int(edition['build']) < 19041:
                        raise ValueError('Windows Terminal requires Desktop Experience and Windows build 19041 or later')
        used={r['vmid'] for r in inv['resources'] if r.get('vmid')}
        names={r.get('name','').lower() for r in inv['resources']}
        with self.m.db() as con:
            # Proxmox can release a VM ID while its guest connection is still pinned here.
            saved_connections={row['id'] for row in con.execute("SELECT id FROM records WHERE kind='connections'")}
            reserved_ids,reserved_names=reserved_targets(con,self.m.decode)
            used.update(reserved_ids)
            names.update(reserved_names)
        vmid=max(100,int(inv['next_id']));configs=[]
        for i in range(count):
            while vmid in used or f"{dest['node']}:{vmid}" in saved_connections: vmid+=1
            name=prefix if count==1 else f'{prefix}-{i+1:02}'
            if name.lower() in names: raise ValueError('A machine named '+name+' already exists. Choose a different name.')
            cfg=dict(action='provision',kind='windows' if os=='windows' else 'linux-cloud',purpose='vm',node=dest['node'],vmid=vmid,name=name,
                storage=dest['storage'],bridge=dest['bridge'],cores=d.get('cores',2),memory=d.get('memory',4096 if os=='windows' or linux_iso else 2048),
                disk=d.get('disk',64 if os=='windows' else 32),start=True)
            b=dict(enabled=True,verify=True,credential_id=credential['id'],timeout=3600)
            if os=='windows':
                cfg['data_disks']=windows_build.disks_requested(d.get('data_disks',[]),template['slots'] if cloning else ('sata3','sata4','sata5'))
                from winrm_setup import check_letters
                reserved={disk['letter'] for disk in cfg['data_disks']}
                if not cloning: reserved.update(windows_build.ISO_SETUP_LETTERS)
                check_letters(selected_drive_letters(selections),reserved)
                cfg['cores']=management.number(cfg['cores'],1,128)
                cfg['memory']=management.number(cfg['memory'],4096,1048576)
                if cloning:
                    cfg.update(source_kind='template',template=template,iso_storage=dest['iso_storage'],disk=template['disk_gib'],
                               windows=windows_options(dict(hostname=name,admin_password=credential['password'],locale=d.get('locale','en-GB'),
                                                            keyboard=d.get('locale','en-GB'),timezone=d.get('timezone','GMT Standard Time'),rdp=management.boolean(d.get('rdp',False)),
                                                            accept_eula=True,wipe_disk=False)))
                    b.update(management_cidr=template['management_cidr'],clone_token=uuid.uuid4().hex)
                else:
                    locale=d.get('locale','en-GB');language=edition['language']
                    if language not in LOCALES: raise ValueError('This image language is not supported by the automatic answer file')
                    cfg.update(source_kind='local' if source.startswith('library:') else 'proxmox',iso_storage=dest['iso_storage'],
                        windows=dict(image_index=edition['index'],hostname=name,admin_password=credential['password'],ui_language=language,
                            locale=locale,keyboard=locale,timezone=d.get('timezone','GMT Standard Time'),accept_eula=True,wipe_disk=True,rdp=management.boolean(d.get('rdp',False))))
                    cfg['filename' if source.startswith('library:') else 'iso']=source[8:] if source.startswith('library:') else source
                    setup_key=windows_setup_key(edition)
                    if setup_key: cfg['windows']['setup_key']=setup_key
                    b.update(agent_iso=dest['agent_iso'],management_cidr=dest['management_cidr'],agent_sha256=dest['agent_sha256'])
            else:
                cfg.update(profile=profile,username=credential['username'],ssh_key=public_key,network_mode='dhcp')
                b.update(seed_storage=dest['iso_storage'])
            cfg['bootstrap']=b
            if os in ('debian','rocky'): operations.validate_vm_fields(cfg)
            elif not cloning: cfg=operations.validate(cfg)
            if linux_iso:
                if cfg['memory']<3072 or cfg['disk']<16:
                    raise ValueError(distro+' ISO installation requires at least 3 GiB RAM and 16 GiB disk.')
                cfg.update(kind=os+'-iso',source_kind='iso',iso_storage=dest['iso_storage'],
                           install_uuid=str(uuid.uuid4()),disk_serial='pm'+uuid.uuid4().hex[:16])
                if os=='debian':
                    cfg['preseed']=debian_build.preseed(cfg)
                    cfg['reusable_preseed']=debian_build.preseed(cfg,reusable=True)
                elif os=='rocky':
                    cfg['dvd_label']=inspected['label']
                    cfg.update(media_kind=inspected['kind'],rocky_version=inspected['version'])
                    cfg['kickstart']=rocky_build.kickstart(cfg)
                    cfg['reusable_kickstart']=rocky_build.kickstart(cfg,reusable=True)
                else:
                    cfg.update(ubuntu_install_source=install_source,
                               ubuntu_source_record=list(inspected['install_sources'][install_source]))
                    cfg['autoinstall']=ubuntu_build.user_data(cfg)
                    cfg['reusable_autoinstall']=ubuntu_build.user_data(cfg,reusable=True)
            elif linux_clone:
                cfg.update(kind=os+'-clone',source_kind='template',iso_storage=dest['iso_storage'],
                           disk=template['disk_gib'],template=template)
            configs.append(cfg);used.add(vmid);vmid+=1
        available=next(s for s in inv['details'][dest['node']]['storage'] if s['storage']==dest['storage']).get('avail')
        if available is not None and sum((0 if (cloning or linux_clone) and template['mode']=='linked' else c['disk'])+sum(disk['size'] for disk in c.get('data_disks',[])) for c in configs)*1073741824>available:
            raise ValueError('Not enough free disk space for this batch. Reduce the quantity or choose a storage with more space under Placement and hardware.')
        plan=dict(operation='simple-build',configs=configs,credential=credential,tasks=selections,purpose=purpose,
            source=source if linux_iso else d.get('source','') if os=='windows' else profile,media_identity=inspected['identity'] if inspected else None,
            edition=template['name'] if cloning or linux_clone else edition['label'] if edition else inspected['release'] if linux_iso else PROFILES[profile]['label'],dangerous=any(t['item'].get('dangerous') for t in selections))
        if os=='ubuntu' and linux_iso:
            plan['edition']+=' · '+ubuntu_build.INSTALL_SOURCES[install_source]
        recipe=specification(plan,configs[0],dict(d,tasks=requested_tasks))
        if reused and any(reused[k]!=recipe[k] for k in ('media_identity','template_identity')):
            raise ValueError('The saved installation source changed. Review the new source in the Create wizard.')
        plan['recipe']=recipe
        ident=uuid.uuid4().hex
        with self.m.db() as con: con.execute('INSERT INTO plans VALUES (?,?,?)',(ident,time.time(),self.m.encode(plan)))
        diagnostics.event('plan.ready',plan_id=ident,platform=os,source_kind=configs[0].get('source_kind'),machine_count=count)
        return dict(id=ident,machines=[{k:c[k] for k in ('vmid','name','node','memory','cores','disk','storage','bridge')} for c in configs],
            edition=plan['edition'],purpose=purpose,tasks=[{'name':t['item']['name'],'params':public_params(t),'dangerous':bool(t['item'].get('dangerous'))} for t in selections],dangerous=plan['dangerous'],
            rdp=bool(configs[0].get('windows',{}).get('rdp')),clone_mode=template['mode'] if cloning or linux_clone else None,data_disks=[{k:v for k,v in disk.items() if k in ('size','label','letter')} for disk in configs[0].get('data_disks',[])],
            remote_access='HTTPS WinRM' if os=='windows' else ('SSH with workstation keys' if personal_keys else 'SSH'),
            **({'ssh_key_fingerprints':[linux_access.fingerprint(k) for k in personal_keys],
                'github_username':github.strip() or None,'password_login':bool(password_task)} if os!='windows' else {}),
            management_cidr=dest['management_cidr'] if os=='windows' else None,
            guest_tools=dest['agent_iso'] if os=='windows' else 'Installed automatically',
            notes=['Windows specializes each new clone and verifies its new name and HTTPS login before software runs.'] if cloning else
                ['Cloud-init configures a new hostname and SSH key before Proxmate verifies the clone and applies software.'] if linux_clone else ['The new machines start automatically. Each gets its own progress report.',
                'Guest tools come from your selected VirtIO ISO. The MSI checksum is frozen and verified before installation; Windows enforces driver signatures.' if os=='windows' else
                'The guest needs DNS and HTTPS access to the Rocky mirror list and its regional mirrors during installation for BaseOS and AppStream packages.' if configs[0].get('media_kind') in ('boot','minimal') else 'Guest tools come from the operating system package repositories.',
                ('Windows setup uses Microsoft\'s public KMS client key for the selected edition. This is not a licence or retail activation key; use your organisation\'s KMS or your own appropriate activation method afterwards.' if windows_setup_key(edition) else 'No generic setup key is supplied for this Windows image. Evaluation media uses its own evaluation terms; other media may ask for a product key in the console.') if os=='windows' else distro+' does not need a product key.',
                'Proxmate attempts the Windows DVD boot key automatically. If setup does not start, open the Proxmox console; the job waits for installation.' if os=='windows' else
                'The selected '+distro+' ISO installs unattended on the new system disk. Proxmate verifies SSH before applying software and login settings.' if linux_iso else
                'Ubuntu uses the official cloud image, with SSH and guest tools configured automatically.',
                'Software may restart the new guest when required. Failed machines are retained for inspection.',
                'Templates are generalized and shut down after software installation.' if purpose=='template' else 'Saved guest connections make later scripts a choose-and-run operation.'])

    def enqueue(self,d):
        plan=self.m.load_plan(d.get('plan_id'))
        if plan.get('operation')!='simple-build': raise ValueError('Choose a build plan')
        if d.get('confirmed') is not True: raise ValueError('Confirm the listed new machines, software and Windows licensing before creating')
        if plan['dangerous'] and d.get('risk_confirmed') is not True: raise ValueError('Confirm the selected sensitive settings or custom scripts')
        credential=plan['credential']
        jobs=[]
        with self.m.db() as con:
            con.execute('BEGIN IMMEDIATE')
            reserved_ids,reserved_names=reserved_targets(con,self.m.decode)
            for i,cfg in enumerate(plan['configs']):
                ident=hashlib.sha256((d['plan_id']+':'+str(i)).encode()).hexdigest()[:32]
                jobs.append({'id':ident,'name':cfg['name'],'url':'/api/jobs/'+ident})
                if con.execute('SELECT 1 FROM jobs WHERE id=?', (ident,)).fetchone():
                    continue
                if cfg['vmid'] in reserved_ids or cfg['name'].lower() in reserved_names:
                    raise ValueError('Another job reserved this machine name or VM ID. Review the build again.')
                require_unbound_target(con,cfg)
                # A still-open review may have been generated before the ISO letter reservation.
                if cfg['kind'] == 'windows' and cfg.get('source_kind') != 'template':
                    windows_build.check_iso_disk_letters(cfg.get('data_disks', []))
                execution={k:plan[k] for k in ('tasks','purpose','source','media_identity')};execution.update(operation='build '+cfg['name'],config=cfg,edition=plan.get('edition'))
                if plan.get('recipe'): execution['recipe']=plan['recipe']
                payload=dict(action='simple',server_id=operations.connection_provider.current()['id'],plan=execution,name=cfg['name'],node=cfg['node'],vmid=cfg['vmid'])
                con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(ident,'simple','queued',time.time(),time.time(),self.m.encode(payload),'Queued: create, prepare remote access, install software.'))
            if not con.execute('SELECT 1 FROM records WHERE kind=? AND id=?', ('credentials', credential['id'])).fetchone():
                saved = self.m.prepare('credentials', credential)
                con.execute('INSERT INTO records VALUES (?,?,?)', ('credentials', saved['id'], self.m.encode(saved)))
        for j in jobs: diagnostics.event('job.queued',job_id=j['id'],plan_id=d['plan_id'],action='simple')
        return dict(jobs=jobs)

    def execute(self,plan,log,job_id):
        cfg=copy.deepcopy(plan['config'])
        with self.m.db() as con:
            require_unbound_target(con,cfg)
        if plan.get('media_identity') and cfg['kind']=='windows':
            with diagnostics.stage('verify_windows_media'):
                if media.inspect_source(plan['source'],cfg['node'])['identity']!=plan['media_identity']: raise ValueError('Windows media changed since review')
            if cfg['source_kind']=='local':
                p=operations.Proxmox();volume=cfg['iso_storage']+':iso/'+operations.upload_iso_name(cfg['filename'])
                existing=p.call('GET',f'nodes/{cfg["node"]}/storage/{cfg["iso_storage"]}/content',content='iso')
                if any(i['volid']==volume for i in existing):
                    if media.inspect_source(volume,cfg['node'])['identity']!=plan['media_identity']: raise ValueError('A different ISO already uses this filename; nothing overwritten')
                    cfg.update(source_kind='proxmox',iso=volume)
        log('Creating '+cfg['name']+'; preparing '+('WinRM' if cfg['kind']=='windows' else 'SSH')+' automatically.')
        with diagnostics.stage('create_vm',vmid=cfg['vmid']):
            if cfg['kind'] in ('rocky-iso','rocky-clone'): rocky_build.check_cpu(operations.Proxmox(),cfg['node'])
            if cfg['kind'] in linux_install.ISO_KINDS:
                installer=LINUX_INSTALLERS[cfg['kind'].split('-')[0]]
                result=linux_install.install(cfg,plan['source'],plan['media_identity'],job_id,log,installer_module=installer)
            elif cfg['kind'] in linux_install.CLONE_KINDS:
                result=linux_install.clone(cfg,log)
            else:
                result=windows_build.specialize(cfg,log) if cfg.get('source_kind')=='template' else operations.execute(cfg,log)
        self.life.after_provision(cfg,result,job_id,log)
        self.finish_build(plan,log,job_id)

    def finish_build(self,plan,log,job_id):
        cfg=plan['config']
        tasks=list(plan['tasks'])
        if cfg['kind'] in linux_install.ISO_KINDS:
            linux_install.detach(cfg,self.m.get('builds',job_id),log)
        if cfg['kind']=='windows' and (cfg.get('data_disks') or cfg.get('source_kind')=='template'):
            build=self.m.get('builds',job_id)
            self.assert_owned(cfg,job_id)
            p=operations.Proxmox()
            if cfg.get('source_kind')=='template': windows_build.detach_clone_media(p,cfg,build,log)
            current=p.call('GET',vm_path(cfg)+'/config')
            if machine_identity(current)!=build['machine_identity'] or windows_build.disk_volumes(cfg,current)!=build.get('data_volumes',{}):
                raise ValueError('Allocated disks changed before guest setup')
            windows_build.check_optical_letters(p,cfg,build['machine_identity'])
            # Claim explicit letters first so an automatic allocation cannot take a later disk's letter.
            volumes = sorted(cfg.get('data_disks', []), key=lambda disk: not disk['letter'])
            tasks=[{'item':{'id':'prepare-created-volume','platform':'windows','name':'Prepare '+d['label'],'disk':d},'params':{}} for d in volumes]+tasks
        if tasks:
            taskplan=self.life.verified_task_plan(cfg,tasks,job_id)
            self.assert_owned(cfg,job_id)
            if plan['purpose']=='template': taskplan['template_build']=job_id
            with diagnostics.stage('guest_tasks',count=len(tasks)):
                self.m.execute(taskplan,log,job_id)
            self.life.record_tasks(taskplan,job_id)
        if plan['purpose']=='template':
            self.finish_template(cfg,job_id,log)
        log('Ready: '+cfg['name']+(' template.' if plan['purpose']=='template' else '. Run more scripts from Operations whenever you need them.'))

    def finish_template(self,cfg,job_id,log):
        from template_finish import finish
        with diagnostics.stage('generalize_template'):
            finish(cfg,self.m,log,self.m.get('builds',job_id)['machine_identity'])

    def finish_recovered_template(self,build_id,recovery_job,log):
        with self.m.db() as con:
            row=con.execute("SELECT payload FROM jobs WHERE id=? AND action='simple'",(build_id,)).fetchone()
        source=self.m.decode(row['payload'])
        if source['server_id']!=operations.connection_provider.current()['id'] or source['plan']['purpose']!='template':
            raise ValueError('The original template build is unavailable on this server.')
        self.finish_template(source['plan']['config'],build_id,log)
        build=self.m.get('builds',build_id)
        self.m.put('builds',dict(build,template_finished_by=recovery_job))

    def assert_owned(self,cfg,job_id):
        built=self.m.get('builds',job_id)
        current=operations.Proxmox().call('GET',f'nodes/{cfg["node"]}/qemu/{cfg["vmid"]}/config')
        if built['machine_identity']!=machine_identity(current): raise ValueError('The newly created VM was replaced; software was not applied')


def register(app,manager,lifecycle):
    flow=SimpleFlow(manager,lifecycle)
    flow.recipes.register(app)
    reviews=Reviews()
    @app.route('/api/simple/software', methods=['GET','POST'])
    def software():
        result = dict(features=[{'id':key,'name':name} for key,name in FEATURES])
        if request.method=='POST':
            data=request.get_json()
            targets=management.targets(data.get('targets') if isinstance(data,dict) else None)
            platforms={}
            recorded=linux_install.template_identities(linux_install.template_builds(manager))
            for distro in LINUX_INSTALLERS:
                result[distro+'_templates']=[]
            client=operations.Proxmox()
            try:
                for guest in targets:
                    path=f'nodes/{guest["node"]}/qemu/{guest["vmid"]}/config'
                    try:
                        config=client.call('GET',path)
                        ostype=config.get('ostype') or ''
                        if config.get('template'):
                            distro=recorded.get((guest['node'],guest['vmid'],machine_identity(config)))
                            if distro: result[distro+'_templates'].append(management.target_id(guest))
                    except (RuntimeError, RequestException):
                        ostype=''
                    platforms[management.target_id(guest)]=('windows' if ostype.startswith(('win','w2k','wvista','wxp'))
                        else 'linux' if ostype in ('l24','l26') else None)
            finally:
                client.session.close()
            result['platforms']=platforms
        return jsonify(result)
    @app.get('/api/simple/options')
    def options(): return jsonify(flow.options())
    @app.get('/api/simple/resources')
    def resources():
        rows=operations.Proxmox().resources()
        saved={r['id']:r for r in manager.all('connections') if r.get('verified_at') and r.get('platform')=='windows'}
        for row in rows:
            if row.get('type')=='qemu' and not row.get('template'):
                binding=saved.get(management.target_id(row))
                if binding: row['last_winrm_check']=binding['verified_at']
        return jsonify(rows)
    @app.post('/api/simple/editions')
    def editions():
        d=request.get_json();node=management.word(d.get('node'))
        with diagnostics.stage('editions'):
            return jsonify(flow.reviewed_media(d.get('source'),node))
    @app.post('/api/simple/plan',endpoint='simple_plan')
    def plan():
        if request.headers.get('Prefer')=='respond-async':
            return jsonify(reviews.submit(operations.connection_provider,flow.plan,request.get_json())),202
        return jsonify(flow.plan(request.get_json()))

    @app.get('/api/simple/reviews/<ident>')
    def review_status(ident):
        result=reviews.get(operations.connection_provider.current()['id'],ident)
        if result is None: return jsonify(error='This review expired. Select Continue to review again.'),404
        return jsonify(result)
    @app.post('/api/simple/run',endpoint='simple_run')
    def run(): return jsonify(flow.enqueue(request.get_json())),202
    @app.post('/api/simple/existing-plan')
    def existing_plan(): return jsonify(flow.existing_plan(request.get_json()))
    return flow
