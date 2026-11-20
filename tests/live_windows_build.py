"""Opt-in acceptance test. Run inside the dashboard container. See docs/windows-workflows.md."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
import sqlite3
import sys
import time

import requests
from cryptography.fernet import Fernet

from guest_tasks import run_guest


PROGRAMS = {
    '7zip': r'7-Zip\7z.exe',
    'git': r'Git\cmd\git.exe',
    'notepadplusplus': r'Notepad++\notepad++.exe',
}
SETTINGS = {
    'edge-first-run': [(r'HKLM:\SOFTWARE\Policies\Microsoft\Edge', 'HideFirstRunExperience', 1)],
    'server-manager': [(r'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Server\ServerManager', 'DoNotOpenAtLogon', 1)],
    'explorer': [(r'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced', 'Hidden', 1),
                 (r'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced', 'HideFileExt', 0)],
}
PACKAGE_CHECK = r"""
param($Package, $Binary)
$install = [Environment]::GetEnvironmentVariable('ChocolateyInstall','Machine')
if (!$install) { $install = Join-Path $env:ProgramData 'chocolatey' }
$choco = Join-Path $install 'bin\choco.exe'
$lines = & $choco list --exact $Package --limit-output
if ($LASTEXITCODE -ne 0 -or !($lines -match ('^' + [regex]::Escape($Package) + '\|'))) { throw 'Package missing' }
$roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
if (!($roots | Where-Object { Test-Path -LiteralPath (Join-Path $_ $Binary) -PathType Leaf })) { throw 'Executable missing' }
"""
WINDOWS_CHECK = r"""
param($Hostname, $Edition, $Build, [long]$DiskGiB)
$version = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
if ($env:COMPUTERNAME -ne $Hostname -or $version.EditionID -ne $Edition -or
    $version.CurrentBuildNumber -ne $Build -or $version.InstallationType -ne 'Server') { throw 'Windows identity or edition mismatch' }
