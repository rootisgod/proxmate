/* Everyday flows. Secrets and drafts stay in memory and are cleared on close. */
let simpleDraft=null,simpleStep=0,simpleOptions=null,simplePlan=null,simpleBusy=false,simpleSelected=[];

function simpleRender(){
  if(!['home','scripts','machines'].includes(view))return false;
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  if(view==='home'){
    $('#content').innerHTML=title('What would you like to do?','Create machines, add software, and get on with your work.')+
      `<div class="cards simple-cards"><article class="card"><span class="pill">WINDOWS SERVER</span><h2>Build Windows machines</h2><p>Pick an ISO and edition. We set up remote access and install your chosen software.</p><button class="primary" data-simple="windows">Create Windows machines</button></article>
      <article class="card"><span class="pill">UBUNTU</span><h2>Build Ubuntu machines</h2><p>Choose a release. We prepare the image, SSH access and your software automatically.</p><button class="primary" data-simple="ubuntu">Create Ubuntu machines</button></article>
      <article class="card"><span class="pill">YOUR EXISTING MACHINES</span><h2>Install something new</h2><p>Choose machines, select software or a saved script, then run it.</p><button data-simple="apply">Run software &amp; scripts</button></article></div>
      <div class="panel simple-summary"><h2>Your machines</h2><p>${(state?.resources||[]).filter(r=>r.type==='qemu'&&!r.template).length} machines &middot; ${jobs.filter(j=>['queued','running'].includes(j.status)).length} jobs in progress</p><button data-view="machines">View machines</button> <button data-view="jobs">View progress</button></div>`;
  }else if(view==='machines'){
    const guests=(state?.resources||[]).filter(r=>r.type==='qemu'&&!r.template);
    $('#content').innerHTML=title('Machines','Create a machine or choose where to run your software.','<div class="row-actions"><button class="primary" data-simple="windows">New Windows machines</button><button data-simple="ubuntu">New Ubuntu machines</button></div>')+
      '<p><button data-simple="apply">Run software on several machines</button></p>'+table(['Machine','Status','Resources','Actions'],guests.map(g=>`<tr><td><strong>${esc(g.name)}</strong><small>${esc(g.node)} / ${g.vmid}</small></td><td><span class="pill ${esc(g.status)}">${esc(g.status)}</span></td><td>${g.maxcpu} processors / ${(g.maxmem/1073741824).toFixed(0)} GB</td><td><div class="row-actions"><button data-simple="apply" data-id="${g.vmid}">Run scripts</button>${mgButton('Power / settings','manage-vm',g.vmid)}<details><summary>More</summary>${mgButton('Connection','connection',g.vmid)}${mgButton('Snapshots','snapshots',g.vmid)}</details></div></td></tr>`));
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
    simpleOptions=await api('simple/options');await loadManagement();
    const dest=simpleOptions.destinations[0];
    simpleDraft={os,purpose,count:1,name:os==='windows'?'win-server':'ubuntu',profile:'ubuntu-24.04',username:'ubuntu',password:'',
      node:dest.node,storage:dest.storage,iso_storage:dest.iso_storage,bridge:dest.bridge,management_cidr:dest.management_cidr,agent_iso:dest.agent_iso,
      cores:2,memory_gb:os==='windows'?4:2,disk:os==='windows'?64:32,locale:'en-GB',timezone:'GMT Standard Time',packages:'',chocolatey:false,features:[],scripts:[],script_params:{}};
    simpleStep=0;simplePlan=null;simpleSelected=[];
    if(filename)simpleDraft.source='library:'+filename;
    drawSimple();$('#simple-dialog').showModal();if(os==='windows')await loadEditions();
  }catch(e){notice(e.message)}
}

