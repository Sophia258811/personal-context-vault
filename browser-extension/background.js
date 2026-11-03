async function allowed(url, settings) {
  if (!settings.running || !settings.token || !url) return false;
  const parsed = new URL(url);
  if (!['http:', 'https:'].includes(parsed.protocol) || ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname)) return false;
  const origin = parsed.origin + '/*';
  if (!(settings.origins || []).some(p => p === origin || p === parsed.protocol + '//*/*')) return false;
  return chrome.permissions.contains({origins: [origin]});
}
async function update(tab) {
  const settings = await chrome.storage.local.get(['running', 'token', 'origins']);
  const enabled = !tab.incognito && await allowed(tab.url, settings);
  await chrome.action.setBadgeText({tabId: tab.id, text: enabled ? 'ON' : ''});
  if (enabled) {
    try { await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['adapters.js', 'capture.js']}); }
    catch { await chrome.storage.session.set({status: '页面暂不可读取，刷新后重试'}); }
  }
}
async function refresh() {
  for (const tab of await chrome.tabs.query({})) await update(tab);
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  (async () => {
    if (message.type === 'refresh-capture' && !sender.tab) { await refresh(); return {ok: true}; }
    if (message.type !== 'capture' || !sender.tab) return {ok: false};
    const settings = await chrome.storage.local.get(['running', 'token', 'origins']);
    if (sender.tab.incognito || !await allowed(sender.url, settings)) return {ok: false, paused: true};
    const response = await fetch('http://127.0.0.1:8765/events', {
      method: 'POST', headers: {'Authorization': 'Bearer ' + settings.token, 'Content-Type': 'application/json'},
      body: JSON.stringify({...message.event, url: sender.url}), signal: AbortSignal.timeout(5000)
    });
    if (!response.ok) throw new Error('receiver');
    await chrome.storage.session.set({status: '已保存最近页面片段'});
    return {ok: true};
  })().then(respond).catch(async () => {
    await chrome.storage.session.set({status: '尚未连接：请双击 Start-PCV，或更新首次配对码'});
    respond({ok: false});
  });
  return true;
});
chrome.tabs.onUpdated.addListener((id, change, tab) => {
  if (change.status === 'complete') return update(tab).catch(() => {});
});
chrome.runtime.onStartup.addListener(() => refresh().catch(() => {}));
chrome.permissions.onRemoved.addListener(() => refresh().catch(() => {}));
