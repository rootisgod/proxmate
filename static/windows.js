const editionCache=new Map();

function freshWindowsSettings(d){
  const edition=d.source_kind==='template'?null:(d.editions||[]).find(e=>String(e.index)===String(d.edition));
  const settings=simpleOptions.windows_settings||[],programs=simpleOptions.common_programs||[];
  const check=(name,item,checked,reason='')=>'<label class="check"><input type="checkbox" name="'+name+'" value="'+esc(item.id)+'" '+
    (checked&&!reason?'checked ':'')+(reason?'disabled ':'')+'><span>'+esc(item.name)+
    (reason||item.detail?'<small>'+esc(reason||item.detail)+'</small>':'')+'</span></label>';
  const options=settings.filter(o=>!o.vm_only||d.purpose==='vm');
  const option=o=>check('windows_setting',o,(d.windows_settings||[]).includes(o.id),
    o.desktop&&edition?.desktop===false?'Requires Desktop Experience':'');
  const apps=programs.map(p=>check('common_program',p,(d.common_programs||[]).includes(p.id),
    p.id==='microsoft-windows-terminal'&&edition&&(edition.desktop===false||(edition.build && Number(edition.build)<19041))?'Requires Desktop Experience and build 19041 or later':''));
  const security=options.filter(o=>o.advanced);
  return {
    settings: bulkChoices('Windows settings',
      wc('rdp','Enable Remote Desktop',d.rdp)+options.filter(o=>!o.advanced).map(option).join(''))+
      '<details class="software-sensitive bulk-choices"'+(d.manual_updates||security.some(o=>d.windows_settings?.includes(o.id))?' open':'')+
      '><summary>Updates, security and console sign-in</summary>'+bulkChoiceButtons('updates, security and console sign-in options')+'<div class="choice-grid">'+
      wc('manual_updates','Use manual Windows updates',d.manual_updates)+security.map(option).join('')+'</div></details>',
    apps: apps.length?bulkChoices('Common programs',apps.join('')):''
  };
}

function openWinRMSetup(page){
  const d=page.data;
  async function check(values){
    const review=await api('simple/machine/winrm/review',{...page.target,identity:d.identity,scope:values.scope});
    if(!currentMachine(page)||!$('#manage-dialog').open)return;
    const descriptions={'stopped-vm':'The VM is stopped. Start it to check WinRM.','agent-unavailable':'Guest-agent inspection is unavailable. Run the commands in an elevated PowerShell console inside this Windows VM.','stopped':'The Windows Remote Management service is stopped.','no-listener':'WinRM is running without an HTTPS listener.','https-listener':'An HTTPS listener is configured. Test the guest login to verify management access.'};
    const status='<p>'+esc(descriptions[review.status])+'</p>';
    if(!review.scope&&!['https-listener','stopped-vm'].includes(review.status)){
      mgDialog('Windows remote management',status+wf('scope','Management client IP or subnet','','text','required')+
        '<p>The VM bridge has no management subnet configured. Enter the address or subnet that should reach TCP 5986.</p>',check,'Review firewall scope');
      return;
    }
    mgDialog('Windows remote management',status+
      (!review.commands||review.status==='https-listener'?'':'<p>The setup enables the service, creates an HTTPS listener if needed and allows inbound TCP 5986 from <strong>'+esc(review.scope)+'</strong>.</p><details '+(!review.plan_id?'open':'')+'><summary>PowerShell for the guest console</summary><textarea readonly aria-label="Commands to run inside Windows">'+esc(review.commands)+'</textarea><p>For manual setup, save the public certificate printed by the commands in the machine’s manual connection settings.</p></details>'),
      async()=>{
        if(!currentMachine(page))throw Error('Machine selection changed.');
        if(review.plan_id){
          const job=await api('simple/machine/winrm/run',{plan_id:review.plan_id,confirmed:true});
          $('#manage-dialog').close();openJobLog(job.id);
        }else{
          $('#manage-dialog').close();
          if(review.status==='https-listener'&&d.addresses.length)connectMachine(page);
        }
      },review.plan_id?'Enable HTTPS WinRM':review.status==='https-listener'&&d.addresses.length?'Test guest login':'Close');
  }
  mgDialog('Check HTTPS WinRM on '+(d.name||d.vmid),wf('scope','Management client IP or subnet (optional)','','text','placeholder="Use the VM bridge subnet"')+
    '<p>Check the Windows service and HTTPS listener. Review the firewall scope before enabling remote access.</p>',check,'Check setup');
}

