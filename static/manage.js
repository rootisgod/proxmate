/* Guest management. Drafts and write-only secrets stay in memory, never web storage. */
const managementViews = ['tasks','credentials','groups','recipes','schedules'];
let mg = null, mgLoading = false, mgSave = null, mgPlan = null, mgSelectedTasks = [];
const mgButton = (label, action, id='')=>`<button type="button" data-mg="${action}" data-record="${esc(id)}">${label}</button>`;
const mgArea = (name,label,value='',extra='')=>`<label class="wide">${label}<textarea name="${name}" ${extra}>${esc(value)}</textarea></label>`;
const mgSelect = (name,label,items,selected='')=>ws(name,label,items,selected);
const mgGuests = ()=> (state?.resources||[]).filter(r=>r.type==='qemu'&&!r.template);
const mgCreds = (platform)=>[['','Choose a credential'],...(mg?.credentials||[]).filter(c=>!platform||c.platform===platform).map(c=>[c.id,`${c.name} (${c.username})`])];

async function loadManagement(){
  const keys=['tasks','credentials','connections','groups','recipes','schedules'];
  const values=await Promise.all(keys.map(k=>api('manage/'+k)));
  mg=Object.fromEntries(keys.map((k,i)=>[k,values[i]]));
}
function managementRender(){
  if(!managementViews.includes(view))return false;
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  if(!mg){
    $('#content').innerHTML='<h1>Loading your tools...</h1>';
    if(!mgLoading){mgLoading=true;loadManagement().then(()=>render()).catch(e=>notice(e.message)).finally(()=>mgLoading=false)}
    return true;
  }
  if(view==='credentials'){renderCredentialWorkspace();return true}
  if(view==='tasks'){
    $('#content').innerHTML=title('Advanced task controls','Review guest operations, check readiness, or prepare a guest bootstrap helper.')+
      '<div class="toolbar">'+mgButton('Apply tasks to VMs','apply')+mgButton('Check guest readiness','readiness')+
      '<button data-lc="helper">Guest bootstrap helper</button><button data-view="scripts">Script library</button></div>';
    return true;
  }
  const headings={groups:['VM groups','Save useful sets of machines. Every run reviews the exact targets.'],recipes:['Build recipes','Reuse a build configuration and an ordered set of follow-up tasks.'],schedules:['Scheduled maintenance','Explicitly enabled schedules. All times and maintenance windows are UTC. Missed runs are skipped.']};
  let html=title(...headings[view],mgButton(view==='recipes'?'Create in build wizard':view==='schedules'?'Schedule an operation':'Add group','new-'+view));
  if(view==='groups')html+=table(['Group','Members','Actions'],mg.groups.map(g=>`<tr><td>${esc(g.name)}</td><td>${g.targets.map(t=>esc(t.node+':'+t.vmid)).join(', ')}</td><td>${mgButton('Apply operation','use-group',g.id)} ${mgButton('Edit','edit-groups',g.id)} ${mgButton('Delete','delete-groups',g.id)}</td></tr>`));
  else if(view==='recipes')html+='<p>Build first, finish OS installation, configure the guest connection, then apply the saved tasks. Recipes never store a Windows setup password.</p>'+table(['Recipe','Build / tasks','Actions'],mg.recipes.map(r=>`<tr><td>${esc(r.name)}</td><td>${esc(r.config.profile||r.config.kind)}<small>${r.tasks.length} follow-up tasks</small></td><td>${mgButton('Build','build-recipe',r.id)} ${mgButton('Apply tasks','apply-recipe',r.id)} ${mgButton('Edit tasks','edit-recipes',r.id)} ${mgButton('Delete','delete-recipes',r.id)}</td></tr>`));
  else if(view==='schedules')html+=table(['Schedule','When (UTC)','Latest result','Actions'],mg.schedules.map(s=>`<tr><td>${esc(s.name)}<small>${s.enabled?'Enabled':'Disabled'} / ${esc(s.summary.operation)} / ${s.summary.targets.length} VMs</small></td><td>Every ${s.interval_minutes} min<small>${s.window_start}:00 - ${s.window_end}:00<br>Next: ${new Date(s.next_run*1000).toISOString()}</small></td><td>${esc(s.last_status||s.last_report)}${s.last_job?`<small><button data-log="${esc(s.last_job)}">View run report</button></small>`:''}</td><td>${mgButton('Edit / enable','edit-schedules',s.id)} ${mgButton('Delete','delete-schedules',s.id)}</td></tr>`));
  $('#content').innerHTML=html;return true;
}

