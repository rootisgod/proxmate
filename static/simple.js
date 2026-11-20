/* Everyday flows. Secrets and drafts stay in memory and are cleared on close. */
let simpleDraft=null,simpleStep=0,simpleOptions=null,simplePlan=null,simpleBusy=false;
let editionLookup=0;

const machineFilters = {query:'', status:'all', selected:new Set()};
const machineKey = guest => guest.node + ':' + guest.vmid;

function resetMachineFilters(){
  machineFilters.query='';
  machineFilters.status='all';
  machineFilters.selected.clear();
}

function machineRows(){
  const guests=mgGuests().slice().sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  const query=machineFilters.query.trim().toLowerCase();
  const shown=guests.filter(g=>(machineFilters.status==='all'||g.status===machineFilters.status)&&
    [g.name,g.node,g.vmid].some(v=>String(v).toLowerCase().includes(query)));
  if(!shown.length)return `<tr><td colspan="5" class="inventory-empty">${guests.length?'No machines match these filters.':'No virtual machines discovered. Create a machine to get started.'}</td></tr>`;
  return shown.map(g=>{
    const key=machineKey(g),name=g.name||'VM '+g.vmid;
    return `<tr><td class="pick"><input type="checkbox" aria-label="Select ${esc(name)} on ${esc(g.node)}" data-machine-key="${esc(key)}" ${machineFilters.selected.has(key)?'checked':''}></td>
      <td class="machine-name"><button class="machine-link" data-machine="${esc(key)}">${esc(name)}</button><small>${esc(g.node)} · VM ${esc(g.vmid)}</small></td>
      <td class="machine-state"><span class="pill ${esc(g.status)}">${esc(g.status||'unknown')}</span></td>
      <td class="capacity">${esc(g.maxcpu??'—')} vCPU<small>${g.maxmem?(g.maxmem/1073741824).toLocaleString(undefined,{maximumFractionDigits:1})+' GiB RAM':'Memory unavailable'}</small></td>
      <td class="machine-actions"><div class="row-actions"><button data-simple="apply" data-id="${esc(g.vmid)}" aria-label="Run software on ${esc(name)}">Run software</button><details class="machine-menu"><summary>Manage</summary><div>${mgButton('Power & hardware','manage-vm',g.vmid)}${('<button data-machine="'+esc(key)+'">Guest connection</button>')}${mgButton('Snapshots','snapshots',g.vmid)}</div></details></div></td></tr>`;
  }).join('');
}

function selectionLabel(){
  const count=machineFilters.selected.size;
  return count?'Run on '+count+' machine'+(count===1?'':'s'):'Run software';
}

function onlineNodes(){
  const nodes=state.resources.filter(r=>r.type==='node');
  return (nodes.length?nodes:state.nodes).filter(n=>n.status==='online').length;
}

function updateMachineInventory(){
  const rows=$('#machine-rows');
  if(!rows)return;
  const guests=mgGuests(),available=new Set(guests.map(machineKey));
  for(const key of machineFilters.selected)if(!available.has(key))machineFilters.selected.delete(key);
  for(const button of document.querySelectorAll('[data-machine-status]')){
    const status=button.dataset.machineStatus;
    button.querySelector('span').textContent=status==='all'?guests.length:guests.filter(g=>g.status===status).length;
  }
  $('#run-selected').textContent=selectionLabel();
  $('#run-selected').disabled=!guests.length;
  const nodes=onlineNodes();
  $('#content .title p').textContent='Discovered across '+nodes+' online Proxmox node'+(nodes===1?'':'s')+'.';
  const html=machineRows();
  if(rows._lastRows!==html){
    const focused=document.activeElement;
    const key=focused.closest('tr')?.querySelector('[data-machine-key]')?.dataset.machineKey;
    const openKeys=new Set([...rows.querySelectorAll('details[open]')].map(menu=>menu.closest('tr').querySelector('input').dataset.machineKey));
    rows.innerHTML=html;
    rows._lastRows=html;
    for(const checkbox of rows.querySelectorAll('[data-machine-key]')){
      const row=checkbox.closest('tr');
      if(openKeys.has(checkbox.dataset.machineKey))row.querySelector('details').open=true;
      if(key===checkbox.dataset.machineKey){
        const replacement=[...row.querySelectorAll('input,button,summary')].find(el=>el.tagName===focused.tagName&&el.textContent===focused.textContent);
        replacement?.focus({preventScroll:true});
      }
    }
  }
}

