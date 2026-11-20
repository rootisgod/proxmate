let machinePage=null;

function remoteButton(kind,target,server,name,context='') {
  const button=document.createElement('button');
  button.type='button';
  button.textContent=(kind==='rdp'?'Open desktop':'Copy SSH command')+(context?' · '+context:'');
  button.onclick=async()=>{
    if(server!==serverInfo?.id)return;
    button.disabled=true;
    try{
      const remote=await api('simple/machine/remote-access',target);
      if(!button.isConnected || server!==serverInfo?.id)return;
      if(remote.kind!==kind)throw Error('Remote access changed. Open the machine to check its connection.');
      if(kind==='rdp'){
        const host=remote.host.includes(':')?'['+remote.host+']':remote.host;
        const file=new Blob(['full address:s:'+host+':'+remote.port+'\r\nauthentication level:i:2\r\nprompt for credentials:i:1\r\n'],{type:'application/rdp'});
        const link=document.createElement('a');link.href=URL.createObjectURL(file);
        link.download=(name||'VM-'+target.vmid).replace(/[^\w.-]/g,'_')+'.rdp';link.click();
        setTimeout(()=>URL.revokeObjectURL(link.href),1000);
        showToast('Open the downloaded .rdp file in your Remote Desktop client.');
      }else{
        try{await navigator.clipboard.writeText(remote.command);showToast('SSH command copied.')}
        catch{
          let field=button.nextElementSibling;
          if(!field?.matches('[data-ssh-command]')){
            field=document.createElement('input');field.readOnly=true;field.dataset.sshCommand='';
            field.setAttribute('aria-label','SSH command to copy');button.after(field);
          }
          field.value=remote.command;field.focus();field.select();
          showToast('SSH command selected. Use your browser’s Copy command.');
        }
      }
    }catch(err){showToast(err.message,{kind:'failure'})}
    finally{button.disabled=false}
  };
  return button;
}

function currentMachine(page){
  return view==='machine'&&machinePage===page&&serverInfo?.id===page.server;
}

function jobConnectionActions(job,log){
  log.parentElement.querySelector('.job-connection-actions')?.remove();
  const recovery=job.connection_recovery;
  if(!recovery||!serverInfo?.connected||recovery.server_id!==serverInfo.id)return;
  const actions=document.createElement('div');
  actions.className='row-actions job-connection-actions';
  for(const {node,vmid,reviewed_identity} of recovery.targets){
    const button=document.createElement('button');
    button.type='button';button.className='primary';
    button.textContent='Refresh connection for '+node+':'+vmid;
    button.onclick=()=>{
      if(!serverInfo?.connected||serverInfo.id!==recovery.server_id)return;
      $('#editor').close();
      openMachine({node,vmid},true,{refreshConnection:true,reviewedIdentity:reviewed_identity});
    };
    actions.append(button);
  }
  log.before(actions);
}

