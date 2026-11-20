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
  if(!shown.length)return `<tr><td colspan="5" class="inventory-empty">${guests.length?'No machines match these filters.':'No virtual machines discovered. <button data-view="create">Create a machine</button>'}</td></tr>`;
  return shown.map(g=>{
    const key=machineKey(g),name=g.name||'VM '+g.vmid;
    return `<tr><td class="pick"><input type="checkbox" aria-label="Select ${esc(name)} on ${esc(g.node)}" data-machine-key="${esc(key)}" ${machineFilters.selected.has(key)?'checked':''}></td>
      <td class="machine-name"><button class="machine-link" data-machine="${esc(key)}">${esc(name)}</button><small>${esc(g.node)} · VM ${esc(g.vmid)}</small>${g.last_winrm_check?'<small>WinRM login checked '+esc(new Date(g.last_winrm_check*1000).toLocaleDateString())+'</small>':''}</td>
      <td class="machine-state"><span class="pill ${esc(g.status)}">${esc(g.status||'unknown')}</span></td>
      <td class="capacity">${esc(g.maxcpu??'—')} vCPU<small>${g.maxmem?(g.maxmem/1073741824).toLocaleString(undefined,{maximumFractionDigits:1})+' GiB RAM':'Memory unavailable'}</small></td>
      <td class="machine-actions"><div class="row-actions"><button data-simple="apply" data-id="${esc(g.vmid)}" aria-label="Run script on ${esc(name)}">Run script</button><button type="button" data-refresh-connection="${esc(key)}" aria-label="Refresh connection for ${esc(name)}">Refresh connection</button><details class="machine-menu"><summary>Manage</summary><div>${powerButton(g)}${mgButton('More VM controls','manage-vm',g.vmid)}<button data-machine="${esc(key)}">Guest connection</button>${mgButton('Snapshots','snapshots',g.vmid)}</div></details></div></td></tr>`;
  }).join('');
}

