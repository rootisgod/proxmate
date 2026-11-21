/* Authoring stores portable choices. Destination bindings and credentials are collected by the deployment form. */
async function openLabDesigner() {
  const loading = labStep('Create lab', '<p>Loading machines and scripts…</p>', 'Retry', '', openLabDesigner);
  $('#lab-next').disabled = true;
  try {
    const [catalog, options] = await Promise.all([
      api('manage/tasks'), api('simple/options').catch(() => null)
    ]);
    if (labActive(loading)) designLab(catalog,options);
  } catch (error) {
    if (labActive(loading)) {
      $('#lab-error').textContent = error.message;
      $('#lab-next').disabled = false;
    }
  }
}

function designLab(catalog, options) {
  const machines = [], guests = state.resources.filter(g => g.type === 'qemu');
  let sequence = 0, copying = false;
  const fields = labStep('Create lab',
    wf('lab_name','Lab name','','text','required maxlength="100" placeholder="Development lab"') +
    '<div id="design-machines"></div><div class="design-add"><button type="button" id="design-new">Add new machine</button>' +
    (guests.length ? '<div>' + ws('existing_vm','Existing VM',[['','Choose a machine'],...guests.map((g,i) =>
      [i,(g.name || 'VM') + ' · ' + g.node + ' / ' + g.vmid])],'') +
      '<button type="button" id="design-copy" disabled>Copy VM settings</button></div>' : '') +
    '<button type="button" id="design-existing">Run scripts on existing VM</button></div>' +
    (options ? '' : '<p>Live media choices are unavailable. Enter a media name or choose it at deployment.</p>'),
    'Save lab', 'Saving lab pack…', async area => {
      if (copying) throw Error('Wait for the selected VM settings to load.');
      if (!machines.length) throw Error('Add a machine to this lab.');
      const definitions = machines.map(m => ({...m.definition(), after:machines.filter(other => m.after.has(other.uid)).map(other => other.key.value)}));
      const pack = await api('simple/labs/packs',{name:area.querySelector('[name=lab_name]').value.trim(),machines:definitions});
      if (!labActive(area)) return;
      loadLabList('packs');
      labStep('Lab saved', '<p><strong>' + esc(pack.name) + '</strong> is in Saved lab packs. Set up the lab when you are ready to choose its destination.</p>' +
        '<p><a href="/api/simple/labs/packs/' + encodeURIComponent(pack.id) + '/export" download>Download ZIP</a></p>',
        'Set up lab', '', () => openSavedPack(pack.id));
    });
  function dependencies() {
    for (const machine of machines) {
      const panel = machine.area.querySelector('.design-dependencies');
      panel.hidden = machines.length < 2;
      panel.innerHTML = '<summary>Start after other machines</summary>' + machines.filter(m => m !== machine).map(other =>
        '<label class="check"><input type="checkbox" data-dependency="' + other.uid + '"' +
        (machine.after.has(other.uid) ? ' checked' : '') + '>' + esc(other.key.value || 'Unnamed machine') + '</label>').join('');
      panel.onchange = e => {
        const uid = Number(e.target.dataset.dependency);
        if (e.target.checked) machine.after.add(uid); else machine.after.delete(uid);
      };
    }
  }
  function add(source) {
    if (machines.length >= 20) throw Error('A lab can include up to 20 machines.');
    const used = new Set(machines.map(m => m.key.value.toLowerCase()));
    const [name] = suggestedLabNames([source.name || 'vm'],15,used);
    const uid = ++sequence;
    const machine = designMachine(uid,name,source,catalog,options);
    machines.push(machine);
    $('#design-machines').append(machine.area);
    machine.key.oninput = dependencies;
    machine.area.querySelector('[data-remove-machine]').onclick = () => {
      machines.splice(machines.indexOf(machine),1);
      for (const other of machines) other.after.delete(uid);
      machine.area.remove(); dependencies();
      $('#design-new').focus();
    };
    dependencies(); machine.key.focus();
  }
  $('#design-new').onclick = () => {
    try { add({os:'ubuntu'}); $('#lab-error').textContent = ''; }
    catch (error) { $('#lab-error').textContent = error.message; }
  };
  const existing = fields.querySelector('[name=existing_vm]'), copy = fields.querySelector('#design-copy');
  const use = fields.querySelector('#design-existing');
  if (existing) existing.onchange = () => {
    copy.disabled = !existing.value || copying;
    use.disabled = copying || (!!existing.value && !!guests[Number(existing.value)]?.template);
  };
  async function addFromVM(existingTarget) {
    const guest = guests[Number(existing.value)];
    copying = copy.disabled = existing.disabled = use.disabled = true;
    $('#lab-status').textContent = 'Reading ' + (guest.name || 'VM ' + guest.vmid) + '…';
    $('#lab-error').textContent = '';
    try {
      const source = await api('simple/labs/machine-source',{node:guest.node,vmid:guest.vmid});
      if (labActive(fields)) add(existingTarget ? {name:source.name,os:source.os === 'windows' ? 'windows' : source.os ? 'linux' : null,
        source:{kind:'existing',hint:source.name}} : source);
    } catch (error) { if (labActive(fields)) $('#lab-error').textContent = error.message; }
    finally {
      copying = false;
      if (labActive(fields)) { existing.disabled = false; existing.onchange(); $('#lab-status').textContent = ''; }
    }
  }
  if (copy) copy.onclick = () => addFromVM(false);
  use.onclick = () => {
    if (existing?.value) return addFromVM(true);
    try { add({name:'target',source:{kind:'existing'}}); }
    catch (error) { $('#lab-error').textContent = error.message; }
  };
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

function designMachine(uid, name, copied, catalog, options) {
  const area = document.createElement('fieldset');
  area.className = 'design-machine';
  const settings = copied.settings || {}, originalOS = copied.os;
  const initialSource = copied.source || {};
  const existingTarget = initialSource.kind === 'existing';
  const systems = [['','Choose an operating system'],['windows','Windows'],...(existingTarget ? [['linux','Linux']] : Object.entries(linuxNames))];
  area.innerHTML = '<legend>' + (existingTarget ? 'Run scripts on an existing VM' : 'Create a new machine') + '</legend><div class="design-machine-head">' +
    (existingTarget ? '<small>Choose the target VM at deployment.</small>' : '') +
    (copied.basis ? '<small>' + (copied.basis === 'guided' ? 'Guided build settings and scripts from ' : 'Hardware settings from ') + esc(copied.name) + '</small>' : '') +
    '<button type="button" data-remove-machine>Remove machine</button></div><div class="form-grid">' +
    wf('key',existingTarget ? 'Role name in this lab' : 'Machine name',name,'text','required maxlength="15" pattern="[A-Za-z](?:[A-Za-z0-9\\-]{0,13}[A-Za-z0-9])?"') +
    labSelect('os','Operating system',systems,originalOS || '') + '</div><div class="design-source">' +
    (existingTarget ? wf('target_hint','Suggested VM name (optional)',initialSource.hint || '','text','maxlength="100"') : '') + '</div>' +
    (existingTarget ? '' : '<details class="design-hardware"><summary>Hardware and guest settings</summary><div class="design-resources">' +
    wf('cores','vCPUs',settings.cores ?? '','number','min="1" max="128" placeholder="Guided default"') +
    wf('memory','Memory (MiB)',settings.memory ?? '','number','min="512" max="1048576" placeholder="Guided default"') +
    wf('disk','System disk (GiB)',settings.disk ?? '','number','min="8" max="4096" placeholder="Guided default"') +
    '</div><div class="design-guest"></div></details>') + '<section class="design-scripts"><h3>Setup scripts</h3>' +
    '<ol class="design-task-list"></ol><div class="design-script-picker"></div></section><details class="design-dependencies"></details>';
  const el = n => area.querySelector('[name="' + n + '"]');
  const key = el('key'), os = el('os');
  let source = {...initialSource};
  const selected = (copied.tasks || []).map((item,original) => ({item,ref:{original},params:{...item.params}}));

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
        button.disabled = true;
        try {
          const info = await api('simple/editions',{node:iso.node,source:iso.source});
          if (!labActive(button)) return;
          const choices = [['','Choose at deployment'],...info.editions.map(e => [e.label,e.label])];
          if (!choices.some(([name]) => name === edition.value)) choices.push([edition.value,edition.value]);
          panel.innerHTML = ws('edition_label','Windows edition',choices,edition.value);
          el('edition_label').onchange = () => { source.edition = el('edition_label').value; };
        } catch (error) { if (labActive(button)) $('#lab-error').textContent = error.message; }
        finally { button.disabled = false; }
      }));
    }
    editions();
    el('source_kind').onchange = () => { source = {kind:el('source_kind').value}; sources(); };
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
  function collectTasks() {
    for (const control of area.querySelectorAll('[data-task-input]')) {
      selected[Number(control.dataset.index)].params[control.dataset.taskInput] = paramValue(control);
    }
  }
  function scripts() {
    const platform = os.value === 'windows' ? 'windows' : 'linux';
    const available = os.value ? catalog.filter(t => t.platform === platform) : [];
    const picker = area.querySelector('.design-script-picker');
    picker.innerHTML = ws('library_script','Library script',[['',available.length ? 'Choose a script' : 'No scripts for this operating system'],...available.map(t => [t.id,t.name])],'') +
      '<button type="button" data-add-script disabled>Add script</button>';
    const choose = el('library_script'), add = picker.querySelector('button');
    choose.onchange = () => { add.disabled = !choose.value; };
    add.onclick = () => {
      collectTasks();
      const item = available.find(t => t.id === choose.value);
      selected.push({item, ref:{id:item.id}, params:{}});
      scripts();
    };
    const list = area.querySelector('.design-task-list');
    list.innerHTML = selected.map(({item,params},i) => {
      const inputs = Object.entries(item.parameters || {}).map(([name,label]) => {
        const type = scriptInputValue(item.input_types,name);
        if (type?.type === 'password') return '<p>' + esc(label) + ' · Collected at deployment</p>';
        return '<label>' + esc(label) + paramControl(type,scriptInputValue(params,name) ?? scriptInputValue(item.defaults,name),
          'data-index="' + i + '" data-task-input="' + esc(name) + '"') + '</label>';
      }).join('');
      return '<li><strong>' + esc(item.name) + '</strong>' + (item.body ? '<details><summary>View script</summary><pre>' + esc(item.body) + '</pre></details>' : '') +
        inputs + '<div class="row-actions">' + [['up','Move up',i === 0],['down','Move down',i === selected.length-1],['remove','Remove',false]].map(
          ([act,label,disabled]) => '<button type="button" data-task-action="' + act + '" data-index="' + i + '" aria-label="' +
          label + ' ' + esc(item.name) + '"' + (disabled ? ' disabled' : '') + '>' + label + '</button>').join('') + '</div></li>';
    }).join('');
    list.onclick = e => {
      const action = e.target.closest('[data-task-action]');
      if (!action) return;
      collectTasks();
      const i = Number(action.dataset.index);
      if (action.dataset.taskAction === 'remove') selected.splice(i,1);
      else {
        const to = action.dataset.taskAction === 'up' ? i-1 : i+1;
        [selected[i],selected[to]] = [selected[to],selected[i]];
      }
      scripts();
    };
  }
  let previousOS = os.value;
  os.onchange = () => {
    collectTasks();
    if ((os.value === 'windows') !== (previousOS === 'windows') && selected.length) {
      if (!window.confirm('Changing platform removes the scripts selected for this machine. Continue?')) {
        os.value = previousOS; return;
      }
      selected.splice(0);
    }
    previousOS = os.value;
    source = existingTarget || os.value === originalOS ? {...initialSource} : {};
    sources(); guestSettings(); scripts();
  };
  sources(); guestSettings(); scripts();
  return {uid,key,area,after:new Set(),definition() {
    collectTasks();
    const tasks = selected.map(({ref,params}) => ({...ref,params}));
    if (existingTarget) {
      if (!tasks.length) throw Error('Choose at least one script for ' + key.value + '.');
      const hint = el('target_hint').value.trim();
      return {key:key.value,os:os.value,source:hint ? {kind:'existing',hint} : {kind:'existing'},tasks};
    }
    const portable = os.value === originalOS ? {...settings} : {};
    for (const name of ['cores','memory','disk']) {
      const field = el(name);
      if (!field.disabled && field.value) portable[name] = Number(field.value);
      else delete portable[name];
    }
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
      settings:portable,tasks};
    if (selected.some(s => s.ref.original !== undefined)) draft.base_job_id = copied.base_job_id;
    return draft;
  }};
}
