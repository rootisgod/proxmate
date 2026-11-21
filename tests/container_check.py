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
    # Execute the real Kickstart disk-selection script against synthetic block-device reports.
    import rocky_build
    answer=rocky_build.kickstart(dict(username='rocky',name='rk-test',disk_serial='pm1234',ssh_key='example'))
    include=root/'storage.ks'
    guard=answer.split('%pre --interpreter=/bin/bash --erroronfail\n')[1].split('%end',1)[0].replace('/tmp/proxmate-storage.ks',str(include))
    for tool,body in {'lsblk':'echo "$DISKS"','blockdev':'echo "$READ_ONLY"',
                      'readlink':'echo "/sys/devices/virtual/block/zram0"',
                      'udevadm':'if test "$1" = info; then echo "ID_SCSI_SERIAL=$SERIAL"; fi'}.items():
        binary=root/tool
        binary.write_text('#!/bin/sh\n'+body+'\n')
        binary.chmod(0o755)
    for disks,serial,readonly,accept in [('sda disk','other','0',False),('sda disk\nsdb disk','pm1234','0',False),
                                       ('sda disk','pm1234','1',False),('sda disk\nzram1 disk','pm1234','0',False),
                                       ('sda disk\nzram0 disk','pm1234','0',True)]:
        env=dict(os.environ,DISKS=disks,SERIAL=serial,READ_ONLY=readonly,PATH=str(root)+':'+os.environ['PATH'])
        result=subprocess.run(['bash','-c',guard],env=env)
        assert (result.returncode==0)==accept and include.exists()==accept
    assert 'ignoredisk --only-use=sda' in include.read_text()
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
    assert client.get('/static/editor-assets/code.js').status_code==200
    examples = [t for t in client.get('/api/manage/tasks').json if t.get('example')]
    assert len(examples)==5
    for example in examples:
        assert client.post('/api/manage/tasks/check',json=example,headers=headers).json=={'valid':True}
    broken = dict(platform='windows',kind='powershell',body='Get-Date\nif (')
    diagnostic = client.post('/api/manage/tasks/check',json=broken,headers=headers).json
    assert not diagnostic['valid'] and diagnostic['line']==2,diagnostic
    assert client.get('/static/notifications.js').status_code==200
    assert client.get('/static/windows.js').status_code==200
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
            if item['id']=='windows-domain-controller':
                params={'domain_name':'lab.example.test','dsrm_password':'Fixture only 123!'}
            steps=compile_task(item,params)
            if item['id']=='windows-domain-controller':
                from script_library import check_powershell
                for step in steps:
                    if 'ansible.windows.win_powershell' in step:
                        check_powershell(step['ansible.windows.win_powershell']['script'])
            tasks.extend(steps)
        if platform=='windows':
            import windows_setup
            for selected in windows_setup.select([o['id'] for o in windows_setup.OPTIONS],[],app.manager,
                                                 platform='windows',purpose='vm',subnet='192.0.2.0/24',desktop=True):
                tasks.extend(compile_task(selected['item'],selected['params']))
            feature = dict(app.manager.task('windows-feature'), installer_sxs=True)
            tasks.extend(compile_task(feature, {'feature':'NET-Framework-Core'}))
            disk=dict(serial='PM0123456789abcdef',size=32,label='Data',letter='F')
            tasks.extend(compile_task(dict(id='prepare-created-volume',name='Prepare Data',disk=disk),{}))
            custom=dict(id='fixture',name='Create a share directory',kind='ansible',parameters={'share':'Share name'},
                        body='- ansible.windows.win_file:\n    path: "C:/Shares/{{ proxmate_inputs.share }}"\n    state: directory')
            tasks.extend(compile_task(custom,{'share':'Lab'}))
        else:
            import pycdlib
            import linux_access
            import ubuntu_build
            selection=linux_access.password_login('ubuntu','Fixture password 123!','ubuntu')
            tasks.extend(compile_task(selection['item'],selection['params']))
            for option in ('ssh','off'):
                for selection in ubuntu_build.setting_tasks(dict(ubuntu_firewall=option,ubuntu_updates='manual')):
                    tasks.extend(compile_task(selection['item'],selection['params']))
            import io
            source=pycdlib.PyCdlib()
            source.new(rock_ridge='1.09')
            source.add_directory('/CASPER',rr_name='casper')
            for name in ('vmlinuz','initrd'):
                data=('test '+name).encode()
                source.add_fp(io.BytesIO(data),len(data),iso_path='/CASPER/'+name.upper()+';1',rr_name=name)
            source.write(str(root/'noble.iso'))
            source.close()
            os.environ['ISO_DIR']=str(root)
            seed=root/'ubuntu-test.iso'
            cfg=dict(node='lab',username='ubuntu',name='noble',disk_serial='pm0123',ssh_key='')
            ubuntu_build.boot_iso(seed,cfg,'qa','library:noble.iso')
            image=pycdlib.PyCdlib()
            image.open(str(seed))
            try:
                assert image.pvd.volume_identifier.decode().strip()=='CIDATA'
                assert image.eltorito_boot_catalog.validation_entry.platform_id == 0
                for entry in ('/boot/grub/grub.cfg','/boot/proxmate/vmlinuz','/boot/proxmate/initrd','/user-data','/meta-data'):
                    assert ubuntu_build.media.image_entry(image,entry),entry
            finally:
                image.close()
        playbook=root/(platform+'.yml')
        playbook.write_text(yaml.safe_dump([{'hosts':'all','gather_facts':False,'tasks':tasks}]))
        result=subprocess.run(['ansible-playbook','--syntax-check','-i','192.0.2.20,',str(playbook)],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    print('Container authentication, credential encryption, static routes and all built-in Ansible syntax checks passed. No network available.')
