"""Start the release Compose file with disposable storage and an unused loopback port."""
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4

import requests


def recover_on_second_dashboard(source, url, target_url, password, target_password, credential_id):
    script = dict(name='Recovery check', platform='linux', kind='shell', body='date -u')
    saved = source.post(url+'/manage/tasks', json=script, timeout=10)
    saved.raise_for_status()
    with requests.Session() as target:
        login = target.post(target_url+'/login', json={'password':target_password}, timeout=10)
        login.raise_for_status()
        target.headers['X-CSRF-Token'] = login.json()['csrf']
        assert target.get(target_url+'/manage/credentials', timeout=10).json() == []
        for mode in ('portable', 'full'):
            phrase = secrets.token_hex(24)
            exported = source.post(url+'/dashboard-backup/export', json=dict(mode=mode, passphrase=phrase, admin_password=password), timeout=30)
            exported.raise_for_status()
            upload = dict(archive=base64.b64encode(exported.content).decode(), passphrase=phrase)
            preview = target.post(target_url+'/dashboard-backup/preview', json=upload, timeout=30)
            preview.raise_for_status()
            reviewed = preview.json()
            request = dict(**upload, review=reviewed['review'], confirmed=True)
            if mode == 'portable':
                request['selected'] = [item['id'] for item in reviewed['items'] if item['status']=='new']
            else:
                request['admin_password'] = target_password
            restored = target.post(target_url+'/dashboard-backup/restore', json=request, timeout=30)
            restored.raise_for_status()
            tasks = target.get(target_url+'/manage/tasks', timeout=10).json()
            assert any(t['name']==script['name'] and t['body']==script['body'] for t in tasks)
            credentials = target.get(target_url+'/manage/credentials', timeout=10).json()
            assert [r['id'] for r in credentials] == ([credential_id] if mode=='full' else [])
            assert target.get(target_url+'/jobs', timeout=10).json()==[]
        assert target.get(target_url+'/server', timeout=10).json()['connected'] is False


def smoke(image):
    source = Path(__file__).resolve().parents[1] / 'compose.release.yaml'
    with tempfile.TemporaryDirectory(prefix='proxmate-install-') as tmp:
        root = Path(tmp)
        shutil.copyfile(source, root / 'compose.yaml')
        (root / 'isos').mkdir()
        password = secrets.token_hex(24)
        secret = root / '.env.admin'
        secret.write_text(password, encoding='ascii')
        secret.chmod(0o444)  # The temporary parent is private; the mounted file is readable by UID 10001.
        env = dict(os.environ, PROXMATE_IMAGE=image, PORT='127.0.0.1:0', ISO_PATH='./isos')
        project = 'proxmate-check-' + uuid4().hex
        recovery_root = root / 'recovery'
        recovery_root.mkdir()
        shutil.copyfile(source, recovery_root / 'compose.yaml')
        (recovery_root/'isos').mkdir()
        target_password = secrets.token_hex(24)
        (recovery_root/'.env.admin').write_text(target_password, encoding='ascii')
        (recovery_root/'.env.admin').chmod(0o444)

        def compose(*args, capture=False, recovery=False):
            return subprocess.run(['docker','compose','-f','compose.yaml','-p',project+'-recovery' if recovery else project,*args],
                                  cwd=recovery_root if recovery else root, env=env, check=True, text=True, stdout=subprocess.PIPE if capture else None)

        try:
            for first_start in (True, False):
                compose('up','-d','--force-recreate','--no-build','--pull','never','--wait','--wait-timeout','90')
                address = compose('port','proxmate','8080',capture=True).stdout.strip()
                assert address.startswith('127.0.0.1:')
                url = 'http://' + address + '/api'
                with requests.Session() as session:
                    assert session.get(url+'/health',timeout=8).json()['status']=='ok'
                    assert session.get(url+'/manage/credentials',timeout=8).status_code==401
                    login=session.post(url+'/login',json={'password':password},timeout=8)
                    login.raise_for_status()
                    assert session.get(url+'/server',timeout=8).json()['connected'] is False
                    saved=session.get(url+'/manage/credentials',timeout=8).json()
                    if first_start:
                        assert saved==[]
                        response=session.post(url+'/manage/credentials',headers={'X-CSRF-Token':login.json()['csrf']},
                            json=dict(name='Persistence check',platform='windows',username='Administrator',password=secrets.token_hex(24)), timeout=8)
                        response.raise_for_status()
                        record=response.json()['id']
                    else:
                        assert [r['id'] for r in saved]==[record] and saved[0]['has_password']
                        compose('up','-d','--no-build','--pull','never','--wait','--wait-timeout','90',recovery=True)
                        target_address=compose('port','proxmate','8080',capture=True,recovery=True).stdout.strip()
                        assert target_address.startswith('127.0.0.1:') and target_address!=address
                        session.headers['X-CSRF-Token']=login.json()['csrf']
                        recover_on_second_dashboard(session,url,'http://'+target_address+'/api',password,target_password,record)
            mounts=json.loads(subprocess.check_output(['docker','inspect',compose('ps','-q','proxmate',capture=True).stdout.strip()],text=True))[0]['Mounts']
            assert any(m['Destination']=='/isos' and not m['RW'] for m in mounts)
            print('Release install passed health, login, read-only ISO mount, persistence, portable migration and full recovery on a second dashboard.')
        finally:
            compose('down','--volumes',recovery=True)
            compose('down','--volumes')  # Only this randomly named test project.


if __name__=='__main__':
    smoke(sys.argv[1])
