async function sendCurrentPage() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  const [result] = await chrome.scripting.executeScript({
    target: {tabId: tab.id},
    files: ['content.js'],
  });
  return chrome.runtime.sendMessage({
    type: 'send_page_to_simpleclaw',
    payload: result.result,
  });
}

document.getElementById('send').addEventListener('click', async () => {
  const status = document.getElementById('status');
  status.textContent = 'Sending...';
  try {
    const response = await sendCurrentPage();
    status.textContent = response && response.ok ? 'Sent to SimpleClaw.' : `Failed: ${(response && response.error) || 'unknown error'}`;
  } catch (err) {
    status.textContent = `Failed: ${err.message}`;
  }
});
