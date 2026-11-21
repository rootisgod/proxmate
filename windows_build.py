"""Windows template specialization and data disks belonging to a reviewed new build."""
import ipaddress
import json
import re
import tempfile
import uuid
from pathlib import Path

import diagnostics
import management
import operations
from vm_config import config_identity, machine_identity, safe_guest_config, vm_path
from provisioning import answer_iso


ISO_SETUP_LETTERS = ('D', 'E', 'F')


def check_iso_disk_letters(items):
    if not isinstance(items, list) or len(items) > 3:
        raise ValueError('Choose up to three data disks')
    for disk in items:
        if not isinstance(disk, dict):
            raise ValueError('Enter data disk settings')
        letter = str(disk.get('letter', '')).upper()
        if letter in ISO_SETUP_LETTERS:
            raise ValueError(f'{letter}: is reserved for Windows setup DVDs. Choose Automatic or a letter from G: to Z:.')


def disks_requested(items, slots=('sata3','sata4','sata5')):
    if not isinstance(items,list) or len(items)>3: raise ValueError('Choose up to three data disks')
    if len(items)>len(slots): raise ValueError('The template has too few free SATA slots for these disks')
    disks=[]
    for i,entry in enumerate(items):
        if not isinstance(entry,dict): raise ValueError('Enter data disk settings')
        size=management.number(entry.get('size'),1,4096)
        label=entry.get('label') or f'Data{i+1}'
        letter=str(entry.get('letter','')).upper()
        if not isinstance(label,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,31}',label):
            raise ValueError('Use a short plain volume label')
        if letter and (not re.fullmatch('[D-Z]',letter) or any(d['letter']==letter for d in disks)):
            raise ValueError('Choose a different available drive letter from D to Z for each disk')
        disks.append(dict(size=size,label=label,letter=letter,slot=slots[i],serial='PM'+uuid.uuid4().hex[:16]))
    return disks


def drive_options(cfg):
    disks=cfg.get('data_disks',[])
    if not isinstance(disks,list) or len(disks)>3: raise ValueError('Invalid data disk list')
    for i,disk in enumerate(disks):
        if (disk.get('slot') not in {f'sata{j}' for j in range(1,6)} or
            disk['slot'] in {earlier['slot'] for earlier in disks[:i]} or not re.fullmatch('PM[a-f0-9]{16}',disk.get('serial',''))):
            raise ValueError('Invalid allocated disk identity')
        management.number(disk['size'],1,4096)
    return {d['slot']:f"{cfg['storage']}:{d['size']},serial={d['serial']}" for d in cfg.get('data_disks',[])}


def disk_volumes(cfg,current):
    volumes={}
    for disk in cfg.get('data_disks',[]):
        parts=current.get(disk['slot'],'').split(',')
        if ('serial='+disk['serial'] not in parts or not parts[0].startswith(cfg['storage']+':') or
            parts[0] in volumes.values()):
            raise ValueError('The allocated data disk changed. Inspect its volume and serial before continuing.')
        volumes[disk['slot']]=parts[0]
    return volumes


