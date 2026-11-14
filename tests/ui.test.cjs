const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');

async function fixture(){
  const dom=new JSDOM(fs.readFileSync('static/index.html','utf8'),{url:'http://localhost:8091',runScripts:'dangerously'});
  const w=dom.window,calls=[],errors=[];
  w.addEventListener('error',e=>errors.push(e.error));
  w.structuredClone=structuredClone;w.setInterval=()=>0;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'))};
  const resources=[{type:'qemu',vmid:201,node:'pve',name:'Test VM',status:'running',maxcpu:2,maxmem:4294967296}];
  const records={tasks:[{id:'install-chocolatey',name:'Install Chocolatey',platform:'windows',parameters:{},description:'Install Chocolatey'},{id:'linux-package',name:'Linux package',platform:'linux',parameters:{package:'Package name'}}],credentials:[],connections:[],groups:[],recipes:[],schedules:[]};
  const catalog={profiles:{'ubuntu-24.04':{label:'Ubuntu 24.04',user:'ubuntu'}},locales:{'en-GB':'English UK'},timezones:['GMT Standard Time']};
  w.fetch=async (url,opts={})=>{
    calls.push([url,opts.body?JSON.parse(opts.body):null]);let value={};
    if(url==='/api/session')value={csrf:'test-csrf'};
    else if(url==='/api/state')value={resources,nodes:[{node:'pve',status:'online'}],details:{pve:{storage:[{storage:'local-lvm',active:1,content:'images'},{storage:'local',active:1,content:'iso,backup'}],network:[{iface:'vmbr0',type:'bridge'}],isos:[],backups:[]}},local_isos:[],next_id:202};
    else if(url==='/api/jobs')value=[];
    else if(url==='/api/lifecycle/state')value={families:[],versions:[],builds:[],policies:[],reports:[]};
    else if(url==='/api/lifecycle/plan')value={id:'recovery-plan',operation:'backup-restore',node:'pve',vmid:202,name:'restore-test',archive:'local:backup/vzdump-qemu-201-test.vma.zst',boot_test:true,disconnected:true,notes:[],dangerous:false};
    else if(url==='/api/lifecycle/run')value={id:'recovery-job'};
    else if(url==='/api/catalog')value=catalog;
    else if(url==='/api/provision/plan')value={steps:['Prepare VM'],notes:[]};
    else if(url==='/api/manage/plan')value={id:'reviewed-plan',operation:'tasks',targets:[resources[0]],tasks:[{name:'Install Chocolatey',params:{}}],settings:{},notes:[],snapshot_before:false,dangerous:false};
    else if(url.startsWith('/api/manage/')){const kind=url.split('/').at(-1);value=records[kind]||{id:'job'};}
    return {ok:true,status:200,json:async()=>value};
  };
  for(const file of ['app.js','wizard.js','manage.js','lifecycle.js']){const s=w.document.createElement('script');s.textContent=fs.readFileSync('static/'+file,'utf8');w.document.head.append(s)}
  const settle=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve))};
  await settle();
  return {w,calls,errors,records,settle,close:()=>dom.window.close()};
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
