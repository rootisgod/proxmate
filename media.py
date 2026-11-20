"""Read Windows edition metadata without mounting media or extracting its disk image."""
import contextlib
import hashlib
import os
from pathlib import Path
import re
import shlex
import struct
import xml.etree.ElementTree as ET

import pycdlib
import paramiko
import operations


def wim_editions(stream):
    header=stream.read(208)
    if len(header)!=208 or header[:8]!=b'MSWIM\0\0\0': raise ValueError('This ISO has an unsupported Windows image format')
    size_flags,offset,original=struct.unpack_from('<QQQ',header,72)
    size=size_flags & ((1<<56)-1)
    if size_flags>>56 & 4 or not 0<size<=2_000_000 or original!=size:
        raise ValueError('Windows edition metadata is compressed or too large to inspect safely')
    stream.seek(0,2);length=stream.tell()
    if offset<208 or offset+size>length: raise ValueError('Invalid Windows edition metadata location')
    stream.seek(offset);xml=stream.read(size)
    text=xml.decode('utf-16')
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper(): raise ValueError('Unsupported Windows metadata declarations')
    root=ET.fromstring(text);editions=[]
    for item in root.findall('IMAGE'):
        index=int(item.attrib['INDEX']);name=item.findtext('DISPLAYNAME') or item.findtext('NAME') or ''
        windows=item.find('WINDOWS')
        if windows is None: continue
        install=windows.findtext('INSTALLATIONTYPE','')
        architecture=windows.findtext('ARCH','')
        if not 1<=index<=100 or architecture!='9' or 'server' not in (install+' '+name).lower(): continue
        desktop='core' not in install.lower() and 'core' not in name.lower()
        label=name
        if 'desktop experience' not in label.lower(): label+=' — '+('Desktop Experience' if desktop else 'Server Core (no desktop)')
        editions.append(dict(index=index,name=name,label=label,desktop=desktop,
            language=windows.findtext('LANGUAGES/DEFAULT','en-US'),build=windows.findtext('VERSION/BUILD',''),
            edition_id=windows.findtext('EDITIONID','')))
    if not editions: raise ValueError('No supported 64-bit Windows Server editions were found in this ISO')
    return dict(editions=editions,identity=hashlib.sha256(header+xml).hexdigest())


def inspect_iso(stream):
    iso=pycdlib.PyCdlib()
    try:
        iso.open_fp(stream)
        for filesystem in ('udf_path','joliet_path','iso_path'):
            for filename in ('install.wim','install.esd'):
                path='/sources/'+filename if filesystem!='iso_path' else '/SOURCES/'+filename.upper()+';1'
                try: image=iso.open_file_from_iso(**{filesystem:path})
                except pycdlib.pycdlibexception.PyCdlibException: continue
                with image: return wim_editions(image)
        raise ValueError('This ISO does not contain sources/install.wim or install.esd')
    finally: iso.close()


@contextlib.contextmanager
def source_file(source,node=None):
    if not isinstance(source,str): raise ValueError('Choose an ISO')
    if source.startswith('library:'):
        filename=source[8:]
        if not re.fullmatch(r'[A-Za-z0-9_. -]+\.iso',filename,re.I): raise ValueError('Invalid local ISO name')
        root=Path(os.getenv('ISO_DIR','D:/ISOs')).resolve();path=(root/filename).resolve()
        if path.parent!=root or not path.is_file(): raise ValueError('ISO is no longer in the library')
        with path.open('rb') as stream: yield stream
    else:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+:iso/[A-Za-z0-9_. -]+\.iso',source,re.I): raise ValueError('Choose a Proxmox ISO')
        p=operations.Proxmox()
        volumes=p.call('GET',f'nodes/{node}/storage/{source.split(":")[0]}/content',content='iso')
        if not any(v['volid']==source for v in volumes): raise ValueError('ISO is no longer on the selected server')
        with p.ssh() as ssh:
            command='test "$(hostname -s)" = '+shlex.quote(node)+' && pvesm path '+shlex.quote(source)
            _,stdout,stderr=ssh.exec_command(command,timeout=20)
            path=stdout.read(4096).decode().strip()
            if stdout.channel.recv_exit_status()!=0 or not path.startswith('/') or '\n' in path:
                raise ValueError('Cannot read this ISO on the connected SSH node')
            with ssh.open_sftp() as sftp:
                with sftp.open(path,'rb') as stream:
                    stream.settimeout(30)
                    yield stream


