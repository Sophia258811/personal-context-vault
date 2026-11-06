const toggle = document.querySelector('#toggle');
const status = document.querySelector('#status');
const detail = document.querySelector('#detail');
const settings = document.querySelector('#settings');
const pairing = document.querySelector('#pairing');
const token = document.querySelector('#token');
const scope = document.querySelector('#scope');
const pauseAll = document.querySelector('#pauseAll');
const allOrigins = ['https://*/*', 'http://*/*'];
const rejectedPairing = '配对码无效，请在设置中重新配对';
let tab;
let origin;
let view;
let allGranted = false;
let pending;
let error;
let revision = 0;
let poll;

const errors = {
  permission_denied: '网站授权未通过。可再次点击并选择允许。',
  site_permission: '当前站点尚未授权。请再次开始并允许采集。',
  ineligible: '请先打开普通 HTTP 或 HTTPS 网页。',
  tab_changed: '页面已变更，请在当前页面重新打开扩展。',
  pairing_needed: '请在设置中粘贴本机 PCV 配对码，再点击开始。',
  invalid_token: '配对码无效。请从本机 PCV 窗口复制当前配对码。',
  source_disabled: '请在本机 PCV 启用浏览器来源，再重试。',
  receiver_offline: '无法连接本机 PCV。请打开 PCV 后重试。',
  restart_needed: '请重启新版 PCV，再开始采集。',
  host_unavailable: '无法调用本机启动器。请运行 Start-PCV.ps1 或重新安装启动器。',
  launcher_unavailable: '无法调用本机启动器。请运行 Start-PCV.ps1 或重新安装启动器。',
  port_in_use: 'PCV 使用的端口已被占用。请关闭占用程序后重试。',
  start_failed: '本机 PCV 启动失败。请运行 Start-PCV.ps1 查看状态。',
  launch_failed: '本机 PCV 启动失败。请运行 Start-PCV.ps1 查看状态。',
  focus_required: '请回到视频所在标签页，从工具栏打开 PCV 后重试。',
  muted_tab: '请取消 Chrome 标签页的静音，再重试音画采集。',
  unavailable: '无法读取或更新采集状态，请重试。'
};
const mediaMessages = {
  starting: '正在连接当前标签页的音画。',
  running: '音画采集已开启。播放视频即可采集。',
  saved: '已保存最近音画片段。',
  busy: '另一标签页正在采集音画。可在设置中暂停全部采集。',
  unsupported: '音画采集需要 Chrome 116 或更新版本。',
  focus_required: errors.focus_required,
  muted_tab: errors.muted_tab,
  unavailable: '音画采集启动失败。可暂停后重试。',
  receiver_offline: errors.receiver_offline,
  fresh_tab_needed: '请用新标签页打开视频，从工具栏点 PCV，再点开始。',
  upload_failed: '音画片段未保存。请检查本机 PCV。',
  pairing_needed: errors.pairing_needed,
  source_disabled: errors.source_disabled,
  restart_needed: errors.restart_needed,
  quota_full: '本机音画存储已达上限，音画采集已停止。',
  permission_revoked: '网站权限已撤回，音画采集已停止。',
  focus_stopped: '已离开原标签页，音画采集已停止。',
  navigation_stopped: '原页面已变更，音画采集已停止。',
  stopped: '音画采集已停止。'
};
const mediaStages = {
  authorization: '检查页面授权', offscreen: '初始化扩展后台',
  tab_capture: '获取标签页音画权限', capture_handshake: '初始化标签页音画',
  get_user_media: '读取标签页音画', audio_resume: '启动音频播放',
  audio_worklet: '初始化音频编码', video_play: '初始化视频画面', player_monitor: '连接网页播放器'
};
const mediaReasons = {
  timeout: '超时', permission: '权限不足', missing: '找不到音画来源',
  unreadable: '浏览器无法读取', aborted: '浏览器中止', failed: '失败'
};

function messageFor(table, key) {
  return Object.hasOwn(table, key) ? table[key] : '';
}

function mediaDetail(media) {
  if (media?.state === 'unavailable') {
    const stage = messageFor(mediaStages, media.diagnostic?.stage);
    const reason = messageFor(mediaReasons, media.diagnostic?.reason);
    if (stage && reason) return `音画采集启动失败（${stage}，${reason}）。可暂停后重试。`;
  }
  return messageFor(mediaMessages, media?.state);
}

