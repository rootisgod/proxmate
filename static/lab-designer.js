/* The role designer edits portable settings and delegates ordered tasks to the shared script editor. */
async function openLabDesigner(packId = null, initialScriptId = null, recipeId = null) {
  if (typeof packId !== 'string') packId = null;
  const heading = packId ? 'Edit lab copy' : 'Create lab';
  const loading = labStep(heading, '<p>Loading roles and scripts…</p>', 'Retry', '', () => openLabDesigner(packId,initialScriptId,recipeId));
  setButtonBusy($('#lab-next'),true);
  try {
    const [catalog,options,recipes,pack,recipe] = await Promise.all([
      api('manage/tasks'),api('simple/options').catch(() => null),api('simple/recipes'),
      packId ? api('simple/labs/packs/' + encodeURIComponent(packId)) : null,
      recipeId ? api('simple/recipes/' + encodeURIComponent(recipeId) + '/lab-source') : null
    ]);
    if (pack && pack.machines.some(m => (m.editable_tasks || []).length !== (m.tasks || []).length)) {
      throw Error('The step definitions for this pack could not be loaded. Reopen it before editing.');
    }
    if (labActive(loading)) designLab(catalog,options,recipes,pack,initialScriptId,recipe);
  } catch (error) {
    if (labActive(loading)) {
      $('#lab-error').textContent = error.message;
      setButtonBusy($('#lab-next'),false);
    }
  }
}

