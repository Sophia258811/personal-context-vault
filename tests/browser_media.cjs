const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const context = vm.createContext({URL});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../browser-extension/media-rules.js'), 'utf8'), context);
const {platform, valid, continues, crop, wav16} = context.pcvMedia;
assert.equal(platform('https://www.douyin.com/video/123'), 'douyin');
assert.equal(platform('https://douyin.com.example.org/video/123'), null);
assert.equal(platform('http://www.youtube.com/watch'), null);
const start = {at: 1000, platform: 'douyin', platform_content_id: '123', position_seconds: 4, rate: 1, audible: true,
  viewport: {width: 1000, height: 500}, rect: {left: .1, top: .2, right: .6, bottom: .8}};
const next = {...start, at: 1500, position_seconds: 4.5};
assert.equal(valid(start), true);
assert.equal(valid({...start, platform_content_id: ''}), false);
assert.equal(continues(start, next), true);
for (const broken of [
  null, {...next, platform_content_id: '456'}, {...next, position_seconds: 4},
  {...next, position_seconds: 30}, {...next, at: 2300}, {...next, rate: 2}, {...next, audible: false},
]) assert.equal(continues(start, broken), false);
assert.deepEqual({...crop(start, 2000, 1000)}, {x: 200, y: 200, w: 1000, h: 600});
assert.equal(crop(start, 1920, 1080), null, 'aspect mismatch must not capture an unrelated rectangle');

const wav = Buffer.from(wav16([new Float32Array(48000).fill(.5)], 48000, .5));
assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
assert.equal(wav.toString('ascii', 8, 12), 'WAVE');
assert.equal(wav.readUInt16LE(22), 1);
assert.equal(wav.readUInt32LE(24), 16000);
assert.equal(wav.readUInt16LE(34), 16);
assert.equal(wav.readUInt32LE(40), 16000);
assert.equal(wav.readInt16LE(44), 16383);
assert.equal(wav16([], 48000, .5), null);
console.log('PASS: media identity, pause/seek/stale gating, crop, mono WAV encoding');

async function testWorker() {
  const noop = {addListener() {}};
  const extension = 'chrome-extension://synthetic/';
  const tab = {id: 7, windowId: 1, active: true, url: 'https://www.douyin.com/video/123?tracking=private'};
  let opened = false;
  let focused = true;
  let status = 200;
  let readiness = 204;
  let pendingReadiness;
  let streamError;
  let captureResult = {ok: true};
  let requests = 0;
  let pendingCheck;
  let session;
  const chrome = {
    runtime: {
      getURL: name => extension + name,
      getContexts: async () => opened ? [{}] : [],
      sendMessage: async message => {
        if (message.action === 'start') {
          session = message.session;
          return captureResult;
        }
        return {ok: true};
      }
    },
    tabs: {
      query: async () => [tab], get: async () => { if (pendingCheck) await pendingCheck; return tab; },
      sendMessage: async () => {}, onActivated: noop, onRemoved: noop, onUpdated: noop
    },
    windows: {get: async () => ({focused}), onFocusChanged: noop},
    storage: {local: {get: async () => ({running: true, token: 'synthetic'})}, onChanged: noop},
    offscreen: {
      createDocument: async () => { opened = true; },
      closeDocument: async () => { opened = false; }
    },
    permissions: {onRemoved: noop},
    scripting: {executeScript: async () => {}},
    tabCapture: {getMediaStreamId: async () => {
      if (streamError) throw streamError;
      return 'synthetic-stream';
    }}
  };
  Object.assign(context, {
    chrome, allowed: async () => true, AbortController, AbortSignal, TextEncoder, setTimeout, clearTimeout,
    crypto: {randomUUID: () => 'synthetic-session'},
    fetch: async (url, options) => {
      if (url === 'http://127.0.0.1:8765/media-ready') {
        assert.equal(options.method, 'GET');
        assert.equal(options.credentials, 'omit');
        assert.equal(options.headers, undefined);
        if (pendingReadiness) await pendingReadiness;
        if (readiness === null) throw new TypeError('Connection refused');
        return {status: readiness};
      }
      requests++;
      assert.equal(url, 'http://127.0.0.1:8765/media');
      assert.equal(options.headers.Authorization, 'Bearer synthetic');
      assert.equal(JSON.parse(options.body).url, 'https://www.douyin.com/video/123');
      return {ok: status === 200, status};
    }
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../browser-extension/media-session.js'), 'utf8'), context);
  const call = (action, sender, data = {}) => context.mediaMessage({action, session, ...data}, sender);
  const popup = {url: extension + 'popup.html'};
  const page = {tab, url: tab.url, frameId: 0};
  const offscreen = {url: extension + 'media-offscreen.html'};
  const upload = () => call('upload', offscreen, {segment: {
    id: 'id', platform_content_id: '123', start_seconds: 0, end_seconds: 4, frames: []
  }});

  assert.equal((await call('start', page)).ok, false);
  focused = false;
  assert.equal((await call('start', popup)).state, 'ineligible');
  focused = true;
  readiness = 404;
  assert.equal((await call('start', popup)).state, 'restart_needed');
  readiness = null;
  assert.equal((await call('start', popup)).state, 'receiver_offline');
  assert.equal(opened, false, 'unavailable receiver must not open an offscreen document');
  readiness = 204;
  streamError = new Error('Extension has not been invoked for the current page (see activeTab permission).');
  assert.equal((await call('start', popup)).state, 'fresh_tab_needed');
  streamError = undefined;
  captureResult = {ok: false, diagnostic: {stage: 'audio_resume', reason: 'timeout'}};
  const failedStart = await call('start', popup);
  assert.equal(failedStart.state, 'unavailable');
  assert.equal(failedStart.diagnostic.stage, 'audio_resume');
  assert.equal(opened, false);
  captureResult = {ok: true};

  let unblockReadiness;
  let enteredReadiness;
  const inReadiness = new Promise(resolve => { enteredReadiness = resolve; });
  pendingReadiness = {then(resolve) { enteredReadiness(); unblockReadiness = resolve; }};
  const pendingStart = call('start', popup);
  await inReadiness;
  await call('stop', popup);
  unblockReadiness();
  assert.equal((await pendingStart).state, 'stopped', 'startup must preserve the explicit stop state');
  assert.equal(opened, false);
  pendingReadiness = undefined;
  assert.equal((await call('start', popup)).state, 'running');
  await call('beat', page, {beat: start});
  assert.equal((await upload()).ok, true);
  await call('beat', page, {beat: null});
  assert.equal((await upload()).ok, false, 'invalid player state must reject even a late upload');
  await call('beat', page, {beat: start});
  status = 507;
  assert.equal((await upload()).ok, false);
  assert.equal((await call('status', popup)).state, 'quota_full');
  assert.equal(opened, false);

  status = 200;
  assert.equal((await call('start', popup)).state, 'running');
  await call('beat', page, {beat: start});
  let unblock;
  pendingCheck = new Promise(resolve => { unblock = resolve; });
  const racing = upload();
  await call('stop', popup);
  unblock();
  assert.equal((await racing).ok, false, 'stop must invalidate uploads awaiting an authorization check');
  assert.equal(opened, false);
  assert.equal(requests, 2);
  console.log('PASS: explicit opt-in, foreground, receiver quota, stop/upload race');
}

testWorker().catch(error => { console.error(error); process.exitCode = 1; });
