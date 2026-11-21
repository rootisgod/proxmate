/* A lab step owns its fields, including any fresh credentials, until replaced or closed. */
function labActive(fields) {
  return fields.isConnected && $('#lab-dialog').open;
}

function labStep(title, html, next, progress, submit, validate) {
  const dialog = $('#lab-dialog'), form = $('#lab-form');
  const fields = document.createElement('div');
  fields.innerHTML = html;
  $('#lab-fields').replaceChildren(fields);
  form.noValidate = !!validate;
  $('#lab-title').textContent = title;
  $('#lab-next').textContent = next;
  $('#lab-next').disabled = false;
  $('#lab-status').textContent = '';
  $('#lab-error').textContent = '';
  form.onsubmit = async event => {
    event.preventDefault();
    if ($('#lab-next').disabled || (validate && !validate(fields))) return;
    $('#lab-next').disabled = true;
    fields.inert = true;
    $('#lab-error').textContent = '';
    $('#lab-status').textContent = progress;
    try { await submit(fields); }
    catch (error) { if (labActive(fields)) $('#lab-error').textContent = error.message; }
    finally {
      if (labActive(fields)) {
        fields.inert = false;
        $('#lab-next').disabled = false;
        $('#lab-status').textContent = '';
      }
    }
  };
  if (!dialog.open) dialog.showModal();
  $('#lab-title').focus();
  return fields;
}

$('#lab-close').onclick = () => $('#lab-dialog').close();
$('#lab-dialog').addEventListener('close', () => {
  if ($('#lab-dialog').open) return; // A native close event can arrive after another pack has opened.
  $('#lab-form').onsubmit = null;
  $('#lab-fields').replaceChildren();
  $('#lab-error').textContent = '';
  $('#lab-status').textContent = '';
});

async function selectLabBuilds() {
  const fields = labStep('Choose saved builds', '<p>Loading saved builds…</p>', 'Continue', '', () => {});
  $('#lab-next').disabled = true;
  try {
    const recipes = await api('simple/recipes');
    if (!labActive(fields)) return;
    if (!recipes.length) {
      fields.textContent = 'Save a guided build from its review or job log to include it in a lab pack.';
      return;
    }
    labStep('Choose saved builds', '<p>Select the builds to include in this lab pack.</p>' +
      recipes.map((r, i) => wc('recipe-' + i, esc(r.name))).join(''), 'Continue', '', area => {
        const sources = recipes.flatMap((r, i) => area.querySelector('[name="recipe-' + i + '"]').checked ?
          [{name:r.name, source:{recipe_id:r.id}}] : []);
        if (!sources.length) throw Error('Choose at least one saved build.');
        composeLab(sources);
      });
  } catch (error) { if (labActive(fields)) $('#lab-error').textContent = error.message; }
}

function suggestedLabNames(names, limit, used = new Set()) {
  return names.map(name => {
    const stem = String(name || 'vm').replace(/[^A-Za-z0-9-]+/g, '-').replace(/^[^A-Za-z]+/, '') || 'vm';
    const suffix = stem.match(/-\d+$/)?.[0] || '';
    let candidate = stem.slice(0, limit).replace(/-+$/, '');
    if (stem.length > limit && suffix.length && suffix.length < limit) {
      candidate = stem.slice(0,limit - suffix.length).replace(/-+$/, '') + suffix;
    }
    for (let i = 2; used.has(candidate.toLowerCase()); i++) {
      candidate = stem.slice(0, limit - 1 - String(i).length).replace(/-+$/, '') + '-' + i;
    }
    used.add(candidate.toLowerCase());
    return candidate;
  });
}

function reviewedLabExport(plan) {
  return buildButton('Export lab', () => composeLab(plan.machines.map(machine => ({
    name:machine.name, source:{plan_id:plan.id, machine_name:machine.name}
  }))));
}

function composeLab(builds) {
  const keys = suggestedLabNames(builds.map(b => b.name?.toLowerCase()),15);
  const rows = builds.map((b, i) => '<fieldset><legend>' + esc(b.name || keys[i]) + '</legend>' +
    wf('key', 'Machine key', keys[i], 'text', 'required maxlength="15" pattern="[A-Za-z](?:[A-Za-z0-9\\-]{0,13}[A-Za-z0-9])?"') +
    (builds.length > 1 ? '<details><summary>Start after other machines</summary>' +
      builds.map((dep, j) => i === j ? '' : '<label class="check"><input type="checkbox" data-after="' + j + '">' +
        esc(dep.name || keys[j]) + '</label>').join('') + '</details>' : '') + '</fieldset>').join('');
  labStep('Export lab', wf('name', 'Lab name', builds.length === 1 ? builds[0].name : '', 'text', 'required maxlength="100"') +
    rows + '<p>The ZIP includes the setup scripts exactly as reviewed, including any values written into their source. Check the scripts before sharing. Whoever imports the ZIP supplies local media and guest credentials.</p>',
    'Download ZIP', 'Preparing ZIP…', async fields => {
      const name = fields.querySelector('[name=name]').value.trim();
      const machines = [...fields.querySelectorAll('fieldset')].map((row, i) => ({
        key:row.querySelector('[name=key]').value, ...builds[i].source,
        after:[...row.querySelectorAll('[data-after]:checked')].map(input => fields.querySelectorAll('[name=key]')[Number(input.dataset.after)].value)
      }));
      const zip = await api('simple/labs/export', {name, machines}, {}, 'blob');
      if (!labActive(fields)) return;
      const link = document.createElement('a');
      link.href = URL.createObjectURL(zip);
      link.download = (name.replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'lab') + '.zip';
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      $('#lab-dialog').close();
    });
}