function selectionLabel(){
  const count=machineFilters.selected.size;
  return 'Run script'+(count?' ('+count+')':'');
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
  $('#content').innerHTML=title('Operations','')+
    `<section class="inventory panel" aria-label="Machine inventory"><div class="inventory-tools"><label>Find a machine<input id="machine-search" type="search" placeholder="Name, node or VM ID" value="${esc(machineFilters.query)}"></label>
    <div class="operation-controls"><button type="button" class="primary" id="run-selected" data-simple="apply">Run script</button>
    <details class="machine-menu"><summary>Power</summary><div><button data-bulk-power="start">Power on</button><button data-bulk-power="shutdown">Graceful shutdown</button></div></details></div></div>
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
  if(!['home','scripts','machines','create'].includes(view))return false;
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  if(view==='home'||view==='machines'){
    renderMachines();
  }else if(view==='create'){
    const templates=state.resources.filter(r=>r.type==='qemu'&&r.template);
    const choices=[['vm','Virtual machine','Build a working guest from installation media or a template.'],
      ['template','Reusable template','Install the OS and your scripts, then prepare the guest for cloning.']];
    $('#content').innerHTML=title('Create','Choose what to build.')+'<div class="build-choices">'+choices.map(([purpose,name,description])=>
      '<section class="panel"><h2>'+name+'</h2><p>'+description+'</p><div class="row-actions">'+
      '<button data-simple="windows" data-purpose="'+purpose+'">Windows Server</button><button data-simple="ubuntu" data-purpose="'+purpose+'">Ubuntu</button></div></section>').join('')+'</div>'+
      '<h2>Available templates</h2>'+(templates.length?table(['Name','Status','Node','Resources','Actions'],guestRows(templates,true)):'<p>No templates discovered.</p>');
  }else{
    if(!mg){$('#content').innerHTML=title('Script library','Loading your saved scripts…');if(!mgLoading){mgLoading=true;loadManagement().then(render).catch(e=>notice(e.message)).finally(()=>mgLoading=false)}return true}
    $('#content').innerHTML=scriptLibrary();
  }
  return true;
}

async function openSimple(os='windows',purpose='vm',filename='',sourceTemplate=null){
  if(!state){view='server';render();return notice('Connect your Proxmox server first.')}
  try{
    if(!serverInfo.ssh_fingerprint&&!(os==='windows'&&purpose==='vm'&&!filename&&windowsTemplates().length)){
      view='server';render();
      $('.server-advanced').open=true;
      $('#server-form [name=ssh_fingerprint]').focus();
      return notice('Add the verified SSH fingerprint and save the connection to enable machine builds.');
    }
    const serverId=serverInfo.id;
    const [options]=await Promise.all([api('simple/options'),loadManagement()]);
    if(serverInfo.id!==serverId||!serverInfo.connected)return;
    simpleOptions=options;
    if(sourceTemplate&&!simpleOptions.destinations.some(n=>n.node===sourceTemplate.node)){
      return notice('The selected template’s node needs online disk and ISO storage and an available network bridge.');
    }
    const defaultClone=sourceTemplate||(os==="windows"&&!serverInfo.ssh_fingerprint&&purpose==="vm"?windowsTemplates().find(t=>simpleOptions.destinations.some(n=>n.node===t.node)):null);
    const dest=simpleOptions.destinations.find(n=>n.node===defaultClone?.node)||simpleOptions.destinations[0];
    const used=new Set([...state.resources.map(g=>g.name),...jobs.filter(j=>['queued','running','waiting'].includes(j.status)).map(j=>j.target?.name)].filter(Boolean).map(name=>name.toLowerCase()));
    const prefix=os==='windows'?'win-server':'ubuntu';
    let name=prefix;
    for(let index=2;used.has(name);index++)name=prefix.slice(0,11-String(index).length)+'-'+index;
    simpleDraft={os,purpose,source_kind:defaultClone?'template':'iso',template_id:defaultClone?.vmid,data_disks:[],count:1,name,profile:'ubuntu-24.04',username:'ubuntu',password:'',
      node:dest.node,storage:dest.storage,iso_storage:dest.iso_storage,bridge:dest.bridge,management_cidr:dest.management_cidr,agent_iso:dest.agent_iso,
      cores:2,memory_gb:os==='windows'?4:2,disk:os==='windows'?64:32,locale:'en-GB',timezone:'GMT Standard Time',packages:'',chocolatey:false,features:[],scripts:[],script_params:{}};
    simpleStep=0;simplePlan=null;simpleBusy=false;$('#simple-fields').inert=false;$('#simple-back').disabled=false;
    if(filename){simpleDraft.source='library:'+filename;simpleDraft.all_media=!mediaCategory({label:filename})}
    $('#simple-next').disabled=false;
    drawSimple();$('#simple-dialog').showModal();focusSimpleStep();if(os==='windows')await loadEditions();
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
  form.querySelectorAll('input[type=checkbox][name]').forEach(i=>{if(!['feature','saved_script','common_program','windows_setting'].includes(i.name))simpleDraft[i.name]=i.checked});
  if(simpleStep===0&&simpleDraft.os==='windows')collectDataDisks(simpleDraft,form);
  if(simpleStep===1){
    simpleDraft.packages=form.elements.packages.value;
    simpleDraft.common_programs=[...form.querySelectorAll('[name=common_program]:checked:not(:disabled)')].map(i=>i.value);
    simpleDraft.windows_settings=[...form.querySelectorAll('[name=windows_setting]:checked:not(:disabled)')].map(i=>i.value);
    for(const key of ['common_program','windows_setting'])delete simpleDraft[key];
    simpleDraft.features=[...form.querySelectorAll('[name=feature]:checked')].map(i=>i.value);
    simpleDraft.scripts=[...form.querySelectorAll('[name=saved_script]:checked')].map(i=>i.value);
    form.querySelectorAll('[data-script-param]').forEach(i=>{simpleDraft.script_params[i.dataset.scriptParam]=i.value});
  }
}

async function loadEditions(){
  if(!simpleDraft||simpleDraft.os!=='windows'||simpleDraft.source_kind==='template')return;
  const draft=simpleDraft,source=draft.source,lookup=++editionLookup;
  const current=()=>simpleDraft===draft&&editionLookup===lookup;
  $('#simple-retry-iso').hidden=true;
  $('#simple-error').textContent='';
  draft.editions=[];delete draft.edition;delete draft.edition_identity;
  showEditionPlaceholder(draft);
  $('#simple-next').disabled=true;
  if(!source){
    reviewProgress('');
    $('#simple-error').textContent='Choose a Windows Server ISO to continue.';
    return;
  }
  reviewProgress('Reading Windows editions from your ISO…');
  try{
    const revision=simpleSources().find(s=>s.id===source)?.revision;
    const cacheKey=revision&&JSON.stringify([serverInfo.id,draft.node,source,revision]);
    const result=(cacheKey&&editionCache.get(cacheKey))||await api('simple/editions',{node:draft.node,source});
    if(!current())return;
    if(!result.editions.length)throw Error('No Windows editions found on this ISO.');
    if(cacheKey){if(editionCache.size>=64)editionCache.delete(editionCache.keys().next().value);editionCache.set(cacheKey,result)}
    draft.edition_identity=result.identity;draft.editions=result.editions;
    draft.edition=String((result.editions.find(e=>e.desktop&&/standard/i.test(e.name))||result.editions[0]).index);
    const select=$('#simple-form [name=edition]');
    select.replaceChildren(...draft.editions.map(e=>new Option(e.label,e.index)));
    select.value=draft.edition;select.disabled=false;
    reviewProgress('');
    $('#simple-next').disabled=false;
  }catch(e){
    if(!current())return;
    $('#simple-retry-iso').hidden=false;showEditionPlaceholder(draft);
    reviewProgress('');$('#simple-error').textContent=e.message;
  }
}

function bulkChoiceButtons(label){
  return '<div class="choice-actions"><button type="button" data-select-group="all" aria-label="Select all '+esc(label)+'">Select all</button>'+
    '<button type="button" data-select-group="none" aria-label="Clear '+esc(label)+'">Clear</button></div>';
}

function bulkChoices(label, options) {
  return '<div class="bulk-choices"><div class="choices-head"><h3>'+esc(label)+'</h3>'+bulkChoiceButtons(label)+'</div>'+
    '<div class="choice-grid">'+options+'</div></div>';
}

function softwareFields(d){
  const windows=d.os==='windows', platform=windows?'windows':'linux';
  const saved=(mg?.tasks||[]).filter(t=>t.kind&&t.platform===platform);
  const fresh=windows?freshWindowsSettings(d):{};
  const sections=[];
  if(fresh.settings) sections.push(['settings','Customisations',fresh.settings]);
  sections.push(['apps','Packages',(fresh.apps||'')+
    '<div class="package-entry">'+(windows?wc('chocolatey','Set up Chocolatey',d.chocolatey):'')+
    '<label>Packages to install<textarea rows="2" name="packages" '+(windows&&!d.chocolatey?'disabled ':'')+'placeholder="'+(windows?'7zip, git':'curl, git')+'">'+esc(d.packages||'')+
    '</textarea></label><div class="package-help"><small>Separate package IDs with commas or spaces.</small>'+
    (windows?'<a href="https://community.chocolatey.org/packages" target="_blank" rel="noopener noreferrer">Browse Chocolatey packages</a>':'')+'</div></div>']);
  if(windows) sections.push(['features','Server features','<h3>Windows Server features</h3><div class="choice-grid">'+simpleOptions.features.map(f=>
    '<label class="check"><input type="checkbox" name="feature" value="'+esc(f.id)+'" '+((d.features||[]).includes(f.id)?'checked':'')+'>'+esc(f.name)+'</label>').join('')+
    '</div><p class="subtle">Windows may restart to finish installing a feature.</p>']);
  sections.push(['scripts','Scripts','<h3>Guest scripts</h3>'+saved.map(t=>{
    const selected=(d.scripts||[]).includes(t.id);
    return '<div class="software-script"><label class="check"><input type="checkbox" name="saved_script" value="'+esc(t.id)+'" '+(selected?'checked':'')+'>'+esc(t.name)+'</label>'+
      '<div class="script-parameters" '+(selected?'':'hidden')+'>'+Object.entries(t.parameters||{}).map(([key,label])=>
      '<label>'+esc(label)+'<input required '+scriptValueAttrs+(selected?'':' disabled')+' data-script-param="'+esc(t.id+':'+key)+'" value="'+esc(d.script_params?.[t.id+':'+key]??scriptInputValue(t.defaults,key)??'')+'"></label>').join('')+
      '</div><details><summary>View source</summary><pre>'+esc(t.body)+'</pre></details></div>';
  }).join('')+(saved.length?'':'<p>Add scripts in the Script library to use them here.</p>')]);
  const preferred=d.software_section||(d.scripts?.length?'scripts':'');
  const active=sections.some(([id])=>id===preferred)?preferred:sections[0][0];
  return '<div class="software-picker"><div class="software-sections" role="group" aria-label="Software categories">'+sections.map(([id,label])=>
    '<button type="button" data-section="'+id+'" aria-controls="software-'+id+'" aria-pressed="'+(id===active)+'">'+label+
    '<span class="section-count" hidden></span></button>').join('')+'</div>'+sections.map(([id,label,body])=>
    '<section class="software-pane" id="software-'+id+'" aria-label="'+label+'" '+(id===active?'':'hidden')+'>'+body+'</section>').join('')+'</div>';
}

function bindSoftware(root,d){
  const picker=root.querySelector('.software-picker');
  if(!picker) return;
  const buttons=[...picker.querySelectorAll('[data-section]')];
  const paneFor=button=>picker.querySelector('#software-'+button.dataset.section);
  function counts(){
    for(const button of buttons){
      const pane=paneFor(button);
      let count=pane.querySelectorAll('input[type=checkbox]:checked:not(:disabled)').length;
      const packages=pane.querySelector('[name=packages]');
      if(packages&&!packages.disabled){
        const names=[...pane.querySelectorAll('[name=common_program]:checked')].map(i=>i.value);
        const entered=packages.value.trim().split(/[\s,]+/).filter(Boolean);
        const unique=new Set([...names,...entered].map(p=>d.os==='windows'?p.toLowerCase():p));
        count+=unique.size-names.length;
      }
      const badge=button.querySelector('.section-count');badge.hidden=!count;badge.textContent=String(count);
    }
  }
  picker.onclick=e=>{
    const button=e.target.closest('button');if(!button) return;
    if(button.dataset.section){
      d.software_section=button.dataset.section;
      for(const item of buttons){
        item.setAttribute('aria-pressed',String(item===button));paneFor(item).hidden=item!==button;
      }
      root.scrollTop=0;
    }else if(button.hasAttribute('data-select-group')){
      const chosen=button.dataset.selectGroup==='all';
      for(const input of button.closest('.bulk-choices').querySelectorAll('input[type=checkbox]:not(:disabled)'))input.checked=chosen;
      counts();
    }
  };
  picker.oninput=counts;
  picker.onchange=e=>{
    if(e.target.name==='chocolatey')picker.querySelector('[name=packages]').disabled=!e.target.checked;
    if(e.target.name==='saved_script'){
      const params=e.target.closest('.software-script').querySelector('.script-parameters');
      params.hidden=!e.target.checked;
      for(const input of params.querySelectorAll('input'))input.disabled=params.hidden;
    }
    counts();
  };
  picker.addEventListener('invalid',e=>{
    if(e.target.hasAttribute('data-script-param') && e.target.closest('.software-pane').hidden)
      picker.querySelector('[data-section=scripts]').click();
  },true);
  counts();
}

function enabledPackageText(d){
  return d.os==='windows'&&!d.chocolatey?'':d.packages||'';
}

function selectedSoftware(d){
  const tasks=[];const windows=d.os==='windows';const entered=enabledPackageText(d).split(/[\s,]+/).filter(Boolean);
  const packages=windows?[...new Set([...(d.common_programs||[]),...entered].map(p=>p.toLowerCase()))]:entered;
  if(windows&&d.features?.includes('NET-Framework-Core'))tasks.push({id:'windows-feature',params:{feature:'NET-Framework-Core'}});
  if(windows&&(d.chocolatey||packages.length))tasks.push({id:'install-chocolatey',params:{}});
  packages.forEach(p=>tasks.push({id:windows?'chocolatey-package':'linux-package',params:{package:p}}));
  if(windows)(d.features||[]).filter(f=>f!=='NET-Framework-Core').forEach(f=>tasks.push({id:'windows-feature',params:{feature:f}}));
  (d.scripts||[]).forEach(id=>{const t=mg.tasks.find(t=>t.id===id);tasks.push({id,params:Object.fromEntries(Object.keys(t.parameters||{}).map(k=>[k,d.script_params?.[id+':'+k]??scriptInputValue(t.defaults,k)??'']))})});
  return tasks;
}

function buildReview(p,windows){
  const first=p.machines[0];
  const machine=m=>esc(m.name)+' · VM '+esc(m.vmid);
  const tasks=p.tasks.map(t=>'<li>'+esc(t.name)+' '+esc(Object.values(t.params).join(', '))+'</li>').join('');
  const risks=p.tasks.filter(t=>t.dangerous).map(t=>t.name);
  const specs=[
    ['Source', esc(p.edition)+(p.clone_mode?' · '+esc(p.clone_mode)+' clone':'')],
    ['Hardware',esc(first.cores)+' vCPU · '+esc(first.memory/1024)+' GiB RAM · '+esc(first.disk)+' GiB system disk'],
    ['Placement',esc(first.node)+' · '+esc(first.storage)+' · '+esc(first.bridge)],
    ['Remote access',esc(p.remote_access)+(p.management_cidr?' from '+esc(p.management_cidr):'')+(p.rdp?' · RDP with NLA':'')]
  ];
  for(const disk of p.data_disks||[]){
    specs.push(['Data disk','Format '+esc(disk.size)+' GiB as NTFS · '+esc(disk.label)+' · '+(disk.letter?esc(disk.letter)+':':'Automatic drive letter')]);
  }
  return '<div class="build-review">'+
    (p.machines.length===1?'<h3>'+machine(first)+'</h3>':
      '<details><summary>'+p.machines.length+' machines · '+esc(first.name)+' … '+esc(p.machines.at(-1).name)+'</summary><ul>'+
      p.machines.map(m=>'<li>'+machine(m)+'</li>').join('')+'</ul></details>')+
    '<dl class="build-specs">'+specs.map(([label,value])=>'<div><dt>'+label+'</dt><dd>'+value+'</dd></div>').join('')+'</dl>'+
    (p.purpose==='template'?'<p>Generalize, shut down and convert to a reusable template.</p>':'')+
    (tasks?'<details'+(p.tasks.length<=4?' open':'')+'><summary>Software and settings ('+p.tasks.length+')</summary><ol>'+tasks+'</ol></details>':'')+
    '<details><summary>Installation details</summary><ul><li>Save the verified guest login and connection.</li>'+
    p.notes.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul></details>'+
    wc('confirmed',windows?'Create these machines with the reviewed settings. I am authorised to use this Windows image and accept its licence.':'Create these machines with the reviewed settings.')+
    (p.dangerous?wc('risk_confirmed','I approve '+(risks.length?esc(risks.join(', ')):'the sensitive settings and custom scripts')+'.'):'')+'</div>';
}

function focusSimpleStep(){
  $('#simple-steps [aria-current="step"]')?.focus({preventScroll:true});
}

// Editing a choice on the Machine step replaces its dependent fields.
function redrawMachineFields(focusName){
  const area=$('#simple-fields');
  const scroll=area.scrollTop;
  const disclosures=[...area.querySelectorAll('details[id]')].map(el=>[el.id,el.open]);
  drawSimple();
  for(const [id,open] of disclosures){
    const disclosure=document.getElementById(id);
    if(disclosure)disclosure.open=open||(id==='installation-details'&&simpleDraft.os==='windows'&&!simpleDraft.management_cidr);
  }
  area.scrollTop=scroll;
  if(focusName)$('#simple-form').elements.namedItem(focusName)?.focus({preventScroll:true});
}

function machineConfigFields(d,dest){
  const windows=d.os==='windows',clone=d.source_kind==='template';
  function group(title,fields,kind=''){
    return '<fieldset class="config-group '+kind+'"><legend>'+title+'</legend><div class="config-controls">'+fields.join('')+'</div></fieldset>';
  }
  const destination=[
    ws('node','Proxmox node',simpleOptions.destinations.map(n=>[n.node,n.node]),d.node),
    ws('storage',clone&&d.clone_mode==='linked'?'New data disk storage':'VM disk storage',dest.storage_choices.map(s=>[s,s]),d.storage)
  ];
  if(!clone)destination.push(ws('bridge','Network bridge',dest.bridges.map(b=>[b,b]),d.bridge));
  const hardware=[
    wf('cores','CPU cores',d.cores,'number','min="1" max="128" required'),
    wf('memory_gb','Memory (GiB)',d.memory_gb,'number','min="'+(windows?4:1)+'" max="1024" required')
  ];
  if(!clone)hardware.push(wf('disk','System disk (GiB)',d.disk,'number','min="'+(windows?64:8)+'" max="4096" required'));
  const media=[ws('iso_storage','Setup media storage',dest.iso_storages.map(s=>[s,s]),d.iso_storage)];
  if(windows&&!clone)media.push(ws('agent_iso','Guest tools ISO',dest.driver_isos.map(s=>[s,s.split('/').pop()]),d.agent_iso));
  const guest=windows?group('Windows guest',[
    wf('management_cidr','Allow WinRM from (CIDR)',d.management_cidr,'text','required'),
    ws('locale','Language and keyboard',[['en-GB','English (UK)'],['en-US','English (US)'],['de-DE','German'],['fr-FR','French'],['es-ES','Spanish']],d.locale),
    ws('timezone','Time zone',[['GMT Standard Time','London'],['UTC','UTC'],['W. Europe Standard Time','Central Europe'],['Eastern Standard Time','US Eastern'],['Pacific Standard Time','US Pacific']],d.timezone)
  ]):'';
  return '<details id="placement-details"><summary>Placement and hardware <small class="config-preview" id="placement-preview"></small></summary>'+
    '<div class="config-body">'+group('Destination',destination)+group('Hardware',hardware,'hardware-group')+'</div></details>'+
    '<details id="installation-details" '+(windows&&!d.management_cidr?'open':'')+'><summary>Installation settings</summary>'+
    '<div class="config-body">'+group('Setup media',media,'media-group')+guest+'</div></details>';
}

function drawSimple(){
  const d=simpleDraft;if(!d)return;
  $('#simple-title').textContent=simpleStep===2?'Ready to create':(d.os==='windows'?'Windows Server':'Ubuntu')+(d.purpose==='template'?' template':' VM setup');
  $('#simple-steps').innerHTML=['Machine','Software','Review'].map((s,i)=>`<li tabindex="-1" ${i===simpleStep?'aria-current="step"':''}>${i+1}. ${s}</li>`).join('');
  $('#simple-back').hidden=simpleStep===0;$('#simple-next').textContent=simpleStep===2?'Create '+simplePlan.machines.length+(d.purpose==='template'?' template':' machine'+(simplePlan.machines.length===1?'':'s')):simpleStep===1?'Review build':'Continue';
  let html='';
  if(simpleStep===0){
    const dest=simpleOptions.destinations.find(n=>n.node===d.node);
    html='<div class="machine-setup"><section aria-labelledby="machine-basics"><h3 id="machine-basics">Machine details</h3>'+
      ws('purpose','Build type',[['vm','Working VM'],['template','Reusable template']],d.purpose)+
      '<div class="machine-name-count">'+wf('name','Machine name',d.name,'text','required maxlength="12" pattern="[A-Za-z][A-Za-z0-9\\-]{0,11}"')+
      (d.purpose==='vm'?wf('count','Quantity',d.count,'number','required min="1" max="20"'):'')+'</div><small id="batch-names" hidden></small>'+
      (d.os==='windows'?wf('password','Administrator password',d.password,'password','required minlength="12" maxlength="128" autocomplete="new-password" placeholder="At least 12 characters"'):
        wf('username','SSH login name',d.username,'text','required pattern="[a-z_][a-z0-9_\\-]{0,31}"'))+
      '</section><section aria-labelledby="machine-source"><h3 id="machine-source">Installation source</h3>'+
      (d.os==='windows'?windowsSourceFields(d):
        ws('profile','Ubuntu version',Object.entries(simpleOptions.profiles),d.profile)+'<p class="subtle">Proxmate uses the official Ubuntu cloud image and generates an SSH key for this login.</p>')+'</section>';
    html+='</div><div class="machine-options">'+machineConfigFields(d,dest)+(d.os==='windows'?dataDiskFields(d):'')+'</div>';
  }else if(simpleStep===1)html=softwareFields(d);
  else html=buildReview(simplePlan,d.os==='windows');
  $('#simple-fields').innerHTML=html;
  $('#simple-fields').scrollTop=0;
  if(simpleStep===1)bindSoftware($('#simple-fields'),d);
  if(simpleStep===2){
    for(const input of $('#simple-fields').querySelectorAll('[type=checkbox]'))input.required=true;
  }
  if(simpleStep===0){
    const fields=$('#simple-form').elements;
    const updatePlacementPreview=()=>{
      const values=[fields.node.value,fields.cores.value&&fields.cores.value+' vCPU',fields.memory_gb.value&&fields.memory_gb.value+' GiB RAM'];
      $('#placement-preview').textContent=values.filter(Boolean).join(' · ');
    };
    fields.cores.oninput=fields.memory_gb.oninput=updatePlacementPreview;
    updatePlacementPreview();
    if(fields.count){
      const preview=$('#batch-names');
      const updateNames=()=>{
        const count=fields.count.valueAsNumber,name=fields.name.value;
        preview.hidden=!(name && fields.count.validity.valid && count>1);
        preview.textContent=preview.hidden?'':name+'-01 … '+name+'-'+String(count).padStart(2,'0');
      };
      fields.count.oninput=fields.name.oninput=updateNames;
      updateNames();
    }
    if(d.os==='windows')bindWindowsFields(d);
    if(d.source_kind==='template')$('#simple-form').elements.node.disabled=true;
    const driver=$('#simple-form [name=agent_iso]');if(driver)driver.onchange=()=>{collectSimple();d.agent_manual=true};
    for(const name of ['purpose','node','source','clone_mode']){const field=$(`#simple-form [name=${name}]`);if(field)field.onchange=async()=>{
      collectSimple();if(name==='purpose'&&d.purpose==='template'){d.count=1;d.source_kind='iso'}
      if(name==='node'){const dest=simpleOptions.destinations.find(n=>n.node===d.node);for(const k of ['storage','iso_storage','bridge','management_cidr','agent_iso'])d[k]=dest[k];d.agent_manual=false;d.scope_edited=false;d.source='';}
      if(name==='node'||name==='source'){d.editions=[];delete d.edition;}redrawMachineFields(name);if(d.os==='windows'&&(!d.edition||name!=='purpose'))await loadEditions();
    }}
  }
}

