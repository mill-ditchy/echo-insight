"use strict";

const videoLab = {config: null, selectedId: null, previewUrl: "", timer: null, generation: 0, busy: false, record: null};
const videoLabLabels = {ready: "已上传", queued: "排队中", processing: "模型正在观看视频…", completed: "分析完成", error: "分析失败"};

function videoLabDuration(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function videoLabLeave() {
  clearTimeout(videoLab.timer);
  videoLab.timer = null;
  videoLab.generation += 1;
}

function videoLabPreview(blob) {
  const player = $("#video-lab-preview");
  player.pause();
  if (videoLab.previewUrl) URL.revokeObjectURL(videoLab.previewUrl);
  videoLab.previewUrl = URL.createObjectURL(blob);
  player.src = videoLab.previewUrl;
  player.hidden = false;
}

function videoLabButtons() {
  const running = ["queued", "processing"].includes(videoLab.record?.status);
  $("#video-lab-submit").disabled = videoLab.busy || !videoLab.config?.available;
  $("#video-lab-file").disabled = videoLab.busy;
  $("#video-lab-prompt").disabled = videoLab.busy;
  $("#video-lab-retry").disabled = videoLab.busy || !videoLab.selectedId || running || !videoLab.config?.available;
}

function renderVideoLabRecord(record) {
  videoLab.record = record;
  $("#video-lab-status").textContent = record.status === "error" ? `${record.error}${record.runs?.length ? "。下方保留上次完成的回答。" : ""}` : videoLabLabels[record.status] || "";
  $("#video-lab-file-info").textContent = `${record.filename} · ${videoLabDuration(record.duration)} · ${(record.size / 1048576).toFixed(1)} MB · ${record.has_audio ? "含音轨" : "无音轨"}`;
  const runs = record.runs || [];
  $("#video-lab-result").hidden = !runs.length;
  if (runs.length) {
    const latest = runs[runs.length - 1];
    $("#video-lab-answer").textContent = latest.text;
    $("#video-lab-result-meta").textContent = `原视频直传 · ${latest.model} · 耗时${latest.elapsed_seconds}秒 · ${formatTime(latest.completed_at)} · 第${runs.length}次回答`;
    $("#video-lab-previous-runs").innerHTML = runs.slice(0, -1).map((run, index) =>
      `<details><summary>第${index + 1}次 · ${escapeHtml(run.model)} · ${escapeHtml(formatTime(run.completed_at))}</summary><p class="setting-help">提问：${escapeHtml(run.prompt)}</p><pre>${escapeHtml(run.text)}</pre></details>`
    ).join("");
  }
  videoLabButtons();
}

async function refreshVideoLabHistory() {
  const data = await api("/api/video-lab/videos");
  $("#video-lab-history").innerHTML = data.videos.length ? data.videos.map(record =>
    `<article class="panel video-lab-history-row"><div><strong>${escapeHtml(record.filename)}</strong><p class="setting-help">${escapeHtml(videoLabDuration(record.duration))} · ${escapeHtml(videoLabLabels[record.status] || record.status)} · ${escapeHtml(formatTime(record.created_at))}</p></div><div class="video-lab-actions"><button class="ghost-button" type="button" data-video-open="${escapeHtml(record.id)}">打开</button><button class="text-button" type="button" data-video-delete="${escapeHtml(record.id)}" ${["queued", "processing"].includes(record.status) ? "disabled" : ""}>删除</button></div></article>`
  ).join("") : '<div class="panel empty-state">还没有实验记录。上传一段视频，先看看模型的原始回答。</div>';
}

async function pollVideoLab(videoId, generation) {
  const record = await api(`/api/video-lab/videos/${encodeURIComponent(videoId)}`);
  if (videoLab.generation !== generation || videoLab.selectedId !== videoId || state.currentView !== "video-lab") return;
  renderVideoLabRecord(record);
  if (["queued", "processing"].includes(record.status)) {
    clearTimeout(videoLab.timer);
    videoLab.timer = setTimeout(() => pollVideoLab(videoId, generation).catch(error => {
      if (videoLab.generation === generation) $("#video-lab-status").textContent = `${error.message}，可刷新记录查看分析状态。`;
    }), 2500);
  } else await refreshVideoLabHistory();
}

async function loadVideoLab() {
  if (window.CyberHost?.kind === "android") throw new Error("请在电脑端使用视频实验入口");
  const [config] = await Promise.all([api("/api/video-lab/config"), refreshVideoLabHistory()]);
  videoLab.config = config;
  $("#video-lab-model").textContent = `当前模型：${config.model}${config.available ? "" : `；${config.message}`}`;
  if (!$("#video-lab-prompt").value.trim()) $("#video-lab-prompt").value = config.default_prompt;
  videoLabButtons();
  if (videoLab.selectedId && !videoLab.busy) await pollVideoLab(videoLab.selectedId, videoLab.generation);
}

async function openVideoLab(videoId) {
  if (videoLab.busy) return;
  videoLabLeave();
  videoLab.selectedId = videoId;
  $("#video-lab-preview").pause();
  $("#video-lab-preview").hidden = true;
  const generation = videoLab.generation;
  $("#video-lab-file").value = "";
  const record = await api(`/api/video-lab/videos/${encodeURIComponent(videoId)}`);
  if (videoLab.generation !== generation) return;
  renderVideoLabRecord(record);
  if (record.runs?.length) $("#video-lab-prompt").value = record.runs[record.runs.length - 1].prompt;
  const response = await fetch(`/api/video-lab/videos/${encodeURIComponent(videoId)}/file`, {headers: {"X-Cyber-Request": "1"}});
  if (!response.ok) throw new Error("原视频预览读取失败，已有分析回答仍可查看");
  const blob = await response.blob();
  if (videoLab.generation !== generation) return;
  videoLabPreview(blob);
  await pollVideoLab(videoId, generation);
}

function selectVideoLabFile() {
  const file = $("#video-lab-file").files[0];
  if (!file) return;
  videoLabLeave();
  videoLab.selectedId = null;
  videoLab.record = null;
  $("#video-lab-result").hidden = true;
  const maxBytes = videoLab.config?.max_bytes || 64 * 1048576;
  if (!/\.(mp4|mov|webm)$/i.test(file.name) || file.size > maxBytes || !file.size) {
    $("#video-lab-preview").pause();
    $("#video-lab-preview").hidden = true;
    $("#video-lab-status").textContent = "请选择64 MB以内的非空MP4、MOV或WebM视频";
    videoLabButtons();
    return;
  }
  videoLabPreview(file);
  $("#video-lab-file-info").textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} MB`;
  $("#video-lab-status").textContent = "视频已选择，点击上传并分析后发送给模型。";
  videoLabButtons();
}

async function analyzeVideoLab(event, retry = false) {
  event?.preventDefault();
  if (videoLab.busy) return;
  const file = $("#video-lab-file").files[0];
  if (!videoLab.config?.available) { $("#video-lab-status").textContent = videoLab.config?.message || "请先配置Gemini视觉模型"; return; }
  if (!retry && (!file || !/\.(mp4|mov|webm)$/i.test(file.name) || !file.size || file.size > videoLab.config.max_bytes)) {
    $("#video-lab-status").textContent = "请选择64 MB以内的非空MP4、MOV或WebM视频"; return;
  }
  if (retry && !videoLab.selectedId) return;
  const prompt = $("#video-lab-prompt").value.trim() || videoLab.config.default_prompt;
  videoLab.busy = true;
  videoLabButtons();
  try {
    if (!retry) {
      $("#video-lab-status").textContent = "正在上传原视频…";
      const uploaded = await api(`/api/video-lab/videos?filename=${encodeURIComponent(file.name)}`, {method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: file});
      videoLab.selectedId = uploaded.id;
      renderVideoLabRecord(uploaded);
    }
    const videoId = videoLab.selectedId;
    const record = await api(`/api/video-lab/videos/${encodeURIComponent(videoId)}/analyze`, {method: "POST", body: JSON.stringify({prompt})});
    if (videoLab.selectedId === videoId) {
      renderVideoLabRecord(record);
      await pollVideoLab(videoId, videoLab.generation);
    }
    await refreshVideoLabHistory();
  } catch (error) { $("#video-lab-status").textContent = error.message; }
  finally { videoLab.busy = false; videoLabButtons(); }
}

$("#video-lab-form").addEventListener("submit", analyzeVideoLab);
$("#video-lab-file").addEventListener("change", selectVideoLabFile);
$("#video-lab-retry").addEventListener("click", event => analyzeVideoLab(event, true));
$("#video-lab-refresh").addEventListener("click", () => loadVideoLab().catch(error => showToast(error.message, true)));
$("#video-lab-history").addEventListener("click", async event => {
  const open = event.target.closest("[data-video-open]");
  const remove = event.target.closest("[data-video-delete]");
  try {
    if (open) await openVideoLab(open.dataset.videoOpen);
    if (remove && !videoLab.busy) {
      const videoId = remove.dataset.videoDelete;
      await api(`/api/video-lab/videos/${encodeURIComponent(videoId)}`, {method: "DELETE"});
      if (videoLab.selectedId === videoId) {
        videoLabLeave(); videoLab.selectedId = null; videoLab.record = null;
        $("#video-lab-result").hidden = true; $("#video-lab-preview").hidden = true;
        $("#video-lab-status").textContent = "视频与实验结果已删除";
        if (videoLab.previewUrl) { URL.revokeObjectURL(videoLab.previewUrl); videoLab.previewUrl = ""; }
        videoLabButtons();
      }
      await refreshVideoLabHistory();
    }
  } catch (error) { showToast(error.message, true); }
});
$("#video-lab-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#video-lab-answer").textContent); showToast("已复制模型回答"); }
  catch (_error) { showToast("无法访问剪贴板，请选中回答文本复制", true); }
});
if (window.CyberHost?.kind === "android") $("#video-lab-nav").hidden = true;
window.VideoLab = Object.freeze({load: loadVideoLab, leave: videoLabLeave});
