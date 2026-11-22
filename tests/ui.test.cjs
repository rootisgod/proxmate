const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');

test('Help is above API documentation and its topic and workspace links resolve',()=>{
  const dashboard=new JSDOM(fs.readFileSync('static/index.html','utf8'));
  const help=new JSDOM(fs.readFileSync('static/help.html','utf8'),{url:'https://dashboard.test/help'});
  try{
    const shell=dashboard.window.document, guide=help.window.document;
    const link=shell.querySelector('.aside-bottom a[href="/help"]');
    assert.equal(link.nextElementSibling.getAttribute('href'),'/api/docs');
    assert.equal(link.target,'_blank');
    const topics=[...guide.querySelectorAll('.guide-index a')].map(a=>a.hash.slice(1));
    assert.deepEqual(topics,[...guide.querySelectorAll('article[id]')].map(el=>el.id));
    for(const a of guide.querySelectorAll('a[href^="/#"]')){
      assert.ok(shell.querySelector('#workspace-nav [data-view="'+a.hash.slice(1)+'"]'),a.href);
    }
  }finally{dashboard.window.close();help.window.close()}
});

test('sidebar stays distinct from the workspace and its navigation stays legible in both themes',()=>{
  const {window:w}=new JSDOM(fs.readFileSync('static/index.html','utf8'));
  const doc=w.document;
  function luminance(rgb){
    const channels=rgb.match(/^rgb\((\d+), (\d+), (\d+)\)$/);
    assert.ok(channels,'Expected an opaque sRGB colour, got '+rgb);
    const [r,g,b]=channels.slice(1).map(value=>{
      const v=Number(value)/255;
      return v<=0.04045 ? v/12.92 : ((v+0.055)/1.055)**2.4;
    });
    return 0.2126*r+0.7152*g+0.0722*b;
  }
  function check(first,second,minimum,label){
    const a=luminance(first), b=luminance(second);
    const ratio=(Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
    assert.ok(ratio>=minimum,doc.documentElement.dataset.theme+' '+label+' contrast '+ratio.toFixed(2));
  }
  function background(element){
    for(let node=element;node;node=node.parentElement){
      const colour=w.getComputedStyle(node).backgroundColor;
      if(colour!=='rgba(0, 0, 0, 0)' && colour!=='transparent') return colour;
    }
    throw new Error('No opaque background for '+element.tagName);
  }
  try{
    for(const link of doc.querySelectorAll('link[rel=stylesheet]')){
      const style=doc.createElement('style');
      style.textContent=fs.readFileSync('.'+link.getAttribute('href'),'utf8');
      link.replaceWith(style);
    }
    doc.querySelector('button[data-view=machines]').classList.add('active');
    for(const theme of ['light','dark']){
      doc.documentElement.dataset.theme=theme;
      const rail=doc.querySelector('.workspace-rail'), canvas=background(doc.querySelector('main'));
      check(background(rail),canvas,theme==='light'?7:2,'sidebar/workspace');
      for(const selector of ['.brand','.nav-section-label','button[data-view=jobs]','button.active','.advanced-nav summary','.advanced-nav button','.aside-bottom a','#logout']){
        const el=rail.querySelector(selector);
        check(w.getComputedStyle(el).color,background(el),4.5,selector);
      }
      const icon=rail.querySelector('button.active .bi');
      check(w.getComputedStyle(icon).color,background(icon),3,'current page icon');
    }
  }finally{w.close()}
});

test('appearance follows the device, remembers an override and synchronizes open tabs',()=>{
  const dom=new JSDOM(fs.readFileSync('static/index.html','utf8'),{url:'https://dashboard.test',runScripts:'outside-only'});
  const w=dom.window, root=w.document.documentElement, picker=w.document.querySelector('#appearance');
  const device=new w.EventTarget();
  device.matches=true;
  w.matchMedia=()=>device;
  const choose=mode=>{picker.value=mode;picker.dispatchEvent(new w.Event('change',{bubbles:true}))};
  try{
    w.eval(fs.readFileSync('static/theme.js','utf8'));
    assert.equal(root.dataset.theme,'dark');assert.equal(picker.value,'system');
    assert.equal(w.localStorage.length,0);
    choose('light');
    device.dispatchEvent(new w.Event('change'));
    assert.equal(root.dataset.theme,'light');assert.equal(w.localStorage.getItem('proxmate.appearance'),'light');
    w.dispatchEvent(new w.StorageEvent('storage',{key:'proxmate.appearance',newValue:'dark'}));
    assert.equal(root.dataset.theme,'dark');assert.equal(picker.value,'dark');
    choose('system');device.matches=false;device.dispatchEvent(new w.Event('change'));
    assert.equal(root.dataset.theme,'light');assert.equal(w.localStorage.length,0);
    device.matches=true;device.dispatchEvent(new w.Event('change'));
    assert.equal(root.dataset.theme,'dark');
  }finally{w.close()}
});

test('appearance resolves before the body exists and works with unavailable storage',async()=>{
  const script=fs.readFileSync('static/theme.js','utf8');
  const html='<head><script>'+script+';window.firstTheme=document.documentElement.dataset.theme;</script></head>'+
    '<body><label><span class="appearance-icon"></span><select id="appearance"><option>system</option><option>light</option><option>dark</option></select></label></body>';
  for(const blocked of [false,true]){
    const dom=new JSDOM(html,{url:'https://dashboard.test',runScripts:'dangerously',beforeParse(w){
      if(blocked)Object.defineProperty(w,'localStorage',{get(){throw new Error('unavailable')}});
      else w.localStorage.setItem('proxmate.appearance','dark');
    }});
    const w=dom.window, picker=w.document.querySelector('select');
    try{
      await new Promise(resolve=>w.document.addEventListener('DOMContentLoaded',resolve,{once:true}));
      assert.equal(w.firstTheme,blocked?'light':'dark');
      assert.equal(picker.value,blocked?'system':'dark');
      picker.value='dark';picker.dispatchEvent(new w.Event('change',{bubbles:true}));
      assert.equal(w.document.documentElement.dataset.theme,'dark');
    }finally{w.close()}
  }
});

test('the script editor mounts its gutter and language styles with the document nonce',async()=>{
  const {outputFiles}=await require('esbuild').build({
    entryPoints:['editor/code.mjs'],bundle:true,format:'iife',globalName:'codeEditor',write:false
  });
  const dom=new JSDOM('<link rel="stylesheet" nonce="page-specific"><div id="host"></div><textarea>Get-Date</textarea>',
    {runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window;
  try{
    w.eval(outputFiles[0].text);
    const editor=w.codeEditor.mount(w.document.querySelector('#host'),w.document.querySelector('textarea'),'powershell',()=>{});
    try{
      editor.language('ansible');
      const styles=[...w.document.head.querySelectorAll('style')];
      assert.ok(styles.length && styles.every(style=>style.nonce==='page-specific'));
      assert.ok(w.document.querySelector('.cm-lineNumbers'));
    }finally{editor.destroy()}
  }finally{w.close()}
});

test('existing VMs ask for a missing login and show only the selected script inputs',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    w.document.querySelector('[data-simple="apply"]').click();await f.settle();
    const form=w.document.querySelector('#manage-form');
    form.querySelector('[name=target]').checked=true;
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.equal(form.elements.packages,undefined);assert.equal(form.elements.chocolatey,undefined);
    form.elements.script.value='install-chocolatey';form.elements.script.dispatchEvent(new w.Event('change'));
    form.querySelector('[data-add-script]').click();
    form.elements.guest_username.value='Administrator';form.elements.guest_password.value='Test-Login#123';
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const payload=f.calls.find(c=>c[0]==='/api/simple/existing-plan')[1];
    assert.deepEqual(payload.targets,[{node:'pve',vmid:201}]);
    assert.deepEqual(payload.tasks,[{id:'install-chocolatey',params:{}}]);
    assert.equal(payload.credential.username,'Administrator');
    assert.match(w.document.querySelector('#manage-title').textContent,/Review/);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('machine inventory is the landing screen with advanced tools collapsed',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    assert.match(w.document.querySelector('h1').textContent,/^Inventory$/);
    w.document.querySelector('nav [data-view=create]').click();await f.settle();
    assert.equal(w.document.querySelector('h1').textContent,'Create machine');
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

test('advanced activity views filter jobs and preserve the full Progress history',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const rows=['backup','ansible','start'].map(action=>({id:action,action,status:'succeeded',created:1}));
    Object.defineProperty(w.document,'hidden',{value:false});
    const fetch=w.fetch;
    w.fetch=async(url,opts)=>url==='/api/jobs?summary=1'?{ok:true,json:async()=>rows}:fetch(url,opts);
    await w.eval('pollDashboard()');
    for(const [view,expected] of [['backups',['backup']],['ansible',['ansible']],['jobs',['backup','ansible','start']]]){
      w.document.querySelector('nav [data-view="'+view+'"]').click();
      await f.settle();
      assert.deepEqual([...w.document.querySelectorAll('#content [data-log]')].map(button=>button.dataset.log),expected);
    }
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
    assert.equal(form.querySelector('select[name=os]'),null);assert.equal(form.elements.packages,undefined);
    assert.equal(form.elements.guest_password,undefined);
    form.elements.script.value='linux-package';form.elements.script.dispatchEvent(new w.Event('change'));
    form.querySelector('[data-add-script]').click();
    form.querySelector('[data-run-param=package]').value='curl';
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
    form.elements.os.value='linux';form.elements.os.dispatchEvent(new w.Event('change'));
    assert.equal(form.elements.guest_username.value,'ubuntu');
    w.document.querySelector('#manage-close').click();
    await f.build('ubuntu');
    assert.equal(w.document.querySelector('.server-advanced').open,true);
    assert.equal(w.document.activeElement.name,'ssh_fingerprint');
    assert.equal(f.calls.some(([url])=>url==='/api/simple/options'),false);
  }finally{f.close()}
});

test('library shortcut preselects a script, offers its defaults, and sends only named overrides',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    f.records.tasks.push({id:'service-test',name:'Start service',platform:'windows',kind:'ansible',
      parameters:{service:'Service name'},defaults:{service:'Spooler'},body:'- name: Start service'});
    f.records.connections.push({node:'pve',vmid:201,platform:'windows'});
    doc.querySelector('[data-view=scripts]').click();await f.settle();
    doc.querySelector('[data-simple-task="service-test"]').click();await f.settle();
    const form=doc.querySelector('#manage-form');
    form.elements.target.click();form.requestSubmit();await f.settle();
    assert.equal(form.elements.script.value,'service-test');
    assert.equal(form.elements.credential_id,undefined);assert.equal(form.elements.packages,undefined);
    const input=form.querySelector('[data-run-param=service]');
    assert.equal(input.value,'Spooler');input.value='Bad name';
    assert.equal(input.validity.patternMismatch,true);
    form.requestSubmit();await f.settle();
    assert.equal(f.calls.some(([url])=>url==='/api/simple/existing-plan'),false);
    input.value='W32Time';
    form.requestSubmit();await f.settle();
    assert.deepEqual(f.calls.find(([url])=>url==='/api/simple/existing-plan')[1],
      {targets:[{node:'pve',vmid:201}],tasks:[{id:'service-test',params:{service:'W32Time'}}]});
    doc.querySelector('[data-edit-run]').click();await f.settle();
    assert.equal(form.querySelector('[data-run-param]').value,'W32Time');
  }finally{f.close()}
});

test('the built-in domain controller script prompts for a masked restore password',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    const id='windows-domain-controller';
    f.records.tasks.push({id,builtin:true,kind:'ansible',platform:'windows',name:'Set up a Windows domain controller',
      description:'First controller of a new forest.',body:'- name: Promote the server',
      parameters:{domain_name:'DNS name',dsrm_password:'Restore password'},
      input_types:{domain_name:{type:'dns_domain'},dsrm_password:{type:'password'}}});
    f.records.connections.push({node:'pve',vmid:201,platform:'windows'});
    doc.querySelector('[data-view=scripts]').click();await f.settle();
    const button=doc.querySelector('[data-simple-task="'+id+'"]');
    assert.equal(button.closest('article').querySelector('[data-mg="edit-tasks"]'),null);
    button.click();await f.settle();
    const form=doc.querySelector('#manage-form');
    form.elements.target.click();form.requestSubmit();await f.settle();
    const password=form.querySelector('[data-run-param=dsrm_password]');
    assert.equal(password.type,'password');assert.equal(password.autocomplete,'new-password');
    password.value='Literal !{{chars}} #123';
    form.querySelector('[data-run-param=domain_name]').value='ad.example.test';
    form.requestSubmit();await f.settle();
    assert.deepEqual(f.calls.find(([url])=>url==='/api/simple/existing-plan')[1].tasks,
      [{id,params:{domain_name:'ad.example.test',dsrm_password:'Literal !{{chars}} #123'}}]);
  }finally{f.close()}
});