function renderCredentialWorkspace(){
  if(!$('#credential-list')){
    $('#content').innerHTML='<div class="settings-workspace">'+title('Guest credentials','Manage the logins Proxmate uses to run scripts on Windows and Linux guests.','<button type="button" class="primary" data-mg="new-credentials"><span class="bi bi-plus-lg" aria-hidden="true"></span> Add credential</button>')+
      `<section class="panel build-defaults" aria-labelledby="build-default-heading"><h2 id="build-default-heading">Defaults for new machines</h2>
      <p>Reuse a guest login for new builds. You can choose a separate login when creating a machine.</p>
      <div id="build-default-cards"></div></section><section class="panel credential-library" aria-label="Saved guest credentials">
      <div class="settings-toolbar" id="credential-filters">
        <label>Search credentials<input type="search" id="credential-query" placeholder="Name, user or VM"></label>
        <label>Guest platform<select id="credential-platform"><option value="">All platforms</option><option value="windows">Windows</option><option value="linux">Linux</option></select></label>
        <span id="credential-count" role="status"></span>
      </div><div id="credential-list"></div></section>
      <p class="settings-help"><span class="bi bi-lock" aria-hidden="true"></span> Guest secrets are encrypted at rest. You can replace a saved secret by editing its credential.</p></div>`;
    $('#credential-query').oninput=renderCredentialWorkspace;
    $('#credential-platform').onchange=renderCredentialWorkspace;
  }
  const cards=$('#build-default-cards');
  const defaults=['windows','linux'].map(platform=>{
    const current=mg.credentials.find(c=>c.platform===platform&&c.is_build_default);
    const os=platform==='windows'?'Windows':'Linux';
    return `<div><div><strong>${os}</strong><small>${current?esc(current.name)+' · '+esc(current.username):'Separate login per build'}</small></div><button type="button" data-mg="build-default" data-record="${platform}" aria-label="Choose ${os} build default">${current?'Change':'Choose default'}</button></div>`;
  }).join('');
  if(defaults!==cards.renderedMarkup){cards.innerHTML=defaults;cards.renderedMarkup=defaults}
  const query=$('#credential-query').value.trim().toLowerCase(),platform=$('#credential-platform').value;
  const guests=mgGuests(),rows=[];
  for(const credential of mg.credentials){
    if(platform&&credential.platform!==platform)continue;
    const connections=mg.connections.filter(c=>c.credential_id===credential.id).map(connection=>{
      const guest=guests.find(g=>g.node===connection.node&&Number(g.vmid)===Number(connection.vmid));
      const target=[guest?.name,connection.node,'VM '+connection.vmid].filter(Boolean).join(' · ');
      let status='Saved connection';
      if(connection.verified_at&&connection.machine_identity&&connection.credential_revision!=null){
        status=connection.credential_revision===credential.updated?'Last verified '+new Date(connection.verified_at*1000).toLocaleDateString():'Credential changed since verification';
      }
      return {target,status};
    });
    if(![credential.name,credential.username,credential.platform,...connections.map(c=>c.target)].join(' ').toLowerCase().includes(query))continue;
    const material=[[credential.has_password,'Password'],[credential.has_private_key,'SSH key'],[credential.has_become_password,'sudo password'],[credential.has_ca_pem,'CA certificate']].filter(([present])=>present).map(([,label])=>label);
    const usage=connections.length?`<details class="credential-usage"><summary>${connections.length} VM connection${connections.length===1?'':'s'}</summary><ul>${connections.map(c=>`<li>${esc(c.target)}<small>${esc(c.status)}</small></li>`).join('')}</ul></details>`:'<span class="settings-muted">No VM connections</span>';
    rows.push(`<li class="credential-entry">
      <div class="credential-name"><span class="settings-icon bi bi-key" aria-hidden="true"></span><div><strong>${esc(credential.name)}</strong><small>${esc(credential.username)}</small></div></div>
      <div class="credential-method"><strong>${credential.platform==='windows'?'Windows · WinRM':'Linux · SSH'}</strong><small>${material.join(', ')||'No saved material'}</small></div>
      <div>${usage}</div><div class="row-actions">${mgButton('Edit / rotate','edit-credentials',credential.id)}${mgButton('Delete','delete-credentials',credential.id)}</div></li>`);
  }
  $('#credential-filters').hidden=!mg.credentials.length;
  const count=(rows.length===mg.credentials.length?'':rows.length+' of ')+mg.credentials.length+(mg.credentials.length===1?' credential':' credentials');
  if($('#credential-count').textContent!==count)$('#credential-count').textContent=count;
  const list=$('#credential-list');
  const markup=rows.length?`<ul class="credential-entries" aria-label="Credentials">${rows.join('')}</ul>`:mg.credentials.length?
    '<div class="settings-empty"><h2>No matching credentials</h2><p>Try another name, VM or platform.</p><button type="button" id="credential-clear">Clear filters</button></div>':
    '<div class="settings-empty"><span class="settings-icon bi bi-key" aria-hidden="true"></span><h2>No guest credentials saved</h2><p>Add a Windows or Linux login to use it with a guest connection.</p></div>';
  if(markup===list.renderedMarkup)return;
  list.innerHTML=markup;list.renderedMarkup=markup;
  const clear=$('#credential-clear');
  if(clear)clear.onclick=()=>{$('#credential-query').value='';$('#credential-platform').value='';renderCredentialWorkspace();$('#credential-query').focus()};
}

