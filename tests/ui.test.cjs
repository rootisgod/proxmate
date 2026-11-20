const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');

test('existing VMs use the same software choices with an inline saved login',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    w.document.querySelector('[data-simple="apply"]').click();await f.settle();
    const form=w.document.querySelector('#manage-form');
    form.querySelector('[name=target]').checked=true;
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    form.elements.packages.value='7zip';
    form.elements.guest_username.value='Administrator';form.elements.guest_password.value='Test-Login#123';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const payload=f.calls.find(c=>c[0]==='/api/simple/existing-plan')[1];
    assert.deepEqual(payload.targets,[{node:'pve',vmid:201}]);
    assert.deepEqual(payload.tasks.map(t=>t.id),['install-chocolatey','chocolatey-package']);
    assert.equal(payload.credential.username,'Administrator');
    assert.match(w.document.querySelector('#manage-title').textContent,/Review/);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('machine inventory is the landing screen with advanced tools collapsed',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    assert.match(w.document.querySelector('h1').textContent,/^Machines$/);
    assert.ok(w.document.querySelector('[data-simple="windows"]'));
    assert.ok(w.document.querySelector('[data-simple="ubuntu"]'));
    assert.equal(w.document.querySelector('.advanced-nav').open,false);
    assert.ok(f.calls.some(([url])=>url==='/api/simple/resources'));
    assert.equal(f.calls.some(([url])=>url==='/api/state'),false);
    assert.ok(f.calls.some(([url])=>url==='/api/jobs?summary=1'));
    w.document.querySelector('[data-view="backups"]').click();await f.settle();
    assert.equal(f.calls.filter(([url])=>url==='/api/state').length,1);
    assert.equal(w.document.querySelector('h1').textContent,'Backups');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('job logs load on open and update only when the open job changes',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    Object.defineProperty(w.document,'hidden',{value:false});
    const job={id:'test-job',action:'backup',target:{vmid:201},updated:1,created:1,status:'running'};
    let reads=0;
    const fetch=w.fetch;
    w.fetch=async(url,opts)=>{
      if(url==='/api/jobs?summary=1')return {ok:true,json:async()=>[{...job}]};
      if(url==='/api/jobs/test-job'){reads++;return {ok:true,json:async()=>({...job,log:'progress '+job.updated})}}
      return fetch(url,opts);
    };
    await w.eval('pollDashboard()');
    assert.equal(reads,0);
    w.document.querySelector('[data-log]').click();await f.settle();
    assert.equal(w.document.querySelector('#job-log').textContent,'progress 1');
    await w.eval('pollDashboard()');
    assert.equal(reads,1);
    job.updated++;job.status='succeeded';
    await w.eval('pollDashboard()');
    assert.equal(w.document.querySelector('#job-log').textContent,'progress 2');
    assert.equal(reads,2);
    assert.equal(f.calls.some(([url])=>url==='/api/state'),false);
  }finally{f.close()}
});

test('inventory keeps filters and runs software on exactly the checked machines',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    f.records.connections.push({node:'pve',vmid:201,platform:'linux'});
    w.document.querySelector('[data-machine-key]').click();
    const search=w.document.querySelector('#machine-search');
    search.value='201';search.dispatchEvent(new w.Event('input'));
    w.document.querySelector('[data-machine-status="stopped"]').click();
    assert.match(w.document.querySelector('#machine-rows').textContent,/No machines match/);
    w.document.querySelector('[data-machine-status="all"]').click();
    assert.equal(w.document.querySelector('[data-machine-key]').checked,true);
    w.document.querySelector('#run-selected').click();await f.settle();
    const form=w.document.querySelector('#manage-form');
    assert.equal(form.querySelector('select[name=os]').value,'ubuntu');
    assert.equal(form.elements.guest_password,undefined);
    form.elements.packages.value='curl';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const plan=f.calls.find(([url])=>url==='/api/simple/existing-plan')[1];
    assert.deepEqual(plan.targets,[{node:'pve',vmid:201}]);
    assert.deepEqual(plan.tasks,[{id:'linux-package',params:{package:'curl'}}]);
    assert.equal(f.calls.some(([url,data])=>url==='/api/simple/software'&&data),false);
  }finally{f.close()}
});

