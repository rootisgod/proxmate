const scriptFormats = {ansible:'Ansible YAML', powershell:'PowerShell', shell:'POSIX shell'};

const libraryFilters = {query:'',platform:'',origin:'',language:''};

function libraryVisible(item) {
  return (!libraryFilters.platform || item.platform === libraryFilters.platform) &&
    (!libraryFilters.origin || scriptOrigin(item) === libraryFilters.origin) &&
    (!libraryFilters.language || (item.kind || 'task') === libraryFilters.language) && scriptMatches(item,libraryFilters.query);
}

function scriptLibrary() {
  const items = [...mg.tasks].sort((a,b) => a.name.localeCompare(b.name));
  const option = (key,value,label) => '<option value="' + value + '"' + (libraryFilters[key] === value ? ' selected' : '') + '>' + label + '</option>';
  const filter = (key,label,any,choices) => '<label>' + label + '<select data-library-filter="' + key + '">' +
    option(key,'',any) + choices.map(([value,title]) => option(key,value,title)).join('') + '</select></label>';
  const rows = items.map(item => {
    const origin = scriptOrigin(item), id = esc(item.id), keys = Object.entries(item.parameters || {});
    const code = item.body ? '<pre tabindex="0">' + esc(item.body) + '</pre>' : '';
    const inputs = keys.length ? '<dl class="script-schema">' + keys.map(([key,label]) =>
      '<dt><code>' + esc(key) + '</code></dt><dd>' + esc(label) +
      (item.input_types?.[key]?.type !== 'password' && Object.hasOwn(item.defaults || {},key) ?
        ' · Default ' + esc(item.defaults[key]) : '') + '</dd>').join('') + '</dl>' : '';
    const details = code || inputs ? '<details class="script-source"><summary>' +
      (code && inputs ? 'Source and inputs' : code ? 'View source' : 'View inputs') + '</summary>' + inputs + code + '</details>' : '';
    return '<article class="script-entry" data-library-id="' + id + '"' + (libraryVisible(item) ? '' : ' hidden') +
      '><div class="script-entry-info"><span class="script-file" aria-hidden="true" data-kind="' + esc(item.kind) + '">' +
      ({ansible:'YML',powershell:'PS1',shell:'SH'}[item.kind] || 'TASK') + '</span><div><h2>' + esc(item.name) +
      '</h2><small>' + esc(item.platform === 'windows' ? 'Windows' : 'Linux') + ' · ' + esc(scriptLanguage(item)) + ' · ' + origin +
      '</small>' + (item.description && (origin !== 'Saved' || !item.description.startsWith('Saved administrator script')) ?
        '<p>' + esc(item.description) + '</p>' : '') + '</div></div><div class="script-entry-actions">' +
      '<button type="button" data-simple-task="' + id + '">Run script</button><button type="button" data-script-to-lab="' + id + '">Use in lab</button>' +
      (origin === 'Saved' ? mgButton('Edit','edit-tasks',item.id) : origin === 'Starter' ? mgButton('Save a copy','edit-tasks',item.id) : '') +
      (origin === 'Saved' ? '<details class="script-options"><summary aria-label="More actions for ' + esc(item.name) + '">More</summary>' +
        mgButton('Delete script','delete-tasks',item.id) + '</details>' : '') + '</div>' + details + '</article>';
  }).join('');
  const matched = items.filter(libraryVisible).length;
  return title('Script library','Reusable guest scripts for machine builds, labs and standalone runs.',
    '<button type="button" class="primary" data-mg="new-tasks">New script</button>') +
    '<section class="script-catalog" aria-label="Scripts"><div class="script-filters"><label>Search scripts<input type="search" data-library-filter="query" value="' +
    esc(libraryFilters.query) + '" placeholder="Name, input or purpose" autocomplete="off"></label>' +
    filter('platform','Platform','All platforms',[['windows','Windows'],['linux','Linux']]) +
    filter('origin','Source','All sources',[['Saved','Saved scripts'],['Built-in','Built-in tasks'],['Starter','Starter scripts']]) +
    filter('language','Language','All languages',[...Object.entries(scriptFormats),['task','Guest task']]) +
    '</div><p class="script-count" data-library-count role="status">' + matched + ' of ' + items.length + ' scripts</p>' +
    '<div class="script-entries">' + rows + '</div><p data-library-empty' + (matched ? ' hidden' : '') + '>' +
    (items.length ? 'No scripts match these filters.' : 'The library is empty. Create a script to use it in builds and runs.') + '</p></section>';
}

function filterLibrary(event) {
  const control = event.target.closest('[data-library-filter]');
  if (!control) return;
  libraryFilters[control.dataset.libraryFilter] = control.value;
  let visible = 0;
  const index = new Map(mg.tasks.map(t => [t.id,t]));
  for (const row of document.querySelectorAll('[data-library-id]')) {
    row.hidden = !libraryVisible(index.get(row.dataset.libraryId));
    if (!row.hidden) visible++;
  }
  document.querySelector('[data-library-count]').textContent = visible + ' of ' + mg.tasks.length + ' scripts';
  document.querySelector('[data-library-empty]').hidden = visible > 0;
}
document.addEventListener('input',filterLibrary);
document.addEventListener('change',filterLibrary);
document.addEventListener('click',async event => {
  const button = event.target.closest('[data-script-to-lab]');
  if (!button) return;
  try { await openLabDesigner(null,button.dataset.scriptToLab); }
  catch (error) { notice(error.message); }
});

// Match the plain values accepted by guest_tasks.compile_task, including at review time.
const scriptValueAttrs = String.raw`maxlength="128" pattern="[A-Za-z0-9][A-Za-z0-9_.+@:\-]{0,127}" title="Start with a letter or digit. The remaining characters can be letters, digits or _ . + @ : - (no spaces)."`;