function mgDialog(heading,html,save,button='Save'){
  $('#manage-title').textContent=heading;$('#manage-fields').innerHTML=html;$('#manage-error').textContent='';
  $('#manage-submit').textContent=button;setButtonBusy($('#manage-submit'),false);mgSave=save;
  if(!$('#manage-dialog').open)$('#manage-dialog').showModal();
}
function mgValues(){
  const form=$('#manage-form'),d=Object.fromEntries(new FormData(form));
  form.querySelectorAll('input[type=checkbox][name]').forEach(i=>{if(i.name!=='target'&&i.name!=='group')d[i.name]=i.checked});
  return d;
}
function targetFields(selected=[]){
  const ids=selected.map(t=>t.node+':'+t.vmid);
  return '<fieldset class="wide target-picker"><legend>Exact virtual machines</legend>'+mgGuests().map(r=>`<label class="check"><input type="checkbox" name="target" value="${esc(r.node+':'+r.vmid)}" ${ids.includes(r.node+':'+r.vmid)?'checked':''}>${esc(r.name)} <span class="subtle">${esc(r.node)}:${r.vmid} / ${esc(r.status)}</span></label>`).join('')+'</fieldset>';
}
function selectedTargets(){return [...$('#manage-fields').querySelectorAll('[name=target]:checked')].map(i=>{const [node,vmid]=i.value.split(':');return {node,vmid:Number(vmid)}})}
async function saveRecord(kind,d){await api('manage/'+kind,d);$('#manage-dialog').close();await loadManagement();if(view==='machine'&&kind==='connections'&&machinePage)await openMachine(machinePage.target);else render();notice('Saved.');}

