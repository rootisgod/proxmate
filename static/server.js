let serverInfo=null;

function serverRender(){
  if(view!=='server' && state)return false;
  if(view!=='server' && ['tasks','scripts','credentials','jobs'].includes(view))return false;
  if(serverInfo?.connected && ['schedules','recovery'].includes(view))return false;
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view==='server'));
  const s=serverInfo||{};
  const status=s.connected?(state?'Connected':'Connection unavailable'):'Disconnected';
  if($('#server-form')){
    // Refresh the saved summary while preserving the editable draft.
    const current=$('.server-status');
    if(current){
      current.textContent=status;current.classList.toggle('is-connected',Boolean(s.connected&&state));
      $('#saved-api-user').textContent=s.username;
      $('#saved-api-tls').textContent=s.verify_tls!==false?'Enabled':'Disabled';
      $('#saved-ssh-pin').textContent=s.ssh_fingerprint?'Configured':'Needed for builds';
    }
    return true;
  }
  const summary=s.configured?`<section class="panel server-summary" aria-label="Saved server connection">
    <div class="server-endpoint"><span class="settings-icon bi bi-hdd-network" aria-hidden="true"></span><div><span class="settings-label">Saved endpoint</span><strong>${esc(s.url)}</strong><span class="server-status ${s.connected&&state?'is-connected':''}">${esc(status)}</span></div></div>
    <dl><div><dt>API account</dt><dd id="saved-api-user">${esc(s.username)}</dd></div><div><dt>Certificate verification</dt><dd id="saved-api-tls">${s.verify_tls!==false?'Enabled':'Disabled'}</dd></div>
    <div><dt>SSH fingerprint</dt><dd id="saved-ssh-pin">${s.ssh_fingerprint?'Configured':'Needed for builds'}</dd></div></dl></section>`:'';
  $('#content').innerHTML='<div class="settings-workspace server-workspace">'+title(s.configured?'Proxmox server':'Connect to Proxmox',s.configured?'Manage the server that provides your inventory and runs machine operations.':'Connect a Proxmox host to discover its machines, storage and networks.')+summary+
    `<section class="panel server-panel"><form id="server-form">
    <h2>${s.configured?'Connection settings':'Server connection'}</h2><div class="form-grid">
    <label class="wide">Server URL<input name="url" type="url" placeholder="https://192.168.1.10:8006" value="${esc(s.url||'')}" required autocomplete="url"></label>
    <label>Proxmox username<input name="username" value="${esc(s.username||'root@pam')}" required autocomplete="username"></label>
    <label>Password<input name="password" type="password" autocomplete="new-password" ${s.has_password?'':'required'}><small>${s.has_password?'Leave blank to keep the saved password for this URL and account.':'Stored encrypted in the Proxmate data volume.'}</small></label>
    </div><details class="server-advanced"><summary>Certificate and SSH settings</summary><div class="form-grid">
    <label class="check wide"><input name="verify_tls" type="checkbox" ${s.verify_tls!==false?'checked':''}>Verify the HTTPS certificate</label>
    <p class="wide subtle">Keep verification enabled with a trusted certificate. Only disable it for a server you trust on your LAN.</p>
    <label>SSH username<input name="ssh_user" value="${esc(s.ssh_user||'root')}" required></label>
    <label>SSH host fingerprint<input name="ssh_fingerprint" value="${esc(s.ssh_fingerprint||'')}" placeholder="SHA256:..."></label>
    <p class="wide subtle">For machine builds, enter the SHA256 fingerprint shown in the Proxmox console. SSH uses the server password.</p></div></details>
    ${s.configured?`<details class="server-replacement"><summary>Replace this server</summary>
      <p>Changing the URL requires replacement. Select this option if a different host reuses the saved URL.</p>
      <label class="check"><input name="replace_confirmed" type="checkbox">Archive this server&rsquo;s VM connections, groups, recipes, schedules, template versions and recovery settings when saving.</label>
      <p>The task library, guest credentials and job history remain available. Archives stay in the data volume and currently have no restore interface.</p></details>`:''}
    <p id="server-result" role="status" aria-live="polite"></p>
    <div class="row-actions server-actions"><button class="primary" type="submit">Save &amp; connect</button><button type="button" id="server-test">Test connection</button>${s.connected?'<button type="button" id="server-disconnect">Disconnect</button>':''}</div>
    ${s.configured?'<p class="settings-help">Finish active jobs and disable schedules and backup policies before changing or disconnecting the server.</p>':''}</form></section></div>`;
  const form=$('#server-form'),feedback=$('#server-result');
  let draftRevision=0;
  const draft=()=>{
    const d=Object.fromEntries(new FormData(form));
    d.verify_tls=form.elements.verify_tls.checked;d.replace_confirmed=!!form.elements.replace_confirmed?.checked;d.expected_id=s.id||null;
    return d;
  };
  async function run(path,body,reset){
    const submittedRevision=draftRevision;
    const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    feedback.dataset.tone='pending';
    feedback.textContent=path==='server/disconnect'?'Disconnecting server…':'Checking server connection…';
    try{
      const result=await api(path,body);
      if(reset){
        form.elements.password.value='';$('#notice').hidden=true;resetMachineFilters();serverInfo=result;state=null;mg=null;lcData=null;lcLoaded=0;mgPlan=null;
        $('#content').innerHTML='';view=result.connected?'machines':'server';await refresh();if(!result.connected)notice('Server disconnected.');
      }else if(submittedRevision===draftRevision){
        feedback.dataset.tone='success';
        feedback.textContent=`Connection successful. Proxmox ${result.version} on ${result.nodes.join(', ')}. Draft settings have not been saved.`;
      }
    }catch(e){if(reset||submittedRevision===draftRevision){feedback.dataset.tone='error';feedback.textContent=e.message}}
    finally{buttons.forEach(b=>b.disabled=false)}
  }
  form.oninput=()=>{draftRevision++;feedback.textContent=''};
  form.onsubmit=e=>{e.preventDefault();run('server/connect',draft(),true)};
  $('#server-test').onclick=()=>{if(form.reportValidity())run('server/test',draft(),false)};
  if($('#server-disconnect'))$('#server-disconnect').onclick=()=>run('server/disconnect',{expected_id:s.id},true);
  return true;
}