test('unknown OS asks for a choice and build prerequisites lead to server settings',async()=>{
  const f=await fixture({ssh_fingerprint:''},true),{w}=f;
  try{
    const fetch=w.fetch;
    w.fetch=(url,opts)=>url==='/api/simple/software'?
      Promise.resolve({ok:true,json:async()=>({platforms:{'pve:201':null},features:[]})}):fetch(url,opts);
    w.document.querySelector('[data-simple=apply][data-id]').click();await f.settle();
    const form=w.document.querySelector('#manage-form');
    assert.equal(form.elements.os.value,'');
    assert.equal(form.elements.packages,undefined);
    form.elements.os.value='ubuntu';form.elements.os.dispatchEvent(new w.Event('change'));
    assert.equal(form.elements.guest_username.value,'ubuntu');
    w.document.querySelector('#manage-close').click();
    w.document.querySelector('[data-simple=ubuntu]').click();await f.settle();
    assert.equal(w.document.querySelector('.server-advanced').open,true);
    assert.equal(w.document.activeElement.name,'ssh_fingerprint');
    assert.equal(f.calls.some(([url])=>url==='/api/simple/options'),false);
  }finally{f.close()}
});

test('inventory polling discovers external changes while preserving search input',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    Object.defineProperty(w.document,'hidden',{value:false});
    const fetch=w.fetch;
    w.fetch=(url,opts)=>url==='/api/simple/resources'?Promise.resolve({ok:true,json:async()=>[
      {type:'qemu',vmid:205,node:'pve',name:'external-vm',status:'running'}
    ]}):fetch(url,opts);
    const search=w.document.querySelector('#machine-search');
    search.focus();search.value='external';search.dispatchEvent(new w.Event('input'));
    await w.eval('lastInventoryRead=0;pollDashboard()');
    assert.equal(w.document.activeElement,search);
    assert.match(w.document.querySelector('#machine-rows').textContent,/external-vm/);
    assert.equal(w.document.querySelector('[data-machine-key]').dataset.machineKey,'pve:205');
  }finally{f.close()}
});

