const releaseDialog = document.getElementById('release-dialog');
document.getElementById('show-release').addEventListener('click', () => {
  document.getElementById('release-feedback').textContent = '';
  releaseDialog.showModal();
});

document.getElementById('copy-release').addEventListener('click', async () => {
  const command = document.getElementById('release-command');
  const feedback = document.getElementById('release-feedback');
  try {
    await navigator.clipboard.writeText(command.textContent);
    feedback.textContent = 'Copied.';
  } catch {
    const range = document.createRange();
    range.selectNodeContents(command);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    feedback.textContent = 'Command selected. Use Copy on your device.';
  }
});
