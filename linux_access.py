"""Public SSH key selection and optional guest password sign-in for Linux builds."""
import base64
import hashlib
import json
import re

import requests
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization


def public_key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 8192 or len(value.splitlines()) != 1:
        raise ValueError('Enter one OpenSSH public key.')
    try:
        key = serialization.load_ssh_public_key(value.encode())
        if value.startswith('sk-'):
            # Preserve the security-key application and wire key type after validation.
            return ' '.join(value.split()[:2])
        return key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValueError('Enter a valid OpenSSH public key.') from None


def github_keys(username):
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', username):
        raise ValueError('Enter a GitHub username.')
    try:
        with requests.get('https://api.github.com/users/'+username+'/keys', params={'per_page': 30},
                          headers={'Accept': 'application/vnd.github+json'}, stream=True,
                          timeout=(4, 6), allow_redirects=False) as response:
            if response.status_code == 404:
                raise ValueError('GitHub account not found. Check the username.')
            if response.status_code != 200:
                raise ValueError('GitHub key lookup failed. Try again or paste your public key.')
            if 'next' in response.links:
                raise ValueError('This GitHub account has more than 30 public keys. Paste the key you want to use.')
            data = bytearray()
            for chunk in response.iter_content(8192):
                data.extend(chunk)
                if len(data) > 100_000:
                    raise ValueError('Too many GitHub keys. Paste one public key.')
            keys = json.loads(data)
            if not isinstance(keys, list) or not keys:
                raise ValueError('This GitHub account has no public SSH keys.')
            return list(dict.fromkeys(public_key(entry['key']) for entry in keys))
    except (requests.RequestException, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('GitHub key lookup failed. Try again or paste your public key.') from None


def fingerprint(key):
    digest = hashlib.sha256(base64.b64decode(key.split()[1])).digest()
    return 'SHA256:'+base64.b64encode(digest).decode().rstrip('=')


def requested_password(draft, distro):
    if draft.get('ubuntu_password'):
        if distro != 'ubuntu' or draft.get('linux_password'):
            raise ValueError('Supply one Linux login password using linux_password.')
        return draft['ubuntu_password']
    return draft.get('linux_password', '')


def password_login(username, password, distro):
    from passlib.hash import sha512_crypt

    if (not isinstance(password, str) or not 12 <= len(password) <= 128 or not password.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in password)):
        raise ValueError('Use a login password of 12–128 characters without control characters.')
    hashed = sha512_crypt.using(rounds=120_000).hash(password)
    steps = [
        {'ansible.builtin.user': {'name': username, 'password': hashed}},
        {'ansible.builtin.blockinfile': {
            'path': '/etc/ssh/sshd_config', 'marker': '# {mark} PROXMATE PASSWORD LOGIN',
            'block': f'Match User {username}\n    PasswordAuthentication yes',
            'validate': '/usr/sbin/sshd -t -f %s',
        }},
        {'ansible.builtin.service': {'name': 'sshd' if distro=='rocky' else 'ssh', 'state': 'reloaded'}},
    ]
    return {'item': {'id': distro+'-password', 'name': 'Enable console and SSH password sign-in',
                     'platform': 'linux', 'kind': 'linux-setup', 'steps': steps, 'dangerous': False}, 'params': {}}