function jobWindowsActions(job,log){
  let bar=log.parentElement.querySelector('.job-downloads');
  if(bar)bar.remove();
  if(!job.has_answer&&!job.has_autoinstall&&!job.has_preseed&&!job.has_kickstart&&!job.server_id)return;
  bar=document.createElement('div');bar.className='row-actions job-downloads';
  if(job.server_id&&job.server_id===serverInfo?.id&&state?.resources.some(r=>r.type==='qemu'&&!r.template&&r.node===job.target.node&&r.vmid===job.target.vmid&&r.name===job.target.name)){
    const link=document.createElement('a');
    link.href='/api/jobs/'+encodeURIComponent(job.id)+'/console';link.target='_blank';link.rel='noopener noreferrer';link.textContent='Open console';bar.append(link);
  }
  if(job.has_answer){
    const path='/api/jobs/'+encodeURIComponent(job.id)+'/answer';
    const safe=document.createElement('a');safe.href=path;safe.download='';safe.textContent='Download reusable answer file';bar.append(safe);
  }
  if(job.has_answer&&!job.reusable_answer_only){
    const path='/api/jobs/'+encodeURIComponent(job.id)+'/answer';
    const original=document.createElement('button');original.type='button';original.textContent='Download original with credentials';
    original.onclick=async()=>{
      if(!window.confirm('Download the original answer file including the Windows setup password?'))return;
      try{
        const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({include_secrets:true})});
        if(!response.ok)throw Error('Original answer file could not be downloaded.');
        const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download='windows-'+job.id+'-original.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }catch(err){notice(err.message)}
    };
    bar.append(original);
  }
  if(job.has_autoinstall||job.has_preseed||job.has_kickstart){
    const link=document.createElement('a');
    link.href='/api/jobs/'+encodeURIComponent(job.id)+(job.has_kickstart?'/kickstart':job.has_preseed?'/preseed':'/autoinstall');
    link.download='';link.textContent=job.has_kickstart?'Download Rocky Kickstart':job.has_preseed?'Download Debian preseed':'Download Ubuntu autoinstall YAML';bar.append(link);
  }
  log.before(bar);
}

function windowsTemplates(){
  return (state?.resources||[]).filter(t=>t.type==='qemu'&&t.template&&(!t.ostype||/^(win|w2k)/i.test(t.ostype)));
}

function windowsSourceFields(d){
  const templates=windowsTemplates().filter(t=>simpleOptions.destinations.some(n=>n.node===t.node));
  let html=d.purpose==='vm'&&templates.length?ws('source_kind','Install from',[['iso','Windows ISO'],['template','Windows template']],d.source_kind):'';
  if(d.source_kind==='template'){
    const selected=templates.find(t=>t.vmid===Number(d.template_id))||templates[0];
    if(!selected)return html+'<p>No templates are available.</p>';
    d.template_id=selected.vmid;
    html+=ws('template_id','Source template',templates.map(t=>[t.vmid,(t.name||'Template')+' · '+t.node+' / '+t.vmid]),d.template_id)+
      ws('clone_mode','Copy type',[['full','Full copy (independent VM)'],['linked','Linked copy (requires source template)']],d.clone_mode||'full')+
      wc('generalized','This is a Sysprep-generalized Windows Server template with the QEMU guest agent installed.',d.generalized);
  }else{
    const all=simpleSources(), candidates=all.filter(s=>s.windows_label);
    const sources=d.all_media||!candidates.length?all:candidates;
    if(!sources.some(s=>s.id===d.source))d.source=sources[0]?.id||'';
    const selected=sources.find(s=>s.id===d.source);
    const location=s=>s.id.startsWith('library:')?s.label:s.id;
    const labels=sources.map(s=>{
      const category=s.windows_label;
      const repeated=category&&sources.filter(other=>other.windows_label===category).length>1;
      return [s.id,d.all_media||!category?location(s):category+(repeated?' · '+location(s):'')];
    });
    html+=ws('source','Windows Server ISO',labels,d.source)+ws('edition','Windows edition',(d.editions||[]).map(e=>[e.index,e.label]),d.edition)+
      '<div class="media-tools"><details id="iso-details"><summary>ISO details</summary><small>'+esc(selected?location(selected):'No ISO found')+'</small></details>'+wc('all_media','Browse all media',d.all_media)+'</div>';
  }
  return html;
}

