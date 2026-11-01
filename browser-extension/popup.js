const status = document.querySelector('#status');
chrome.storage.session.get('status').then(x => { status.textContent = x.status || '尚未开始'; });
document.querySelector('#start').onclick = async () => {
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const url = new URL(tab.url);
    if (!['https:', 'http:'].includes(url.protocol)) throw new Error('仅支持网页');
    const granted = await chrome.permissions.request({origins: [url.origin + '/*']});
    if (!granted) throw new Error('未授权该站点');
    const saved = await chrome.storage.session.get(['token', 'enabled']);
    const token = document.querySelector('#token').value.trim() || saved.token;
    if (!token) throw new Error('请输入配对码');
    const enabled = saved.enabled || {};
    enabled[tab.id] = url.origin;
    await chrome.storage.session.set({token, enabled, status: '当前标签页在已授权站点持续采集'});
    await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['capture.js']});
    await chrome.action.setBadgeText({text: 'ON'});
    status.textContent = '已开始；本站跳转自动继续，跨站需再次授权';
  } catch (e) { status.textContent = e.message; }
};
document.querySelector('#stop').onclick = async () => {
  await chrome.storage.session.set({enabled: {}, status: '已暂停全部'});
  await chrome.action.setBadgeText({text: ''});
  status.textContent = '已暂停全部';
};