function designLab(catalog, options, recipes, pack, initialScriptId, recipe) {
  const machines = [], guests = (state?.resources || []).filter(g => g.type === 'qemu');
  let sequence = 0, active = null, copying = false;
  const name = pack ? pack.name.slice(0,93) + ' (copy)' : '';
  const fields = labStep(pack ? 'Edit lab copy' : 'Create lab',
    '<div class="lab-designer"><div class="design-name">' +
    wf('lab_name','Lab name',name,'text','required maxlength="100" placeholder="Development lab"') + '</div>' +
    '<div class="design-layout"><div class="design-outline"><div class="design-outline-head"><h3>Roles</h3>' +
    '<button type="button" id="design-add-role" aria-label="Add a role"><span class="bi bi-plus-lg" aria-hidden="true"></span> Add role</button></div>' +
    '<div id="design-roles"></div></div><div class="design-detail"><div id="design-add-panel"></div><div id="design-machines"></div></div></div></div>',
    pack ? 'Save copy' : 'Save lab','Saving lab pack…',async area => {
      const definitions = machines.map(m => ({...m.definition(),after:machines.filter(other => m.after.has(other.uid)).map(other => other.key.value)}));
      const saved = await api('simple/labs/packs',{name:area.querySelector('[name=lab_name]').value.trim(),machines:definitions});
      if (!labActive(area)) return;
      loadLabList('packs');
      labStep('Lab saved','<p><strong>' + esc(saved.name) + '</strong> is in Saved packs. Set up the lab when you are ready to choose its destination.</p>' +
        '<p><a href="/api/simple/labs/packs/' + encodeURIComponent(saved.id) + '/export" download>Download ZIP</a></p>',
        'Set up lab','',() => openSavedPack(saved.id));
    }, area => {
      const field = area.querySelector('[name=lab_name]');
      if (!field.reportValidity()) return false;
      if (copying || !machines.length) {
        $('#lab-error').textContent = copying ? 'Wait for the role to load.' : 'Add a role to this lab.';
        if (!copying) showAdd();
        return false;
      }
      for (const machine of machines) {
        const invalid = [...machine.area.querySelectorAll('input,select,textarea')].find(input => input.willValidate && !input.validity.valid);
        if (invalid) {
          selectMachine(machine);
          machine.showPane(invalid.closest('.design-process') ? 'process' : 'settings');
          for (let node=invalid.parentElement; node && node!==machine.area; node=node.parentElement) {
            if (node.tagName === 'DETAILS') node.open = true;
          }
          invalid.reportValidity();
          return false;
        }
        if (machine.intent === 'Existing VM' && !machine.stepCount) {
          selectMachine(machine);machine.showPane('process');
          $('#lab-error').textContent = 'Add a script step for ' + machine.key.value + '.';
          return false;
        }
      }
      return true;
    });

  const rail = fields.querySelector('#design-roles'), chooser = fields.querySelector('#design-add-panel');
  function outline() {
    const focusUid = rail.contains(document.activeElement) ? document.activeElement.dataset.chooseRole : '';
    rail.innerHTML = machines.length ? machines.map(m =>
      '<button type="button" data-choose-role="' + m.uid + '"' + (m === active ? ' aria-current="true"' : '') + '>' +
      '<span class="bi bi-' + (m.intent === 'Existing VM' ? 'terminal' : 'pc-display') + '" aria-hidden="true"></span><span><strong>' + esc(m.key.value || 'Unnamed role') + '</strong><small>' + esc(m.intent) +
      ' · ' + m.stepCount + (m.stepCount === 1 ? ' step' : ' steps') + '</small></span></button>').join('') :
      '<p>Roles appear here as you add them.</p>';
    if (focusUid) rail.querySelector('[data-choose-role="' + focusUid + '"]')?.focus();
  }
  function dependencies() {
    for (const machine of machines) {
      const names = () => machines.filter(other => machine.after.has(other.uid)).map(other => other.key.value || 'Unnamed role');
      const panel = machine.area.querySelector('.design-dependencies');
      panel.innerHTML = '<h4>Starts after</h4>' + (machines.length === 1 ? '<p>No other roles in this lab.</p>' :
        machines.filter(m => m !== machine).map(other => '<label class="check"><input type="checkbox" data-dependency="' + other.uid + '"' +
        (machine.after.has(other.uid) ? ' checked' : '') + '>' + esc(other.key.value || 'Unnamed role') + '</label>').join(''));
      panel.onchange = e => {
        const uid = Number(e.target.dataset.dependency);
        if (e.target.checked) machine.after.add(uid); else machine.after.delete(uid);
        machine.updateHeading(names());
      };
      machine.updateHeading(names());
    }
  }
  function selectMachine(machine) {
    active = machine; chooser.hidden = true;
    for (const m of machines) m.area.hidden = m !== machine;
    outline();
  }
  rail.onclick = event => {
    const item = event.target.closest('[data-choose-role]');
    if (item) { selectMachine(machines.find(m => m.uid === Number(item.dataset.chooseRole))); active.area.querySelector('h3').focus(); }
  };
  function add(source, suggestedName = source.name) {
    if (machines.length >= 20) throw Error('A lab can include up to 20 roles.');
    const used = new Set(machines.map(m => m.key.value.toLowerCase()));
    const [key] = suggestedLabNames([suggestedName || 'vm'],15,used);
    const machine = designMachine(++sequence,key,source,catalog,options,outline);
    machines.push(machine);
    fields.querySelector('#design-machines').append(machine.area);
    machine.key.oninput = () => { dependencies(); outline(); };
    machine.area.querySelector('[data-remove-machine]').onclick = () => {
      const index = machines.indexOf(machine);
      machines.splice(index,1);
      for (const other of machines) other.after.delete(machine.uid);
      machine.area.remove(); dependencies();
      if (machines.length) selectMachine(machines[Math.min(index,machines.length-1)]); else showAdd();
      fields.querySelector('#design-add-role').focus();
    };
    dependencies();selectMachine(machine);
    return machine;
  }
  function showAdd() {
    active = null;
    for (const machine of machines) machine.area.hidden = true;
    chooser.hidden = false;outline();
    chooser.querySelector('[name=role_method]:checked').focus();
  }
  const modes = [
    ['new','New machine','Define a new VM and its setup.'],
    ['saved','Saved build','Start with a reusable build and its steps.'],
    ['copy','VM blueprint','Copy settings from a machine on this server.'],
    ['existing','Existing VM','Run steps on a machine already in the lab.']
  ];
  chooser.innerHTML = '<h3>Add a role</h3><fieldset class="design-intents"><legend>Role source</legend>' +
    modes.map(([id,label,detail],i) => '<label><input type="radio" name="role_method" value="' + id + '"' + (!i ? ' checked' : '') +
      '><span><strong>' + label + '</strong><small>' + detail + '</small></span></label>').join('') +
    '</fieldset><div class="design-add-input"></div><div class="design-add-actions"><button type="button" class="primary" id="design-accept-role">Add to lab</button></div>';
  const selection = () => chooser.querySelector('[name=role_method]:checked').value;
  const addButton = chooser.querySelector('#design-accept-role');
  function addInputs() {
    const mode = selection(), slot = chooser.querySelector('.design-add-input');
    addButton.disabled = false;
    if (mode === 'new') slot.innerHTML = ws('new_os','Operating system',[['windows','Windows'],...Object.entries(linuxNames)],'ubuntu');
    if (mode === 'saved') {
      slot.innerHTML = recipes.length ? ws('saved_build','Saved build',[['','Choose a saved build'],...recipes.map(r => [r.id,r.name])],'') :
        '<p>Save a build from its guided review or job log to use it here.</p>';
      addButton.disabled = !recipes.length;
    }
    if (mode === 'copy' || mode === 'existing') {
      const choices = guests.map((g,i) => [g,i]).filter(([g]) => mode === 'copy' || !g.template);
      slot.innerHTML = choices.length ? ws('existing_vm',mode === 'copy' ? 'Blueprint VM' : 'Suggested VM',
        [[ '',mode === 'copy' ? 'Choose a machine' : 'Choose at deployment'],...choices.map(([g,i]) => [i,(g.name || 'VM') + ' · ' + g.node + ' / ' + g.vmid])],'') :
        '<p>' + (mode === 'copy' ? 'No VM blueprints are available on this server.' : 'Select the actual VM when you deploy.') + '</p>';
      addButton.disabled = mode === 'copy' && !choices.length;
    }
    slot.insertAdjacentHTML('beforeend',wf('role_name','Role name (optional)','','text','maxlength="15" pattern="[A-Za-z](?:[A-Za-z0-9\\-]{0,13}[A-Za-z0-9])?" placeholder="Use a suggested name"'));
    if (!options && mode === 'new') slot.insertAdjacentHTML('beforeend','<p>Live media are unavailable. Choose the source at deployment.</p>');
  }
  chooser.querySelector('.design-intents').onchange = addInputs;addInputs();
  addButton.onclick = async () => {
    $('#lab-error').textContent = '';
    const mode = selection();
    const roleName = chooser.querySelector('[name=role_name]');
    if (!roleName.reportValidity()) return;
    copying = true;chooser.inert = true;
    $('#lab-status').textContent = mode === 'saved' || mode === 'copy' ? 'Reading build settings…' : '';
    try {
      let source;
      if (mode === 'new') source = {os:chooser.querySelector('[name=new_os]').value};
      else if (mode === 'saved') {
        const id = chooser.querySelector('[name=saved_build]')?.value;
        if (!id) throw Error('Select a saved build.');
        source = await api('simple/recipes/' + encodeURIComponent(id) + '/lab-source');
        source = {...source,basis:'recipe',name:source.name || recipes.find(r => r.id === id).name};
      } else {
        const chosen = chooser.querySelector('[name=existing_vm]')?.value;
        if (!chosen && mode === 'copy') throw Error('Select a VM to use as a blueprint.');
        const g = chosen ? guests[Number(chosen)] : null;
        const data = g ? await api('simple/labs/machine-source',{node:g.node,vmid:g.vmid}) : {};
        source = mode === 'copy' ? data : {name:data.name || 'target',os:data.os === 'windows' ? 'windows' : data.os ? 'linux' : null,
          source:data.name ? {kind:'existing',hint:data.name} : {kind:'existing'}};
      }
      if (labActive(fields)) {
        const machine = add(source,roleName.value || source.name);
        roleName.value = '';
        machine.showPane(source.os ? 'process' : 'settings');
        (source.os ? machine.area.querySelector('h3') : machine.area.querySelector('[name=os]')).focus();
      }
    } catch(error) { if (labActive(fields)) $('#lab-error').textContent = error.message; }
    finally { copying=false;chooser.inert=false;if(labActive(fields)) $('#lab-status').textContent=''; }
  };
  fields.querySelector('#design-add-role').onclick = showAdd;
  if (pack) {
    for (const m of pack.machines) add({...m,name:m.key,tasks:m.editable_tasks,base_pack_id:pack.id,base_role:m.key,basis:'pack'});
    pack.machines.forEach((m,i) => machines.filter(other => m.after?.includes(other.key.value)).forEach(other => machines[i].after.add(other.uid)));
    dependencies();selectMachine(machines[0]);
  } else if (recipe) {
    const name = recipes.find(r => r.id === recipe.base_recipe_id)?.name || 'vm';
    add({...recipe,name,basis:'recipe'});
  } else if (initialScriptId) {
    const item = catalog.find(t => t.id === initialScriptId);
    if (item) add({os:item.platform === 'windows' ? 'windows' : 'ubuntu',prefill:item});
    else { showAdd(); $('#lab-error').textContent = 'The selected script is no longer in the library.'; }
  } else showAdd();
  fields.querySelector('[name=lab_name]').focus();
}

