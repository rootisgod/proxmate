let machinePage=null;

function currentMachine(page){
  return view==='machine'&&machinePage===page&&serverInfo?.id===page.server;
}

async function openMachine(target, push=false){
  const page={target,server:serverInfo.id};
  if(push)history.pushState({},'', '#machine/'+[page.server,target.node,target.vmid].map(encodeURIComponent).join('/'));
  machinePage=page;view='machine';renderMachine();
  try{
    const data=await api('simple/machine',target);
    if(!currentMachine(page))return;
    page.data=data;renderMachine();
    try{
      const result=await api('simple/machine/backup',{...target,identity:data.identity});
      page.backup=result.latest?new Date(result.latest.ctime*1000).toLocaleString()+' · '+result.latest.volid:'No backup found';
    }catch{page.backup='Backup storage unavailable'}
    if(currentMachine(page)&&$('#machine-backup'))$('#machine-backup').textContent=page.backup;
  }catch(err){
    if(currentMachine(page)){page.error=err.message;renderMachine()}
  }
}

function restoreMachineLocation(){
  const parts=location.hash.split('/');
  if(parts[0]!=='#machine'||!serverInfo?.connected||parts[1]!==serverInfo.id||parts.length!==4||!/^\d+$/.test(parts[3]))return false;
  const target={node:parts[2],vmid:Number(parts[3])};
  if(!machinePage||!currentMachine(machinePage)||machineKey(machinePage.target)!==machineKey(target))openMachine(target);
  return true;
}

window.addEventListener('hashchange',()=>{
  if(!restoreMachineLocation()&&view==='machine'){view='machines';machinePage=null;render()}
});

function renderMachine(){
  const page=machinePage;
  if(!page||page.server!==serverInfo?.id){view='machines';render();return}
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view==='machines'));
  const back='<button class="machine-back" data-view="machines">← Machines</button>';
  const d=page.data;
  if(!d){
    $('#content').innerHTML=back+'<p role="status">'+esc(page.error||'Reading machine details…')+'</p>';
    return;
  }
  const guestName=d.name||'VM '+d.vmid;
  const url=new URL(serverInfo.url);
  url.search=new URLSearchParams({console:'kvm',novnc:'1',node:d.node,vmid:String(d.vmid),vmname:guestName});
  const connection=d.connection;
  const connectable=d.status==='running'&&Boolean(d.identity)&&d.addresses.length>0;
  const os=d.os||(d.platform==='windows'?'Windows':d.platform==='linux'?'Linux':'Unknown');
  const guidance=d.status!=='running'?'Start the machine to discover its guest network and test a login.':
    !d.identity?'A stable VM identity is required to verify a login. Check the VM configuration in Proxmox.':
    !d.addresses.length?'Enable the QEMU Guest Agent in Proxmox and start its service inside the guest, then refresh.':
    d.verified_at?'Last verified '+new Date(d.verified_at*1000).toLocaleString():
    connection.host?'A connection is saved. Test its login to verify remote access.':
    'Test a guest login to run software on this machine.';
  const rows=d.jobs.map(j=>`<li><button data-log="${esc(j.id)}"><span>${esc(j.operation)}</span><span class="pill ${esc(j.status)}">${esc(j.status)}</span></button></li>`).join('');
  const hosts=d.addresses.map(i=>`<li><span>${esc(i.host)} <small>${esc(i.interface)}</small></span><button data-copy-ip="${esc(i.host)}" aria-label="Copy ${esc(i.host)}">Copy IP</button></li>`).join('');
  $('#content').innerHTML=back+title(esc(guestName),esc(d.node)+' · VM '+esc(d.vmid),
    `<div class="row-actions machine-controls">${mgButton(d.status==='running'?'Shut down':'Start machine','manage-vm',d.vmid)} <a class="console-link" target="_blank" rel="noopener noreferrer" href="${esc(url.href)}">Open console ↗</a></div>`)+
    `<section class="panel machine-facts" aria-label="Machine details"><div>Status<strong><span class="pill ${esc(d.status)}">${esc(d.status)}</span></strong></div><div>Operating system<strong>${esc(os)}</strong></div><div>Processors<strong>${esc(d.cores??'—')} vCPU</strong></div><div>Memory<strong>${d.memory?(d.memory/1073741824).toLocaleString()+' GiB':'—'}</strong></div></section>
    <div class="machine-panels"><section class="panel machine-card"><h2>Guest connection</h2><p>${esc(guidance)}</p>${connection.host?`<p>${esc(connection.platform==='windows'?'HTTPS WinRM':'SSH')} · ${esc(connection.host)} · port ${esc(connection.port)}</p>`:''}
    <div class="row-actions">${connectable?`<button id="machine-connect" class="${d.verified_at?'':'primary'}">${d.verified_at?'Retest login':connection.host?'Verify login':'Connect this machine'}</button>`:''}
    ${connection.host?`<button data-simple="apply" data-id="${esc(d.vmid)}" class="${d.verified_at?'primary':''}">Run software</button>`:''}
    ${mgButton('Manual settings','connection',d.vmid)}</div></section>
    <section class="panel machine-card"><h2>Network</h2>${hosts?`<ul class="address-list">${hosts}</ul>`:'<p>No guest addresses reported.</p>'}</section></div>
    <section class="panel machine-card"><h2>Latest backup for VM ${esc(d.vmid)}</h2><p id="machine-backup">${esc(page.backup||'Checking backup storage…')}</p></section>
    <section class="recent-jobs"><h2>Recent jobs for VM ${esc(d.vmid)}</h2>${rows?`<ul>${rows}</ul>`:'<p>No matching jobs in the last 100 operations.</p>'}</section>`;
  if($('#machine-connect'))$('#machine-connect').onclick=()=>connectMachine(page);
}

