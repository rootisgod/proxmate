"""Local, opt-in logins for provisioning new guests. Only public keys enter Linux setup."""
import re

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from provisioning import windows_options


def linux_username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', value):
        raise ValueError('Choose a Linux login name using lowercase letters, numbers, underscores or hyphens')
    if value in ('root','daemon','bin','sys','sync','games','man','lp','mail','news','uucp','proxy','www-data',
                 'backup','list','irc','_apt','nobody','messagebus','systemd-network','systemd-timesync','sshd'):
        raise ValueError('Choose a Linux login name other than a system account.')
    return value


def setup_key(credential, platform):
    """Validate installer compatibility and return the Linux public key, if applicable."""
    if credential['platform'] != platform:
        raise ValueError('Choose a credential for this operating system.')
    if platform == 'windows':
        if credential['username'].casefold() != 'administrator' or not credential.get('password'):
            raise ValueError('Windows setup needs the local Administrator account and its password.')
        windows_options({'admin_password': credential['password']})
        return ''
    linux_username(credential['username'])
    material = credential.get('private_key', '').strip().encode()
    try:
        load = serialization.load_ssh_private_key if material.startswith(b'-----BEGIN OPENSSH') else serialization.load_pem_private_key
        key = load(material, password=None)
        if not isinstance(key, (ec.EllipticCurvePrivateKey, ed25519.Ed25519PrivateKey, rsa.RSAPrivateKey)):
            raise ValueError()
        return key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValueError('Linux builds need a valid, unencrypted SSH private key for the guest login.') from None


def defaults(manager):
    result = dict(windows=None, linux=None)
    with manager.db() as con:
        for row in con.execute("""SELECT d.platform, r.value FROM build_credential_defaults d
                                  JOIN records r ON r.kind='credentials' AND r.id=d.credential_id"""):
            credential = manager.decode(row['value'])
            result[row['platform']] = {k: credential[k] for k in ('id', 'name', 'username')}
    return result


def set_default(manager, data):
    if not isinstance(data, dict) or set(data) != {'platform','credential_id'} or data['platform'] not in ('windows','linux'):
        raise ValueError('Select Windows or Linux and a saved credential, or clear its default.')
    platform, ident = data['platform'], data['credential_id']
    with manager.db() as con:
        con.execute('BEGIN IMMEDIATE')
        if ident is None:
            con.execute('DELETE FROM build_credential_defaults WHERE platform=?', (platform,))
        else:
            if not isinstance(ident, str): raise ValueError('Select a saved credential.')
            row = con.execute("SELECT value FROM records WHERE kind='credentials' AND id=?", (ident,)).fetchone()
            if not row: raise ValueError('Saved credential no longer exists.')
            setup_key(manager.decode(row['value']), platform)
            con.execute('INSERT OR REPLACE INTO build_credential_defaults VALUES (?,?)', (platform, ident))
    return defaults(manager)


def selected(manager, platform, ident):
    if not isinstance(ident,str):
        raise ValueError('Choose a saved build credential by ID.')
    with manager.db() as con:
        row = con.execute("""SELECT r.value FROM build_credential_defaults d JOIN records r
                             ON r.kind='credentials' AND r.id=d.credential_id WHERE d.platform=? AND r.id=?""",
                          (platform,ident)).fetchone()
    if not row: raise ValueError('The selected build default changed. Reopen the build and review the guest login.')
    credential = manager.decode(row['value'])
    return credential, setup_key(credential, platform)