function renderRecentJobs(){
  const panel=$('#recent-jobs');
  if(!panel)return;
  panel.hidden=!jobs.length;
  const recent=jobs.slice(0,3).map(j=>`<li><button data-log="${esc(j.id)}"><span>${esc(j.operation||j.action)}</span><span class="pill ${esc(j.status)}">${esc(j.status)}</span></button></li>`).join('');
  panel.querySelector('ul').innerHTML=recent;
}

function renderMachines(){
  const filters=[['all','All machines'],['running','Running'],['stopped','Stopped']];
  const create=`<details class="create-menu"><summary class="primary">Create machine</summary><div><button data-simple="windows">Windows Server</button><button data-simple="ubuntu">Ubuntu</button></div></details>`;
  $('#content').innerHTML=title('Machines','',create)+
    `<section class="inventory panel" aria-label="Machine inventory"><div class="inventory-tools"><label>Find a machine<input id="machine-search" type="search" placeholder="Name, node or VM ID" value="${esc(machineFilters.query)}"></label>
    <button id="run-selected" data-simple="apply">Run software</button></div>
    <div class="inventory-filters" role="group" aria-label="Filter by status">${filters.map(([id,label])=>`<button data-machine-status="${id}" aria-pressed="${machineFilters.status===id}">${label} <span></span></button>`).join('')}</div>
    <table><thead><tr><th aria-label="Select"></th><th>Machine</th><th>Status</th><th>Resources</th><th>Actions</th></tr></thead><tbody id="machine-rows"></tbody></table></section>
    <section id="recent-jobs" class="recent-jobs" aria-label="Recent jobs"><div><h2>Recent jobs</h2><button data-view="jobs">View progress</button></div><ul></ul></section>`;
  updateMachineInventory();
  renderRecentJobs();
  $('#machine-search').oninput=e=>{machineFilters.query=e.target.value;updateMachineInventory()};
  $('.inventory-filters').onclick=e=>{
    const button=e.target.closest('[data-machine-status]');
    if(!button)return;
    machineFilters.status=button.dataset.machineStatus;
    for(const other of $('.inventory-filters').children)other.setAttribute('aria-pressed',String(other===button));
    updateMachineInventory();
  };
  $('#machine-rows').onchange=e=>{
    const key=e.target.dataset.machineKey;
    if(!key)return;
    if(e.target.checked)machineFilters.selected.add(key);else machineFilters.selected.delete(key);
    $('#run-selected').textContent=selectionLabel();
  };
}

function simpleRender(){
  if(!['home','scripts','machines'].includes(view))return false;
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  if(view==='home'||view==='machines'){
    renderMachines();
  }else{
    if(!mg){$('#content').innerHTML=title('Script library','Loading your saved scripts…');if(!mgLoading){mgLoading=true;loadManagement().then(render).catch(e=>notice(e.message)).finally(()=>mgLoading=false)}return true}
    $('#content').innerHTML=title('Script library','Save scripts once. Run them on new or existing machines.',mgButton('Save a script','new-tasks'))+
      '<p><button data-simple="apply">Install software or run scripts</button></p><div class="cards">'+mg.tasks.filter(t=>t.kind).map(t=>`<article class="card"><span class="pill">${t.platform==='windows'?'Windows':'Ubuntu / Linux'}</span><h2>${esc(t.name)}</h2><div class="row-actions"><button data-simple-task="${esc(t.id)}">Run on machines</button>${mgButton('Edit','edit-tasks',t.id)}${mgButton('Delete','delete-tasks',t.id)}</div><details><summary>View script</summary><pre>${esc(t.body)}</pre></details></article>`).join('')+'</div>'+(mg.tasks.some(t=>t.kind)?'':'<div class="panel simple-summary"><h2>Your scripts will appear here</h2><p>Save PowerShell, shell scripts or Ansible task lists. Common software installers are already included in the wizard.</p></div>');
  }
  return true;
}

