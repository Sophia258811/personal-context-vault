const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const context = {URL};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../browser-extension/adapters.js'), 'utf8'), context);
const a = context.pcvAdapters;
assert.equal(a.identity('https://www.douyin.com/?modal_id=12345').platform_content_id, '12345');
assert.equal(a.identity('https://www.douyin.com/video/12345').platform_content_id, '12345');
assert.equal(a.identity('https://www.douyin.com/').platform_content_id, null);
assert.equal(a.identity('https://www.douyin.com/?modal_id=invalid').platform_content_id, null);
assert.equal(a.identity('https://evil-douyin.com/video/12345').platform_content_id, null);
assert.equal(a.identity('https://www.youtube.com/watch?v=abcdefghijk&tracking=x').platform_content_id, 'abcdefghijk');
assert.notEqual(a.identity('https://www.bilibili.com/video/BV123?p=1').platform_content_id,
                a.identity('https://www.bilibili.com/video/BV123?p=2').platform_content_id);
assert.equal(a.region({closest: s => s.includes('comment') ? {} : null}), 'comments');
assert.equal(a.region({closest: () => null}), 'unclassified');
assert.equal(a.media({readyState: 0}, 'now'), null);
const timing = a.media({readyState: 1, currentTime: 12.5, duration: Infinity, paused: false, playbackRate: 1}, 'now');
assert.equal(timing.position_seconds, 12.5);
assert.equal(timing.duration_seconds, null);
console.log('PASS: platform identities, ambiguous fallback, regions, media timestamps');
const card = {getAttribute: name => name === 'data-e2e-vid' ? '123456789' : null,
  querySelector: () => ({textContent: '24:54'})};
const active = {readyState: 1, currentTime: 66, duration: Infinity, paused: false, playbackRate: 1,
  closest: selector => selector.includes('feed-active-video') ? card : card};
const neighbour = {closest: () => null};
assert.equal(a.identity('https://www.douyin.com/?recommend=1', active).platform_content_id, '123456789');
assert.equal(a.selectVideo([neighbour, active]), active);
assert.equal(a.selectVideo([neighbour, neighbour]), null);
assert.equal(a.media(active, 'now').duration_seconds, 1494);
assert.equal(a.media(active, 'now').duration_source, 'player_time_label');
console.log('PASS: active feed card identity and MSE duration fallback');
