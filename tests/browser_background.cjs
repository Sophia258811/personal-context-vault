// Exercise service-worker decisions without opening a user's private browser.
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const handlers = {};
const state = {token: 'synthetic', running: true, origins: ['https://example.org/*']};
const session = {};
const activeTab = {id: 10, windowId: 1, active: true, url: 'https://www.douyin.com/video/123'};
const workflow = [];
let receiverOnline = false;
let offscreenOpen = false;
let offscreenStart = {ok: true};
let launchWait;
let injections = 0;
let requests = 0;
const chrome = {
  runtime: {onMessage: {addListener: f => handlers.message = f}, onStartup: {addListener: f => handlers.startup = f},
    onInstalled: {addListener: f => handlers.installed = f},
    getContexts: async () => offscreenOpen ? [{}] : [],
    sendNativeMessage: async (host, message) => {
      assert.equal(host, 'org.pcv.launcher');
      assert.equal(message.action, 'start');
      workflow.push('launch');
      if (launchWait) await launchWait;
      receiverOnline = true;
      return {ok: true};
    },
    sendMessage: async message => {
      assert.equal(message.target, 'pcv-offscreen');
      workflow.push('offscreen-' + message.action);
      return message.action === 'start' ? offscreenStart : {ok: true};
    }},
  tabs: {onUpdated: {addListener: f => handlers.updated = f}, onRemoved: {addListener: f => handlers.removed = f},
    onActivated: {addListener: () => {}}, query: async () => [activeTab], get: async () => activeTab,
    sendMessage: async (_id, message) => { workflow.push(message.type); return {ok: true}; }},
  storage: {local: {get: async () => ({...state}), set: async x => Object.assign(state, x)},
    session: {get: async () => ({...session}), set: async x => Object.assign(session, x)}},
  permissions: {contains: async () => true, onRemoved: {addListener: f => handlers.permissions = f}},
  scripting: {executeScript: async () => { workflow.push('inject'); injections++; }},
  tabCapture: {getMediaStreamId: async ({targetTabId}) => {
    assert.equal(targetTabId, activeTab.id);
    workflow.push('tab-capture');
    return 'synthetic-stream';
  }},
  offscreen: {createDocument: async () => { offscreenOpen = true; },
    closeDocument: async () => { offscreenOpen = false; }},
  action: {setBadgeText: async () => {}}
};
chrome.runtime.getURL = name => 'chrome-extension://test/' + name;
chrome.windows = {onFocusChanged: {addListener: () => {}}, get: async () => ({focused: true})};
chrome.storage.onChanged = {addListener: () => {}};
const context = vm.createContext({
  chrome, URL, AbortSignal, AbortController, setTimeout, clearTimeout,
  crypto: require('node:crypto').webcrypto,
  fetch: async (url, options) => {
    if (url.endsWith('/media-ready')) {
      if (!receiverOnline) throw new Error('synthetic receiver offline');
      return {ok: true, status: 204};
    }
    requests++;
    assert.equal(JSON.parse(options.body).url, 'https://example.org/read');
    return {ok: true};
  }
});
const execute = file => vm.runInContext(fs.readFileSync(path.join(__dirname, '../browser-extension', file), 'utf8'), context);
context.importScripts = (...files) => files.forEach(execute);
execute('background.js');
(async () => {
  await handlers.updated(7, {status: 'complete'}, {id: 7, url: 'https://example.org/next'});
  assert.equal(injections, 1);
  await handlers.updated(7, {status: 'complete'}, {url: 'https://other.example/'});
  assert.equal(injections, 1);
  const send = () => new Promise(resolve => handlers.message(
    {type: 'capture', event: {url: 'https://forged.example/', text: 'synthetic'}},
    {tab: {id: 7}, url: 'https://example.org/read'}, resolve));
  assert.equal((await send()).ok, true);
  state.running = false;
  assert.equal((await send()).paused, true);
  assert.equal(requests, 1);
  await handlers.updated(7, {status: 'complete'}, {url: 'https://example.org/next'});
  assert.equal(injections, 1);
  state.running = true;
  state.origins = ['https://*/*'];
  await handlers.updated(8, {status: 'complete'}, {id: 8, url: 'https://new.example/'});
  assert.equal(injections, 2);
  await handlers.updated(9, {status: 'complete'}, {id: 9, url: 'https://new.example/', incognito: true});
  assert.equal(injections, 2);
  chrome.permissions.contains = async () => false;
  assert.equal((await send()).paused, true);
  assert.equal(requests, 1);
  console.log('PASS: navigation, cross-origin isolation, sender URL, pause');

  const origin = new URL(activeTab.url).origin;
  const control = (action, overrides = {}) => new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('No control response for ' + action)), 2000);
    handlers.message({type: 'capture-control', action, tabId: activeTab.id, origin, scope: 'site', ...overrides},
      {url: chrome.runtime.getURL('popup.html')}, result => { clearTimeout(timeout); resolve(result); });
  });
  state.running = false;
  state.origins = ['https://example.org/*'];
  workflow.length = 0;
  const denied = await control('start');
  assert.equal(denied.error, 'site_permission');
  assert.equal(state.running, false);
  assert.equal(workflow.length, 0);
  chrome.permissions.contains = async request => request.origins?.every(pattern =>
    pattern === origin + '/*' || pattern === 'https://example.org/*');
  assert.equal((await control('start', {scope: 'all'})).error, 'site_permission');
  assert.equal(workflow.length, 0, 'site grant must not authorize all sites');
  const started = await control('start');
  assert.equal(started.running, true);
  assert.equal(started.currentMedia, true);
  assert.equal(started.media.state, 'running');
  assert.deepEqual([...state.origins].sort(), [origin + '/*', 'https://example.org/*'].sort());
  assert.equal(workflow.filter(step => step === 'launch').length, 1);
  assert.equal(workflow.filter(step => step === 'tab-capture').length, 1);
  assert.equal(workflow.filter(step => step === 'offscreen-start').length, 1);
  assert.equal((await control('status')).currentMedia, true);
  const paused = await control('stop');
  assert.equal(paused.running, false);
  assert.equal(paused.currentMedia, false);
  assert.equal(offscreenOpen, false);
  assert.equal((await control('status')).running, false);

  offscreenStart = {ok: false, diagnostic: {stage: 'audio_worklet', reason: 'timeout'}};
  const degraded = await control('start');
  assert.equal(degraded.running, true, 'text capture should survive audio initialization failure');
  assert.equal(degraded.currentMedia, false);
  assert.equal(degraded.media.state, 'unavailable');
  assert.equal(degraded.media.diagnostic.stage, 'audio_worklet');
  assert.equal(degraded.media.diagnostic.reason, 'timeout');
  assert.equal(offscreenOpen, false);
  await control('stop');

  receiverOnline = false;
  workflow.length = 0;
  let finishLaunch;
  launchWait = new Promise(resolve => { finishLaunch = resolve; });
  const pendingStart = control('start');
  for (let turn = 0; turn < 20 && !workflow.includes('launch'); turn++) {
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.deepEqual(workflow, ['launch']);
  assert.equal((await control('start')).busy, true);
  assert.deepEqual(workflow, ['launch'], 'repeated start must not launch twice');
  const pendingStop = control('stop');
  await new Promise(resolve => setImmediate(resolve));
  finishLaunch();
  await Promise.all([pendingStart, pendingStop]);
  assert.equal((await control('status')).running, false);
  assert.equal(offscreenOpen, false);
  assert.deepEqual(workflow, ['launch'], 'late launcher must not re-enable text or audio');
  console.log('PASS: one start launches receiver, scopes text and media, reports audio failure, and honors pause during startup');
})().catch(error => { console.error(error); process.exitCode = 1; });
