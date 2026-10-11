const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../web/static/app.js"), "utf8");
const nodes = new Map();
const context = {URL, setTimeout: () => {}, document: {querySelector: selector => {
  if (!nodes.has(selector)) nodes.set(selector, {value: "", checked: false, files: [], textContent: "", innerHTML: "", classList: {add() {}, remove() {}, toggle() {}}, style: {}});
  return nodes.get(selector);
}}};
vm.runInNewContext(source.slice(0, source.lastIndexOf("\ninitializeFontSettings();")), context);
(async () => {
  context.fillVideoAnalysisForm({enabled: true, input_mode: "video", max_segments: 6, online_lookup: false});
  assert.equal(context.videoAnalysisDraft().input_mode, "video");
  assert.equal(context.videoAnalysisDraft().max_segments, 6);
  assert.equal(context.videoAnalysisDraft().online_lookup, false);
  const button = {};
  let sent;
  context.api = async (url, options) => {sent = {url, body: JSON.parse(options.body)}; return {message: "已保存", video_analysis: sent.body};};
  await context.updateVideoAnalysis({preventDefault() {}, currentTarget: {querySelector: () => button}});
  assert.equal(sent.url, "/api/settings/video-analysis");
  assert.equal(button.disabled, false);
  context.api = async () => { throw new Error("输入方式不受支持"); };
  await context.testVideoInput({currentTarget: button});
  assert.equal(nodes.get("#video-analysis-result").textContent, "输入方式不受支持");
  assert.equal(button.disabled, false);
  const html = context.updateCard({title: "标题", ai_summary: "摘要", ai_analysis_mode: "visual", ai_segment_count: 3,
    ai_frame_count: 24, ai_completeness: "partial", ai_input_mode: "images", ai_analysis_warnings: ["<img src=x onerror=alert(1)>"],
    last_attempt_status: "error", last_attempt_error: "失败", article_id: "BVtest", platform: "bilibili"});
  assert.match(html, /分段画面 · 3段 · 24帧 · 分析不完整/);
  assert.match(html, /查看分段分析依据/);
  assert.ok(!html.includes("<img src=x"));
  assert.match(html, /&lt;img/);
  context.api = async () => ({entries: [{name: "<script>bad</script>", work: "作品", text: "资料"}]});
  await context.loadMediaKnowledge();
  assert.ok(!nodes.get("#media-knowledge-list").innerHTML.includes("<script>"));
  nodes.get("#knowledge-name") || context.document.querySelector("#knowledge-name");
  context.document.querySelector("#knowledge-name").value = "新角色";
  context.document.querySelector("#knowledge-variant").value = "二创形象";
  context.document.querySelector("#knowledge-appearance").value = "独特耳饰";
  context.api = async (url, options) => {
    if (options) { sent = {url, method: options.method, body: JSON.parse(options.body)}; return {message: "已添加"}; }
    return {entries: []};
  };
  await context.addMediaKnowledge({preventDefault() {}, currentTarget: {querySelector: () => button, reset() {}}});
  assert.equal(sent.method, "POST");
  assert.equal(sent.body.entries.length, 1);
  assert.equal(sent.body.entries[0].variant, "二创形象");
  assert.equal(sent.body.entries[0].appearance, "独特耳饰");
  console.log("Video understanding UI checks passed (settings, input failure, completeness, evidence and escaping).");
})().catch(error => {console.error(error); process.exitCode = 1;});