function editCredential(record={}){
  const editing=Boolean(record.id);
  mgDialog(editing?'Edit guest credential':'Add guest credential','<div class="form-grid credential-fields">'+
    wf('name','Credential name',record.name||'','text','required')+mgSelect('platform','Guest platform',[['windows','Windows / WinRM HTTPS'],['linux','Linux / SSH']],record.platform||'windows')+
    wf('username','Guest username',record.username||'','text','required autocomplete="off"')+wf('password','Guest password','','password','autocomplete="new-password"')+
    mgArea('private_key','SSH private key (unencrypted)')+wf('become_password','sudo password (optional)','','password','autocomplete="new-password"')+mgArea('ca_pem','WinRM issuing CA certificate (optional PEM)')+
    (editing?'<p class="wide">Leave a secret blank to keep its saved value. After rotating a login, review affected plans and schedules again.</p>':'<p class="wide" id="credential-login-hint"></p>')+'</div>',d=>saveRecord('credentials',{...d,platform:editing?record.platform:d.platform,id:record.id}),editing?'Save changes':'Add credential');
  const fields=$('#manage-form').elements;
  const showPlatform=()=>{
    const linux=fields.platform.value==='linux';
    for(const [name,visible] of [['private_key',linux],['become_password',linux],['ca_pem',!linux]]){
      fields[name].closest('label').hidden=!visible;fields[name].disabled=!visible;
    }
    fields.password.required=!editing&&!linux;
    const hint=$('#credential-login-hint');
    if(hint)hint.textContent=linux?'Enter a password or an SSH private key.':'Use the Windows guest password. Domain accounts can use DOMAIN\\username.';
  };
  fields.platform.disabled=editing;
  fields.platform.onchange=showPlatform;showPlatform();
}
function editBuildDefault(platform){
  const windows=platform==='windows',os=windows?'Windows':'Linux';
  const choices=mg.credentials.filter(c=>c.platform===platform&&(windows?c.has_password&&c.username.toLowerCase()==='administrator':c.has_private_key))
    .sort((a,b)=>a.name.localeCompare(b.name));
  const current=choices.find(c=>c.is_build_default)?.id||'';
  mgDialog(os+' build default','<div class="form-grid">'+wf('filter','Find a credential','','search','autocomplete="off"')+
    ws('credential_id','Login for new '+os+' machines',[['','Separate login per build']],'')+
    '<p class="wide">'+(windows?'Windows setup uses the local Administrator password.':'Linux setup installs the public key for the selected account. Each guest still generates its own SSH host keys.')+
    ' Changing the default affects future builds.</p>'+
    (choices.length?'':'<p class="wide">Add a '+(windows?'Windows credential for Administrator':'Linux credential with an SSH private key')+' to make it available here.</p>')+'</div>',async d=>{
      await api('manage/credentials/defaults',{platform,credential_id:d.credential_id||null});
      $('#manage-dialog').close();await loadManagement();render();notice(os+' build default saved.');
    },'Save default');
  const form=$('#manage-form'),search=form.elements.filter,select=form.elements.credential_id;
  const filter=()=>{
    const selected=select.value,query=search.value.trim().toLowerCase();
    for(const option of [...select.options].slice(1))option.remove();
    for(const c of choices)if((c.name+' '+c.username).toLowerCase().includes(query)||c.id===selected)
      select.add(new Option(c.name+' · '+c.username,c.id));
    select.value=selected;
  };
  filter();select.value=current;search.oninput=filter;
}
function editGroup(record={}){
  mgDialog('VM group','<div class="form-grid">'+wf('name','Group name',record.name||'','text','required')+mgSelect('credential_id','Optional credential for runs selected through this group', [['','Use each VM connection credential'],...mgCreds().slice(1)],record.credential_id)+targetFields(record.targets||[])+'</div>',d=>saveRecord('groups',{id:record.id,name:d.name,credential_id:d.credential_id,targets:selectedTargets()}));
}
function editConnection(vmid,preferredCredential=''){
  const r=mgGuests().find(g=>g.vmid===Number(vmid));if(!r)return notice('Choose an existing QEMU VM.');
  const existing=mg.connections.find(c=>c.node===r.node&&c.vmid===r.vmid)||{};
  const show=(platform)=>{
    mgDialog(`Guest connection: ${r.name}`,'<div class="form-grid">'+mgSelect('platform','Guest OS',[['windows','Windows'],['linux','Linux']],platform)+wf('host','Guest IP / DNS name',existing.host||'','text','required autocomplete="off"')+wf('port','Port',existing.platform===platform?existing.port:(platform==='windows'?5986:22),'number','required min="1" max="65535"')+mgSelect('credential_id','Saved guest credential',mgCreds(platform),existing.credential_id||preferredCredential)+(platform==='windows'?wc('verify_tls','Verify WinRM TLS certificate (recommended)',existing.verify_tls!==false)+mgArea('ca_pem','Per-VM public TLS certificate PEM (optional; blank retains saved certificate)',existing.ca_pem||'')+wc('clear_ca','Remove per-VM certificate trust (clear the field above first; use the credential CA instead)',false):wf('fingerprint','SSH host fingerprint (SHA256:...)',existing.fingerprint||'','text','required')+wc('become','Use sudo for guest tasks',existing.become!==false))+'<p class="wide">The address must belong to this VM. WinRM must already be configured. Windows uses HTTPS and NTLM. For Linux, verify the fingerprint in the VM console. This form does not enable remote access or change guest networking.</p></div>',d=>saveRecord('connections',{...d,node:r.node,vmid:r.vmid}));
    $('#manage-fields [name=platform]').onchange=e=>show(e.target.value);
  };show(existing.platform||'windows');
}

