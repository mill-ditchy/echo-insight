const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const nodes = new Map();
const context = {URL, document: {querySelector(selector) {
  if (!nodes.has(selector)) nodes.set(selector, {});
  return nodes.get(selector);
}}};
const source = fs.readFileSync(path.join(__dirname, '../web/static/app.js'), 'utf8');
vm.runInNewContext(source.slice(0, source.lastIndexOf('\ninitializeFontSettings();')), context);
const item = {platform: 'xiaohongshu', article_id: 'xhs_' + 'a'.repeat(24),
  title: '<img src=x onerror=alert(1)>', description: '正文<内容>', images: ['https://example.com/ignored'],
  image_count: 9, tags: ['话题'], ai_summary: '摘要', ai_analysis_mode: 'note_visual', ai_frame_count: 6,
  ai_completeness: 'partial', ai_analysis_warnings: ['只分析前6张'], link: 'https://www.xiaohongshu.com/explore/' + 'a'.repeat(24)};
const card = context.updateCard(item);
assert.ok(card.includes('打开原笔记'));
assert.ok(card.includes('重新分析图文'));
assert.ok(card.includes('已读 6/9 张'));
assert.ok(card.includes('读取不完整'));
assert.ok(card.includes('/api/notes/xhs_'));
assert.ok(!card.includes('https://example.com/ignored'));
assert.ok(!card.includes('<img src=x'));
context.renderBriefing(item);
assert.equal(nodes.get('#briefing-note').hidden, false);
assert.equal(nodes.get('#briefing-note-body').textContent, '正文<内容>\n\n话题：话题');
assert.equal(nodes.get('#briefing-original-link').textContent, '打开小红书原笔记');
assert.ok(nodes.get('#briefing-summary').textContent.includes('原笔记'));
assert.equal(context.noteImageUrl({...item, article_id: '../secret'}, 0), '');
assert.ok(context.summaryText({...item, ai_summary: '', ai_status: 'processing', ai_stage: 'note_ocr'}).includes('图片文字'));
assert.ok(context.summaryText({...item, ai_summary: '', ai_status: 'error', ai_error: '需要登录'}).includes('需要登录'));
assert.ok(context.summaryText({...item, ai_summary: '', detail_status: 'preview_only'}).includes('标题和封面'));
context.renderBriefing({platform: 'bilibili'});
assert.equal(nodes.get('#briefing-note').hidden, true);
assert.equal(context.platformLabel('xiaohongshu'), '小红书');

const complete = {...item, images: Array(18).fill('ignored'), image_count: 18, ai_frame_count: 18,
  ai_summary: '原文事实[正文]。图片结论[图片18]。<script>bad</script>', ai_completeness: 'complete',
  ai_note_reading_mode: 'full', ai_image_numbers: Array.from({length: 18}, (_, i) => i + 1),
  ai_note_evidence: [{type: 'image', number: 18, ocr: '<img onerror=alert(1)>'},
                     {type: 'batch', numbers: [13, 18], text: '<script>bad</script>'}]};
assert.ok(context.updateCard(complete).includes('完整模式'));
assert.ok(context.noteAnalysisButtons(complete).includes('data-reading-mode="full"'));
assert.ok(context.noteAnalysisButtons({...complete, last_attempt_status: 'processing'}).includes('disabled'));
context.renderBriefing(complete);
assert.ok(nodes.get('#briefing-note-images').innerHTML.includes('/images/17'));
assert.ok(nodes.get('#briefing-note-images').innerHTML.includes('id="note-image-18"'));
assert.ok(nodes.get('#briefing-summary').innerHTML.includes('data-note-number="18"'));
assert.ok(!nodes.get('#briefing-summary').innerHTML.includes('<script>'));
assert.ok(!nodes.get('#briefing-note-evidence').innerHTML.includes('<img onerror'));
assert.ok(nodes.get('#briefing-note-evidence').innerHTML.includes('图片 13、18'));
assert.ok(nodes.get('#briefing-note-scope').textContent.includes('已读 18 张'));
assert.ok(!context.noteCitations('[图片30]', complete).includes('data-note-number'));
assert.ok(context.collectionStatusText({message: '登录已失效', pending_count: 8}).includes('待补抓 8 篇'));
assert.ok(!source.includes('最多展示和分析前6张'));

// Run the page-state extraction against realistic Vue state shapes; private state is excluded.
const browserSource = fs.readFileSync(path.join(__dirname, '../src/platforms/xiaohongshu_browser.py'), 'utf8');
function expression(name) {
  return browserSource.split(name + ' = r"""')[1].split('"""')[0];
}
const id = 'a'.repeat(24);
const page = {window: {__INITIAL_STATE__: {privateSecret: 'never-extract',
  user: {userPageData: {_rawValue: {basicInfo: {nickname: '博主'}}}, notes: {_rawValue: [[{
    id, xsecToken: 'token', noteCard: {type: 'normal', displayTitle: '笔记', user: {userId: 'b'.repeat(24)}, cover: {url: 'https://sns-img-qc.xhscdn.com/img'}}
  }]]}}}}, document: {querySelector: () => null, querySelectorAll: () => [], body: {innerText: ''}}};
const profile = vm.runInNewContext(expression('PROFILE_EXPRESSION'), page);
assert.equal(profile.profile_name, '博主');
assert.equal(profile.notes.length, 1);
assert.equal(profile.notes[0].xsecToken, 'token');
assert.ok(!JSON.stringify(profile).includes('never-extract'));
page.location = {pathname: '/explore/' + id};
page.window.__INITIAL_STATE__.note = {noteDetailMap: {[id]: {note: {noteId: id, type: 'normal', title: '正文标题', desc: '正文', imageList: [{url: 'https://sns-img-qc.xhscdn.com/img'}]}}}};
const detail = vm.runInNewContext(expression('DETAIL_EXPRESSION'), page);
assert.equal(detail.detail_available, true);
assert.equal(detail.desc, '正文');
assert.equal(detail.imageList.length, 1);
console.log('Xiaohongshu UI and page extraction checks passed.');

(async () => {
  const endpoint = '/api/notes/xhs_' + id + '/images/0';
  let sent;
  context.fetch = async (url, options) => {sent = {url, options}; return {ok: true, blob: async () => ({})};};
  context.URL = class extends URL { static createObjectURL() {return 'blob:test-local-image';} };
  const element = {dataset: {noteImage: endpoint}, src: ''};
  context.loadNoteImages({querySelectorAll: () => [element]});
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.equal(sent.options.headers['X-Cyber-Request'], '1');
  assert.equal(element.src, 'blob:test-local-image');
  console.log('Protected image loading header and local blob rendering passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