async function openSimple(os='windows',purpose='vm',filename=''){
  if(!state){view='server';render();return notice('Connect your Proxmox server first.')}
  try{
    if(!serverInfo.ssh_fingerprint){
      view='server';render();
      $('.server-advanced').open=true;
      $('#server-form [name=ssh_fingerprint]').focus();
      return notice('Add the verified SSH fingerprint and save the connection to enable machine builds.');
    }
    const serverId=serverInfo.id;
    const [options]=await Promise.all([api('simple/options'),loadManagement()]);
    if(serverInfo.id!==serverId||!serverInfo.connected)return;
    simpleOptions=options;
    const dest=simpleOptions.destinations[0];
    const used=new Set([...state.resources.map(g=>g.name),...jobs.filter(j=>['queued','running','waiting'].includes(j.status)).map(j=>j.target?.name)].filter(Boolean).map(name=>name.toLowerCase()));
    const prefix=os==='windows'?'win-server':'ubuntu';
    let name=prefix;
    for(let index=2;used.has(name);index++)name=prefix.slice(0,11-String(index).length)+'-'+index;
    simpleDraft={os,purpose,count:1,name,profile:'ubuntu-24.04',username:'ubuntu',password:'',
      node:dest.node,storage:dest.storage,iso_storage:dest.iso_storage,bridge:dest.bridge,management_cidr:dest.management_cidr,agent_iso:dest.agent_iso,
      cores:2,memory_gb:os==='windows'?4:2,disk:os==='windows'?64:32,locale:'en-GB',timezone:'GMT Standard Time',packages:'',chocolatey:false,features:[],scripts:[],script_params:{}};
    simpleStep=0;simplePlan=null;
    if(filename)simpleDraft.source='library:'+filename;
    $('#simple-next').disabled=false;
    drawSimple();$('#simple-dialog').showModal();if(os==='windows')await loadEditions();
  }catch(e){notice(e.message)}
}

function simpleSources(){
  const dest=simpleOptions.destinations.find(n=>n.node===simpleDraft.node);
  const priority=iso=>/server/i.test(iso.label)&&!/sql/i.test(iso.label)?(/2025/.test(iso.label)?2:1):0;
  return [...dest.isos,...simpleOptions.local_isos].sort((a,b)=>priority(b)-priority(a)||a.label.localeCompare(b.label));
}

function collectSimple(){
  if(!simpleDraft)return;
  const form=$('#simple-form');Object.assign(simpleDraft,Object.fromEntries(new FormData(form)));
  form.querySelectorAll('input[type=checkbox][name]').forEach(i=>{if(!['feature','saved_script'].includes(i.name))simpleDraft[i.name]=i.checked});
  if(simpleStep===1){
    simpleDraft.features=[...form.querySelectorAll('[name=feature]:checked')].map(i=>i.value);
    simpleDraft.scripts=[...form.querySelectorAll('[name=saved_script]:checked')].map(i=>i.value);
    form.querySelectorAll('[data-script-param]').forEach(i=>{simpleDraft.script_params[i.dataset.scriptParam]=i.value});
  }
}

async function loadEditions(){
  if(!simpleDraft||simpleDraft.os!=='windows')return;
  const draft=simpleDraft,source=draft.source,lookup=++editionLookup;
  const current=()=>simpleDraft===draft&&editionLookup===lookup;
  if(!source){
    $('#simple-next').disabled=true;
    $('#simple-error').textContent='Choose a Windows Server ISO to continue.';
    return;
  }
  $('#simple-next').disabled=true;$('#simple-error').textContent='Reading Windows editions from your ISO…';
  try{
    const result=await api('simple/editions',{node:draft.node,source});
    if(!current())return;
    if(!result.editions.length)throw Error('No Windows editions found on this ISO.');
    draft.editions=result.editions;
    draft.edition=String((result.editions.find(e=>e.desktop&&/standard/i.test(e.name))||result.editions[0]).index);
    const select=$('#simple-form [name=edition]');
    select.replaceChildren(...draft.editions.map(e=>new Option(e.label,e.index)));
    select.value=draft.edition;
    $('#simple-error').textContent='';
    $('#simple-next').disabled=false;
  }catch(e){if(current()){draft.editions=[];delete draft.edition;$('#simple-error').textContent=e.message;}}
}

