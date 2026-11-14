"""Isolated UI QA: python tests/preview.py. No Proxmox client or worker is used."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
root = Path(tempfile.mkdtemp(prefix='proxmate-wizard-preview-'))
(root / 'password').write_text('preview-only')
os.environ['DATA_DIR'] = str(root)
os.environ['APP_PASSWORD_FILE'] = str(root / 'password')
import app
import operations


def forbidden(*args, **kwargs):
    raise RuntimeError('Live Proxmox access is disabled in this preview')


operations.Proxmox = forbidden
app.inventory = lambda: {'nodes':[{'node':'preview-only','status':'online'}], 'resources':[], 'next_id':999,
    'local_isos':[{'name':'windows-server-test.iso','bytes':5000000000},{'name':'ubuntu-server-test.iso','bytes':3000000000}],
    'details':{'preview-only':{'storage':[{'storage':'local','active':1,'content':'iso,backup'}, {'storage':'local-lvm','active':1,'content':'images'}],
    'network':[{'iface':'vmbr0','type':'bridge'}], 'isos':[{'volid':'local:iso/windows-server-test.iso'}],'backups':[]}}}

if __name__ == '__main__':
    app.app.run(host='127.0.0.1',port=8091,use_reloader=False)
