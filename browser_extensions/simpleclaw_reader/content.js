(() => {
  function cleanText(value) {
    return (value || '').replace(/\s+/g, ' ').trim();
  }

  function cloneWithoutNoise(node) {
    const clone = node.cloneNode(true);
    clone.querySelectorAll('script, style, noscript, svg, canvas, iframe, input, textarea, select, button, nav, header, footer').forEach((child) => child.remove());
    return clone;
  }

  function extractReadableText() {
    const bodyClone = cloneWithoutNoise(document.body);
    const candidates = [
      document.querySelector('article'),
      document.querySelector('main'),
      bodyClone,
    ].filter(Boolean);
    let best = '';
    for (const node of candidates) {
      const source = node === bodyClone ? node : cloneWithoutNoise(node);
      const text = cleanText(source.innerText || source.textContent || '');
      if (text.length > best.length) best = text;
    }
    return best;
  }

  const passwordField = Boolean(document.querySelector('input[type="password"]'));
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));

  return {
    type: 'page_text',
    request_id: params.get('simpleclaw_request'),
    url: location.href,
    origin: location.origin,
    title: document.title || '',
    text: extractReadableText(),
    has_password_field: passwordField,
  };
})();
