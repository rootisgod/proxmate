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

// Match the plain values accepted by guest_tasks.compile_task, including at review time.
const scriptValueAttrs = String.raw`maxlength="128" pattern="[A-Za-z0-9][A-Za-z0-9_.+@:\-]{0,127}" title="Start with a letter or digit. The remaining characters can be letters, digits or _ . + @ : - (no spaces)."`;

function scriptInputValue(values,key) {
  return values && Object.hasOwn(values,key) ? values[key] : undefined;
}

function bindScriptInputs(grid,record,edited) {
  const rows=grid.querySelector('[data-input-rows]'), add=grid.querySelector('[data-add-input]');
  let nextId=0;
  function addRow(key='',label='',value='') {
    const id='script-input-'+nextId++;
    function field(part,caption,initial,attrs) {
      const inputId=id+'-'+part;
      return '<div><label for="'+inputId+'">'+caption+'</label><input id="'+inputId+'" data-field="'+part+'" '+
        attrs+' value="'+esc(initial)+'" aria-describedby="'+inputId+'-error">'+
        '<small id="'+inputId+'-error" class="input-error" aria-live="polite"></small></div>';
    }
    rows.insertAdjacentHTML('beforeend','<div class="script-input-row">'+
      field('key','Variable name',key,'required maxlength="40" pattern="[a-z][a-z0-9_]{0,39}" title="Start with a lowercase letter. Use lowercase letters, digits or underscores."')+
      field('label','Display label',label,'required maxlength="100"')+
      field('value','Default (optional)',value,scriptValueAttrs)+
      '<button type="button" data-remove-input>Remove</button></div>');
    add.disabled=rows.children.length>=20;
  }
  function validate() {
    const keys=[...rows.querySelectorAll('[data-field=key]')];
    for(const key of keys){
      const duplicate=key.value && keys.some(other=>other!==key && other.value===key.value);
      key.setCustomValidity(duplicate?'Each variable needs a unique name.':'');
    }
    for(const input of rows.querySelectorAll('input')){
      if(input.dataset.field==='label')
        input.setCustomValidity(input.value && !input.value.trim()?'Enter a display label.':'');
      const invalid=input.dataset.touched && !input.validity.valid;
      input.setAttribute('aria-invalid',String(Boolean(invalid)));
      const error=invalid?(input.validity.patternMismatch?input.title:input.validationMessage):'';
      if(input.nextElementSibling.textContent!==error)input.nextElementSibling.textContent=error;
    }
  }
  for(const [key,label] of Object.entries(record.parameters||{}))addRow(key,label,scriptInputValue(record.defaults,key));
  add.onclick=()=>{
    addRow();
    rows.lastElementChild.querySelector('input').focus();
    edited();
  };
  rows.onclick=e=>{
    if(!e.target.closest('[data-remove-input]'))return;
    const row=e.target.closest('.script-input-row'), next=row.nextElementSibling||row.previousElementSibling;
    row.remove();add.disabled=false;validate();edited();
    (next?.querySelector('[data-field=key]')||add).focus();
  };
  function showError(e) {
    e.target.dataset.touched='true';
    validate();
  }
  rows.addEventListener('input',showError);
  rows.addEventListener('invalid',showError,true);
  return () => {
    validate();
    const parameters=Object.create(null), defaults=Object.create(null);
    if(!grid.disabled)for(const row of rows.children){
      const key=row.querySelector('[data-field=key]').value;
      parameters[key]=row.querySelector('[data-field=label]').value.trim();
      const value=row.querySelector('[data-field=value]').value;
      if(value)defaults[key]=value;
    }
    return {parameters,defaults};
  };
}

function editScript(record={}) {
  const copy = Boolean(record.example);
  const heading = copy?'Edit a starter copy':record.id?'Edit script':'New script';
  mgDialog(heading,'<div class="script-settings">'+wf('name','Script name',record.name||'','text','required maxlength="100"')+
    ws('platform','Guest OS',[['windows','Windows'],['linux','Linux']],record.platform||'windows')+
    ws('kind','Language',Object.entries(scriptFormats),record.kind||'ansible')+'</div>'+
    '<div class="script-workbench"><div class="code-toolbar"><strong>Source</strong>'+
    '<button type="button" data-find hidden>Find</button><button type="button" data-check>Check syntax</button></div>'+
    '<div data-code-host></div><textarea name="body" aria-label="Script source" spellcheck="false" rows="12">'+esc(record.body||'')+'</textarea>'+
    '<div class="code-status"><span data-check-result role="status"></span><span data-cursor>Ln 1, Col 1</span></div></div>'+
    '<p class="script-runtime" hidden>PowerShell on Linux requires pwsh on the guest.</p>'+
    '<fieldset class="script-inputs"><legend>Ansible inputs</legend>'+
      '<div data-input-rows></div><div class="script-input-actions"><button type="button" data-add-input>Add input</button>'+
      '<small>Use <code>{{ proxmate_inputs.name }}</code> in a module argument.</small></div></fieldset>',
    async values => {
      const schema=readInputs();
      if (!form.reportValidity() || !await check()) return;
      await saveRecord('tasks', {id:copy?undefined:record.id, name:values.name,
        platform:values.platform, kind:values.kind, body:values.body, ...schema});
    }, copy?'Save copy':'Save script');

  const form = $('#manage-form'), dialog = $('#manage-dialog'), {body,platform,kind} = form.elements;
  const host = form.querySelector('[data-code-host]'), status = form.querySelector('[data-check-result]');
  const checkButton = form.querySelector('[data-check]'), find = form.querySelector('[data-find]');
  const grid=form.querySelector('.script-inputs');
  const readInputs=bindScriptInputs(grid,record,edited);
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
    grid.hidden=grid.disabled=kind.value!=='ansible';
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
