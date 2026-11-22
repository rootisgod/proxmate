"""Start the release Compose file with disposable storage and an unused loopback port."""
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

        def compose(*args, capture=False):
            return subprocess.run(['docker','compose','-f','compose.yaml','-p',project,*args],
                                  cwd=root, env=env, check=True, text=True, stdout=subprocess.PIPE if capture else None)

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
            mounts=json.loads(subprocess.check_output(['docker','inspect',compose('ps','-q','proxmate',capture=True).stdout.strip()],text=True))[0]['Mounts']
            assert any(m['Destination']=='/isos' and not m['RW'] for m in mounts)
            print('Release install passed login, health, read-only ISO mount and encrypted data persistence after recreation.')
        finally:
            compose('down','--volumes')  # Only this randomly named test project.


if __name__=='__main__':
    smoke(sys.argv[1])