function importLab() {
  labStep('Import lab pack', '<label>Lab pack ZIP<input type="file" accept=".zip,application/zip" required></label>' +
    '<p><a href="/api/simple/labs/example" download>Download example pack</a> · <a href="/static/lab-packs.md" target="_blank" rel="noopener">Lab pack format</a></p>',
    'Inspect pack', 'Reading lab pack…', async fields => {
      const file = fields.querySelector('input').files[0];
      if (!file) throw Error('Choose a lab pack ZIP.');
      if (file.size > 2 * 1024 * 1024) throw Error('Choose a ZIP of 2 MiB or smaller.');
      const archive = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = () => reject(Error('Could not read this ZIP. Choose the file again.'));
        reader.readAsDataURL(file);
      });
      if (!labActive(fields)) return;
      const pack = await api('simple/labs/import', {archive});
      if (!labActive(fields)) return;
      loadLabList('packs');
      await prepareLabSetup(pack,fields);
    });
}

function labTasks(pack, machine, reviewedTasks = []) {
  if (!machine.tasks?.length) return '<p>No setup tasks.</p>';
  const tasks = machine.tasks.map((task,i) => {
    const reviewed = reviewedTasks[i];
    const source = task.file ?
      '<details><summary>' + esc(task.file) + '</summary><pre>' + esc(pack.scripts[task.file]) + '</pre></details>' :
      esc(task.run) + ' · version ' + esc(task.version);
    const params = Object.entries({...task.with,...reviewed?.params}).map(([key, value]) => '<dt>' + esc(key) + '</dt><dd>' +
      (typeof value === 'object' && value?.prompt ? (reviewed ? 'Supplied for this run' : 'Prompt at deployment') : esc(value)) + '</dd>').join('');
    return '<li>' + (reviewed ? '<strong>' + esc(reviewed.name) + '</strong><br>' : '') + source +
      (params ? '<dl class="lab-params">' + params + '</dl>' : '') + '</li>';
  });
  return '<details class="lab-tasks"' + (reviewedTasks.length ? ' open' : '') + '><summary>Setup tasks (' + tasks.length + ')</summary><ol>' + tasks.join('') + '</ol></details>';
}

function labSelect(name, label, choices, value) {
  return '<label>' + label + '<select name="' + name + '" required>' + choices.map(([id, text]) =>
    '<option value="' + esc(id) + '"' + (String(id) === String(value) ? ' selected' : '') + '>' + esc(text) + '</option>'
  ).join('') + '</select></label>';
}

function matchingSource(choices, hint) {
  if (!hint) return '';
  const matches = choices.filter(item => item.hint === hint);
  return matches.length === 1 ? matches[0].id : '';
}

function labPlacement(row, machine, options) {
  const node = row.querySelector('[name=node]').value;
  const dest = options.destinations.find(d => d.node === node);
  const slot = row.querySelector('.lab-placement');
  const pairs = values => values.map(value => [value, value]);
  let html = labSelect('storage', 'VM disk storage', pairs(dest.storage_choices), dest.storage) +
    labSelect('iso_storage', 'Setup media storage', pairs(dest.iso_storages), dest.iso_storage) +
    labSelect('bridge', 'Network bridge', pairs(dest.bridges), dest.bridge);
  const source = machine.source, windows = machine.os === 'windows';
  if (source.kind === 'template') {
    const templates = windows ? windowsTemplates() : options[machine.os + '_templates'] || [];
    const choices = templates.filter(t => t.node === node).map(t => ({id:t.vmid, hint:t.name, label:t.name + ' · VM ' + t.vmid}));
    html += labSelect('template_id', 'Local template', [['',choices.length ? 'Choose a template' : 'No suitable templates on this node'],
      ...choices.map(t => [t.id,t.label])], matchingSource(choices, source.hint));
    if (windows) html += '<label class="check wide"><input type="checkbox" name="generalized" required>I verified that this Windows template was generalized with Sysprep and has the QEMU Guest Agent.</label>';
  }
  if (source.kind === 'iso') {
    const media = windows ? [...dest.isos,...options.local_isos] : dest[machine.os + '_isos'] || [];
    const choices = media.map(item => ({id:item.id, hint:item.label.replace(/ \(ISO library\)$/, ''),
      label:item.id.startsWith('library:') ? item.label : item.label + ' · ' + item.id.split(':')[0]}));
    html += labSelect('source', 'Local installer ISO', [['',choices.length ? 'Choose an ISO' : 'No suitable ISOs on this node'],
      ...choices.map(item => [item.id,item.label])], matchingSource(choices, source.hint));
    if (windows) html += '<div class="lab-editions"></div>' +
      labSelect('agent_iso', 'Guest tools ISO', dest.driver_isos.length ? pairs(dest.driver_isos) : [['', 'VirtIO guest tools required']], dest.agent_iso);
  }
  if (windows) html += wf('management_cidr', 'WinRM management subnet', dest.management_cidr, 'text', 'required');
  slot.innerHTML = html;
  const bridge = slot.querySelector('[name=bridge]'), subnet = slot.querySelector('[name=management_cidr]');
  if (subnet) bridge.onchange = () => { subnet.value = dest.bridge_subnets?.[bridge.value] || ''; };
  if (windows && source.kind === 'iso') {
    const iso = slot.querySelector('[name=source]');
    iso.onchange = () => loadLabEditions(slot, node, iso.value, source.edition);
    iso.onchange();
  }
}

