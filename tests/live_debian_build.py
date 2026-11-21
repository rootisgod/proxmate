"""Opt-in acceptance test. Creates four guided Linux guests and retains their jobs on the selected node."""
import argparse
import io
import os
from pathlib import Path
import re
import secrets
import shlex
import time

import paramiko
import requests
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from guest_tasks import known_host


class Dashboard:
    def __init__(self, distro='debian', ubuntu_series=None):
        assert distro in ('debian','rocky','ubuntu')
        self.distro=distro
        self.prefix={'rocky':'rk','debian':'db','ubuntu':'ub'}[distro]
        self.ubuntu_series=ubuntu_series
        if distro=='ubuntu':
            assert ubuntu_series in ('22.04','24.04','26.04')
        self.url = 'http://127.0.0.1:'+os.environ.get('PORT','8080')+'/api/'
        self.http = requests.Session()
        self.http.trust_env = False
        token = (Path(os.environ.get('DATA_DIR','/data'))/'api-token').read_text().strip()
        self.http.headers['Authorization'] = 'Bearer '+token
        server = self.api('server')
        assert server['connected']
        self.http.headers['X-Proxmate-Server'] = server['id']

    def api(self, path, body=None):
        response = self.http.request('GET' if body is None else 'POST', self.url+path, json=body,
                                     allow_redirects=False, timeout=(5,240))
        response.raise_for_status()
        return response.json()

    def wait(self, job):
        previous = None
        deadline = time.monotonic()+7200
        while time.monotonic()<deadline:
            result = self.api('jobs/'+job)
            state = result['status']
            last = result['log'].splitlines()[-1:]
            if (state,last)!=previous:
                print(job,state,*last,flush=True)
                previous = state,last
            if state=='succeeded':
                return result
            if state not in ('running','queued','waiting'):
                raise RuntimeError('Check dashboard job '+job)
            time.sleep(10)
        raise TimeoutError('Check dashboard job '+job)

    def submit(self, node, name, **choices):
        assert re.fullmatch(self.prefix+r'[vtab]-[0-9a-f]{6}',name)
        draft = dict(os=self.distro,node=node,name=name,**choices)
        plan = self.api('simple/plan',draft)
        machine, = plan['machines']
        assert machine['node']==node and machine['name']==name and not plan['dangerous']
        assert plan['purpose']==draft.get('purpose','vm') and plan['clone_mode']==draft.get('clone_mode')
        body = dict(plan_id=plan['id'],confirmed=True)
        try:
            result = self.api('simple/run',body)
        except (requests.Timeout,requests.ConnectionError):
            result = self.api('simple/run',body)
        job, = result['jobs']
        print('Submitted',name,machine['vmid'],job['id'],flush=True)
        return job['id']


def new_key():
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption())
    return paramiko.Ed25519Key.from_private_key(io.StringIO(pem.decode())), private.public_key().public_bytes(
        serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH).decode()


def check_guest(api, job, key, *, folder='', present=False, password=None, excluded_key='', install_source=None):
    build = api.api('jobs/'+job)
    target = {k:build['target'][k] for k in ('node','vmid')}
    machine = api.api('simple/machine',target)
    assert machine['verified_at'] and machine['name']==build['target']['name'] and machine['status']=='running'
    binding, = [r for r in api.api('manage/connections') if r['id']==f"{target['node']}:{target['vmid']}"]
    assert machine['identity']==binding['machine_identity'] and binding['host'] in {r['host'] for r in machine['addresses']}
    entry = paramiko.hostkeys.HostKeyEntry.from_line(known_host(binding).strip())
    def inspect(*, login_key=None, login_password=None):
        with paramiko.SSHClient() as ssh:
            ssh.get_host_keys().add(entry.hostnames[0],entry.key.get_name(),entry.key)
            ssh.connect(binding['host'],port=binding['port'],username=api.distro,pkey=login_key,password=login_password,
                        allow_agent=False,look_for_keys=False,timeout=10)
            if api.distro=='ubuntu':
                os_check='test "$ID:$VERSION_ID" = ubuntu:'+api.ubuntu_series
            else:
                os_check='test "$ID:${VERSION_ID%%.*}" = '+('rocky:10' if api.distro=='rocky' else 'debian:13')
            cmd = 'set -eu; . /etc/os-release; '+os_check+'; jq --version >/dev/null; sudo -n true; systemctl is-active --quiet qemu-guest-agent'
            if install_source:
                # Subiquity records the installation source it actually used inside the guest.
                read_source = ("import sys,yaml; from pathlib import Path; "
                               "answer=yaml.safe_load(Path('/var/log/installer/autoinstall-user-data').read_text()); "
                               "sys.exit(answer['autoinstall']['source']['id'] != sys.argv[1])")
                cmd += '; sudo -n python3 -c '+shlex.quote(read_source)+' '+shlex.quote(install_source)
            if api.distro=='rocky':
                cmd += '; test "$(getenforce)" = Enforcing; systemctl is-active --quiet firewalld; sudo -n firewall-cmd --query-service=ssh >/dev/null'
                if excluded_key:
                    cmd += '; sudo -n test ! -e /root/anaconda-ks.cfg; sudo -n test ! -e /root/original-ks.cfg'
            if folder:
                assert re.fullmatch(r'pm-[a-f0-9]{6}',folder)
                cmd += '; test '+('' if present else '! ')+'-d /var/tmp/'+folder
            cmd += '; hostname; cat /etc/machine-id; cat ~/.ssh/authorized_keys'
            _,out,_ = ssh.exec_command(cmd,timeout=30)
            lines = out.read().decode().splitlines()
            assert out.channel.recv_exit_status()==0 and lines[0]==machine['name']
            assert re.fullmatch(r'[0-9a-f]{32}',lines[1])
            keys={' '.join(line.split()[:2]) for line in lines[2:] if line.strip()}
            assert len(keys)==2 and key.get_name()+' '+key.get_base64() in keys
            assert not excluded_key or excluded_key not in keys
            return lines[1]
    machine_id = inspect(login_key=key)
    if password:
        assert inspect(login_password=password)==machine_id
    return machine_id,binding['fingerprint']