test('one simple wizard selects a named Windows edition, three machines and software',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    w.document.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form');
    assert.equal(w.document.querySelector('#simple-dialog').open,true);
    assert.equal(form.elements.edition.value,'7');
    assert.match(form.elements.edition.textContent,/Desktop Experience/);
    assert.equal(form.elements.image_index,undefined);
    form.elements.count.value='3';form.elements.name.value='lab';form.elements.password.value='Password-Test#123';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    form.elements.packages.value='7zip, git';form.querySelector('[name=feature]').checked=true;
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const plan=f.calls.find(c=>c[0]==='/api/simple/plan')[1];
    assert.equal(plan.count,'3');assert.equal(plan.edition,'7');
    assert.deepEqual(plan.tasks.map(t=>t.id),['install-chocolatey','chocolatey-package','chocolatey-package','windows-feature']);
    assert.match(w.document.querySelector('#simple-fields').textContent,/lab-03/);
    form.elements.confirmed.checked=true;
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const run=f.calls.find(c=>c[0]==='/api/simple/run');
    assert.deepEqual(run[1],{plan_id:'simple-plan',confirmed:true,risk_confirmed:false});
    assert.equal(run[2]['X-Proxmate-Server'],'selected-server');
    assert.equal(w.document.querySelector('#simple-fields').children.length,0);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('edition inspection preserves edits and ignores obsolete responses',async()=>{
  const f=await fixture({},true),{w}=f;
  const fetch=w.fetch,pending=[];
  w.fetch=async(url,opts)=>{
    if(url==='/api/simple/editions')return new Promise(resolve=>pending.push(resolve));
    const response=await fetch(url,opts);
    if(url==='/api/simple/options'){
      const options=await response.json();
      options.local_isos.push({id:'library:install.iso',label:'install.iso (ISO library)'});
    }
    return response;
  };
  try{
    w.document.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form'),next=w.document.querySelector('#simple-next');
    form.elements.source.value='library:install.iso';
    form.elements.source.dispatchEvent(new w.Event('change'));await f.settle();
    assert.equal(pending.length,2);
    form.elements.name.value='custom';
    form.elements.password.value='Edited#12345';
    pending[1]({ok:true,json:async()=>({editions:[{index:2,label:'Custom edition'}]})});
    await f.settle();
    pending[0]({ok:false,json:async()=>({error:'Previous ISO is unreadable'})});
    await f.settle();
    assert.equal(form.elements.edition.value,'2');
    assert.equal(form.elements.name.value,'custom');
    assert.equal(form.elements.password.value,'Edited#12345');
    assert.equal(w.document.querySelector('#simple-error').textContent,'');
    assert.equal(next.disabled,false);
  }finally{f.close()}
});

test('Ubuntu simple setup has no ISO index, public-key or snippets-storage step',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    w.document.querySelector('[data-simple="ubuntu"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form');
    assert.equal(form.elements.profile.value,'ubuntu-24.04');
    assert.equal(form.elements.ssh_key,undefined);assert.equal(form.elements.snippet_storage,undefined);
    assert.equal(form.elements.source,undefined);
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    form.elements.packages.value='curl';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.deepEqual(f.calls.find(c=>c[0]==='/api/simple/plan')[1].tasks,[{id:'linux-package',params:{package:'curl'}}]);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('all provisioning wizards open over LAN HTTP without crypto.randomUUID',async()=>{
  const f=await fixture(),{w}=f;
  try{
    Object.defineProperty(w.crypto,'randomUUID',{value:undefined,configurable:true});
    const keys=new Set();
    for(const kind of ['linux-cloud','windows','linux-iso']){
      await w.eval(`openWizard('${kind}')`);await f.settle();
      assert.equal(w.document.querySelector('#wizard').open,true,kind+' wizard opens');
      const key=w.eval('wizard.key');
      assert.match(key,/^[a-f0-9]{32}$/);
      assert.equal(keys.has(key),false,'Each new draft gets a separate request key');
      keys.add(key);
      w.document.querySelector('#wizard-close').click();
    }
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('responses from the previous server cannot repopulate UI caches after replacement',async()=>{
  const f=await fixture(),{w}=f;
  try{
    let finish;
    w.fetch=()=>new Promise(resolve=>{finish=resolve});
    const pending=w.eval("api('manage/groups')");
    w.eval("serverInfo={...serverInfo,id:'replacement-server'}");
    finish({ok:true,status:200,json:async()=>[{id:'obsolete-group'}]});
    await assert.rejects(pending,/Server changed/);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('server settings test drafts without persisting or exposing saved passwords',async()=>{
  const f=await fixture(),{w}=f;
  try{
    w.document.querySelector('[data-view="server"]').click();await f.settle();
    const form=w.document.querySelector('#server-form');
    assert.equal(form.elements.password.value,'');
    assert.equal(form.elements.url.value,f.server.url);
    form.elements.password.value='new-draft-secret';
    w.document.querySelector('#server-test').click();await f.settle();
    assert.match(w.document.querySelector('#server-result').textContent,/Connection successful/);
    const call=f.calls.find(c=>c[0]==='/api/server/test');
    assert.equal(call[1].expected_id,'selected-server');
    assert.equal(call[1].password,'new-draft-secret');
    assert.equal(call[1].verify_tls,true);
    assert.equal(f.calls.filter(c=>c[0]==='/api/server/connect').length,0);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('fresh installations show server setup without fetching inventory',async()=>{
  const f=await fixture({id:null,configured:false,connected:false,has_password:false,url:''});
  try{
    assert.ok(f.w.document.querySelector('#server-form'));
    assert.equal(f.calls.filter(c=>c[0]==='/api/state').length,0);
    assert.equal(f.w.document.querySelector('[name=password]').required,true);
    assert.equal(f.w.document.querySelector('#server-disconnect'),null);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('disconnect clears old inventory and leaves scripts and credentials accessible',async()=>{
  const f=await fixture(),{w}=f;
  try{
    w.document.querySelector('[data-view="server"]').click();await f.settle();
    w.document.querySelector('#server-disconnect').click();await f.settle();
    assert.match(w.document.querySelector('#connection-label').textContent,/Disconnected/);
    assert.equal(w.document.querySelector('#server-disconnect'),null);
    w.document.querySelector('[data-view="machines"]').click();await f.settle();
    assert.ok(w.document.querySelector('#server-form'));
    assert.equal(w.document.querySelector('[data-action="start"]'),null);
    w.document.querySelector('[data-view="credentials"]').click();await f.settle();
    assert.match(w.document.querySelector('h1').textContent,/credentials/i);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('refresh after another tab replaces the server closes obsolete operation forms',async()=>{
  const f=await fixture(),{w}=f;
  try{
    w.document.querySelector('[data-action="start"]').click();await f.settle();
    assert.equal(w.document.querySelector('#editor').open,true);
    f.server.id='replacement-server';f.server.url='https://192.0.2.11:8006';
    w.document.querySelector('#editor').close();
    // Simulate an open draft while an explicit refresh discovers the replacement.
    w.document.querySelector('#editor').showModal();
    w.document.querySelector('#refresh').click();await f.settle();
    assert.equal(w.document.querySelector('#editor').open,false);
    assert.equal(w.document.querySelector('#connection-label').textContent,f.server.url);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('machine page keeps the selected VM through discovery, login and return to inventory',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const machine={node:'pve',vmid:201,name:'Test VM',identity:'guest-a',platform:'linux',status:'running',
      addresses:[{host:'192.0.2.21',interface:'eth0'},{host:'192.0.2.22',interface:'eth1'}],
      jobs:[],logins:[{id:'linux-login',name:'Lab login',platform:'linux'}],connection:{}};
    const fetch=w.fetch;let checked;
    w.fetch=async(url,opts)=>{
      if(url==='/api/simple/machine')return {ok:true,json:async()=>structuredClone(machine)};
      if(url==='/api/simple/machine/backup')return {ok:true,json:async()=>({latest:null})};
      if(url==='/api/simple/machine/connect'){
        checked=JSON.parse(opts.body);
        machine.verified_at=100;machine.connection={updated:100,host:checked.host,platform:'linux',port:22,credential_id:checked.credential_id};
        return {ok:true,json:async()=>({verified_at:100})};
      }
      return fetch(url,opts);
    };
    w.document.querySelector('.machine-link').click();await f.settle();
    assert.match(w.location.hash,/machine\/selected-server\/pve\/201/);
    assert.equal(w.document.querySelector('h1').textContent,'Test VM');
    assert.equal(new URL(w.document.querySelector('.console-link').href).searchParams.get('vmid'),'201');
    w.document.querySelector('#machine-connect').click();
    const form=w.document.querySelector('#manage-form');
    assert.equal(form.elements.host.value,'');
    form.elements.host.value='192.0.2.22';form.elements.credential_id.value='linux-login';
    form.elements.credential_id.dispatchEvent(new w.Event('change'));
    assert.ok(form.elements.password.disabled);
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.deepEqual(checked,{node:'pve',vmid:201,identity:'guest-a',connection_revision:null,platform:'linux',host:'192.0.2.22',credential_id:'linux-login'});
    assert.equal(w.document.querySelector('#machine-connect').textContent,'Retest login');
    assert.ok(w.document.querySelector('#content [data-simple="apply"]'));
    assert.equal(form.elements.password,undefined);
    w.document.querySelector('.machine-back').click();
    assert.equal(w.document.querySelector('h1').textContent,'Machines');
    assert.equal(w.location.hash,'');assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('a late machine detail response cannot reopen a closed machine page',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const fetch=w.fetch;let resolve;
    w.fetch=(url,opts)=>url==='/api/simple/machine'?new Promise(done=>resolve=done):fetch(url,opts);
    w.document.querySelector('.machine-link').click();
    w.document.querySelector('.machine-back').click();
    resolve({ok:true,json:async()=>({name:'Obsolete guest'})});await f.settle();
    assert.equal(w.document.querySelector('h1').textContent,'Machines');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

async function fixture(initialServer={},startAtHome=false){
  const dom=new JSDOM(fs.readFileSync('static/index.html','utf8'),{url:'http://192.0.2.2:8091',runScripts:'dangerously'});
  const w=dom.window,calls=[],errors=[];
  w.addEventListener('error',e=>errors.push(e.error));
  w.structuredClone=structuredClone;w.setInterval=()=>0;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'))};
  const server={id:'selected-server',configured:true,connected:true,url:'https://192.0.2.10:8006',username:'root@pam',ssh_fingerprint:'test-fingerprint',has_password:true,verify_tls:true,...initialServer};
  const resources=[{type:'qemu',vmid:201,node:'pve',name:'Test VM',status:'running',maxcpu:2,maxmem:4294967296}];
  const records={tasks:[{id:'install-chocolatey',name:'Install Chocolatey',platform:'windows',parameters:{},description:'Install Chocolatey'},{id:'linux-package',name:'Linux package',platform:'linux',parameters:{package:'Package name'}}],credentials:[],connections:[],groups:[],recipes:[],schedules:[]};
  const catalog={profiles:{'ubuntu-24.04':{label:'Ubuntu 24.04',user:'ubuntu'}},locales:{'en-GB':'English UK'},timezones:['GMT Standard Time']};
  w.fetch=async (url,opts={})=>{
    calls.push([url,opts.body?JSON.parse(opts.body):null,opts.headers]);let value={};
    if(url==='/api/server')value={...server};
    else if(url==='/api/server/disconnect'){server.connected=false;value={...server};}
    else if(url==='/api/server/test')value={ok:true,version:'9',nodes:['pve']};
    else if(url==='/api/session')value={csrf:'test-csrf'};
    else if(url==='/api/state')value={resources,nodes:[{node:'pve',status:'online'}],details:{pve:{storage:[{storage:'local-lvm',active:1,content:'images'},{storage:'local',active:1,content:'iso,backup'}],network:[{iface:'vmbr0',type:'bridge'}],isos:[],backups:[]}},local_isos:[],next_id:202};
    else if(url==='/api/jobs?summary=1')value=[];
    else if(url==='/api/simple/resources')value=[...resources,{type:'node',node:'pve',status:'online'}];
    else if(url==='/api/lifecycle/state')value={families:[],versions:[],builds:[],policies:[],reports:[]};
    else if(url==='/api/lifecycle/plan')value={id:'recovery-plan',operation:'backup-restore',node:'pve',vmid:202,name:'restore-test',archive:'local:backup/vzdump-qemu-201-test.vma.zst',boot_test:true,disconnected:true,notes:[],dangerous:false};
    else if(url==='/api/lifecycle/run')value={id:'recovery-job'};
    else if(url.startsWith('/api/simple/software'))value={platforms:{'pve:201':'windows'},features:[{id:'Web-Server',name:'Web server (IIS)'}]};
    else if(url==='/api/simple/options')value={destinations:[{node:'pve',storage:'local-lvm',iso_storage:'local',bridge:'vmbr0',management_cidr:'192.0.2.0/24',agent_iso:'local:iso/virtio.iso',storage_choices:['local-lvm'],iso_storages:['local'],bridges:['vmbr0'],driver_isos:['local:iso/virtio.iso'],isos:[{id:'local:iso/server.iso',label:'Windows Server 2025.iso'}]}],local_isos:[],profiles:{'ubuntu-24.04':'Ubuntu 24.04 LTS'},features:[{id:'Web-Server',name:'Web server (IIS)'}]};
    else if(url==='/api/simple/editions')value={identity:'media-v1',editions:[{index:7,name:'Windows Server Standard',label:'Standard (Desktop Experience)',desktop:true,language:'en-US'}]};
    else if(url==='/api/simple/plan'){const d=JSON.parse(opts.body);value={id:'simple-plan',machines:Array.from({length:Number(d.count)},(_,i)=>({vmid:202+i,name:d.name+'-0'+(i+1),node:'pve',memory:4096,cores:2,disk:64,storage:'local-lvm',bridge:'vmbr0'})),edition:'Standard (Desktop Experience)',purpose:d.purpose,tasks:[{name:'Install Chocolatey',params:{}}],remote_access:'HTTPS WinRM',notes:[],dangerous:false};}
    else if(url==='/api/simple/run')value={jobs:[{id:'new-job'}]};
    else if(url==='/api/catalog')value=catalog;
    else if(url==='/api/provision/plan')value={steps:['Prepare VM'],notes:[]};
    else if(url==='/api/manage/plan'||url==='/api/simple/existing-plan')value={id:'reviewed-plan',operation:'tasks',targets:[resources[0]],tasks:[{name:'Install Chocolatey',params:{}}],settings:{},notes:[],snapshot_before:false,dangerous:false};
    else if(url.startsWith('/api/manage/')){const kind=url.split('/').at(-1);value=records[kind]||{id:'job'};}
    return {ok:true,status:200,json:async()=>value};
  };
  for(const file of ['app.js','wizard.js','manage.js','lifecycle.js','server.js','simple.js','machine.js']){const s=w.document.createElement('script');s.textContent=fs.readFileSync('static/'+file,'utf8');w.document.head.append(s)}
  const settle=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve))};
  await settle();
  if(server.connected&&!startAtHome){w.eval("view='overview';render()");await settle();}
  return {w,calls,errors,records,server,settle,close:()=>dom.window.close()};
}

test('management tabs render, secrets are blank on edit, and VM controls are available',async()=>{
  const f=await fixture(),{w}=f;
  try{
    assert.equal(f.errors.length,0);
    assert.ok(w.document.querySelector('[data-mg="connection"]'));
    w.document.querySelector('[data-view="credentials"]').click();await f.settle();
    assert.match(w.document.querySelector('h1').textContent,/credentials/i);
    w.document.querySelector('[data-mg="new-credentials"]').click();await f.settle();
    assert.ok(w.document.querySelector('#manage-dialog').open);
    assert.equal(w.document.querySelector('#manage-fields [name=password]').value,'');
    w.document.querySelector('#manage-close').click();
    assert.equal(w.document.querySelector('#manage-fields').children.length,0);
    for(const view of ['tasks','groups','recipes','schedules']){
      w.document.querySelector(`[data-view="${view}"]`).click();await f.settle();
      assert.ok(w.document.querySelector('#content h1'));
    }
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('task sequence survives parameters and review submits exact targets, then run uses plan ID',async()=>{
  const f=await fixture(),{w}=f;
  try{
    w.document.querySelector('[data-view="tasks"]').click();await f.settle();
    w.document.querySelector('[data-mg="use-task"][data-record="install-chocolatey"]').click();await f.settle();
    w.document.querySelector('[name=target]').checked=true;
    w.document.querySelector('#manage-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const plan=f.calls.find(c=>c[0]==='/api/manage/plan')[1];
    assert.deepEqual(plan.targets,[{node:'pve',vmid:201}]);
    assert.deepEqual(plan.tasks,[{id:'install-chocolatey',params:{}}]);
    assert.match(w.document.querySelector('#manage-title').textContent,/Review/);
    w.document.querySelector('[name=confirmed]').checked=true;
    w.document.querySelector('#manage-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.deepEqual(f.calls.find(c=>c[0]==='/api/manage/run')[1],{plan_id:'reviewed-plan',confirmed:true,risk_confirmed:false});
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('wizard loads, preserves back navigation, and recipe save excludes setup password',async()=>{
  const f=await fixture(),{w}=f;
  try{
    await w.eval("openWizard('linux-cloud')");
    assert.ok(w.document.querySelector('#wizard').open);
    w.document.querySelector('#wizard-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.ok(w.document.querySelector('#wizard-fields [name=ssh_key]'));
    w.document.querySelector('#wizard-back').click();
    assert.ok(w.document.querySelector('#wizard-fields [name=profile]'));
    w.eval("wizard.draft.kind='windows';wizard.draft.admin_password='secret-must-not-save';wizard.draft.image_index=1;wizard.draft.accept_eula=true;wizard.draft.wipe_disk=true");
    await w.eval('saveWizardRecipe()');
    w.document.querySelector('#manage-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const saved=f.calls.find(c=>c[0]==='/api/manage/recipes'&&c[1]);
    assert.ok(saved);
    assert.equal(saved[1].config.windows.admin_password,'');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('recovery and version views work; restore requires a reviewed plan',async()=>{
  const f=await fixture(),{w}=f;
  try{
    for(const view of ['versions','recovery']){
      w.document.querySelector(`[data-view="${view}"]`).click();await f.settle();
      assert.ok(w.document.querySelector('#content h1'));
    }
    w.eval("lcRestore({node:'pve',volid:'local:backup/vzdump-qemu-201-test.vma.zst'})");
    assert.equal(w.document.querySelector('[name=disconnected]').checked,true);
    assert.equal(w.document.querySelector('[name=boot_test]').checked,false);
    w.document.querySelector('[name=boot_test]').checked=true;
    w.document.querySelector('#manage-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const plan=f.calls.find(c=>c[0]==='/api/lifecycle/plan')[1];
    assert.equal(plan.archive,'local:backup/vzdump-qemu-201-test.vma.zst');
    assert.equal(plan.disconnected,true);assert.equal(plan.boot_test,true);
    w.document.querySelector('[name=confirmed]').checked=true;
    w.document.querySelector('#manage-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.equal(f.calls.find(c=>c[0]==='/api/lifecycle/run')[1].plan_id,'recovery-plan');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('bootstrap is explicit and recipes restore bootstrap settings',async()=>{
  const f=await fixture(),{w}=f;
  try{
    await w.eval("openWizard('linux-cloud')");
    w.eval("wizard.step=2;showWizardStep()");
    assert.equal(w.document.querySelector('[name=bootstrap_enabled]').checked,false);
    w.document.querySelector('[name=bootstrap_enabled]').checked=true;
    w.document.querySelector('[name=bootstrap_enabled]').dispatchEvent(new w.Event('change'));
    assert.ok(w.document.querySelector('[name=bootstrap_snippet_storage]'));
    w.document.querySelector('[name=bootstrap_verify]').checked=true;
    w.document.querySelector('[name=bootstrap_verify]').dispatchEvent(new w.Event('change'));
    assert.equal(w.document.querySelector('[name=start]').checked,true);
    assert.ok(w.document.querySelector('[name=bootstrap_credential_id]'));
    const draft={};w.hydrateBootstrapDraft(draft,{enabled:true,snippet_storage:'local',verify:false});
    assert.equal(draft.bootstrap_enabled,true);assert.equal(draft.bootstrap_snippet_storage,'local');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});