document.addEventListener('click',async e=>{
  const button=e.target.closest('button');if(!button)return;
  try{
    if(button.dataset.cloneTemplate){
      const src=state.resources.find(r=>r.type==='qemu'&&r.template&&r.vmid===Number(button.dataset.cloneTemplate));
      if(!src)return notice('Refresh to see available templates.');
      const fromView=view;
      const hint=await api('simple/software',{targets:[{node:src.node,vmid:src.vmid}]});
      if(view!==fromView)return;
      if(hint.platforms[machineKey(src)]==='windows')await openSimple('windows','vm','',src);
      else{
        const inventory=state.details?state:await api('state');
        if(view!==fromView)return;
        if(!inventory.resources.some(r=>r.type==='qemu'&&r.template&&machineKey(r)===machineKey(src)))throw Error('The source template changed. Refresh the inventory.');
        state=inventory;openAction('clone',src.vmid,'',src.node);
      }
    }
    else if(button.dataset.simple==='apply'){
      const targets=button.dataset.id?mgGuests().filter(g=>g.vmid===Number(button.dataset.id)):
        button.id==='run-selected'?mgGuests().filter(g=>machineFilters.selected.has(machineKey(g))):[];
      if(button.dataset.id&&!targets.length)return notice('This machine is no longer in the inventory. Refresh to see available machines.');
      await openScriptRun(targets);
    }
    else if(button.dataset.simple){
      await openSimple(button.dataset.simple,button.dataset.purpose||'vm',button.dataset.filename||'');
    }
    if(button.dataset.simpleTask)await openScriptRun([],button.dataset.simpleTask);
  }catch(err){notice(err.message)}
});
$('#simple-close').onclick=()=>$('#simple-dialog').close();
$('#simple-dialog').addEventListener('close',()=>{reviewProgress('');$('#simple-retry-iso').hidden=true;simpleBusy=false;simpleDraft=null;simplePlan=null;$('#simple-fields').replaceChildren();$('#simple-error').textContent=''});
$('#simple-back').onclick=()=>{collectSimple();simpleStep--;drawSimple();focusSimpleStep()};
$('#simple-retry-iso').onclick=()=>loadEditions();
function reviewProgress(message){
  const el=$('#simple-progress');el.hidden=!message;el.textContent=message;
}
async function reviewBuild(draft){
  reviewProgress('Checking storage and build settings…');
  const settings={...draft,packages:enabledPackageText(draft),memory:Number(draft.memory_gb)*1024,tasks:selectedSoftware(draft)};
  const ticket=await api('simple/plan',settings,{Prefer:'respond-async'});
  if(!ticket.review_id)return ticket;
  const stages={inventory:'Checking available storage and machine IDs…',editions:'Checking the selected Windows edition…',drivers:'Checking guest drivers and installer integrity…'};
  let failures=0;
  while(simpleDraft===draft){
    let result;
    try{result=await api('simple/reviews/'+encodeURIComponent(ticket.review_id));failures=0}
    catch(error){
      if(!error.transport||++failures>5)throw Error(error.message+' · Review '+ticket.request_id);
      if(simpleDraft===draft)reviewProgress('Reconnecting to your build review…');
    }
    if(simpleDraft!==draft)return;
    if(result?.state==='ready')return result.plan;
    if(result?.state==='failed')throw Error(result.error+' · Request '+result.request_id);
    if(result)reviewProgress(stages[result.stage]||'Preparing your build review…');
    await new Promise(resolve=>setTimeout(resolve,failures?1500:700));
  }
}
$('#simple-form').onsubmit=async e=>{
  e.preventDefault();if(simpleBusy)return;simpleBusy=true;const draft=simpleDraft;
  $('#simple-next').disabled=true;$('#simple-error').textContent='';
  try{
    collectSimple();
    if(simpleStep===0){if(draft.source_kind==='template'&&!draft.generalized)throw Error('Confirm the source template has been prepared.');if(draft.os==='windows'&&draft.source_kind!=='template'&&!draft.edition)throw Error('Choose a readable Windows Server ISO and edition.');simpleStep=1;drawSimple();focusSimpleStep()}
    else if(simpleStep===1){$('#simple-back').disabled=true;$('#simple-next').textContent='Preparing review…';$('#simple-fields').inert=true;const plan=await reviewBuild(draft);if(simpleDraft!==draft)return;simplePlan=plan;simpleStep=2;drawSimple();focusSimpleStep()}
    else{if(!draft.confirmed)throw Error('Confirm the machines and software before creating.');const result=await api('simple/run',{plan_id:simplePlan.id,confirmed:true,risk_confirmed:!!draft.risk_confirmed});submittedBuilds(result.jobs);if(simpleDraft===draft)$('#simple-dialog').close();await refresh();}
  }catch(err){if(simpleDraft===draft)$('#simple-error').textContent=err.message}
  finally{
    if(simpleDraft===draft){
      simpleBusy=false;reviewProgress('');$('#simple-fields').inert=false;$('#simple-back').disabled=false;$('#simple-next').disabled=false;
      if(simpleStep===1)$('#simple-next').textContent='Review build';
    }
  }
};