async function loadLabEditions(slot, node, source, hint) {
  const holder = slot.querySelector('.lab-editions'), field = document.createElement('div');
  holder.replaceChildren(field);
  field.innerHTML = labSelect('edition', 'Windows edition', [['',source ? 'Reading editions…' : 'Choose an ISO first']], '');
  if (!source) return;
  try {
    const result = await api('simple/editions', {node,source});
    if (!labActive(field)) return;
    const editions = hint ? result.editions.filter(e => e.label === hint) : result.editions;
    const matches = hint ? editions : [];
    field.innerHTML = labSelect('edition','Windows edition',
      [['',editions.length ? 'Choose an edition' : 'Choose an ISO with the required edition'],...editions.map(e => [e.index,e.label])],matches.length === 1 ? matches[0].index : '');
    field.querySelector('select').dataset.identity = result.identity;
  } catch(error) {
    if (!labActive(field)) return;
    const message = document.createElement('p');
    message.setAttribute('role', 'alert');
    message.textContent = error.message;
    field.append(message,buildButton('Retry edition check', () => loadLabEditions(slot,node,source,hint)));
  }
}
function labAccess(machine) {
  const password = (name, label, required) => wf(name, label, '', 'password',
    (required ? 'required ' : '') + 'minlength="12" maxlength="128" autocomplete="new-password"');
  const windows = machine.os === 'windows';
  const settings = machine.settings || {};
  const keyLabel = settings.key_login ? '' : ' (optional)';
  const fields = windows ? password('password', 'New Administrator password', true) :
    (settings.key_login ? '<p class="wide">Supply a workstation public key or a GitHub account for SSH access.</p>' : '') +
    wf('ssh_public_key', 'Workstation SSH public key' + keyLabel, '', 'text', 'maxlength="8192" autocomplete="off"') +
    wf('github_username', 'GitHub account for SSH keys' + keyLabel, '', 'text', 'maxlength="39" autocomplete="off"') +
    (settings.purpose === 'template' ? '' : password('linux_password', settings.password_login ? 'New guest password' : 'New guest password (optional)', settings.password_login));
  return '<div class="form-grid lab-access">' + fields + labPrompts(machine) + '</div>';
}

function labPrompts(machine) {
  return machine.secret_inputs.map(input =>
    '<label>' + esc(input.label || input.key) + '<input type="password" data-prompt="' + esc(input.key) +
    '" autocomplete="new-password" required></label>').join('');
}

function afterLabel(machine) {
  return machine.after?.length ? 'Starts after ' + esc(machine.after.join(', ')) : 'Ready to start independently';
}

async function prepareLabSetup(pack, fields) {
  const existing = pack.machines.some(m => m.source.kind === 'existing');
  const creates = pack.machines.some(m => m.source.kind !== 'existing');
  const [options,connections,credentials,resources] = await Promise.all([
    creates ? api('simple/options') : {destinations:[]},
    existing ? api('manage/connections') : [], existing ? api('manage/credentials') : [], existing ? api('simple/resources') : []
  ]);
  if (labActive(fields)) mapLab(pack,{...options,connections,credentials,existingGuests:resources.filter(g => g.type === 'qemu' && !g.template)});
}

