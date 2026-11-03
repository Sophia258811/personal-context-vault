const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../browser-extension/popup.js'), 'utf8');
const state = {running: true, token: 'synthetic', origins: ['https://example.org/*']};
const session = {status: '已暂停全部'};
let elements;
async function open(delay) {
  elements = Object.fromEntries(['status','detail','token','scope','start','stop'].map(k => ['#' + k, {value: k === 'scope' ? 'site' : '', textContent: ''}]));
  const chrome = {
    storage: {
      local: {get: async () => { if (delay) await new Promise(r => setTimeout(r, delay)); return {...state}; }, set: async x => Object.assign(state,x)},
      session: {get: async () => ({...session}), set: async x => Object.assign(session,x)}
    },
    tabs: {query: async () => [{url: 'https://example.org/'}]},
    permissions: {request: async () => true}, runtime: {sendMessage: async () => ({ok:true})}
  };
  const context = {chrome, URL, document: {querySelector: k => elements[k]}};
  vm.runInNewContext(source, context);
  await new Promise(r => setTimeout(r, delay + 10));
}
(async () => {
  for (const delay of [0,20]) {
    await open(delay);
    assert.match(elements['#status'].textContent, /已开启/);
    assert.equal(elements['#detail'].textContent, '');
  }
  await elements['#stop'].onclick();
  assert.equal(state.running, false);
  await elements['#start'].onclick();
  assert.equal(state.running, true);
  assert.ok(!session.status.includes('暂停'));
  await open(0);
  assert.match(elements['#status'].textContent, /已开启/);
  state.running = false;
  session.status = '已保存最近页面片段';
  await open(0);
  assert.match(elements['#status'].textContent, /已暂停/);
  assert.equal(elements['#detail'].textContent, '');
  console.log('PASS: stale pause, reopen, asynchronous order, stop/start, stale success');
})().catch(error => {console.error(error); process.exitCode=1;});