function simpleSources(){
  const dest=simpleOptions.destinations.find(n=>n.node===simpleDraft.node);
  return [...dest.isos,...simpleOptions.local_isos].filter(i=>/server/i.test(i.label)&&!/sql/i.test(i.label)).sort((a,b)=>(/2025/.test(b.label)?1:0)-(/2025/.test(a.label)?1:0)||a.label.localeCompare(b.label));
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
  const draft=simpleDraft,source=draft.source;
  if(!source)return;
  $('#simple-next').disabled=true;$('#simple-error').textContent='Reading Windows editions from your ISO…';
  try{
    const result=await api('simple/editions',{node:draft.node,source});
    if(simpleDraft!==draft||draft.source!==source)return;
    draft.editions=result.editions;
    draft.edition=String((result.editions.find(e=>e.desktop&&/standard/i.test(e.name))||result.editions[0]).index);
    drawSimple();$('#simple-error').textContent='';
  }catch(e){if(simpleDraft===draft){draft.editions=[];$('#simple-error').textContent=e.message;}}
  finally{if(simpleDraft===draft)$('#simple-next').disabled=false;}
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
    html+='</div><details><summary>Advanced settings <span class="subtle">— we have chosen these for you</span></summary><div class="form-grid">'+
      ws('node','Proxmox server',simpleOptions.destinations.map(n=>[n.node,n.node]),d.node)+ws('storage','Disk storage',dest.storage_choices.map(s=>[s,s]),d.storage)+ws('bridge','Network',dest.bridges.map(b=>[b,b]),d.bridge)+ws('iso_storage','Setup media storage',dest.iso_storages.map(s=>[s,s]),d.iso_storage)+
      wf('cores','Processors',d.cores,'number','min="1" max="128" required')+wf('memory_gb','Memory (GB)',d.memory_gb,'number',`min="${d.os==='windows'?4:1}" max="1024" required`)+wf('disk','Disk size (GB)',d.disk,'number',`min="${d.os==='windows'?64:8}" max="4096" required`);
    if(d.os==='windows')html+=ws('agent_iso','Windows guest tools',dest.driver_isos.map(s=>[s,s.split('/').pop()]),d.agent_iso)+wf('management_cidr','Allow remote management from this network',d.management_cidr,'text','required')+ws('locale','Language / keyboard',[['en-GB','English (UK)'],['en-US','English (US)'],['de-DE','German'],['fr-FR','French'],['es-ES','Spanish']],d.locale)+ws('timezone','Time zone',[['GMT Standard Time','London'],['UTC','UTC'],['W. Europe Standard Time','Central Europe'],['Eastern Standard Time','US Eastern'],['Pacific Standard Time','US Pacific']],d.timezone)+wc('rdp','Enable Remote Desktop',d.rdp);
    html+='</div></details>';
  }else if(simpleStep===1)html=softwareFields(d);
  else{
    const p=simplePlan;html=`<h3>${esc(p.edition)}</h3><ul>${p.machines.map(m=>`<li><strong>${esc(m.name)}</strong> — ${m.cores} processors, ${m.memory/1024} GB memory, ${m.disk} GB disk</li>`).join('')}</ul><h3>We will do the setup</h3><ol><li>Create the machines on ${esc(p.machines[0].node)} using ${esc(p.machines[0].storage)} and ${esc(p.machines[0].bridge)}.</li><li>Install the OS and configure ${esc(p.remote_access)}${p.management_cidr?' for '+esc(p.management_cidr):''}.</li><li>Save each machine’s login and connection securely.</li>${p.tasks.map(t=>`<li>${esc(t.name)} ${esc(Object.values(t.params).join(', '))}</li>`).join('')}${p.purpose==='template'?'<li>Generalize, shut down and convert to a reusable template.</li>':''}</ol><ul class="subtle">${p.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`+
      wc('confirmed',d.os==='windows'?'Create these new machines and install this software. I am authorised to use this Windows image and accept its licence.':'Create these new machines and install this software.')+(p.dangerous?wc('risk_confirmed','I have reviewed and trust the selected scripts.'):'');
  }
  $('#simple-fields').innerHTML=html;
  if(simpleStep===0){
    const driver=$('#simple-form [name=agent_iso]');if(driver)driver.onchange=()=>{collectSimple();d.agent_manual=true};
    for(const name of ['purpose','node','source']){const field=$(`#simple-form [name=${name}]`);if(field)field.onchange=async()=>{
      collectSimple();if(name==='purpose'&&d.purpose==='template')d.count=1;
      if(name==='node'){const dest=simpleOptions.destinations.find(n=>n.node===d.node);for(const k of ['storage','iso_storage','bridge','management_cidr','agent_iso'])d[k]=dest[k];d.source='';}
      if(name==='node'||name==='source'){d.editions=[];delete d.edition;}drawSimple();if(d.os==='windows'&&name!=='purpose')await loadEditions();
    }}
  }
}

async function openSimpleApply(preselected=[],taskId=''){
  if(!state){view='server';render();return}
  await loadManagement();const software=await api('simple/software');simpleOptions={...(simpleOptions||{}),features:software.features};
  const platform=taskId?mg.tasks.find(t=>t.id===taskId)?.platform:mg.connections.find(c=>preselected.some(t=>t.node===c.node&&t.vmid===c.vmid))?.platform||'windows';
  let d={os:platform==='linux'?'ubuntu':'windows',packages:'',scripts:taskId?[taskId]:[],features:[],script_params:{},existing:true};
  mgDialog('Run software & scripts','<div class="form-grid">'+ws('os','Operating system',[['windows','Windows Server'],['ubuntu','Ubuntu / Linux']],d.os)+'</div>'+targetFields(preselected)+'<div id="simple-software"></div><details><summary>Login (only needed for machines not yet connected)</summary>'+mgSelect('credential_id','Use a saved login',mgCreds())+'<p>Or enter a login to save securely:</p>'+wf('guest_username','Username',d.os==='windows'?'Administrator':'ubuntu')+wf('guest_password','Password','','password','autocomplete="new-password"')+'</details><p>Saved connections are reused. For new connections, we discover the guest address and public trust information through its guest agent.</p>',async values=>{
    d={...d,...values};d.features=[...$('#manage-form').querySelectorAll('[name=feature]:checked')].map(i=>i.value);d.scripts=[...$('#manage-form').querySelectorAll('[name=saved_script]:checked')].map(i=>i.value);
    const tasks=selectedSoftware(d);if(!tasks.length)throw Error('Choose a package, feature or saved script.');
    mgPlan=await api('simple/existing-plan',{targets:selectedTargets(),tasks,credential_id:values.credential_id,credential:values.guest_password?{platform:d.os==='windows'?'windows':'linux',username:values.guest_username,password:values.guest_password}:undefined});reviewOperation();
  },'Review and run');
  $('#simple-software').innerHTML=softwareFields(d);
  $('#manage-fields [name=os]').onchange=e=>{d.os=e.target.value;d.features=[];d.scripts=[];$('#manage-fields [name=guest_username]').value=d.os==='windows'?'Administrator':'ubuntu';$('#simple-software').innerHTML=softwareFields(d)};
}

document.addEventListener('click',async e=>{
  const button=e.target.closest('button');if(!button)return;
  try{
    if(button.dataset.simple==='apply')await openSimpleApply(button.dataset.id?[mgGuests().find(g=>g.vmid===Number(button.dataset.id))]:[]);
    else if(button.dataset.simple)await openSimple(button.dataset.simple,button.dataset.purpose||'vm',button.dataset.filename||'');
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