function bindExistingLabTarget(row, machine, options, previous) {
  const destinations = options.existingGuests;
  const select = row.querySelector('[name=target]');
  for (const guest of destinations) {
    const id = guest.node + ':' + guest.vmid, connection = options.connections.find(c => c.id === id);
    const reason = guest.status !== 'running' ? (guest.status || 'status unavailable') :
      connection?.verified_at && connection.platform !== machine.os ? 'verified ' + connection.platform + ' guest' : '';
    const option = new Option((guest.name || 'VM') + ' · ' + guest.node + ' / ' + guest.vmid + (reason ? ' · ' + reason : ''), id);
    option.disabled = !!reason;
    select.add(option);
    if (!reason && previous && id === previous.node + ':' + previous.vmid) select.value = id;
  }
  if (![...select.options].some(o => o.value && !o.disabled)) {
    select.options[0].textContent = 'No suitable running VMs';
    const note = document.createElement('p');
    note.textContent = 'Start a compatible ' + (machine.os === 'windows' ? 'Windows' : 'Linux') + ' VM, then reopen this pack to refresh the choices.';
    select.parentElement.after(note);
  }
  const login = row.querySelector('.lab-target-login');
  select.onchange = () => {
    login.replaceChildren();
    const guest = destinations.find(g => g.node + ':' + g.vmid === select.value);
    if (!guest) return;
    const connection = options.connections.find(c => c.id === select.value);
    if (connection) {
      login.textContent = 'Use the saved ' + (connection.platform === 'windows' ? 'Windows' : 'Linux') + ' login for ' + guest.name + '.';
      return;
    }
    const choices = options.credentials.filter(c => c.platform === machine.os);
    login.innerHTML = '<p>This VM needs a guest login.</p>' + ws('credential_id','Login for this VM',[['','Enter guest credentials'],
      ...choices.map(c => [c.id,c.name + ' · ' + c.username])],choices.length === 1 ? choices[0].id : '') + '<div class="form-grid lab-new-login"></div>';
    const chooser = login.querySelector('select'), entry = login.querySelector('.lab-new-login');
    chooser.onchange = () => {
      if (chooser.value) { entry.replaceChildren(); return; }
      const windows = machine.os === 'windows';
      entry.innerHTML = wf('username','Guest username',windows ? 'Administrator' : '','text','required autocomplete="off"') +
        wf('password','Guest password','','password', (windows ? 'required ' : '') + 'autocomplete="new-password"') +
        (windows ? '<label>Issuing CA certificate (optional)<input type="file" name="ca_pem"></label>' :
          '<label>SSH private key file (optional)<input type="file" name="private_key"></label>' +
          wf('become_password','Sudo password (optional)','','password','autocomplete="new-password"'));
    };
    chooser.onchange();
  };
  select.onchange();
}

async function existingLabBinding(row, machine, options) {
  const selected = row.querySelector('[name=target]').selectedOptions[0];
  const guest = !selected?.disabled && options.existingGuests.find(g => g.node + ':' + g.vmid === selected?.value);
  if (!guest) throw Error('Choose an existing VM for ' + machine.key + '.');
  const secrets = {}, chooser = row.querySelector('[name=credential_id]');
  if (chooser?.value) secrets.credential_id = chooser.value;
  else if (chooser) {
    const credential = {platform:machine.os};
    for (const input of row.querySelectorAll('.lab-new-login [name]')) {
      if (input.type === 'file') {
        if (input.files[0]) credential[input.name] = await input.files[0].text();
      } else if (input.value) credential[input.name] = input.value;
    }
    if (!credential.password && !credential.private_key) throw Error('Supply a guest password or SSH private key for ' + machine.key + '.');
    secrets.credential = credential;
  }
  return {binding:{node:guest.node,vmid:guest.vmid},secrets};
}

