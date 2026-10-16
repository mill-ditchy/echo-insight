const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../web/static/index.html'), 'utf8');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function node() {
  const classes = new Set();
  return {
    textContent: '', innerHTML: '', value: '', hidden: false, disabled: false,
    style: {setProperty() {}}, listeners: new Map(),
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
      toggle(name, enabled) {
        enabled = enabled === undefined ? !classes.has(name) : enabled;
        if (enabled) classes.add(name); else classes.delete(name);
        return enabled;
      },
    },
    setAttribute() {}, removeAttribute() {}, focus() {}, appendChild() {},
    addEventListener(type, listener) { this.listeners.set(type, listener); },
    querySelectorAll() { return []; },
    querySelector() { return node(); },
  };
}

function harness() {
  // Run the complete production script, including event binding. Static ID
  // lookups must match the real HTML rather than silently creating missing nodes.
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => ['#' + match[1], node()]));
  nodes.set('.app-shell', node());
  const settingsPanels = [...html.matchAll(/<div\b[^>]*\bdata-settings-panel="([^"]+)"[^>]*>/g)].map(match => {
    const id = match[0].match(/\bid="([^"]+)"/);
    const panel = id ? nodes.get('#' + id[1]) : node();
    panel.dataset = {settingsPanel: match[1]};
    panel.hidden = /\bhidden\b/.test(match[0]);
    const headingMatch = html.slice(match.index + match[0].length).match(/<h3([^>]*)>([^<]*)<\/h3>/);
    const headingId = headingMatch[1].match(/\bid="([^"]+)"/);
    const heading = headingId ? nodes.get('#' + headingId[1]) : node();
    heading.textContent = headingMatch[2];
    panel.querySelector = selector => selector === 'h3' ? heading : node();
    return panel;
  });
  const settingsLinks = [...html.matchAll(/<button\b[^>]*\bdata-settings-page="([^"]+)"[^>]*>/g)].map(match => {
    const button = node();
    button.dataset = {settingsPage: match[1]};
    return button;
  });
  const requests = [], timers = new Map();
  let timerId = 0;
  const context = vm.createContext({
    URL,
    document: {
      documentElement: node(), body: node(),
      querySelector(selector) {
        assert.ok(nodes.has(selector), 'Missing HTML node: ' + selector);
        return nodes.get(selector);
      },
      querySelectorAll(selector) {
        if (selector === '[data-settings-panel]') return settingsPanels;
        if (selector === '[data-settings-page]') return settingsLinks;
        return [];
      },
      createElement: node, addEventListener() {},
    },
    localStorage: {getItem() { return null; }},
    MutationObserver: class { observe() {} },
    requestAnimationFrame(callback) { callback(); },
    setInterval() {},
    setTimeout(callback, delay) { timers.set(++timerId, {callback, delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    window: {
      scrollTo() {}, matchMedia() { return {matches: false, addEventListener() {}}; },
      CyberHost: {request(url, options = {}) {
        if (url === '/api/dashboard') return Promise.resolve({updates: [], unread_updates: [], unread_count: 0});
        if (url === '/api/sources') return Promise.resolve({sources: []});
        const request = {...deferred(), url, options};
        requests.push(request);
        return request.promise;
      }},
    },
  });
  vm.runInContext(source, context);
  return {context, state: vm.runInContext('state', context), nodes, requests, timers};
}

function detail(uid, videos = []) {
  return {source: {platform: 'xiaohongshu', uid, name: uid, unread_count: 0}, videos,
    has_more: true, next_offset: 200};
}

async function display(h, uid, videos = []) {
  const pending = h.context.loadSourceDetail('xiaohongshu', uid);
  h.requests.at(-1).resolve(detail(uid, videos));
  assert.equal(await pending, true);
}

async function check(name, test) {
  await test();
  console.log('Passed:', name);
}

async function main() {
  for (const order of [[0, 1], [1, 0]]) {
    await check('latest source wins, response order ' + order.join(','), async () => {
      const h = harness();
      const pending = [h.context.loadSourceDetail('xiaohongshu', 'A'), h.context.loadSourceDetail('xiaohongshu', 'B')];
      assert.equal(h.state.sourceUid, null, 'pending requests do not change the displayed identity');
      for (const index of order) {
        h.requests[index].resolve(detail(index ? 'B' : 'A'));
        assert.equal(await pending[index], Boolean(index));
      }
      assert.equal(h.state.sourceUid, 'B');
      assert.equal(h.state.sourceName, 'B');
      assert.equal(h.state.sourceDetailData.source.uid, 'B');
      assert.equal(h.nodes.get('#detail-source-name').textContent, 'B');
    });
  }

  await check('navigation cancels an unfinished detail request', async () => {
    const h = harness();
    const pending = h.context.loadSourceDetail('xiaohongshu', 'A');
    h.context.setView('accounts');
    h.requests[0].resolve(detail('A'));
    assert.equal(await pending, false);
    assert.equal(h.state.currentView, 'accounts');
    assert.equal(h.state.sourceUid, null);
  });

  await check('old request errors do not replace current feedback', async () => {
    const h = harness();
    const old = h.context.loadSourceDetail('xiaohongshu', 'A');
    await display(h, 'B');
    h.requests[0].reject(new Error('old request failed'));
    assert.equal(await old, false);
    assert.equal(h.nodes.get('#toast').textContent, '');
    assert.equal(h.state.sourceUid, 'B');
  });

  await check('old polling callback cannot reopen its source', async () => {
    const h = harness();
    await display(h, 'A', [{platform: 'xiaohongshu', article_id: 'a', content_type: 'video', ai_status: 'processing'}]);
    const timer = h.timers.get(h.state.sourceTimer);
    const next = h.context.loadSourceDetail('xiaohongshu', 'B');
    timer.callback(); // Even if a cancelled callback was already ready to run.
    assert.equal(h.requests.length, 2);
    h.requests[1].resolve(detail('B'));
    await next;
  });

  await check('late history page cannot append to a different source', async () => {
    const h = harness();
    await display(h, 'A');
    const button = h.nodes.get('#source-load-more');
    const page = button.listeners.get('click')({currentTarget: button});
    const request = h.requests.at(-1);
    assert.ok(request.url.includes('source_uid=A'));
    await display(h, 'B');
    request.resolve({items: [{article_id: 'old-A'}], has_more: false, next_offset: 201});
    await page;
    assert.equal(h.state.sourceDetailData.videos.length, 0);
    assert.equal(h.state.sourceDetailData.source.uid, 'B');
    assert.equal(button.hidden, false);
    assert.equal(button.disabled, false);
  });

  await check('paging keeps the Xiaohongshu content filter', async () => {
    const h = harness();
    await display(h, 'A');
    h.context.showSourceContentType('image_note');
    const button = h.nodes.get('#source-load-more');
    const page = button.listeners.get('click')({currentTarget: button});
    h.requests.at(-1).resolve({items: [
      {platform: 'xiaohongshu', article_id: 'image', content_type: 'image_note', title: 'wanted-image'},
      {platform: 'xiaohongshu', article_id: 'video', content_type: 'video', title: 'excluded-video'},
    ], has_more: false, next_offset: 202});
    await page;
    assert.equal(h.state.sourceDetailData.videos.length, 2);
    assert.ok(h.nodes.get('#source-videos').innerHTML.includes('wanted-image'));
    assert.ok(!h.nodes.get('#source-videos').innerHTML.includes('excluded-video'));
  });

  await check('failed navigation leaves paging usable without accepting the old page', async () => {
    const h = harness();
    await display(h, 'A');
    const button = h.nodes.get('#source-load-more');
    const click = button.listeners.get('click');
    const oldPage = click({currentTarget: button});
    const oldRequest = h.requests.at(-1);
    const next = h.context.loadSourceDetail('xiaohongshu', 'B');
    h.requests.at(-1).reject(new Error('B unavailable'));
    assert.equal(await next, false);
    assert.equal(h.state.sourceUid, 'A');
    assert.equal(button.disabled, false);
    const newPage = click({currentTarget: button});
    const newRequest = h.requests.at(-1);
    oldRequest.resolve({items: [{article_id: 'stale'}], has_more: false});
    await oldPage;
    assert.equal(button.disabled, true, 'an old completion cannot re-enable a newer request');
    assert.equal(h.state.sourceDetailData.videos.length, 0);
    newRequest.resolve({items: [{platform: 'xiaohongshu', article_id: 'current', title: 'current'}], has_more: false});
    await newPage;
    assert.equal(button.disabled, false);
    assert.equal(h.state.sourceDetailData.videos[0].article_id, 'current');
  });

  await check('delete uses the modal target and preserves a newer detail', async () => {
    const h = harness();
    await display(h, 'A');
    h.context.openDeleteSourceModal();
    await display(h, 'B');
    const deletion = h.context.confirmDeleteSource();
    const request = h.requests.at(-1);
    assert.equal(request.url, '/api/sources/xiaohongshu/A');
    assert.equal(request.options.method, 'DELETE');
    request.resolve({message: 'removed A'});
    await deletion;
    assert.equal(h.state.currentView, 'source-detail');
    assert.equal(h.state.sourceUid, 'B');
    assert.equal(h.state.sourceDetailData.source.uid, 'B');
  });

  await check('delete completion cannot cancel navigation still loading', async () => {
    const h = harness();
    await display(h, 'A');
    h.context.openDeleteSourceModal();
    const next = h.context.loadSourceDetail('xiaohongshu', 'B');
    const nextRequest = h.requests.at(-1);
    const deletion = h.context.confirmDeleteSource();
    h.requests.at(-1).resolve({message: 'removed A'});
    await deletion;
    nextRequest.resolve(detail('B'));
    assert.equal(await next, true);
    assert.equal(h.state.sourceUid, 'B');
  });

  await check('deleting the current source returns to the source list', async () => {
    const h = harness();
    await display(h, 'A');
    h.context.openDeleteSourceModal();
    const deletion = h.context.confirmDeleteSource();
    h.requests.at(-1).resolve({message: 'removed A'});
    await deletion;
    assert.equal(h.state.currentView, 'sources');
    assert.equal(h.state.sourceDetailData, null);
    assert.equal(h.state.sourceUid, null);
    assert.equal(h.state.deleteSourceTarget, null);
  });

  await check('late retry completion cannot reopen an old source', async () => {
    const h = harness();
    await display(h, 'A');
    const retry = h.context.retrySourceSummaries();
    const request = h.requests.at(-1);
    await display(h, 'B');
    request.resolve({message: 'retrying A'});
    await retry;
    assert.equal(h.requests.length, 3);
    assert.equal(h.state.sourceUid, 'B');
  });
}

if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = {harness, detail, display, check};
