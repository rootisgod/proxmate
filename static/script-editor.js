const scriptFormats = {ansible:'Ansible YAML', powershell:'PowerShell', shell:'POSIX shell'};

function scriptLibrary() {
  const scripts = mg.tasks.filter(t => t.kind);
  function cards(records) {
    return '<div class="script-cards">' + records.map(t =>
      '<article class="panel script-card"><div class="script-meta">'+
      esc(t.platform === 'windows' ? 'Windows' : 'Linux')+' · '+esc(scriptFormats[t.kind])+
      '</div><h3>'+esc(t.name)+'</h3>'+(t.example?'<p>'+esc(t.description)+'</p>':'')+
      '<div class="row-actions">'+mgButton(t.example?'Open example':'Edit script','edit-tasks',t.id)+
      '<button data-simple-task="'+esc(t.id)+'">Run script</button>'+
      (t.example?'':mgButton('Delete','delete-tasks',t.id))+'</div></article>').join('')+'</div>';
  }
  const saved = scripts.filter(t => !t.example), examples = scripts.filter(t => t.example);
  return title('Script library','Guest scripts for new and existing machines.',mgButton('New script','new-tasks'))+
    '<button data-simple="apply">Run a script</button>'+
    (saved.length?'<h2>Your scripts</h2>'+cards(saved):'')+
    (examples.length?'<h2>Starter scripts</h2><p>Open an example to read it or save your own copy.</p>'+cards(examples):'');
}

function scriptInputs(text) {
  const parameters = Object.create(null), defaults = Object.create(null);
  for (const line of text.split('\n').filter(v => v.trim())) {
    const parts = line.split('|').map(v => v.trim()), [key,label,value] = parts;
    if (!key || !label || parts.length > 3 || Object.hasOwn(parameters,key))
      throw Error('Give each input a unique name, label and optional default.');
    parameters[key] = label;
    if (value) defaults[key] = value;
  }
  return {parameters, defaults};
}

function editScript(record={}) {
  const copy = Boolean(record.example);
  const heading = copy?'Edit a starter copy':record.id?'Edit script':'New script';
  const inputs = Object.entries(record.parameters||{}).map(([key,label])=>
    [key,label,record.defaults?.[key]||''].join(' | ')).join('\n');
  mgDialog(heading,'<div class="script-settings">'+wf('name','Script name',record.name||'','text','required maxlength="100"')+
    ws('platform','Guest OS',[['windows','Windows'],['linux','Linux']],record.platform||'windows')+
    ws('kind','Language',Object.entries(scriptFormats),record.kind||'ansible')+'</div>'+
    '<div class="script-workbench"><div class="code-toolbar"><strong>Source</strong>'+
    '<button type="button" data-find hidden>Find</button><button type="button" data-check>Check syntax</button></div>'+
    '<div data-code-host></div><textarea name="body" aria-label="Script source" spellcheck="false" rows="12">'+esc(record.body||'')+'</textarea>'+
    '<div class="code-status"><span data-check-result role="status"></span><span data-cursor>Ln 1, Col 1</span></div></div>'+
    '<p class="script-runtime" hidden>PowerShell on Linux requires pwsh on the guest.</p>'+
    '<details class="script-inputs"><summary>Ansible inputs</summary>'+
      mgArea('inputs','One name | label | optional default per line',inputs,'rows="2"')+
      '<small>Use {{ proxmate_inputs.name }} inside a module argument.</small></details>',
    async values => {
      if (!await check()) return;
      const schema = scriptInputs(values.kind==='ansible'?values.inputs:'');
      await saveRecord('tasks', {id:copy?undefined:record.id, name:values.name,
        platform:values.platform, kind:values.kind, body:values.body, ...schema});
    }, copy?'Save copy':'Save script');

  const form = $('#manage-form'), dialog = $('#manage-dialog'), {body,platform,kind} = form.elements;
  const host = form.querySelector('[data-code-host]'), status = form.querySelector('[data-check-result]');
  const checkButton = form.querySelector('[data-check]'), find = form.querySelector('[data-find]');
  let editor, version=0, request=0;
  const active = () => dialog.open && host.isConnected;
  function edited() {
    version++;
    status.textContent = 'Edited';
    status.className = '';
  }
  function updateFormat() {
    const shell = kind.querySelector('[value=shell]');
    if (platform.value==='windows' && kind.value==='shell') kind.value='powershell';
    shell.disabled = platform.value==='windows';
    form.querySelector('.script-inputs').hidden = kind.value!=='ansible';
    form.querySelector('.script-runtime').hidden = !(platform.value==='linux' && kind.value==='powershell');
    editor?.language(kind.value);
  }
  async function check() {
    const current = version, id = ++request;
    status.textContent = 'Checking…'; status.className = ''; checkButton.disabled=true;
    try {
      const result = await api('manage/tasks/check',{platform:platform.value,kind:kind.value,body:body.value});
      if (!active() || current!==version || id!==request) return false;
      status.className = result.valid?'code-valid':'code-error';
      status.textContent = result.valid?'Syntax OK':
        (result.line?'Line '+result.line+', column '+result.column+' · ':'')+result.message;
      if (!result.valid && result.line) {
        if (editor) editor.locate(result.line,result.column);
        else {
          const offset = body.value.split('\n').slice(0,result.line-1).reduce((n,line)=>n+line.length+1,0)+result.column-1;
          body.focus();body.setSelectionRange(offset,offset);
        }
      }
      return result.valid;
    } catch (error) {
      if (active() && current===version && id===request) {
        status.className='code-error'; status.textContent=error.message;
      }
      return false;
    } finally { if (id===request) checkButton.disabled=false; }
  }
  updateFormat();
  form.oninput=edited;
  platform.onchange=kind.onchange=()=>{edited();updateFormat()};
  checkButton.onclick=check;
  find.onclick=()=>editor?.find();
  const shortcuts = e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (e.key.toLowerCase()==='s') {
      e.preventDefault(); if (!$('#manage-submit').disabled) form.requestSubmit();
    } else if (e.key==='Enter') { e.preventDefault(); if (!checkButton.disabled) check(); }
  };
  form.addEventListener('keydown',shortcuts);
  dialog.addEventListener('close',()=>{
    editor?.destroy();form.oninput=null;form.removeEventListener('keydown',shortcuts);
  },{once:true});
  import('/static/editor-assets/code.js').then(({mount})=>{
    if (!active()) return;
    editor=mount(host,body,kind.value,(line,col)=>{
      form.querySelector('[data-cursor]').textContent='Ln '+line+', Col '+col;
    });
    find.hidden=false;
  }).catch(()=>{ if (active()) status.textContent='Plain text editor'; });
}
