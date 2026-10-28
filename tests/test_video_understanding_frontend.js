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
  console.log("Video understanding UI checks passed (settings, input failure, completeness, evidence and escaping).");
})().catch(error => {console.error(error); process.exitCode = 1;});
