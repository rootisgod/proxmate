/* Recovery and template versions use frozen, server-reviewed lifecycle plans. */
const lcViews=['recovery','versions'];
let lcData=null,lcLoading=false,lcLoaded=0;
const lcButton=(text,action,id='')=>`<button type="button" data-lc="${action}" data-record="${esc(id)}">${text}</button>`;
function hydrateBootstrapDraft(d,b={}){for(const key of ['enabled','verify','management_cidr','agent_iso','snippet_storage','credential_id','host','timeout'])d['bootstrap_'+key]=b?.[key]??(key==='enabled'||key==='verify'?false:'');}
function bootstrapPayload(d){
  if(!d.bootstrap_enabled||d.kind==='linux-iso')return null;
  const b={enabled:true,verify:d.purpose==='vm'&&!!d.bootstrap_verify};
  if(d.kind==='windows')Object.assign(b,{management_cidr:d.bootstrap_management_cidr,agent_iso:d.bootstrap_agent_iso});
  else b.snippet_storage=d.bootstrap_snippet_storage;
  if(b.verify)Object.assign(b,{credential_id:d.bootstrap_credential_id,host:d.bootstrap_host||'',timeout:d.bootstrap_timeout||1800});
  return b;
}
function bootstrapFields(d,detail){
  if(d.kind==='linux-iso')return '<p>After interactive installation, use the guest bootstrap helper to install SSH/Python and the guest agent.</p>'+lcButton('Download guest helper','helper');
  let html='<fieldset><legend>Guest bootstrap (optional)</legend>'+wc('bootstrap_enabled','Prepare remote management and install the guest agent on first boot',d.bootstrap_enabled);
  if(d.bootstrap_enabled){
    html+='<div class="form-grid">';
    if(d.kind==='windows')html+=wf('bootstrap_management_cidr','Management client IP/CIDR allowed to reach HTTPS WinRM',d.bootstrap_management_cidr||'','text','required')+ws('bootstrap_agent_iso','Trusted VirtIO ISO (signed guest agent + matching serial driver)',detail.isos.map(i=>[i.volid,i.volid]),d.bootstrap_agent_iso)+'<p class="wide">Windows uses HTTPS only, a per-guest certificate, and a firewall rule scoped to this subnet. Select VirtIO media supporting this Windows edition. Passwords are not embedded in the bootstrap script.</p>';
    else html+=ws('bootstrap_snippet_storage','Storage with snippets enabled',detail.storage.filter(s=>s.active&&s.content.split(',').includes('snippets')).map(s=>[s.storage,s.storage]),d.bootstrap_snippet_storage)+'<p class="wide">Cloud-init vendor data installs the guest agent, SSH and Python while preserving Proxmox login/network settings. The guest needs package repository access. Snippets storage must already be enabled on the host.</p>';
    if(d.purpose==='vm')html+=wc('bootstrap_verify','Start this VM and verify guest authentication after bootstrap',d.bootstrap_verify);
    else html+='<p class="wide">Template preparation does not verify guest login; test a clone after preparation.</p>';
    if(d.bootstrap_verify&&d.purpose==='vm')html+=ws('bootstrap_credential_id','Saved credential matching the new guest login',mgCreds(d.kind==='windows'?'windows':'linux'),d.bootstrap_credential_id)+wf('bootstrap_host','Guest IP/DNS (optional when guest agent reports exactly one IPv4)',d.bootstrap_host||'')+wf('bootstrap_timeout','Wait timeout (seconds)',d.bootstrap_timeout||1800,'number','required min="60" max="3600"')+'<p class="wide">Public TLS/SSH identity is retrieved through the authenticated Proxmox guest agent, then WinRM/SSH authentication is tested. A successful check saves the VM connection. This never turns TLS verification off.</p>';
    html+='</div>';
  }
  return html+'</fieldset>';
}
async function loadLifecycle(){lcData=await api('lifecycle/state');lcLoaded=Date.now();}
function lifecycleRender(){
  if(!lcViews.includes(view))return false;
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  if(!lcData||Date.now()-lcLoaded>5000){if(!lcLoading){lcLoading=true;loadLifecycle().then(()=>render()).catch(e=>notice(e.message)).finally(()=>lcLoading=false)}}
  if(!lcData){$('#content').innerHTML='<h1>Loading lifecycle tools...</h1>';return true}
  let html='';
  if(view==='versions'){
    html=title('Template versions','Build, test, then promote. Previous versions stay available.',lcButton('New template family','family'));
    html+='<div class="cards task-cards">'+lcData.families.map(f=>{const preferred=lcData.versions.find(v=>v.id===f.preferred_id);return `<article class="card"><h3>${esc(f.name)}</h3><p>Preferred: ${esc(preferred?.label||'None yet')}</p><div class="row-actions">${lcButton('Build from recipe','build',f.id)}${lcButton('Register version','register',f.id)}${preferred?lcButton('Clone preferred','clone',preferred.id):''}</div></article>`}).join('')+'</div>';
    html+=table(['Version','Template','Test evidence','Actions'],lcData.versions.map(v=>`<tr><td><strong>${esc(lcData.families.find(f=>f.id===v.family_id)?.name)} / ${esc(v.label)}</strong><small>${lcData.families.some(f=>f.preferred_id===v.id)?'Preferred':'Retained'}</small></td><td>${esc(v.node)}:${v.vmid}</td><td>${v.test?.passed?'Isolated boot verified':'Not tested'}${v.test?`<small>Test VM ${v.test.vmid}</small>`:''}</td><td>${lcButton('Provenance','provenance',v.id)} ${lcButton('Test clone','test',v.id)} ${lcButton('Promote','promote',v.id)}</td></tr>`));
    html+='<h2>Recent build candidates</h2>'+table(['Build','Recipe / source','Next step'],lcData.builds.slice().sort((a,b)=>b.created-a.created).slice(0,20).map(b=>`<tr><td>${esc(b.node)}:${b.vmid}<small>${esc(b.config?.name)}</small></td><td>${esc(b.recipe_name||b.config?.profile||b.config?.iso||b.config?.filename||'Wizard')}<small>${b.tasks.length} recorded task runs</small></td><td>Finish installation and tasks; generalize and shut down.${b.family_id?lcButton('Register prepared version','candidate',b.id):''}</td></tr>`));
  }else{
    html=title('Backup recovery','Restore into a fresh VM ID. Test copies stay disconnected and are retained.',lcButton('Review retention','retention'));
    const archives=lcArchives();
    html+=table(['Archive','Node / size','Action'],archives.map((a,i)=>`<tr><td>${esc(a.volid)}<small>${a.protected?'Protected':''}</small></td><td>${esc(a.node)}<small>${(a.size/1073741824).toFixed(2)} GB</small></td><td>${lcButton('Restore / test','restore',i)}</td></tr>`));
    html+='<div class="toolbar"><h2>Backup policies</h2>'+lcButton('Scheduled restore check','new-check')+lcButton('Retention policy','new-retention')+'</div><p>Policies start disabled. Restore checks use new IDs from a reserved range and retain test VMs. Retention defaults to reporting candidates without deleting them.</p>';
    html+=table(['Policy','Scope / schedule','Latest result','Action'],lcData.policies.map(p=>`<tr><td>${esc(p.name)}<small>${esc(p.mode)} / ${p.enabled?'Enabled':'Disabled'}</small></td><td>${esc(p.node)} / source VM ${p.source_vmid}<small>Every ${p.interval_minutes} min; UTC ${p.window_start}:00-${p.window_end}:00<br>Next ${new Date(p.next_run*1000).toISOString()}</small></td><td>${esc(p.last_status||p.last_report)}${p.last_job?`<small><button data-log="${esc(p.last_job)}">Report</button></small>`:''}</td><td>${lcButton('Edit / disable','policy',p.id)}</td></tr>`));
    html+='<h2>Successful recovery checks</h2>'+table(['Destination','Check','Job'],lcData.reports.slice().reverse().map(r=>`<tr><td>${esc(r.node)}:${r.vmid}</td><td>${r.boot_verified?'Restore/clone + guest-agent boot verified':'Restore/clone only; not boot tested'}</td><td><button data-log="${esc(r.id)}">View report</button></td></tr>`));
  }
  $('#content').innerHTML=html;return true;
}
function lcArchives(){return Object.entries(state?.details||{}).flatMap(([node,d])=>(d.backups||[]).filter(a=>/:backup\/(vzdump-qemu-|vm\/)/.test(a.volid)).map(a=>({...a,node})));}
function lcNodeOptions(){return (state?.nodes||[]).filter(n=>n.status==='online').map(n=>[n.node,n.node]);}
function lcStorage(node){return (state?.details[node]?.storage||[]).filter(s=>s.active&&s.content.split(',').includes('images')).map(s=>[s.storage,s.storage]);}
function lcConfirm(plan){
  let html=`<h3>${esc(plan.operation)}</h3>`;
  if(plan.vmid)html+=`<p>Destination: <strong>${esc(plan.node)}:${plan.vmid}</strong> / ${esc(plan.name||plan.label||'')}</p>`;
  if(plan.archive)html+=`<p>Archive: ${esc(plan.archive)}</p>`;
  if(plan.disconnected!==undefined)html+=`<p>Networking: <strong>${plan.disconnected?'Disconnected':'Original network retained; VM stays stopped'}</strong>. Test boot: ${plan.boot_test?'Yes':'No'}.</p>`;
  if(plan.candidates)html+='<h3>Exactly these archives will be deleted ('+plan.candidates.length+')</h3><ul>'+plan.candidates.map(a=>`<li>${esc(a.volid)} (${(a.size/1073741824).toFixed(2)} GB)</li>`).join('')+'</ul>';
  if(plan.tasks)html+='<h3>Recipe task order</h3>'+plan.tasks.map(t=>`<p>${esc(t.name)} ${esc(JSON.stringify(t.params))}</p>${t.body?`<details><summary>Review saved script</summary><pre>${esc(t.body)}</pre></details>`:''}`).join('');
  html+='<details><summary>Plan details and provenance</summary><pre>'+esc(JSON.stringify(plan,null,2))+'</pre></details><ul>'+plan.notes.map(n=>`<li>${esc(n)}</li>`).join('')+'</ul>'+wc('confirmed','Execute exactly this reviewed plan.')+(plan.dangerous?wc('risk_confirmed','I approve these archive deletions / have reviewed and trust the listed scripts.'):'');
  mgDialog('Review lifecycle plan',html,async d=>{await api('lifecycle/run',{plan_id:plan.id,confirmed:d.confirmed,risk_confirmed:!!d.risk_confirmed});$('#manage-dialog').close();view='jobs';await refresh();notice('Lifecycle operation queued. Follow its report in Activity.')},'Run reviewed plan');
}
async function lcReview(payload){lcConfirm(await api('lifecycle/plan',payload));}
function lcRestore(archive,version=null,clone=false){
  const node=version?.node||archive.node;
  mgDialog(version?(clone?'Clone template version':'Test template on a disconnected clone'):'Restore backup into a new VM','<div class="form-grid">'+wf('vmid','Unused destination VM ID',state?.next_id||9000,'number','required min="100"')+wf('name','Destination name',(clone?'clone-':'restore-test-')+(state?.next_id||9000),'text','required')+ws('storage','Destination disk storage',lcStorage(node))+(version&&!clone?'<p class="wide">A full clone will be disconnected, boot-tested through the guest agent, gracefully shut down and retained. This does not verify application/data correctness.</p>':wc('disconnected','Disconnect all network interfaces before any test boot',!clone)+wc('boot_test','Boot-test through guest agent, gracefully shut down, and retain the copy',false))+wf('timeout','Guest-agent wait timeout (seconds)',300,'number','required min="30" max="1800"')+'</div>',d=>lcReview({...d,node,operation:version?(clone?'version-clone':'template-test'):'backup-restore',archive:archive?.volid,version_id:version?.id,...(version&&!clone?{boot_test:true,disconnected:true}:{})}),'Review restore');
}
function lcRetention(){
  mgDialog('Review backup retention','<div class="form-grid">'+ws('node','Backup node',lcNodeOptions())+wf('backup_storage','Backup storage ID','local','text','required')+wf('source_vmid','Original VM ID',201,'number','required min="100"')+wf('keep','Always retain at least the newest count',3,'number','required min="1"')+wf('older_than_days','Only delete archives older than days',30,'number','required min="1"')+'<p class="wide">Protected archives are always retained. The next step lists exact deletion candidates; nothing is deleted during review.</p></div>',d=>lcReview({...d,operation:'backup-retention'}),'Find candidates');
}
function lcRegister(familyId,candidate){
  const guests=(state?.resources||[]).filter(r=>r.type==='qemu');
  mgDialog('Register a prepared template version','<div class="form-grid">'+ws('guest','Prepared guest/template',guests.map(g=>[g.node+':'+g.vmid,`${g.name} / ${g.node}:${g.vmid}`]),candidate?candidate.node+':'+candidate.vmid:undefined)+wf('label','Version label',candidate?.label||new Date().toISOString().slice(0,10),'text','required')+wc('prepared','The guest is generalized, shut down, and setup passwords/cached answer files have been removed or rotated.')+'<p class="wide">A stopped VM will be converted to a template. onboot is disabled. Existing template versions remain unchanged. Windows clones need their own WinRM certificate/identity after Sysprep; do not distribute a setup certificate private key.</p></div>',d=>{const[node,vmid]=d.guest.split(':');return lcReview({operation:'version-register',node,vmid:Number(vmid),family_id:familyId,label:d.label,prepared:d.prepared})},'Review registration');
}
function lcBuild(familyId){
  if(!mg.recipes.length)return notice('Save a recipe in the build wizard first.');
  const draw=(recipeId)=>{
    const r=mg.recipes.find(r=>r.id===recipeId)||mg.recipes[0];
    mgDialog('Build replacement from a saved recipe','<div class="form-grid">'+ws('recipe_id','Build recipe',mg.recipes.map(r=>[r.id,r.name]),r.id)+wf('label','Version label',new Date().toISOString().slice(0,10),'text','required')+wf('vmid','Unused VM ID',state?.next_id||9000,'number','required min="100"')+wf('name','Candidate VM name','template-candidate','text','required')+wc('start','Start this new candidate after preparation',false)+(r.config.kind==='windows'?wf('admin_password','Windows setup administrator password (optional)','','password','autocomplete="new-password"')+wc('accept_eula','I accept the Windows licence terms for this new installation.')+wc('wipe_disk','Format only disk 0 of this NEW candidate.'): '')+'<p class="wide">Recipe tasks run automatically only when its bootstrap verification is enabled and the candidate is started. Otherwise finish installation and apply the recipe tasks separately. Generalization and template registration follow explicitly.</p></div>',d=>lcReview({...d,operation:'version-build',family_id:familyId}),'Review build');
    $('#manage-fields [name=recipe_id]').onchange=e=>draw(e.target.value);
  };draw();
}
function lcPolicy(mode,record={}){
  mgDialog(mode==='restore-check'?'Scheduled restore checks':'Backup retention policy','<div class="form-grid">'+wf('name','Policy name',record.name||(mode==='restore-check'?'Weekly restore check':'Backup retention'),'text','required')+ws('node','Backup node',lcNodeOptions(),record.node)+wf('backup_storage','Backup storage ID',record.backup_storage||'local','text','required')+wf('source_vmid','Original VM ID',record.source_vmid||201,'number','required min="100"')+wf('interval_minutes','Interval (minutes)',record.interval_minutes||10080,'number','required min="15"')+wf('window_start','UTC window start hour',record.window_start??0,'number','required min="0" max="23"')+wf('window_end','UTC window end hour',record.window_end??24,'number','required min="1" max="24"')+(mode==='restore-check'?wf('storage','Restore disk storage ID',record.storage||'local-lvm','text','required')+wf('range_start','Reserved scratch VM ID range start',record.range_start||9500,'number','required min="100"')+wf('range_end','Reserved scratch VM ID range end',record.range_end||9599,'number','required min="100"')+wf('timeout','Boot check timeout (seconds)',record.timeout||300,'number','required min="30" max="1800"')+'<p class="wide">Restores the newest backup of this VM to an unused ID in this range. Every test VM is disconnected and retained. Clean up inspected copies in Proxmox; occupied IDs are never reused.</p>':wf('keep','Keep newest archives',record.keep||3,'number','required min="1"')+wf('older_than_days','Minimum archive age (days)',record.older_than_days||30,'number','required min="1"')+wc('auto_delete','Automatically delete eligible archives (otherwise report candidates only)',record.auto_delete||false)+wc('risk_confirmed','I approve recurring deletion within this storage/source-VM scope.'))+wc('enabled','Enable this recurring policy',record.enabled||false)+wc('confirmed','I confirm this exact policy scope, timing and retention/test settings.')+'</div>',async d=>{await api('lifecycle/policies',{...d,id:record.id,mode});$('#manage-dialog').close();await loadLifecycle();render()},'Save policy');
}
async function lcHelper(){
  mgDialog('Guest bootstrap helper','<div class="form-grid">'+ws('platform','Guest platform',[['linux','Linux: Ubuntu / Fedora / Rocky'],['windows','Windows x64']])+wf('management_cidr','Windows: management client IP/CIDR (HTTPS WinRM)','','text')+ws('agent_iso','Windows: selected trusted VirtIO ISO',Object.values(state?.details||{}).flatMap(d=>d.isos||[]).map(i=>[i.volid,i.volid]))+'<p class="wide">Download and inspect this helper, then run it elevated inside the intended guest. Downloading does not execute it. Linux enables SSH and installs Python/guest agent. Windows installs signed guest drivers/agent and enables HTTPS WinRM only for the chosen subnet.</p></div>',async d=>{
    const response=await fetch('/api/bootstrap/helper',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(d)});
    if(!response.ok)throw Error((await response.json()).error);
    const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=d.platform==='linux'?'proxmate-bootstrap.sh':'ProxmateBootstrap.ps1';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  },'Download helper');
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-lc]');if(!b)return;
  try{
    if(!lcData)await loadLifecycle();if(!mg)await loadManagement();
    const id=b.dataset.record,action=b.dataset.lc,v=lcData.versions.find(v=>v.id===id);
    if(action==='family')mgDialog('New template family',wf('name','Family name','','text','required'),async d=>{await api('lifecycle/families',d);$('#manage-dialog').close();await loadLifecycle();render()});
    else if(action==='restore')lcRestore(lcArchives()[Number(id)]);
    else if(action==='retention')lcRetention();
    else if(action==='test'||action==='clone')lcRestore(null,v,action==='clone');
    else if(action==='promote')await lcReview({operation:'version-promote',version_id:id});
    else if(action==='register')lcRegister(id);
    else if(action==='candidate'){const candidate=lcData.builds.find(b=>b.id===id);lcRegister(candidate.family_id,candidate)}
    else if(action==='build')lcBuild(id);
    else if(action==='provenance')mgDialog('Version provenance',`<pre>${esc(JSON.stringify(v,null,2))}</pre>`,()=>$('#manage-dialog').close(),'Close');
    else if(action==='new-check')lcPolicy('restore-check');
    else if(action==='new-retention')lcPolicy('retention');
    else if(action==='policy'){const p=lcData.policies.find(p=>p.id===id);lcPolicy(p.mode,p)}
    else if(action==='helper')await lcHelper();
  }catch(err){notice(err.message);if($('#manage-dialog').open)$('#manage-error').textContent=err.message}
});