function primaryAction() {
  if (!view) return 'status';
  if (!origin) return view.running ? 'stop' : 'status';
  const expanding = scope.value === 'all' && !view.allSitesAuthorized;
  if (needsPairing(view)) return 'start';
  const currentActive = view.running && view.siteAuthorized &&
    (view.currentMedia || !view.mediaSupported || view.attempted);
  return currentActive && !expanding ? 'stop' : 'start';
}

function setText(element, value) {
  if (element.textContent !== value) element.textContent = value;
}

function needsPairing(result) {
  return ['pairing_needed', 'invalid_token'].includes(result?.error) || result?.textStatus === rejectedPairing;
}

function render() {
  const action = primaryAction();
  const busy = pending || view?.busy;
  toggle.disabled = Boolean(busy) || (!origin && !view?.running && Boolean(view));
  scope.disabled = Boolean(busy);
  pauseAll.disabled = Boolean(busy);
  pauseAll.hidden = !view?.running || action === 'stop';
  const issue = error || view?.error;
  pairing.hidden = !needsPairing(view) && view?.paired !== false;
  setText(toggle, busy ? (pending === 'stop' ? '正在暂停…' : pending ? '正在启动…' : '正在更新采集…')
    : action === 'stop' ? '暂停采集'
      : action === 'status' ? '刷新状态'
        : view.siteAuthorized ? '继续采集' : '开始采集');
  setText(status, !view ? '' : !origin ? errors.ineligible
    : view.running && view.siteAuthorized ? '当前站点文字采集已开启'
      : view.running ? '其他已授权站点的采集已开启' : '采集已暂停');
  const message = messageFor(errors, issue) || (issue ? errors.unavailable : '');
  const textStatus = view?.running && view.siteAuthorized ? view.textStatus : '';
  const mediaStatus = view?.running && view.mediaSupported ? mediaDetail(view.media) : '';
  setText(detail, [...new Set([message, textStatus, mediaStatus].filter(Boolean))].join(' '));
}

function send(action, extra = {}) {
  return chrome.runtime.sendMessage({type: 'capture-control', action, tabId: tab?.id, origin, ...extra});
}

function update(result) {
  if (!result || typeof result.running !== 'boolean') throw new Error('Invalid status');
  view = result;
  if (view.paired && !needsPairing(view)) token.value = '';
}

function scheduleRefresh() {
  clearTimeout(poll);
  if (view?.busy || view?.currentMedia) poll = setTimeout(refresh, 1000);
}

async function refresh() {
  if (pending) return;
  const generation = ++revision;
  try {
    const result = await send('status');
    if (generation !== revision) return;
    update(result);
    if (error === 'unavailable') error = undefined;
  } catch {
    if (generation !== revision) return;
    error = 'unavailable';
  }
  render();
  scheduleRefresh();
}

async function control(action) {
  if (pending || view?.busy) return;
  if (action === 'status') return refresh();
  ++revision;
  pending = action;
  error = undefined;
  const selectedScope = scope.value === 'all' ? 'all' : 'site';
  const pairCode = token.value.trim();
  render();
  try {
    if (action === 'start') {
      const grantNeeded = selectedScope === 'all' ? !allGranted : !view.siteAuthorized;
      if (grantNeeded) {
        // Request within the click handler before any asynchronous work.
        const granted = await chrome.permissions.request({
          origins: selectedScope === 'all' ? allOrigins : [`${origin}/*`]
        });
        if (!granted) {
          error = 'permission_denied';
          return;
        }
        if (selectedScope === 'all') allGranted = true;
      }
    }
    update(await send(action, action === 'start'
      ? {scope: selectedScope, ...(pairCode ? {token: pairCode} : {})} : {}));
    if (needsPairing(view)) {
      settings.open = true;
      render();
      token.focus();
    }
  } catch {
    error = 'unavailable';
  } finally {
    pending = undefined;
    render();
    scheduleRefresh();
  }
}

toggle.onclick = () => control(primaryAction());
pauseAll.onclick = () => control('stop');
scope.onchange = render;

async function initialize() {
  const [activeTab] = await chrome.tabs.query({active: true, currentWindow: true});
  tab = activeTab;
  if (/^https?:\/\//.test(tab?.url) && !tab.incognito) origin = new URL(tab.url).origin;
  allGranted = await chrome.permissions.contains({origins: allOrigins}).catch(() => false);
  chrome.storage.onChanged.addListener(refresh);
  await refresh();
}

initialize().catch(() => {
  error = 'unavailable';
  render();
});