function mapLab(pack, options, previous = Object.create(null)) {
  const nodes = options.destinations.map(d => [d.node,d.node]);
  if (!nodes.length && pack.machines.some(m => m.source.kind !== 'existing')) throw Error('Connect a node with VM storage, ISO storage and a bridge before reviewing this lab.');
  const used = new Set([...state.resources.map(g => g.name), ...jobs.filter(j => ['queued','running','waiting'].includes(j.status))
    .map(j => j.target?.name)].filter(Boolean).map(name => name.toLowerCase()));
  const names = suggestedLabNames(pack.machines.map(m => m.key),12,used);
  const cards = pack.machines.map((machine, i) => {
    const source = machine.source, saved = previous[machine.key];
    if (source.kind === 'existing') return '<fieldset data-machine-index="' + i + '"><legend>' + esc(machine.key) + ' · Run scripts on an existing VM</legend>' +
      '<p>' + esc(machine.os === 'windows' ? 'Windows' : 'Linux') + (source.hint ? ' · Suggested VM ' + esc(source.hint) : '') + '<br>' +
      afterLabel(machine) + '</p>' + labTasks(pack,machine) + labSelect('target','Existing VM to change',[['','Choose a VM on this server']],'') +
      '<div class="lab-target-login"></div><div class="form-grid">' + labPrompts(machine) + '</div></fieldset>';
    const sourceLabel = {cloud:'Cloud image',iso:'Installer ISO',template:'Template'}[source.kind];
    const label = machine.os === 'windows' ? 'Windows' : linuxNames[machine.os];
    const settings = machine.settings || {};
    const hardware = [settings.cores && esc(settings.cores) + ' vCPU', settings.memory && esc(settings.memory) + ' MiB RAM',
      settings.disk && esc(settings.disk) + ' GiB disk'].filter(Boolean);
    return '<fieldset data-machine-index="' + i + '"><legend>' + esc(machine.key) + '</legend>' +
      '<p>' + esc(label) + ' · ' + esc(sourceLabel) + (source.hint ? ' · ' + esc(source.hint) : '') +
      (source.edition ? ' · ' + esc(source.edition) : '') + (hardware.length ? '<br>' + hardware.join(' · ') : '') +
      '<br>' + afterLabel(machine) + '</p>' + labTasks(pack,machine) +
      '<div class="form-grid">' + wf('name','New machine name',saved?.name || names[i],'text','required maxlength="12" pattern="[A-Za-z](?:[A-Za-z0-9\\-]{0,10}[A-Za-z0-9])?"') +
      labSelect('node','Proxmox node',nodes,saved?.node || nodes[0][0]) + '</div>' +
      '<div class="form-grid lab-placement"></div>' + labAccess(machine) + '</fieldset>';
  });
  const pickRole = (area,i) => {
    for (const row of area.querySelectorAll('[data-machine-index]')) row.hidden = Number(row.dataset.machineIndex) !== i;
    for (const button of area.querySelectorAll('[data-map-role]')) button.setAttribute('aria-pressed',String(Number(button.dataset.mapRole) === i));
  };
  const switcher = cards.length > 1 ? '<nav class="lab-mapping-roles" aria-label="Deployment roles">' +
    pack.machines.map((m,i) => '<button type="button" data-map-role="' + i + '"><strong>' + esc(m.key) + '</strong><small>' +
      (m.source.kind === 'existing' ? 'Run on existing VM' : m.settings?.purpose === 'template' ? 'Create template' : 'Create VM') + '</small></button>').join('') + '</nav>' : '';
  const fields = labStep('Set up ' + pack.name,'<div class="lab-mapping">' + switcher + '<div>' + cards.join('') + '</div></div>',
    'Review lab', 'Checking machines and local sources…', async area => {
    const bindings = Object.create(null), secrets = Object.create(null);
    for (const row of area.querySelectorAll('[data-machine-index]')) {
      const machine = pack.machines[Number(row.dataset.machineIndex)];
      const prompts = [...row.querySelectorAll('[data-prompt]')];
      const inputs = Object.fromEntries(prompts.map(input => [input.dataset.prompt,input.value]));
      if (machine.source.kind === 'existing') {
        try {
          const existing = await existingLabBinding(row,machine,options);
          bindings[machine.key] = existing.binding;
          secrets[machine.key] = prompts.length ? {...existing.secrets,inputs} : existing.secrets;
        } catch (error) { pickRole(area,Number(row.dataset.machineIndex)); throw error; }
        continue;
      }
      const binding = Object.fromEntries([...row.querySelectorAll('[name]')].filter(input => !input.closest('.lab-access'))
        .map(input => [input.name,input.type === 'checkbox' ? input.checked : input.value]));
      const edition = row.querySelector('[name=edition]');
      if (edition) binding.edition_identity = edition.dataset.identity;
      bindings[machine.key] = binding;
      const login = Object.fromEntries([...row.querySelectorAll('.lab-access [name]')].filter(input => input.value)
        .map(input => [input.name,input.value]));
      if (prompts.length) login.inputs = inputs;
      if (machine.settings?.key_login && !login.ssh_public_key?.trim() && !login.github_username?.trim()) {
        pickRole(area,Number(row.dataset.machineIndex));
        throw Error('Supply a workstation public key or GitHub account for ' + machine.key + '.');
      }
      secrets[machine.key] = login;
    }
    if (!labActive(area)) return;
    const review = await api('simple/labs/plan',{pack_id:pack.id,bindings,secrets});
    if (labActive(area)) confirmLab(pack,options,bindings,review);
  },area => {
    for (const row of area.querySelectorAll('[data-machine-index]')) {
      const input = row.querySelector('input:invalid, select:invalid, textarea:invalid');
      if (input) { pickRole(area,Number(row.dataset.machineIndex)); input.reportValidity(); return false; }
    }
    return true;
  });
  for (const button of fields.querySelectorAll('[data-map-role]')) button.onclick = () => pickRole(fields,Number(button.dataset.mapRole));
  pickRole(fields,0);
  fields.querySelectorAll('[data-machine-index]').forEach(row => {
    const machine = pack.machines[Number(row.dataset.machineIndex)], node = row.querySelector('[name=node]');
    if (machine.source.kind === 'existing') {
      bindExistingLabTarget(row,machine,options,previous[machine.key]);
      return;
    }
    node.onchange = () => labPlacement(row,machine,options);
    node.onchange();
    const saved = previous[machine.key];
    if (!saved) return;
    for (const input of row.querySelectorAll('.lab-placement [name]')) {
      if (input.type === 'checkbox' || input.name === 'edition' || !(input.name in saved)) continue;
      if (input.value !== saved[input.name]) {
        input.value = saved[input.name];
        if (input.name === 'source') input.dispatchEvent(new Event('change'));
      }
    }
  });
}

