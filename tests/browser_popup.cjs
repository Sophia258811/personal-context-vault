const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../browser-extension/popup.js'), 'utf8');
const tab = {id: 7, url: 'https://www.douyin.com/video/123'};
const origin = new URL(tab.url).origin;
const messages = [];
const requests = [];
const granted = new Set();
const status = {running: false, paired: true, siteAuthorized: false, mediaSupported: true,
  currentMedia: false, attempted: false, busy: false, media: {state: 'idle'},
  textStatus: '已保存最近页面片段'};
let allow = true;
let mediaResult = {state: 'running'};
let statusWait;

async function open() {
  const elements = Object.fromEntries(['toggle', 'status', 'detail', 'settings', 'pairing', 'token',
    'scope', 'pauseAll'].map(id => ['#' + id, {textContent: '', value: id === 'scope' ? 'site' : '',
      focus() { this.focused = true; }, addEventListener(event, handler) { this['on' + event] = handler; }}]));
  const chrome = {
    tabs: {query: async () => [tab]},
    storage: {onChanged: {addListener: callback => { elements.refresh = callback; }}},
    permissions: {
      contains: async request => request.origins.every(pattern => granted.has(pattern)),
      request: async request => {
        assert.ok(request.origins, 'popup must only request the chosen site scope');
        requests.push([...request.origins]);
        if (allow) request.origins.forEach(pattern => granted.add(pattern));
        return allow;
      },
    },
    runtime: {sendMessage: async message => {
      assert.equal(message.type, 'capture-control');
      assert.equal(message.tabId, tab.id);
      assert.equal(message.origin, origin);
      messages.push({...message});
      status.siteAuthorized = granted.has(origin + '/*');
      if (message.action === 'start') {
        if (!status.paired && !message.token) return {...status, error: 'pairing_needed'};
        Object.assign(status, {paired: true, running: true, currentMedia: mediaResult.state === 'running',
          attempted: true, media: mediaResult, textStatus: '已保存最近页面片段'});
      } else if (message.action === 'stop') {
        Object.assign(status, {running: false, currentMedia: false, media: {state: 'stopped'}});
      }
      const snapshot = {...status};
      if (message.action === 'status' && statusWait) await statusWait;
      return snapshot;
    }},
  };
  vm.runInNewContext(source, {chrome, URL, setTimeout: () => 0, clearTimeout: () => {},
    document: {querySelector: key => elements[key]}});
  await new Promise(resolve => setImmediate(resolve));
  return elements;
}

(async () => {
  let elements = await open();
  assert.match(elements['#toggle'].textContent, /开始|继续/);
  assert.doesNotMatch(elements['#status'].textContent, /采集已开启/);
  allow = false;
  await elements['#toggle'].onclick();
  assert.equal(messages.some(message => message.action === 'start'), false);
  allow = true;
  await elements['#toggle'].onclick();
  assert.deepEqual(requests, [[origin + '/*'], [origin + '/*']]);
  assert.equal(messages.filter(message => message.action === 'start').length, 1);
  assert.equal(messages.find(message => message.action === 'start').scope, 'site');
  assert.match(elements['#toggle'].textContent, /暂停/);

  elements = await open();
  assert.match(elements['#toggle'].textContent, /暂停/);
  let finishRefresh;
  statusWait = new Promise(resolve => { finishRefresh = resolve; });
  const oldStatus = elements.refresh();
  await elements['#toggle'].onclick();
  finishRefresh();
  await oldStatus;
  statusWait = undefined;
  assert.equal(messages.at(-1).action, 'stop');
  assert.match(elements['#toggle'].textContent, /开始|继续/);
  assert.doesNotMatch(elements['#status'].textContent, /采集已开启/);

  mediaResult = {state: 'unavailable', diagnostic: {stage: 'audio_worklet', reason: 'timeout'}};
  await elements['#toggle'].onclick();
  const report = [elements['#status'].textContent, elements['#detail'].textContent].join(' ');
  assert.match(report, /文字/);
  assert.match(report, /初始化音频编码/);
  assert.match(report, /超时/);
  assert.match(elements['#toggle'].textContent, /暂停/);
  assert.equal(requests.length, 2, 'restarting an authorized site should not request new permissions');
  await elements['#toggle'].onclick();

  status.paired = false;
  elements = await open();
  await elements['#toggle'].onclick();
  assert.equal(elements['#settings'].open, true);
  assert.equal(elements['#token'].focused, true);
  assert.match(elements['#toggle'].textContent, /开始|继续/);
  console.log('PASS: single capture control, site-scoped consent, status on reopen, and distinct audio and pairing diagnostics');
})().catch(error => { console.error(error); process.exitCode = 1; });
