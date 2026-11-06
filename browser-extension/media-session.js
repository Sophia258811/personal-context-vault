// Only the extension popup may start capture. All uploads pass through this worker.
const mediaOffscreen = chrome.runtime.getURL('media-offscreen.html');
const mediaPopup = chrome.runtime.getURL('popup.html');
let mediaSession;
let mediaStarting;
let mediaClosing;
let mediaState = 'idle';
let mediaDiagnostic;

function mediaStatus() {
  return {state: mediaState, diagnostic: mediaDiagnostic};
}

async function receiverProbe() {
  try {
    const response = await fetch('http://127.0.0.1:8765/media-ready', {
      method: 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(2000)
    });
    return response.status === 204 ? null : 'restart_needed';
  } catch { return 'receiver_offline'; }
}

function startupReason(error) {
  if (error?.name === 'TimeoutError' || error?.name === 'AbortError') return 'timeout';
  if (/not been invoked|activeTab|permission|not allowed/i.test(error?.message || '')) return 'permission';
  if (/captur.*(?:already|same tab)|already.*captur/i.test(error?.message || '')) return 'busy';
  return 'failed';
}

async function startupStep(operation, timeout = 5000) {
  let timer;
  try {
    return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error('Timed out'), {name: 'TimeoutError'})), timeout);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

async function offscreenExists() {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'], documentUrls: [mediaOffscreen]
  });
  return contexts.length !== 0;
}

async function sendOffscreen(action, session, data = {}) {
  return chrome.runtime.sendMessage({target: 'pcv-offscreen', action, session: session.id, ...data});
}

async function stopMedia(state = 'stopped', diagnostic) {
  const session = mediaSession;
  if (!session && !mediaStarting) return;
  if (mediaStarting) mediaStarting.cancelled = true;
  mediaState = state;
  mediaDiagnostic = diagnostic;
  if (!session) return;
  mediaSession = undefined;
  session.upload?.abort();
  mediaClosing = (async () => {
    await Promise.allSettled([
      startupStep(chrome.tabs.sendMessage(session.tabId, {type: 'pcv-media-unwatch', session: session.id}, {frameId: 0})),
      startupStep(sendOffscreen('stop', session)),
    ]);
    if (await startupStep(offscreenExists())) await startupStep(chrome.offscreen.closeDocument());
  })();
  try { await mediaClosing; } catch { /* Offscreen watchdog also stops orphaned tracks. */ }
  finally { mediaClosing = undefined; }
}

async function currentMediaTab(session) {
  const [tab, window, settings] = await Promise.all([
    chrome.tabs.get(session.tabId),
    chrome.windows.get(session.windowId),
    chrome.storage.local.get(['running', 'token', 'origins'])
  ]);
  if (!window.focused || !tab.active || tab.incognito || tab.mutedInfo?.muted ||
      tab.url !== session.url || !await allowed(tab.url, settings)) return null;
  return settings;
}

async function startMedia(targetTab, receiverVerified = false) {
  if (mediaSession || mediaStarting || mediaClosing) return {state: 'busy'};
  if (!chrome.tabCapture || !chrome.offscreen || !chrome.runtime.getContexts) return {state: 'unsupported'};
  const attempt = {cancelled: false};
  mediaStarting = attempt;
  let stage = 'authorization';
  function checkCancelled() {
    if (attempt.cancelled) throw new Error('Cancelled');
  }
  try {
    const tab = targetTab ?? (await chrome.tabs.query({active: true, currentWindow: true}))[0];
    checkCancelled();
    const platform = tab?.url && pcvMedia.platform(tab.url);
    if (!platform) return {state: 'ineligible'};
    if (tab.mutedInfo?.muted) return {state: 'muted_tab'};
    const session = {id: crypto.randomUUID(), tabId: tab.id, windowId: tab.windowId, url: tab.url, platform};
    const settings = await currentMediaTab(session);
    checkCancelled();
    if (!settings) return {state: receiverVerified ? 'focus_required' : 'ineligible'};
    session.token = settings.token;
    mediaSession = session;
    mediaState = 'starting';
    mediaDiagnostic = undefined;

    if (!receiverVerified) {
      stage = 'receiver';
      const status = await receiverProbe();
      checkCancelled();
      if (status) {
        await stopMedia(status);
        return mediaStatus();
      }
    }

    stage = 'offscreen';
    if (await startupStep(offscreenExists())) await startupStep(chrome.offscreen.closeDocument());
    checkCancelled();
    const opening = chrome.offscreen.createDocument({
      url: 'media-offscreen.html', reasons: ['USER_MEDIA'],
      justification: 'Save user-started short audio and cropped video frames from the active video tab to local PCV'
    });
    opening.then(() => {
      if (!mediaSession && !mediaStarting) return chrome.offscreen.closeDocument();
    }).catch(() => {});
    await startupStep(opening);
    checkCancelled();
    stage = 'tab_capture';
    const streamId = await startupStep(chrome.tabCapture.getMediaStreamId({targetTabId: tab.id}));
    checkCancelled();
    stage = 'capture_handshake';
    const result = await startupStep(sendOffscreen('start', session, {streamId}), 15000);
    checkCancelled();
    if (!result?.ok) {
      await stopMedia('unavailable', result?.diagnostic || {stage, reason: 'failed'});
      return mediaStatus();
    }
    stage = 'player_monitor';
    await startupStep(chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['adapters.js', 'capture.js']}));
    checkCancelled();
    await startupStep(chrome.tabs.sendMessage(tab.id, {type: 'pcv-media-watch', session: session.id}, {frameId: 0}));
    checkCancelled();
    mediaState = 'running';
    return mediaStatus();
  } catch (error) {
    if (!attempt.cancelled) {
      const reason = startupReason(error);
      const state = stage === 'receiver' ? 'receiver_offline'
        : stage === 'tab_capture' && reason === 'permission' ? 'fresh_tab_needed'
        : stage === 'tab_capture' && reason === 'busy' ? 'busy'
        : 'unavailable';
      await stopMedia(state, {stage, reason});
    } else {
      // A stop during document creation can finish before the new document exists.
      if (await startupStep(offscreenExists()).catch(() => false)) {
        await startupStep(chrome.offscreen.closeDocument()).catch(() => {});
      }
    }
    return mediaStatus();
  } finally {
    mediaStarting = undefined;
  }
}