def inspect_source(source,node):
    try:
        with source_file(source,node) as stream: return inspect_iso(stream)
    except (ValueError,FileNotFoundError): raise
    except paramiko.SSHException as exc:
        raise ValueError(ssh_error(exc)) from exc
    except Exception as exc:
        raise ValueError('Could not read the ISO. Check the Server SSH connection and choose an intact Windows Server ISO.') from exc


def ssh_error(exc):
    if 'fingerprint mismatch' in str(exc):
        return 'The server SSH key does not match the saved fingerprint. Open Server and enter the host fingerprint verified from its Proxmox console. No host key was automatically trusted.'
    return 'The server SSH connection failed. Check the SSH username, password and verified fingerprint on the Server page.'


def ssh_node():
    try:
        with operations.Proxmox().ssh() as ssh:
            _,stdout,_=ssh.exec_command('hostname -s',timeout=15)
            hostname=stdout.read(1024).decode().strip()
            if not hostname or stdout.channel.recv_exit_status()!=0:
                raise ValueError('Could not identify the connected SSH node.')
            return hostname
    except paramiko.SSHException as exc: raise ValueError(ssh_error(exc)) from exc


def verify_ssh_node(node):
    if ssh_node()!=node:
        raise ValueError('The SSH connection reaches a different Proxmox node. Choose the connected node under Advanced.')


def supports_guest_agent(source,node,build):
    flavour='2k25' if int(build)>=26100 else '2k22' if int(build)>=20348 else '2k19'
    with source_file(source,node) as stream:
        iso=pycdlib.PyCdlib()
        try:
            iso.open_fp(stream)
            for path in ('/guest-agent/qemu-ga-x86_64.msi',f'/vioserial/{flavour}/amd64/vioser.inf'):
                found=False
                # VirtIO media may expose long filenames only through Rock Ridge;
                # its raw ISO9660 aliases need not match those filenames.
                for fs in ('udf_path','joliet_path','rr_path','iso_path'):
                    try:
                        iso.get_record(**{fs:path if fs!='iso_path' else path.upper()+';1'});found=True;break
                    except (pycdlib.pycdlibexception.PyCdlibException,IndexError):
                        # Some pycdlib versions raise IndexError for an absent RR path.
                        pass
                if not found:return False
            return True
        finally:iso.close()


def guest_agent_sha256(source,node):
    """Pin the exact MSI supplied by the user's selected VirtIO media."""
    with source_file(source,node) as stream:
        iso=pycdlib.PyCdlib()
        try:
            iso.open_fp(stream)
            for fs in ('udf_path','joliet_path','rr_path','iso_path'):
                path='/guest-agent/qemu-ga-x86_64.msi'
                try: msi=iso.open_file_from_iso(**{fs:path if fs!='iso_path' else path.upper()+';1'})
                except (pycdlib.pycdlibexception.PyCdlibException,IndexError): continue
                with msi:
                    digest=hashlib.sha256();size=0
                    while chunk:=msi.read(1024*1024):
                        size+=len(chunk)
                        if size>128*1024*1024: raise ValueError('Guest agent MSI exceeds the inspection limit')
                        digest.update(chunk)
                if not size: raise ValueError('Guest agent MSI is empty')
                return digest.hexdigest()
            raise ValueError('Selected VirtIO ISO does not contain the x64 guest agent MSI')
        finally:iso.close()