function softwareFields(d){
  const platform=d.os==='windows'?'windows':'linux';
  const saved=(mg?.tasks||[]).filter(t=>t.kind&&t.platform===platform);
  return `<p>${d.existing?'Existing machines need WinRM or SSH already enabled.':d.os==='windows'?'WinRM and guest tools are set up automatically on new machines.':'SSH and guest tools are set up automatically on new machines.'}</p>`+
    (d.os==='windows'?wc('chocolatey','Set up Chocolatey',d.chocolatey):'')+
    `<label>Packages to install<textarea name="packages" placeholder="${d.os==='windows'?'7zip, git, notepadplusplus':'curl, git, nginx'}">${esc(d.packages||'')}</textarea><small>Separate package names with commas or spaces.${d.os==='windows'?' Chocolatey is included automatically when you choose packages.':''}</small></label>`+
    (d.os==='windows'?'<fieldset><legend>Windows Server features</legend>'+simpleOptions.features.map(f=>`<label class="check"><input type="checkbox" name="feature" value="${esc(f.id)}" ${(d.features||[]).includes(f.id)?'checked':''}>${esc(f.name)}</label>`).join('')+'<p class="subtle">Machines may restart to finish installing a feature.</p></fieldset>':'')+
    '<fieldset><legend>Your saved scripts</legend>'+saved.map(t=>`<label class="check"><input type="checkbox" name="saved_script" value="${esc(t.id)}" ${(d.scripts||[]).includes(t.id)?'checked':''}>${esc(t.name)}</label>`+Object.entries(t.parameters||{}).map(([key,label])=>`<label>${esc(label)}<input data-script-param="${esc(t.id+':'+key)}" value="${esc(d.script_params?.[t.id+':'+key]||'')}"></label>`).join('')+`<details><summary>View ${esc(t.name)}</summary><pre>${esc(t.body)}</pre></details>`).join('')+(saved.length?'':'<p>No saved scripts yet. Add them in Script library and they will appear here.</p>')+'</fieldset>';
}

function selectedSoftware(d){
  const tasks=[];const windows=d.os==='windows';const packages=(d.packages||'').split(/[\s,]+/).filter(Boolean);
  if(windows&&(d.chocolatey||packages.length))tasks.push({id:'install-chocolatey',params:{}});
  packages.forEach(p=>tasks.push({id:windows?'chocolatey-package':'linux-package',params:{package:p}}));
  if(windows)(d.features||[]).forEach(f=>tasks.push({id:'windows-feature',params:{feature:f}}));
  (d.scripts||[]).forEach(id=>{const t=mg.tasks.find(t=>t.id===id);tasks.push({id,params:Object.fromEntries(Object.keys(t.parameters||{}).map(k=>[k,d.script_params?.[id+':'+k]||'']))})});
  return tasks;
}

