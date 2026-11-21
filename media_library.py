"""List mounted and Proxmox ISO files; inspect one explicitly selected installer."""
import threading
import time
from collections import OrderedDict

from flask import jsonify, request
from requests import RequestException

import debian_build
import management
import media
import operations
import rocky_build
import ubuntu_build


INSTALLERS = {'ubuntu': ubuntu_build, 'debian': debian_build, 'rocky': rocky_build}


def family(source, name):
    for os_name, installer in INSTALLERS.items():
        if installer.candidates([{'id':source}]):
            return os_name
    if 'virtio' in name.lower():
        return 'virtio'
    if media.windows_iso_label(name):
        return 'windows'
    return 'other'


def inventory(node=None):
    client=operations.Proxmox()
    files=[]
    warnings=[]
    try:
        nodes=[{'node':n['node'],'status':n['status']} for n in client.call('GET','nodes')]
        online=[n['node'] for n in nodes if n['status']=='online']
        if node is not None and node not in online: raise ValueError('Choose an online Proxmox node.')
        node=node if node is not None else next(iter(online),None)
        if node:
            try:
                stores=client.call('GET',f'nodes/{node}/storage')
            except (RequestException,RuntimeError):
                stores=[]
                warnings.append(f'Could not list storage on {node}. Refresh to retry.')
            for store in stores:
                if not store.get('active') or 'iso' not in store.get('content','').split(','): continue
                storage=store['storage']
                try:
                    volumes=client.call('GET',f'nodes/{node}/storage/{storage}/content',content='iso')
                except (RequestException,RuntimeError):
                    warnings.append(f'Could not list {storage}. Refresh to retry.')
                    continue
                for volume in volumes:
                    source=volume['volid']
                    if not source.lower().endswith('.iso'): continue
                    name=source.rsplit('/',1)[-1]
                    files.append(dict(id=source,name=name,location='proxmox',storage=storage,bytes=volume.get('size'),
                                      revision=media.volume_revision(volume),family=family(source,name)))
    finally:
        client.session.close()
    for item in operations.local_iso_inventory():
        name=item['name']
        source='library:'+name
        files.append(dict(id=source,name=name,location='library',storage=None,bytes=item['bytes'],
                          revision=f"{item['mtime']}-{item['bytes']}",family=family(source,name)))
    recommended={}
    for os_name, installer in INSTALLERS.items():
        choices=installer.candidates(files)
        if choices:
            recommended[os_name]=choices[0]['id']
    windows=[f for f in files if f['family']=='windows']
    if windows:
        def preference(f):
            label=media.windows_iso_label(f['name'])
            return label.startswith('Windows Server'), label, f['location']=='proxmox'
        recommended['windows']=max(windows,key=preference)['id']
    return dict(node=node,nodes=nodes,files=files,warnings=warnings,recommended=recommended)


def inspect_windows(result, node, source, tools, ssh_ready):
    inspected=media.inspect_source(source,node)
    editions=[]
    for e in inspected['editions']:
        row={k:e[k] for k in ('index','label','build','language')}
        row.update(compatible=False,reason=media.windows_edition_issue(e))
        editions.append(row)
    result['editions']=editions
    supported=[e for e in editions if not e['reason']]
    if not supported:
        raise ValueError('No editions support guided Windows setup on this dashboard.')
    if not tools or not ssh_ready:
        result.update(status='missing',detail='Add a VirtIO guest-tools ISO to this node.' if not tools
                      else 'Configure the verified SSH connection in Server settings to check guest tools.')
        return
    result['agent_iso']=tools
    media.verify_ssh_node(node)
    digests=media.review_guest_agent_builds(tools,node,[e['build'] for e in supported])
    for e in supported:
        e['compatible']=e['build'] in digests
        if not e['compatible']: e['reason']='This tools ISO lacks the required x64 agent or serial driver.'
    if not any(e['compatible'] for e in editions):
        raise ValueError('The selected VirtIO ISO does not support these Windows builds. Choose another tools ISO and recheck.')
    result.update(status='verified',detail='Windows edition and matching VirtIO agent and serial driver verified.')


