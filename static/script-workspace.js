/* Guest task metadata and ordered selections shared by builds, labs and Operations. */
function scriptOrigin(item) {
  return item.example ? 'Starter' : item.builtin || !item.kind ? 'Built-in' : 'Saved';
}

function scriptLanguage(item) {
  return scriptFormats[item.kind] || 'Guest task';
}

function scriptMatches(item, query) {
  const source = [item.name, item.description, item.platform, scriptLanguage(item), scriptOrigin(item),
    ...Object.values(item.parameters || {})].filter(Boolean).join(' ').toLowerCase();
  return query.trim().toLowerCase().split(/\s+/).every(word => source.includes(word));
}

function scriptSource(item) {
  return item.body ? '<details class="script-source"><summary>View source</summary><pre>' +
    esc(item.body) + '</pre></details>' : '';
}

function mountScriptSequence(host, {catalog, steps = [], portable = false, occupied = [],
                                   onChange, getOccupied, runInputs = false, catalogView = 'compact'}) {
  const selected = steps.map(s => ({item:s.item, ref:{...s.ref}, params:{...s.params}}));
  let letters = occupied, driveStatus = getOccupied ? 'unread' : 'ready';
  const selectName = runInputs ? 'script' : 'library_script', browse = catalogView === 'list';
  const order = '<ol class="script-steps" aria-label="Script execution order"></ol>';
  const picker = '<div class="script-lookup"><label>Search scripts<input type="search" data-script-search placeholder="Name or purpose" autocomplete="off"></label>' +
    (browse ? '</div><ul class="script-results" aria-label="Script catalog"></ul>' :
      '<label>Library script<select name="' + selectName + '" data-script-choice></select></label>' +
      '<button type="button" data-add-script>Add script</button></div>') + '<small data-lookup-status role="status"></small>' +
    '<div class="script-preview" data-script-preview></div>';
  host.innerHTML = (browse ? order + '<details class="script-catalog-drawer"' +
    (selected.length ? '' : ' open') + '><summary>Add step</summary>' + picker + '</details>' : picker + order) +
    '<div class="script-drive-status" role="status"></div>';
  const search = host.querySelector('[data-script-search]'), choose = host.querySelector('select');
  const add = host.querySelector('[data-add-script]'), status = host.querySelector('[data-lookup-status]');
  const preview = host.querySelector('[data-script-preview]'), list = host.querySelector('ol');
  const driveNotice = host.querySelector('.script-drive-status');
  const results = host.querySelector('.script-results');
  const stepInputs = () => [...list.querySelectorAll('[data-task-input]')];
  function read() {
    for (const el of stepInputs()) {
      const step = selected[Number(el.dataset.index)];
      if (!el.hasAttribute('data-script-drive') || driveStatus === 'ready') step.params[el.dataset.taskInput] = paramValue(el);
    }
    return selected.map(s => ({
      item:s.item, ref:{...s.ref}, params:Object.fromEntries(Object.keys(s.item.parameters || {})
        .filter(k => !portable || s.item.input_types?.[k]?.type !== 'password')
        .map(k => [k, s.params[k] ?? scriptInputValue(s.item.defaults,k) ?? '']))
    }));
  }
  function changed() { if (onChange) onChange(read()); }
  function setOccupied(values) {
    letters = values; driveStatus = 'ready'; driveNotice.replaceChildren();
    for (const el of list.querySelectorAll('[data-script-drive]')) {
      const step = selected[Number(el.dataset.index)], key = el.dataset.taskInput;
      const wanted = step.params[key] ?? scriptInputValue(step.item.defaults,key);
      for (const option of el.options) {
        if (!option.value) continue;
        const used = letters.includes(option.value);
        option.disabled = used; option.textContent = option.value + ':' + (used ? ' — in use' : '');
      }
      el.value = letters.includes(wanted) ? '' : wanted || '';
    }
    changed();
  }
  async function loadDrives() {
    if (!getOccupied || driveStatus === 'loading' || driveStatus === 'ready') return;
    const selects = list.querySelectorAll('[data-script-drive]');
    if (!selects.length) return;
    driveStatus = 'loading'; driveNotice.textContent = 'Checking available drive letters…';
    try {
      const found = await getOccupied();
      if (host.contains(list)) setOccupied(found);
    }
    catch (err) {
      driveStatus = 'failed'; driveNotice.textContent = err.message + ' ';
      const retry = document.createElement('button');
      retry.type = 'button'; retry.textContent = 'Retry drive discovery'; retry.onclick = loadDrives; driveNotice.append(retry);
    }
  }
  function drawSteps() {
    list.innerHTML = selected.map(({item,ref,params}, i) => {
      const label = item.name;
      const buttons = [['up','Move up',i === 0],['down','Move down',i === selected.length - 1],['remove','Remove',false]];
      const controls = Object.entries(item.parameters || {}).map(([key, caption]) => {
        const spec = scriptInputValue(item.input_types,key);
        if (portable && spec?.type === 'password') return '<p class="script-prompt">' + esc(caption) + ' · Enter at deployment</p>';
        const attrs = 'data-task-input="' + esc(key) + '" data-index="' + i + '"' +
          (runInputs ? ' data-run-param="' + esc(key) + '"' : '') +
          (spec?.type === 'drive' ? ' data-script-drive' : '');
        return '<label>' + esc(caption) + paramControl(spec,scriptInputValue(params,key) ?? scriptInputValue(item.defaults,key),
          attrs,spec?.type === 'drive' && driveStatus !== 'ready' ? [...'DEFGHIJKLMNOPQRSTUVWXYZ'] : letters) + '</label>';
      }).join('');
      const origin = Object.hasOwn(ref,'id') ? scriptOrigin(item) : 'Frozen';
      return '<li data-script-step><div class="script-step-head"><div><strong>' + esc(label) + '</strong><small>' +
        esc(origin) + (item.kind ? ' · ' + esc(scriptLanguage(item)) : '') + '</small></div><div class="script-step-actions">' +
        buttons.map(([action,text,disabled]) => '<button type="button" data-task-action="' + action + '" data-index="' + i +
          '" aria-label="' + text + ' ' + esc(label) + '"' + (disabled ? ' disabled' : '') + '>' + text + '</button>').join('') +
        '</div></div>' + (controls ? '<div class="script-step-inputs">' + controls + '</div>' : '') + scriptSource(item) + '</li>';
    }).join('');
    for (const el of list.querySelectorAll('input[type=password]')) {
      el.value = selected[Number(el.dataset.index)].params[el.dataset.taskInput] || '';
    }
    if (driveStatus === 'unread') loadDrives();
  }
  function showChoice() {
    if (!choose) {
      results.querySelectorAll('button').forEach(b => { b.disabled = selected.length >= 50; });
      return;
    }
    const item = catalog.find(t => t.id === choose.value);
    add.disabled = !item || selected.length >= 50;
    preview.innerHTML = item && !selected.some(s => s.ref.id === item.id)
      ? (item.description ? '<p>' + esc(item.description) + '</p>' : '') + scriptSource(item) : '';
  }
  function filter() {
    const current = choose?.value;
    const matches = catalog.filter(item => scriptMatches(item, search.value));
    if (browse) {
      results.innerHTML = matches.slice().sort((a,b) => a.name.localeCompare(b.name)).map(item =>
        '<li><div><strong>' + esc(item.name) + '</strong><small>' + esc(scriptLanguage(item)) + ' · ' + scriptOrigin(item) +
        '</small>' + (item.description ? '<p>' + esc(item.description) + '</p>' : '') + scriptSource(item) +
        '</div><button type="button" data-add-catalog="' + esc(item.id) + '" aria-label="Add ' + esc(item.name) + '">Add</button></li>').join('');
    } else {
      choose.innerHTML = '<option value="">' + (matches.length ? 'Choose a script' : 'No matching scripts') + '</option>' +
      ['Saved','Built-in','Starter'].map(origin => {
        const items = matches.filter(t => scriptOrigin(t) === origin).sort((a,b) => a.name.localeCompare(b.name));
        return items.length ? '<optgroup label="' + origin + '">' + items.map(t =>
          '<option value="' + esc(t.id) + '">' + esc(t.name) + '</option>').join('') + '</optgroup>' : '';
      }).join('');
      choose.value = matches.some(t => t.id === current) ? current : '';
    }
    status.textContent = !matches.length ? (catalog.length ? 'No scripts match this search.' : 'No compatible scripts available.') : '';
    showChoice();
  }
  function addSelected(id) {
    const item = catalog.find(t => t.id === id);
    if (!item || selected.length >= 50) return;
    read(); selected.push({item,ref:{id:item.id},params:{}});
    drawSteps(); changed(); showChoice();
    status.textContent = item.name + ' added as step ' + selected.length + '.';
    if (!browse) choose.focus();
  }
  if (choose) choose.onchange = showChoice;
  search.oninput = filter;
  search.onkeydown = event => { if (event.key === 'Enter') event.preventDefault(); };
  if (add) add.onclick = () => addSelected(choose.value);
  if (results) results.onclick = event => {
    const button = event.target.closest('[data-add-catalog]');
    if (button) addSelected(button.dataset.addCatalog);
  };
  list.oninput = changed;
  list.onchange = changed;
  list.onclick = event => {
    const action = event.target.closest('[data-task-action]');
    if (!action || action.disabled) return;
    const i = Number(action.dataset.index), direction = action.dataset.taskAction;
    read();
    const target = direction === 'up' ? i - 1 : i + 1;
    if (direction === 'remove') selected.splice(i,1);
    else [selected[i],selected[target]] = [selected[target],selected[i]];
    drawSteps(); changed(); showChoice();
    const focusRow = list.children[direction === 'remove' ? Math.min(i,selected.length - 1) : target];
    (focusRow?.querySelector('[data-task-action="' + direction + '"]:not(:disabled)') ||
      focusRow?.querySelector('[data-task-action=remove]') || choose || search).focus();
    status.textContent = direction === 'remove' ? 'Script removed.' : 'Moved to step ' + (target + 1) + '.';
  };
  filter(); drawSteps();
  if (runInputs && choose && selected.length && catalog.some(t => t.id === selected[0].ref.id)) {
    choose.value = selected[0].ref.id; showChoice();
  }
  return {steps:read,values:() => read().map(s => ({...s.ref,params:s.params})),setOccupied};
}