def review_template(life, dest, d):
    target=dict(node=dest['node'],vmid=management.number(d.get('template_id'),100,999999999))
    resource,cfg=life.guest(operations.Proxmox(),target,True)
    if not cfg.get('ostype','').startswith(('win','w2k')) or resource.get('status')!='stopped':
        raise ValueError('Choose a stopped Windows template')
    if not (cfg.get('vmgenid') or cfg.get('smbios1')): raise ValueError('Template lacks a stable VM identity')
    if d.get('generalized') is not True: raise ValueError('Confirm this template was generalized with Sysprep and has the QEMU guest agent installed')
    safe_guest_config(cfg)
    if any('media=cdrom' in str(v) and not str(v).startswith('none,') for v in cfg.values()):
        raise ValueError('Detach setup media from the source template before cloning')
    network=re.search(r'(?:^|,)bridge=([^,]+)',cfg.get('net0',''))
    if not network or network[1] not in dest['bridges']: raise ValueError('Template needs an available primary network bridge')
    bridge=network[1]
    scope=d.get('management_cidr') or dest['management_cidr']
    if bridge!=dest['bridge'] and not d.get('scope_edited') and scope==dest['management_cidr']:
        scope=dest.get('bridge_subnets',{}).get(bridge)
    if not scope: raise ValueError('Enter the WinRM management subnet for the template network '+bridge)
    mode=d.get('clone_mode','full')
    if mode not in ('full','linked'): raise ValueError('Choose full or linked clone')
    try: subnet=ipaddress.ip_network(scope,strict=False)
    except ValueError: raise ValueError('Enter the management client address or subnet') from None
    if subnet.prefixlen==0: raise ValueError('Restrict WinRM to a management subnet')
    media_slot=next((key for key in ('ide2','ide3','sata1','sata2') if key not in cfg or cfg[key].startswith('none,media=cdrom')),None)
    if not media_slot: raise ValueError('Template needs a free setup media slot')
    slots=[f'sata{i}' for i in range(1,6) if f'sata{i}' not in cfg and f'sata{i}'!=media_slot]
    return dict(media_slot=media_slot,slots=slots,bridge=bridge,source=target['vmid'],identity=config_identity(cfg),name=resource.get('name') or str(target['vmid']),
                mode=mode,management_cidr=str(subnet),disk_gib=max(1,(resource.get('maxdisk',0)+2**30-1)//2**30))


def specialize(cfg,log):
    template=cfg['template']
    p=operations.Proxmox()
    node=cfg['node']
    source=f"nodes/{node}/qemu/{template['source']}"
    current=p.call('GET',source+'/config')
    if not current.get('template') or config_identity(current)!=template['identity']:
        raise ValueError('Source template changed since review. Review a new clone.')
    if any(r.get('vmid')==cfg['vmid'] for r in p.resources()): raise ValueError('Destination VM ID already exists')
    args=dict(newid=cfg['vmid'],name=cfg['name'],full=int(template['mode']=='full'))
    if args['full']: args['storage']=cfg['storage']
    log('Cloning the reviewed Windows template to a new, stopped VM.')
    p.wait(node,p.call('POST',source+'/clone',**args),log)
    base=vm_path(cfg)
    target=p.call('GET',base+'/config')
    identity=machine_identity(target)
    if (not (target.get('smbios1') or target.get('vmgenid')) or target.get('template') or
        target.get('name')!=cfg['name'] or identity==machine_identity(current)):
        raise ValueError('Cannot establish a distinct identity for the new clone')
    slots=drive_options(cfg)
    if any(slot in target for slot in slots): raise ValueError('A requested data disk slot is occupied in the clone')
    media_slot=template['media_slot']
    if media_slot in target and not target[media_slot].startswith('none,media=cdrom'): raise ValueError('Clone setup media slot is occupied')
    if p.call('GET',base+'/status/current').get('status')!='stopped': raise ValueError('Clone started before specialization was prepared')
    with tempfile.TemporaryDirectory(prefix='proxmate-clone-') as tmp:
        path=Path(tmp)/f"proxmate-clone-{cfg['vmid']}-{uuid.uuid4().hex}.iso"
        answer_iso(cfg['windows'],path,cfg['bootstrap'],clone=True)
        path.chmod(0o600)
        volume=operations.upload_generated(p,node,cfg['iso_storage'],path,log)
    p.wait(node,p.call('PUT',base+'/config',digest=target['digest'],agent=1,onboot=0,
                       cores=cfg['cores'],sockets=1,memory=cfg['memory'],**{media_slot:volume+',media=cdrom',**slots}),log)
    attached=p.call('GET',base+'/config')
    if machine_identity(attached)!=identity or attached.get(media_slot,'').split(',')[0]!=volume:
        raise ValueError('Cloned machine or setup media changed before first boot')
    volumes=disk_volumes(cfg,attached)
    p.wait(node,p.call('POST',base+'/status/start'),log)
    return dict(machine_identity=identity,data_volumes=volumes,setup_slot=media_slot,setup_volume=volume)


def detach_clone_media(p,cfg,build,log):
    source=build['source']
    base=vm_path(cfg)
    current=p.call('GET',base+'/config')
    volume=source['setup_volume']
    slot=source['setup_slot']
    expected_file=r'proxmate-clone-'+str(cfg['vmid'])+r'-[a-f0-9]{32}\.iso'
    if not re.fullmatch(re.escape(cfg['iso_storage'])+':iso/'+expected_file,volume): raise ValueError('Unexpected clone media identity')
    if machine_identity(current)!=build['machine_identity'] or current.get(slot,'').split(',')[0]!=volume:
        raise ValueError('Clone setup media changed. Inspect the VM before cleanup.')
    p.wait(cfg['node'],p.call('PUT',base+'/config',digest=current['digest'],**{slot:'none,media=cdrom'}),log)
    from urllib.parse import quote
    effective=p.call('GET',base+'/config',current=1)
    if machine_identity(effective)!=build['machine_identity'] or any(str(v).split(',')[0]==volume for v in effective.values()):
        raise ValueError('Setup media is still attached. Inspect the clone before cleanup.')
    p.wait(cfg['node'],p.call('DELETE',f"nodes/{cfg['node']}/storage/{cfg['iso_storage']}/content/{quote(volume,safe='')}"),log)


def check_optical_letters(client, cfg, identity):
    requested = {disk['letter']: disk for disk in cfg.get('data_disks', []) if disk['letter']}
    if not requested:
        return
    from winrm_setup import agent_script
    script = r"""
$ErrorActionPreference = 'Stop'
$volumes = @(Get-Volume | Where-Object DriveLetter)
$used = @(Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Name })
[pscustomobject]@{
    optical = @($volumes | Where-Object { $_.DriveType -eq 'CD-ROM' } | ForEach-Object { [string]$_.DriveLetter })
    used = @($used) + @($volumes | ForEach-Object { [string]$_.DriveLetter })
} | ConvertTo-Json -Compress
"""
    # The optional agent probe makes DVD conflicts actionable before the first write.
    # Guests with restricted guest-exec permissions still have the WinRM disk guards.
    from requests import RequestException
    try:
        info = json.loads(agent_script(client, cfg, identity, script))
        if not isinstance(info, dict) or not all(isinstance(info.get(k), list) for k in ('optical', 'used')):
            raise RuntimeError('Incomplete drive inventory')
    except (RuntimeError, RequestException, json.JSONDecodeError) as exc:
        diagnostics.event('disk.letter_check_unavailable', vmid=cfg['vmid'], error_type=type(exc).__name__)
        return
    conflicts = requested.keys() & set(info['optical'])
    if not conflicts:
        return
    letter = sorted(conflicts)[0]
    free = next((c for c in 'DEFGHIJKLMNOPQRSTUVWXYZ' if c not in info['used'] and c not in requested), None)
    message = f"{requested[letter]['label']} requested {letter}:, which Windows assigned to a virtual DVD drive."
    suggestion = 'Choose Automatic' + (f' or {free}:' if free else '') + ' for the data disk.'
    diagnostics.event('disk.letter_conflict', vmid=cfg['vmid'], slot=requested[letter]['slot'],
                      letter=letter, drive_type='cdrom', free_letter=free)
    raise ValueError(message + ' ' + suggestion)
