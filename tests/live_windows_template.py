"""Opt-in Windows template and full-clone acceptance test on a connected lab node."""
import argparse
import io
import json
import re
import secrets
import time
import zipfile
from xml.etree import ElementTree

import requests
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from guest_tasks import run_guest
from live_windows_build import LiveTest, PACKAGE_CHECK, PROGRAMS, WINDOWS_CHECK, require
from operations import Proxmox
from vm_config import config_identity, machine_identity, vm_path


def verify_folder_task(item):
    steps = yaml.safe_load(item['body'])
    require(item['kind'] == 'ansible' and item['platform'] == 'windows' and isinstance(steps, list)
            and len(steps) == 1 and isinstance(steps[0], dict) and set(steps[0]) <= {'name', 'ansible.windows.win_file'}
            and steps[0].get('ansible.windows.win_file') == {'path': r'C:\{{ proxmate_inputs.name }}', 'state': 'directory'},
            'The library example has been customized. Inspect it before using it in the live test.')


def submit(test, draft, *, source_identity=None):
    review = test.api('simple/plan', draft)
    require(len(review['machines']) == 1 and review['purpose'] == draft['purpose'], 'Unexpected review scope')
    machine = review['machines'][0]
    require(all(machine[key] == draft[key] for key in ('name', 'node', 'storage', 'cores', 'memory', 'bridge')),
            'Reviewed placement or hardware changed')
    frozen = test.read('SELECT value FROM plans WHERE id=?', (review['id'],))
    config = frozen['configs'][0]
    require(config['vmid'] == machine['vmid'] and config['kind'] == 'windows', 'Unexpected frozen destination')
    for field in ('source', 'edition_identity'):
        if field in draft:
            require(frozen['source' if field == 'source' else 'media_identity'] == draft[field], 'ISO review changed')
    if 'template_id' in draft:
        require(config['template']['source'] == draft['template_id'] and config['template']['identity'] == source_identity
                and review['clone_mode'] == 'full',
                'Unexpected template or clone mode')
    require(review['data_disks'] == draft.get('data_disks', []), 'Unexpected disk review')
    require([(s['item']['id'], s['params']) for s in frozen['tasks']] ==
            [(s['id'], s['params']) for s in draft['tasks']], 'Unexpected guest tasks')
    require(all(not s['item'].get('dangerous') or s['item']['id'] == 'example-win-folder' for s in frozen['tasks']),
            'Unexpected risky task')
    for selection in frozen['tasks']:
        if selection['item']['id'] == 'example-win-folder':
            verify_folder_task(selection['item'])
    if review['dangerous']:
        require([s['item']['id'] for s in frozen['tasks']] == ['example-win-folder'], 'Unexpected risk confirmation')
    body = dict(plan_id=review['id'], confirmed=True, risk_confirmed=review['dangerous'])
    print(f"Reviewed {machine['node']}:{machine['vmid']} {machine['name']} ({review['edition']})", flush=True)
    try:
        result = test.api('simple/run', body)
    except (requests.Timeout, requests.ConnectionError):
        result = test.api('simple/run', body)
    ident = result['jobs'][0]['id']
    print('Submitted job', ident, flush=True)
    return ident


def start_template(test, source_job, node):
    original = test.payload(source_job)
    cfg = original['config']
    require(cfg['node'] == node and cfg['kind'] == 'windows' and cfg['source_kind'] in ('local', 'proxmox')
            and cfg['windows']['accept_eula'], 'Choose a previously accepted Windows ISO build on the test node')
    images = test.api('simple/editions', dict(node=node, source=original['source']))
    edition = next((e for e in images['editions'] if e['index'] == cfg['windows']['image_index']), None)
    require(images['identity'] == original['media_identity'] and edition and edition['desktop'],
            'Windows Desktop Experience source or edition changed')
    draft = {key: cfg[key] for key in ('node', 'storage', 'iso_storage', 'bridge', 'cores', 'memory', 'disk')}
    draft.update(os='windows', purpose='template', count=1, name='pwt-'+secrets.token_hex(4),
                 password=secrets.token_urlsafe(32)+'Aa1!', source=original['source'], edition=edition['index'],
                 edition_identity=images['identity'], locale=cfg['windows']['locale'], timezone=cfg['windows']['timezone'],
                 management_cidr=cfg['bootstrap']['management_cidr'], agent_manual=True, agent_iso=cfg['bootstrap']['agent_iso'],
                 tasks=[dict(id='install-chocolatey', params={}), dict(id='chocolatey-package', params={'package':'7zip'})])
    return submit(test, draft)


