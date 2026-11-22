const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state=null,jobs=[],view='machines',csrf='',action='',fixed={};
function requestId(){
  const bytes=crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
}
async function api(path,body,extraHeaders={},responseType='json'){
  const selectedId=typeof serverInfo!=='undefined'?(serverInfo?.id??null):null;
  const headers=body?{'Content-Type':'application/json','X-CSRF-Token':csrf,'X-Proxmate-Server':selectedId||'','X-Request-ID':requestId(),...extraHeaders}:{...extraHeaders};
  let r,data;
  try{
    r=await fetch('/api/'+path,{method:body?'POST':'GET',headers,body:body?JSON.stringify(body):undefined});
    data=r.ok&&responseType==='blob'?await r.blob():await r.json();
  }catch{
    const id=r?.headers?.get('X-Request-ID')||headers['X-Request-ID'];
    const error=Error('The dashboard could not reach the server. Check the connection and retry.'+(id?' Request '+id:''));
    error.transport=true;throw error;
  }
  if(!path.startsWith('server')&&typeof serverInfo!=='undefined'&&selectedId!==(serverInfo?.id??null))throw Error('Server changed. Reload before continuing.');
  if(!r.ok){
    if(r.status===401&&!$('#login').open)$('#login').showModal();
    const ref=r.headers?.get('X-Request-ID');
    throw Error((data.error||'Request failed')+(ref&&r.status>=500?' · Request '+ref:''));
  }
  return data;
}
function notice(message){$('#notice').textContent=message;$('#notice').hidden=false}

function setButtonBusy(button,busy){
  button.disabled=busy;
  if(busy)button.setAttribute('aria-busy','true');
  else button.removeAttribute('aria-busy');
}

async function withBusyButton(button,work){
  if(button.disabled || button.hasAttribute('aria-busy'))return;
  setButtonBusy(button,true);
  try{return await work()}
  finally{setButtonBusy(button,false)}
}