function drawSimple(){
  const d=simpleDraft;if(!d)return;
  $('#simple-title').textContent=simpleStep===2?'Ready to create':'Create '+(d.os==='windows'?'Windows Server':'Ubuntu')+' '+(d.purpose==='template'?'template':'machines');
  $('#simple-steps').innerHTML=['Machines','Software','Create'].map((s,i)=>`<li ${i===simpleStep?'aria-current="step"':''}>${i+1}. ${s}</li>`).join('');
  $('#simple-back').hidden=simpleStep===0;$('#simple-next').textContent=simpleStep===2?'Create '+simplePlan.machines.length+(d.purpose==='template'?' template':' machine'+(simplePlan.machines.length===1?'':'s')):'Continue';
  let html='';
  if(simpleStep===0){
    const dest=simpleOptions.destinations.find(n=>n.node===d.node);
    html='<div class="form-grid">'+ws('purpose','What do you want to create?',[['vm','Working machines'],['template','A reusable template']],d.purpose);
    if(d.purpose==='vm')html+=wf('count','How many machines?',d.count,'number','required min="1" max="20"');
    html+=wf('name','Name'+(Number(d.count)>1?' (we add -01, -02…)':''),d.name,'text','required maxlength="12" pattern="[A-Za-z][A-Za-z0-9-]{0,11}"');
    if(d.os==='windows'){
      const sources=simpleSources();if(!sources.some(s=>s.id===d.source))d.source=sources[0]?.id||'';
      html+=ws('source','Windows Server ISO',sources.map(s=>[s.id,s.label]),d.source)+ws('edition','Windows edition',(d.editions||[]).map(e=>[e.index,e.label]),d.edition)+
        wf('password','Administrator password',d.password,'password','required minlength="12" maxlength="128" autocomplete="new-password"')+'<p class="wide subtle">We save this login securely, so you can run scripts later. Use upper and lower case, a number and a symbol.</p>';
    }else html+=ws('profile','Ubuntu version',Object.entries(simpleOptions.profiles),d.profile)+wf('username','Login name',d.username,'text','required pattern="[a-z_][a-z0-9_-]{0,31}"')+'<p class="wide">We use the official ready-to-boot Ubuntu image. No installer or ISO selection is needed.</p>';
    html+=`</div><details ${d.os==='windows'&&!d.management_cidr?'open':''}><summary>Placement and hardware <span class="subtle">${esc(d.node)} · ${esc(d.storage)} · ${esc(d.bridge)}</span></summary><div class="form-grid">`+
      ws('node','Proxmox server',simpleOptions.destinations.map(n=>[n.node,n.node]),d.node)+ws('storage','Disk storage',dest.storage_choices.map(s=>[s,s]),d.storage)+ws('bridge','Network',dest.bridges.map(b=>[b,b]),d.bridge)+ws('iso_storage','Setup media storage',dest.iso_storages.map(s=>[s,s]),d.iso_storage)+
      wf('cores','Processors',d.cores,'number','min="1" max="128" required')+wf('memory_gb','Memory (GB)',d.memory_gb,'number',`min="${d.os==='windows'?4:1}" max="1024" required`)+wf('disk','Disk size (GB)',d.disk,'number',`min="${d.os==='windows'?64:8}" max="4096" required`);
    if(d.os==='windows')html+=ws('agent_iso','Windows guest tools',dest.driver_isos.map(s=>[s,s.split('/').pop()]),d.agent_iso)+wf('management_cidr','Allow remote management from this network',d.management_cidr,'text','required')+ws('locale','Language / keyboard',[['en-GB','English (UK)'],['en-US','English (US)'],['de-DE','German'],['fr-FR','French'],['es-ES','Spanish']],d.locale)+ws('timezone','Time zone',[['GMT Standard Time','London'],['UTC','UTC'],['W. Europe Standard Time','Central Europe'],['Eastern Standard Time','US Eastern'],['Pacific Standard Time','US Pacific']],d.timezone)+wc('rdp','Enable Remote Desktop',d.rdp);
    html+='</div></details>';
  }else if(simpleStep===1)html=softwareFields(d);
  else{
    const p=simplePlan;html=`<h3>${esc(p.edition)}</h3><ul>${p.machines.map(m=>`<li><strong>${esc(m.name)}</strong> — ${m.cores} processors, ${m.memory/1024} GB memory, ${m.disk} GB disk</li>`).join('')}</ul><h3>Build steps</h3><ol><li>Create the machines on ${esc(p.machines[0].node)} using ${esc(p.machines[0].storage)} and ${esc(p.machines[0].bridge)}.</li><li>Install the OS and configure ${esc(p.remote_access)}${p.management_cidr?' for '+esc(p.management_cidr):''}.</li><li>Save each machine’s login and connection securely.</li>${p.tasks.map(t=>`<li>${esc(t.name)} ${esc(Object.values(t.params).join(', '))}</li>`).join('')}${p.purpose==='template'?'<li>Generalize, shut down and convert to a reusable template.</li>':''}</ol><details><summary>Installation details</summary><ul class="subtle">${p.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></details>`+
      wc('confirmed',d.os==='windows'?'Create these new machines and install this software. I am authorised to use this Windows image and accept its licence.':'Create these new machines and install this software.')+(p.dangerous?wc('risk_confirmed','I have reviewed and trust the selected scripts.'):'');
  }
  $('#simple-fields').innerHTML=html;
  if(simpleStep===0){
    const driver=$('#simple-form [name=agent_iso]');if(driver)driver.onchange=()=>{collectSimple();d.agent_manual=true};
    for(const name of ['purpose','node','source']){const field=$(`#simple-form [name=${name}]`);if(field)field.onchange=async()=>{
      collectSimple();if(name==='purpose'&&d.purpose==='template')d.count=1;
      if(name==='node'){const dest=simpleOptions.destinations.find(n=>n.node===d.node);for(const k of ['storage','iso_storage','bridge','management_cidr','agent_iso'])d[k]=dest[k];d.agent_manual=false;d.source='';}
      if(name==='node'||name==='source'){d.editions=[];delete d.edition;}drawSimple();if(d.os==='windows'&&name!=='purpose')await loadEditions();
    }}
  }
}

