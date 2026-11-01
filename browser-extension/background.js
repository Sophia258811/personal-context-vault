chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.type !== 'capture' || !sender.tab) return;
  (async () => {
    const {token, enabled = {}} = await chrome.storage.session.get(['token', 'enabled']);
    if (!token || enabled[sender.tab.id] !== new URL(sender.url).origin) {
      respond({ok: false, paused: true});
      return;
    }
    try {
      const response = await fetch('http://127.0.0.1:8765/events', {
        method: 'POST', headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        body: JSON.stringify({...message.event, url: sender.url}),
        signal: AbortSignal.timeout(5000)
      });
      if (!response.ok) throw new Error('receiver');
      await chrome.storage.session.set({status: '已保存最近页面片段'});
      respond({ok: true});
    } catch {
      await chrome.storage.session.set({status: '保存失败：检查本地接收端与配对码'});
      respond({ok: false});
    }
  })();
  return true;
});
chrome.tabs.onUpdated.addListener(async (id, change, tab) => {
  if (change.status !== 'complete' || !tab.url) return;
  const {enabled = {}} = await chrome.storage.session.get('enabled');
  if (!enabled[id]) return;
  try {
    const origin = new URL(tab.url).origin;
    if (enabled[id] !== origin) {
      await chrome.action.setBadgeText({tabId: id, text: 'OFF'});
      return;
    }
    if (!await chrome.permissions.contains({origins: [origin + '/*']})) return;
    await chrome.scripting.executeScript({target: {tabId: id}, files: ['capture.js']});
    await chrome.action.setBadgeText({tabId: id, text: 'ON'});
  } catch {
    await chrome.storage.session.set({status: '页面无法注入，请重新开始'});
  }
});
chrome.tabs.onRemoved.addListener(async id => {
  const {enabled = {}} = await chrome.storage.session.get('enabled');
  delete enabled[id];
  await chrome.storage.session.set({enabled});
});