let refreshVersion=0;
function needsInventoryDetails(){
  return ['overview','templates','backups','ansible','tasks','recipes','recovery','versions'].includes(view) ||
    Boolean(state?.details && ($('#wizard[open], #manage-dialog[open]') || ($('#editor').open&&!$('#job-log'))));
}
async function readInventory(){
  if(needsInventoryDetails())return api('state');
  const resources=await api('simple/resources');
  return {resources,nodes:resources.filter(r=>r.type==='node')};
}
function loadInventoryDetails(){
  const current=state;
  $('#content').innerHTML='<p role="status">Loading inventory details…</p>';
  if(current.loadingDetails)return;
  const revision=refreshVersion;
  current.loadingDetails=true;
  api('state').then(inventory=>{
    if(state!==current||revision!==refreshVersion)return;
    state=inventory;
    if(needsInventoryDetails())render();
  }).catch(err=>{
    if(state!==current||revision!==refreshVersion)return;
    notice(err.message);
    if(needsInventoryDetails())$('#content').innerHTML='<p>Inventory details are unavailable. Use Refresh to retry.</p>';
  }).finally(()=>{delete current.loadingDetails});
}
async function refresh(){
  const revision=++refreshVersion;
  try{
    const selected=await api('server');if(revision!==refreshVersion)return;
    if(serverInfo&&(serverInfo.id!==selected.id||serverInfo.connected!==selected.connected)){
      resetMachineFilters();if(view==="machine")view="machines";state=null;mg=null;lcData=null;lcLoaded=0;mgPlan=null;
      document.querySelectorAll('dialog[open]').forEach(d=>{if(d.id!=='login')d.close()});$('#content').innerHTML='';
    }
    serverInfo=selected;
    const results=await Promise.all([selected.connected?readInventory():Promise.resolve(null),api('jobs?summary=1')]);
    if(revision!==refreshVersion)return;
    [state,jobs]=results;observeJobs(jobs,selected.connected?selected.id:null);$('#connection').className=state?'online':'';
    const connectionLabel=$('#connection-label');
    connectionLabel.textContent=state?(selected.url||'Connected').replace(/^https?:\/\//,''):'Disconnected';
    connectionLabel.title=state?'Connected to '+selected.url:'Disconnected';
    if(view==='machines'&&state&&typeof restoreMachineLocation==='function'&&restoreMachineLocation())return;
    render();
  }catch(e){if(revision!==refreshVersion)return;state=null;$('#connection').className='';$('#connection-label').textContent='Connection unavailable';$('#connection-label').removeAttribute('title');notice(e.message);render()}
}

function workspaceHeading(name,sub){
  const icon=$('#workspace-nav button[aria-current=page] .bi')?.className||'bi bi-grid';
  return `<div class="page-heading"><span class="page-symbol ${esc(icon)}" aria-hidden="true"></span><div><h1>${name}</h1><p>${sub}</p></div></div>`;
}
function title(name,sub,button=''){return `<div class="title">${workspaceHeading(name,sub)}${button}</div>`}
function btn(label,a,id){return `<button data-action="${a}" ${id?`data-id="${id}"`:''}>${label}</button>`}
function table(head,rows){return `<div class="panel table-wrap"><table><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${head.length}" class="empty">Nothing here yet. Your next operation will appear here.</td></tr>`}</tbody></table></div>`}
function guestRows(items,template=false){return items.map(r=>`<tr><td><strong>${esc(r.name)}</strong><small>${r.type.toUpperCase()} · ${r.vmid}</small></td><td><span class="pill ${esc(r.status)}">${template?'template':esc(r.status)}</span></td><td>${esc(r.node)}</td><td>${r.maxcpu} cores · ${(r.maxmem/1073741824).toFixed(0)} GB</td><td><div class="row-actions">${r.type==='qemu'?(template?'<button data-clone-template="'+esc(r.vmid)+'">Clone →</button>':`<button data-simple="apply" data-id="${r.vmid}">Run script</button>`+mgButton('Power / settings','manage-vm',r.vmid)+mgButton('Connection','connection',r.vmid)+mgButton('Snapshots','snapshots',r.vmid)+btn('Backup','backup',r.vmid)+btn('Start','start',r.vmid)+btn('Finish setup','finish-setup',r.vmid)+btn('Template','convert-template',r.vmid)):''}</div></td></tr>`)}
function activity(rows=jobs){return table(['Operation','Status','Submitted','Details'],rows.map(j=>`<tr><td>${esc(j.operation||j.action)}<small>${esc(j.target?.name||j.target?.label||j.target?.filename||j.target?.vmid||j.target?.node||'')}</small></td><td><span class="pill ${j.status}">${j.status}</span></td><td>${new Date(j.created*1000).toLocaleString()}</td><td><button data-log="${j.id}">View log</button></td></tr>`))}
function workspaceRoute(){
  const name=location.hash.slice(1);
  return Array.from(document.querySelectorAll('#workspace-nav [data-view]')).some(b=>b.dataset.view===name)?name:null;
}

function workspaceLocation(active=view){
  const current=active==='machine'||active==='home'?'machines':active;
  const links=document.querySelectorAll('#workspace-nav [data-view]');
  for(const link of links){
    const selected=link.dataset.view===current;
    link.classList.toggle('active',selected);
    if(selected){
      link.setAttribute('aria-current','page');
      const group=link.closest('[role=group]')?.getAttribute('aria-label')||'More tools';
      $('#content').dataset.area=group;
      $('#workspace-group').textContent=group;
      $('#workspace-location').textContent=active==='machine'?'Machine details':link.textContent.trim();
      document.title=$('#workspace-location').textContent+' · Proxmate';
      const tools=link.closest('details');
      if(tools)tools.open=true;
    }else link.removeAttribute('aria-current');
  }
}

function navigateWorkspace(name){
  const previous=view;
  view=name;
  if(previous!==name||location.hash!=='#'+name)history.pushState({},'','#'+name);
  render();
  if(previous!==name){
    $('#content').focus({preventScroll:true});
    document.documentElement.scrollTop=0;
  }
}

window.addEventListener('hashchange',()=>{
  if(location.hash.startsWith('#machine/')||location.hash==='#content')return;
  view=workspaceRoute()||'machines';
  render();
});

async function openMediaTool(filename,node,advanced){
  const server=serverInfo?.id;
  try{
    const inventory=await api('state');
    if(server!==serverInfo?.id||view!=='media')return;
    if(!inventory.details[node])throw Error('The selected Proxmox server is unavailable. Refresh the ISO library.');
    state=inventory;
    if(advanced)await openWizard('linux-iso',filename,'vm',node);
    else openAction('upload-iso',null,filename,node);
  }catch(error){notice(error.message)}
}
const openMediaUpload=(filename,node)=>openMediaTool(filename,node,false);
const openMediaAdvanced=(filename,node)=>openMediaTool(filename,node,true);

let activityFilter='all';
function renderActivity(){
  const active=['running','waiting','queued'];
  const attention=['failed','interrupted'];
  const matches=(job,key)=>key==='all'||(key==='active'?active:attention).includes(job.status);
  const filters=[['all','All jobs'],['active','In progress'],['attention','Needs attention']];
  const filtered=jobs.filter(j=>matches(j,activityFilter));
  $('#content').innerHTML=title('Activity','Track builds and script runs. Open a job to inspect its result.')+
    '<div class="activity-filters inventory-filters" role="group" aria-label="Filter jobs">'+filters.map(([key,label])=>
      `<button data-activity-filter="${key}" aria-pressed="${key===activityFilter}">${label} <span>${jobs.filter(j=>matches(j,key)).length}</span></button>`).join('')+
    '</div>'+(filtered.length?activity(filtered):'<div class="build-empty"><span class="bi bi-activity" aria-hidden="true"></span><strong>No jobs in this view</strong></div>');
  for(const filter of document.querySelectorAll('[data-activity-filter]'))filter.onclick=()=>{
    activityFilter=filter.dataset.activityFilter;renderActivity();
    document.querySelector(`[data-activity-filter="${activityFilter}"]`).focus({preventScroll:true});
  };
}
function render(){
  const showServer=!state&&!['scripts','tasks','credentials','jobs'].includes(view);
  workspaceLocation(showServer?'server':view);
  if(workspaceRoute()&&workspaceRoute()!==view&&view!=='machine')history.replaceState({},'','#'+view);
  if(view==='jobs'){renderActivity();return}
  if(state&&!state.details&&needsInventoryDetails()){loadInventoryDetails();return}
  if(typeof serverRender==='function'&&serverRender())return;if(view==='machine'&&typeof renderMachine==='function'){renderMachine();return}if(typeof simpleRender==='function'&&simpleRender())return;if(typeof lifecycleRender==='function'&&lifecycleRender())return;if(typeof managementRender==='function'&&managementRender())return;if(!state)return;const guests=state.resources.filter(r=>r.vmid),templates=guests.filter(r=>r.template),vms=guests.filter(r=>!r.template);let html='';
if(view==='overview'){html=title('Your lab, at a glance.','Less repetition. More time to build.', '<button class="primary" data-wizard="linux-cloud">＋ Build a machine</button>')+`<div class="stats"><div class="stat"><span>VIRTUAL MACHINES</span><strong>${vms.length}</strong></div><div class="stat"><span>READY TEMPLATES</span><strong>${templates.length}</strong></div><div class="stat"><span>RUNNING GUESTS</span><strong>${vms.filter(r=>r.status==='running').length}</strong></div><div class="stat"><span>ACTIVE JOBS</span><strong>${jobs.filter(j=>['running','queued','waiting'].includes(j.status)).length}</strong></div></div><h2>A shortcut to your next project</h2><div class="cards"><div class="card"><span class="icon">▧</span><h3>Build once. Use again.</h3><p>Create an Ubuntu cloud template, or prepare a Windows or Linux installer VM.</p><button data-wizard="linux-cloud">Linux wizard</button></div><div class="card"><span class="icon">⌘</span><h3>Put your playbook to work.</h3><p>Run Ansible against your inventory and keep the result in one place.</p>${btn('Run a playbook →','ansible')}</div><div class="card"><span class="icon">◷</span><h3>A little peace of mind.</h3><p>Take a compressed VM backup on your Proxmox backup storage.</p>${btn('Take a backup →','backup')}</div></div><h2>Your machines <span class="subtle">${vms.length} guests</span></h2>`+table(['Name','Status','Node','Resources','Actions'],guestRows(vms));}
if(view==='templates')html=title('Templates','A good starting point, every time.', '<button class="primary" data-simple="windows" data-purpose="template">＋ Template wizard</button>')+`<div class="panel"><div class="panel-head"><div><h3>Windows & Linux from ISO</h3><p>Choose Windows Server, Ubuntu, Debian or Rocky Linux. We install your software, prepare the guest and make it reusable.</p></div><button data-simple="windows" data-purpose="template">Windows template</button><button data-simple="ubuntu" data-purpose="template">Ubuntu template</button><button data-simple="debian" data-purpose="template">Debian template</button><button data-simple="rocky" data-purpose="template">Rocky template</button></div></div>`+table(['Name','Status','Node','Resources','Actions'],guestRows(templates,true));
if(view==='backups')html=title('Backups','Snapshot-mode backups, compressed with Zstandard.','<button class="primary" data-action="backup">＋ Take backup</button>')+table(['Backup archive','Node','Size'],Object.entries(state.details).flatMap(([node,d])=>(d.backups||[]).map(b=>`<tr><td>${esc(b.volid)}</td><td>${esc(node)}</td><td>${(b.size/1073741824).toFixed(2)} GB</td></tr>`)))+'<p><button data-view="recovery">Restore, test backups and manage retention</button></p><h2>Backup activity</h2>'+activity(jobs.filter(job=>job.action==='backup'));
if(view==='ansible')html=title('Ansible','Your playbooks. Your inventory. One place to run them.','<button class="primary" data-action="ansible">＋ Run playbook</button>')+`<p>Playbooks run as the Proxmate service user inside the container. Configure SSH credentials and known_hosts for your targets. This is an administrator code-execution feature.</p>`+activity(jobs.filter(job=>job.action==='ansible'));

$('#content').innerHTML=html;document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));}
function options(items,value,label){return items.map(x=>`<option value="${esc(value(x))}">${esc(label(x))}</option>`).join('')}
function select(name,label,items){return `<label>${label}<select name="${name}" required>${items}</select></label>`}
function input(name,label,value,type='text'){return `<label>${label}<input name="${name}" value="${esc(value)}" type="${type}" required></label>`}
function openAction(a,id,filename,selectedNode){if(!state)return;action=a;fixed={};const guest=state.resources.find(r=>r.vmid===Number(id));const node=selectedNode||guest?.node||state.nodes.find(n=>n.status==='online')?.node;const detail=state.details[node];if(!detail){notice('No online Proxmox server is available.');return}let f=select('node','Proxmox server',options(state.nodes.filter(n=>n.status==='online'),x=>x.node,x=>x.node));
const storage=()=>select('storage','VM storage',options(detail.storage.filter(s=>s.active&&s.content.split(',').includes('images')),s=>s.storage,s=>s.storage));
const vm=()=>select('vmid','Virtual machine',options(state.resources.filter(r=>r.type==='qemu'&&!r.template&&r.node===node),r=>r.vmid,r=>`${r.vmid} · ${r.name}`));
if(['linux-template','installer','clone'].includes(a)){f+=input('vmid','New VM ID',state.next_id,'number')+input('name','Name',a==='linux-template'?'proxmate-ubuntu-2404':'my-new-vm')+storage()+input('cores','CPU cores',2,'number')+input('memory','Memory (MB)',a==='installer'?4096:2048,'number');}
if(['linux-template','installer'].includes(a))f+=select('bridge','Network bridge',options(detail.network.filter(n=>n.type==='bridge'),x=>x.iface,x=>x.iface))+input('disk','Disk (GB)',a==='installer'?64:32,'number');
if(a==='linux-template'||a==='clone')f+=input('username','Cloud-init username','ubuntu')+'<label class="wide">SSH public key (needed for cloud-image login)<textarea name="ssh_key" placeholder="ssh-ed25519 AAAA…"></textarea></label>';
if(a==='clone')f+=select('source','Source template',options(state.resources.filter(r=>r.type==='qemu'&&r.template&&r.node===node),r=>r.vmid,r=>`${r.vmid} · ${r.name}`));
if(a==='installer')f+=select('os','Operating system','<option value="linux">Linux</option><option value="windows">Windows Server / Windows 11</option>')+select('iso','Proxmox ISO',options(detail.isos,x=>x.volid,x=>x.volid));
if(a==='ansible')f+='<label class="check wide"><input type="checkbox" name="host_confirmed">If targeting Proxmox: I reviewed this code and intend to run it on the hypervisor itself.</label>';
if(['backup','convert-template','finish-setup','start'].includes(a))f+=vm();
if(a==='backup')f+=select('backup_storage','Backup destination',options(detail.storage.filter(s=>s.active&&s.content.split(',').includes('backup')),s=>s.storage,s=>s.storage));
if(a==='finish-setup')f+='<div class="wide"><p>This detaches and deletes only the Proxmate-generated answer ISO attached to the stopped VM. Remove Windows cached answer files and rotate the setup password before sharing a template.</p><label><input type="checkbox" name="prepared" required>Installation has finished and this VM is shut down. Remove its generated answer media.</label></div>';
if(a==='convert-template')f+='<div class="wide"><p>Template conversion changes this VM into a reusable source. For Windows, run Sysprep /generalize /oobe /shutdown first. For Linux, clean machine identity and cloud-init state, then shut down.</p><label><input type="checkbox" name="prepared" required>I have generalized and shut down this guest.</label></div>';
if(a==='upload-iso'){
  fixed.filename=filename;
  f='<p class="wide">Copy <strong>'+esc(filename)+'</strong> from the Proxmate ISO folder to the storage below. The original stays in the folder.</p>'+f+
    select('storage','Destination ISO storage',options(detail.storage.filter(s=>s.active&&s.content.split(',').includes('iso')),s=>s.storage,s=>s.storage));
}
$('#action-form .modal-footer button').textContent=a==='upload-iso'?'Copy ISO':'Run operation →';
if(a==='ansible')f+=select('target','Run against','<option value="custom">Custom inventory</option><option value="proxmox">Proxmox HYPERVISOR (advanced)</option>')+'<label class="wide">Inventory<textarea name="inventory" required>[lab]\n# 192.168.1.50 ansible_user=ubuntu</textarea></label><label class="wide">Playbook<textarea name="playbook" required rows="12">---\n- name: Check connectivity\n  hosts: all\n  gather_facts: false\n  tasks:\n    - name: Ping guests\n      ansible.builtin.ping:</textarea></label>';
$('#fields').innerHTML='<div class="form-grid">'+f+'</div>';$('#form-title').textContent={'linux-template':'Create Ubuntu template',installer:'Create installer VM',clone:'Clone a template',backup:'Take a backup','convert-template':'Convert to template',ansible:'Run Ansible playbook','upload-iso':'Copy ISO to Proxmox storage',start:'Start virtual machine','finish-setup':'Remove generated answer media'}[a];$('#form-error').textContent='';$('#action-form [name=node]').value=node;if(id){const field=$(`#action-form [name=${a==='clone'?'source':'vmid'}]`);if(field)field.value=id}$('#action-form [name=node]').onchange=e=>{const chosen=e.target.value;$('#editor').close();openAction(a,null,filename,chosen)};$('#editor').showModal();}
async function readJobLog(log){
  if(log.dataset.loading)return;
  log.dataset.loading='true';
  try{
    const job=await api('jobs/'+encodeURIComponent(log.dataset.job));
    if(!log.isConnected||!$('#editor').open)return;
    log.textContent=job.log;
    if(typeof jobConnectionActions==='function')jobConnectionActions(job,log);
    if(typeof jobWindowsActions==='function')jobWindowsActions(job,log);
    jobFollowupActions(job,log);
    log.dataset.updated=String(job.updated);
    log.dataset.status=job.status;
    $('#form-title').textContent=job.action+' · '+job.status;
    $('#form-error').textContent='';
  }catch(err){
    if(log.isConnected&&$('#editor').open)$('#form-error').textContent=err.message;
  }finally{delete log.dataset.loading}
}
function openJobLog(id){
  const log=document.createElement('pre');
  log.id='job-log';log.dataset.job=id;log.textContent='Loading job log…';
  $('#form-title').textContent='Job log';$('#fields').replaceChildren(log);
  $('#form-error').textContent='';$('#editor .modal-footer').hidden=true;
  $('#editor').showModal();readJobLog(log);
}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.view)navigateWorkspace(b.dataset.view);if(b.dataset.action)openAction(b.dataset.action,b.dataset.id);if(b.dataset.upload)openAction('upload-iso',null,b.dataset.upload);if(b.dataset.log)openJobLog(b.dataset.log)});
const navToggle=$('#nav-toggle');
function toggleNav(open){
  navToggle.setAttribute('aria-expanded',String(open));
  document.querySelector('aside').classList.toggle('nav-open',open);
}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&navToggle.getAttribute('aria-expanded')==='true'){
    toggleNav(false);navToggle.focus();
  }
});
navToggle.onclick=()=>toggleNav(navToggle.getAttribute('aria-expanded')==='false');
$('#workspace-nav').addEventListener('click',e=>{
  if(e.target.closest('[data-view]')&&navToggle.getAttribute('aria-expanded')==='true'){
    toggleNav(false);
    $('#content').focus();
  }
});
$('#editor').addEventListener('close',()=>$('.modal-footer').hidden=false);
$('#close').onclick=()=>$('#editor').close();
$('#refresh').onclick=async()=>{
  const button=$('#refresh'),page=view,server=serverInfo?.id;
  if(button.disabled)return;
  setButtonBusy(button,true);
  try{
    await refresh();
    if(view!==page||server!==serverInfo?.id)return;
    if(view==='machine'&&machinePage)await openMachine(machinePage.target);
    else if(view==='media')$('.media-refresh')?.click();
    else if(mg&&[...managementViews,'scripts'].includes(view)){await loadManagement();render()}
  }catch(error){notice(error.message)}
  finally{setButtonBusy(button,false)}
};
$('#logout').onclick=async()=>{await api('logout',{});location.reload()};
$('#login-form').onsubmit=async e=>{
  e.preventDefault();
  const form=e.currentTarget;
  await withBusyButton(form.querySelector('button'),async()=>{
    try{
      const r=await api('login',{password:new FormData(form).get('password')});
      csrf=r.csrf;$('#login').close();form.reset();$('#notice').hidden=true;await refresh();
    }catch(err){$('#login-error').textContent=err.message}
  });
};
$('#action-form').onsubmit=async e=>{
  e.preventDefault();
  const form=e.currentTarget,button=e.submitter||form.querySelector('.modal-footer button');
  await withBusyButton(button,async()=>{
    try{
      const d={action,...fixed,...Object.fromEntries(new FormData(form))};
      if(!d.ssh_key)delete d.ssh_key;
      if(d.prepared)d.prepared=true;
      if(d.host_confirmed)d.host_confirmed=true;
      await api('jobs',d);
      $('#editor').close();view='jobs';notice('Operation queued. Follow its progress below.');await refresh();
    }catch(err){$('#form-error').textContent=err.message}
  });
};
async function startDashboard(){view=workspaceRoute()||view;try{csrf=(await api('session')).csrf;await refresh()}catch{if(!$('#login').open)$('#login').showModal()}}
if(document.readyState==='complete')startDashboard();else document.addEventListener('DOMContentLoaded',startDashboard,{once:true});
let polling=false,lastInventoryRead=Date.now();