function confirmLab(pack, options, bindings, review) {
  const cards = review.machines.map(machine => {
    const plan = machine.plan;
    const imported = pack.machines.find(item => item.key === machine.key);
    if (machine.mode === 'existing') {
      const target = plan.targets[0];
      return '<fieldset><legend>Run scripts on existing VM · ' + esc(target.name) + '</legend><p>VM ' + esc(target.vmid) +
        ' on ' + esc(target.node) + ' · ' + esc(target.platform) + ' · ' + esc(target.guest_host) + (target.port ? ' / port ' + esc(target.port) : '') +
        ' · ' + esc(target.credential_name) + '</p><p>' + esc(machine.key) + ' · ' + afterLabel(machine) + '</p>' +
        (plan.snapshot_before ? '<p>Take a snapshot before running scripts.</p>' : '') + labTasks(pack,imported,plan.tasks) + '</fieldset>';
    }
    const files = [...new Set((imported.tasks || []).map(t => t.file).filter(Boolean))];
    const bundled = files.length ? '<details class="lab-tasks"><summary>Bundled script source (' + files.length + ')</summary>' +
      files.map(path => '<h4>' + esc(path) + '</h4><pre>' + esc(pack.scripts[path]) + '</pre>').join('') + '</details>' : '';
    return '<fieldset><legend>Create machine · ' + esc(machine.key) + '</legend><p>' + afterLabel(machine) + '</p>' +
      buildReview(plan, imported.os === 'windows', false) +
      '<p>Mapped source · ' + esc(plan.source.value) + (plan.iso_storage ? ' · ISO storage ' + esc(plan.iso_storage) : '') +
      (imported.os === 'windows' && plan.source.kind === 'iso' ? ' · Guest tools ' + esc(plan.guest_tools) : '') + '</p>' +
      bundled + '</fieldset>';
  });
  const license = pack.machines.some(m => m.os === 'windows' && m.source.kind !== 'existing') ? ' I am authorised to use the Windows media and accept its licence.' : '';
  const existing = review.machines.some(m => m.mode === 'existing');
  const details = cards.length === 1 ? cards.join('') : review.machines.map((machine,i) => {
    const isTarget = machine.mode === 'existing';
    const target = isTarget ? machine.plan.targets[0] : machine.plan.machines[0];
    const action = isTarget ? 'Run scripts on existing VM' : machine.plan.purpose === 'template' ? 'Create template' : 'Create VM';
    return '<details class="lab-review-role" name="lab-reviewed-role"' + (i === 0 ? ' open' : '') +
      '><summary><strong>' + esc(target.name) + '</strong><span>' + action + ' · ' + esc(target.node) + ' / VM ' + esc(target.vmid) +
      '</span></summary>' + cards[i] + '</details>';
  }).join('');
  const fields = labStep('Review ' + review.name,'<div class="lab-review-summary">' + details + '</div><button type="button" id="lab-edit">Edit deployment settings</button>' +
    wc('confirmed','Deploy the reviewed lab in the listed order.' + license) +
    (existing ? wc('existing_confirmed','Run the listed scripts on the existing VMs identified above.') : '') +
    (review.dangerous ? wc('risk_confirmed','I reviewed and approve the scripts and sensitive settings in this lab.') : ''),
    'Deploy lab', 'Submitting reviewed lab…', async area => {
      const run = await api('simple/labs/run',{plan_id:review.id,confirmed:area.querySelector('[name=confirmed]').checked,
        ...(existing ? {existing_confirmed:area.querySelector('[name=existing_confirmed]').checked} : {}),
        risk_confirmed:!!area.querySelector('[name=risk_confirmed]')?.checked});
      if (!labActive(area)) return;
      showLabRun(run);
      await refresh();
    },area => {
      const missing = area.querySelector('input[required]:not(:checked)');
      if (!missing) return true;
      $('#lab-error').textContent = missing.name === 'existing_confirmed' ? 'Confirm the changes to the listed existing VMs.' :
        missing.name === 'risk_confirmed' ? 'Review and approve the scripts and sensitive settings.' : 'Confirm the reviewed deployment before continuing.';
      missing.scrollIntoView?.({block:'center'});
      missing.focus();
      return false;
    });
  for (const input of fields.querySelectorAll('[type=checkbox]')) input.required = true;
  $('#lab-edit').onclick = () => mapLab(pack,options,bindings);
}