function dataDiskFields(d){
  const rows=(d.data_disks||[]).map((disk,i)=>`<div class="disk-row" data-disk-row>${wf('disk_size_'+i,'Size (GiB)',disk.size,'number','min="1" max="4096" required')}${wf('disk_label_'+i,'Volume label',disk.label,'text','maxlength="32"')}${ws('disk_letter_'+i,'Drive letter',[['','Automatic'],...Array.from('DEFGHIJKLMNOPQRSTUVWXYZ',l=>[l,l+':'])],disk.letter||'')}<button type="button" data-remove-disk="${i}">Remove disk</button></div>`).join('');
  return '<details id="disk-details" '+(rows?'open':'')+'><summary>Data disks ('+(d.data_disks||[]).length+')</summary>'+rows+
    (rows&&d.source_kind!=='template'?'<p class="subtle">ISO builds reserve D:, E: and F: for the setup DVDs. Automatic picks a free letter inside Windows.</p>':'')+
    ((d.data_disks||[]).length<3?'<button type="button" id="add-data-disk">Add data disk</button>':'')+'</details>';
}

function collectDataDisks(d,form){
  d.data_disks=[...form.querySelectorAll('[data-disk-row]')].map((row,i)=>({
    size:Number(form.elements['disk_size_'+i].value),label:form.elements['disk_label_'+i].value,letter:form.elements['disk_letter_'+i].value.toUpperCase()}));
  for(const k of Object.keys(d))if(/^disk_(size|label|letter)_/.test(k))delete d[k];
}

function showEditionPlaceholder(d){
  const select=$('#simple-form [name=edition]');
  if(!select||d.editions?.length)return;
  let label='Select an ISO first';
  if(d.source)label=$('#simple-retry-iso').hidden?'Reading editions…':'Editions unavailable';
  select.replaceChildren(new Option(label,''));
  select.disabled=true;
}

function bindWindowsFields(d){
  const form=$('#simple-form');
  showEditionPlaceholder(d);
  const pickers=[...form.querySelectorAll('select[name^="disk_letter_"]')];
  const reserved=d.source_kind==='template'?[]:simpleOptions.iso_setup_letters||[];
  function updateLetters(){
    const chosen=pickers.map(picker=>picker.value);
    pickers.forEach((picker,index)=>{
      let error='';
      for(const option of picker.options){
        const letter=option.value;
        if(!letter)continue;
        const reason=reserved.includes(letter)?'Reserved for setup DVDs':
          chosen.some((other,i)=>i!==index&&other===letter)?'Selected for another disk':'';
        option.disabled=Boolean(reason);
        option.textContent=letter+':'+(reason?' — '+reason:'');
        if(letter===chosen[index]&&reason)error=letter+': '+reason+'. Choose Automatic or another letter.';
      }
      picker.setCustomValidity(error);
    });
  }
  for(const picker of pickers)picker.onchange=updateLetters;
  updateLetters();
  form.elements.management_cidr.oninput=()=>{d.scope_edited=true};
  for(const name of ['source_kind','template_id','all_media']){
    const field=form.elements[name];if(!field)continue;
    field.onchange=async()=>{
      const previousSource=d.source;
      collectSimple();
      if(name==='all_media'){
        redrawMachineFields(name);
        if(d.source!==previousSource)await loadEditions();
        return;
      }
      editionLookup++;d.edition_identity='';d.editions=[];delete d.edition;
      $('#simple-retry-iso').hidden=true;
      reviewProgress('');
      if(d.source_kind==='template'){
        if(name==='source_kind'||name==='template_id')d.generalized=false;
        const available=windowsTemplates().filter(t=>simpleOptions.destinations.some(n=>n.node===t.node));
        const src=available.find(t=>t.vmid===Number(d.template_id))||available[0];
        const dest=simpleOptions.destinations.find(n=>n.node===src?.node);
        if(dest){d.template_id=src.vmid;Object.assign(d,{node:dest.node,storage:dest.storage,bridge:dest.bridge,iso_storage:dest.iso_storage,management_cidr:dest.management_cidr,scope_edited:false})}
      }
      redrawMachineFields(name);
      if(d.source_kind==='template'){setButtonBusy($('#simple-next'),false);$('#simple-error').textContent=''}
      else await loadEditions();
    };
  }
  const add=$('#add-data-disk');
  if(add)add.onclick=()=>{
    collectSimple();
    const index=d.data_disks.length;
    d.data_disks.push({size:32,label:'Data'+(index+1),letter:''});
    redrawMachineFields();
    form.elements['disk_size_'+index].focus();
  };
  form.querySelectorAll('[data-remove-disk]').forEach(btn=>btn.onclick=()=>{
    collectSimple();d.data_disks.splice(Number(btn.dataset.removeDisk),1);redrawMachineFields();
    const nextAdd=$('#add-data-disk');
    (nextAdd.closest('details').open?nextAdd:nextAdd.closest('details').querySelector('summary')).focus();
  });
}