function taskPicker(values=[], forRecipe=false){
  mgSelectedTasks=structuredClone(values);
  return '<fieldset class="wide"><legend>Scripts in execution order</legend><div id="task-sequence" data-recipe="' + forRecipe + '"></div></fieldset>';
}
function drawTasks(){
  const host=$('#task-sequence');
  if(!host)return;
  const catalog=mg.tasks.filter(t=>host.dataset.recipe!=='true'||!Object.values(t.input_types||{}).some(s=>s.type==='password'));
  const steps=mgSelectedTasks.map(s=>({item:mg.tasks.find(t=>t.id===s.id) || {id:s.id,name:'Unavailable script',parameters:{}},ref:{id:s.id},params:s.params}));
  host.sequence=mountScriptSequence(host,{catalog,steps});
}
function collectTasks(){return $('#task-sequence')?.sequence?.values() || [];}
function editRecipe(record){
  mgDialog('Recipe follow-up tasks','<div class="form-grid">'+wf('name','Recipe name',record.name,'text','required')+mgSelect('credential_id','Suggested guest credential', [['','Choose later'],...mgCreds().slice(1)],record.credential_id)+taskPicker(record.tasks||[],true)+'<p class="wide">To change build settings, choose Build, edit the wizard, and save as a new recipe. Guest tasks run separately after installation and connection setup.</p></div>',d=>saveRecord('recipes',{...record,name:d.name,credential_id:d.credential_id,tasks:collectTasks()}));drawTasks();
}
async function saveWizardRecipe(){
  await loadManagement();const config=wizardPayload();if(config.windows)config.windows.admin_password='';
  mgDialog('Save this build as a recipe','<div class="form-grid">'+wf('name','Recipe name',config.name,'text','required')+mgSelect('credential_id','Suggested guest credential',[['','Choose later'],...mgCreds().slice(1)])+taskPicker([],true)+'<p class="wide">The VM ID and Windows setup password are omitted. Recheck licensing, disk formatting and the new VM ID whenever you reuse this recipe.</p></div>',d=>saveRecord('recipes',{name:d.name,config,credential_id:d.credential_id,tasks:collectTasks()}));drawTasks();
}
async function buildRecipe(record){
  await openWizard(record.config.kind,record.config.filename||'',record.config.purpose);
  if(!wizard)return;
  Object.assign(wizard.draft,record.config,record.config.windows||{}, {vmid:state.next_id,admin_password:'',accept_eula:false,wipe_disk:false,confirmed:false,start:false});
  if(typeof hydrateBootstrapDraft==='function')hydrateBootstrapDraft(wizard.draft,record.config.bootstrap);wizard.step=0;showWizardStep();
  notice(`Recipe loaded. After installation, configure the VM connection and apply "${record.name}" tasks from Recipes.`);
}

