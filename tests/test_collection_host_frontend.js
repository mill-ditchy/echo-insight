const assert = require('node:assert/strict');
const vm = require('node:vm');
const {harness, detail, display, check} = require('./test_source_navigation_frontend');

const item = (id, fields = {}) => ({article_id: String(id), platform: 'xiaohongshu',
  source_uid: 'A', content_type: 'image_note', content_type_label: '图文', ...fields});

async function main() {
  await check('polling preserves loaded pages, fresh results and the selected type', async () => {
    const h = harness();
    await display(h, 'A', Array.from({length: 200}, (_, i) => item(i)));
    h.context.showSourceContentType('image_note');
    const more = h.context.loadMoreSourceContent({currentTarget: h.nodes.get('#source-load-more')});
    h.requests.at(-1).resolve({items: [item(200), item(201)], next_offset: 202, has_more: false});
    await more;
    const refresh = h.context.loadSourceDetail('xiaohongshu', 'A', {prepare: false});
    assert.ok(h.requests.at(-1).url.endsWith('?limit=202'));
    h.requests.at(-1).resolve(detail('A', [item(0, {ai_summary: '新简报'}), item('new')]));
    await refresh;
    assert.equal(h.state.sourceDetailData.videos.length, 203);
    assert.equal(h.state.sourceDetailData.videos.find(row => row.article_id === '0').ai_summary, '新简报');
    assert.equal(h.state.sourceContentType, 'image_note');
    assert.ok(h.nodes.get('#source-videos').innerHTML.includes('新简报'));
  });
  await check('duplicate page items merge once and refresh known items', async () => {
    const h = harness(); await display(h, 'A', [item(0)]);
    h.context.showSourceContentType('image_note');
    const more = h.context.loadMoreSourceContent({currentTarget: h.nodes.get('#source-load-more')});
    h.requests.at(-1).resolve({items: [item(0, {title: '更新'}), item(1), item(1)], next_offset: 203, has_more: false});
    await more;
    assert.equal(h.state.sourceDetailData.videos.length, 2);
    assert.equal(h.state.sourceDetailData.videos[0].title, '更新');
  });
  await check('late preparation cannot reopen a source after navigation', async () => {
    const h = harness();
    const pending = h.context.loadSourceDetail('xiaohongshu', 'A');
    h.requests.at(-1).resolve({...detail('A'), needs_preparation: true});
    await new Promise(setImmediate);
    const command = h.requests.at(-1);
    assert.equal(command.options.method, 'POST');
    assert.ok(command.url.endsWith('/prepare'));
    h.context.setView('sources');
    command.resolve(detail('A', [item(0)]));
    assert.equal(await pending, false);
    assert.equal(h.state.currentView, 'sources');
  });
  await check('empty polling is a pure GET without preparation', async () => {
    const h = harness();
    const pending = h.context.loadSourceDetail('xiaohongshu', 'A', {prepare: false});
    h.requests.at(-1).resolve({...detail('A'), needs_preparation: true});
    await pending;
    assert.equal(h.requests.length, 1);
  });
  await check('briefing beyond the first page refreshes by content identity', async () => {
    const h = harness();
    h.state.currentView = 'briefing'; h.state.briefingItem = item(201);
    vm.runInContext('renderBriefing = () => {};', h.context);
    const pending = h.context.refreshNoteBriefing();
    assert.equal(h.requests.at(-1).url, '/api/history/xiaohongshu/201');
    h.requests.at(-1).resolve({item: item(201, {ai_summary: '完成'})});
    await pending;
    assert.equal(h.state.briefingItem.ai_summary, '完成');
  });
  await check('late single-item result cannot replace another platform briefing', async () => {
    const h = harness(); h.state.currentView = 'briefing'; h.state.briefingItem = item(201);
    const pending = h.context.refreshNoteBriefing();
    h.state.briefingItem = item(201, {platform: 'douyin'});
    h.requests.at(-1).resolve({item: item(201, {ai_summary: '旧请求'})});
    await pending;
    assert.equal(h.state.briefingItem.platform, 'douyin');
    assert.equal(h.state.briefingItem.ai_summary, undefined);
  });
  await check('dashboard refresh preserves pages and rejects a stale page response', async () => {
    const h = harness(); await Promise.resolve(); await Promise.resolve();
    h.state.dashboard = {updates: [item(0), item(1)], unread_count: 2, next_offset: 2};
    const more = h.context.loadMoreDashboardContent({currentTarget: h.nodes.get('#dashboard-load-more')});
    const old = h.requests.at(-1);
    const refresh = h.context.loadDashboard();
    // The harness serves the first dashboard page immediately.
    await refresh;
    old.resolve({updates: [item('stale')], unread_count: 2, next_offset: 3});
    await more;
    assert.ok(!h.state.dashboard.updates.some(row => row.article_id === 'stale'));
    assert.equal(h.nodes.get('#dashboard-load-more').disabled, false);
  });
  await check('dashboard polling retains loaded unread pages and current results', async () => {
    const h = harness(); await new Promise(setImmediate);
    h.state.dashboard = {updates: Array.from({length: 101}, (_, i) => item(i)), unread_count: 101, next_offset: 101};
    const pending = h.context.loadDashboard();
    assert.equal(h.requests.at(-1).url, '/api/dashboard?limit=101');
    h.requests.at(-1).resolve({updates: [item('new'), item(0, {ai_summary: '新摘要'})],
      unread_updates: [], unread_count: 102, next_offset: 101, has_more: true});
    await pending;
    assert.equal(h.state.dashboard.updates.length, 102);
    assert.equal(h.state.dashboard.updates.find(row => row.article_id === '0').ai_summary, '新摘要');
  });
  await check('page completed during dashboard polling is retained', async () => {
    const h = harness(); await new Promise(setImmediate);
    h.state.dashboard = {updates: Array.from({length: 101}, (_, i) => item(i)), unread_count: 102, next_offset: 101};
    const refresh = h.context.loadDashboard();
    const header = h.requests.at(-1);
    const more = h.context.loadMoreDashboardContent({currentTarget: h.nodes.get('#dashboard-load-more')});
    h.requests.at(-1).resolve({updates: [item(101)], unread_count: 102, next_offset: 102});
    await more;
    header.resolve({updates: [item(0)], unread_updates: [], unread_count: 102, next_offset: 101});
    await refresh;
    assert.ok(h.state.dashboard.updates.some(row => row.article_id === '101'));
  });
  await check('remote host login capabilities control behavior without rewriting labels', async () => {
    const h = harness(); await new Promise(setImmediate);
    h.context.window.CyberHost.canLogin = false;
    await h.context.startQrLogin();
    await h.context.startZhihuLogin();
    assert.equal(h.requests.length, 0);
    assert.ok(h.nodes.get('#toast').textContent.includes('电脑端'));
  });
}

main().catch(error => { console.error(error); process.exitCode = 1; });
