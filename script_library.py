"""Starter guest scripts and parse-only checks shared by the editor and save API."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import yaml


class ScriptDiagnostic(ValueError):
    def __init__(self, message, line=1, column=1):
        self.message, self.line, self.column = message, line, column
        super().__init__(f'Line {line}, column {column}: {message}')


def example(ident, name, os_name, kind, description, body, *, parameters=None, defaults=None):
    return dict(id='example-' + ident, name=name, platform=os_name, kind=kind, example=True,
                dangerous=True, parameters=parameters or {}, defaults=defaults or {}, description=description, body=body)


EXAMPLES = [
    example('win-folder', 'Create a tools folder', 'windows', 'ansible',
            'Create a folder on C: if it is missing. The default is C:\\LabTools.',
            "- name: Create a tools folder\n"
            "  ansible.windows.win_file:\n"
            "    path: 'C:\\{{ proxmate_inputs.name }}'\n"
            "    state: directory\n",
            parameters={'name': 'Folder name'}, defaults={'name': 'LabTools'}),
    example('win-report', 'Write a Windows version report', 'windows', 'powershell',
            'Save the computer name and Windows build under ProgramData.',
            "$os = Get-CimInstance Win32_OperatingSystem\n"
            "$report = [PSCustomObject]@{\n    Computer = $env:COMPUTERNAME\n    Windows = $os.Caption\n    Build = $os.BuildNumber\n}\n"
            "$report | ConvertTo-Json | Set-Content -Path (Join-Path $env:ProgramData 'lab-system.json')\n"),
    example('linux-folder', 'Create a shared tools directory', 'linux', 'ansible',
            'Create /opt/lab-tools with predictable permissions.',
            "- name: Create the tools directory\n  ansible.builtin.file:\n    path: /opt/lab-tools\n    state: directory\n    mode: '0755'\n"),
    example('linux-report', 'Write a Linux system report', 'linux', 'powershell',
            'Save the hostname and kernel release under /var/tmp. Requires pwsh on the guest.',
            "$report = [PSCustomObject]@{\n    Hostname = [Environment]::MachineName\n    Kernel = (uname -r)\n}\n"
            "$report | ConvertTo-Json | Set-Content -Path '/var/tmp/lab-system.json'\n"),
    example('linux-shell', 'Record the Linux release', 'linux', 'shell',
            'Copy the distribution release information into /var/tmp.',
            "set -eu\ncp /etc/os-release /var/tmp/lab-os-release\n"),
]


PS_PARSE = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$tokens = $errors = $null
$null = [Management.Automation.Language.Parser]::ParseInput([Console]::In.ReadToEnd(), [ref]$tokens, [ref]$errors)
if ($errors.Count) {
    $first = $errors[0]
    [Console]::Write((@{message=$first.Message;line=$first.Extent.StartLineNumber;column=$first.Extent.StartColumnNumber} | ConvertTo-Json -Compress))
}
"""


def check_powershell(body):
    parser = shutil.which('pwsh') or (shutil.which('powershell') if os.name == 'nt' else None)
    if not parser:
        raise ValueError('PowerShell parser is unavailable on this server')
    env = {k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE')}
    env.update(POWERSHELL_TELEMETRY_OPTOUT='1', POWERSHELL_UPDATECHECK='Off', DOTNET_NOLOGO='1')
    result = subprocess.run([parser, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', PS_PARSE],
                            input=body.encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=env, timeout=10)
    if result.returncode:
        raise ValueError('PowerShell syntax check could not finish')
    if result.stdout.strip():
        error = json.loads(result.stdout.decode('utf-8-sig'))
        raise ScriptDiagnostic(error['message'], error['line'], error['column'])


def check_script(platform, kind, body):
    if platform not in ('windows', 'linux') or kind not in ('ansible', 'powershell', 'shell'):
        raise ValueError('Choose a platform and script type')
    if kind == 'shell' and platform != 'linux':
        raise ValueError('POSIX shell requires a Linux guest')
    if not isinstance(body, str) or not body.strip() or len(body) > 100000 or '\x00' in body:
        raise ValueError('Enter 1–100000 characters of script text')
    if kind == 'ansible':
        validate_script(body)
    elif kind == 'powershell':
        check_powershell(body)
    else:
        shell = shutil.which('sh')
        if not shell and os.name == 'nt':
            git_shell = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/sh.exe'
            shell = str(git_shell) if git_shell.is_file() else None
        if not shell:
            raise ValueError('POSIX shell parser is unavailable on this server')
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'SystemRoot', 'SYSTEMROOT')}
        result = subprocess.run([shell, '-n', '-s'], input=body.replace('\r\n', '\n').encode(),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, timeout=5)
        if result.returncode:
            error = result.stderr.decode('utf-8', 'replace').splitlines()
            location = re.search(r':\s*(?:line )?(\d+):', error[0]) if error else None
            raise ScriptDiagnostic('Invalid POSIX shell syntax. Check quotes and block endings.',
                                   int(location[1]) if location else 1)


def validate_script(body):
    try: tasks = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        raise ScriptDiagnostic(getattr(exc, 'problem', None) or 'Invalid Ansible task YAML',
                               mark.line + 1 if mark else 1, mark.column + 1 if mark else 1) from None
    if not isinstance(tasks, list) or not tasks or len(tasks) > 100:
        raise ValueError('Provide a YAML list of 1-100 Ansible tasks, not a full playbook')
    # Prevent accidental host/controller delegation and inventory/connection overrides.
    allowed_modules = {'ansible.builtin.command', 'ansible.builtin.shell', 'ansible.builtin.package',
                       'ansible.builtin.apt', 'ansible.builtin.dnf', 'ansible.builtin.service',
                       'ansible.builtin.copy', 'ansible.builtin.file', 'ansible.builtin.lineinfile',
                       'ansible.windows.win_powershell', 'ansible.windows.win_shell',
                       'ansible.windows.win_command', 'ansible.windows.win_service',
                       'ansible.windows.win_copy', 'ansible.windows.win_file',
                       'ansible.windows.win_regedit', 'ansible.windows.win_updates', 'ansible.windows.win_feature',
                       'chocolatey.chocolatey.win_chocolatey'}
    for index, task in enumerate(tasks):
        def task_error(message):
            mark = yaml.compose(body).value[index].start_mark
            return ScriptDiagnostic(message, mark.line + 1, mark.column + 1)
        if not isinstance(task, dict): raise task_error('Each task must be an object')
        modules = set(task) & allowed_modules
        if len(modules) != 1 or set(task) - allowed_modules - {'name'}:
            raise task_error('Use one supported fully qualified guest module per task, with an optional name; delegation and connection overrides are not supported')
    return tasks
