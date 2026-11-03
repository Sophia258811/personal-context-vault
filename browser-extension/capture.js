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
      const sections = {body: [], comments: [], navigation: [], unclassified: []};
      const observedAt = new Date().toISOString();
      const videos = [...document.querySelectorAll('video')].filter(v => {
        const r = v.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
      });
      // Multiple visible players are ambiguous; do not bind one arbitrarily.
      const video = globalThis.pcvAdapters.selectVideo(videos);
      const activeCard = video?.closest('[data-e2e-vid]');
      const identity = globalThis.pcvAdapters.identity(location.href, video);
      const media = globalThis.pcvAdapters.media(video, observedAt);
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      let count = 0;
      while ((node = walker.nextNode()) && count < 12000) {
        const el = node.parentElement;
        const card = el?.closest('[data-e2e-vid]');
        if (card && activeCard && card !== activeCard) continue;
        if (!el || el.closest('input,textarea,select,script,style,noscript,[contenteditable],[role="textbox"]')) continue;
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) continue;
        const style = getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;
        const text = node.textContent.trim();
        if (text) {
          const clipped = text.slice(0, 12000 - count);
          parts.push(clipped); sections[globalThis.pcvAdapters.region(el)].push(clipped); count += clipped.length;
        }
      }
      const text = parts.join('\n').slice(0, 12000);
      const url = new URL(location.href);
      url.search = ''; url.hash = ''; url.username = ''; url.password = '';
      const signature = JSON.stringify([url.href, identity, text, media?.position_seconds, media?.paused]);
      if (!text || signature === last) return;
      const result = await chrome.runtime.sendMessage({type: 'capture', event: {
        url: url.href, title: document.title, text, ...identity, schema_version: 2,
        observed_at: observedAt, sections: Object.fromEntries(Object.entries(sections).map(([k,v]) => [k,v.join('\n')])),
        media, available_modalities: ['text'],
        capture_limitations: ['region_classification_is_heuristic', 'no_audio_or_video_bytes',
          ...(videos.length > 1 ? ['multiple_visible_players'] : [])]
      }});
      if (result?.ok) last = signature;
    } finally { busy = false; }
  }
  setInterval(() => capture().catch(() => {}), 15000);
  capture().catch(() => {});
}
