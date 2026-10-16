const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/static/app.js'), 'utf8');
function setup() {
  const cards = [];
  const context = {
    URL, window: {}, document: { querySelectorAll() { return cards; } },
    setTimeout() {},
  };
  vm.runInNewContext(source.slice(0, source.lastIndexOf('\ninitializeFontSettings();')), context);
  return {context, cards};
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

(async () => {
  const {context, cards} = setup();
  const container = { querySelectorAll() { return []; } };
  context.renderSources(container, [{platform: 'bilibili', uid: '100', name: '博主', avatar: 'https://i0.hdslb.com/author.jpg'}]);
  assert.match(container.innerHTML, /source-avatar-image/);
  assert.match(container.innerHTML, /https:\/\/i0.hdslb.com\/author.jpg/);
  assert.ok(container.innerHTML.indexOf('source-avatar') < container.innerHTML.indexOf('source-name'));
  assert.doesNotMatch(container.innerHTML, /source-platform|platform-icon|\/static\/icons/);
  assert.match(container.innerHTML, /referrerpolicy="no-referrer"/);

  for (const url of ['javascript:alert(1)', 'file:///C:/secret', 'https://user:secret@example.com/a']) {
    const html = context.sourceAvatarContents({name: '<script>', avatar: url});
    assert.doesNotMatch(html, /<img|<script>/);
    assert.match(html, /&lt;/);
  }
  context.window.CyberHost = {showRemoteAvatars: false};
  assert.doesNotMatch(context.sourceAvatarContents({name: '博主', avatar: 'https://i0.hdslb.com/a'}), /<img/);
  context.window.CyberHost = {};

  let errorHandler, removed = false;
  const image = {addEventListener(event, handler) { assert.equal(event, 'error'); errorHandler = handler; }, remove() { removed = true; }};
  context.bindSourceAvatarFallbacks({querySelectorAll() { return [image]; }});
  errorHandler();
  assert.equal(removed, true);

  vm.runInNewContext('state.currentView = "sources"; state.sourcesStage = "sources"; state.selectedPlatform = "bilibili"; state.sources = [{platform:"bilibili", uid:"100", name:"博主"}];', context);
  const sourceItem = vm.runInNewContext('state.sources[0]', context);
  const slot = {};
  cards.push({dataset: {platform: 'bilibili', uid: '100'}, querySelector() { return slot; }, querySelectorAll() { return []; }});
  let called = 0;
  const response = deferred();
  context.api = async (url, options) => {
    called++;
    assert.equal(url, '/api/sources/bilibili/100/avatar');
    assert.equal(options.method, 'POST');
    return response.promise;
  };
  const first = context.loadSourceAvatars([sourceItem]);
  const second = context.loadSourceAvatars([sourceItem]);
  response.resolve({avatar: 'https://i0.hdslb.com/author.jpg'});
  await Promise.all([first, second]);
  assert.equal(called, 1);
  assert.match(slot.innerHTML, /author.jpg/);
  assert.equal(sourceItem.avatar, 'https://i0.hdslb.com/author.jpg');

  const late = setup();
  vm.runInNewContext('state.currentView = "sources"; state.sourcesStage = "sources"; state.selectedPlatform = "bilibili"; state.sources = [{platform:"bilibili", uid:"100", name:"博主"}];', late.context);
  const lateSlot = {innerHTML: 'current platform'};
  late.cards.push({dataset: {platform: 'bilibili', uid: '100'}, querySelector() { return lateSlot; }, querySelectorAll() { return []; }});
  const pending = deferred();
  late.context.api = () => pending.promise;
  const job = late.context.loadSourceAvatars(vm.runInNewContext('state.sources', late.context));
  vm.runInNewContext('state.selectedPlatform = "zhihu";', late.context);
  pending.resolve({avatar: 'https://i0.hdslb.com/late.jpg'});
  await job;
  assert.equal(lateSlot.innerHTML, 'current platform');

  console.log('Source avatar checks passed (author image, no platform logo, fallback, URL validation, deduplication and late-response isolation).');
})().catch(error => { console.error(error); process.exitCode = 1; });