function applyOperation({operation='tasks',selected=[],groups=[],tasks=[]}={}){
  mgPlan=null;
  mgDialog('1. Choose an operation and its targets','<div class="form-grid">'+mgSelect('operation','Operation',Object.entries({tasks:'Apply guest tasks',readiness:'Guest readiness check',start:'Power on',shutdown:'Graceful shutdown',stop:'Force power off (risk of data loss)',resize:'Change CPU / RAM (stopped VMs)',backup:'Take backup',snapshot:'Take snapshot',restore:'Restore snapshot (overwrites VM state)'}),operation)+targetFields(selected)+
    (mg.groups.length?'<fieldset class="wide"><legend>Or include saved groups</legend>'+mg.groups.map(g=>`<label class="check"><input type="checkbox" name="group" value="${esc(g.id)}" ${groups.includes(g.id)?'checked':''}>${esc(g.name)} (${g.targets.length} VMs)</label>`).join('')+'</fieldset>':'')+'<div id="operation-options" class="wide"></div></div>',async d=>{
      const payload={operation:d.operation,targets:selectedTargets(),group_ids:[...document.querySelectorAll('#manage-fields [name=group]:checked')].map(i=>i.value)};
      if(d.operation==='tasks')Object.assign(payload,{tasks:collectTasks(),snapshot_before:!!d.snapshot_before});
      if(d.operation==='resize')Object.assign(payload,{cores:d.cores,memory:d.memory});
      if(d.operation==='backup')payload.backup_storage=d.backup_storage;
      if(d.operation==='restore')payload.snapshot=d.snapshot;
      mgPlan=await api('manage/plan',payload);reviewOperation();
    },'Review exact plan');
  const draw=()=>{
    const op=$('#manage-fields [name=operation]').value;
    $('#operation-options').innerHTML=op==='tasks'?taskPicker(tasks)+wc('snapshot_before','Take a snapshot before tasks; abort this VM if the snapshot fails'):op==='resize'?'<div class="form-grid">'+wf('cores','CPU cores',2,'number','required min="1" max="128"')+wf('memory','Memory MB',4096,'number','required min="512" max="1048576"')+'</div><p>All selected VMs must be stopped. Changes apply on next start.</p>':op==='backup'?wf('backup_storage','Backup storage ID (available on every target node)','local','text','required'):op==='restore'?wf('snapshot','Existing snapshot name','','text','required')+'<p>All targets must be stopped. Their current state will be replaced by this snapshot.</p>':op==='shutdown'?'<p>Wait up to 120 seconds for a graceful shutdown. Never force power off on timeout.</p>':op==='stop'?'<p>Force-stop can lose unsaved data. The next step requires separate confirmation.</p>':op==='readiness'?'<p>Check guest agent, configured TCP endpoint, saved credentials and WinRM/SSH runtime. Configure a guest connection first.</p>':'';
    drawTasks();
  };$('#manage-fields [name=operation]').onchange=draw;draw();
}
function planHTML(p){return `<dl class="review-grid"><dt>Operation</dt><dd>${esc(p.operation)}</dd><dt>Snapshots before tasks</dt><dd>${p.snapshot_before?'Yes':'No'}</dd><dt>Settings</dt><dd>${esc(JSON.stringify(p.settings))}</dd></dl><h3>Exact targets (${p.targets.length})</h3><ul>${p.targets.map(t=>`<li>${esc(t.node)}:${t.vmid} / ${esc(t.name)}${t.guest_host?`<br>Guest: ${esc(t.guest_host)}:${t.port} / ${esc(t.platform)} / Credential: ${esc(t.credential_name)}${t.platform==='windows'?` / TLS verification: ${t.verify_tls?'on':'OFF'}`:''}`:''}</li>`).join('')}</ul>`+(p.tasks.length?'<h3>Task order</h3><ol>'+p.tasks.map(t=>`<li>${esc(t.name)} ${esc(JSON.stringify(t.params))}</li>`).join('')+'</ol>':'')+'<ul>'+p.notes.map(n=>`<li>${esc(n)}</li>`).join('')+'</ul>';}
function reviewOperation(){
  const p=mgPlan;
  mgDialog('2. Review this operation',planHTML(p)+wc('confirmed','I confirm this operation on exactly these VMs.')+(p.dangerous?wc('risk_confirmed','I understand the destructive effects / have reviewed and trust these scripts.'):'')+(['tasks','backup','shutdown','start','readiness'].includes(p.operation)?'<p>'+mgButton('Save as schedule instead','schedule-plan')+'</p>':''),async d=>{
    await api('manage/run',{plan_id:p.id,confirmed:d.confirmed,risk_confirmed:!!d.risk_confirmed});$('#manage-dialog').close();view='jobs';await refresh();notice('Reviewed operation queued. Follow each VM result in Activity.');
  },'Run reviewed operation');
}
function editSchedule(record){
  const p=record?.summary||mgPlan;
  mgDialog('Schedule reviewed operation',planHTML(p)+'<div class="form-grid">'+wf('name','Schedule name',record?.name||'Maintenance','text','required')+wf('interval_minutes','Repeat every (minutes)',record?.interval_minutes||1440,'number','required min="15" max="525600"')+wf('window_start','UTC window start hour',record?.window_start??0,'number','required min="0" max="23"')+wf('window_end','UTC window end hour (exclusive)',record?.window_end??24,'number','required min="1" max="24"')+wc('enabled','Enable recurring runs',record?.enabled||false)+wc('confirmed','I confirm this recurring operation on exactly the listed VMs.')+(p.dangerous?wc('risk_confirmed','I understand and approve these tasks for recurring execution.'):'')+'<p class="wide">First run is at least one interval after saving, aligned to the next open maintenance window. Missed runs, runs outside the window, and overlapping runs are skipped. Editing a task or group does not change this saved plan. To change targets/tasks or refresh credentials, create a new reviewed schedule.</p></div>',d=>saveRecord('schedules',{...d,id:record?.id,plan_id:record?undefined:p.id}));
}

