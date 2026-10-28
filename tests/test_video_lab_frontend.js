const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {harness} = require("./test_source_navigation_frontend");
const source = fs.readFileSync(require("node:path").join(__dirname, "../web/static/video-lab.js"), "utf8");

function setup(hostOptions = {}) {
  const h = harness();
  h.state.currentView = "video-lab";
  h.playerPauseCount = 0;
  h.nodes.get("#video-lab-preview").pause = () => { h.playerPauseCount += 1; };
  h.nodes.get("#video-lab-file").files = [];
  h.context.URL = class extends URL {
    static createObjectURL() { return "blob:video-test"; }
    static revokeObjectURL() {}
  };
  Object.assign(h.context.window.CyberHost, hostOptions);
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
  const pauseBeforeNavigation = h.playerPauseCount;
  h.context.setView("sources");
  assert.equal(h.playerPauseCount, pauseBeforeNavigation + 1, "Leaving video lab must pause its hidden media player");

  h.state.currentView = "video-lab";
  state.selectedId = record.id;
  h.context.api = async url => url === '/api/video-lab/videos' ? {videos: []} : {};
  const pauseBeforeDelete = h.playerPauseCount;
  const deleteButton = {dataset: {videoDelete: record.id}};
  await h.nodes.get('#video-lab-history').listeners.get('click')({target: {
    closest: selector => selector === '[data-video-delete]' ? deleteButton : null,
  }});
  assert.equal(h.playerPauseCount, pauseBeforeDelete + 1, "Deleting the selected video must stop playback before hiding it");
  assert.equal(state.selectedId, null);
  assert.equal(h.nodes.get('#video-lab-preview').hidden, true);
  const mobile = setup({kind: 'android'});
  const mobileSent = [], mobileUploads = [], copied = [];
  Object.assign(mobile.context.window.CyberHost, {kind: 'android',
    uploadVideo: async file => { mobileUploads.push(file); return record; },
    videoPreviewUrl: id => `https://appassets.androidplatform.net/backend/api/video-lab/videos/${id}/file`,
    copyText: text => { copied.push(text); },
  });
  mobile.context.fetch = () => { throw new Error('Android video previews must use the native streaming URL'); };
  mobile.context.api = async (url, options = {}) => {
    mobileSent.push({url, options});
    if (url.endsWith('/config')) return config;
    if (url.endsWith('/analyze')) return {...record, status: 'queued'};
    if (url === '/api/video-lab/videos') return {videos: [record]};
    return completed;
  };
  await mobile.context.window.VideoLab.load();
  assert.equal(mobile.nodes.get('#video-lab-nav').hidden, false);
  const phoneFile = {name: '手机视频.mp4', size: 32 * 1048576};
  mobile.nodes.get('#video-lab-file').files = [phoneFile];
  mobile.context.selectVideoLabFile();
  await mobile.context.analyzeVideoLab({preventDefault() {}});
  assert.equal(mobileUploads.length, 1); assert.equal(mobileUploads[0], phoneFile);
  assert.ok(!mobileSent.some(request => request.url.includes('?filename=')), 'Large video must bypass the JSON/base64 request adapter');
  assert.ok(mobileSent.some(request => request.url.endsWith('/analyze')));
  await mobile.context.openVideoLab(record.id);
  assert.equal(mobile.nodes.get('#video-lab-preview').src,
    'https://appassets.androidplatform.net/backend/api/video-lab/videos/' + record.id + '/file');
  assert.equal(mobile.nodes.get('#video-lab-preview').hidden, false);
  assert.equal(mobile.nodes.get('#video-lab-answer').textContent, completed.runs[0].text);
  await mobile.nodes.get('#video-lab-copy').listeners.get('click')();
  assert.deepEqual(copied, [completed.runs[0].text]);
  console.log("Video upload UI checks passed (original file, retries, retained answers, escaping, size cap and stale polling).");
})().catch(error => {console.error(error); process.exitCode = 1;});
