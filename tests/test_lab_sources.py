import copy
from types import SimpleNamespace

import operations
from test_api import service
from test_simple_flow import settings, simple_lab
from vm_config import machine_identity


ROUTE = '/api/simple/labs/machine-source'


class ConfigReader:
    def __init__(self, target, config, *, guest_type='qemu'):
        self.target = target
        self.config = config
        self.guest_type = guest_type
        self.reads = []
        self.session = SimpleNamespace(close=lambda: None)

    def resources(self):
        self.reads.append('inventory')
        return [dict(self.target, type=self.guest_type, status='running', name='inventory-name')]

    def call(self, method, path, **data):
        assert method == 'GET' and not data
        assert path == f'nodes/{self.target["node"]}/qemu/{self.target["vmid"]}/config'
        self.reads.append('config')
        return copy.deepcopy(self.config)


def test_external_source_reads_hardware_only(service, monkeypatch):
    app, client = service
    target = dict(node='pve', vmid=202)
    cfg = dict(name='external', cores='2', sockets=3, memory=8192, ostype='l26',
               boot='order=ide2;scsi1;net0', ide2='local:iso/source.iso,media=cdrom,size=4G',
               scsi0='local:vm-202-disk-0,size=500G', scsi1='private-store:vm-202-disk-1,size=36.5G',
               net0='e1000=BB:BB:BB:BB:BB:BB,bridge=secret-bridge', ipconfig0='ip=192.0.2.17',
               smbios1='uuid=machine-secret', cipassword='sensitive', sshkeys='sensitive-public-key')
    host=ConfigReader(target,cfg)
    monkeypatch.setattr(operations,'Proxmox',lambda:host)
    headers={'Authorization':'Bearer '+app.API_TOKEN}
    response=client.post(ROUTE,json=target,headers=headers)
    assert response.status_code==200 and host.reads==['inventory','config']
    assert response.json==dict(name='external', basis='hardware', settings=dict(cores=6,memory=8192,disk=37),
                               source=None,os=None,tasks=[])
    cfg.update(ostype='win11', template=1)
    del cfg['boot']
    reply=client.post(ROUTE,json=target,headers=headers).json
    assert reply['os']=='windows' and 'disk' not in reply['settings']
    assert not client.get('/api/jobs',headers=headers).json and not app.manager.all('lab-packs')


def test_guided_source_uses_owned_identity_and_frozen_choices(simple_lab, monkeypatch):
    app,client,h,_=simple_lab
    draft={**settings(),'count':1,'tasks':[{'id':'windows-domain-controller',
           'params':{'domain_name':'lab.example.test','dsrm_password':'Original recovery #456!'}}]}
    review=client.post('/api/simple/plan',json=draft,headers=h).json
    vmid=review['machines'][0]['vmid']
    job_id=client.post('/api/simple/run',json=dict(plan_id=review['id'],confirmed=True,risk_confirmed=True),
                       headers=h).json['jobs'][0]['id']
    current={'name':'renamed','vmgenid':'4c8d90db-16f5-4736-ae24-87f7943bad3d','smbios1':'uuid=created','cores':4,'memory':8192,'ostype':'win10'}
    app.manager.put('builds',dict(id=job_id,node='lab',vmid=vmid,machine_identity=machine_identity(current)))
    target=dict(node='migrated-node',vmid=vmid)
    host=ConfigReader(target,current)
    monkeypatch.setattr(operations,'Proxmox',lambda:host)
    def inspect():
        result=client.post(ROUTE,json=target,headers=h)
        assert result.status_code==200,result.json
        assert draft['password'] not in result.text and 'Original recovery #456!' not in result.text
        return result.json

    source=inspect()
    assert source['basis']=='guided' and source['name']=='renamed' and source['base_job_id']==job_id
    assert source['os']=='windows' and source['source']==dict(kind='iso',hint='server.iso',edition=review['edition'])
    assert source['settings']['cores']==review['machines'][0]['cores']
    assert source['tasks'][0]['params']=={'domain_name':'lab.example.test'}
    assert source['tasks'][0]['input_types']['dsrm_password']['type']=='password'

    current['smbios1']='uuid=replacement'
    fallback=inspect()
    assert fallback==dict(name='renamed',basis='hardware',os='windows',source=None,settings=dict(cores=4,memory=8192),tasks=[])
    current['smbios1']='uuid=created'
    with app.db() as con:
        row=con.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
        payload=app.decode_payload(row['payload'])
        con.execute('UPDATE jobs SET payload=? WHERE id=?',(app.encode_payload(dict(payload,server_id='old-server')),job_id))
    assert inspect()==fallback
    with app.db() as con:
        payload['plan'].pop('recipe')
        con.execute('UPDATE jobs SET payload=? WHERE id=?',(app.encode_payload(payload),job_id))
    assert inspect()==fallback
    del current['smbios1'],current['vmgenid']
    app.manager.put('builds',dict(id=job_id,node=target['node'],vmid=vmid,machine_identity=machine_identity(current)))
    assert inspect()==fallback and len(client.get('/api/jobs',headers=h).json)==1


def test_machine_source_rejects_stale_selection_extra_inputs_and_containers(service, monkeypatch):
    app,client=service
    h={'Authorization':'Bearer '+app.API_TOKEN}
    target={'node':'pve','vmid':202}
    host=ConfigReader(target,{},guest_type='lxc')
    monkeypatch.setattr(operations,'Proxmox',lambda:host)
    assert client.post(ROUTE,json=target).status_code==401
    assert client.post(ROUTE,json=target,headers={**h,'X-Proxmate-Server':'previous'}).status_code==409
    for data in [dict(target,job_id='override'),dict(node='pve',vmid='invalid'),[]]:
        assert client.post(ROUTE,json=data,headers=h).status_code==400
    assert not host.reads
    assert client.post(ROUTE,json=target,headers=h).status_code==400
    assert host.reads==['inventory']