function renderLabsWorkspace() {
  let workspace = $('#labs-workspace');
  if (!workspace) {
    $('#content').innerHTML = '<div id="labs-workspace"><div class="lab-page-head">' +
      workspaceHeading('Labs','Build reusable processes for new and existing machines.') +
      '<div class="lab-page-actions"><button id="lab-import"><span class="bi bi-upload" aria-hidden="true"></span> Import ZIP</button>' +
      '<button class="primary" id="lab-create"><span class="bi bi-plus-lg" aria-hidden="true"></span> Create lab</button></div></div>' +
      '<nav class="lab-page-tabs" aria-label="Lab views"><button type="button" data-lab-list="packs"><span class="bi bi-box-seam" aria-hidden="true"></span> Saved packs</button>' +
      '<button type="button" data-lab-list="runs"><span class="bi bi-clock-history" aria-hidden="true"></span> Deployments</button></nav><section class="lab-library"></section></div>';
    workspace = $('#labs-workspace');
    $('#lab-create').onclick = () => openLabDesigner();
    $('#lab-import').onclick = importLab;
    workspace.querySelector('.lab-page-tabs').onclick = event => {
      const button = event.target.closest('[data-lab-list]');
      if (button) showLabLibrary(workspace,button.dataset.labList);
    };
    showLabLibrary(workspace,'packs');
  } else loadLabList(workspace.dataset.selected);
  return true;
}

function showLabLibrary(workspace,kind) {
  workspace.dataset.selected = kind;
  for (const tab of workspace.querySelectorAll('[data-lab-list]')) {
    if (tab.dataset.labList === kind) tab.setAttribute('aria-current','page'); else tab.removeAttribute('aria-current');
  }
  const packs = kind === 'packs', library = workspace.querySelector('.lab-library');
  library.setAttribute('aria-label',packs ? 'Saved lab packs' : 'Lab deployments');
  library.innerHTML = '<div class="lab-list-toolbar"><label class="lab-search"><span>Search ' + (packs ? 'packs' : 'deployments') +
    '</span><input type="search" placeholder="Search by name"></label>' +
    (packs ? '<button type="button" id="lab-export">Export saved builds</button>' :
      '<label>Status<select class="lab-status-filter"><option value="">All statuses</option>' +
      ['queued','running','waiting','succeeded','failed','interrupted'].map(status => '<option>' + status + '</option>').join('') + '</select></label>') +
    '</div><div id="lab-' + kind + '" class="lab-list-results"></div>';
  if (packs) $('#lab-export').onclick = selectLabBuilds;
  const results = library.querySelector('.lab-list-results');
  library.querySelector('input').oninput = () => drawLabList(results,kind);
  const status = library.querySelector('.lab-status-filter');
  if (status) status.onchange = () => drawLabList(results,kind);
  loadLabList(kind);
}

async function loadLabList(kind) {
  const area = $('#lab-' + kind);
  if (!area) return;
  const revision = area._labRevision = (area._labRevision || 0) + 1;
  if (!area._labRecords) area.innerHTML = '<p class="lab-list-message" role="status">Loading ' + (kind === 'packs' ? 'saved packs' : 'deployments') + '…</p>';
  try {
    const records = await api('simple/labs/' + kind);
    if (!area.isConnected || area._labRevision !== revision) return;
    if (area._labRecords && JSON.stringify(area._labRecords) === JSON.stringify(records)) return;
    area._labRecords = records;
    drawLabList(area,kind);
  } catch (error) {
    if (!area.isConnected || area._labRevision !== revision) return;
    delete area._labRecords;
    area.innerHTML = '<div class="lab-list-message" role="alert"><strong>Could not load ' +
      (kind === 'packs' ? 'saved packs' : 'deployments') + '</strong><p>' + esc(error.message) + '</p><button type="button">Retry</button></div>';
    area.querySelector('button').onclick = () => loadLabList(kind);
  }
}