async function pollDashboard(){
  if(polling||document.hidden||!state||$('#login').open)return;
  polling=true;
  const revision=refreshVersion;
  try{
    const selected=await api('server');
    if(revision!==refreshVersion)return;
    if(selected.id!==serverInfo?.id||selected.connected!==serverInfo?.connected){await refresh();return}
    const labRun=$('#lab-run');
    if(labRun&&['queued','running','waiting'].includes(labRun.dataset.status))await updateLabRun(labRun);
    if(revision!==refreshVersion)return;
    const previous=jobs.map(j=>j.id+':'+j.status).join(',');
    const nextJobs=await api('jobs?summary=1');
    if(revision!==refreshVersion)return;
    jobs=nextJobs;observeJobs(jobs,selected.id);
    const changed=previous!==jobs.map(j=>j.id+':'+j.status).join(',');
    const log=$('#job-log');
    if(log){
      const current=jobs.find(j=>j.id===log.dataset.job);
      const needsUpdate=current?String(current.updated)!==log.dataset.updated:
        (!log.dataset.status||['queued','running','waiting'].includes(log.dataset.status));
      if(needsUpdate)await readJobLog(log);
      if(revision!==refreshVersion)return;
    }
    if(changed&&view==='jobs'){
      state={resources:state.resources,nodes:state.nodes};
      lastInventoryRead=0;
    }else if(changed){
      const inventory=await readInventory();
      if(revision!==refreshVersion)return;
      state=inventory;
      lastInventoryRead=Date.now();
    }else if(view==='machines'&&!$('dialog[open]')&&Date.now()-lastInventoryRead>=60000){
      lastInventoryRead=Date.now();
      const resources=await api('simple/resources');
      if(revision!==refreshVersion)return;
      state.resources=resources;
      if(view==='machines'&&!$('dialog[open]'))updateMachineInventory();
    }
    if(view==='schedules'&&mg)mg.schedules=await api('manage/schedules');
    if(changed&&view==='machines'&&$('#machine-rows')){updateMachineInventory();renderRecentJobs()}
    else if(!$('#editor').open&&(changed||view==='schedules'))render();
  }catch{
    if(revision===refreshVersion&&view==='machines')notice('Refresh failed. Showing the last available inventory.');
  }finally{polling=false}
}
setInterval(pollDashboard,4000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollDashboard()});