async function openMachine(target, push=false, {refreshConnection=false,reviewedIdentity}={}){
  const page={target,server:serverInfo.id,reviewedIdentity};
  if(push)history.pushState({},'', '#machine/'+[page.server,target.node,target.vmid].map(encodeURIComponent).join('/'));
  machinePage=page;view='machine';renderMachine();
  try{
    const data=await api('simple/machine',reviewedIdentity?{...target,reviewed_identity:reviewedIdentity}:target);
    if(!currentMachine(page))return;
    page.data=data;renderMachine();
    if(data.status==='running' && data.identity && data.addresses.length){
      const target={...page.target,identity:data.identity};
      api('simple/machine/remote-access',target).then(remote=>{
        if(currentMachine(page) && remote.kind){
          page.remoteKind=remote.kind;
          const spot=$('#machine-remote');if(spot)spot.replaceChildren(remoteButton(remote.kind,target,page.server,data.name));
        }
      }).catch(()=>{}); // The console is still available when the guest cannot report remote access.
    }
    if(refreshConnection&&$('#machine-connect'))connectMachine(page);
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
  const back='<button class="machine-back" data-view="machines">← Operations</button>';
  const d=page.data;
  if(!d){
    $('#content').innerHTML=back+'<p role="status">'+esc(page.error||'Reading machine details…')+'</p>';
    return;
  }
  const guestName=d.name||'VM '+d.vmid;
  const url=new URL(serverInfo.url);
  url.search=new URLSearchParams({console:'kvm',novnc:'1',node:d.node,vmid:String(d.vmid),vmname:guestName});
  const connection=d.connection;
  const savedIP=connection.host&&(/^\d+(\.\d+){3}$/.test(connection.host)||connection.host.includes(':'));
  const addressChanged=savedIP&&d.addresses.length>0&&!d.addresses.some(a=>a.host===connection.host);
  const connectable=d.status==='running'&&Boolean(d.identity)&&d.addresses.length>0;
  const os=d.os||(d.platform==='windows'?'Windows':d.platform==='linux'?'Linux':'Unknown');
  const guidance=d.status!=='running'?'Start the machine to discover its guest network and test a login.':
    !d.identity?'A stable VM identity is required to verify a login. Check the VM configuration in Proxmox.':
    !d.addresses.length?'Enable the QEMU Guest Agent in Proxmox and start its service inside the guest, then refresh.':
    addressChanged?'The guest agent reports a different address. Test the current address to update the saved connection.':
    d.verified_at?'Last verified '+new Date(d.verified_at*1000).toLocaleString():
    connection.host?'A connection is saved. Test its login to verify remote access.':
    'Test a guest login to run software on this machine.';
  const rows=d.jobs.map(j=>`<li><button data-log="${esc(j.id)}"><span>${esc(j.operation)}</span><span class="pill ${esc(j.status)}">${esc(j.status)}</span></button></li>`).join('');
  const hosts=d.addresses.map(i=>`<li><span>${esc(i.host)} <small>${esc(i.interface)}</small></span><button data-copy-ip="${esc(i.host)}" aria-label="Copy ${esc(i.host)}">Copy IP</button></li>`).join('');
  $('#content').innerHTML=back+title(esc(guestName),esc(d.node)+' · VM '+esc(d.vmid),
    `<div class="row-actions machine-controls">${powerButton(d)} <span id="machine-remote"></span><a class="console-link" target="_blank" rel="noopener noreferrer" href="${esc(url.href)}">Open console ↗</a></div>`)+
    `<section class="panel machine-facts" aria-label="Machine details"><div>Status<strong><span class="pill ${esc(d.status)}">${esc(d.status)}</span></strong></div><div>Operating system<strong>${esc(os)}</strong></div><div>Processors<strong>${esc(d.cores??'—')} vCPU</strong></div><div>Memory<strong>${d.memory?(d.memory/1073741824).toLocaleString()+' GiB':'—'}</strong></div></section>
    <div class="machine-panels"><section class="panel machine-card"><h2>Guest connection</h2><p>${esc(guidance)}</p>${connection.host?`<p>${esc(connection.platform==='windows'?'HTTPS WinRM':'SSH')} · ${esc(connection.host)} · port ${esc(connection.port)}</p>`:''}
    <div class="row-actions">${connectable?`<button id="machine-connect" class="${d.verified_at&&!addressChanged?'':'primary'}">${addressChanged?'Verify new address':d.verified_at?'Retest login':connection.host?'Verify login':'Connect this machine'}</button>`:''}
    ${d.status==='running'?`<button data-simple="apply" data-id="${esc(d.vmid)}" class="${d.verified_at&&!addressChanged?'primary':''}">Run script</button>`:''}
    ${d.platform==='windows'&&d.identity?'<button id="machine-winrm">Check WinRM setup</button>':''}
    ${mgButton('Manual settings','connection',d.vmid)}</div></section>
    <section class="panel machine-card"><h2>Network</h2>${hosts?`<ul class="address-list">${hosts}</ul>`:'<p>No guest addresses reported.</p>'}<button type="button" id="refresh-machine-network">Refresh connection</button></section></div>
    <section class="panel machine-card"><h2>Latest backup for VM ${esc(d.vmid)}</h2><p id="machine-backup">${esc(page.backup||'Checking backup storage…')}</p></section>
    <section class="recent-jobs"><h2>Recent jobs for VM ${esc(d.vmid)}</h2>${rows?`<ul>${rows}</ul>`:'<p>No matching jobs in the last 100 operations.</p>'}</section>`;
  if(page.remoteKind)$('#machine-remote').append(remoteButton(page.remoteKind,{...page.target,identity:d.identity},page.server,d.name));
  if($('#machine-winrm'))$('#machine-winrm').onclick=()=>openWinRMSetup(page);
  if($('#machine-connect'))$('#machine-connect').onclick=()=>connectMachine(page);
  $('#refresh-machine-network').onclick=()=>openMachine(page.target,false,{refreshConnection:true});
}

function connectMachine(page){
  const d=page.data, previous=d.connection;
  const hosts=d.addresses.map(i=>[i.host,i.host+(i.interface?' · '+i.interface:'')]);
  const ipv4=hosts.filter(([host])=>!host.includes(':'));
  const preferred=hosts.some(([host])=>host===previous.host)?previous.host:
    hosts.length===1?hosts[0][0]:ipv4.length===1?ipv4[0][0]:'';
  if(!preferred)hosts.unshift(['','Choose a guest address']);
  const platformField=d.platform?'':mgSelect('platform','Operating system',[['','Choose the guest OS'],['windows','Windows'],['linux','Linux']],previous.platform||'');
  const savedAddress=previous.host?'<p>Saved address <strong>'+esc(previous.host)+'</strong></p>':'';
  mgDialog('Connect '+(d.name||'VM '+d.vmid),savedAddress+'<div class="form-grid">'+platformField+mgSelect('host','Current guest address',hosts,preferred)+
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
        notice('Connection to '+(d.name||'VM '+d.vmid)+' verified and saved at '+values.host+'.'+(page.reviewedIdentity?' You can now run the script again.':''));
        await openMachine(page.target);
      }
    },'Test and save');
  function loginFields(){
    const platform=d.platform||$('#manage-form').elements.platform.value;
    const list=d.logins.filter(c=>c.platform===platform).map(c=>[c.id,c.name]);
    const suggested='suggested_credential_id' in d?d.suggested_credential_id:previous.credential_id;
    $('#machine-login').innerHTML=platform?(mgSelect('credential_id','Guest login',list.concat([['','Enter a new login']]),suggested||'')+
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
  if(b.dataset.machine||b.dataset.refreshConnection){
    const guest=mgGuests().find(r=>machineKey(r)===(b.dataset.machine||b.dataset.refreshConnection));
    if(guest)await openMachine({node:guest.node,vmid:guest.vmid},true,{refreshConnection:!!b.dataset.refreshConnection});
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
