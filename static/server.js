let serverInfo=null;

function serverRender(){
  if(view!=='server' && state)return false;
  if(view!=='server' && ['tasks','credentials','jobs'].includes(view))return false;
  if(serverInfo?.connected && ['schedules','recovery'].includes(view))return false;
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view==='server'));
  if($('#server-form'))return true; // Preserve an unfinished edit during background polling.
  const s=serverInfo||{};
  $('#content').innerHTML=title('Proxmox server','Choose the server for this Proxmate installation. One saved server at a time.')+
    `<div class="panel server-panel"><p><strong>${s.connected?'Connected to '+esc(s.url):s.configured?'Disconnected':'No server configured'}</strong></p>
    <p>Test checks login and node access. Save &amp; connect tests again before saving. Disconnect leaves your VMs running.</p>
    <form id="server-form"><div class="form-grid">
    <label class="wide">Server URL<input name="url" type="url" placeholder="https://192.168.1.10:8006" value="${esc(s.url||'')}" required autocomplete="url"></label>
    <label>Proxmox username<input name="username" value="${esc(s.username||'root@pam')}" required autocomplete="username"></label>
    <label>Password<input name="password" type="password" autocomplete="new-password" ${s.has_password?'':'required'}><small>${s.has_password?'Saved securely. Leave blank to keep it for this address and user.':'Stored encrypted in the Proxmate data volume.'}</small></label>
    <label class="check wide"><input name="verify_tls" type="checkbox" ${s.verify_tls!==false?'checked':''}>Verify the HTTPS certificate (recommended)</label>
    <p class="wide subtle">For a self-signed certificate, install a trusted certificate or explicitly turn verification off for a server you trust on your LAN.</p>
    <label>SSH username<input name="ssh_user" value="${esc(s.ssh_user||'root')}" required></label>
    <label>SSH host fingerprint (optional)<input name="ssh_fingerprint" value="${esc(s.ssh_fingerprint||'')}" placeholder="SHA256:..."></label>
    <p class="wide subtle">Linux image builds and hypervisor scripts need SSH. Verify the fingerprint independently from the server console before entering it here. SSH uses the saved password; Test checks the HTTPS API only.</p>
    ${s.configured?'<label class="check wide"><input name="replace_confirmed" type="checkbox">Replace the server and archive its VM connections, groups, recipes, schedules, template versions and recovery settings. Keep the task library, guest credentials and job history.</label><p class="wide subtle">Required for a different URL. Also select this if a new server reuses the same address. Archives remain in the data volume; there is no archive restore interface yet.</p>':''}
    </div><p id="server-result" role="status" aria-live="polite"></p><div class="row-actions"><button type="button" id="server-test">Test connection</button><button class="primary" type="submit">Save &amp; connect</button>${s.connected?'<button type="button" id="server-disconnect">Disconnect</button>':''}</div>
    <p class="subtle">Finish queued/running jobs and disable schedules and backup policies before disconnecting or changing connection details.</p></form></div>`;
  const form=$('#server-form');
  const draft=()=>{const d=Object.fromEntries(new FormData(form));d.verify_tls=form.elements.verify_tls.checked;d.replace_confirmed=!!form.elements.replace_confirmed?.checked;d.expected_id=s.id||null;return d};
  async function run(path,body,reset){
    const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    $('#server-result').textContent='Checking server connection...';
    try{
      const result=await api(path,body);
      if(reset)form.elements.password.value='';
      if(reset){serverInfo=result;state=null;mg=null;lcData=null;lcLoaded=0;mgPlan=null;$('#content').innerHTML='';view='server';await refresh();notice(path.endsWith('disconnect')?'Disconnected. Your VMs have not been changed.':'Server connection saved. Choose Overview to view this server.');}
      else $('#server-result').textContent=`Connection successful. Proxmox ${result.version}; nodes: ${result.nodes.join(', ')}. Not saved yet.`;
    }catch(e){$('#server-result').textContent=e.message}
    finally{buttons.forEach(b=>b.disabled=false)}
  }
  form.onsubmit=e=>{e.preventDefault();run('server/connect',draft(),true)};
  $('#server-test').onclick=()=>{if(form.reportValidity())run('server/test',draft(),false)};
  if($('#server-disconnect'))$('#server-disconnect').onclick=()=>run('server/disconnect',{expected_id:s.id},true);
  return true;
}