function designMedia(os, kind, options) {
  const destinations = options?.destinations || [];
  if (kind === 'template') {
    const templates = os === 'windows' ? windowsTemplates() : options?.[os + '_templates'] || [];
    return templates.map(t => ({name:t.name})).filter(t => t.name);
  }
  if (kind !== 'iso') return [];
  return destinations.flatMap(dest => {
    const isos = os === 'windows' ? [...dest.isos,...options.local_isos] : dest[os + '_isos'] || [];
    return isos.map(iso => ({node:dest.node,source:iso.id,name:iso.label.replace(/ \(ISO library\)$/, '')}));
  });
}

function designMachine(uid, name, copied, catalog, options, onChange) {
  const area = document.createElement('section');
  area.className = 'design-machine';
  const settings = copied.settings || {}, originalOS = copied.os;
  const initialSource = copied.source || {};
  const existingTarget = initialSource.kind === 'existing';
  const systems = [['','Choose an operating system'],['windows','Windows'],...(existingTarget ? [['linux','Linux']] : Object.entries(linuxNames))];
  const intent = existingTarget ? 'Existing VM' : copied.basis === 'recipe' ? 'Saved build' :
    ['guided','hardware'].includes(copied.basis) ? 'VM blueprint' : 'New VM';
  area.innerHTML = '<div class="design-role-head"><div><h3 tabindex="-1"></h3><p class="design-role-summary"></p></div>' +
    '<button type="button" data-remove-machine>Remove role</button></div>' +
    '<nav class="design-role-tabs" aria-label="Role editor"><button type="button" data-role-pane="process">Process</button>' +
    '<button type="button" data-role-pane="settings">Role settings</button></nav>' +
    '<div class="design-process"><h4>Process steps</h4><div class="design-step-host"></div></div>' +
    '<div class="design-settings"><h4>Machine settings</h4>' +
    (existingTarget ? '<p>Choose the exact target VM when deploying this lab.</p>' :
      ['guided','hardware','recipe'].includes(copied.basis) ? '<p>Based on ' + esc(copied.name) + '.</p>' : '') + '<div class="form-grid">' +
    wf('key',existingTarget ? 'Role name in this lab' : 'Machine name',name,'text','required maxlength="15" pattern="[A-Za-z](?:[A-Za-z0-9\\-]{0,13}[A-Za-z0-9])?"') +
    labSelect('os','Operating system',systems,originalOS || '') + '</div><div class="design-source">' +
    (existingTarget ? wf('target_hint','Suggested VM name (optional)',initialSource.hint || '','text','maxlength="100"') : '') + '</div>' +
    (existingTarget ? '' : '<details class="design-hardware"><summary>Hardware and guest settings</summary><div class="design-resources">' +
    wf('cores','CPU cores',settings.cores ?? '','number','min="1" max="128" placeholder="Guided default"') +
    wf('memory_gb','Memory (GiB)',settings.memory != null ? settings.memory / 1024 : '','number','min="0.5" max="1024" step="any" placeholder="Guided default"') +
    wf('disk','System disk (GiB)',settings.disk ?? '','number','min="8" max="4096" placeholder="Guided default"') +
    '</div><div class="design-guest"></div></details>') + '<section class="design-dependencies"></section></div>';
  const el = n => area.querySelector('[name="' + n + '"]');
  const key = el('key'), os = el('os'), after = new Set();
  let source = {...initialSource}, editor;
  let selected = copied.prefill ? [{item:copied.prefill,ref:{id:copied.prefill.id},params:{}}] :
    (copied.tasks || []).map((item,original) => ({item,ref:{original},params:{...item.params}}));
  let stepCount = selected.length;
  let prerequisites = [];
  function updateHeading(requiredRoles = prerequisites) {
    prerequisites = requiredRoles;
    area.querySelector('h3').textContent = key.value || 'Unnamed role';
    const platform = os.value === 'linux' ? 'Linux' : os.value === 'windows' ? 'Windows' : linuxNames[os.value];
    area.querySelector('.design-role-summary').textContent = [intent,platform,prerequisites.length ? 'After ' + prerequisites.join(', ') : 'No prerequisites'].filter(Boolean).join(' · ');
  }
  function showPane(pane) {
    area.querySelector('.design-process').hidden = pane !== 'process';
    area.querySelector('.design-settings').hidden = pane !== 'settings';
    for (const tab of area.querySelectorAll('[data-role-pane]')) tab.setAttribute('aria-pressed',String(tab.dataset.rolePane === pane));
  }
  area.querySelector('.design-role-tabs').onclick = event => {
    const button = event.target.closest('[data-role-pane]');
    if (button) showPane(button.dataset.rolePane);
  };
  function sources() {
    if (existingTarget) return;
    const choices = [['iso','Installer ISO'],['template','Prepared template']];
    if (os.value === 'ubuntu') choices.unshift(['cloud','Ubuntu cloud image']);
    if (!choices.some(([kind]) => kind === source.kind)) source = {kind:choices[0][0]};
    const holder = area.querySelector('.design-source');
    holder.innerHTML = '<div class="form-grid">' + ws('source_kind','Install from',choices,source.kind) + '<div class="design-hint"></div></div><div class="design-edition"></div>';
    holder.hidden = !os.value;
    const candidates = designMedia(os.value,source.kind,options);
    const names = [...new Set(candidates.map(c => c.name))].sort();
    if (source.kind !== 'cloud') {
      const choices = [['','Choose at deployment'],...names.map(n => ['name:'+n,n]),['custom','Enter another name…']];
      const selectedHint = source.hint ? names.includes(source.hint) ? 'name:'+source.hint : 'custom' : '';
      holder.querySelector('.design-hint').innerHTML = ws('media_hint',source.kind === 'iso' ? 'Installer filename' : 'Template name',choices,selectedHint) +
        wf('custom_hint','Portable source name',selectedHint === 'custom' ? source.hint : '','text','maxlength="100"');
      const custom = el('custom_hint');
      const hintChanged = () => {
        custom.parentElement.hidden = el('media_hint').value !== 'custom';
        custom.disabled = custom.parentElement.hidden;
        custom.required = !custom.disabled;
        source.hint = custom.disabled ? el('media_hint').value.slice(5) : custom.value.trim();
      };
      hintChanged();
      el('media_hint').onchange = () => { delete source.edition; hintChanged(); editions(); };
      custom.oninput = () => { delete source.edition; hintChanged(); editions(); };
    }
    function editions() {
      const panel = holder.querySelector('.design-edition');
      panel.replaceChildren();
      if (os.value !== 'windows' || source.kind !== 'iso') return;
      const iso = candidates.find(c => c.name === source.hint);
      panel.innerHTML = wf('edition_label','Windows edition (optional)',source.edition || '','text','maxlength="100" placeholder="Choose at deployment"');
      const edition = el('edition_label');
      edition.oninput = () => { source.edition = edition.value.trim(); };
      if (iso) panel.append(buildButton('Read editions from ISO',async event => {
        const button = event.currentTarget;
        try {
          const info = await api('simple/editions',{node:iso.node,source:iso.source});
          if (!labActive(button)) return;
          const choices = [['','Choose at deployment'],...info.editions.map(e => [e.label,e.label])];
          if (!choices.some(([name]) => name === edition.value)) choices.push([edition.value,edition.value]);
          panel.innerHTML = ws('edition_label','Windows edition',choices,edition.value);
          el('edition_label').onchange = () => { source.edition = el('edition_label').value; };
        } catch (error) { if (labActive(button)) $('#lab-error').textContent = error.message; }
      }));
    }
    editions();
    el('source_kind').onchange = () => {
      source = {kind:el('source_kind').value}; sources();
      editor?.setOccupied([...(source.kind === 'iso' ? options?.iso_setup_letters || [] : []),...(settings.data_disks || []).map(d => d.letter).filter(Boolean)]);
    };
    el('disk').disabled = source.kind === 'template';
    el('disk').placeholder = el('disk').disabled ? 'From template' : 'Guided default';
  }

  function guestSettings() {
    if (existingTarget) return;
    const initial = os.value === originalOS ? settings : {};
    const target = area.querySelector('.design-guest');
    target.innerHTML = !os.value ? '' : os.value === 'windows' ? wc('rdp','Enable Remote Desktop',initial.rdp) :
      wf('username','Guest login name (optional)',initial.username || '','text','maxlength="32" placeholder="Guided default"') +
      wc('key_login','Require a workstation SSH key at deployment',initial.key_login) +
      (initial.purpose === 'template' ? '' : wc('password_login','Require a new guest password at deployment',initial.password_login));
  }
  function scripts() {
    const platform = os.value === 'windows' ? 'windows' : 'linux';
    stepCount = selected.length;
    editor = mountScriptSequence(area.querySelector('.design-step-host'),{
      catalog:os.value ? catalog.filter(t => t.platform === platform) : [],steps:selected,portable:true,catalogView:'list',
      occupied:[...(source.kind === 'iso' ? options?.iso_setup_letters || [] : []),...(settings.data_disks || []).map(d => d.letter).filter(Boolean)],
      onChange(steps) {
        if (steps.length !== stepCount) { stepCount = steps.length; onChange(); }
      }
    });
  }
  let previousOS = os.value;
  os.onchange = () => {
    selected = editor.steps();
    if ((os.value === 'windows') !== (previousOS === 'windows') && selected.length) {
      if (!window.confirm('Changing platform removes the scripts selected for this machine. Continue?')) {
        os.value = previousOS; return;
      }
      selected.splice(0);
    }
    previousOS = os.value;
    source = existingTarget || os.value === originalOS ? {...initialSource} : {};
    sources(); guestSettings(); scripts(); updateHeading(); onChange();
  };
  sources(); guestSettings(); scripts(); updateHeading(); showPane('process');
  return {uid,key,area,after,intent,showPane,updateHeading,get stepCount() { return stepCount; },definition() {
    const tasks = editor.values();
    const base = {};
    if (tasks.some(s => s.original !== undefined)) {
      if (copied.base_pack_id) Object.assign(base,{base_pack_id:copied.base_pack_id,base_role:copied.base_role});
      else if (copied.base_recipe_id) base.base_recipe_id = copied.base_recipe_id;
      else base.base_job_id = copied.base_job_id;
    }
    if (existingTarget) {
      const hint = el('target_hint').value.trim();
      return {key:key.value,os:os.value,source:hint ? {kind:'existing',hint} : {kind:'existing'},tasks,...base};
    }
    const portable = os.value === originalOS ? {...settings} : {};
    for (const name of ['cores','disk']) {
      const field = el(name);
      if (!field.disabled && field.value) portable[name] = Number(field.value);
      else delete portable[name];
    }
    if (el('memory_gb').value) portable.memory = Math.round(Number(el('memory_gb').value) * 1024);
    else delete portable.memory;
    for (const name of ['rdp','key_login','password_login','username']) {
      const field = el(name);
      if (!field) continue;
      const value = field.type === 'checkbox' ? field.checked : field.value.trim();
      if (value) portable[name] = value; else delete portable[name];
    }
    if (source.kind !== 'template') delete portable.clone_mode;
    if (source.kind === 'template') delete portable.purpose;
    if (source.kind !== 'iso') delete portable.ubuntu_install_source;
    const draft = {key:key.value,os:os.value,source:Object.fromEntries(Object.entries(source).filter(([,v]) => v)),
      settings:portable,tasks,...base};
    return draft;
  }};
}