def verify(data, listing, ssh_ready):
    node,source,os_name=listing['node'],data['source'],data['os']
    files={f['id']:f for f in listing['files']}
    if source not in files: raise ValueError('This ISO is no longer listed on the selected node. Refresh the library.')
    if os_name!='windows' and files[source]['family']!=os_name:
        raise ValueError('Choose an installer filename supported by this Linux build.')
    tools=data.get('agent_iso')
    if tools is not None and (os_name!='windows' or tools not in listing['driver_isos']):
        raise ValueError('Choose a VirtIO ISO available on the selected Proxmox node.')
    result=dict(node=node,source=source,os=os_name,status='failed',checked_at=time.time())
    if not ssh_ready and files[source]['location']=='proxmox':
        return dict(result,status='missing',detail='Add the verified SSH fingerprint in Server settings to inspect node storage.')
    try:
        if os_name=='windows':
            inspect_windows(result,node,source,tools or next(iter(listing['driver_isos']),None),ssh_ready)
            if not listing['driver_isos'] and listing['warnings'] and result['status']=='missing':
                result['detail']='No VirtIO ISO is visible in the available listing. Refresh the unavailable storage and recheck.'
        else:
            inspected=INSTALLERS[os_name].inspect(source,node)
            result['release']=inspected['release']
            if os_name=='ubuntu': result['install_sources']=list(inspected['install_sources'])
            if os_name=='rocky': result['kind']=inspected['kind']
            if not ssh_ready:
                return dict(result,status='missing',detail='Installer contents verified. Configure node SSH for guided setup.')
            media.verify_ssh_node(node)
            if os_name=='rocky':
                client=operations.Proxmox()
                try: rocky_build.check_cpu(client,node)
                finally: client.session.close()
            result.update(status='verified',detail='Installer release and boot files verified.')
    except ValueError as exc:
        result['detail']=str(exc)
    except Exception:
        result['detail']='Could not finish the media check. Verify the ISO and the node connection, then retry.'
    return result


class MediaLibrary:
    def __init__(self):
        self.checks=OrderedDict()
        self.lock=threading.Lock()
        self.slots=threading.BoundedSemaphore(2)

    def listing(self,node):
        listing=inventory(node)
        host=operations.connection_provider.current()
        listing['ssh_configured']=bool(host.get('ssh_fingerprint'))
        by_id={f['id']:f for f in listing['files']}
        listing['driver_isos']=media.driver_sources(f['id'] for f in listing['files'] if f['location']=='proxmox')
        with self.lock:
            for source,item in by_id.items():
                saved=self.checks.get((host['id'],host.get('ssh_fingerprint'),listing['node'],source))
                if not saved: continue
                result,revisions=saved
                if (result['checked_at']>time.time()-600 and
                        all(revision and by_id.get(i,{}).get('revision')==revision for i,revision in revisions.items())):
                    item['check']=result
        return listing

    def check(self,data):
        if (not isinstance(data,dict) or set(data)-{'node','source','os','agent_iso'} or
                not all(isinstance(data.get(k),str) for k in ('node','source','os')) or
                data['os'] not in ('windows',*INSTALLERS) or
                ('agent_iso' in data and not isinstance(data['agent_iso'],str))):
            raise ValueError('Supply one node, ISO source and supported operating system.')
        node=management.word(data['node'],'node')
        listing=self.listing(node)
        result=verify(data,listing,listing['ssh_configured'])
        host=operations.connection_provider.current()
        key=(host['id'],host.get('ssh_fingerprint'),node,data['source'])
        refs={data['source'],result.get('agent_iso')}
        revisions={f['id']:f['revision'] for f in listing['files'] if f['id'] in refs}
        with self.lock:
            self.checks.pop(key,None)
            if result['status']!='missing' and all(revisions.values()):
                self.checks[key]=(result,revisions)
                if len(self.checks)>256: self.checks.popitem(last=False)
        return result


def register(app):
    library=MediaLibrary()

    @app.get('/api/simple/media')
    def media_inventory():
        return jsonify(library.listing(request.args.get('node')))

    @app.post('/api/simple/media/verify')
    def media_verify():
        if not library.slots.acquire(blocking=False):
            return jsonify(error='Two media checks are already running. Retry when one finishes.'),429
        try:
            return jsonify(library.check(request.get_json()))
        finally:
            library.slots.release()
