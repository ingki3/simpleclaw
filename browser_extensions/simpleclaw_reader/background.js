chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== 'send_page_to_simpleclaw') return false;

  chrome.runtime.sendNativeMessage(
    'com.simpleclaw.browser_handoff',
    message.payload,
    (response) => {
      if (chrome.runtime.lastError) {
        sendResponse({ok: false, error: chrome.runtime.lastError.message});
        return;
      }
      sendResponse(response || {ok: false, error: 'empty native host response'});
    },
  );
  return true;
});
