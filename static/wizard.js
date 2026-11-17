/* Local wizard state is memory-only. No passwords or drafts in browser storage. */
let wizard = null;
const wf = (name, label, value = '', type = 'text', extra = '') => `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
const ws = (name, label, items, selected) => `<label>${label}<select name="${name}">${items.map(([value,text])=>`<option value="${esc(value)}" ${String(value)===String(selected)?'selected':''}>${esc(text)}</option>`).join('')}</select></label>`;
const wc = (name, label, checked=false) => `<label class="check wide"><input type="checkbox" name="${name}" ${checked?'checked':''}>${label}</label>`;

function wizardRequestKey() {
  // getRandomValues works on LAN HTTP; randomUUID requires a secure context.
  // This is an opaque idempotency key, so it does not need UUID formatting.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), b=>b.toString(16).padStart(2,'0')).join('');
}

async function openWizard(kind='linux-cloud', filename='', purpose='vm') {
  if (!state) return notice('Connect to Proxmox before opening the wizard.');
  try {
    const catalog = await api('catalog');
    if(typeof loadManagement==='function')await loadManagement();
    const node = state.nodes.find(n=>n.status==='online')?.node;
    if (!node) throw Error('No online Proxmox node is available.');
    wizard = {step:0, catalog, draft:{kind,purpose,source_kind:filename?'local':'proxmox',filename,node,
      vmid:state.next_id,name:kind==='windows'?'windows-server':'my-linux-vm',profile:'ubuntu-24.04',
      cores:2,memory:kind==='windows'?4096:2048,disk:kind==='windows'?64:32,username:'ubuntu',
      network_mode:'dhcp',ui_language:'en-US',locale:'en-GB',keyboard:'en-GB',timezone:'GMT Standard Time',
      hostname:'PROXMATE',image_index:'',admin_password:'',rdp:false,start:false}, key:wizardRequestKey()};
    showWizardStep(); $('#wizard').showModal();
  } catch(e) { notice(e.message); }
}

function collectWizard() {
  const values = Object.fromEntries(new FormData($('#wizard-form')));
  $('#wizard-fields').querySelectorAll('input[type=checkbox]').forEach(i=>values[i.name]=i.checked);
  Object.assign(wizard.draft, values);
}

function showWizardStep() {
  const d=wizard.draft, cat=wizard.catalog, step=wizard.step, detail=state.details[d.node];
  $('#wizard-error').textContent='';
  $('#wizard-steps').innerHTML=['Source','Guest settings','Hardware & network','Review'].map((name,i)=>`<li ${i===step?'aria-current="step"':''}><span>${i+1}</span>${name}</li>`).join('');
  $('#wizard-back').hidden=step===0; $('#wizard-next').textContent=step===3?'Create from this plan':'Continue →';
  $('#wizard-download').hidden=!(step===3&&d.kind==='windows');
  let html='';
  if(step===0) {
    html='<h3>What would you like to build?</h3><div class="form-grid">'+
      ws('kind','Installation path',[['linux-cloud','Linux cloud image — automated'],['windows','Windows ISO + answer file'],['linux-iso','Linux ISO — interactive installation']],d.kind)+
      ws('purpose','What do you want at the end?',[['vm','A virtual machine'],['template','A reusable template']],d.purpose)+
      ws('node','Proxmox node',state.nodes.filter(n=>n.status==='online').map(n=>[n.node,n.node]),d.node);
    if(d.kind==='linux-cloud') html+=ws('profile','Distribution and version',Object.entries(cat.profiles).map(([key,p])=>[key,p.label]),d.profile)+
      '<p class="wide">Official x86-64 cloud image; download and checksum verification happen only when you create it. Cloud images support SSH and cloud-init without an installer ISO.</p>';
    else {
      html+=ws('source_kind','Where is your ISO?',[['local','Local ISO library'],['proxmox','Already on Proxmox']],d.source_kind);
      if(d.source_kind==='local') html+=ws('filename','Your ISO',state.local_isos.map(i=>[i.name,i.name]),d.filename);
      else html+=ws('iso','Proxmox ISO',detail.isos.map(i=>[i.volid,i.volid]),d.iso);
      if(d.source_kind==='local'||d.kind==='windows') html+=ws('iso_storage','ISO storage (including generated answer media)',detail.storage.filter(s=>s.active&&s.content.split(',').includes('iso')).map(s=>[s.storage,s.storage]),d.iso_storage);
      html+='<p class="wide">To provide another ISO, place it in the mounted ISO library and refresh the dashboard. Your original file is never modified.</p>';
      if(d.purpose==='template') html+='<p class="wide">This prepares an installer VM. After installation you will generalize, shut down, and convert it in the Templates flow.</p>';
    }
    html+='</div>';
  }
  if(step===1) {
    html='<h3>Make it yours</h3><div class="form-grid">'+wf('name','Proxmox VM name',d.name,'text','required pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,62}"');
    if(d.kind==='windows') {
      const locales=Object.entries(cat.locales);
      html+=wf('hostname','Windows computer name',d.hostname,'text','required maxlength="15"')+
        wf('image_index','Edition image index in this ISO',d.image_index,'number','required min="1" max="100"')+
        '<p class="wide">Confirm the edition index using <code>DISM /Get-WimInfo /WimFile:X:\\sources\\install.wim</code> on the mounted ISO (use install.esd if present). We do not guess editions from the filename.</p>'+
        ws('ui_language','Display language (must exist in ISO)',locales,d.ui_language)+ws('locale','Regional format',locales,d.locale)+ws('keyboard','Keyboard layout',locales,d.keyboard)+ws('timezone','Timezone',cat.timezones.map(t=>[t,t]),d.timezone)+
        wf('admin_password','Administrator password (optional)',d.admin_password,'password','autocomplete="new-password" minlength="12" maxlength="128"')+
        '<p class="wide">Leave the password empty to complete account setup in the console. Supplying one puts it in the answer media; no automatic login is enabled. Use upper/lowercase, a number and a symbol.</p>'+
        wc('rdp','Enable Remote Desktop with NLA (domain/private networks only)',d.rdp)+
        wc('accept_eula','I am authorised to install this Windows image and accept its licence terms.',d.accept_eula)+
        wc('wipe_disk','Format disk 0 of the NEW VM using a GPT layout. Never attach this answer file to an existing machine.',d.wipe_disk);
    } else if(d.kind==='linux-cloud') {
      html+=wf('username','Login user',d.username,'text','required pattern="[a-z_][a-z0-9_-]{0,31}"')+
        `<label class="wide">SSH public key<textarea name="ssh_key" required placeholder="ssh-ed25519 AAAA…">${esc(d.ssh_key||'')}</textarea></label><p class="wide">Only a public key. Never paste a private key. Cloud-image login requires this key.</p>`;
    } else html+='<p class="wide">Language, user accounts and packages are selected in the distribution’s installer. No unattended answer file is generated for arbitrary Linux ISOs.</p>';
    html+='</div>';
  }
  if(step===2) {
    html='<h3>Give it a home</h3><div class="form-grid">'+wf('vmid','Unused VM ID',d.vmid,'number','required min="100" max="999999999"')+
      ws('storage','VM disks',detail.storage.filter(s=>s.active&&s.content.split(',').includes('images')).map(s=>[s.storage,s.storage]),d.storage)+
      wf('cores','CPU cores',d.cores,'number','required min="1" max="128"')+wf('memory','Memory (MB)',d.memory,'number',`required min="${d.kind==='windows'?4096:512}" max="1048576"`)+
      wf('disk','Disk size (GB)',d.disk,'number',`required min="${d.kind==='windows'?64:8}" max="4096"`)+
      ws('bridge','Network bridge',detail.network.filter(n=>n.type==='bridge').map(n=>[n.iface,n.iface]),d.bridge);
    if(d.kind==='linux-cloud') {
      html+=ws('network_mode','IPv4 configuration',[['dhcp','DHCP'],['static','Static IPv4']],d.network_mode);
      if(d.network_mode==='static') html+=wf('address','IP address / prefix (e.g. 192.168.1.50/24)',d.address,'text','required')+wf('gateway','Gateway',d.gateway,'text','required');
      html+=wf('dns','DNS IP addresses (space separated, optional)',d.dns);
    } else html+=d.kind==='windows'?'<p class="wide">The Windows answer file uses DHCP. Static addressing can be configured after installation. RDP is not opened on public network profiles.</p>':'<p class="wide">Configure the guest IP address and DNS in the Linux installer.</p>';
    if(!(d.kind==='linux-cloud'&&d.purpose==='template'))html+=wc('start','Start the new VM after preparation (otherwise leave it stopped)',d.start);
    html+='</div>';
    if(typeof bootstrapFields==='function')html+=bootstrapFields(d,detail);
  }
  if(step===3) {
    const plan=wizard.plan;
    html='';
    html=`<h3>Review before creating</h3><dl class="review-grid"><dt>Destination</dt><dd>${esc(d.node)} · VM ${esc(d.vmid)} · ${esc(d.name)}</dd><dt>Resources</dt><dd>${esc(d.cores)} cores · ${esc(d.memory)} MB · ${esc(d.disk)} GB</dd><dt>Source</dt><dd>${esc(d.kind==='linux-cloud'?cat.profiles[d.profile].label:d.source_kind==='local'?d.filename:d.iso)}</dd><dt>Result</dt><dd>${esc(d.kind==='linux-cloud'?d.purpose:'Installer VM; OS installation must be checked in console')}</dd></dl>`+
      '<ol class="plan-list">'+plan.steps.map(s=>`<li>${esc(s)}</li>`).join('')+'</ol><ul>'+plan.notes.map(s=>`<li>${esc(s)}</li>`).join('')+'</ul>';
    html+='<p><button type="button" data-mg="save-wizard-recipe">Save as recipe</button></p>';
    if(plan.bootstrap_preview)html+=`<details><summary>Review first-boot bootstrap configuration</summary><pre>${esc(plan.bootstrap_preview)}</pre></details>`;
    if(plan.answer_preview)html+=`<details><summary>Preview Autounattend.xml (password redacted)</summary><pre>${esc(plan.answer_preview)}</pre></details>`;
    html+=wc('confirmed','Create these resources and upload the listed media on my Proxmox host. No existing VM will be replaced.',false);
  }
  $('#wizard-fields').innerHTML=html;
  $('#wizard-fields').querySelectorAll('select').forEach(el=>{if(['kind','purpose','source_kind','node','network_mode','profile'].includes(el.name))el.onchange=()=>{
    collectWizard();
    if(el.name==='kind'){d.memory=d.kind==='windows'?4096:2048;d.disk=d.kind==='windows'?64:32;d.start=false;delete d.iso;}
    if(el.name==='node'){delete d.iso;delete d.iso_storage;delete d.storage;delete d.bridge;}
    if(el.name==='profile')d.username=cat.profiles[d.profile].user;
    if(d.kind==='linux-cloud'&&d.purpose==='template')d.start=false;
    showWizardStep();
  }});
  $('#wizard-fields').querySelectorAll('[name=bootstrap_enabled],[name=bootstrap_verify]').forEach(el=>el.onchange=()=>{collectWizard();if(d.bootstrap_verify&&d.purpose==='vm')d.start=true;showWizardStep()});
  $('#wizard-fields').scrollTop=0;
}

function wizardPayload() {
  const d=wizard.draft;
  const result={action:'provision',kind:d.kind,purpose:d.purpose,node:d.node,vmid:d.vmid,name:d.name,storage:d.storage,bridge:d.bridge,cores:d.cores,memory:d.memory,disk:d.disk,start:!!d.start};
  if(d.kind==='linux-cloud')Object.assign(result,{profile:d.profile,username:d.username,ssh_key:d.ssh_key,network_mode:d.network_mode,address:d.address,gateway:d.gateway,dns:d.dns});
  else {
    Object.assign(result,{source_kind:d.source_kind,iso_storage:d.iso_storage});
    if(d.source_kind==='local')result.filename=d.filename;else result.iso=d.iso;
    if(d.kind==='windows')result.windows=Object.fromEntries(['ui_language','locale','keyboard','timezone','hostname','image_index','admin_password','rdp','accept_eula','wipe_disk'].map(k=>[k,d[k]??'']));
  }
  if(typeof bootstrapPayload==='function'){const b=bootstrapPayload(d);if(b)result.bootstrap=b;}
  return result;
}

document.addEventListener('click',e=>{const b=e.target.closest('[data-wizard]');if(b)openWizard(b.dataset.wizard||'linux-cloud',b.dataset.filename||'',b.dataset.purpose||'vm');});
$('#wizard-close').onclick=()=>$('#wizard').close();
$('#wizard').addEventListener('close',()=>{wizard=null;$('#wizard-fields').replaceChildren();});
$('#wizard-back').onclick=()=>{collectWizard();wizard.step--;showWizardStep();};
$('#wizard-form').onsubmit=async e=>{
  e.preventDefault();const activeWizard=wizard;const button=$('#wizard-next');button.disabled=true;$('#wizard-back').disabled=true;$('#wizard-close').disabled=true;
  try {
    collectWizard();
    if(wizard.step===0&&wizard.draft.kind!=='linux-cloud'&&!wizard.draft[wizard.draft.source_kind==='local'?'filename':'iso'])throw Error('No ISO is available in this source. Choose another source or add an ISO to the library.');
    if(wizard.step===1&&wizard.draft.kind==='windows'&&(!wizard.draft.accept_eula||!wizard.draft.wipe_disk))throw Error('Confirm the Windows licence and new-disk formatting options to continue.');
    if(wizard.step===2){const plan=await api('provision/plan',wizardPayload());if(wizard!==activeWizard)return;wizard.plan=plan;wizard.step++;showWizardStep();}
    else if(wizard.step===3){
      if(!wizard.draft.confirmed)throw Error('Confirm the reviewed plan to create resources.');
      const response=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,'Idempotency-Key':wizard.key},body:JSON.stringify({...wizardPayload(),confirmed:true})});
      const data=await response.json();if(!response.ok)throw Error(data.error||'Submission failed');
      $('#wizard').close();view='jobs';notice('Provisioning queued. Installer preparation does not mean the OS installation is complete.');await refresh();
    }else{wizard.step++;showWizardStep();}
  }catch(error){$('#wizard-error').textContent=error.message;}finally{button.disabled=false;$('#wizard-back').disabled=false;$('#wizard-close').disabled=false;}
};
$('#wizard-download').onclick=async()=>{
  try{
    const response=await fetch('/api/windows/answer-file',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({...wizardPayload().windows,bootstrap:wizardPayload().bootstrap})});
    if(!response.ok)throw Error((await response.json()).error);
    const url=URL.createObjectURL(await response.blob()), link=document.createElement('a');link.href=url;link.download='Autounattend.xml';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(error){$('#wizard-error').textContent=error.message;}
};
