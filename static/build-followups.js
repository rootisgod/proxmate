function buildButton(label, click){
  const button=document.createElement('button');
  button.type='button';
  button.textContent=label;
  button.onclick=click;
  return button;
}

async function loadGuidedRecipes(area){
  try{
    const recipes=await api('simple/recipes');
    if(!area.isConnected)return;
    area.innerHTML='<h2>Saved builds</h2>';
    if(!recipes.length){
      area.insertAdjacentHTML('beforeend','<p>Choose <strong>Save this build</strong> in a build review or job log to use its settings again.</p>');
      return;
    }
    for(const recipe of recipes){
      const row=document.createElement('div');row.className='saved-build';
      const label=document.createElement('span');label.textContent=recipe.name;
      const detail=document.createElement('small');
      detail.textContent=(recipe.os==='windows'?'Windows':linuxNames[recipe.os])+' · '+recipe.edition;
      label.append(detail);
      row.append(label,buildButton('Use build',()=>repeatGuidedBuild(recipe,{recipe_id:recipe.id})),
        buildButton('Remove',async()=>{
          if(!window.confirm('Remove “'+recipe.name+'” from saved builds?'))return;
          try{await api('simple/recipes/'+encodeURIComponent(recipe.id)+'/delete',{});await loadGuidedRecipes(area)}
          catch(error){notice(error.message)}
        }));
      area.append(row);
    }
  }catch(error){if(area.isConnected)area.textContent='Saved builds could not be loaded. '+error.message}
}

function saveGuidedRecipe(source,name){
  mgDialog('Save this build',wf('name','Saved build name',name||'','text','required maxlength="100"')+
    '<p>Reuse the installation source, hardware, settings and software. Each new machine gets its own login.</p>',async values=>{
    await api('simple/recipes',{...source,name:values.name});
    $('#manage-dialog').close();
    if($('#saved-builds'))await loadGuidedRecipes($('#saved-builds'));
    showToast('Build saved to Create.');
  },'Save build');
}

function repeatGuidedBuild(recipe,reuse){
  const windows=recipe.os==='windows';
  const fields=wf('name','New machine name','','text','required maxlength="12" pattern="[A-Za-z][A-Za-z0-9\\-]{0,11}" autofocus')+
    (recipe.password_login?wf(windows?'password':recipe.os==='ubuntu'?'ubuntu_password':'linux_password',windows?'New Administrator password':'New guest password','','password',
      'required minlength="12" maxlength="128" autocomplete="new-password"'):'')+
    (recipe.needs_key_source?'<p>Supply the workstation key source again for this older build.</p>'+
      wf('github_username','GitHub username','','text','maxlength="39"')+wf('ssh_public_key','Or paste an SSH public key','','text','maxlength="8192"'):'')+
    (windows&&recipe.source_kind==='template'?wc('generalized','I verified this source template was prepared with Sysprep and the QEMU Guest Agent.'):'');
  mgDialog('Create another '+(recipe.purpose==='template'?'template':'VM'),
    '<p>'+esc(recipe.name||recipe.edition)+'</p>'+fields+'<p role="status" id="repeat-progress"></p>',async values=>{
    const progress=$('#repeat-progress');
    const active=()=>progress.isConnected&&$('#manage-dialog').open;
    const plan=await requestBuildReview({...values,reuse},active,message=>{progress.textContent=message});
    if(!active()||!plan)return;
    mgDialog('Review new '+(recipe.purpose==='template'?'template':'VM'),buildReview(plan,windows),async confirmation=>{
      const result=await api('simple/run',{plan_id:plan.id,confirmed:!!confirmation.confirmed,risk_confirmed:!!confirmation.risk_confirmed});
      submittedBuilds(result.jobs);
      $('#manage-dialog').close();await refresh();
    },'Create '+(recipe.purpose==='template'?'template':'VM'));
  },'Review build');
}

function reviewRemaining(job,target){
  mgDialog('Review remaining tasks','<p role="status" id="recovery-progress">Checking the original machine and its saved connection…</p>',()=>{},'Checking…');
  const progress=$('#recovery-progress');
  $('#manage-submit').disabled=true;
  api('simple/recovery/plan',{job_id:job.id,target:target.target}).then(plan=>{
    if(!progress.isConnected||!$('#manage-dialog').open)return;
    const warning=plan.recovery.started?'<p>The first task below was started and may have changed the guest. Check its effects before running it again.</p>':'';
    const destination=plan.targets[0];
    const tasks='<ol>'+plan.tasks.map(t=>'<li>'+esc(t.name)+' '+esc(Object.values(t.params||{}).join(', '))+
      (t.data_disk?' · '+esc(t.data_disk.size)+' GiB NTFS · '+esc(t.data_disk.letter||'Automatic drive letter'):'')+'</li>').join('')+'</ol>';
    mgDialog('Review remaining tasks','<h3>'+esc(destination.name)+' · '+esc(target.target)+'</h3><p>'+
      'Login verified · '+esc(destination.credential_name)+' · '+esc(destination.guest_host)+'</p>'+warning+tasks+
      (plan.snapshot_before?'<p>Take a new pre-task snapshot.</p>':'')+
      (plan.finish_template?'<p>After the tasks succeed, generalize this guest and shut it down as a reusable template.</p>':'')+
      wc('confirmed','Run the listed tasks on this machine.')+(plan.dangerous?wc('risk_confirmed','I approve the effects of this run.'):''),
      async values=>{
        const run=await api('manage/run',{plan_id:plan.id,confirmed:!!values.confirmed,risk_confirmed:!!values.risk_confirmed});
        $('#manage-dialog').close();openJobLog(run.id);await refresh();
      },'Run remaining tasks');
  }).catch(error=>{if(progress.isConnected)progress.textContent=error.message});
}

function jobFollowupActions(job,log){
  log.parentElement.querySelector('.job-followups')?.remove();
  const section=document.createElement('section');section.className='job-followups';
  if(job.recovery_of)section.append(buildButton('View original run',()=>openJobLog(job.recovery_of)));
  if(job.reusable_build&&job.server_id===serverInfo?.id){
    const actions=document.createElement('div');actions.className='row-actions';
    actions.append(buildButton('Create another like this',()=>repeatGuidedBuild(job.reusable_build,{job_id:job.id})),
      buildButton('Save this build',()=>saveGuidedRecipe({job_id:job.id},job.target.name)));
    section.append(actions);
  }
  if(job.task_recovery?.server_id===serverInfo?.id){
    for(const target of job.task_recovery?.targets||[]){
      const row=document.createElement('div');row.className='saved-build';
      const line=document.createElement('span');line.textContent=target.name+' · '+target.target;
      const detail=document.createElement('small');
      detail.textContent=target.completed+' completed · '+target.remaining+' remaining · '+target.next_task;line.append(detail);
      row.append(line,target.successor?buildButton('View recovery run',()=>openJobLog(target.successor)):
        buildButton('Review remaining tasks',()=>reviewRemaining(job,target)));
      section.append(row);
    }
  }
  if(section.childElementCount)log.before(section);
}