$disk = Get-Partition -DriveLetter $env:SystemDrive.Substring(0,1) | Get-Disk
if ([math]::Abs($disk.Size - ($DiskGiB * 1GB)) -gt 1MB) { throw 'System disk size mismatch' }
"""


def require(ok, message):
    if not ok:
        raise ValueError(message)


class LiveTest:
    def __init__(self):
        data = Path(os.environ.get('DATA_DIR', '/data'))
        # Importing the web app would run its startup queue recovery. Use a read-only
        # connection for the two things deliberately absent from the public jobs API:
        # the original build inputs and the new guest's encrypted login.
        self.db = sqlite3.connect((data / 'jobs.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256((data / 'session-key').read_text().strip().encode()).digest()))
        self.http = requests.Session()
        self.http.trust_env = False
        self.http.headers['Authorization'] = 'Bearer ' + (data / 'api-token').read_text().strip()
        server = self.api('server')
        require(server.get('connected'), 'Connect the dashboard to the source Proxmox server first')
        self.server = server['id']
        self.http.headers['X-Proxmate-Server'] = self.server

    def api(self, path, body=None):
        response = self.http.request('GET' if body is None else 'POST',
                                     'http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/api/' + path,
                                     json=body, allow_redirects=False, timeout=(5, 240))
        if not response.ok:
            raise RuntimeError(f'{path}: HTTP {response.status_code}: ' + str(response.json().get('error', 'request failed')))
        return response.json()

    def read(self, query, keys):
        row = self.db.execute(query, keys).fetchone()
        require(row, 'Missing job or guest record')
        value = row[0]
        return json.loads(self.cipher.decrypt(value[10:].encode()) if value.startswith('encrypted:') else value)

    def record(self, kind, ident):
        return self.read('SELECT value FROM records WHERE kind=? AND id=?', (kind, ident))

    def payload(self, ident):
        job = self.read('SELECT payload FROM jobs WHERE id=?', (ident,))
        require(job.get('server_id') == self.server and job.get('action') == 'simple', 'Choose a build from the connected server')
        return job['plan']

    def start(self, source_job, seed):
        source = self.payload(source_job)
        cfg = source['config']
        require(cfg['kind'] == 'windows' and cfg.get('source_kind') in ('local', 'proxmox')
                and source['purpose'] == 'vm' and not cfg.get('data_disks') and cfg['windows']['accept_eula'],
                'This test repeats reviewed Windows ISO VM builds with one system disk')
        options = self.api('simple/options')
        images = self.api('simple/editions', {'node': cfg['node'], 'source': source['source']})
        edition = next((e for e in images['editions'] if e['index'] == cfg['windows']['image_index']), None)
        require(images['identity'] == source['media_identity'] and edition and edition.get('desktop'),
                'The original Desktop Experience image and edition are required')
        rng = random.Random(seed)
        def pick(catalog, checks):
            return rng.sample(sorted({v['id'] for v in catalog}.intersection(checks)), 2)
        programs = pick(options['common_programs'], PROGRAMS)
        settings = pick(options['windows_settings'], SETTINGS)
        name = 'pmt-' + secrets.token_hex(4)
        print(f'Seed {seed}; VM {name}; packages {programs}; settings {settings}', flush=True)
        draft = {k: cfg[k] for k in ('node', 'storage', 'bridge', 'iso_storage', 'cores', 'memory', 'disk')}
        draft.update(os='windows', purpose='vm', count=1, name=name, password=secrets.token_urlsafe(32)+'Aa1!',
                     source=source['source'], edition=edition['index'], edition_identity=images['identity'],
                     locale=cfg['windows']['locale'], timezone=cfg['windows']['timezone'], agent_manual=True,
                     agent_iso=cfg['bootstrap']['agent_iso'], management_cidr=cfg['bootstrap']['management_cidr'],
                     windows_settings=settings, tasks=[{'id':'install-chocolatey', 'params':{}}] +
                     [{'id':'chocolatey-package', 'params':{'package':pkg}} for pkg in programs])
        review = self.api('simple/plan', draft)
        require(len(review['machines']) == 1 and not review['dangerous'], 'Unexpected build scope')
        target = review['machines'][0]
        require(target['vmid'] != cfg['vmid'] and target['name'] == name and
                all(target[k] == cfg[k] for k in ('node','storage','bridge','cores','memory','disk')) and
                review['edition'] == source['edition'], 'Review differs from the expected new VM')
        require(not self.db.execute("SELECT 1 FROM records WHERE kind='connections' AND id=?",
                                    (f"{target['node']}:{target['vmid']}",)).fetchone(), 'Reviewed ID has an existing saved connection')
        print(f"Reviewed {target['node']}:{target['vmid']} ({review['edition']}); plan {review['id']}", flush=True)
        body = {'plan_id': review['id'], 'confirmed': True}
        try:
            result = self.api('simple/run', body)
        except (requests.Timeout, requests.ConnectionError):
            result = self.api('simple/run', body)  # Same plan, same durable job ID.
        return result['jobs'][0]['id']

    def finish(self, ident):
        plan = self.payload(ident)
        cfg = plan['config']
        require(re.fullmatch(r'pmt-[0-9a-f]{8}', cfg['name']) and cfg['kind']=='windows', 'Choose a test VM job')
        print(f'Watching job {ident} on {cfg["node"]}:{cfg["vmid"]}. Resume this check with --verify-job {ident}.', flush=True)
        previous = None
        deadline = time.monotonic() + 3*3600
        while time.monotonic() < deadline:
            try:
                job = self.api('jobs/' + ident)
            except (requests.ConnectionError, requests.Timeout):
                if previous != 'offline':
                    print('Dashboard unreachable; reconnecting to the same job.', flush=True)
                previous = 'offline'
                time.sleep(10)
                continue
            state = job['status']
            last = (job.get('log') or '').strip().splitlines()[-1:]
            if (state,last) != previous:
                print(state, *last, flush=True)
                previous = state,last
            if state == 'succeeded':
                break
            require(state in ('queued','waiting','running'), 'Build ended with status '+state)
            time.sleep(10)
        else:
            raise TimeoutError('Monitoring timed out; use --verify-job to continue watching the same job')
        self.verify(ident, plan)

    def verify(self, ident, plan):
        cfg = plan['config']
        target = {'node': cfg['node'], 'vmid': cfg['vmid']}
        machine = self.api('simple/machine',target)
        build = self.record('builds', ident)
        binding = self.record('connections', f"{cfg['node']}:{cfg['vmid']}")
        credential = self.record('credentials', cfg['bootstrap']['credential_id'])
        require(machine['name'] == cfg['name'] and machine['status'] == 'running' and build.get('bootstrap_verified')
                and machine['identity'] == build['machine_identity'] == binding['machine_identity']
                and binding['updated'] == build['connection_revision'] and machine.get('verified_at')
                and binding['credential_id'] == credential['id'] and binding['credential_revision'] == credential['updated']
                and binding.get('ca_pem') and binding['verify_tls'] and binding['host'] in {i['host'] for i in machine['addresses']},
                'Guest identity, address or verified login has changed')
        def check(label, script, **params):
            task = {'ansible.windows.win_powershell': {'script':script, 'parameters':params}}
            try:
                run_guest(binding, credential, [task], lambda _: None)
            except Exception as exc:
                raise RuntimeError('Guest check failed: '+label) from exc
            print('Verified',label,flush=True)
        require(machine['cores'] == cfg['cores'] and machine['memory'] == cfg['memory'] * 2**20, 'CPU or RAM differs from the review')
        images = self.api('simple/editions', {'node':cfg['node'], 'source':plan['source']})
        require(images['identity'] == plan['media_identity'], 'The reference installation media has changed')
        edition = next(e for e in images['editions'] if e['index'] == cfg['windows']['image_index'])
        check(f"{cfg['name']}, {edition['label']}, {cfg['cores']} vCPU, {cfg['memory']//1024} GiB RAM, {cfg['disk']} GiB system disk",
              WINDOWS_CHECK, Hostname=cfg['name'], Edition=edition['edition_id'], Build=edition['build'], DiskGiB=cfg['disk'])
        for selection in plan['tasks']:
            task = selection['item']['id']
            if task == 'install-chocolatey':
                continue  # Each package assertion also executes the installed CLI.
            if task == 'chocolatey-package':
                pkg = selection['params']['package']
                check(pkg+' package and executable', PACKAGE_CHECK, Package=pkg, Binary=PROGRAMS[pkg])
            else:
                for path,name,value in SETTINGS[task.removeprefix('setup-')]:
                    check(name, 'param($Path,$Name,[int]$Expected)\nif ((Get-ItemPropertyValue -LiteralPath $Path -Name $Name) -ne $Expected) { throw "Value mismatch" }',
                          Path=path, Name=name, Expected=value)
        print(f"PASS {cfg['name']} at {binding['host']} (VM {cfg['vmid']})",flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--source-job',help='Create one new VM using this earlier Windows ISO build')
    mode.add_argument('--verify-job',help='Watch and verify an existing test job without creating a VM')
    parser.add_argument('--seed',type=int,help='Repeat the same selection on the same catalogue')
    args = parser.parse_args()
    try:
        test = LiveTest()
        test.finish(args.verify_job or test.start(args.source_job, args.seed if args.seed is not None else secrets.randbits(32)))
    except (ValueError, RuntimeError, OSError, requests.RequestException) as error:
        sys.exit(str(error))
