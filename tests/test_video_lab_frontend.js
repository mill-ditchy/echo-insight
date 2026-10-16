const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {harness} = require("./test_source_navigation_frontend");
const source = fs.readFileSync(require("node:path").join(__dirname, "../web/static/video-lab.js"), "utf8");

function setup() {
  const h = harness();
  h.state.currentView = "video-lab";
  h.nodes.get("#video-lab-preview").pause = () => {};
  h.nodes.get("#video-lab-file").files = [];
  h.context.URL = class extends URL {
    static createObjectURL() { return "blob:video-test"; }
    static revokeObjectURL() {}
  };
  vm.runInContext(source, h.context);
  return h;
}

const config = {available: true, model: "gemini-test", max_bytes: 64 * 1048576, default_prompt: "请分析整个视频"};
const record = {id: "a".repeat(32), filename: "样片.mp4", size: 64, duration: 2, has_audio: true, status: "ready", created_at: "2026-10-16T12:00:00Z", runs: []};
const completed = {...record, status: "completed", runs: [{text: "00:00红色；00:01蓝色", model: "gemini-test", prompt: "提问", elapsed_seconds: 2, completed_at: record.created_at}]};

(async () => {
  const h = setup();
  const sent = [];
  h.context.api = async (url, options = {}) => {
    sent.push({url, options});
    if (url.endsWith("/config")) return config;
    if (url.endsWith("/analyze")) return {...record, status: "queued"};
    if (url.includes("?filename=")) return record;
    if (url === "/api/video-lab/videos") return {videos: [record]};
    return completed;
  };
  await h.context.window.VideoLab.load();
  assert.match(h.nodes.get("#video-lab-model").textContent, /gemini-test/);
  assert.equal(h.nodes.get("#video-lab-prompt").value, config.default_prompt);
  const file = {name: "原视频.mp4", size: 64};
  h.nodes.get("#video-lab-file").files = [file];
  h.context.selectVideoLabFile();
  await h.context.analyzeVideoLab({preventDefault() {}});
  const upload = sent.find(r => r.url.includes("?filename="));
  assert.equal(upload.options.body, file);
  assert.equal(upload.options.headers["Content-Type"], "application/octet-stream");
  const analysis = sent.find(r => r.url.endsWith("/analyze"));
  assert.deepEqual(JSON.parse(analysis.options.body), {prompt: config.default_prompt});
  assert.equal(h.nodes.get("#video-lab-answer").textContent, completed.runs[0].text);
  assert.equal(h.nodes.get("#video-lab-result").hidden, false);
  assert.equal(h.nodes.get("#video-lab-submit").disabled, false);
  assert.equal(h.nodes.get("#video-lab-file").disabled, false);
  const uploadCount = sent.filter(r => r.url.includes("?filename=")).length;
  await h.context.analyzeVideoLab({preventDefault() {}}, true);
  assert.equal(sent.filter(r => r.url.includes("?filename=")).length, uploadCount);

  h.context.renderVideoLabRecord({...completed, status: "error", error: "本次连接失败"});
  assert.match(h.nodes.get("#video-lab-status").textContent, /保留上次/);
  assert.equal(h.nodes.get("#video-lab-answer").textContent, completed.runs[0].text);
  h.context.renderVideoLabRecord({...completed, runs: [{...completed.runs[0], text: "<img src=x onerror=alert(1)>"}, completed.runs[0]]});
  assert.ok(!h.nodes.get("#video-lab-previous-runs").innerHTML.includes("<img"));

  h.nodes.get("#video-lab-file").files = [{name: "too-large.mp4", size: config.max_bytes + 1}];
  h.context.selectVideoLabFile();
  const countBefore = sent.length;
  await h.context.analyzeVideoLab({preventDefault() {}});
  assert.equal(sent.length, countBefore);
  assert.equal(h.nodes.get("#video-lab-preview").hidden, true);
  assert.match(h.nodes.get("#video-lab-status").textContent, /64 MB/);

  const state = vm.runInContext("videoLab", h.context);
  state.selectedId = record.id;
  let resolve;
  h.context.api = () => new Promise(yes => { resolve = yes; });
  const pending = h.context.pollVideoLab(record.id, state.generation);
  h.context.window.VideoLab.leave();
  const answerBefore = h.nodes.get("#video-lab-answer").textContent;
  resolve({...completed, runs: [{...completed.runs[0], text: "不应覆盖离开后的页面"}]});
  await pending;
  assert.equal(h.nodes.get("#video-lab-answer").textContent, answerBefore);
  console.log("Video upload UI checks passed (original file, retries, retained answers, escaping, size cap and stale polling).");
})().catch(error => {console.error(error); process.exitCode = 1;});
