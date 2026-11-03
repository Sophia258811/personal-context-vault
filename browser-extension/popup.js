const status = document.querySelector('#status');
const detail = document.querySelector('#detail');
let renderGeneration = 0;
async function render() {
  const generation = ++renderGeneration;
  const [settings, recent] = await Promise.all([
    chrome.storage.local.get(['running', 'token']), chrome.storage.session.get('status')
  ]);
  if (generation !== renderGeneration) return;
  // The persistent switch is authoritative; a previous transfer message is not.
  status.textContent = settings.running ? '采集已开启（仅已授权范围）' : '采集已暂停';
  detail.textContent = settings.running && recent.status && !recent.status.includes('暂停') ? recent.status : '';
  if (settings.token) document.querySelector('#token').placeholder = '已配对，无需重复输入';
}
render().catch(() => { status.textContent = '无法读取配置，请重新加载扩展'; });
document.querySelector('#start').onclick = async () => {
  ++renderGeneration;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const all = document.querySelector('#scope').value === 'all';
    if (!all && !/^https?:/.test(tab.url)) throw new Error('请先打开普通网页');
    const origins = all ? ['https://*/*', 'http://*/*'] : [new URL(tab.url).origin + '/*'];
    if (!await chrome.permissions.request({origins})) throw new Error('未授权，尚未开始');
    const saved = await chrome.storage.local.get(['token', 'origins']);
    const token = document.querySelector('#token').value.trim() || saved.token;
    if (!token) throw new Error('首次请从 PCV 窗口复制配对码');
    await chrome.storage.local.set({token, running: true, origins: [...new Set([...(saved.origins || []), ...origins])]});
    await chrome.storage.session.set({status: '等待页面发送；开启不代表已收到记录'});
    await render();
    await chrome.runtime.sendMessage({type: 'refresh-capture'});
  } catch (e) { detail.textContent = e.message; }
};
document.querySelector('#stop').onclick = async () => {
  ++renderGeneration;
  await chrome.storage.local.set({running: false});
  await chrome.storage.session.set({status: '已暂停全部'});
  await render();
  await chrome.runtime.sendMessage({type: 'refresh-capture'});
};
