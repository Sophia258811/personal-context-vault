if (!globalThis.pcvCompanionInstalled) {
  globalThis.pcvCompanionInstalled = true;
  let last = '';
  let busy = false;
  async function capture() {
    if (busy || document.visibilityState !== 'visible' || !document.hasFocus()) return;
    busy = true;
    try {
      // Read rendered non-editable text; never serialize forms, cookies or storage.
      const parts = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      let count = 0;
      while ((node = walker.nextNode()) && count < 12000) {
        const el = node.parentElement;
        if (!el || el.closest('input,textarea,select,script,style,noscript,[contenteditable],[role="textbox"]')) continue;
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) continue;
        const style = getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;
        const text = node.textContent.trim();
        if (text) { parts.push(text); count += text.length; }
      }
      const text = parts.join('\n').slice(0, 12000);
      const url = new URL(location.href);
      url.search = ''; url.hash = ''; url.username = ''; url.password = '';
      const signature = url.href + text;
      if (!text || signature === last) return;
      const result = await chrome.runtime.sendMessage({type: 'capture', event: {url: url.href, title: document.title, text}});
      if (result?.ok) last = signature;
    } finally { busy = false; }
  }
  setInterval(() => capture().catch(() => {}), 15000);
  capture().catch(() => {});
}
