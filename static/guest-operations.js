/* Operations on existing, explicitly selected Proxmox guests. */
function powerButton(guest) {
  if(!['running','stopped'].includes(guest.status))return '';
  return '<button type="button" data-guest-power="'+esc(machineKey(guest))+'">'+(guest.status==='running'?'Shut down':'Power on')+'</button>';
}

document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-guest-power],[data-bulk-power]');
  if(!button)return;
  const menu=button.closest('details');
  if(menu)menu.open=false;
  try{
    if(button.dataset.bulkPower){
      await openPowerRun(button.dataset.bulkPower,mgGuests().filter(g=>machineFilters.selected.has(machineKey(g))));
    }else{
      const guest=view==='machine'&&machinePage?.data?machinePage.data:mgGuests().find(g=>machineKey(g)===button.dataset.guestPower);
      if(guest&&machineKey(guest)===button.dataset.guestPower)await openPowerRun(guest.status==='running'?'shutdown':'start',[guest]);
    }
  }catch(err){notice(err.message)}
});

function chooseRunTargets(titleText, next) {
  if (!mgGuests().length) return notice('No virtual machines are available. Open Create to build one.');
  mgDialog(titleText, targetFields(), async () => {
    const keys = new Set(selectedTargets().map(machineKey));
    const guests = mgGuests().filter(g => keys.has(machineKey(g)));
    if (!guests.length) throw Error('Select at least one machine.');
    await next(guests);
  }, 'Continue');
}

function targetSummary(guests) {
  return '<ul class="run-targets" aria-label="Selected machines">'+guests.map(g =>
    '<li><strong>'+esc(g.name || 'VM '+g.vmid)+'</strong> · '+esc(machineKey(g))+'</li>').join('')+'</ul>';
}

function reviewGuestOperation(plan, back, source='') {
  const label = {tasks:'Run script', start:'Power on', shutdown:'Shut down'}[plan.operation];
  const task = plan.tasks[0];
  const settings = Object.entries(task?.params || {});
  const fields = (task?'<h3>'+esc(task.name)+'</h3>':'')+
    (settings.length?'<dl class="review-grid">'+settings.map(([k,v])=>'<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>').join('')+'</dl>':'')+
    (source?'<details><summary>Script source</summary><pre class="run-source">'+esc(source)+'</pre></details>':'')+
    '<h3>Target machines</h3><ul class="run-targets">'+plan.targets.map(t=>'<li><strong>'+esc(t.name||'VM '+t.vmid)+'</strong> · '+esc(machineKey(t))+
      (t.guest_host?'<small>'+esc(t.credential_name)+' · '+esc(t.guest_host)+' · '+esc(t.platform==='windows'?'WinRM':'SSH')+'</small>':'')+'</li>').join('')+'</ul>'+
    (plan.dangerous?wc('risk_confirmed','I have reviewed and trust this script on the listed machines.'):'')+
    '<button type="button" data-edit-run>Back</button>';
  mgDialog('Review '+(task?'script run':label.toLowerCase()),fields,async values=>{
    if(plan.dangerous&&!values.risk_confirmed)throw Error('Confirm that you trust the script before running it.');
    await api('manage/run',{plan_id:plan.id,confirmed:true,risk_confirmed:!!values.risk_confirmed});
    $('#manage-dialog').close();view='jobs';await refresh();
    notice(label+' queued for '+plan.targets.length+' machine'+(plan.targets.length===1?'':'s')+'. Follow the job in Progress.');
  },label);
  $('#manage-fields [data-edit-run]').onclick=back;
}