def check_answer(test, job, cfg):
    response = test.http.get(test.base_url + 'jobs/' + job + '/answer', timeout=20, allow_redirects=False)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        manifest = json.loads(bundle.read('build.json'))
        xml = bundle.read('Autounattend.xml')
        root = ElementTree.fromstring(xml)
        require(manifest['job'] == job and manifest['machine'] == cfg['name'] and manifest['original'] is False
                and cfg['windows']['admin_password'].encode() not in xml and b'REPLACE_WITH_PASSWORD' in xml
                and root.findtext('.//{*}ComputerName') == cfg['name'], 'Reusable answer archive mismatch')
    print('Verified reusable answer for', cfg['name'], flush=True)


def clone_job(test, template, suffix, extra, source_identity):
    name = ('pwa-' if extra else 'pwb-') + suffix
    candidates = [row for row in test.api('jobs?summary=1') if row.get('server_id') == test.server
                  and row['action'] == 'simple' and row.get('target', {}).get('name') == name]
    if candidates:
        require(len(candidates) == 1, 'Multiple builds have the expected clone name')
        job = candidates[0]['id']
        cfg = test.payload(job)['config']
        require(cfg.get('template', {}).get('source') == template['vmid'] and cfg['node'] == template['node']
                and cfg['template']['identity'] == source_identity, 'Existing job belongs to a different template')
        print('Using existing clone job', job, flush=True)
        return job
    draft = {key: template[key] for key in ('node', 'storage', 'iso_storage', 'bridge')}
    draft.update(os='windows', purpose='vm', source_kind='template', template_id=template['vmid'], generalized=True,
                 clone_mode='full', cores=2, memory=4096, name=name, password=secrets.token_urlsafe(32)+'Aa1!',
                 management_cidr=template['bootstrap']['management_cidr'], count=1, tasks=[])
    if extra:
        draft.update(data_disks=[{'size': 2, 'label': 'PMData', 'letter': 'R'}],
                     tasks=[dict(id='example-win-folder', params={'name': 'PmTools'+suffix})])
    return submit(test, draft, source_identity=source_identity)


CLONE_CHECK = r"""
param($Thumbprint, $Folder, [bool]$Extra, $Serial, $SourceName)
$setup = Get-ItemProperty -LiteralPath HKLM:\SYSTEM\Setup
if ($setup.SystemSetupInProgress -ne 0 -or $setup.OOBEInProgress -ne 0) { throw 'Windows setup is unfinished' }
if ($setup.UnattendFile -match '^[D-Z]:\\Autounattend\.xml$' -or
    $setup.UnattendFile -eq '\\?\GLOBALROOT\Device\CdRom0\Autounattend.xml') { throw 'Detached setup DVD still configured' }
foreach ($path in @('C:\Windows\Panther\unattend-original.xml','C:\Windows\Panther\unattend.xml',
                    'C:\Windows\Panther\Unattend\unattend.xml','C:\Windows\System32\Sysprep\unattend.xml')) {
    if (Test-Path -LiteralPath $path) {
        $answer = [xml](Get-Content -LiteralPath $path -Raw)
        if (@($answer.SelectNodes("//*[local-name()='ComputerName']") | Where-Object InnerText -eq $SourceName).Count) {
            throw 'Source template answer was inherited'
        }
    }
}
$listener = @(Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTPS' })
if ($listener.Count -ne 1 -or (Get-Item -LiteralPath ($listener[0].PSPath+'\CertificateThumbprint')).Value -ne $Thumbprint) {
    throw 'Unexpected WinRM listener identity'
}
if ((Test-Path -LiteralPath (Join-Path C:\ $Folder) -PathType Container) -ne $Extra) { throw 'Folder mismatch' }
$disks = @(Get-Disk)
if (!$Extra) {
    if ($disks.Count -ne 1 -or @(Get-Volume | Where-Object FileSystemLabel -eq 'PMData').Count) { throw 'Unexpected data disk' }
    return
}
$drive = Get-Volume -DriveLetter R
$disk = Get-Partition -DriveLetter R | Get-Disk
if ($disks.Count -ne 2 -or $disk.IsBoot -or $disk.IsSystem -or $disk.Size -ne 2GB -or
    $disk.SerialNumber.Trim() -cne $Serial -or $drive.FileSystem -ne 'NTFS' -or $drive.FileSystemLabel -cne 'PMData' -or
    [IO.File]::ReadAllText(('R:\.proxmate-volume-'+$Serial)) -cne $Serial) { throw 'Data disk mismatch' }
"""


