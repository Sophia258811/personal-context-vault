if (!globalThis.pcvCompanionInstalled) {
  globalThis.pcvCompanionInstalled = true;
  let last = '';
  let busy = false;
  let mediaWatch;
  let mediaTimer;
  let previousPlayer;

  async function sendMediaBeat(beat) {
    const session = mediaWatch;
    if (!session) return;
    try {
      const response = await chrome.runtime.sendMessage({target: 'pcv-media', action: 'beat', session, beat});
      if (!response?.ok && session === mediaWatch) stopWatchingMedia();
    } catch {
      if (session === mediaWatch) stopWatchingMedia();
    }
  }

  function invalidateMedia() { sendMediaBeat(null); }
  const mediaEvents = ['pause', 'seeking', 'waiting', 'ended', 'emptied', 'ratechange', 'volumechange'];
  function stopWatchingMedia() {
    clearInterval(mediaTimer);
    mediaWatch = undefined;
    for (const event of mediaEvents) previousPlayer?.removeEventListener(event, invalidateMedia);
    previousPlayer = undefined;
  }

  function measureMedia() {
    if (document.visibilityState !== 'visible' || !document.hasFocus() || document.pictureInPictureElement ||
        window.visualViewport?.scale !== 1 || !innerWidth || !innerHeight) return invalidateMedia();
    const visible = [...document.querySelectorAll('video')].filter(video => {
      const rect = video.getBoundingClientRect();
      return rect.width >= 80 && rect.height >= 80 && rect.right > 0 && rect.bottom > 0 &&
        rect.left < innerWidth && rect.top < innerHeight &&
        (!video.checkVisibility || video.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}));
    });
    const video = globalThis.pcvAdapters.selectVideo(visible);
    if (video !== previousPlayer) {
      for (const event of mediaEvents) {
        previousPlayer?.removeEventListener(event, invalidateMedia);
        video?.addEventListener(event, invalidateMedia);
      }
      previousPlayer = video;
    }
    if (!video || video.paused || video.seeking || video.ended || video.readyState < 3 ||
        video.playbackRate < 0.5 || video.playbackRate > 2) return invalidateMedia();
    const identity = globalThis.pcvAdapters.identity(location.href, video);
    if (!identity.platform_content_id) return invalidateMedia();
    for (const element of document.querySelectorAll('audio,video')) {
      if (element !== video && !element.paused && !element.muted && element.volume > 0) return invalidateMedia();
    }
    const rect = video.getBoundingClientRect();
    const left = Math.max(rect.left, 0);
    const top = Math.max(rect.top, 0);
    const right = Math.min(rect.right, innerWidth);
    const bottom = Math.min(rect.bottom, innerHeight);
    if (right - left < rect.width * 0.6 || bottom - top < rect.height * 0.6) return invalidateMedia();
    sendMediaBeat({
      ...identity, position_seconds: video.currentTime, rate: video.playbackRate,
      audible: !video.muted && video.volume > 0,
      viewport: {width: innerWidth, height: innerHeight},
      rect: {left: left / innerWidth, top: top / innerHeight, right: right / innerWidth, bottom: bottom / innerHeight}
    });
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message.type === 'pcv-media-watch') {
      stopWatchingMedia();
      mediaWatch = message.session;
      mediaTimer = setInterval(measureMedia, 500);
      measureMedia();
    } else if (message.type === 'pcv-media-unwatch' && message.session === mediaWatch) {
      stopWatchingMedia();
    }
  });
  document.addEventListener('visibilitychange', invalidateMedia);
  window.addEventListener('blur', invalidateMedia);
  window.addEventListener('pagehide', () => { invalidateMedia(); stopWatchingMedia(); });
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