async function openSimpleApply(preselected=[],taskId=''){
  if(!state){view='server';render();return}
  if(!preselected.length){
    mgDialog('Choose machines',targetFields(),async()=>{
      const selected=new Set(selectedTargets().map(machineKey));
      const guests=mgGuests().filter(g=>selected.has(machineKey(g)));
      if(!guests.length)throw Error('Select at least one machine.');
      await openSimpleApply(guests,taskId);
    },'Choose software');
    return;
  }
  const serverId=serverInfo.id,fromPicker=$('#manage-dialog').open;
  const targets=preselected.map(({node,vmid})=>({node,vmid}));
  if(targets.length>100)throw Error('Choose up to 100 machines per run.');
  await loadManagement();
  const bindings=targets.map(t=>mg.connections.find(c=>c.node===t.node&&c.vmid===t.vmid));
  const missing=targets.filter((_,i)=>!bindings[i]);
  const platforms=new Set(bindings.filter(Boolean).map(c=>c.platform));
  const script=mg.tasks.find(t=>t.id===taskId);
  if(script)platforms.add(script.platform);
  const software=await api('simple/software',missing.length?{targets:missing}:undefined);
  if(serverInfo.id!==serverId||!serverInfo.connected||(fromPicker&&!$('#manage-dialog').open))return;
  const hints=software.platforms||{};
  for(const t of missing){
    const hint=hints[machineKey(t)];
    if(hint&&(targets.length>1||!script))platforms.add(hint);
  }
  if(platforms.size>1)throw Error('These targets or scripts use different operating systems. Select Windows and Linux machines in separate runs.');
  simpleOptions={...(simpleOptions||{}),features:software.features};
  const platform=[...platforms][0];
  const d={os:platform==='windows'?'windows':platform==='linux'?'ubuntu':'',packages:'',scripts:taskId?[taskId]:[],features:[],script_params:{},existing:true};
  const osChoices=[['','Choose the guest OS'],['windows','Windows'],['ubuntu','Linux']];
  const summary=preselected.map(g=>`<li><strong>${esc(g.name||'VM '+g.vmid)}</strong> · ${esc(g.node)} / ${esc(g.vmid)}</li>`).join('');
  mgDialog('Run software',`<ul class="run-targets" aria-label="Selected machines">${summary}</ul>`+
    ws('os','Guest operating system',osChoices,d.os)+'<div id="simple-software"></div><div id="guest-login"></div>',async values=>{
    if(!values.os)throw Error('Choose the guest operating system.');
    Object.assign(d,values);
    d.features=[...$('#manage-form').querySelectorAll('[name=feature]:checked')].map(i=>i.value);
    d.scripts=[...$('#manage-form').querySelectorAll('[name=saved_script]:checked')].map(i=>i.value);
    $('#manage-form').querySelectorAll('[data-script-param]').forEach(i=>{d.script_params[i.dataset.scriptParam]=i.value});
    const tasks=selectedSoftware(d);
    if(!tasks.length)throw Error('Choose a package, feature or saved script.');
    if(missing.length&&!values.credential_id&&!values.guest_password)throw Error('Choose a saved guest login or enter a username and password.');
    const formState=mgSave;
    const reviewed=await api('simple/existing-plan',{targets,tasks,credential_id:values.credential_id,
      credential:values.guest_password?{platform:d.os==='windows'?'windows':'linux',username:values.guest_username,password:values.guest_password}:undefined});
    if(mgSave!==formState||serverInfo.id!==serverId)return;
    mgPlan=reviewed;reviewOperation();
  },'Review run');
  function showPlatform(){
    $('#simple-software').innerHTML=d.os?softwareFields(d):'';
    const login=$('#guest-login');
    if(!d.os){login.replaceChildren();return}
    if(!missing.length){login.textContent='Using the saved guest connections.';return}
    const choices=mgCreds(d.os==='windows'?'windows':'linux');
    choices[0]=['','Enter a new login'];
    login.innerHTML='<fieldset><legend>Guest login</legend><p>Proxmate will read the address and connection details from the guest agent.</p>'+
      (choices.length>1?mgSelect('credential_id','Login',choices):'')+
      '<div class="form-grid" id="inline-login">'+wf('guest_username','Username',d.os==='windows'?'Administrator':'ubuntu')+
      wf('guest_password','Password','','password','autocomplete="new-password"')+'</div></fieldset>';
    const saved=login.querySelector('[name=credential_id]');
    if(saved)saved.onchange=()=>{
      const fields=$('#inline-login');
      fields.hidden=Boolean(saved.value);
      fields.querySelectorAll('input').forEach(input=>{input.disabled=fields.hidden});
    };
  }
  const select=$('#manage-fields [name=os]');
  select.disabled=Boolean(script||bindings.some(Boolean));
  // A disabled select is omitted from FormData. The form retains the known platform.
  if(select.disabled)select.insertAdjacentHTML('afterend',`<input type="hidden" name="os" value="${esc(d.os)}">`);
  select.onchange=()=>{d.os=select.value;d.features=[];d.scripts=[];showPlatform()};
  showPlatform();
}