async function uploadMedia(message, session) {
  const beat = session.beat;
  const segment = message.segment;
  if (!beat || !segment || session.upload || segment.platform_content_id !== beat.platform_content_id ||
      !Number.isFinite(segment.start_seconds) || !Number.isFinite(segment.end_seconds) ||
      segment.end_seconds <= segment.start_seconds || segment.end_seconds - segment.start_seconds > 15 ||
      Math.abs(segment.end_seconds - beat.position_seconds) > 2 ||
      Date.now() - beat.at > pcvMedia.IDLE_MS) return {ok: false};
  const settings = await currentMediaTab(session);
  if (!settings || session !== mediaSession || session.beat !== beat) return {ok: false};
  // Only origin and path are needed. Query parameters may contain private data.
  const page = new URL(session.url);
  const body = JSON.stringify({...segment, platform: session.platform, url: page.origin + page.pathname});
  if (new TextEncoder().encode(body).length > 4 * 1024 * 1024) return {ok: false};
  const controller = new AbortController();
  session.upload = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch('http://127.0.0.1:8765/media', {
      method: 'POST', headers: {'Authorization': 'Bearer ' + settings.token, 'Content-Type': 'application/json'},
      body, signal: controller.signal
    });
    const terminal = {
      401: 'pairing_needed', 403: 'source_disabled', 404: 'restart_needed', 507: 'quota_full'
    }[response.status];
    if (terminal && session === mediaSession) {
      await stopMedia(terminal);
      return {ok: false};
    }
    if (!response.ok) throw new Error('receiver rejected media');
    if (session === mediaSession) mediaState = 'saved';
    return {ok: true};
  } catch {
    if (session === mediaSession) mediaState = 'upload_failed';
    return {ok: false};
  } finally {
    clearTimeout(timeout);
    session.upload = undefined;
  }
}

async function mediaMessage(message, sender) {
  if (!sender.tab && sender.url === mediaPopup) {
    if (message.action === 'start') return startMedia();
    if (message.action === 'stop') await stopMedia();
    return mediaStatus();
  }
  const session = mediaSession;
  if (!session || session.id !== message.session) return {ok: false};
  if (message.action === 'beat' && sender.tab?.id === session.tabId && sender.frameId === 0 &&
      sender.url === session.url) {
    const beat = pcvMedia.valid(message.beat) && message.beat.platform === session.platform
      ? {...message.beat, at: Date.now()} : null;
    if (!beat || (session.beat && !pcvMedia.continues(session.beat, beat))) {
      session.upload?.abort();
    }
    session.beat = beat;
    await sendOffscreen('beat', session, {beat});
    return {ok: true};
  }
  if (!sender.tab && sender.url === mediaOffscreen) {
    if (message.action === 'upload') return uploadMedia(message, session);
    if (message.action === 'ended') await stopMedia();
  }
  return {ok: false};
}

chrome.tabs.onActivated.addListener(({tabId, windowId}) => {
  if (mediaSession && (tabId !== mediaSession.tabId || windowId !== mediaSession.windowId)) stopMedia('focus_stopped');
});
chrome.tabs.onRemoved.addListener(tabId => {
  if (mediaSession?.tabId === tabId) stopMedia('navigation_stopped');
});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (mediaSession?.tabId === tabId && (change.status === 'loading' || change.url || change.mutedInfo?.muted)) {
    stopMedia('navigation_stopped');
  }
});
chrome.windows.onFocusChanged.addListener(windowId => {
  if (mediaSession && windowId !== mediaSession.windowId) stopMedia('focus_stopped');
});
chrome.permissions.onRemoved.addListener(() => stopMedia('permission_revoked'));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  const removedOrigin = changes.origins?.oldValue?.some(origin => !changes.origins.newValue?.includes(origin));
  const replacedToken = changes.token && mediaSession && changes.token.newValue !== mediaSession.token;
  if (changes.running?.newValue === false || removedOrigin || replacedToken) stopMedia();
});
