// Exercise service-worker decisions without opening a user's private browser.
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const handlers = {};
const state = {token: 'synthetic', enabled: {7: 'https://example.org'}};
let injections = 0;
let requests = 0;
const chrome = {
  runtime: {onMessage: {addListener: f => handlers.message = f}},
  tabs: {onUpdated: {addListener: f => handlers.updated = f}, onRemoved: {addListener: f => handlers.removed = f}},
  storage: {session: {get: async () => state, set: async x => Object.assign(state, x)}},
  permissions: {contains: async () => true},
  scripting: {executeScript: async () => injections++},
  action: {setBadgeText: async () => {}}
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../browser-extension/background.js'), 'utf8'), {
  chrome, URL, AbortSignal,
  fetch: async (_, options) => {
    requests++;
    assert.equal(JSON.parse(options.body).url, 'https://example.org/read');
    return {ok: true};
  }
});
(async () => {
  await handlers.updated(7, {status: 'complete'}, {url: 'https://example.org/next'});
  assert.equal(injections, 1);
  await handlers.updated(7, {status: 'complete'}, {url: 'https://other.example/'});
  assert.equal(injections, 1);
  const send = () => new Promise(resolve => handlers.message(
    {type: 'capture', event: {url: 'https://forged.example/', text: 'synthetic'}},
    {tab: {id: 7}, url: 'https://example.org/read'}, resolve));
  assert.equal((await send()).ok, true);
  state.enabled = {};
  assert.equal((await send()).paused, true);
  assert.equal(requests, 1);
  await handlers.updated(7, {status: 'complete'}, {url: 'https://example.org/next'});
  assert.equal(injections, 1);
  console.log('PASS: navigation, cross-origin isolation, sender URL, pause');
})().catch(error => { console.error(error); process.exitCode = 1; });