function connectMachine(page){
  const d=page.data, previous=d.connection;
  const hosts=d.addresses.map(i=>[i.host,i.host+(i.interface?' · '+i.interface:'')]);
  if(previous.host&&!hosts.some(([host])=>host===previous.host))hosts.push([previous.host,previous.host+' (saved)']);
  const preferred=previous.host||(hosts.length===1?hosts[0][0]:'');
  if(!preferred)hosts.unshift(['','Choose a guest address']);
  const platformField=d.platform?'':mgSelect('platform','Operating system',[['','Choose the guest OS'],['windows','Windows'],['linux','Linux']],previous.platform||'');
  mgDialog('Connect '+(d.name||'VM '+d.vmid),'<div class="form-grid">'+platformField+mgSelect('host','Guest address',hosts,preferred)+
    '<div class="wide" id="machine-login"></div></div>',async values=>{
      if(!currentMachine(page))throw Error('Machine selection changed. Reopen the connection.');
      const platform=d.platform||values.platform;
      if(!platform||!values.host)throw Error('Choose the operating system and guest address.');
      const body={...page.target,identity:d.identity,connection_revision:previous.updated??null,platform,host:values.host};
      if(values.credential_id)body.credential_id=values.credential_id;
      else body.credential={username:values.username,password:values.password,private_key:values.private_key};
      $('#manage-submit').textContent='Testing login…';
      try{await api('simple/machine/connect',body)}
      finally{if(currentMachine(page))$('#manage-submit').textContent='Test and save'}
      if(currentMachine(page)){
        $('#manage-dialog').close();mg=null;
        notice('Guest login verified. You can now run software on this machine.');
        await openMachine(page.target);
      }
    },'Test and save');
  function loginFields(){
    const platform=d.platform||$('#manage-form').elements.platform.value;
    const list=d.logins.filter(c=>c.platform===platform).map(c=>[c.id,c.name]);
    $('#machine-login').innerHTML=platform?(mgSelect('credential_id','Guest login',list.concat([['','Enter a new login']]),previous.credential_id||'')+
      '<div class="form-grid" id="new-machine-login">'+wf('username','Username',platform==='windows'?'Administrator':'','text','required autocomplete="off"')+
      wf('password','Password','','password','autocomplete="new-password"')+
      (platform==='linux'?mgArea('private_key','SSH private key (if using a key)'):'')+'</div>'):'';
    const chooser=$('#manage-form').elements.credential_id;
    if(chooser){
      const toggle=()=>{
        const fields=$('#new-machine-login');fields.hidden=Boolean(chooser.value);
        fields.querySelectorAll('input,textarea').forEach(el=>el.disabled=fields.hidden);
      };chooser.onchange=toggle;toggle();
    }
  }
  if(!d.platform)$('#manage-form').elements.platform.onchange=loginFields;
  loginFields();
}

document.addEventListener('click',async e=>{
  const b=e.target.closest('button');
  if(!b)return;
  if(b.dataset.view&&location.hash.startsWith('#machine/')){
    history.pushState({},'',location.pathname+location.search);machinePage=null;
  }
  if(b.dataset.machine){
    const guest=mgGuests().find(r=>machineKey(r)===b.dataset.machine);
    if(guest)await openMachine({node:guest.node,vmid:guest.vmid},true);
  }
  if(b.dataset.copyIp){
    try{await navigator.clipboard.writeText(b.dataset.copyIp);notice('Copied '+b.dataset.copyIp)}
    catch{
      const range=document.createRange();range.selectNodeContents(b.previousElementSibling.firstChild);
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
      notice('Address selected. Use your browser’s Copy command.');
    }
  }
});