test('script catalog filters builtins and saved source and opens a lab using the selected script',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    f.records.tasks.push({id:'folder',name:'Project directory',kind:'ansible',platform:'linux',
      parameters:{owner:'Folder owner'},defaults:{owner:'operator'},body:'- ansible.builtin.file: {path: /opt/team, state: directory}'});
    doc.querySelector('nav [data-view=scripts]').click();await f.settle();
    const search=doc.querySelector('[data-library-filter=query]'),platform=doc.querySelector('[data-library-filter=platform]');
    assert.ok(doc.querySelector('[data-library-id=install-chocolatey]'));
    search.value='owner';search.dispatchEvent(new w.Event('input',{bubbles:true}));
    const shown=()=>[...doc.querySelectorAll('[data-library-id]:not([hidden])')];
    assert.deepEqual(shown().map(e=>e.dataset.libraryId),['folder']);
    assert.match(shown()[0].textContent,/Default operator/);
    assert.match(shown()[0].querySelector('pre').textContent,/\/opt\/team/);
    platform.value='windows';platform.dispatchEvent(new w.Event('change',{bubbles:true}));
    assert.equal(shown().length,0);assert.equal(doc.querySelector('[data-library-empty]').hidden,false);
    let launch;w.openLabDesigner=(...args)=>{launch=args};
    platform.value='linux';platform.dispatchEvent(new w.Event('change',{bubbles:true}));
    shown()[0].querySelector('[data-script-to-lab]').click();assert.deepEqual(launch,[null,'folder']);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('standalone runs select exact targets and review every library script in order before queueing',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    f.records.tasks.push({id:'report',name:'Collect details',kind:'powershell',platform:'windows',body:'Write-Output draft'});
    f.records.connections.push({node:'pve',vmid:201,platform:'windows'});
    const original=w.fetch;let plan;
    w.fetch=async(url,options)=>{
      if(url==='/api/simple/existing-plan'){
        plan=JSON.parse(options.body);
        return {ok:true,json:async()=>({id:'run-plan',operation:'tasks',targets:[{node:'pve',vmid:201,name:'Test VM'}],
          tasks:[{name:'Collect details',params:{},source:'Write-Output reviewed'},{name:'Install Chocolatey',params:{}}],dangerous:true})};
      }
      return original(url,options);
    };
    doc.querySelector('nav [data-view=run-scripts]').click();await f.settle();
    assert.equal(doc.querySelector('h1').textContent,'Run scripts');
    const next=doc.querySelector('[data-choose-run-scripts]');
    assert.ok(next.disabled);doc.querySelector('[data-run-target]').click();assert.ok(!next.disabled);
    next.click();await f.settle();
    const form=doc.querySelector('#manage-form'),choose=form.elements.script;
    for(const id of ['install-chocolatey','report']){
      choose.value=id;choose.dispatchEvent(new w.Event('change'));form.querySelector('[data-add-script]').click();
    }
    form.querySelector('[data-task-action=up][data-index="1"]').click();
    assert.equal(doc.activeElement.dataset.index,'0');
    form.requestSubmit();await f.settle();
    assert.deepEqual(plan,{targets:[{node:'pve',vmid:201}],tasks:[{id:'report',params:{}},{id:'install-chocolatey',params:{}}]});
    assert.equal(form.querySelectorAll('.reviewed-scripts li').length,2);assert.match(form.textContent,/Write-Output reviewed/);
    assert.doesNotMatch(form.textContent,/Write-Output draft/);
    assert.ok(form.querySelector('[name=risk_confirmed]'));assert.ok(!f.calls.some(([url])=>url==='/api/manage/run'));
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('one-off script review retains source and parameters and never saves a library entry',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    f.records.connections.push({node:'pve',vmid:201,platform:'linux'});
    doc.querySelector('[data-simple=apply][data-id]').click();await f.settle();
    const form=doc.querySelector('#manage-form');
    doc.querySelector('[data-run-mode=once]').click();
    const source='- ansible.builtin.service:\n    name: "{{ proxmate_inputs.service }}"\n    state: started';
    form.elements.body.value=source;form.elements.body.dispatchEvent(new w.Event('input'));
    form.querySelector('[data-run-param=service]').value='cron';
    form.requestSubmit();await f.settle();
    const payload=f.calls.find(([url])=>url==='/api/simple/existing-plan')[1];
    assert.deepEqual(payload.one_off,{kind:'ansible',platform:'linux',body:source,parameters:{service:'service'},params:{service:'cron'}});
    assert.equal(payload.credential_id,undefined);
    assert.equal(f.calls.some(([url,body])=>url==='/api/manage/tasks'&&body),false);
    doc.querySelector('[data-edit-run]').click();await f.settle();
    assert.equal(form.elements.body.value,source);assert.equal(form.querySelector('[data-run-param]').value,'cron');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Operations reviews power for the checked machines without asking for a guest login',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    const fetch=w.fetch;
    w.fetch=async(url,opts)=>{
      const reply=await fetch(url,opts);
      if(url==='/api/manage/plan')return {ok:true,json:async()=>({id:'power-plan',operation:'shutdown',tasks:[],targets:[{node:'pve',vmid:201,name:'Test VM'}]})};
      return reply;
    };
    doc.querySelector('[data-machine-key]').click();
    doc.querySelector('[data-bulk-power=shutdown]').click();await f.settle();
    assert.deepEqual(f.calls.find(([url])=>url==='/api/manage/plan')[1],{operation:'shutdown',targets:[{node:'pve',vmid:201}]});
    assert.equal(f.calls.some(([url])=>url==='/api/manage/run'),false);
    assert.equal(doc.querySelector('#manage-form').elements.credential_id,undefined);
    doc.querySelector('#manage-form').requestSubmit();await f.settle();
    assert.deepEqual(f.calls.find(([url])=>url==='/api/manage/run')[1],{plan_id:'power-plan',confirmed:true,risk_confirmed:false});
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Create loads detailed storage only when opening a Linux template clone',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    const template={type:'qemu',template:1,node:'pve',vmid:9001,name:'linux-base',status:'stopped',maxcpu:2,maxmem:2147483648};
    w.eval('state.resources.push('+JSON.stringify(template)+')');
    const fetch=w.fetch;
    w.fetch=async(url,opts)=>{
      const reply=await fetch(url,opts);
      if(url==='/api/simple/software')return {ok:true,json:async()=>({platforms:{'pve:9001':'linux'}})};
      if(url==='/api/state'){const inventory=await reply.json();return {ok:true,json:async()=>({...inventory,resources:[...inventory.resources,template]})}}
      return reply;
    };
    doc.querySelector('nav [data-view=create]').click();
    assert.equal(f.calls.some(([url])=>url==='/api/state'),false);
    doc.querySelector('[data-clone-template="9001"]').click();await f.settle();
    assert.equal(doc.querySelector('#editor').open,true);
    assert.equal(doc.querySelector('#action-form [name=source]').value,'9001');
    assert.deepEqual(f.errors,[]);
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
    await f.build('windows');
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
      options.local_isos.push({id:'library:win_2022.iso',label:'win_2022.iso (ISO library)',windows_label:'Windows Server 2022'});
    }
    return response;
  };
  try{
    await f.build('windows');
    const form=w.document.querySelector('#simple-form'),next=w.document.querySelector('#simple-next');
    form.elements.source.value='library:win_2022.iso';
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
    await f.build('windows');
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
    assert.equal(doc.activeElement.textContent,'2. Software & scripts');
    doc.querySelector('#simple-back').click();
    assert.equal(doc.activeElement.textContent,'1. Machine');
    assert.equal(fields.edition.value,'8');assert.equal(lookups,1);
    assert.equal(fields.name.value,'lab');assert.equal(fields.disk_label_0.value,'Data1');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('guided field help explains Ubuntu setup media and is safe to open with keyboard or tap',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,fetch=w.fetch;
  w.fetch=async(url,opts)=>{
    const response=await fetch(url,opts);
    if(url==='/api/simple/options')(await response.json()).destinations[0].ubuntu_isos=[
      {id:'local:iso/ubuntu-24.04-live-server-amd64.iso',label:'Ubuntu Server ISO'}
    ];
    return response;
  };
  try{
    await f.build('ubuntu');
    const dialog=doc.querySelector('#simple-dialog');
    doc.querySelector('#installation-details').open=true;
    const storage=dialog.querySelector('[name=iso_storage]');
    const button=dialog.querySelector('[aria-label="Help for Setup media storage"]');
    const popup=dialog.querySelector('#guided-help-popup');
    assert.equal(storage.labels[0].textContent,'Setup media storage');
    assert.match(doc.getElementById(storage.getAttribute('aria-describedby')).textContent,/removes it after it verifies SSH/);
    button.dispatchEvent(new w.MouseEvent('pointerover',{bubbles:true}));
    assert.equal(popup.hidden,true);
    const hover=new w.MouseEvent('pointerover',{bubbles:true});
    Object.defineProperty(hover,'pointerType',{value:'mouse'});
    button.dispatchEvent(hover);
    assert.match(popup.textContent,/installer already on Proxmox stays where it is/);
    assert.equal(popup.hidden,false);
    button.focus();
    button.click();
    assert.equal(popup.hidden,false);
    button.click();
    assert.equal(popup.hidden,true);
    storage.focus();button.focus();
    assert.equal(popup.hidden,false);
    assert.equal(button.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true})),false);
    assert.equal(popup.hidden,true);
    assert.equal(dialog.open,true);
    assert.equal(f.calls.some(([url])=>url==='/api/simple/plan'),false);
    const sourceKind=dialog.querySelector('[name=source_kind]');
    sourceKind.value='cloud';
    sourceKind.dispatchEvent(new w.Event('change',{bubbles:true}));
    const cloudHelp=dialog.querySelector('[aria-label="Help for Setup media storage"]');
    cloudHelp.click();
    assert.match(popup.textContent,/generated cloud-init ISO/);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Ubuntu password sign-in is opt-in and clears when unchecked or switching to a template',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    await f.build('ubuntu');
    const form=doc.querySelector('#simple-form');
    assert.equal(form.elements.ubuntu_password_enabled.checked,false);
    assert.equal(form.elements.ubuntu_password,undefined);
    form.elements.ubuntu_password_enabled.click();
    assert.equal(form.elements.ubuntu_password.type,'password');
    assert.equal(form.elements.ubuntu_password.required,true);
    form.elements.ubuntu_password.value='Fixture password 123!';
    form.elements.ubuntu_password_enabled.click();
    form.elements.ubuntu_password_enabled.click();
    assert.equal(form.elements.ubuntu_password.value,'');
    form.elements.ubuntu_password.value='Fixture password 123!';
    form.elements.purpose.value='template';
    form.elements.purpose.dispatchEvent(new w.Event('change',{bubbles:true}));
    assert.equal(form.elements.ubuntu_password_enabled,undefined);
    form.elements.purpose.value='vm';
    form.elements.purpose.dispatchEvent(new w.Event('change',{bubbles:true}));
    assert.equal(form.elements.ubuntu_password_enabled.checked,false);
    assert.equal(form.elements.ubuntu_password,undefined);
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
    await f.build('windows');
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
    await f.build('windows');
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

for(const [os,filename] of [['debian','debian-13.7.0-amd64-DVD-1.iso'],['rocky','Rocky-10.3-x86_64-dvd1.iso']])
  test(os+' Create discovers its own ISO, offers SSH and reviews Linux packages',async()=>{
  const f=await fixture({},true),{w}=f,fetch=w.fetch;
  w.fetch=async(url,opts)=>{
    const response=await fetch(url,opts);
    if(url==='/api/simple/options'){
      const data=await response.json();
      data.destinations[0][os+'_isos']=[{id:'ssd:iso/'+filename,label:filename}];
      data[os]={packages:[{id:'jq',name:'jq'}]};
    }
    return response;
  };
  try{
    await f.build(os);
    const form=w.document.querySelector('#simple-form');
    assert.equal(form.elements.username.value,os);
    assert.equal(form.elements.source.value,'ssd:iso/'+filename);
    assert.deepEqual(Array.from(form.elements.source_kind.options,o=>o.value),['iso']);
    const next=async()=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle()};
    await next();
    form.querySelector('[name=common_program][value=jq]').click();
    await next();
    const plan=f.calls.find(c=>c[0]==='/api/simple/plan')[1];
    assert.equal(plan.os,os);
    assert.deepEqual(plan.tasks,[{id:'linux-package',params:{package:'jq'}}]);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Ubuntu ISO review sends the selected Minimized image and derives its release from media',async()=>{
  const f=await fixture({},true),{w}=f,fetch=w.fetch;
  const source='ssd:iso/ubuntu-26.04.1-live-server-amd64.iso';
  w.fetch=async(url,opts)=>{
    const response=await fetch(url,opts);
    if(url==='/api/simple/options'){
      const options=await response.json();
      options.destinations[0].ubuntu_isos=[{id:source,label:'Ubuntu Server 26.04.1'}];
      options.ubuntu={release:'Ubuntu Server LTS',install_sources:{'ubuntu-server':'Standard','ubuntu-server-minimal':'Minimized'},
        packages:[],firewall:{default:'Ubuntu default'},updates:{default:'Ubuntu default'}};
    }
    return response;
  };
  try{
    await f.build('ubuntu');
    const form=w.document.querySelector('#simple-form');
    assert.equal(form.elements.ubuntu_install_source.value,'ubuntu-server');
    form.elements.ubuntu_install_source.value='ubuntu-server-minimal';
    for(let step=0;step<2;step++){
      form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    }
    const plan=f.calls.find(c=>c[0]==='/api/simple/plan')[1];
    assert.equal(plan.source,source);
    assert.equal(plan.ubuntu_install_source,'ubuntu-server-minimal');
    assert.equal(plan.profile,undefined);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Ubuntu simple setup has no ISO index, public-key or snippets-storage step',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    await f.build('ubuntu');
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

test('dashboard backup is available before connecting and requires a fresh preview after a file change',async()=>{
  const f=await fixture({connected:false},true),{w}=f,doc=w.document;
  const original=w.fetch,calls=[];
  w.fetch=async(url,options)=>{
    if(!url.startsWith('/api/dashboard-backup/'))return original(url,options);
    calls.push([url,JSON.parse(options.body)]);
    return {ok:true,json:async()=>({mode:'portable',review:'sample',created:1700000000,destination:'Unconfigured dashboard',
      items:[{id:'0',kind:'script',name:'Report <host>',status:'new'},{id:'1',kind:'lab',name:'Existing',status:'connect a server'}],omitted:[]})};
  };
  try{
    doc.querySelector('nav [data-view="dashboard-backup"]').click();await f.settle();
    const download=doc.querySelector('#backup-download'),upload=doc.querySelector('#backup-upload');
    assert.equal(doc.querySelector('#workspace-location').textContent,'Backup & restore');
    download.elements.mode.value='portable';download.elements.mode.dispatchEvent(new w.Event('change'));
    assert.equal(download.elements.admin_password.disabled,true);
    Object.defineProperty(upload.elements.file,'files',{value:[new w.File(['test bytes'],'file.proxmate')]});
    upload.elements.passphrase.value='long enough passphrase';
    upload.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    assert.equal(calls[0][0],'/api/dashboard-backup/preview');
    assert.equal(calls[0][1].archive,Buffer.from('test bytes').toString('base64'));
    const choices=doc.querySelectorAll('#restore-review [name=selected]');
    assert.equal(choices[0].checked,true);assert.equal(choices[1].disabled,true);
    assert.equal(doc.querySelector('#restore-review host'),null);
    w.eval('render()');assert.equal(doc.querySelector('#backup-upload'),upload);
    upload.elements.passphrase.dispatchEvent(new w.Event('input',{bubbles:true}));
    assert.equal(doc.querySelector('#restore-review').childElementCount,0);
    assert.equal(calls.length,1);assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('server settings test drafts without persisting or exposing saved passwords',async()=>{
  const f=await fixture(),{w}=f;
  try{
    w.document.querySelector('[data-view="server"]').click();await f.settle();
    const form=w.document.querySelector('#server-form');
    assert.equal(form.elements.password.value,'');
    assert.equal(form.elements.url.value,f.server.url);
    const summary=w.document.querySelector('.server-summary');
    assert.match(summary.textContent,/SSH fingerprintConfigured/);
    form.elements.password.value='new-draft-secret';
    form.elements.url.value='https://new-pve.example:8006';
    w.eval('render()');
    assert.equal(w.document.querySelector('#server-form'),form);
    assert.ok(summary.textContent.includes(f.server.url));
    assert.ok(!summary.textContent.includes('new-pve'));
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
    const connection=w.document.querySelector('#connection-label');
    assert.equal(connection.textContent,new URL(f.server.url).host);
    assert.equal(connection.title,'Connected to '+f.server.url);
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
    assert.equal(w.document.querySelector('h1').textContent,'Inventory');
    assert.equal(w.location.hash,'#machines');assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('refresh network details selects the current IPv4 when the saved address has changed',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,original=w.fetch;
  let address='192.0.2.92';
  try{
    w.fetch=async(url,opts)=>{
      if(url==='/api/simple/machine')return {ok:true,json:async()=>({node:'pve',vmid:201,name:'Windows',identity:'vm',platform:'windows',status:'running',
        addresses:[{host:address},{host:'fd00::1'}],jobs:[],suggested_credential_id:'current',
        logins:[{id:'old',name:'Former VM',platform:'windows'},{id:'current',name:'Windows login',platform:'windows'}],
        connection:{host:'192.0.2.92',platform:'windows',port:5986,credential_id:'old'}})};
      if(url==='/api/simple/machine/backup')return {ok:true,json:async()=>({latest:null})};
      return original(url,opts);
    };
    doc.querySelector('.machine-link').click();await f.settle();
    address='192.0.2.60';doc.querySelector('#refresh-machine-network').click();await f.settle();
    assert.equal(doc.querySelector('#manage-dialog').open,true);
    assert.equal(doc.querySelector('#machine-connect').textContent,'Verify new address');
    const select=doc.querySelector('#manage-form').elements.host;
    assert.equal(select.value,address);assert.deepEqual([...select.options].map(o=>o.value),[address,'fd00::1']);
    assert.equal(doc.querySelector('#manage-form').elements.credential_id.value,'current');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

for(const source of ['inventory','failed job']){
  test('refresh connection from '+source+' discovers the VM and waits for Test and save',async()=>{
    const f=await fixture({},true),{w}=f,doc=w.document,original=w.fetch;
    const requests=[];
    const target={node:'pve',vmid:201};
    const data={...target,name:'Web server',identity:'vm',platform:'linux',status:'running',
      addresses:[{host:'192.0.2.60'},{host:'fd00::60'}],jobs:[],logins:[{id:'saved',name:'Lab login',platform:'linux'}],
      connection:{host:'192.0.2.17',platform:'linux',port:22,credential_id:'saved',updated:1}};
    try{
      w.fetch=async(url,opts)=>{
        if(url==='/api/jobs/failure')return {ok:true,json:async()=>({action:'manage',status:'failed',log:'job failed',
          connection_recovery:{server_id:f.server.id,targets:[{...target,reviewed_identity:'reviewed'}]}})};
        if(url.startsWith('/api/simple/machine')){
          requests.push([url,JSON.parse(opts.body)]);
          return {ok:true,json:async()=>url==='/api/simple/machine'?data:{}};
        }
        return original(url,opts);
      };
      if(source==='failed job'){
        w.eval("openJobLog('failure')");await f.settle();
        const button=doc.querySelector('.job-connection-actions button');
        assert.equal(button.textContent,'Refresh connection for pve:201');
        button.click();
      }else doc.querySelector('[data-refresh-connection="pve:201"]').click();
      await f.settle();
      assert.equal(doc.querySelector('#manage-dialog').open,true);
      assert.equal(doc.querySelector('#editor').open,false);
      assert.deepEqual(requests[0][1],source==='failed job'?{...target,reviewed_identity:'reviewed'}:target);
      const form=doc.querySelector('#manage-form');
      assert.equal(form.elements.host.value,'192.0.2.60');
      assert.equal(form.elements.credential_id.value,'saved');
      assert.match(doc.querySelector('#manage-fields').textContent,/192\.0\.2\.17/);
      assert.equal(requests.some(([url])=>url.endsWith('/connect')),false);
      form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
      assert.deepEqual(requests.find(([url])=>url.endsWith('/connect'))[1],
        {...target,identity:'vm',connection_revision:1,platform:'linux',host:'192.0.2.60',credential_id:'saved'});
      assert.equal(f.calls.some(([url])=>url==='/api/manage/run'),false);
      assert.deepEqual(f.errors,[]);
    }finally{f.close()}
  });
}

test('a late machine detail response cannot reopen a closed machine page',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const fetch=w.fetch;let resolve;
    w.fetch=(url,opts)=>url==='/api/simple/machine'?new Promise(done=>resolve=done):fetch(url,opts);
    w.document.querySelector('.machine-link').click();
    w.document.querySelector('.machine-back').click();
    resolve({ok:true,json:async()=>({name:'Obsolete guest'})});await f.settle();
    assert.equal(w.document.querySelector('h1').textContent,'Inventory');
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Windows template review preserves the name, clone mode, disks and software inputs',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    w.eval("state.resources.push({type:'qemu',template:1,node:'pve',vmid:9000,name:'Server 2025 base'})");
    f.records.tasks.push({id:'share',name:'Configure share',platform:'windows',kind:'ansible',body:'test',parameters:{share_name:'Share name'},defaults:{share_name:'Lab'}});
    await f.build('windows');
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
    form.querySelector('[data-section=scripts]').click();
    assert.equal(form.querySelector('[data-task-input]'),null);
    form.querySelector('[data-add-catalog=share]').click();
    let parameter=form.querySelector('[data-task-input=share_name]');
    assert.equal(parameter.value,'Lab');
    parameter.value='two words';assert.ok(!parameter.checkValidity());
    assert.ok(!parameter.closest('.software-pane').hidden);
    form.querySelector('[data-task-action=remove]').click();assert.equal(form.querySelector('[data-task-input]'),null);
    form.querySelector('[data-add-catalog=share]').click();
    form.querySelector('[data-add-catalog=share]').click();
    parameter=form.querySelectorAll('[data-task-input=share_name]')[1];
    parameter.value='Second';
    form.querySelectorAll('[data-task-action=up]')[1].click();
    form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    const payload=f.calls.find(([u])=>u==='/api/simple/plan')[1];
    assert.equal(payload.source_kind,'template');assert.equal(Number(payload.template_id),9000);
    assert.equal(payload.name,'files-01');assert.equal(payload.manual_updates,true);
    assert.deepEqual(payload.data_disks,[{size:80,label:'Data',letter:'F'}]);
    assert.deepEqual(payload.tasks,[{id:'share',params:{share_name:'Second'}},{id:'share',params:{share_name:'Lab'}}]);
    assert.equal(payload.scriptSteps,undefined);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Windows ISO picker categorizes candidates and keeps other media browsable',async()=>{
  const f=await fixture({},true),{w}=f;
  try{
    const fetch=w.fetch;
    w.fetch=async(url,opts)=>{
      const res=await fetch(url,opts);if(url!=='/api/simple/options')return res;
      const d=await res.json();d.destinations[0].isos=[{id:'local:iso/win2022.iso',label:'win2022.iso',windows_label:'Windows Server 2022'},
        {id:'local:iso/virtio-win-2025.iso',label:'virtio-win-2025.iso',windows_label:null},{id:'local:iso/sql2022.iso',label:'sql2022.iso',windows_label:null}];
      return {ok:true,json:async()=>d};
    };
    await f.build('windows');
    const form=w.document.querySelector('#simple-form');
    assert.deepEqual([...form.elements.source.options].map(o=>o.text),['Windows Server 2022']);
    assert.match(w.document.querySelector('#simple-fields').textContent,/win2022\.iso/);
    form.elements.all_media.checked=true;form.elements.all_media.dispatchEvent(new w.Event('change'));await f.settle();
    assert.equal(form.elements.source.options.length,3);assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('opening a machine build shows progress immediately and allows a retry after failure',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    doc.querySelector('nav [data-view=create]').click();await f.settle();
    const original=w.fetch,requests=[];
    w.fetch=(url,opts)=>url==='/api/simple/options'
      ?new Promise(resolve=>requests.push(resolve)):original(url,opts);
    const card=doc.querySelector('.platform-card[data-simple=ubuntu]');
    card.click();card.click();
    assert.equal(card.getAttribute('aria-busy'),'true');
    assert.equal(card.disabled,true);
    assert.equal(requests.length,1);
    requests[0]({ok:false,status:503,json:async()=>({error:'Storage unavailable'})});await f.settle();
    assert.equal(card.hasAttribute('aria-busy'),false);
    assert.equal(card.disabled,false);
    assert.match(doc.querySelector('#notice').textContent,/Storage unavailable/);
    card.click();
    requests[1](await original('/api/simple/options'));await f.settle();
    assert.ok(doc.querySelector('#simple-dialog').open);
    assert.equal(card.hasAttribute('aria-busy'),false);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('confirming an ISO copy allows only one submission while the job request is in flight',async()=>{
  const f=await fixture(),{w}=f,doc=w.document;
  try{
    w.openAction('upload-iso',null,'tools.iso','pve');
    const form=doc.querySelector('#action-form'),button=form.querySelector('.modal-footer button');
    const original=w.fetch,requests=[];
    w.fetch=(url,opts)=>url==='/api/jobs'&&opts.method==='POST'
      ?new Promise(resolve=>requests.push(resolve)):original(url,opts);
    button.click();form.requestSubmit();button.click();
    assert.equal(requests.length,1);
    assert.equal(button.disabled,true);
    assert.equal(button.getAttribute('aria-busy'),'true');
    requests[0]({ok:false,status:409,json:async()=>({error:'File already exists'})});await f.settle();
    assert.ok(doc.querySelector('#editor').open);
    assert.match(doc.querySelector('#form-error').textContent,/File already exists/);
    assert.equal(button.disabled,false);
    assert.equal(button.hasAttribute('aria-busy'),false);
    form.requestSubmit();
    assert.equal(requests.length,2);
    requests[1]({ok:true,json:async()=>({id:'copy-job'})});await f.settle();
    assert.equal(doc.querySelector('#editor').open,false);
    assert.equal(button.hasAttribute('aria-busy'),false);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('ISO Library filters node files and explicitly verifies before handing the selected image to Create',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try {
    const source='local:iso/windows-server.iso',tools='local:iso/virtio-win.iso';
    const listing={node:'pve',nodes:[{node:'pve',status:'online'},{node:'other',status:'online'}],warnings:[],
      ssh_configured:true,driver_isos:[tools],files:[
        {id:source,name:'windows-server.iso',location:'proxmox',storage:'local',family:'windows',bytes:2**30},
        {id:'library:other.iso',name:'other.iso',location:'library',family:'other',bytes:1200}]};
    const fetch=w.fetch,checks=[],handoffs=[];
    w.fetch=async(url,opts)=>{
      if(url==='/api/simple/media')return {ok:true,json:async()=>listing};
      if(url==='/api/simple/media?node=other')return {ok:true,json:async()=>({...listing,node:'other',driver_isos:[],files:[]})};
      if(url==='/api/simple/media/verify'){
        checks.push(JSON.parse(opts.body));
        return {ok:true,json:async()=>({node:'pve',source,os:'windows',status:'verified',agent_iso:tools,
          checked_at:1,detail:'Media verified.',editions:[{index:1,label:'Server Standard',build:'26100',language:'en-US',compatible:true}]})};
      }
      return fetch(url,opts);
    };
    w.openSimpleMedia=(...args)=>handoffs.push(args);
    doc.querySelector('nav [data-view=media]').click();await f.settle();
    assert.equal(doc.querySelector('h1').textContent,'ISO Library');
    assert.equal(checks.length,0);
    assert.equal(doc.querySelectorAll('.media-file').length,2);
    const copy=doc.querySelector('[data-upload-file]');
    assert.equal(copy.textContent,'Copy to Proxmox storage…');
    assert.match(copy.closest('li').textContent,/Proxmate ISO folder/);
    assert.equal(doc.querySelectorAll('[data-upload-file]').length,1);
    copy.click();await f.settle();
    const form=doc.querySelector('#action-form');
    assert.ok(doc.querySelector('#editor').open);
    assert.match(form.textContent,/Copy other\.iso from the Proxmate ISO folder/);
    assert.equal(form.elements.node.value,'pve');
    assert.equal(form.elements.storage.value,'local');
    assert.equal(form.querySelector('.modal-footer button').textContent,'Copy ISO');
    assert.ok(!f.calls.some(([url,body])=>url==='/api/jobs' && body));
    doc.querySelector('#close').click();
    assert.match(doc.querySelector('[data-family=rocky]').textContent,/No candidates/);
    const search=doc.querySelector('[name=media_search]');
    search.value='not an iso';search.dispatchEvent(new w.Event('input',{bubbles:true}));
    assert.match(doc.querySelector('.media-files').textContent,/No files match/);
    search.value='';search.dispatchEvent(new w.Event('input',{bubbles:true}));
    doc.querySelector('[data-family=windows]').click();
    assert.equal(doc.querySelectorAll('.media-file').length,1);
    doc.querySelector('[data-check-group]').click();await f.settle();
    assert.deepEqual(checks,[{node:'pve',source,os:'windows',agent_iso:tools}]);
    assert.match(doc.querySelector('.media-files').textContent,/Server Standard/);
    assert.ok(doc.querySelector('[data-check-group]').disabled);
    doc.querySelector('[data-check]').click();await f.settle();
    assert.equal(checks.length,2);
    doc.querySelector('[data-use]').click();
    assert.deepEqual(handoffs,[['windows','pve',source,tools]]);
    const node=doc.querySelector('[name=media_node]');node.value='other';
    node.dispatchEvent(new w.Event('change',{bubbles:true}));await f.settle();
    assert.match(doc.querySelector('.media-files').textContent,/No ISO files/);
    assert.equal(doc.querySelector('[data-use]'),null);
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
    else if(url==='/api/simple/recipes')value=[];
    else if(url==='/api/lifecycle/state')value={families:[],versions:[],builds:[],policies:[],reports:[]};
    else if(url==='/api/lifecycle/plan')value={id:'recovery-plan',operation:'backup-restore',node:'pve',vmid:202,name:'restore-test',archive:'local:backup/vzdump-qemu-201-test.vma.zst',boot_test:true,disconnected:true,notes:[],dangerous:false};
    else if(url==='/api/lifecycle/run')value={id:'recovery-job'};
    else if(url.startsWith('/api/simple/software'))value={platforms:{'pve:201':'windows'},features:[{id:'Web-Server',name:'Web server (IIS)'}]};
    else if(url==='/api/simple/options')value={iso_setup_letters:['D','E','F'],destinations:[{node:'pve',storage:'local-lvm',iso_storage:'local',bridge:'vmbr0',management_cidr:'192.0.2.0/24',agent_iso:'local:iso/virtio.iso',storage_choices:['local-lvm'],iso_storages:['local'],bridges:['vmbr0'],driver_isos:['local:iso/virtio.iso'],isos:[{id:'local:iso/server.iso',label:'Windows Server 2025.iso',windows_label:'Windows Server 2025'}]}],local_isos:[],profiles:{'ubuntu-24.04':'Ubuntu 24.04 LTS'},features:[{id:'Web-Server',name:'Web server (IIS)'}]};
    else if(url==='/api/simple/editions')value={identity:'media-v1',editions:[{index:7,name:'Windows Server Standard',label:'Standard (Desktop Experience)',desktop:true,language:'en-US'}]};
    else if(url==='/api/simple/plan'){const d=JSON.parse(opts.body);value={id:'simple-plan',machines:Array.from({length:Number(d.count||1)},(_,i)=>({vmid:202+i,name:d.name+'-0'+(i+1),node:'pve',memory:4096,cores:2,disk:64,storage:'local-lvm',bridge:'vmbr0'})),edition:'Standard (Desktop Experience)',purpose:d.purpose,tasks:[{name:'Install Chocolatey',params:{}}],remote_access:'HTTPS WinRM',notes:[],dangerous:false};}
    else if(url==='/api/simple/run')value={jobs:[{id:'new-job'}]};
    else if(url==='/api/catalog')value=catalog;
    else if(url==='/api/provision/plan')value={steps:['Prepare VM'],notes:[]};
    else if(url==='/api/manage/plan'||url==='/api/simple/existing-plan')value={id:'reviewed-plan',operation:'tasks',targets:[resources[0]],tasks:[{name:'Install Chocolatey',params:{}}],settings:{},notes:[],snapshot_before:false,dangerous:false};
    else if(url.startsWith('/api/manage/')){const kind=url.split('/').at(-1);value=records[kind]||{id:'job'};}
    return {ok:true,status:200,json:async()=>value};
  };
  for(const asset of w.document.querySelectorAll('script[src]')){
    const url=new URL(asset.src);
    assert.equal(url.origin,w.location.origin);
    const script=w.document.createElement('script');
    script.textContent=fs.readFileSync('.'+url.pathname,'utf8');
    w.document.head.append(script);
  }
  const settle=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve))};
  await settle();
  if(server.connected&&!startAtHome){w.eval("view='overview';render()");await settle();}
  const build=async os=>{
    w.document.querySelector('nav [data-view="create"]').click();await settle();
    w.document.querySelector('[data-simple="'+os+'"][data-purpose="vm"]').click();await settle();
  };
  return {w,calls,errors,records,server,settle,build,close:()=>dom.window.close()};
}

test('guest credentials filter VM connections, preserve focus and show verification after rotation',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    f.records.credentials.push({id:'win',name:'Windows admins',platform:'windows',username:'win-admin',updated:2,has_password:true},
      {id:'ssh',name:'Linux deploy',platform:'linux',username:'svc-linux',updated:3,has_private_key:true,has_become_password:true});
    f.records.connections.push({node:'pve',vmid:201,credential_id:'win',machine_identity:'stable-id',verified_at:11000,credential_revision:2});
    doc.querySelector('[data-view="credentials"]').click();await f.settle();
    const query=doc.querySelector('#credential-query'),platform=doc.querySelector('#credential-platform'),list=doc.querySelector('#credential-list');
    assert.equal(list.querySelectorAll('.credential-entry').length,2);
    assert.match(list.textContent,/SSH key, sudo password/);
    query.value='Test VM';query.dispatchEvent(new w.Event('input'));query.focus();
    const usage=list.querySelector('.credential-usage');usage.open=true;
    assert.match(usage.textContent,/Test VM.*201.*Last verified/);
    w.eval('render()');
    assert.equal(doc.activeElement,query);
    assert.equal(list.querySelector('.credential-usage'),usage);
    assert.equal(usage.open,true);
    f.records.credentials[0].updated=4;await w.eval('loadManagement();');w.eval('render()');
    assert.match(list.textContent,/Credential changed since verification/);
    platform.value='linux';platform.dispatchEvent(new w.Event('change'));
    assert.match(list.textContent,/No matching credentials/);
    doc.querySelector('#credential-clear').click();
    list.querySelector('[data-record="ssh"][data-mg="edit-credentials"]').click();
    const fields=doc.querySelector('#manage-form').elements;
    assert.equal(fields.private_key.value,'');assert.equal(fields.ca_pem.disabled,true);
    assert.equal(fields.platform.disabled,true);
    doc.querySelector('#manage-close').click();
    doc.querySelector('[data-mg="new-credentials"]').click();
    assert.equal(fields.password.required,true);
    fields.platform.value='linux';fields.platform.dispatchEvent(new w.Event('change'));
    assert.equal(fields.password.required,false);assert.equal(fields.private_key.disabled,false);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('a pinned build login can be found, used and overridden without submitting stale login fields',async()=>{
  const f=await fixture({},true),{w,records}=f,doc=w.document,original=w.fetch;
  const win={id:'lab-win',name:'Lab Administrator',username:'Administrator',platform:'windows',has_password:true};
  const linux={id:'lab-ssh',name:'Lab SSH',username:'deploy',platform:'linux',has_private_key:true,is_build_default:true};
  records.credentials.push(win,linux,{id:'ops',name:'Domain ops',platform:'windows',username:'domain-user',has_password:true});
  const changes=[];
  w.fetch=async(url,opts)=>{
    if(url==='/api/manage/credentials/defaults'&&opts?.body){
      changes.push(JSON.parse(opts.body));win.is_build_default=true;
      return {ok:true,json:async()=>({windows:win,linux})};
    }
    const response=await original(url,opts);
    if(url==='/api/simple/options')(await response.json()).build_defaults={windows:win,linux};
    return response;
  };
  try{
    doc.querySelector('nav [data-view=credentials]').click();await f.settle();
    assert.match(doc.querySelector('#build-default-cards').textContent,/Lab SSH/);
    doc.querySelector('[data-mg=build-default][data-record=windows]').click();await f.settle();
    // Chromium can deliver the preceding dialog's close event after reopening.
    doc.querySelector('#manage-dialog').dispatchEvent(new w.Event('close'));
    const picker=doc.querySelector('#manage-form').elements;
    assert.deepEqual([...picker.credential_id.options].map(o=>o.value),['',win.id]);
    picker.filter.value='missing';picker.filter.dispatchEvent(new w.Event('input'));
    assert.equal(picker.credential_id.options.length,1);
    picker.filter.value='lab';picker.filter.dispatchEvent(new w.Event('input'));picker.credential_id.value=win.id;
    doc.querySelector('#manage-submit').click();await f.settle();
    assert.deepEqual(changes,[{platform:'windows',credential_id:win.id}]);
    assert.match(doc.querySelector('#build-default-cards').textContent,/Lab Administrator/);
    await f.build('windows');
    let fields=doc.querySelector('#simple-form').elements;
    assert.ok(doc.querySelector('#simple-dialog').open&&fields.edition.value);
    assert.equal(fields.build_credential_id.value,win.id);assert.ok(fields.password.disabled);
    fields.build_credential_id.value='';fields.build_credential_id.dispatchEvent(new w.Event('change'));
    assert.ok(!fields.password.disabled&&fields.password.required);
    fields.password.value='Separate-pass#123';
    fields.build_credential_id.value=win.id;fields.build_credential_id.dispatchEvent(new w.Event('change'));
    doc.querySelector('#simple-next').click();await f.settle();
    assert.equal(w.eval('simpleStep'),1,JSON.stringify([...doc.querySelectorAll('#simple-form :invalid')].map(x=>x.name))+doc.querySelector('#simple-error').textContent);
    doc.querySelector('#simple-next').click();await f.settle();
    assert.equal(w.eval('simpleStep'),2,doc.querySelector('#simple-error').textContent);
    const draft=f.calls.find(([url])=>url==='/api/simple/plan')[1];
    assert.equal(draft.build_credential_id,win.id);assert.ok(!('password' in draft));
    doc.querySelector('#simple-close').click();await f.settle();
    await f.build('ubuntu');fields=doc.querySelector('#simple-form').elements;
    assert.ok(fields.username.disabled);assert.equal(fields.build_credential_id.value,linux.id);
    assert.ok(!fields.github_username.disabled);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

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
    w.document.querySelector('[data-mg="apply"]').click();await f.settle();
    const script=w.document.querySelector('[name=library_script]');
    script.value='install-chocolatey';script.dispatchEvent(new w.Event('change'));
    w.document.querySelector('[data-add-script]').click();
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
    await f.build('windows');
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
    await f.build('windows');
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
    await f.build('windows');
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
    await f.build('windows');
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

test('Ansible input rows preserve existing settings and validate additions before saving',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    const example={id:'example-folder',name:'Folder',platform:'windows',kind:'ansible',example:true,
      body:"- ansible.windows.win_file:\n    path: 'C:/{{ proxmate_inputs.name }}'\n    state: directory",
      parameters:{name:'Folder name'},defaults:{name:'LabTools'}};
    f.records.tasks.push(example);
    const original=w.fetch;let saved;
    w.fetch=async(url,opts)=>{
      if(url==='/api/manage/tasks/check')return {ok:true,json:async()=>({valid:true})};
      if(url==='/api/manage/tasks'&&opts?.body)saved=JSON.parse(opts.body);
      return original(url,opts);
    };
    doc.querySelector('[data-view=scripts]').click();await f.settle();
    doc.querySelector('[data-record=example-folder][data-mg=edit-tasks]').click();
    const form=doc.querySelector('#manage-form'),grid=form.querySelector('.script-inputs');
    const rows=grid.querySelector('[data-input-rows]'),add=grid.querySelector('[data-add-input]');
    const field=(row,part)=>row.querySelector('[data-field='+part+']');
    const set=(input,value)=>{input.value=value;input.dispatchEvent(new w.Event('input',{bubbles:true}))};
    assert.deepEqual([...rows.firstElementChild.querySelectorAll('input')].map(i=>i.value),['name','Folder name','LabTools']);
    add.click();
    const second=rows.lastElementChild,key=field(second,'key'),label=field(second,'label'),value=field(second,'value');
    assert.equal(doc.activeElement,key);
    for(const bad of ['Bad','folder name','a-b','9name']){
      set(key,bad);assert.ok(key.validity.patternMismatch);assert.ok(key.nextElementSibling.textContent);
    }
    set(key,'name');assert.match(key.validationMessage,/unique/);
    set(label,'   ');assert.ok(label.validity.customError);
    set(label,'Second folder | optional');set(value,'two words');assert.ok(value.validity.patternMismatch);
    form.requestSubmit();await f.settle();assert.equal(saved,undefined);
    form.elements.kind.value='powershell';form.elements.kind.dispatchEvent(new w.Event('change'));
    assert.ok(grid.hidden && grid.disabled && form.checkValidity());
    form.elements.kind.value='ansible';form.elements.kind.dispatchEvent(new w.Event('change'));
    set(key,'second_name2');set(value,'A_2.3+tag@lab:1-ok');assert.ok(value.checkValidity());
    set(value,'');assert.ok(form.checkValidity());
    for(let i=rows.children.length;i<20;i++)add.click();
    assert.ok(add.disabled);
    while(rows.children.length>2)rows.lastElementChild.querySelector('[data-remove-input]').click();
    assert.ok(!add.disabled);
    form.requestSubmit();await f.settle();
    assert.deepEqual(saved.parameters,{name:'Folder name',second_name2:'Second folder | optional'});
    assert.deepEqual(saved.defaults,{name:'LabTools'});
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('typed library inputs round trip from editor to the operations review',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    const record={id:'typed',name:'Typed script',platform:'windows',kind:'ansible',body:'- ansible.windows.win_file: {}',
      parameters:{action:'Action',enabled:'Enable',letter:'Free drive letter'},
      input_types:{action:{type:'choice',choices:['start','stop']},enabled:{type:'boolean'},letter:{type:'drive'}},
      defaults:{action:'start',enabled:'false',letter:'D'}};
    f.records.tasks.push(record);
    f.records.connections.push({node:'pve',vmid:201,platform:'windows'});
    const original=w.fetch;let saved;
    w.fetch=async(url,opts)=>{
      if(url==='/api/simple/drive-letters')return {ok:true,json:async()=>({occupied:['C','D']})};
      if(url==='/api/manage/tasks/check')return {ok:true,json:async()=>({valid:true})};
      if(url==='/api/manage/tasks' && opts?.body)saved=JSON.parse(opts.body);
      return original(url,opts);
    };
    doc.querySelector('[data-view=scripts]').click();await f.settle();
    doc.querySelector('[data-record=typed][data-mg=edit-tasks]').click();
    const form=doc.querySelector('#manage-form');
    assert.deepEqual([...form.querySelectorAll('[data-input-type]')].map(el=>el.value),['choice','boolean','drive']);
    form.requestSubmit();await f.settle();
    assert.deepEqual(saved.input_types,record.input_types);assert.deepEqual(saved.defaults,record.defaults);
    doc.querySelector('[data-simple-task=typed]').click();form.querySelector('[name=target]').click();
    form.requestSubmit();await f.settle();
    const param=k=>form.querySelector('[data-run-param='+k+']');
    assert.equal(param('action').tagName,'SELECT');param('action').value='stop';
    assert.equal(param('enabled').type,'checkbox');param('enabled').click();
    assert.ok(param('letter').querySelector('[value=D]').disabled);assert.equal(param('letter').value,'');
    param('letter').value='G';form.requestSubmit();await f.settle();
    const request=f.calls.find(([url])=>url==='/api/simple/existing-plan')[1];
    assert.deepEqual(request.tasks,[{id:'typed',params:{action:'stop',enabled:'true',letter:'G'}}]);
  }finally{f.close()}
});

test('completed build shortcuts resolve the original job again before exporting a connection',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    let details,blob,filename;
    const original=w.fetch;
    w.fetch=async(url,opts)=>{
      if(url==='/api/simple/machine/remote-access'){
        details=JSON.parse(opts.body);
        return {ok:true,json:async()=>({kind:'rdp',host:'2001:db8::9',port:3391})};
      }
      return original(url,opts);
    };
    w.URL.createObjectURL=b=>{blob=b;return 'blob:rdp'};w.URL.revokeObjectURL=()=>{};
    w.HTMLAnchorElement.prototype.click=function(){filename=this.download};
    w.eval("selectNotifications(serverInfo.id);announceJobs([{action:'simple',id:'done',server_id:serverInfo.id,remote_kind:'rdp',target:{node:'pve',vmid:201,name:'demo'}}],'succeeded')");
    const button=doc.querySelector('.toast-remote-actions button');assert.equal(button.textContent,'Open desktop');
    button.click();await f.settle();
    assert.deepEqual(details,{node:'pve',vmid:201,job_id:'done'});assert.equal(filename,'demo.rdp');
    const text=await new Promise(resolve=>{const reader=new w.FileReader();reader.onload=()=>resolve(reader.result);reader.readAsText(blob)});
    assert.match(text,/full address:s:\[2001:db8::9\]:3391\r\n/);assert.doesNotMatch(text,/password|username/i);
  }finally{f.close()}
});

test('a saved build asks for fresh credentials and reviews before it queues',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,fetch=w.fetch;
  const recipe={name:'Lab Windows',id:'saved',os:'windows',purpose:'vm',source_kind:'iso',password_login:true};
  w.fetch=async(url,opts)=>url==='/api/simple/recipes'?{ok:true,json:async()=>[recipe]}:fetch(url,opts);
  try{
    doc.querySelector('nav [data-view=create]').click();await f.settle();
    doc.querySelector('#saved-builds button').click();await f.settle();
    const fields=doc.querySelector('#manage-form').elements;
    assert.equal(fields.password.value,'');assert.equal(fields.name.value,'');
    fields.password.value='Fresh-Password#123';fields.name.value='win-copy';
    doc.querySelector('#manage-submit').click();await f.settle();
    assert.equal(doc.querySelector('#manage-title').textContent,'Review new VM');
    assert.deepEqual(f.calls.find(([url])=>url==='/api/simple/plan')[1],{
      name:'win-copy',password:'Fresh-Password#123',reuse:{recipe_id:'saved'}});
    assert.equal(f.calls.some(([url])=>url==='/api/simple/run'),false);
    fields.confirmed.checked=true;doc.querySelector('#manage-submit').click();await f.settle();
    assert.equal(f.calls.filter(([url])=>url==='/api/simple/run').length,1);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('failed job offers the frozen remainder and warns before retrying a started task',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,fetch=w.fetch;
  w.fetch=async(url,opts)=>{
    if(url==='/api/jobs/failed')return {ok:true,json:async()=>({id:'failed',status:'failed',action:'manage',log:'Failed',
      task_recovery:{server_id:'selected-server',targets:[{name:'ci-box',target:'pve:201',completed:1,remaining:2,next_task:'Configure tools',started:true}]}})};
    if(url==='/api/simple/recovery/plan')return {ok:true,json:async()=>({id:'retry-plan',dangerous:true,
      recovery:{started:true},targets:[{name:'ci-box',guest_host:'192.0.2.8',credential_name:'VM login'}],
      tasks:[{name:'Configure tools',params:{}},{name:'Check service',params:{}}]})};
    return fetch(url,opts);
  };
  try{
    w.eval("openJobLog('failed')");await f.settle();
    assert.match(doc.querySelector('.job-followups').textContent,/1 completed · 2 remaining/);
    doc.querySelector('.job-followups button').click();await f.settle();
    assert.match(doc.querySelector('#manage-fields').textContent,/may have changed the guest/);
    assert.deepEqual([...doc.querySelectorAll('#manage-fields li')].map(el=>el.textContent.trim()),['Configure tools','Check service']);
    assert.equal(f.calls.some(([url])=>url==='/api/manage/run'),false);
    const fields=doc.querySelector('#manage-form').elements;
    fields.confirmed.checked=true;fields.risk_confirmed.checked=true;
    doc.querySelector('#manage-submit').click();await f.settle();
    assert.deepEqual(f.calls.find(([url])=>url==='/api/manage/run')[1],{plan_id:'retry-plan',confirmed:true,risk_confirmed:true});
    assert.deepEqual(f.errors,[]);
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
    await f.build('windows');
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

test('exporting guided and saved lab builds downloads the reviewed sources and chosen prerequisites',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,original=w.fetch,exports=[];
  const recipes=[{id:'storage-recipe',name:'Storage',os:'ubuntu'},{id:'web-recipe',name:'Web',os:'ubuntu'}];
  let downloaded='';
  w.URL.createObjectURL=()=>'blob:lab';w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){downloaded=this.download};
  w.fetch=async(url,opts)=>{
    if(url==='/api/simple/labs/export'){
      exports.push({data:JSON.parse(opts.body),headers:opts.headers});
      return {ok:true,blob:async()=>new w.Blob(['lab'])};
    }
    if(url==='/api/simple/recipes')return {ok:true,json:async()=>recipes};
    if(url==='/api/jobs/finished')return {ok:true,json:async()=>({id:'finished',action:'simple',server_id:f.server.id,
      target:{name:'from-job'},reusable_build:{},updated:1,status:'succeeded',log:'Done'})};
    return original(url,opts);
  };
  try{
    doc.querySelector('nav [data-view=labs]').click();await f.settle();
    doc.querySelector('#lab-export').click();await f.settle();
    doc.querySelectorAll('#lab-fields input').forEach(input=>input.click());
    doc.querySelector('#lab-next').click();await f.settle();
    doc.querySelector('#lab-form [name=name]').value='Shared lab';
    doc.querySelectorAll('#lab-fields [name=key]')[0].value='files';
    doc.querySelectorAll('#lab-fields fieldset')[1].querySelector('[data-after="0"]').click();
    doc.querySelector('#lab-next').click();await f.settle();
    assert.deepEqual(exports[0].data,{name:'Shared lab',machines:[
      {key:'files',recipe_id:'storage-recipe',after:[]},{key:'web',recipe_id:'web-recipe',after:['files']}]});
    assert.equal(exports[0].headers['X-CSRF-Token'],'test-csrf');
    assert.equal(exports[0].headers['X-Proxmate-Server'],f.server.id);
    assert.equal(downloaded,'Shared-lab.zip');
    w.eval("openJobLog('finished')");await f.settle();
    [...doc.querySelectorAll('.job-followups button')].find(b=>b.textContent==='Export lab').click();
    doc.querySelector('#lab-next').click();await f.settle();
    assert.deepEqual(exports[1].data,{name:'from-job',machines:[{key:'from-job',job_id:'finished',after:[]}]});
    doc.querySelector('#close').click();
    await f.build('windows');
    const wizard=doc.querySelector('#simple-form');
    wizard.elements.name.value='server';wizard.elements.count.value='2';wizard.elements.password.value='Fresh-Password#123';
    wizard.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    wizard.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await f.settle();
    [...wizard.querySelectorAll('button')].find(b=>b.textContent==='Export lab').click();
    doc.querySelector('#lab-form [name=name]').value='Batch';
    doc.querySelector('#lab-next').click();await f.settle();
    assert.deepEqual(exports[2].data,{name:'Batch',machines:[
      {key:'server-01',plan_id:'simple-plan',machine_name:'server-01',after:[]},
      {key:'server-02',plan_id:'simple-plan',machine_name:'server-02',after:[]}]});
    assert.equal(doc.querySelector('#simple-dialog').open,true);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('import inspects scripts, maps local media and secrets, then confirms one frozen lab and follows its jobs',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,original=w.fetch,requests=[];
  const script='- name: <img src=x>\n  ansible.builtin.file:\n    path: /opt/tools\n    state: directory\n';
  const edition='Standard (Desktop Experience)';
  const pack={id:'imported',name:'Pair',scripts:{'tasks/tools.yml':script},machines:[
    {key:'seed',os:'windows',after:[],settings:{},source:{kind:'iso',hint:'Windows Server 2025.iso',edition},tasks:[
      {run:'windows-domain-controller',version:1,with:{dsrm_password:{prompt:'password'}}}],secret_inputs:[{key:'password',label:'Restore password'}]},
    {key:'worker',os:'ubuntu',after:['seed'],settings:{key_login:true},source:{kind:'cloud'},tasks:[{file:'tasks/tools.yml'}],secret_inputs:[]}]};
  const machines=pack.machines.map((m,i)=>({key:m.key,after:m.after,plan:{machines:[{vmid:202+i,name:m.key,node:'pve',
    memory:4096,cores:2,disk:32,storage:'local-lvm',bridge:'vmbr0'}],source:{value:i?'ubuntu-24.04':'local:iso/server.iso'},iso_storage:'local',
    edition:i?'Ubuntu':edition,purpose:'vm',tasks:[],notes:[]}}));
  const review={id:'frozen',name:'Pair',dangerous:true,machines};
  const run={id:'deployment',name:'Pair',status:'queued',created:1,machines:machines.map((m,i)=>({...m.plan.machines[0],key:m.key,after:m.after,job_id:'job-'+i,status:i?'pending':'queued'}))};
  const badZip=()=>({ok:false,status:400,json:async()=>({error:'Invalid ZIP'})});
  let valid=false;
  w.fetch=async(url,opts={})=>{
    if(!url.startsWith('/api/simple/labs/'))return original(url,opts);
    requests.push([url,opts.body?JSON.parse(opts.body):null]);
    const json=value=>({ok:true,json:async()=>value});
    if(url.endsWith('/import'))return valid?json(pack):badZip();
    if(url.endsWith('/plan'))return json(review);
    if(url.endsWith('/run')||url.endsWith('/runs/deployment'))return json(run);
    return json([]);
  };
  try{
    doc.querySelector('nav [data-view=labs]').click();await f.settle();
    doc.querySelector('#lab-import').click();
    const file=doc.querySelector('#lab-fields [type=file]'),upload=()=>doc.querySelector('#lab-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
    Object.defineProperty(file,'files',{value:[new w.File(['portable zip'],'lab.zip')]});
    upload();await f.settle();
    assert.match(doc.querySelector('#lab-error').textContent,/Invalid ZIP/);
    valid=true;upload();await f.settle();
    assert.equal(requests.find(([url])=>url.endsWith('/import'))[1].archive,Buffer.from('portable zip').toString('base64'));
    assert.equal(doc.querySelectorAll('#lab-fields [data-machine-index]').length,2);
    assert.equal(doc.querySelector('#lab-fields pre').textContent,script);assert.equal(doc.querySelector('#lab-fields img'),null);
    assert.match(doc.querySelector('#lab-fields').textContent,/Starts after seed/);
    assert.equal(doc.querySelector('#lab-fields [name=edition]').value,'7');
    const machine=doc.querySelector('#lab-fields [data-machine-index]'),login=machine.querySelector('[name=password]'),prompt=machine.querySelector('[data-prompt=password]');
    assert.equal(login.type,'password');assert.equal(login.value,'');assert.equal(prompt.type,'password');
    login.value='Fresh-Guest#123';prompt.value='Domain-Recovery#123';
    doc.querySelector('#lab-fields [name=github_username]').value='sample-user';
    doc.querySelector('#lab-next').click();await f.settle();
    const plan=requests.find(([url])=>url.endsWith('/plan'))[1];
    assert.deepEqual(plan.secrets,{seed:{password:'Fresh-Guest#123',inputs:{password:'Domain-Recovery#123'}},worker:{github_username:'sample-user'}});
    assert.deepEqual(plan.bindings.seed,{name:'seed',node:'pve',storage:'local-lvm',iso_storage:'local',bridge:'vmbr0',
      source:'local:iso/server.iso',edition:'7',agent_iso:'local:iso/virtio.iso',management_cidr:'192.0.2.0/24',edition_identity:'media-v1'});
    assert.equal(doc.querySelector('#lab-title').textContent,'Review Pair');
    assert.equal(doc.querySelector('#lab-fields [type=password]'),null);
    assert.doesNotMatch(doc.querySelector('#lab-fields').innerHTML,/Fresh-Guest|Domain-Recovery/);
    doc.querySelector('#lab-next').click();await f.settle();
    assert.equal(requests.some(([url])=>url.endsWith('/run')),false);
    doc.querySelectorAll('#lab-fields [type=checkbox]').forEach(b=>b.click());
    doc.querySelector('#lab-next').click();await f.settle();
    assert.deepEqual(requests.find(([url])=>url.endsWith('/run'))[1],{plan_id:'frozen',confirmed:true,risk_confirmed:true});
    assert.match(doc.querySelector('#lab-run').textContent,/Waiting for prerequisites/);
    run.status='failed';run.machines[0].status='failed';run.machines[1].status='blocked';
    Object.defineProperty(doc,'hidden',{value:false});
    await w.eval('pollDashboard()');
    assert.match(doc.querySelector('#lab-run').textContent,/Prerequisite did not succeed/);
    assert.equal(doc.querySelectorAll('#lab-run button[data-log]').length,2);
    assert.equal(w.localStorage.length,0);assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('reopening a saved lab handles optional manifest fields, stale dialogs and names on the destination',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,original=w.fetch;
  const machines=[{key:'longmachinex-01',os:'ubuntu',source:{kind:'cloud'},secret_inputs:[]},
    {key:'constructor',os:'ubuntu',source:{kind:'cloud'},secret_inputs:[]}];
  const pack={id:'portable',name:'Saved pack',machines,scripts:{}};
  let resolveInspection;
  w.fetch=async(url,opts)=>{
    if(url==='/api/simple/labs/packs')return {ok:true,json:async()=>[{id:'portable',name:'Saved pack',machine_count:2,created:123}]};
    if(url==='/api/simple/labs/packs/portable')return {ok:true,json:()=>new Promise(resolve=>{resolveInspection=resolve})};
    return original(url,opts);
  };
  try{
    w.eval("jobs=[{status:'waiting',target:{name:'longmachi-01'}}]");
    doc.querySelector('nav [data-view=labs]').click();await f.settle();
    const shelf=doc.querySelector('#lab-packs');
    assert.match(shelf.textContent,/Saved pack.*2 roles/);
    assert.equal(shelf.querySelector('a').getAttribute('href'),'/api/simple/labs/packs/portable/export');
    shelf.querySelector('button').click();await f.settle();
    doc.querySelector('#lab-close').click();doc.querySelector('#lab-import').click();
    resolveInspection(pack);await f.settle();
    assert.equal(doc.querySelector('#lab-title').textContent,'Import lab pack');
    assert.equal(doc.querySelector('#lab-fields a[download]').getAttribute('href'),'/api/simple/labs/example');
    doc.querySelector('#lab-close').click();
    shelf.querySelector('button').click();await f.settle();
    doc.querySelector('#lab-dialog').dispatchEvent(new w.Event('close'));
    resolveInspection(pack);await f.settle();
    const names=[...doc.querySelectorAll('#lab-fields [name=name]')].map(n=>n.value);
    assert.deepEqual(names,['longmachin-2','constructor']);
    assert.equal(doc.querySelector('#lab-fields [name=linux_password]').value,'');
    assert.match(doc.querySelector('#lab-fields').textContent,/Ready to start independently/);
    assert.equal(f.calls.some(([url])=>url==='/api/simple/labs/plan'),false);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Create lab combines new builds, copied settings and existing guest roles with ordered editable scripts',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,orig=w.fetch,calls=[];
  f.records.tasks.push({id:'dc',name:'Set up domain',platform:'windows',parameters:{domain:'Domain',restore:'Restore password'},
    input_types:{domain:{type:'dns_domain'},restore:{type:'password'}}});
  w.fetch=async(url,opts={})=>{
    if(url==='/api/simple/labs/machine-source'){
      calls.push([url,JSON.parse(opts.body)]);
      return {ok:true,json:async()=>({basis:'guided',name:'Original',base_job_id:'old-job',os:'windows',source:{kind:'iso',hint:'Windows Server 2025.iso'},
        settings:{cores:8,memory:8192,locale:'en-GB'},tasks:[{name:'Frozen script',parameters:{tag:'Tag'},params:{tag:'Old'}}]})};
    }
    if(opts.method==='POST'&&url==='/api/simple/labs/packs'){
      calls.push([url,JSON.parse(opts.body)]);
      return {ok:true,json:async()=>({id:'saved',name:'Pair and role'})};
    }
    return orig(url,opts);
  };
  const change=(el,value)=>{el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}))};
  const addTask=(row,id)=>{
    row.querySelector('[data-role-pane=process]').click();
    row.querySelector('[data-add-catalog="'+id+'"]').click();
  };
  const addRole=async(mode,source)=>{
    doc.querySelector('#design-add-role').click();
    doc.querySelector('[name=role_method][value='+mode+']').click();
    if(source)change(doc.querySelector('#design-add-panel [name=existing_vm]'),source);
    doc.querySelector('#design-accept-role').click();await f.settle();
  };
  try{
    doc.querySelector('nav [data-view=labs]').click();await f.settle();
    doc.querySelector('#lab-create').click();await f.settle();
    doc.querySelector('[name=lab_name]').value='Pair and role';
    await addRole('new');
    const first=doc.querySelector('.design-machine');
    first.querySelector('[name=key]').value='tools';
    first.querySelector('[name=cores]').value='4';
    addTask(first,'linux-package');first.querySelector('[data-task-input=package]').value='curl';
    await addRole('copy','0');
    const second=doc.querySelectorAll('.design-machine')[1];
    assert.equal(second.querySelector('[name=cores]').value,'8');
    assert.equal(second.querySelector('[name=memory_gb]').value,'8');
    assert.equal(second.querySelector('[name=media_hint]').value,'name:Windows Server 2025.iso');
    [...second.querySelectorAll('button')].find(b=>b.textContent==='Read editions from ISO').click();await f.settle();
    change(second.querySelector('[name=edition_label]'),'Standard (Desktop Experience)');
    second.querySelector('[data-task-input=tag]').value='New';
    addTask(second,'dc');second.querySelector('[data-task-input=domain]').value='lab.example.test';
    second.querySelector('[data-task-action=up][data-index="1"]').click();
    second.querySelector('[data-dependency]').click();
    await addRole('existing','0');
    const third=doc.querySelectorAll('.design-machine')[2];
    assert.equal(third.querySelector('[name=os]').value,'windows');
    assert.equal(third.querySelector('[name=memory_gb]'),null);assert.equal(third.querySelector('[data-script-step]'),null);
    addTask(third,'install-chocolatey');
    assert.equal(doc.querySelector('#lab-fields input[type=password]'),null);
    assert.match(second.textContent,/deployment/i);
    assert.equal(first.hidden,true);assert.equal(third.hidden,false);
    doc.querySelector('#lab-next').click();await f.settle();
    const draft=calls.find(([path])=>path.endsWith('/packs'))[1];
    assert.deepEqual(draft,{name:'Pair and role',machines:[
      {key:'tools',os:'ubuntu',source:{kind:'cloud'},settings:{cores:4},after:[],tasks:[{id:'linux-package',params:{package:'curl'}}]},
      {key:'Original',os:'windows',source:{kind:'iso',hint:'Windows Server 2025.iso',edition:'Standard (Desktop Experience)'},settings:{cores:8,memory:8192,locale:'en-GB'},
        base_job_id:'old-job',after:['tools'],tasks:[{id:'dc',params:{domain:'lab.example.test'}},{original:0,params:{tag:'New'}}]},
      {key:'Original-2',os:'windows',source:{kind:'existing',hint:'Original'},after:[],tasks:[{id:'install-chocolatey',params:{}}]}]});
    assert.equal(doc.querySelector('#lab-title').textContent,'Lab saved');
    assert.equal(f.calls.some(([url])=>/labs\/(run|plan)$/.test(url)),false);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('existing-only lab deliberately selects its target, uses the saved login and requires the existing-VM confirmation',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,original=w.fetch,requests=[];
  f.records.connections.push({id:'pve:201',node:'pve',vmid:201,platform:'linux',credential_id:'saved'},
    {id:'pve:203',node:'pve',vmid:203,platform:'windows',credential_id:'windows',verified_at:1});
  const machine={key:'service',os:'linux',source:{kind:'existing',hint:'Test VM'},tasks:[{run:'linux-package',version:1,with:{package:'curl'}}],secret_inputs:[]};
  const pack={id:'s',name:'Configure',machines:[machine],scripts:{}};
  w.fetch=async(url,opts={})=>{
    if(url==='/api/simple/resources')return {ok:true,json:async()=>[
      {type:'qemu',name:'Test VM',node:'pve',vmid:201,status:'running'},
      {type:'qemu',name:'Offline',node:'pve',vmid:202,status:'stopped'},
      {type:'qemu',name:'Windows',node:'pve',vmid:203,status:'running'},
      {type:'qemu',name:'Unknown',node:'pve',vmid:204,status:'running'}]};
    if(!url.startsWith('/api/simple/labs/'))return original(url,opts);
    const body=opts.body?JSON.parse(opts.body):null;
    requests.push([url,body]);let value=[];
    if(url.endsWith('/packs'))value=[{id:'s',name:'Configure',created:1,machine_count:1}];
    if(url.endsWith('/packs/s'))value=pack;
    if(url.endsWith('/plan'))value={id:'review',name:'Configure',dangerous:true,existing:true,machines:[{key:'service',after:[],mode:'existing',
      plan:{targets:[{name:'Test VM',node:'pve',vmid:201,guest_host:'192.0.2.31',platform:'linux',credential_name:'Server login'}],tasks:[],snapshot_before:false}}]};
    if(url.endsWith('/run'))value={id:'run',name:'Configure',status:'queued',machines:[{...machine,mode:'existing',name:'Test VM',node:'pve',vmid:201,after:[],job_id:'child',status:'queued'}]};
    return {ok:true,json:async()=>value};
  };
  try{
    doc.querySelector('nav [data-view=labs]').click();await f.settle();
    doc.querySelector('#lab-packs button').click();await f.settle();
    assert.equal(f.calls.some(([url])=>url==='/api/simple/options'),false);
    const target=doc.querySelector('#lab-fields [name=target]');
    assert.equal(target.value,'');assert.equal(target.querySelector('[value="pve:202"]').disabled,true);
    assert.equal(target.querySelector('[value="pve:203"]').disabled,true);
    assert.equal(target.querySelector('[value="pve:204"]').disabled,false);
    target.value='pve:204';target.dispatchEvent(new w.Event('change'));
    doc.querySelector('.lab-new-login [name=username]').value='operator';
    doc.querySelector('.lab-new-login [name=password]').value='TemporaryGuest#123';
    target.value='pve:201';target.dispatchEvent(new w.Event('change'));
    assert.match(doc.querySelector('.lab-target-login').textContent,/saved Linux login/);
    assert.equal(doc.querySelector('#lab-fields [name=password]'),null);
    doc.querySelector('#lab-next').click();await f.settle();
    assert.deepEqual(requests.find(([url])=>url.endsWith('/plan'))[1],{pack_id:'s',bindings:{service:{node:'pve',vmid:201}},secrets:{service:{}}});
    assert.match(doc.querySelector('#lab-fields legend').textContent,/Run scripts on existing VM · Test VM/);
    doc.querySelector('#lab-fields [name=confirmed]').click();doc.querySelector('#lab-fields [name=risk_confirmed]').click();
    doc.querySelector('#lab-next').click();await f.settle();
    assert.equal(requests.some(([url])=>url.endsWith('/run')),false);
    doc.querySelector('#lab-fields [name=existing_confirmed]').click();
    doc.querySelector('#lab-next').click();await f.settle();
    assert.deepEqual(requests.find(([url])=>url.endsWith('/run'))[1],{plan_id:'review',confirmed:true,risk_confirmed:true,existing_confirmed:true});
    assert.match(doc.querySelector('#lab-run').textContent,/Scripts on existing VM/);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('Labs lists support retries and filters, and saved packs retain frozen steps when edited as copies',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document,original=w.fetch;
  let attempts=0,created;
  const machine={key:'files',os:'ubuntu',source:{kind:'cloud'},settings:{cores:4},tasks:[{file:'tasks/tools.yml'}],
    editable_tasks:[{name:'Frozen tools',parameters:{folder:'Folder'},defaults:{folder:'Tools'},params:{folder:'Demo'}}],secret_inputs:[]};
  const saved={id:'original',name:'Lab A',machines:[machine],scripts:{'tasks/tools.yml':'- name: Prepare tools'}};
  w.fetch=async(url,opts={})=>{
    const json=value=>({ok:true,json:async()=>value});
    if(url==='/api/simple/labs/packs'&&opts.method==='POST'){created=JSON.parse(opts.body);return json({id:'copy',name:created.name})}
    if(url==='/api/simple/labs/packs'){
      if(++attempts===1)return {ok:false,status:503,json:async()=>({error:'Temporarily unavailable'})};
      return json([{id:'original',name:'Lab A',machine_count:1,created:1}]);
    }
    if(url==='/api/simple/labs/runs')return json([]);
    if(url==='/api/simple/labs/packs/original')return json(saved);
    if(url==='/api/simple/recipes')return json([{id:'recipe',name:'Windows seed'}]);
    if(url==='/api/simple/recipes/recipe/lab-source')return json({base_recipe_id:'recipe',os:'windows',source:{kind:'iso'},settings:{},
      tasks:[{name:'Frozen setup',parameters:{},params:{}}]});
    return original(url,opts);
  };
  try{
    doc.querySelector('nav [data-view=labs]').click();await f.settle();
    assert.match(doc.querySelector('#lab-packs').textContent,/Could not load/);
    doc.querySelector('#lab-packs button').click();await f.settle();
    const search=doc.querySelector('.lab-library input[type=search]');
    search.value='absent';search.dispatchEvent(new w.Event('input'));
    assert.match(doc.querySelector('#lab-packs').textContent,/No matches/);doc.querySelector('#lab-packs button').click();
    doc.querySelector('[data-lab-list=runs]').click();await f.settle();assert.match(doc.querySelector('#lab-runs').textContent,/No deployments yet/);
    doc.querySelector('[data-lab-list=packs]').click();await f.settle();
    doc.querySelector('#lab-packs [data-pack-action=edit]').click();await f.settle();
    assert.equal(doc.querySelector('#lab-next').textContent,'Save copy');
    assert.equal(doc.querySelector('[name=lab_name]').value,'Lab A (copy)');
    const first=doc.querySelector('.design-machine');
    assert.match(first.querySelector('.design-process').textContent,/Frozen tools/);
    first.querySelector('[data-task-input=folder]').value=''; // Save should navigate back to this role to report the missing setting.
    doc.querySelector('#design-add-role').click();doc.querySelector('[name=role_method][value=saved]').click();
    doc.querySelector('[name=saved_build]').value='recipe';doc.querySelector('#design-accept-role').click();await f.settle();
    const second=doc.querySelectorAll('.design-machine')[1];
    assert.equal(first.hidden,true);assert.equal(second.hidden,false);
    second.querySelector('[data-role-pane=settings]').click();second.querySelector('[data-dependency]').click();
    doc.querySelector('#lab-next').click();await f.settle();
    assert.equal(created,undefined);assert.equal(first.hidden,false);assert.equal(first.querySelector('.design-process').hidden,false);
    first.querySelector('[data-task-input=folder]').value='Fresh';
    const filter=first.querySelector('[data-script-search]');
    filter.value='Linux package';filter.dispatchEvent(new w.Event('input'));
    assert.ok(first.querySelector('[data-add-catalog=linux-package]'));assert.equal(first.querySelector('[data-add-catalog=install-chocolatey]'),null);
    doc.querySelector('#lab-next').click();await f.settle();
    assert.deepEqual(created,{name:'Lab A (copy)',machines:[
      {key:'files',os:'ubuntu',source:{kind:'cloud'},settings:{cores:4},after:[],base_pack_id:'original',base_role:'files',tasks:[{original:0,params:{folder:'Fresh'}}]},
      {key:'Windows-seed',os:'windows',source:{kind:'iso'},settings:{},after:['files'],base_recipe_id:'recipe',tasks:[{original:0,params:{}}]}]});
    assert.equal(f.calls.some(([url])=>url==='/api/simple/labs/run'),false);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('workspace navigation has named destinations and preserves routes through browser history',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  try{
    const navigate=name=>doc.querySelector('nav [data-view="'+name+'"]').click();
    navigate('create');await f.settle();
    assert.equal(w.location.hash,'#create');
    assert.equal(doc.querySelector('nav [aria-current=page]').textContent,'Create machine');
    doc.querySelector('[data-build-purpose=template]').click();
    assert.equal(doc.querySelectorAll('.platform-card[data-purpose=template]').length,4);
    navigate('labs');await f.settle();assert.equal(doc.querySelector('#workspace-location').textContent,'Labs');
    w.history.back();await new Promise(resolve=>w.addEventListener('hashchange',resolve,{once:true}));
    assert.equal(doc.querySelector('h1').textContent,'Create machine');
    navigate('machines');
    w.eval("state.resources.push({type:'qemu',template:1,node:'pve',vmid:998,name:'golden-base'})");
    doc.querySelector('[data-machine-status=templates]').click();
    assert.equal(doc.querySelector('.operation-controls').hidden,true);
    assert.ok(doc.querySelector('[data-clone-template="998"]'));
    navigate('jobs');doc.querySelector('[data-activity-filter=active]').click();
    assert.equal(doc.querySelector('[data-activity-filter=active]').getAttribute('aria-pressed'),'true');
    assert.equal(f.calls.some(([url])=>url==='/api/state'),false);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});

test('a checked installer is handed to the wizard on its exact node with its chosen guest tools',async()=>{
  const f=await fixture({},true),{w}=f,doc=w.document;
  const fetch=w.fetch,source='pool:iso/unlabelled.iso',agent='pool:iso/virtio-test.iso';
  w.fetch=async(url,opts)=>{
    const result=await fetch(url,opts);
    if(url==='/api/simple/options'){
      const options=await result.json();
      options.destinations.push({...options.destinations[0],node:'second',agent_iso:'pool:iso/virtio-old.iso',
        isos:[{id:source,label:'unlabelled.iso',windows_label:null},{id:'pool:iso/win2025.iso',label:'win2025.iso',windows_label:'Windows Server 2025'}],driver_isos:[agent]});
    }
    return result;
  };
  try{
    await w.eval('openSimpleMedia("windows","second",'+JSON.stringify(source)+','+JSON.stringify(agent)+')');
    const form=doc.querySelector('#simple-form');
    assert.equal(form.elements.node.value,'second');
    assert.equal(form.elements.source.value,source);
    assert.equal(form.elements.agent_iso.value,agent);
    doc.querySelector('#simple-close').click();
    await w.eval('openSimpleMedia("windows","second","missing.iso")');
    assert.equal(doc.querySelector('#simple-dialog').open,false);
    assert.match(doc.querySelector('#notice').textContent,/no longer available/);
    assert.deepEqual(f.errors,[]);
  }finally{f.close()}
});
