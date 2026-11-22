const mediaFamilies = {windows:'Windows Server', ubuntu:'Ubuntu Server LTS', debian:'Debian 13', rocky:'Rocky Linux 10'};
const mediaKinds = {...mediaFamilies, virtio:'VirtIO guest tools', other:'Other media'};

function mediaResultLabel(status) {
  return {verified:'Verified',failed:'Check failed',missing:'Missing prerequisite'}[status] || 'Candidate';
}

function mediaSize(bytes) {
  if (bytes == null) return 'Size unavailable';
  return bytes >= 2**30 ? (bytes / 2**30).toFixed(2)+' GiB' : (bytes / 2**20).toFixed(1)+' MiB';
}

function renderMediaWorkspace() {
  const content = $('#content'), server = serverInfo?.id;
  if (!serverInfo?.connected) {
    content.innerHTML = '<h1>ISO Library</h1><p>Connect a Proxmox server to browse its ISO storage.</p><button data-view="server">Open Server settings</button>';
    return;
  }
  if (content.querySelector('.media-workspace')?.dataset.server === server) return;
  content.innerHTML = '<div class="media-workspace"><div class="media-heading">'+workspaceHeading('ISO Library','Check installer media before creating a machine.')+
    '<button type="button" class="media-refresh">Refresh library</button></div>'+
    '<div class="media-notice" role="status" aria-live="polite"></div><div class="media-browser"></div></div>';
  const root=content.querySelector('.media-workspace'), browser=root.querySelector('.media-browser');
  root.dataset.server=server;
  const notice=root.querySelector('.media-notice');
  let listing=null, selectedNode='', sequence=0, query='', filter='', filterLocation='', driver='';
  let checking=new Set(), errors=new Map(), batch=false;
  const current=()=>root.isConnected && serverInfo?.id===server;
  const familyOf=f=>f.check?.status==='verified'||f.check?.editions?.length ? f.check.os : f.family;

  function statusFor(family) {
    const files=listing.files.filter(f=>familyOf(f)===family);
    if (files.some(f=>f.check?.status==='verified')) return ['verified','Verified'];
    if (!files.length) return listing.warnings.length ? ['candidate','Listing incomplete'] : ['missing','No candidates'];
    if (family==='windows'&&!listing.driver_isos.length) return listing.warnings.length?['candidate','Listing incomplete']:['missing','Needs VirtIO ISO'];
    if (files.some(f=>!f.check)) return ['candidate',files.length+' candidate'+(files.length===1?'':'s')];
    return files.some(f=>f.check.status==='missing')?['missing','Missing prerequisite']:['failed','Check failed'];
  }

  function readiness() {
    root.querySelector('.media-readiness').innerHTML=Object.entries(mediaFamilies).map(([k,name])=>{
      const [kind,label]=statusFor(k);
      return '<button type="button" data-family="'+k+'" aria-pressed="'+(filter===k)+'"><span>'+name+'</span>'+
        '<span class="media-state state-'+kind+'">'+label+'</span></button>';
    }).join('');
  }

  function details(result) {
    let html='<p>'+esc(result.detail)+'</p>';
    if (result.agent_iso) html+='<p>Guest tools · '+esc(result.agent_iso)+'</p>';
    if (result.release) html+='<p>'+esc(result.release)+'</p>';
    if (result.install_sources) html+='<p>Installation images · '+esc(result.install_sources.join(', '))+'</p>';
    if (['boot','minimal'].includes(result.kind)) html+='<p>This image also needs network access to Rocky package mirrors during installation.</p>';
    if (result.editions?.length) {
      html+='<details><summary>'+result.editions.length+' Windows edition'+(result.editions.length===1?'':'s')+'</summary><ul>'+
        result.editions.map(e=>'<li>'+esc(e.label)+' · '+esc(e.language)+' · '+esc(e.reason || (e.compatible?'Compatible tools verified':'Tools required'))+'</li>').join('')+'</ul></details>';
    }
    return html+'<p>Checked '+esc(new Date(result.checked_at*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}))+'</p>';
  }

  function rows() {
    const visible=listing.files.map((file,i)=>({file,i})).filter(({file})=>
      (!filter||familyOf(file)===filter)&&(!filterLocation||file.location===filterLocation)&&
      (file.name+' '+(file.storage||'')+' '+mediaKinds[familyOf(file)]).toLowerCase().includes(query.trim().toLowerCase()));
    root.querySelector('.media-count').textContent=visible.length+' '+(visible.length===1?'file':'files');
    root.querySelector('.media-files').innerHTML=visible.length ? visible.map(({file:f,i})=>{
      const family=familyOf(f),r=f.check,pending=checking.has(f.id);
      const state=pending?'checking':r?.status || (mediaFamilies[family]?'candidate':'unverified');
      const label=pending?'Checking…':r?mediaResultLabel(r.status):mediaFamilies[family]?'Candidate':'Unchecked';
      let actions='';
      if (listing.node && family!=='virtio') {
        actions+='<button type="button" data-check="'+i+'"'+(pending?' disabled aria-busy="true"':batch?' disabled':'')+'>'+
          (pending?'Checking…':r?'Recheck':family==='other'?'Check as Windows':'Check media')+'</button>';
      }
      if (r?.status==='verified' && !pending) actions+='<button type="button" class="primary" data-use="'+i+'">Use in Create</button>';
      if (listing.node && f.location==='library') {
        actions+='<button type="button" data-upload-file="'+i+'">Copy to Proxmox storage…</button>';
        if (family==='other'&&!r) actions+='<button type="button" data-advanced-file="'+i+'">Advanced wizard</button>';
      }
      return '<li class="media-file"><div class="media-file-main"><div class="media-file-name">'+esc(f.name)+'</div>'+
        '<div class="media-file-meta">'+esc(f.location==='library'?'Proxmate ISO folder':listing.node+' / '+f.storage)+' · '+esc(mediaSize(f.bytes))+' · '+esc(mediaKinds[family])+'</div>'+
        (r?'<div class="media-check-detail">'+details(r)+'</div>':'')+
        (errors.has(f.id)?'<p class="media-check-error">'+esc(errors.get(f.id))+'</p>':'')+'</div><span class="media-state state-'+state+'">'+label+
        '</span><div class="media-file-actions">'+actions+'</div></li>';
    }).join('') : '<li class="media-empty">'+(listing.files.length?'No files match these filters.':'No ISO files were found in Proxmox storage or the Proxmate ISO folder.')+'</li>';
  }

  function paint() {
    const nodes=listing.nodes.filter(n=>n.status==='online');
    browser.innerHTML='<div class="media-section-head"><h2>Installer readiness</h2><label class="media-node">Proxmox server '+
      '<select name="media_node">'+(nodes.map(n=>'<option value="'+esc(n.node)+'">'+esc(n.node)+'</option>').join('')||'<option>No online servers</option>')+'</select></label></div>'+
      '<div class="media-readiness" role="group" aria-label="Filter files by installer family"></div>'+
      '<div class="media-readiness-actions"><button type="button" class="primary" data-check-group>Check installers</button>'+
      '<span>Checks one candidate for each unverified system on this server.</span></div>'+
      '<div class="media-warnings">'+listing.warnings.map(w=>'<p>'+esc(w)+'</p>').join('')+
      (!nodes.length?'<p>No online Proxmox server is available for media checks.</p>':'')+'</div>'+
      (!listing.ssh_configured?'<p class="media-config">A verified SSH connection is required to inspect Proxmox storage. <button type="button" data-view="server">Server settings</button></p>':'')+
      '<div class="media-section-head"><h2>ISO files</h2><span class="media-count" aria-live="polite"></span></div>'+
      (listing.files.some(f=>f.location==='library')?'<p class="subtle">Guided builds copy installers when needed. You can also copy an ISO to Proxmox storage ahead of time.</p>':'')+
      '<div class="media-filters"><label>Search files<input type="search" name="media_search" placeholder="Filename or storage"></label>'+
      '<label>Media type<select name="media_family"><option value="">All types</option>'+
      Object.entries(mediaKinds).map(([k,v])=>'<option value="'+k+'">'+v+'</option>').join('')+'</select></label>'+
      '<label>Location<select name="media_location"><option value="">All locations</option><option value="proxmox">Proxmox storage</option><option value="library">Proxmate ISO folder</option></select></label></div>'+
      (listing.driver_isos.length?'<label class="media-driver">VirtIO ISO for Windows checks<select name="media_driver">'+
        listing.driver_isos.map(s=>'<option value="'+esc(s)+'">'+esc(s)+'</option>').join('')+'</select></label>':'')+
      '<ul class="media-files" aria-label="ISO files"></ul>';
    root.querySelector('[name=media_node]').value=listing.node||'';
    root.querySelector('[name=media_search]').value=query;
    root.querySelector('[name=media_family]').value=filter;
    root.querySelector('[name=media_location]').value=filterLocation;
    if (listing.driver_isos.length) root.querySelector('[name=media_driver]').value=driver;
    readiness();rows();
    updateBatch();
  }

  function candidates() {
    const choices=[];
    for (const os of Object.keys(mediaFamilies)) {
      const files=listing.files.filter(f=>familyOf(f)===os);
      if (!files.some(f=>f.check?.status==='verified') && files.length) {
        const preferred=files.find(f=>f.id===listing.recommended?.[os]);
        choices.push((preferred&&!preferred.check?preferred:null)||files.find(f=>!f.check)||preferred||files[0]);
      }
    }
    return choices;
  }
  function updateBatch() {
    const button=root.querySelector('[data-check-group]');
    if (!button) return;
    button.disabled=batch||!listing.node||checking.size>0||!candidates().length;
    if(batch)button.setAttribute('aria-busy','true');else button.removeAttribute('aria-busy');
    button.textContent=batch?'Checking installers…':'Check installers';
  }

  async function load(node=selectedNode) {
    const token=++sequence;
    checking=new Set();errors=new Map();
    batch=false;
    selectedNode=node;
    notice.textContent='Reading ISO storage…';
    browser.innerHTML='';
    try {
      const result=await api('simple/media'+(node?'?node='+encodeURIComponent(node):''));
      if (!current()||token!==sequence) return;
      listing=result;selectedNode=result.node||'';
      if (!listing.driver_isos.includes(driver)) driver=listing.driver_isos[0]||'';
      paint();
      notice.textContent='';
    } catch(err) {
      if (!current()||token!==sequence) return;
      notice.textContent=err.message;
      browser.innerHTML='<button type="button" data-retry-list>Retry listing</button>'+
        (node?'<button type="button" data-reset-node>Use available server</button>':'');
    }
  }

  async function check(file) {
    if (checking.has(file.id)) return;
    const token=sequence,os=mediaFamilies[familyOf(file)]?familyOf(file):'windows';
    checking.add(file.id);
    errors.delete(file.id);
    notice.textContent='Checking '+file.name+'…';rows();updateBatch();
    try {
      const result=await api('simple/media/verify',{node:listing.node,source:file.id,os,...(os==='windows'&&driver?{agent_iso:driver}:{})});
      if (!current()||sequence!==token) return;
      file.check=result;
      notice.textContent=file.name+' · '+mediaResultLabel(result.status);
    } catch(err) {
      if (current()&&sequence===token){errors.set(file.id,err.message);notice.textContent=err.message;}
    } finally {
      if (current()&&sequence===token) {
        checking.delete(file.id);readiness();rows();updateBatch();
        if (!batch && document.activeElement===document.body) root.querySelector('[data-check="'+listing.files.indexOf(file)+'"]')?.focus();
      }
    }
  }

  async function checkGroup() {
    const token=sequence, files=candidates();
    batch=true;updateBatch();
    for (const file of files) {
      if (!current()||token!==sequence) return;
      await check(file);
    }
    if (current()&&token===sequence) {
      batch=false;updateBatch();rows();
      notice.textContent='Finished checking '+files.length+' installer candidate'+(files.length===1?'':'s')+'.';
    }
  }

  root.oninput=e=>{if(e.target.name==='media_search'){query=e.target.value;rows();}};
  root.onchange=e=>{
    if (e.target.name==='media_node') load(e.target.value);
    if (e.target.name==='media_family'){filter=e.target.value;readiness();rows();}
    if (e.target.name==='media_location'){filterLocation=e.target.value;rows();}
    if (e.target.name==='media_driver'){driver=e.target.value;notice.textContent='Recheck a Windows installer to verify it with the selected tools ISO.';}
  };
  root.onclick=e=>{
    const button=e.target.closest('button');
    if (!button) return;
    const d=button.dataset;
    if (button.matches('.media-refresh,[data-retry-list]'))return withBusyButton(button,()=>load());
    if (button.hasAttribute('data-reset-node')){load('');return;}
    if (button.hasAttribute('data-check-group')){checkGroup();return;}
    if (d.family){
      filter=filter===d.family?'':d.family;root.querySelector('[name=media_family]').value=filter;
      readiness();rows();root.querySelector('[data-family="'+d.family+'"]').focus();return;
    }
    if (d.check!=null) check(listing.files[Number(d.check)]);
    if (d.use!=null) {
      const f=listing.files[Number(d.use)];
      return withBusyButton(button,()=>openSimpleMedia(f.check.os,listing.node,f.id,f.check.agent_iso));
    }
    if (d.uploadFile!=null) return withBusyButton(button,()=>openMediaUpload(listing.files[Number(d.uploadFile)].name,listing.node));
    if (d.advancedFile!=null) return withBusyButton(button,()=>openMediaAdvanced(listing.files[Number(d.advancedFile)].name,listing.node));
  };
  load();
}
