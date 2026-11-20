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
    assert.ok(form.elements.packages.disabled);form.elements.chocolatey.click();
    assert.equal(form.elements.packages.disabled,false);form.elements.packages.value='7zip';
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
    form.elements.chocolatey.click();form.elements.packages.value='7zip, git';form.querySelector('[name=feature]').checked=true;
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

test('browsing media and editing disks preserve the chosen edition and machine details',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,fetch=w.fetch;
  let lookups=0;
  w.fetch=async(url,opts)=>{
    const res=await fetch(url,opts);
    if(url==='/api/simple/editions'){
      lookups++;
      const result=await res.json();
      result.editions.push({index:8,name:'Standard Core',label:'Standard Core',desktop:false});
    }
    return res;
  };
  try{
    doc.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=doc.querySelector('#simple-form'),fields=form.elements;
    assert.equal(doc.activeElement.textContent,'1. Machine');
    fields.edition.value='8';fields.name.value='lab';fields.password.value='Example#12345';
    fields.count.value='3';fields.count.dispatchEvent(new w.Event('input'));
    assert.equal(doc.querySelector('#batch-names').textContent,'lab-01 … lab-03');
    doc.querySelector('#placement-details').open=true;doc.querySelector('#iso-details').open=true;
    fields.all_media.click();await f.settle();
    assert.equal(doc.activeElement,fields.all_media);
    assert.equal(doc.querySelector('#placement-details').open,true);assert.equal(doc.querySelector('#iso-details').open,true);
    assert.equal(fields.edition.value,'8');
    doc.querySelector('#disk-details').open=true;
    doc.querySelector('#add-data-disk').click();
    assert.equal(doc.activeElement,fields.disk_size_0);
    doc.querySelector('#simple-next').click();await f.settle();
    assert.equal(doc.activeElement.textContent,'2. Software');
    doc.querySelector('#simple-back').click();
    assert.equal(doc.activeElement.textContent,'1. Machine');
    assert.equal(fields.edition.value,'8');assert.equal(lookups,1);
    assert.equal(fields.name.value,'lab');assert.equal(fields.disk_label_0.value,'Data1');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('machine settings group related controls and surface a missing WinRM network',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,fetch=w.fetch;
  w.fetch=async(url,opts)=>{
    const response=await fetch(url,opts);
    if(url==='/api/simple/options')(await response.json()).destinations[0].management_cidr='';
    return response;
  };
  try{
    doc.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=doc.querySelector('#simple-form'),fields=form.elements;
    const groups=panel=>[...form.querySelectorAll(panel+' fieldset')].map(g=>
      [g.querySelector('legend').textContent,[...g.querySelectorAll('select,input')].map(el=>el.name)]);
    assert.deepEqual(groups('#placement-details'),[
      ['Destination',['node','storage','bridge']],['Hardware',['cores','memory_gb','disk']]
    ]);
    assert.deepEqual(groups('#installation-details'),[
      ['Setup media',['iso_storage','agent_iso']],['Windows guest',['management_cidr','locale','timezone']]
    ]);
    assert.equal(doc.querySelector('#placement-details').open,false);
    assert.equal(doc.querySelector('#installation-details').open,true);
    fields.memory_gb.value='8';fields.memory_gb.dispatchEvent(new w.Event('input'));
    assert.match(doc.querySelector('#placement-preview').textContent,/8 GiB RAM/);
    fields.management_cidr.value='192.0.2.0/24';fields.password.value='Example#12345';
    doc.querySelector('#simple-next').click();await f.settle();
    doc.querySelector('#simple-back').click();
    assert.equal(fields.management_cidr.value,'192.0.2.0/24');assert.equal(fields.memory_gb.value,'8');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('failed ISO inspection can be retried after a redraw without losing the form',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,fetch=w.fetch;
  let lookups=0;
  w.fetch=async(url,opts)=>{
    const res=await fetch(url,opts),result=await res.json();
    if(url==='/api/simple/options')result.destinations[0].isos[0].revision='v1';
    if(url==='/api/simple/editions'&&++lookups===1)result.editions=[];
    return res;
  };
  try{
    doc.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=doc.querySelector('#simple-form'),fields=form.elements,retry=doc.querySelector('#simple-retry-iso');
    fields.name.value='lab';fields.password.value='Example#12345';
    fields.all_media.click();await f.settle();
    assert.equal(lookups,1);assert.equal(retry.hidden,false);
    assert.match(doc.querySelector('#simple-error').textContent,/No Windows editions/);
    assert.equal(fields.edition.selectedOptions[0].text,'Editions unavailable');
    assert.equal(fields.edition.disabled,true);assert.equal(doc.querySelector('#simple-next').disabled,true);
    retry.click();await f.settle();
    assert.equal(lookups,2);assert.equal(retry.hidden,true);assert.equal(fields.edition.value,'7');
    assert.equal(fields.edition.disabled,false);assert.equal(doc.querySelector('#simple-next').disabled,false);
    assert.equal(fields.name.value,'lab');assert.equal(fields.password.value,'Example#12345');
    assert.equal(f.calls.some(([url])=>url==='/api/simple/plan'||url==='/api/simple/run'),false);
    assert.deepEqual(f.errors,[]);
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

test('refresh network details selects the current IPv4 when the saved address has changed',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,original=w.fetch;
  let address='192.0.2.92';
  try{
    w.fetch=async(url,opts)=>{
      if(url==='/api/simple/machine')return {ok:true,json:async()=>({node:'pve',vmid:201,name:'Windows',identity:'vm',platform:'windows',status:'running',
        addresses:[{host:address},{host:'fd00::1'}],jobs:[],logins:[],connection:{host:'192.0.2.92',platform:'windows',port:5986}})};
      if(url==='/api/simple/machine/backup')return {ok:true,json:async()=>({latest:null})};
      return original(url,opts);
    };
    doc.querySelector('.machine-link').click();await f.settle();
    address='192.0.2.60';doc.querySelector('#refresh-machine-network').click();await f.settle();
    doc.querySelector('#machine-connect').click();
    const select=doc.querySelector('#manage-form').elements.host;
    assert.equal(select.value,address);assert.deepEqual([...select.options].map(o=>o.value),[address,'fd00::1']);
    assert.deepEqual(f.errors,[]);
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

test('Windows template review preserves the name, clone mode, disks and software inputs',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    w.eval("state.resources.push({type:'qemu',template:1,node:'pve',vmid:9000,name:'Server 2025 base'})");
    f.records.tasks.push({id:'share',name:'Configure share',platform:'windows',kind:'ansible',body:'test',parameters:{share_name:'Share name'},defaults:{share_name:'Lab'}});
    w.document.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form');
    form.elements.name.value='files-01';form.elements.source_kind.value='template';
    form.elements.source_kind.dispatchEvent(new w.Event('change'));await f.settle();
    assert.equal(form.elements.edition,undefined);
    assert.equal(form.elements.template_id.value,'9000');assert.equal(form.elements.clone_mode.value,'full');
    form.elements.generalized.checked=true;form.elements.password.value='Example-Secret#123';
    w.document.querySelector('#add-data-disk').click();
    form.elements.disk_size_0.value='80';form.elements.disk_label_0.value='Data';form.elements.disk_letter_0.value='F';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    form.elements.manual_updates.checked=true;
    assert.equal(form.querySelector('[data-script-param]').value,'Lab');
    form.querySelector('[name=saved_script]').checked=true;
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const payload=f.calls.find(([u])=>u==='/api/simple/plan')[1];
    assert.equal(payload.source_kind,'template');assert.equal(Number(payload.template_id),9000);
    assert.equal(payload.name,'files-01');assert.equal(payload.manual_updates,true);
    assert.deepEqual(payload.data_disks,[{size:80,label:'Data',letter:'F'}]);
    assert.deepEqual(payload.tasks,[{id:'share',params:{share_name:'Lab'}}]);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Windows ISO picker categorizes candidates and keeps other media browsable',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const fetch=w.fetch;
    w.fetch=async(url,opts)=>{
      const res=await fetch(url,opts);if(url!=='/api/simple/options')return res;
      const d=await res.json();d.destinations[0].isos=[{id:'local:iso/win2022.iso',label:'win2022.iso'},
        {id:'local:iso/virtio-win-2025.iso',label:'virtio-win-2025.iso'},{id:'local:iso/sql2022.iso',label:'sql2022.iso'}];
      return {ok:true,json:async()=>d};
    };
    w.document.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form');
    assert.deepEqual([...form.elements.source.options].map(o=>o.text),['Windows Server 2022']);
    assert.match(w.document.querySelector('#simple-fields').textContent,/win2022\.iso/);
    form.elements.all_media.checked=true;form.elements.all_media.dispatchEvent(new w.Event('change'));await f.settle();
    assert.equal(form.elements.source.options.length,3);assert.deepEqual(f.errors,[]);
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
    else if(url==='/api/simple/options')value={iso_setup_letters:['D','E','F'],destinations:[{node:'pve',storage:'local-lvm',iso_storage:'local',bridge:'vmbr0',management_cidr:'192.0.2.0/24',agent_iso:'local:iso/virtio.iso',storage_choices:['local-lvm'],iso_storages:['local'],bridges:['vmbr0'],driver_isos:['local:iso/virtio.iso'],isos:[{id:'local:iso/server.iso',label:'Windows Server 2025.iso'}]}],local_isos:[],profiles:{'ubuntu-24.04':'Ubuntu 24.04 LTS'},features:[{id:'Web-Server',name:'Web server (IIS)'}]};
    else if(url==='/api/simple/editions')value={identity:'media-v1',editions:[{index:7,name:'Windows Server Standard',label:'Standard (Desktop Experience)',desktop:true,language:'en-US'}]};
    else if(url==='/api/simple/plan'){const d=JSON.parse(opts.body);value={id:'simple-plan',machines:Array.from({length:Number(d.count)},(_,i)=>({vmid:202+i,name:d.name+'-0'+(i+1),node:'pve',memory:4096,cores:2,disk:64,storage:'local-lvm',bridge:'vmbr0'})),edition:'Standard (Desktop Experience)',purpose:d.purpose,tasks:[{name:'Install Chocolatey',params:{}}],remote_access:'HTTPS WinRM',notes:[],dangerous:false};}
    else if(url==='/api/simple/run')value={jobs:[{id:'new-job'}]};
    else if(url==='/api/catalog')value=catalog;
    else if(url==='/api/provision/plan')value={steps:['Prepare VM'],notes:[]};
    else if(url==='/api/manage/plan'||url==='/api/simple/existing-plan')value={id:'reviewed-plan',operation:'tasks',targets:[resources[0]],tasks:[{name:'Install Chocolatey',params:{}}],settings:{},notes:[],snapshot_before:false,dangerous:false};
    else if(url.startsWith('/api/manage/')){const kind=url.split('/').at(-1);value=records[kind]||{id:'job'};}
    return {ok:true,status:200,json:async()=>value};
  };
  for(const file of ['notifications.js','app.js','wizard.js','script-editor.js','manage.js','lifecycle.js','server.js','windows.js','simple.js','machine.js']){const s=w.document.createElement('script');s.textContent=fs.readFileSync('static/'+file,'utf8');w.document.head.append(s)}
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

test('WinRM inspection shows guest commands and requires a separate enable action',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const machine={node:'pve',vmid:201,name:'Windows lab',platform:'windows',identity:'guest-a',status:'running',addresses:[],logins:[],jobs:[],connection:{}};
    const fetch=w.fetch;let enables=0;
    w.fetch=async(url,opts)=>{
      if(url==='/api/simple/machine')return {ok:true,json:async()=>machine};
      if(url==='/api/simple/machine/winrm/review')return {ok:true,json:async()=>({status:'no-listener',scope:'192.0.2.0/24',commands:'guest console instructions',plan_id:'frozen'})};
      if(url==='/api/simple/machine/winrm/run'){enables++;assert.deepEqual(JSON.parse(opts.body),{plan_id:'frozen',confirmed:true});return {ok:true,json:async()=>({id:'queued'})}}
      return fetch(url,opts);
    };
    w.document.querySelector('.machine-link').click();await f.settle();
    w.document.querySelector('#machine-winrm').click();await f.settle();
    const form=w.document.querySelector('#manage-form');
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.equal(enables,0);assert.match(form.textContent,/192\.0\.2\.0\/24/);
    assert.equal(form.querySelector('textarea').value,'guest console instructions');
    assert.equal(form.querySelector('#manage-submit').textContent,'Enable HTTPS WinRM');
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.equal(enables,1);assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('job toasts follow builds across views without opening a log or repeating history',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const jobs=(status)=>[{id:'new',action:'simple',status,created:Date.now()/1000,target:{name:'files-01'}}];
    const observe=rows=>w.eval('observeJobs('+JSON.stringify(rows)+',"selected-server")');
    w.eval("submittedBuilds([{id:'new',name:'files-01'}])");
    observe(jobs('queued'));observe(jobs('waiting'));observe(jobs('running'));
    w.document.querySelector('[data-view="scripts"]').click();await f.settle();
    observe(jobs('succeeded'));observe(jobs('succeeded'));
    const texts=[...w.document.querySelectorAll('.toast [role=status]')].map(e=>e.textContent);
    assert.deepEqual(texts,['Build · files-01 queued','Build · files-01 started','Build · files-01 finished']);
    assert.equal(w.document.querySelector('#job-log'),null);
    w.eval('observedJobs=null');observe(jobs('succeeded'));
    assert.equal(w.document.querySelectorAll('.toast').length,0);
    const other=[{id:'failed',action:'simple',status:'failed',created:Date.now()/1000,target:{name:'vm-fail'}}];
    observe(other);
    assert.match(w.document.querySelector('.toast [role=alert]').textContent,/vm-fail failed/);
    w.document.querySelector('.toast-close').click();
    assert.equal(w.document.querySelectorAll('.toast').length,0);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('software review reconnects to its ticket and preserves the draft after a failure',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const fetch=w.fetch;let submissions=0,reads=0;
    w.fetch=async(url,opts)=>{
      if(url==='/api/simple/plan'){
        submissions++;assert.equal(opts.headers.Prefer,'respond-async');
        return {ok:true,json:async()=>({review_id:'ticket',request_id:'ref01'})};
      }
      if(url==='/api/simple/reviews/ticket'){
        reads++;if(reads===1)throw TypeError('Load failed');
        return {ok:true,json:async()=>({state:'failed',error:'Driver media is unavailable',request_id:'ref01'})};
      }
      return fetch(url,opts);
    };
    w.document.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form');
    form.elements.password.value='Test-Login#123456';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    form.elements.chocolatey.click();form.elements.packages.value='7zip';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.match(w.document.querySelector('#simple-progress').textContent,/Reconnecting/);
    assert.ok(w.document.querySelector('#simple-next').disabled);
    await new Promise(resolve=>setTimeout(resolve,1600));await f.settle();
    assert.equal(submissions,1);assert.equal(reads,2);
    assert.equal(form.elements.packages.value,'7zip');assert.equal(w.document.querySelector('#simple-next').disabled,false);
    assert.match(w.document.querySelector('#simple-error').textContent,/Driver media is unavailable.*ref01/);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('fresh Windows choices survive Back and submit distinct apps and compatible settings',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const fetch=w.fetch;
    w.fetch=async(url,opts)=>{
      const r=await fetch(url,opts);if(url!=='/api/simple/options')return r;
      const data=await r.json();
      data.common_programs=[{id:'7zip',name:'7-Zip'},{id:'git',name:'Git'}];
      data.windows_settings=[{id:'explorer',name:'Show extensions',desktop:true},
        {id:'console-sign-in',name:'Sign in once',advanced:true,vm_only:true}];
      return {ok:true,json:async()=>data};
    };
    w.document.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form');
    const next=async()=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle()};
    form.elements.password.value='Example-Windows#123';
    await next();
    assert.equal(form.querySelectorAll('[name=windows_setting]:checked,[name=common_program]:checked').length,0);
    form.querySelector('[value=explorer]').checked=true;form.querySelector('[value="7zip"]').checked=true;
    form.elements.chocolatey.click();form.elements.packages.value='7ZIP, git';form.elements.rdp.checked=true;
    w.document.querySelector('#simple-back').click();await f.settle();
    await next();
    assert.ok(form.querySelector('[value=explorer]').checked && form.querySelector('[value="7zip"]').checked && form.elements.rdp.checked);
    w.document.querySelector('#simple-back').click();await f.settle();
    form.elements.purpose.value='template';form.elements.purpose.dispatchEvent(new w.Event('change'));await f.settle();
    await next();assert.equal(form.querySelector('[value=console-sign-in]'),null);
    await next();
    const plan=f.calls.find(([url])=>url==='/api/simple/plan')[1];
    assert.deepEqual(plan.windows_settings,['explorer']);
    assert.deepEqual(plan.tasks.map(t=>[t.id,t.params.package]),[['install-chocolatey',undefined],['chocolatey-package','7zip'],['chocolatey-package','git']]);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});


test('Chocolatey gates custom package entry and excludes disabled IDs from review',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    w.document.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form'),fields=form.elements;
    const next=async()=>{form.querySelector('#simple-next').click();await f.settle()};
    fields.password.value='Example-Windows#123';
    await next();form.querySelector('[data-section=apps]').click();
    const badge=()=>form.querySelector('[data-section=apps] .section-count');
    assert.ok(fields.packages.disabled);
    const catalog=form.querySelector('.package-help a');
    assert.equal(catalog.href,'https://community.chocolatey.org/packages');assert.equal(catalog.target,'_blank');
    fields.chocolatey.click();assert.equal(fields.packages.disabled,false);
    fields.packages.value='7zip';fields.packages.dispatchEvent(new w.Event('input',{bubbles:true}));
    assert.equal(badge().textContent,'2');
    fields.chocolatey.click();assert.ok(fields.packages.disabled);assert.ok(badge().hidden);
    form.querySelector('#simple-back').click();await next();
    assert.ok(fields.packages.disabled);assert.equal(fields.packages.value,'7zip');
    fields.chocolatey.click();assert.equal(fields.packages.value,'7zip');
    fields.chocolatey.click();await next();
    const plan=f.calls.find(([url])=>url==='/api/simple/plan')[1];
    assert.equal(plan.packages,'');assert.deepEqual(plan.tasks,[]);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('software categories keep choices and bulk controls respect edition and scope',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const original=w.fetch;
    w.fetch=async(url,options)=>{
      const r=await original(url,options),data=await r.json();
      if(url==='/api/simple/options')Object.assign(data,{
        windows_settings:[{id:'openssh',name:'OpenSSH'},{id:'explorer',name:'Explorer',desktop:true},{id:'disable-firewall',name:'Firewall',advanced:true}],
        common_programs:[{id:'git',name:'Git'},{id:'microsoft-windows-terminal',name:'Terminal'}]});
      if(url==='/api/simple/editions')Object.assign(data.editions[0],{desktop:false,build:'17763'});
      return {...r,json:async()=>data};
    };
    w.document.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form'),click=s=>form.querySelector(s).click();
    form.elements.password.value='Test-windows#1234';
    const next=async()=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle()};
    await next();
    click('#software-settings [data-select-group=all]');
    assert.ok(form.elements.rdp.checked && form.querySelector('[value=openssh]').checked);
    assert.ok(form.querySelector('[value=explorer]').disabled);
    assert.equal(form.querySelector('[value=disable-firewall]').checked,false);
    assert.equal(form.elements.manual_updates.checked,false);
    form.querySelector('.software-sensitive summary').click();
    click('.software-sensitive [data-select-group=all]');
    assert.ok(form.elements.manual_updates.checked && form.querySelector('[value=disable-firewall]').checked);
    click('.software-sensitive [data-select-group=none]');
    assert.equal(form.elements.manual_updates.checked,false);assert.equal(form.querySelector('[value=disable-firewall]').checked,false);
    assert.ok(form.elements.rdp.checked && form.querySelector('[value=openssh]').checked);
    click('.software-sensitive [data-select-group=all]');
    click('[data-section=apps]');assert.ok(form.querySelector('#software-settings').hidden);
    click('#software-apps [data-select-group=all]');
    assert.ok(form.querySelector('[value=git]').checked);
    assert.equal(form.querySelector('[value=microsoft-windows-terminal]').checked,false);
    click('[data-section=settings]');click('#software-settings [data-select-group=none]');
    assert.equal(form.querySelector('[value=openssh]').checked,false);
    assert.equal(form.querySelector('[data-section=apps] .section-count').textContent,'1');
    await next();const plan=f.calls.find(([u])=>u==='/api/simple/plan')[1];
    assert.deepEqual(plan.windows_settings,['disable-firewall']);assert.ok(plan.manual_updates);assert.equal(plan.tasks.at(-1).params.package,'git');
  }finally{f.close()}
});

test('editor checks drafts, keeps an invalid copy open, and saves a corrected copy',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const original=w.fetch;let saved=null;
    const example={id:'example-ps',example:true,name:'Starter',platform:'linux',kind:'powershell',body:'Write-Output ok',parameters:{}};
    f.records.tasks.push(example);
    w.fetch=async(u,o)=>{
      if(u==='/api/manage/tasks/check')return {ok:true,json:async()=>JSON.parse(o.body).body==='if ('?
        {valid:false,line:1,column:4,message:'Unexpected PowerShell syntax'}:{valid:true}};
      if(u==='/api/manage/tasks'&&o?.body)saved=JSON.parse(o.body);
      return original(u,o);
    };
    w.document.querySelector('[data-view=scripts]').click();await f.settle();
    w.document.querySelector('[data-record=example-ps][data-mg=edit-tasks]').click();
    const form=w.document.querySelector('#manage-form');
    assert.equal(form.elements.kind.value,'powershell');
    assert.equal(form.elements.body.value,example.body);
    assert.equal(w.document.querySelector('#manage-submit').textContent,'Save copy');
    form.elements.body.value='if (';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.equal(saved,null);assert.ok(w.document.querySelector('#manage-dialog').open);
    assert.match(form.querySelector('[data-check-result]').textContent,/Line 1, column 4/);
    form.elements.body.value='Get-Date';
    form.elements.body.dispatchEvent(new w.Event('input',{bubbles:true}));
    form.querySelector('[data-check]').click();await f.settle();assert.equal(form.querySelector('[data-check-result]').textContent,'Syntax OK');
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.equal(saved.id,undefined);assert.equal(saved.body,'Get-Date');assert.equal(example.body,'Write-Output ok');
  }finally{f.close()}
});


test('ISO disk letters exclude setup DVDs and letters selected by another disk',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    w.eval("state.resources.push({type:'qemu',template:1,node:'pve',vmid:9000,name:'Server template'})");
    w.document.querySelector('[data-simple="windows"]').click();await f.settle();
    const form=w.document.querySelector('#simple-form');
    const field=i=>form.elements['disk_letter_'+i];
    const option=(i,value)=>[...field(i).options].find(o=>o.value===value);
    const set=(el,value)=>{el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}))};
    form.querySelector('#add-data-disk').click();form.querySelector('#add-data-disk').click();
    assert.deepEqual([...field(0).options].filter(o=>o.disabled).map(o=>o.value),['D','E','F']);
    assert.equal(field(0).value,'');set(field(0),'G');
    assert.ok(option(1,'G').disabled && !option(0,'G').disabled && !option(1,'').disabled);
    set(field(0),'');assert.equal(option(1,'G').disabled,false);
    set(form.elements.source_kind,'template');await f.settle();
    assert.equal(option(1,'E').disabled,false);
    set(field(1),'E');set(form.elements.source_kind,'iso');await f.settle();
    assert.ok(option(1,'E').disabled && !field(1).checkValidity());
    assert.match(field(1).validationMessage,/setup DVDs/);
    set(field(1),'');assert.ok(field(1).checkValidity());
  }finally{f.close()}
});