document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-mg]');if(!b)return;
  const a=b.dataset.mg,id=b.dataset.record;
  if(b.disabled)return;
  setButtonBusy(b,true);
  try{
    if(!mg)await loadManagement();
    if(a==='build-default')editBuildDefault(id);
    else if(a==='new-credentials')editCredential();
    else if(a==='new-tasks')editScript();
    else if(a==='new-groups')editGroup();
    else if(a==='new-recipes')openWizard();
    else if(a==='new-schedules'||a==='apply')applyOperation();
    else if(a==='readiness')applyOperation({operation:'readiness'});
    else if(a==='manage-vm'){const g=mgGuests().find(g=>g.vmid===Number(id));applyOperation({operation:g.status==='stopped'?'start':'shutdown',selected:[g]})}
    else if(a==='connection')editConnection(id);
    else if(a==='snapshots'){
      const g=mgGuests().find(g=>g.vmid===Number(id));const snaps=await api(`manage/snapshots/${encodeURIComponent(g.node)}/${g.vmid}`);
      mgDialog('VM snapshots',snaps.filter(s=>s.name!=='current').map(s=>`<p><strong>${esc(s.name)}</strong> ${esc(s.description||'')}</p>`).join('')||'<p>No snapshots yet.</p>',()=>{$('#manage-dialog').close();applyOperation({operation:'restore',selected:[g]})},'Choose snapshot to restore');
    }
    else if(a==='use-task')await openScriptRun([],id);
    else if(a==='use-group')applyOperation({groups:[id]});
    else if(a==='build-recipe')await buildRecipe(mg.recipes.find(r=>r.id===id));
    else if(a==='apply-recipe'){const r=mg.recipes.find(r=>r.id===id);applyOperation({tasks:r.tasks});notice('Use the recipe credential when configuring each VM connection: '+(mg.credentials.find(c=>c.id===r.credential_id)?.name||'choose a guest credential'));}
    else if(a==='save-wizard-recipe')await saveWizardRecipe();
    else if(a==='schedule-plan')editSchedule();
    else if(a.startsWith('edit-')){const kind=a.slice(5),r=mg[kind].find(r=>r.id===id);({credentials:editCredential,tasks:editScript,groups:editGroup,recipes:editRecipe,schedules:editSchedule})[kind](r)}
    else if(a.startsWith('delete-')){const kind=a.slice(7);mgDialog('Delete saved record','<p>Remove this saved configuration? Existing VMs, snapshots and job history are retained. Referenced records cannot be deleted.</p>'+wc('confirmed','Delete this record.'),async d=>{if(!d.confirmed)throw Error('Confirm deletion');await api(`manage/${kind}/${id}/delete`,{});$('#manage-dialog').close();await loadManagement();render()},'Delete record')}
  }catch(err){notice(err.message);if($('#manage-dialog').open)$('#manage-error').textContent=err.message}
  finally{setButtonBusy(b,false)}
});
$('#manage-close').onclick=()=>$('#manage-dialog').close();
$('#manage-dialog').addEventListener('close',event=>{
  if(event.currentTarget.open)return;
  $('#manage-fields').replaceChildren();mgSave=null;mgSelectedTasks=[];mgPlan=null;
});
$('#manage-form').onsubmit=async e=>{
  e.preventDefault();const button=$('#manage-submit');
  if(button.disabled||!mgSave)return;
  setButtonBusy(button,true);
  const save=mgSave;
  try{await save(mgValues())}catch(err){if(mgSave===save)$('#manage-error').textContent=err.message}finally{if(mgSave===save)setButtonBusy(button,false)}
};
document.addEventListener('click',e=>{if(e.target.closest('[data-view]')&&managementViews.includes(view)){loadManagement().then(()=>render()).catch(err=>notice(err.message))}});
