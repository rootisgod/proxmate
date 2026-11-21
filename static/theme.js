/* Resolve appearance before the first paint, including on the sign-in and API pages. */
(() => {
  const key = 'proxmate.appearance';
  const system = window.matchMedia?.('(prefers-color-scheme: dark)');
  const preference = value => value === 'dark' || value === 'light' ? value : 'system';
  let mode = 'system';
  try { mode = preference(localStorage.getItem(key)); } catch { /* Storage may be disabled. */ }

  function apply() {
    document.documentElement.dataset.theme = mode === 'system' ? (system?.matches ? 'dark' : 'light') : mode;
    const select = document.querySelector('#appearance');
    if (select) {
      select.value = mode;
      const icon = select.parentElement.querySelector('.appearance-icon');
      icon.className = 'bi appearance-icon bi-' + {system:'display', light:'sun', dark:'moon-stars'}[mode];
    }
  }

  apply();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply, {once:true});
  system?.addEventListener('change', () => { if (mode === 'system') apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    mode = preference(event.newValue);
    apply();
  });
  document.addEventListener('change', event => {
    if (event.target.id !== 'appearance') return;
    mode = preference(event.target.value);
    apply();
    try {
      if (mode === 'system') localStorage.removeItem(key);
      else localStorage.setItem(key, mode);
    } catch { /* The choice still applies for this tab. */ }
  });
})();