document.addEventListener('click',async e=>{
  const button=e.target.closest('button');if(!button)return;
  try{
    if(button.dataset.simple==='apply'){
      const targets=button.dataset.id?mgGuests().filter(g=>g.vmid===Number(button.dataset.id)):
        button.id==='run-selected'?mgGuests().filter(g=>machineFilters.selected.has(machineKey(g))):[];
      if(button.dataset.id&&!targets.length)return notice('This machine is no longer in the inventory. Refresh to see available machines.');
      await openSimpleApply(targets);
    }
    else if(button.dataset.simple){
      if(button.closest('.create-menu'))button.closest('.create-menu').open=false;
      await openSimple(button.dataset.simple,button.dataset.purpose||'vm',button.dataset.filename||'');
    }
    if(button.dataset.simpleTask)await openSimpleApply([],button.dataset.simpleTask);
  }catch(err){notice(err.message)}
});
$('#simple-close').onclick=()=>$('#simple-dialog').close();
$('#simple-dialog').addEventListener('close',()=>{simpleDraft=null;simplePlan=null;$('#simple-fields').replaceChildren();$('#simple-error').textContent=''});
$('#simple-back').onclick=()=>{collectSimple();simpleStep--;drawSimple()};
$('#simple-form').onsubmit=async e=>{
  e.preventDefault();if(simpleBusy)return;simpleBusy=true;const draft=simpleDraft;
  $('#simple-next').disabled=true;$('#simple-error').textContent='';
  try{
    collectSimple();
    if(simpleStep===0){if(draft.os==='windows'&&!draft.edition)throw Error('Choose a readable Windows Server ISO and edition.');simpleStep=1;drawSimple()}
    else if(simpleStep===1){const plan=await api('simple/plan',{...draft,memory:Number(draft.memory_gb)*1024,tasks:selectedSoftware(draft)});if(simpleDraft!==draft)return;simplePlan=plan;simpleStep=2;drawSimple()}
    else{if(!draft.confirmed)throw Error('Confirm the machines and software before creating.');await api('simple/run',{plan_id:simplePlan.id,confirmed:true,risk_confirmed:!!draft.risk_confirmed});$('#simple-dialog').close();view='jobs';await refresh();notice('Your machines are queued. Each job installs the OS, prepares remote access and applies your software.');}
  }catch(err){if(simpleDraft===draft)$('#simple-error').textContent=err.message}
  finally{simpleBusy=false;$('#simple-next').disabled=false}
};
