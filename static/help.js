const guideIndex = document.querySelector('.guide-index details');
const compact = window.matchMedia('(max-width: 800px)');
guideIndex.open = !compact.matches;
compact.addEventListener('change', () => { guideIndex.open = !compact.matches; });
guideIndex.addEventListener('click', event => {
  if (compact.matches && event.target.closest('a')) guideIndex.open = false;
});

const copyTask = document.querySelector('#copy-task');
copyTask.parentElement.hidden = false;
copyTask.addEventListener('click', async () => {
  const example = document.querySelector('#member-join-example');
  const status = document.querySelector('#copy-status');
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(example.textContent);
      status.textContent = 'Task copied.';
      return;
    } catch { /* A LAN dashboard or browser permissions may require manual copying. */ }
  }
  const range = document.createRange();
  range.selectNodeContents(example);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  status.textContent = 'Task selected. Use your device’s Copy action.';
});