def folder_operation(api, job, folder):
    guest = api.api('jobs/'+job)['target']
    target = {k:guest[k] for k in ('node','vmid')}
    script = dict(platform='linux',kind='ansible',parameters={'name':'Directory name'},params={'name':folder},
                  body="- ansible.builtin.file:\n    path: '/var/tmp/{{ proxmate_inputs.name }}'\n    state: directory\n    mode: '0755'\n")
    plan = api.api('simple/existing-plan',dict(targets=[target],one_off=script))
    reviewed, = plan['targets']
    assert reviewed['vmid']==target['vmid'] and reviewed['node']==target['node'] and reviewed['name']==guest['name']
    job = api.api('manage/run',dict(plan_id=plan['id'],confirmed=True,risk_confirmed=True))
    api.wait(job['id'])


def check_answer(api, job, *secrets_to_exclude, install_source=None):
    artifact={'rocky':'kickstart','debian':'preseed','ubuntu':'autoinstall'}[api.distro]
    response=api.http.get(api.url+'jobs/'+job+'/'+artifact,timeout=10)
    response.raise_for_status()
    assert 'REPLACE_WITH_SYSTEM_DISK_SERIAL' in response.text
    assert all(secret not in response.text for secret in secrets_to_exclude)
    if install_source:
        assert yaml.safe_load(response.text)['autoinstall']['source']['id']==install_source


def exercise(api, node, source, *, templates_only=False):
    suffix = secrets.token_hex(3)
    package = [dict(id='linux-package',params={'package':'jq'})]
    if not templates_only:
        personal, public = new_key()
        password = secrets.token_urlsafe(30)
        vm = api.submit(node,api.prefix+'v-'+suffix,source_kind='iso',source=source,ssh_public_key=public,linux_password=password,tasks=package)
        api.wait(vm)
        install_source='ubuntu-server' if api.distro=='ubuntu' else None
        check_guest(api,vm,personal,password=password,install_source=install_source)
        check_answer(api,vm,public,password,install_source=install_source)
        print('PASS',api.distro,'working VM, package, independent key and password logins, and reusable installer answer.',flush=True)
    _,source_key = new_key()
    ubuntu_choices={'ubuntu_install_source':'ubuntu-server-minimal'} if api.distro=='ubuntu' else {}
    base = api.submit(node,api.prefix+'t-'+suffix,source_kind='iso',source=source,purpose='template',ssh_public_key=source_key,tasks=package,**ubuntu_choices)
    template = api.wait(base)['target']
    check_answer(api,base,source_key,install_source=ubuntu_choices.get('ubuntu_install_source'))
    def sealed():
        row, = [r for r in api.api('simple/resources') if r.get('node')==node and r.get('vmid')==template['vmid']]
        return row.get('template') and row['name']==template['name'] and row['status']=='stopped'
    for _ in range(30):
        if sealed(): break
        time.sleep(2)
    assert sealed()
    clones = []
    for prefix in (api.prefix+'a-',api.prefix+'b-'):
        key,pub = new_key()
        job = api.submit(node,prefix+suffix,template_id=template['vmid'],source_kind='template',clone_mode='full',ssh_public_key=pub)
        clones.append((job,key))
    for job,_ in clones: api.wait(job)
    folder = 'pm-'+suffix
    folder_operation(api,clones[0][0],folder)
    first,second = [check_guest(api,job,key,folder=folder,present=(i==0),excluded_key=source_key,
                                install_source=ubuntu_choices.get('ubuntu_install_source'))
                    for i,(job,key) in enumerate(clones)]
    assert first[0]!=second[0], 'Clones share an OS machine ID'
    assert first[1]!=second[1], 'Clones share an SSH host key'
    assert sealed(), 'Source template changed during cloning'
    print('PASS',api.distro,'ISO template, two independent clone identities, and post-build Ansible.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os',choices=('debian','rocky','ubuntu'),default='debian')
    parser.add_argument('--ubuntu-series',choices=('22.04','24.04','26.04'),help='Expected installed Ubuntu LTS series')
    parser.add_argument('--node',required=True)
    parser.add_argument('--source',required=True,help='Exact installer ID from simple/options')
    parser.add_argument('--templates-only',action='store_true',help='Exercise a fresh template and two full clones only')
    args=parser.parse_args()
    if args.os=='ubuntu' and not args.ubuntu_series:parser.error('--os ubuntu requires --ubuntu-series')
    exercise(Dashboard(args.os,args.ubuntu_series),args.node,args.source,templates_only=args.templates_only)
