// Conservative identity extraction: never use unrelated recommendation links.
globalThis.pcvAdapters = (() => {
  function identity(href, video) {
    const url = new URL(href);
    const host = url.hostname;
    let platform = 'browser', id = null, method = 'unresolved';
    if (host === 'douyin.com' || host.endsWith('.douyin.com')) {
      platform = 'douyin';
      id = url.pathname.match(/^\/video\/(\d+)\/?$/)?.[1] || url.searchParams.get('modal_id');
      if (id && /^\d+$/.test(id)) method = 'page_url';
      else {
        const container = video?.closest('[data-e2e-vid],[data-aweme-id],[data-video-id]');
        id = container?.getAttribute('data-e2e-vid') || container?.getAttribute('data-aweme-id') || container?.getAttribute('data-video-id');
        if (id && /^\d+$/.test(id)) method = 'player_ancestor_attribute';
        else id = null;
      }
    } else if (host === 'www.youtube.com' || host === 'youtube.com' || host === 'm.youtube.com') {
      platform = 'youtube';
      id = url.pathname === '/watch' ? url.searchParams.get('v') : url.pathname.match(/^\/shorts\/([^/]+)/)?.[1];
      if (id && /^[\w-]{11}$/.test(id)) method = 'page_url'; else id = null;
    } else if (host === 'bilibili.com' || host.endsWith('.bilibili.com')) {
      platform = 'bilibili';
      id = url.pathname.match(/^\/video\/(BV[a-zA-Z0-9]+|av\d+)/)?.[1];
      if (id) { id += ':p' + (/^\d+$/.test(url.searchParams.get('p') || '') ? url.searchParams.get('p') : '1'); method = 'page_url'; }
    }
    return {platform, platform_content_id: id || null, identity_method: method};
  }
  function region(el) {
    if (el.closest('[data-e2e*="comment"],[data-testid*="comment"],#comments,ytd-comments')) return 'comments';
    if (el.closest('nav,header,footer,[role="navigation"]')) return 'navigation';
    if (el.closest('article,main,[role="main"],[data-e2e*="desc"]')) return 'body';
    return 'unclassified';
  }
  function media(video, observedAt) {
    if (!video || video.readyState < 1) return null;
    let duration = Number.isFinite(video.duration) ? video.duration : null;
    let durationSource = duration === null ? 'unavailable' : 'html_video_element';
    const card = video.closest?.('[data-e2e-vid]');
    if (duration === null && card) {
      const label = card.querySelector('.time-duration')?.textContent?.trim();
      if (label && /^\d{1,3}:[0-5]\d(?::[0-5]\d)?$/.test(label)) {
        duration = label.split(':').reduce((total, n) => total * 60 + Number(n), 0);
        durationSource = 'player_time_label';
      }
    }
    return {observed_at: observedAt, position_seconds: Number.isFinite(video.currentTime) ? video.currentTime : null,
      duration_seconds: duration, duration_source: durationSource,
      paused: video.paused, playback_rate: video.playbackRate, timing_source: 'html_video_element'};
  }
  function selectVideo(videos) {
    const active = videos.filter(v => v.closest('[data-e2e="feed-active-video"]'));
    return active.length === 1 ? active[0] : videos.length === 1 ? videos[0] : null;
  }
  return {identity, region, media, selectVideo};
})();
