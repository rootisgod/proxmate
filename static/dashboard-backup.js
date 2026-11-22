function renderDashboardBackup(){
  if(document.getElementById('dashboard-backup-page'))return;
  const page=document.createElement('div');
  page.id='dashboard-backup-page';
  page.className='settings-workspace';
  page.innerHTML=title('Backup &amp; restore','Recover this dashboard or move your lab definitions to another cluster.')+
    '<section class="panel server-panel"><h2>Create a backup</h2><form id="backup-download"><div class="form-grid">'+
    '<label class="wide">Backup contents<select name="mode"><option value="full">Complete dashboard</option><option value="portable">Portable configuration</option></select></label>'+
    '<p class="wide subtle" id="backup-scope"></p><label>Backup passphrase<input type="password" name="passphrase" minlength="12" maxlength="256" required autocomplete="new-password"><small>At least 12 characters. Keep it with your recovery instructions.</small></label>'+
    '<label>Confirm passphrase<input type="password" name="repeat" required autocomplete="new-password"></label>'+
    '<label class="wide">Current dashboard password<input type="password" name="admin_password" autocomplete="current-password" required></label></div>'+
    '<p role="status" class="backup-feedback"></p><button class="primary">Download backup</button></form></section>'+
    '<section class="panel server-panel"><h2>Restore a backup</h2><p>Choose a file to see its contents and how it affects this dashboard.</p>'+
    '<form id="backup-upload"><div class="form-grid"><label>Backup file<input type="file" name="file" accept=".proxmate" required></label>'+
    '<label>Backup passphrase<input type="password" name="passphrase" autocomplete="off" minlength="12" maxlength="256" required></label></div>'+
    '<p role="status" class="backup-feedback"></p><button>Preview restore</button></form><div id="restore-review"></div></section>';
  $('#content').replaceChildren(page);
  const download=page.querySelector('#backup-download'), upload=page.querySelector('#backup-upload');
  const review=page.querySelector('#restore-review');
  const report=(form,msg)=>{form.querySelector('.backup-feedback').textContent=msg};
  function scope(){
    const full=download.elements.mode.value==='full', admin=download.elements.admin_password;
    admin.closest('label').hidden=!full;
    admin.disabled=!full;
    if(!full)admin.value='';
    page.querySelector('#backup-scope').textContent=full?
      'Includes saved credentials, server settings, lab definitions, scripts, schedules and job history. VM disks and ISOs are backed up separately.':
      'Includes custom scripts, lab packs and portable saved builds. Credentials and machine bindings stay on this dashboard.';
  }
  download.elements.mode.onchange=scope;
  scope();
  download.onsubmit=async event=>{
    event.preventDefault();
    await withBusyButton(download.querySelector('button'),async()=>{
      const d=Object.fromEntries(new FormData(download));
      if(d.passphrase!==d.repeat){report(download,'The backup passphrases must match.');return}
      delete d.repeat;
      report(download,'Preparing encrypted backup…');
      try{
        const blob=await api('dashboard-backup/export',d,{},'blob');
        const link=document.createElement('a');
        link.href=URL.createObjectURL(blob);
        link.download='proxmate-'+d.mode+'-'+new Date().toISOString().replace(/[:.]/g,'-')+'.proxmate';
        link.click();
        setTimeout(()=>URL.revokeObjectURL(link.href),1000);
        download.reset();scope();
        report(download,'Backup downloaded. Store the file and its passphrase separately.');
      }catch(e){report(download,e.message)}
    });
  };
  let revision=0;
  function invalidate(){revision++;review.replaceChildren();report(upload,'')}
  upload.oninput=invalidate;
  upload.onchange=invalidate;
  function readFile(file){
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onload=()=>resolve(reader.result.split(',')[1]);
      reader.onerror=()=>reject(Error('Could not read this backup file.'));
      reader.readAsDataURL(file);
    });
  }
  upload.onsubmit=async event=>{
    event.preventDefault();
    const attempt=++revision;
    review.replaceChildren();
    await withBusyButton(upload.querySelector('button'),async()=>{
      try{
        const file=upload.elements.file.files[0];
        if(!file||file.size>64*1024*1024)throw Error('Choose a .proxmate file up to 64 MiB.');
        report(upload,'Opening and checking the backup…');
        const passphrase=upload.elements.passphrase.value;
        const archive=await readFile(file);
        const body={archive,passphrase};
        const result=await api('dashboard-backup/preview',body);
        if(attempt!==revision||!page.isConnected)return;
        report(upload,'Preview ready. No changes have been made.');
        showReview(result,body);
      }catch(e){if(attempt===revision)report(upload,e.message)}
    });
  };
  function showReview(result,body){
    const full=result.mode==='full';
    const stamp=new Date(result.created*1000).toLocaleString();
    review.innerHTML='<form class="backup-review"><h3>'+ (full?'Replace this dashboard':'Import definitions')+'</h3>'+
      '<p>Backup created '+esc(stamp)+'. Destination: <strong>'+esc(result.destination)+'</strong>.</p>'+
      (full?'<p>Recovered server: <strong>'+esc(result.source)+'</strong></p><dl class="backup-counts">'+
        Object.entries(result.counts).map(([k,v])=>'<div><dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd></div>').join('')+'</dl>'+
        '<p>This replaces the current dashboard, including its '+esc(result.destination_counts.jobs||0)+' jobs and '+esc(result.destination_counts.credentials||0)+
        ' credentials. Download a complete backup of this destination first if you want to undo replacement. Finish active jobs and disable schedules and policies here before restoring. The recovered server will be disconnected, automation paused and unfinished jobs interrupted.</p>'+
        '<label>Current dashboard password<input name="admin_password" type="password" autocomplete="current-password" required></label>'+
        '<label class="check"><input name="confirmed" type="checkbox" required>Replace this dashboard with the reviewed backup.</label>':
        (result.items.length?'<fieldset><legend>Definitions</legend>'+result.items.map(item=>
          '<label class="check"><input name="selected" type="checkbox" value="'+esc(item.id)+'" '+(item.status==='new'?'checked':'disabled')+'>'+
          esc(item.name)+' · '+esc(item.kind)+' · '+esc(item.status)+'</label>').join('')+'</fieldset>':'<p>No portable definitions in this file.</p>')+
        (result.omitted.length?'<p>Saved builds without portable snapshots: '+result.omitted.map(esc).join(', ')+'. Use a complete backup to recover them.</p>':'')+
        '<p>Imported builds appear under Labs. Choose local media, logins and targets when deploying.</p>')+
      '<p class="backup-feedback" role="status"></p><button class="primary">'+(full?'Restore complete dashboard':'Import selected')+'</button></form>';
    const form=review.querySelector('form'),button=form.querySelector('button');
    const update=()=>{button.disabled=full?!form.elements.confirmed.checked:!form.querySelector('[name="selected"]:checked')};
    form.onchange=update;update();
    form.onsubmit=async event=>{
      event.preventDefault();
      await withBusyButton(button,async()=>{
        const fields=new FormData(form);
        const data={...body,review:result.review,confirmed:true};
        if(full)data.admin_password=fields.get('admin_password');
        else data.selected=fields.getAll('selected');
        report(form,'Applying the reviewed changes…');
        upload.querySelector('button').disabled=true;
        try{
          const done=await api('dashboard-backup/restore',data);
          sessionStorage.setItem('proxmate-backup-result',full?'Dashboard restored. Verify your recovered server in Server settings before reconnecting.':
            'Imported '+done.imported+' definitions. Open Labs or Script library to use them.');
          location.reload();
        }catch(e){report(form,e.message)}
        finally{upload.querySelector('button').disabled=false}
      });
    };
  }
  const message=sessionStorage.getItem('proxmate-backup-result');
  if(message){sessionStorage.removeItem('proxmate-backup-result');showToast(message,{kind:'success'});report(upload,message)}
}