function scriptInputValue(values,key) {
  return values && Object.hasOwn(values,key) ? values[key] : undefined;
}

function paramValue(control){
  return control.type==='checkbox'?String(control.checked):control.value;
}

function paramControl(spec,value,attrs,occupied=[],required=true){
  const kind=spec?.type;
  if(kind==='boolean')return '<input type="checkbox" '+attrs+(value==='true'?' checked':'')+'>';
  const needed=required?' required':'';
  if(kind==='password')return '<input type="password" autocomplete="new-password" minlength="12" maxlength="128" '+attrs+needed+'>';
  if(kind==='dns_domain')return '<input type="text" autocapitalize="none" spellcheck="false" maxlength="253" placeholder="lab.example.test" '+attrs+needed+' value="'+esc(value??'')+'">';
  if(kind==='choice'||kind==='drive'){
    const choices=kind==='drive'?[...'DEFGHIJKLMNOPQRSTUVWXYZ']:spec.choices;
    return '<select '+attrs+needed+'><option value="">'+(required?'Choose…':'No default')+'</option>'+
      choices.map(v=>{
        const used=kind==='drive' && occupied.includes(v);
        return '<option value="'+esc(v)+'"'+(used?' disabled':value===v?' selected':'')+'>'+esc(v)+
          (kind==='drive'?':':'')+(used?' — in use':'')+'</option>';
      }).join('')+'</select>';
  }
  return '<input '+scriptValueAttrs+' '+attrs+needed+' value="'+esc(value??'')+'">';
}

function bindScriptInputs(grid,record,edited) {
  const rows=grid.querySelector('[data-input-rows]'), add=grid.querySelector('[data-add-input]');
  let nextId=0;
  function addRow(key='',label='',value='',spec={type:'text'}) {
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
      '<div><label for="'+id+'-type">Type</label><select id="'+id+'-type" data-input-type>'+
      Object.entries({text:'Text',choice:'Dropdown',boolean:'Yes / no',drive:'Free drive letter'}).map(([k,v])=>
        '<option value="'+k+'"'+(spec.type===k?' selected':'')+'>'+v+'</option>').join('')+'</select></div>'+
      '<div data-default-box><label for="'+id+'-value">Default (optional)</label></div>'+
      '<button type="button" data-remove-input>Remove</button>'+
      '<div class="input-choices" hidden></div></div>');
    const row=rows.lastElementChild,type=row.querySelector('[data-input-type]'),choicesBox=row.querySelector('.input-choices');
    const defaultBox=row.querySelector('[data-default-box]');
    const options=()=>choicesBox.querySelector('input')?.value.split(',').map(v=>v.trim()).filter(Boolean)||[];
    function drawDefault(v){
      const control=paramControl({type:type.value,choices:options()},v,'id="'+id+'-value" data-field="value" aria-describedby="'+id+'-value-error"',[],false);
      defaultBox.querySelector('[data-field=value]')?.remove();
      defaultBox.querySelector('small')?.remove();
      defaultBox.insertAdjacentHTML('beforeend',control+'<small id="'+id+'-value-error" class="input-error" aria-live="polite"></small>');
    }
    function drawChoices(){
      choicesBox.hidden=type.value!=='choice';
      choicesBox.innerHTML=choicesBox.hidden?'':field('choices','Choices (comma separated)',(spec.choices||[]).join(', '),'required maxlength="6499"');
      drawDefault(value);
    }
    type.onchange=()=>{value=paramValue(defaultBox.querySelector('[data-field=value]'));drawChoices();platformChanged();validate();edited()};
    choicesBox.oninput=()=>drawDefault(defaultBox.querySelector('[data-field=value]').value);
    drawChoices();
    function platformChanged(){
      const driveOption=type.querySelector('[value=drive]');
      driveOption.disabled=grid.form.elements.platform.value!=='windows';
      type.setCustomValidity(type.value==='drive'&&driveOption.disabled?'Free drive letters require Windows.':'');
    }
    platformChanged();
    row.updatePlatform=platformChanged;
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
      if(input.dataset.field==='choices'){
        const options=input.value.split(',').map(v=>v.trim());
        const valid=options.length<=50 && new Set(options).size===options.length && options.every(v=>/^[A-Za-z0-9][A-Za-z0-9_.+@:-]{0,127}$/.test(v));
        input.setCustomValidity(valid?'':'Enter 1–50 unique plain values, separated by commas.');
      }
      if(!input.nextElementSibling)continue;
      const invalid=input.dataset.touched && !input.validity.valid;
      input.setAttribute('aria-invalid',String(Boolean(invalid)));
      const error=invalid?(input.validity.patternMismatch?input.title:input.validationMessage):'';
      if(input.nextElementSibling.textContent!==error)input.nextElementSibling.textContent=error;
    }
  }
  for(const [key,label] of Object.entries(record.parameters||{}))addRow(key,label,scriptInputValue(record.defaults,key),scriptInputValue(record.input_types,key));
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
    const parameters=Object.create(null), defaults=Object.create(null), input_types=Object.create(null);
    if(!grid.disabled)for(const row of rows.children){
      const key=row.querySelector('[data-field=key]').value;
      parameters[key]=row.querySelector('[data-field=label]').value.trim();
      const type=row.querySelector('[data-input-type]').value;
      if(type!=='text')input_types[key]=type==='choice'?{type,choices:row.querySelector('[data-field=choices]').value.split(',').map(v=>v.trim())}:{type};
      const value=paramValue(row.querySelector('[data-field=value]'));
      if(value)defaults[key]=value;
    }
    return {parameters,defaults,input_types};
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
    for(const row of grid.querySelectorAll('.script-input-row'))row.updatePlatform();
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