async function openPowerRun(operation,guests) {
  if(!['start','shutdown'].includes(operation))throw Error('Choose a power operation.');
  const label=operation==='start'?'Power on':'Graceful shutdown';
  if(!guests.length)return chooseRunTargets(label, selected=>openPowerRun(operation,selected));
  const wrong=guests.find(g=>g.status!==(operation==='start'?'stopped':'running'));
  if(wrong)throw Error((wrong.name||machineKey(wrong))+' is '+wrong.status+'. Select '+(operation==='start'?'stopped':'running')+' machines for this action.');
  const targets=guests.map(({node,vmid})=>({node,vmid}));
  const loading=()=>{};
  mgDialog(label,'<p role="status">Checking target machines…</p>',loading,'Review');
  $('#manage-submit').disabled=true;
  try{
    const plan=await api('manage/plan',{operation,targets});
    if(mgSave!==loading||!$('#manage-dialog').open)return;
    reviewGuestOperation(plan,()=>chooseRunTargets(label,selected=>openPowerRun(operation,selected)));
  }catch(err){
    if(mgSave===loading){
      chooseRunTargets(label,selected=>openPowerRun(operation,selected));
      $('#manage-error').textContent=err.message;
    }
  }
}

function oneOffInputNames(body) {
  const pattern=/\bproxmate_inputs(?:\.([a-z][a-z0-9_]{0,39})\b|\[['"]([a-z][a-z0-9_]{0,39})['"]\])/g;
  return [...new Set(Array.from(body.matchAll(pattern), match=>match[1]||match[2]))];
}

async function openScriptRun(guests=[],taskId='',draft=null) {
  if(!state){view='server';render();return}
  if(!guests.length)return chooseRunTargets('Choose machines for the script',selected=>openScriptRun(selected,taskId));
  if(guests.length>100)throw Error('Choose up to 100 machines per run.');
  if(taskId==='windows-domain-controller'&&guests.length!==1)throw Error('Choose one Windows Server for the first domain controller.');
  const stopped=guests.find(g=>g.status!=='running');
  if(stopped)throw Error('Start '+(stopped.name||machineKey(stopped))+' before running a guest script.');
  const loading=()=>{};
  mgDialog('Run script','<p role="status">Checking machine logins and compatible scripts…</p>',loading,'Review run');
  $('#manage-submit').disabled=true;
  const active=()=>mgSave===loading&&$('#manage-dialog').open;
  try{
    const [scripts,connections,credentials]=await Promise.all(['tasks','connections','credentials'].map(k=>api('manage/'+k)));
    if(!active())return;
    const targets=guests.map(({node,vmid})=>({node,vmid}));
    const saved=targets.map(t=>connections.find(c=>machineKey(c)===machineKey(t)));
    const missing=guests.filter((_,i)=>!saved[i]);
    const known=new Set(saved.filter(Boolean).map(c=>c.platform));
    const selected=scripts.find(t=>t.id===taskId);
    if(taskId&&!selected)throw Error('This script is no longer in the library.');
    if(selected)known.add(selected.platform);
    if(missing.length){
      const {platforms}=await api('simple/software',{targets:missing.map(({node,vmid})=>({node,vmid}))});
      if(!active())return;
      for(const g of missing){const hint=platforms?.[machineKey(g)];if(hint)known.add(hint)}
    }
    if(known.size>1)throw Error('Choose machines and a script for the same operating system.');
    const platform=[...known][0]||'';
    scriptRunForm({guests,targets,scripts,credentials,missing,platform},
      draft?{...draft,os:platform||draft.os}:{os:platform,mode:'library',script:taskId,kind:'ansible',body:'',params:{}});
  }catch(err){
    if(active()){
      $('#manage-fields').innerHTML='<button type="button" data-change-targets>Choose machines</button>';
      $('#manage-fields [data-change-targets]').onclick=()=>openScriptRun([],taskId);
      $('#manage-error').textContent=err.message;
    }
  }
}

function scriptRunForm(context,draft) {
  const {guests,targets,scripts,credentials,missing,platform}=context;
  const osField=platform?'':ws('os','Guest operating system',[['','Choose Windows or Linux'],['windows','Windows'],['linux','Linux']],draft.os);
  mgDialog('Run script',targetSummary(guests)+osField+
    '<div id="run-setup"></div>',async()=>{
    if(!draft.os)throw Error('Choose the guest operating system.');
    const form=$('#manage-form'),fields=form.elements;
    const params={};
    draft.params={};
    for(const el of form.querySelectorAll('[data-run-param]')){
      const key=el.dataset.runParam;
      params[key]=paramValue(el);
      if(el.type!=='password')draft.params[key]=params[key];
    }
    const payload={targets};
    let source='';
    if(draft.mode==='library'){
      const task=scripts.find(t=>t.id===fields.script.value&&t.platform===draft.os);
      if(!task)throw Error('Choose a library script.');
      payload.tasks=[{id:task.id,params}];source=task.body||'';
    }else{
      source=fields.body.value;
      payload.one_off={platform:draft.os,kind:fields.kind.value,body:source,
        parameters:Object.fromEntries(Object.keys(params).map(key=>[key,key])),params};
    }
    if(missing.length){
      if(fields.credential_id.value)payload.credential_id=fields.credential_id.value;
      else {
        payload.credential={platform:draft.os,username:fields.guest_username.value,password:fields.guest_password.value};
        if(fields.private_key?.value)payload.credential.private_key=fields.private_key.value;
      }
    }
    const current=mgSave;
    const plan=await api('simple/existing-plan',payload);
    if(missing.length)mg=null;
    if(mgSave===current&&$('#manage-dialog').open)
      reviewGuestOperation(plan,()=>openScriptRun(guests,draft.mode==='library'?draft.script:'',draft),source);
  },'Review run');
  const setup=$('#run-setup');
  let driveRequest;
  function renderParams(parameters,values,types={}) {
    const entries=Object.entries(parameters);
    const box=$('#run-params');
    box.innerHTML=entries.length?'<fieldset><legend>Parameters</legend><div class="form-grid">'+entries.map(([key,label])=>
      '<label>'+esc(label)+paramControl(scriptInputValue(types,key),scriptInputValue(values,key),'data-run-param="'+esc(key)+'"')+'</label>').join('')+'</div></fieldset>':'';
    const drives=[...box.querySelectorAll('[data-run-param]')].filter(el=>scriptInputValue(types,el.dataset.runParam)?.type==='drive');
    if(!drives.length)return;
    for(const el of drives)el.innerHTML='<option value="">Checking drive letters…</option>';
    const retry=document.createElement('button');retry.type='button';retry.textContent='Retry drive discovery';retry.hidden=true;box.append(retry);
    async function loadLetters(){
      retry.hidden=true;
      try{
        driveRequest??=api('simple/drive-letters',{targets});
        const {occupied}=await driveRequest;
        if(!retry.isConnected)return;
        for(const el of drives){
          const key=el.dataset.runParam;
          el.outerHTML=paramControl(types[key],scriptInputValue(values,key),'data-run-param="'+esc(key)+'"',occupied);
        }
      }catch(err){
        driveRequest=null;if(!retry.isConnected)return;
        for(const el of drives)el.innerHTML='<option value="">Drive discovery unavailable</option>';
        retry.hidden=false;$('#manage-error').textContent=err.message;
      }
    }
    retry.onclick=()=>{$('#manage-error').textContent='';loadLetters()};loadLetters();
  }
  function scriptFields(){
    const holder=$('#run-script'), library=draft.mode==='library';
    if(library){
      const available=scripts.filter(t=>t.platform===draft.os&&(t.id!=='windows-domain-controller'||guests.length===1));
      const choices=[['','Choose a script'],...available.slice().sort((a,b)=>Number(!!b.kind)-Number(!!a.kind)||a.name.localeCompare(b.name)).map(t=>[t.id,t.name])];
      holder.innerHTML=ws('script','Library script',choices,draft.script)+'<div id="run-params"></div><div id="run-script-details"></div>';
      const select=holder.querySelector('select');select.required=true;
      const choose=()=>{
        const task=available.find(t=>t.id===select.value);
        if(select.value!==draft.script)draft.params={};
        draft.script=select.value;
        renderParams(task?.parameters||{},{...task?.defaults,...draft.params},task?.input_types);
        $('#run-script-details').innerHTML=(task?.builtin?'<p>'+esc(task.description)+'</p>':'')+
          (task?.body?'<details><summary>View script source</summary><pre class="run-source">'+esc(task.body)+'</pre></details>':'');
      };
      select.onchange=choose;choose();
    }else{
      const languages=Object.entries(scriptFormats).filter(([kind])=>kind!=='shell'||draft.os==='linux');
      holder.innerHTML=ws('kind','Language',languages,draft.kind)+'<label>Script source<textarea name="body" required maxlength="100000" rows="8" spellcheck="false"></textarea></label><small id="run-syntax-help"></small><div id="run-params"></div>';
      const body=holder.querySelector('textarea'),kind=holder.querySelector('select');body.value=draft.body;
      let lastKeys=null;
      const inputs=()=>{
        draft.body=body.value;draft.kind=kind.value;
        const keys=kind.value==='ansible'?oneOffInputNames(body.value):[];
        if(JSON.stringify(keys)!==lastKeys){
          for(const input of holder.querySelectorAll('[data-run-param]'))draft.params[input.dataset.runParam]=input.value;
          renderParams(Object.fromEntries(keys.map(key=>[key,key])),draft.params);lastKeys=JSON.stringify(keys);
        }
        $('#run-syntax-help').textContent=kind.value==='ansible'?'Paste a guest task list. Use {{ proxmate_inputs.name }} for a run parameter.':kind.value==='powershell'&&draft.os==='linux'?'PowerShell on Linux requires pwsh in the guest.':'';
      };
      body.oninput=kind.onchange=inputs;inputs();
    }
  }
  function setupFields(){
    if(!draft.os){setup.replaceChildren();return}
    setup.innerHTML='<div class="script-run-mode" role="group" aria-label="Script source">'+
      [['library','Library script'],['once','One-off script']].map(([mode,label])=>'<button type="button" data-run-mode="'+mode+'" aria-pressed="'+(mode===draft.mode)+'">'+label+'</button>').join('')+
      '</div><div id="run-script"></div><div id="run-login"></div>';
    for(const button of setup.querySelectorAll('[data-run-mode]'))button.onclick=()=>{
      if(button.dataset.runMode===draft.mode)return;
      draft.mode=button.dataset.runMode;draft.params={};
      for(const other of setup.querySelectorAll('[data-run-mode]'))other.setAttribute('aria-pressed',String(other===button));
      scriptFields();
    };
    scriptFields();
    const login=$('#run-login');
    if(!missing.length){login.innerHTML='<p>Using the saved guest login for each machine.</p>';return}
    const allowed=credentials.filter(c=>c.platform===draft.os);
    const choices=[['','Enter a new login'],...allowed.map(c=>[c.id,c.name+' · '+c.username])];
    login.innerHTML='<fieldset><legend>Missing guest login</legend><p>'+missing.map(g=>esc(g.name||machineKey(g))).join(', ')+
      '</p>'+ws('credential_id','Login to save for these machines',choices,allowed.length===1?allowed[0].id:'')+
      '<div class="form-grid" id="run-new-login">'+wf('guest_username','Username',draft.os==='windows'?'Administrator':'ubuntu','text','required autocomplete="off"')+
      wf('guest_password','Password','','password','autocomplete="new-password"')+
      (draft.os==='linux'?mgArea('private_key','SSH private key (optional)'):'')+'</div></fieldset>';
    const chooser=login.querySelector('select');
    const toggle=()=>{
      const area=$('#run-new-login');area.hidden=Boolean(chooser.value);
      area.querySelectorAll('input,textarea').forEach(el=>el.disabled=area.hidden);
      area.querySelector('[name=guest_password]').required=draft.os==='windows';
    };
    chooser.onchange=toggle;toggle();
  }
  const os=$('#manage-form').elements.os;
  if(os)os.onchange=()=>{draft.os=os.value;draft.script='';draft.kind='ansible';draft.params={};setupFields()};
  setupFields();
}