def exercise(test, job, node):
    plan = test.payload(job)
    template = plan['config']
    require(plan['purpose'] == 'template' and template['kind'] == 'windows' and template['node'] == node
            and re.fullmatch(r'pwt-[0-9a-f]{8}', template['name']) and not template['data_disks'],
            'Choose a Windows acceptance template job on this node')
    print('Template job', job, 'on', node, template['vmid'], flush=True)
    test.wait(job)
    build = test.record('builds', job)
    server = test.read('SELECT value FROM server_connection WHERE id=1', ())
    require(server.get('connected') and server['id'] == test.server, 'Selected server changed')
    pve = Proxmox(server)
    path = vm_path(template)
    source = pve.call('GET', path+'/config')
    require(build.get('bootstrap_verified') and build['machine_identity'] == machine_identity(source)
            and source.get('template') and source.get('name') == template['name']
            and pve.call('GET', path+'/status/current')['status'] == 'stopped', 'Template was not sealed and stopped')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(row.get('node') == node and row.get('vmid') == template['vmid'] and row.get('template')
               and row.get('name') == template['name'] and row.get('status') == 'stopped'
               for row in test.api('simple/resources')):
            break
        time.sleep(2)
    else:
        raise TimeoutError('The stopped template did not appear in dashboard inventory')
    check_answer(test, job, template)
    source_identity = config_identity(source)
    suffix = template['name'][4:]
    edition_info = test.api('simple/editions', {'node':node, 'source':plan['source']})
    require(edition_info['identity'] == plan['media_identity'], 'Reference installation media changed')
    edition = next(e for e in edition_info['editions'] if e['index'] == template['windows']['image_index'])
    identities = {machine_identity(source)}
    thumbprints = set()
    jobs = [clone_job(test, template, suffix, extra, source_identity) for extra in (True, False)]
    for index, ident in enumerate(jobs):
        test.wait(ident)
        clone = test.payload(ident)
        cfg = clone['config']
        require(cfg['template']['identity'] == source_identity and cfg['template']['mode'] == 'full'
                and cfg['template']['source'] == template['vmid'], 'Clone review differs from the sealed source')
        machine, binding, credential = test.verified_guest(ident, clone)
        require(machine['identity'] not in identities and machine['cores'] == 2 and machine['memory'] == 4*2**30,
                'Clone identity or hardware mismatch')
        identities.add(machine['identity'])
        thumbprint = x509.load_pem_x509_certificate(binding['ca_pem'].encode()).fingerprint(hashes.SHA1()).hex().upper()
        require(thumbprint not in thumbprints, 'Clones share the same WinRM listener certificate')
        thumbprints.add(thumbprint)
        def check(label, script, **inputs):
            try:
                run_guest(binding, credential, [{'ansible.windows.win_powershell': {'script':script, 'parameters':inputs}}],
                          lambda _: None)
            except RuntimeError as exc:
                raise RuntimeError(f'{cfg["name"]} failed guest check: {label}') from exc
            print('Verified', cfg['name'], label, flush=True)
        check('hostname, installed edition and system disk', WINDOWS_CHECK, Hostname=cfg['name'],
              Edition=edition['edition_id'], Build=edition['build'], DiskGiB=template['disk'])
        check('inherited 7-Zip package and executable', PACKAGE_CHECK, Package='7zip', Binary=PROGRAMS['7zip'])
        extra = index == 0
        require(len(cfg['data_disks']) == int(extra), 'Unexpected clone disk allocation')
        check('listener, folder and disk layout', CLONE_CHECK, Thumbprint=thumbprint, Folder='PmTools'+suffix,
              Extra=extra, Serial=cfg['data_disks'][0]['serial'] if extra else '', SourceName=template['name'])
        check_answer(test, ident, cfg)
        print(f'PASS {cfg["name"]} VM {cfg["vmid"]} job {ident}', flush=True)
    require(config_identity(pve.call('GET', path+'/config')) == source_identity
            and pve.call('GET', path+'/status/current')['status'] == 'stopped', 'Source template changed')
    print('PASS stopped source template and two independently verified full clones', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--source-job', help='Earlier accepted Windows ISO build on the selected node')
    source.add_argument('--template-job', help='Resume the same acceptance template and its two named clones')
    parser.add_argument('--node', required=True)
    args = parser.parse_args()
    test = LiveTest()
    exercise(test, args.template_job or start_template(test, args.source_job, args.node), args.node)
