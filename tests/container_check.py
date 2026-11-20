"""Run inside the built image with --network none; never contact an infrastructure host."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

sys.path.insert(0,'/app')
from guest_tasks import BUILTINS, compile_task

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    os.environ['DATA_DIR']=str(root/'data')
    (root/'password').write_text('isolated-test-only')
    os.environ['APP_PASSWORD_FILE']=str(root/'password')
    import app
    import operations
    def forbidden(*a,**kw): raise AssertionError('No Proxmox calls allowed in isolated container checks')
    operations.Proxmox=forbidden
    client=app.app.test_client()
    assert client.get('/api/health').json['status']=='ok'
    assert client.get('/api/manage/credentials').status_code==401
    token=client.post('/api/login',json={'password':'isolated-test-only'}).json['csrf']
    headers={'X-CSRF-Token':token}
    cred=client.post('/api/manage/credentials',json={'name':'Test','platform':'windows','username':'admin','password':'Do-not-expose-123!'},headers=headers)
    assert cred.status_code==200 and cred.json['has_password'] and 'password' not in cred.json
    assert 'Do-not-expose' not in client.get('/api/manage/credentials').get_data(as_text=True)
    assert len(client.get('/api/manage/tasks').json)==len(BUILTINS)
    assert client.get('/static/manage.js').status_code==200
    assert client.get('/static/lifecycle.js').status_code==200
    assert client.get('/api/lifecycle/state').json['policies']==[]
    helper=client.post('/api/bootstrap/helper',json={'platform':'linux'},headers=headers)
    assert helper.status_code==200
    shell=root/'bootstrap.sh';shell.write_text(helper.text)
    subprocess.run(['sh','-n',str(shell)],check=True)
    assert client.get('/api/server').json['connected'] is False
    assert client.get('/static/server.js').status_code==200
    assert client.get('/static/machine.js').status_code==200
    assert client.post('/api/simple/machine',json={'node':'test','vmid':201},headers=headers).status_code==409
    assert client.get('/static/simple.js').status_code==200
    assert client.get('/api/simple/software').json['features']
    family=client.post('/api/lifecycle/families',json={'name':'Isolated family'},headers=headers)
    assert family.status_code==409  # No selected server on a clean installation.
    assert client.post('/api/jobs',json={'action':'start','node':'pve','vmid':201},headers=headers).status_code==409
    assert client.get('/api/openapi.json').status_code==200
    import bootstrap
    generated=bootstrap.windows_script({'management_cidr':'192.0.2.0/24'})
    assert 'Invoke-ProxmateBootstrap' in generated and '__SUBNET__' not in generated
    # Syntax-check actual installed collections without running any task.
    for platform in ('windows','linux'):
        tasks=[]
        for item in BUILTINS:
            if item['platform']!=platform:continue
            params={k:('started' if k=='state' else 'testpackage') for k in item['parameters']}
            tasks.extend(compile_task(item,params))
        playbook=root/(platform+'.yml')
        playbook.write_text(yaml.safe_dump([{'hosts':'all','gather_facts':False,'tasks':tasks}]))
        result=subprocess.run(['ansible-playbook','--syntax-check','-i','192.0.2.20,',str(playbook)],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    print('Container authentication, credential encryption, static routes and all built-in Ansible syntax checks passed. No network available.')
