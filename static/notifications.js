let notificationServer=null;
let observedJobs=null;

function toastHost(){
  const host=document.getElementById('toasts');
  const parent=[...document.querySelectorAll('dialog[open]')].at(-1)||document.body;
  if(host.parentElement!==parent)parent.append(host);
  return host;
}

function showToast(message,{kind='info',jobId}={}){
  const host=toastHost(),card=document.createElement('div'),text=document.createElement('span');
  card.className='toast toast-'+kind;
  text.setAttribute('role',kind==='failure'?'alert':'status');
  card.append(text);host.append(card);text.textContent=message;
  if(jobId){
    const details=document.createElement('button');
    details.type='button';details.textContent='View log';
    details.onclick=()=>{card.remove();openJobLog(jobId)};
    card.append(details);
  }
  const close=document.createElement('button');
  close.type='button';close.className='toast-close';close.setAttribute('aria-label','Dismiss notification');close.textContent='×';
  close.onclick=()=>card.remove();card.append(close);
  while(host.childElementCount>4)host.firstElementChild.remove();
  const dismiss=()=>{if(!card.matches(':hover, :focus-within'))card.remove()};
  if(kind!=='failure'){
    setTimeout(dismiss,9000);
    card.addEventListener('focusout',()=>setTimeout(dismiss,9000));
    card.addEventListener('mouseleave',()=>setTimeout(dismiss,9000));
  }
}

document.addEventListener('close',()=>{if(document.getElementById('toasts')?.childElementCount)toastHost()},true);
document.addEventListener('toggle',e=>{
  if(e.target.tagName==='DIALOG'&&document.getElementById('toasts')?.childElementCount)toastHost();
},true);

function selectNotifications(id){
  if(id===notificationServer&&observedJobs)return;
  document.getElementById('toasts').replaceChildren();
  notificationServer=id;observedJobs=null;
  if(id){
    try{const saved=JSON.parse(sessionStorage.getItem('proxmate.jobs.'+id));if(saved?.states&&Number.isFinite(saved.since))observedJobs=saved}catch{}
  }
}

function saveObserved(){
  try{sessionStorage.setItem('proxmate.jobs.'+notificationServer,JSON.stringify(observedJobs))}catch{}
}

function announceJobs(rows,status){
  const labels={queued:'queued',running:'started',succeeded:'finished',failed:'failed',interrupted:'interrupted',cancelled:'cancelled'};
  const first=rows[0],single=rows.length===1;
  const label=first.target?.name||first.name||first.target?.vmid;
  const title=single?(first.action==='simple'?'Build':first.action||'Job')+(label?' · '+label:''):rows.length+' jobs';
  showToast(title+' '+labels[status],{kind:['failed','interrupted','cancelled'].includes(status)?'failure':status==='succeeded'?'success':'info',jobId:single?first.id:null});
}

function submittedBuilds(rows){
  if(!rows.length)return;
  selectNotifications(serverInfo.id);
  observedJobs??={since:Date.now()/1000,states:{}};
  const fresh=rows.filter(j=>!observedJobs.states[j.id]);
  for(const j of fresh)observedJobs.states[j.id]='queued';
  saveObserved();
  if(fresh.length)announceJobs(fresh.map(j=>({...j,action:'simple'})),'queued');
}

function observeJobs(rows,id){
  selectNotifications(id);
  if(!id)return;
  const first=!observedJobs;
  observedJobs??={since:Math.max(0,...rows.map(j=>j.created||0)),states:{}};
  const next={},groups=new Map();
  for(const j of rows){
    if(j.server_id&&j.server_id!==id)continue;
    const status=j.status==='waiting'?'running':j.status,old=observedJobs.states[j.id];
    next[j.id]=status;
    if(first||old===status||(!old&&j.created<observedJobs.since))continue;
    if(!['queued','running','succeeded','failed','interrupted','cancelled'].includes(status))continue;
    if(!groups.has(status))groups.set(status,[]);
    groups.get(status).push(j);
  }
  if(first||JSON.stringify(observedJobs.states)!==JSON.stringify(next)){
    observedJobs.states=next;saveObserved();
  }
  for(const [status,items] of groups)announceJobs(items,status);
}
