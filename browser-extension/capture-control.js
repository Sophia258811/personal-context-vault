const captureSettings = ['running', 'token', 'origins'];
const allCaptureOrigins = ['https://*/*', 'http://*/*'];
let captureStart;
let captureStop;
let lastCaptureAttempt;

async function requestedTab({tabId, origin}) {
  if (!Number.isInteger(tabId) || typeof origin !== 'string') return null;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  return tab && !tab.incognito && captureOrigin(tab.url) === `${origin}/*` ? tab : null;
}

async function captureView(message) {
  const [tab, settings, recent] = await Promise.all([
    requestedTab(message), chrome.storage.local.get(captureSettings),
    chrome.storage.session.get(['status', 'statusTab'])
  ]);
  const attempt = tab?.id === lastCaptureAttempt?.tabId && tab?.url === lastCaptureAttempt?.url
    ? lastCaptureAttempt : undefined;
  const currentMedia = Boolean(tab && mediaSession?.tabId === tab.id);
  return {
    running: Boolean(settings.running), paired: Boolean(settings.token),
    siteAuthorized: Boolean(tab && await siteAuthorized(tab.url, settings)),
    allSitesAuthorized: allCaptureOrigins.every(origin => settings.origins?.includes(origin)) &&
      await chrome.permissions.contains({origins: allCaptureOrigins}),
    mediaSupported: Boolean(tab && pcvMedia.platform(tab.url)), currentMedia,
    media: currentMedia || attempt?.media?.state === 'running' ? mediaStatus() : attempt?.media ?? {state: 'idle'},
    textStatus: tab && tab.id === recent.statusTab ? recent.status : '',
    busy: Boolean(captureStart || captureStop), attempted: Boolean(attempt?.attempted), error: attempt?.error
  };
}

async function ensureCaptureReceiver(paired) {
  const status = await receiverProbe();
  if (status !== 'receiver_offline') return status;
  try {
    const result = await chrome.runtime.sendNativeMessage('org.pcv.launcher', {action: 'start', minimized: paired});
    if (!result?.ok) return result?.error === 'port_in_use' ? 'port_in_use' : 'launch_failed';
    return receiverProbe();
  } catch { return 'launch_failed'; }
}

async function enableCapture(message, attempt) {
  const tab = await requestedTab(message);
  if (!tab?.active) return 'tab_changed';
  attempt.url = tab.url;
  const origins = message.scope === 'all' ? allCaptureOrigins : [captureOrigin(tab.url)];
  if (!await chrome.permissions.contains({origins})) return 'site_permission';
  const saved = await chrome.storage.local.get(captureSettings);
  const token = typeof message.token === 'string' && message.token.trim() || saved.token;
  if (attempt.cancelled) return;
  const receiverError = await ensureCaptureReceiver(Boolean(token));
  if (receiverError || attempt.cancelled) return receiverError;
  if (!token) return 'pairing_needed';
  const current = await requestedTab(message);
  if (!current?.active || current.url !== tab.url) return 'tab_changed';
  if (attempt.cancelled) return;
  await chrome.storage.session.set({status: '', statusTab: tab.id});
  if (attempt.cancelled) return;
  await chrome.storage.local.set({token, running: true, origins: [...new Set([...(saved.origins || []), ...origins])]});
  if (attempt.cancelled) return;
  await update(current);
  if (attempt.cancelled) return;
  if (pcvMedia.platform(current.url) && mediaSession?.tabId !== current.id) {
    if (mediaSession) await stopMedia();
    if (!attempt.cancelled) attempt.media = await startMedia(current, true);
  }
  if (!attempt.cancelled) attempt.attempted = true;
  await refresh(current.id);
}

async function captureControl(message) {
  if (message.action === 'stop') {
    if (captureStart) captureStart.cancelled = true;
    lastCaptureAttempt = undefined;
    captureStop ??= (async () => {
      await chrome.storage.local.set({running: false});
      await stopMedia();
      await refresh();
    })().finally(() => { captureStop = undefined; });
    await captureStop;
  } else if (message.action === 'start' && !captureStart && !captureStop) {
    const attempt = {tabId: message.tabId};
    captureStart = attempt;
    try { attempt.error = await enableCapture(message, attempt); }
    catch { attempt.error = 'failed'; }
    finally {
      captureStart = undefined;
      if (!attempt.cancelled) lastCaptureAttempt = attempt;
    }
    const view = await captureView(message);
    if (!attempt.cancelled) {
      if (attempt.error) view.error = attempt.error;
    }
    return view;
  }
  return captureView(message);
}
