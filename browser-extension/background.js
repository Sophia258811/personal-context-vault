importScripts('media-rules.js', 'media-session.js', 'capture-control.js');

function captureOrigin(url) {
  try {
    const parsed = new URL(url);
    if (['http:', 'https:'].includes(parsed.protocol) && !['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname)) {
      return parsed.origin + '/*';
    }
  } catch { /* No ordinary page is open. */ }
  return null;
}
async function siteAuthorized(url, settings) {
  const origin = captureOrigin(url);
  if (!origin || !(settings.origins || []).some(p => p === origin || p === new URL(url).protocol + '//*/*')) return false;
  return chrome.permissions.contains({origins: [origin]});
}
async function allowed(url, settings) {
  return Boolean(settings.running && settings.token && await siteAuthorized(url, settings));
}
async function update(tab) {
  const settings = await chrome.storage.local.get(['running', 'token', 'origins']);
  const enabled = !tab.incognito && await allowed(tab.url, settings);
  await chrome.action.setBadgeText({tabId: tab.id, text: enabled ? 'ON' : ''});
  if (enabled) {
    try { await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['adapters.js', 'capture.js']}); }
    catch { await chrome.storage.session.set({status: '页面暂不可读取，刷新后重试', statusTab: tab.id}); }
  }
}
async function refresh(skipTab) {
  for (const tab of await chrome.tabs.query({})) if (tab.id !== skipTab) await update(tab);
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.target === 'pcv-offscreen') return false;
  if (message.target === 'pcv-media') {
    mediaMessage(message, sender).then(respond, () => respond({ok: false}));
    return true;
  }
  if (message.type === 'capture-control') {
    if (sender.tab || sender.url !== mediaPopup) return false;
    captureControl(message).then(respond, () => respond({error: 'failed'}));
    return true;
  }
  (async () => {
    if (message.type !== 'capture' || !sender.tab) return {ok: false};
    const settings = await chrome.storage.local.get(['running', 'token', 'origins']);
    if (sender.tab.incognito || !await allowed(sender.url, settings)) return {ok: false, paused: true};
    const response = await fetch('http://127.0.0.1:8765/events', {
      method: 'POST', headers: {'Authorization': 'Bearer ' + settings.token, 'Content-Type': 'application/json'},
      body: JSON.stringify({...message.event, url: sender.url}), signal: AbortSignal.timeout(5000)
    });
    if (response.status === 401) {
      await chrome.storage.session.set({status: '配对码无效，请在设置中重新配对', statusTab: sender.tab.id});
      return {ok: false};
    }
    if (!response.ok) throw new Error('receiver');
    await chrome.storage.session.set({status: '已保存最近页面片段', statusTab: sender.tab.id});
    return {ok: true};
  })().then(respond).catch(async () => {
    await chrome.storage.session.set({status: '未收到页面片段，请检查本机 PCV', statusTab: sender.tab?.id ?? null});
    respond({ok: false});
  });
  return true;
});
chrome.tabs.onUpdated.addListener((id, change, tab) => {
  if (change.status === 'complete') return update(tab).catch(() => {});
});
chrome.runtime.onStartup.addListener(() => refresh().catch(() => {}));
chrome.runtime.onInstalled.addListener(() => refresh().catch(() => {}));
chrome.permissions.onRemoved.addListener(() => refresh().catch(() => {}));