function drawLabList(area,kind) {
  if (!area._labRecords) return;
  const packs = kind === 'packs', library = area.parentElement;
  const query = (library.querySelector('[type=search]')?.value || '').trim().toLowerCase();
  const status = library.querySelector('.lab-status-filter')?.value;
  const rows = area._labRecords.filter(r => r.name.toLowerCase().includes(query) && (!status || r.status === status));
  if (!rows.length) {
    const filtered = !!area._labRecords.length;
    const heading = filtered ? 'No matches' : packs ? 'No saved lab packs yet' : 'No deployments yet';
    const detail = filtered ? 'Try a different name or clear your filters.' : packs ?
      'Create your first lab or import a pack to define the machines and steps.' : 'Deploy a reviewed pack to follow each machine and its job log here.';
    area.innerHTML = '<div class="lab-list-empty"><span aria-hidden="true" class="lab-empty-mark bi bi-' +
      (filtered ? 'search' : packs ? 'box-seam' : 'clock-history') + '"></span><h2>' + heading + '</h2><p>' + detail + '</p>' +
      (filtered ? '<button type="button">Clear filters</button>' : packs ? '<a href="/api/simple/labs/example" download>Download example pack</a>' :
        '<button type="button">Browse saved packs</button>') + '</div>';
    if (filtered) area.querySelector('button').onclick = () => {
      library.querySelector('input').value = '';
      if (status) library.querySelector('select').value = '';
      drawLabList(area,kind);
      library.querySelector('input').focus();
    };
    else if (!packs) area.querySelector('button').onclick = () => $('#labs-workspace [data-lab-list=packs]').click();
    return;
  }
  area.innerHTML = '<table class="lab-records"><thead><tr><th scope="col">' + (packs ? 'Lab pack' : 'Deployment') +
    '</th><th scope="col">' + (packs ? 'Roles' : 'Status') + '</th><th scope="col">' + (packs ? 'Saved' : 'Started') +
    '</th><th scope="col" aria-label="Actions"></th></tr></thead><tbody>' + rows.map((r,i) =>
      '<tr data-record="' + i + '"><th scope="row">' + esc(r.name) + '</th><td>' +
      (packs ? esc(r.machine_count) + (r.machine_count === 1 ? ' role' : ' roles') : '<span class="pill ' + esc(r.status) + '">' + esc(r.status) + '</span>') +
      '</td><td>' + esc(new Date(r.created * 1000).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'})) +
      '</td><td><div class="lab-row-actions">' + (packs ? '<button type="button" data-pack-action="setup">Set up lab</button>' +
      '<button type="button" data-pack-action="edit">Edit a copy</button><a href="/api/simple/labs/packs/' + encodeURIComponent(r.id) + '/export" download>Download ZIP</a>' :
      '<button type="button" data-pack-action="run">View deployment</button>') + '</div></td></tr>').join('') + '</tbody></table>';
  area.onclick = event => {
    const button = event.target.closest('[data-pack-action]');
    if (!button) return;
    const record = rows[Number(button.closest('tr').dataset.record)];
    if (button.dataset.packAction === 'edit') openLabDesigner(record.id);
    else if (button.dataset.packAction === 'setup') openSavedPack(record.id);
    else openLabRun(record.id);
  };
}

async function openSavedPack(id) {
  const fields = labStep('Set up lab', '<p>Loading the pack and destination options…</p>', 'Retry', '', () => openSavedPack(id));
  try {
    const pack = await api('simple/labs/packs/' + encodeURIComponent(id));
    if (labActive(fields)) await prepareLabSetup(pack,fields);
  } catch (error) { if (labActive(fields)) $('#lab-error').textContent = error.message; }
}

async function openLabRun(id) {
  const fields = labStep('Lab deployment', '<p>Loading progress…</p>', 'Retry', '', () => openLabRun(id));
  try {
    const run = await api('simple/labs/runs/' + encodeURIComponent(id));
    if (labActive(fields)) showLabRun(run);
  } catch (error) { if (labActive(fields)) $('#lab-error').textContent = error.message; }
}

function showLabRun(run) {
  const fields = labStep(run.name + ' deployment','<div id="lab-run"></div>', 'Refresh status', '', () => updateLabRun($('#lab-run')));
  const panel = fields.querySelector('#lab-run');
  panel.dataset.id = run.id;
  drawLabRun(panel,run);
}

function drawLabRun(panel, run) {
  panel.dataset.status = run.status;
  const html = '<p>Deployment <strong>' + esc(run.status) + '</strong></p>' + run.machines.map(machine => {
    const pending = machine.status === 'blocked' ? 'Prerequisite did not succeed' :
      machine.status === 'pending' ? 'Waiting for prerequisites' : '';
    return '<section class="lab-job"><div><h3>' + esc(machine.name) + '</h3><small>' + esc(machine.key) + ' · ' +
      esc(machine.node) + ' / VM ' + esc(machine.vmid) + (machine.after.length ? ' · After ' + esc(machine.after.join(', ')) : '') +
      (machine.mode === 'existing' ? ' · Scripts on existing VM' : machine.owned ? ' · Creation recorded' : ' · New build') +
      '</small>' + (pending ? '<p>' + pending + '</p>' : '') + '</div><span class="pill ' + esc(machine.status) + '">' +
      esc(machine.status) + '</span>' + (machine.job_id ? '<button type="button" data-log="' + esc(machine.job_id) + '">View log</button>' : '') + '</section>';
  }).join('');
  if (panel._lastMarkup !== html) {
    panel.innerHTML = html;
    panel._lastMarkup = html;
  }
}

async function updateLabRun(panel) {
  if (!panel || panel.dataset.loading) return;
  panel.dataset.loading = 'true';
  try {
    const run = await api('simple/labs/runs/' + encodeURIComponent(panel.dataset.id));
    if (labActive(panel)) {
      drawLabRun(panel,run);
      $('#lab-error').textContent = '';
    }
  } catch (error) { if (labActive(panel)) $('#lab-error').textContent = error.message; }
  finally { delete panel.dataset.loading; }
}
