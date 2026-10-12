const state = { currentView: "dashboard", dashboard: null, sources: [], sourcesStage: "platforms", selectedPlatform: null, sourceContentType: null, sourceDetailData: null, qrKey: null, qrTimer: null, sourceTimer: null, dashboardTimer: null, sourcePlatform: null, sourceUid: null, sourceName: null, unreadPanelOpen: false, briefingBack: "dashboard", briefingItem: null };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const FONT_DEFAULTS = { interfaceSize: 14, briefingSize: 14 };
const FONT_STORAGE_KEYS = {
  interfaceSize: "cyber-intel-interface-font-size",
  briefingSize: "cyber-intel-briefing-font-size",
};

function clampFontSize(value, minimum, maximum, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(maximum, Math.max(minimum, parsed)) : fallback;
}

function loadFontPreference(key, fallback) {
  try { return localStorage.getItem(key) || fallback; }
  catch (_error) { return fallback; }
}

function applyFontSizes(interfaceSize, briefingSize, persist = false) {
  const normalizedInterface = clampFontSize(interfaceSize, 13, 18, FONT_DEFAULTS.interfaceSize);
  const normalizedBriefing = clampFontSize(briefingSize, 13, 22, FONT_DEFAULTS.briefingSize);
  document.documentElement.style.setProperty("--ui-font-size", `${normalizedInterface}px`);
  document.documentElement.style.setProperty("--briefing-font-size", `${normalizedBriefing}px`);

  const interfaceInput = $("#interface-font-size");
  const briefingInput = $("#briefing-font-size");
  if (interfaceInput) interfaceInput.value = normalizedInterface;
  if (briefingInput) briefingInput.value = normalizedBriefing;
  if ($("#interface-font-size-value")) $("#interface-font-size-value").textContent = `${normalizedInterface} 像素`;
  if ($("#briefing-font-size-value")) $("#briefing-font-size-value").textContent = `${normalizedBriefing} 像素`;

  if (persist) {
    try {
      localStorage.setItem(FONT_STORAGE_KEYS.interfaceSize, String(normalizedInterface));
      localStorage.setItem(FONT_STORAGE_KEYS.briefingSize, String(normalizedBriefing));
    } catch (_error) { /* 字体设置仍会在当前窗口生效。 */ }
  }
}

function initializeFontSettings() {
  const interfaceSize = loadFontPreference(FONT_STORAGE_KEYS.interfaceSize, FONT_DEFAULTS.interfaceSize);
  const briefingSize = loadFontPreference(FONT_STORAGE_KEYS.briefingSize, FONT_DEFAULTS.briefingSize);
  applyFontSizes(interfaceSize, briefingSize);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Cyber-Request": "1", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "请求失败");
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function safeExternalUrl(value) {
  if (typeof value !== "string" || /[\u0000-\u0020\\]/.test(value)) return "";
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : "";
  } catch (_error) { return ""; }
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.style.borderColor = error ? "#fecaca" : "#bfdbfe";
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 3600);
}

function formatTime(value) {
  if (!value) return "暂无";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function normalizeDisplaySummary(value) {
  return String(value ?? "")
    .replace(/\*\*([\s\S]*?)\*\*/g, "$1")
    .replace(/^\s*#{1,6}\s*/gm, "")
    .replace(/^\s*[-*]\s+/gm, "• ")
    .replace(/`/g, "")
    .trim();
}

function updateKey(item) {
  return `${item.platform || ""}:${item.article_id || ""}`;
}

function isImageNote(item) {
  return item.platform === "xiaohongshu" || (item.platform === "douyin" && item.content_type === "image_note");
}

function noteSummaryText(value) {
  // Keep source markers in the stored summary; the reading view shows clean prose.
  return normalizeDisplaySummary(value).replace(/\[(?:图片\s*\d+|正文)\]/g, "").trim();
}

function summaryText(item) {
  if (isImageNote(item)) {
    if (item.ai_summary) return noteSummaryText(item.ai_summary);
    if (item.ai_status === "error") return `图文简报生成失败：${item.ai_error || "请重试"}`;
    if (item.detail_status === "preview_only") return item.fetch_warning || "目前只取得标题，请登录后重试读取正文。";
    return item.ai_stage === "summarizing" ? "正在生成简报…" : item.ai_status === "processing" ? "正在读取笔记内容…" : "等待生成简报…";
  }
  const stageLabels = {
    waiting: "等待处理：即将解析视频并生成AI简报…",
    subtitle: "正在读取视频字幕…",
    media_download: "正在读取视频内容…",
    note_reading: "正在重新读取作品…",
    transcribing_and_frames: "正在同时进行语音转写和关键帧分析…",
    transcribing: "正在进行语音转写…",
    frames: "正在分析视频关键帧…",
    segment_analysis: "正在按时间段分析画面与屏幕文字…",
    knowledge_lookup: "正在核对作品与角色资料…",
    character_recognition: "正在检索人物原型并核对角色名字…",
    summarizing: "内容已读取，正在生成AI简报…",
  };
  const zhihuNeedsAi = item.platform === "zhihu" && ["article", "zvideo", "video"].includes(item.content_type);
  if (item.platform === "zhihu" && !zhihuNeedsAi) return item.description || "暂无公开原文";
  let summary = item.ai_summary ? normalizeDisplaySummary(item.ai_summary) : "等待处理：即将解析视频并生成AI简报…";
  if (zhihuNeedsAi && !item.ai_summary) summary = "等待处理：即将阅读正文并生成AI简报…";
  if (!item.ai_summary && item.ai_status === "pending") summary = zhihuNeedsAi ? "等待处理：即将阅读正文并生成AI简报…" : (stageLabels[item.ai_stage] || stageLabels.waiting);
  if (!item.ai_summary && item.ai_status === "processing") summary = stageLabels[item.ai_stage] || (zhihuNeedsAi ? "正在阅读正文并生成AI简报，请稍候…" : "正在解析视频并生成AI简报，请稍候…");
  if (!item.ai_summary && item.ai_status === "error") summary = `AI简报生成失败：${item.ai_error || "请稍后重试"}`;
  return summary;
}

function updateCard(item) {
  const summary = summaryText(item);
  const sourceLabel = item.is_baseline ? `${item.source} · 初始展示` : item.source;
  const isReanalyzing = item.last_attempt_status === "processing";
  let analysisLabel = "";
  if (item.ai_summary && item.ai_analysis_mode === "visual") analysisLabel = `视觉+文字 · ${item.ai_frame_count || 0}帧`;
  else if (item.ai_summary && item.visual_error) analysisLabel = "纯文字回退";
  else if (item.ai_summary) analysisLabel = "纯文字";
  if (item.ai_segment_count) analysisLabel = `${item.ai_input_mode === "video" ? "采样视频" : "分段画面"} · ${item.ai_segment_count}段 · ${item.ai_frame_count || 0}帧`;
  if (item.ai_completeness === "partial") analysisLabel += " · 分析不完整";
  if (["xiaohongshu", "douyin"].includes(item.platform) && item.ai_summary) analysisLabel = item.ai_completeness === "partial" ? "摘要范围有限" : "摘要已生成";
  const analysisNote = [item.fetch_warning, item.visual_error, ...(["xiaohongshu", "douyin"].includes(item.platform) ? [] : item.ai_analysis_warnings || []), item.last_attempt_status === "error" ? item.last_attempt_error : ""].filter(Boolean).join("；");
  const analysisBadge = analysisLabel ? `<span class="analysis-badge ${item.ai_analysis_mode === "visual" ? "visual" : ""}" title="${escapeHtml(analysisNote || analysisLabel)}">${escapeHtml(analysisLabel)}</span>` : "";
  const analysisWarning = analysisNote ? `<div class="setting-help">${escapeHtml(analysisNote)}</div>` : "";
  const reanalyzeButton = item.platform === "bilibili" && item.link ? `<button class="text-button reanalyze-button" data-platform="bilibili" data-article-id="${escapeHtml(item.article_id)}" ${isReanalyzing ? "disabled" : ""}>${isReanalyzing ? "正在分析…" : "重新视觉分析"}</button>` : "";
  const summaryToggle = String(summary).length > 180 ? `<button class="text-button summary-toggle">展开简报</button>` : "";
  const originalLabel = item.platform === "douyin" ? "打开抖音原作品 ↗" : item.platform === "xiaohongshu" ? "打开原笔记 ↗" : item.platform === "zhihu" ? "打开原内容 ↗" : "打开原视频 ↗";
  const originalUrl = safeExternalUrl(item.link);
  const originalLink = originalUrl ? `<a class="original-link" href="${escapeHtml(originalUrl)}" target="_blank" rel="noopener noreferrer">${originalLabel}</a>` : "";
  const evidenceDetails = item.platform === "bilibili" && item.ai_segment_count ? `<details class="video-evidence" data-platform="${escapeHtml(item.platform)}" data-article-id="${escapeHtml(item.article_id)}"><summary>查看分段分析依据与资料出处</summary><div class="video-evidence-body setting-help"></div></details>` : "";
  const contentBadge = item.content_type_label ? `<span class="analysis-badge">${escapeHtml(item.content_type_label)}</span>` : "";
  return `<article class="update-card ${item.read ? "" : "unread"}" data-update-key="${escapeHtml(updateKey(item))}">
    <div class="update-top"><h3 class="update-title"><button class="update-title-button briefing-link" type="button">${escapeHtml(item.title)}</button></h3><span class="update-source">${escapeHtml(sourceLabel)}</span></div>
    <div class="update-meta">发布时间：${escapeHtml(item.published || "未知")} · 检测于：${escapeHtml(formatTime(item.detected_at))}</div>
    <div class="update-summary">${escapeHtml(summary)}</div>
    ${analysisWarning}
    ${evidenceDetails}
    <div class="analysis-footer"><div class="analysis-context">${contentBadge}${analysisBadge}${originalLink}</div><div class="analysis-actions">${summaryToggle}${reanalyzeButton}</div></div>
  </article>`;
}

function renderUpdates(container, items, emptyText) {
  container.innerHTML = items.length ? items.map(updateCard).join("") : `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
  container.querySelectorAll(".reanalyze-button").forEach((button) => button.addEventListener("click", reanalyzeVideo));
  container.querySelectorAll(".video-evidence").forEach((element) => element.addEventListener("toggle", async () => {
    if (!element.open || element.dataset.loaded) return;
    const body = element.querySelector(".video-evidence-body");
    body.textContent = "正在读取分析依据…";
    try {
      const data = await api(`/api/videos/${encodeURIComponent(element.dataset.platform)}/${encodeURIComponent(element.dataset.articleId)}/evidence`);
      const blocks = data.segments.map(segment => `${segment.start.toFixed(1)}–${segment.end.toFixed(1)} 秒：\n${segment.evidence.observations.join("\n")}\n可见文字：${segment.evidence.onscreen_text.join("；") || "未读到"}\n未确认事项：${segment.evidence.uncertainties.join("；") || "无额外记录"}`);
      for (const entry of data.context) blocks.push(`参考资料：${entry.name}${entry.status === "unverified_lookup" ? "（检索候选，未验证）" : "（用户资料）"}\n${entry.source_url}`);
      const roles = {depicted: "画面人物原型", template: "原片对照人物", reference: "参考形象"};
      for (const subject of data.character_recognition?.subjects || []) {
        const identity = subject.identity;
        blocks.push(`${subject.timestamp.toFixed(1)}秒 · ${roles[subject.role] || "人物"}：${identity ? `${identity.name} / ${identity.work} / ${identity.variant}` : "身份未确认"}\n核对特征：${subject.matched_traits.join("；") || "证据不足"}\n二创改动：${subject.changes || "未记录"}\n冲突：${subject.conflicts.join("；") || "无记录"}\n${identity ? `资料出处：${identity.source_url}` : `候选（未确认）：${subject.candidates.map(c => `${c.name}（${c.work}；距离${c.distance}）`).join("；")}`}`);
      }
      for (const warning of data.warnings || []) blocks.push(warning);
      for (const failed of data.failures) blocks.push(`未完成区间：${failed.start}–${failed.end} 秒；${failed.error}`);
      body.textContent = blocks.join("\n\n");
      body.style.whiteSpace = "pre-wrap";
      element.dataset.loaded = "1";
    } catch (error) { body.textContent = error.message; }
  }));
  container.querySelectorAll(".briefing-link").forEach((button) => button.addEventListener("click", () => {
    const card = button.closest(".update-card");
    const item = items.find((candidate) => updateKey(candidate) === card.dataset.updateKey);
    if (item) openBriefing(item);
  }));
  container.querySelectorAll(".summary-toggle").forEach((button) => button.addEventListener("click", async () => {
    const card = button.closest(".update-card");
    const expanded = card.classList.toggle("expanded");
    button.textContent = expanded ? "收起简报" : "展开简报";
    const item = items.find((candidate) => updateKey(candidate) === card.dataset.updateKey);
    if (expanded && item && !item.read) await markItemRead(item);
  }));
}

async function reanalyzeVideo(event) {
  const button = event.currentTarget;
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "正在加入队列…";
  try {
    const data = await api(`/api/videos/${encodeURIComponent(button.dataset.platform)}/${encodeURIComponent(button.dataset.articleId)}/reanalyze`, { method: "POST", ...(button.dataset.readingMode ? {body: JSON.stringify({reading_mode: button.dataset.readingMode})} : {}) });
    showToast(data.message);
    if (state.currentView === "briefing") await refreshNoteBriefing();
    else if (state.currentView === "source-detail") await loadSourceDetail(state.sourcePlatform, state.sourceUid);
    else await loadDashboard();
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
    button.textContent = originalText;
  }
}

function noteAnalysisButtons(item) {
  if (!item.link) return "";
  const busy = ["pending", "processing"].includes(item.last_attempt_status) || ["pending", "processing"].includes(item.ai_status);
  if (item.platform === "douyin" && item.content_type === "video") return `<button type="button" class="text-button reanalyze-button" data-platform="douyin" data-article-id="${escapeHtml(item.article_id)}" ${busy ? "disabled" : ""}>${busy ? "正在分析视频…" : "重新分析视频"}</button>`;
  if (busy) return `<button class="text-button" type="button" disabled>正在分析图文…</button>`;
  return ["quick", "full"].map(mode => `<button type="button" class="text-button reanalyze-button" data-platform="${escapeHtml(item.platform)}" data-article-id="${escapeHtml(item.article_id)}" data-reading-mode="${mode}" ${busy ? "disabled" : ""}>${busy ? "正在分析…" : mode === "full" ? "完整分析图文" : "重新分析图文（快速）"}</button>`).join("");
}

async function refreshNoteBriefing() {
  const item = state.briefingItem;
  if (!item || state.currentView !== "briefing") return;
  const data = await api(`/api/sources/${encodeURIComponent(item.platform)}/${encodeURIComponent(item.source_uid)}`);
  if (state.currentView !== "briefing" || state.briefingItem.article_id !== item.article_id) return;
  const updated = data.videos.find(row => row.article_id === item.article_id);
  if (updated) { state.briefingItem = updated; renderBriefing(updated); }
  if (updated && (["pending", "processing"].includes(updated.ai_status) || updated.last_attempt_status === "processing")) {
    setTimeout(() => refreshNoteBriefing().catch(error => showToast(error.message, true)), 5000);
  }
}

function collectionStatusText(status = {}) {
  return [status.message || "尚未检查", status.last_success_at ? `最近成功：${formatTime(status.last_success_at)}` : "尚无成功检查记录", status.pending_count ? `待补抓 ${status.pending_count} 篇` : "", status.scan_warning || ""].filter(Boolean).join(" · ");
}

function renderUnreadPanel(items = []) {
  const list = $("#unread-list");
  list.innerHTML = items.length ? items.map((item) => `<button class="unread-row" type="button" data-update-key="${escapeHtml(updateKey(item))}">
    <span class="unread-row-title">${escapeHtml(item.title)}</span>
    <span class="unread-row-meta">${escapeHtml(item.source || "未知作者")} · 发布时间：${escapeHtml(item.published || "未知")}</span>
    <span class="unread-row-action">查看简报 →</span>
  </button>`).join("") : `<div class="empty-state">目前没有未读消息。</div>`;
  list.querySelectorAll(".unread-row").forEach((button) => button.addEventListener("click", () => {
    const item = items.find((candidate) => updateKey(candidate) === button.dataset.updateKey);
    if (item) openBriefing(item);
  }));
}

function toggleUnreadPanel(force) {
  state.unreadPanelOpen = typeof force === "boolean" ? force : !state.unreadPanelOpen;
  $("#unread-panel").hidden = !state.unreadPanelOpen;
  $("#unread-summary-card").setAttribute("aria-expanded", String(state.unreadPanelOpen));
}

function syncReadCounters(result, item) {
  if (state.dashboard) {
    state.dashboard.unread_count = result.unread_count;
    state.dashboard.unread_updates = (state.dashboard.unread_updates || []).filter((candidate) => updateKey(candidate) !== updateKey(item));
    (state.dashboard.updates || []).forEach((candidate) => {
      if (updateKey(candidate) === updateKey(item)) candidate.read = true;
    });
    const source = (state.dashboard.sources || []).find((candidate) => candidate.platform === result.platform && String(candidate.uid) === String(result.source_uid));
    if (source) source.unread_count = result.source_unread_count;
  }
  $("#unread-count").textContent = result.unread_count;
  $("#latest-caption").textContent = result.unread_count ? `有 ${result.unread_count} 条内容等待查看` : "最近一次更新内容";
  renderUnreadPanel(state.dashboard?.unread_updates || []);
  $$('[data-update-key]').forEach((element) => {
    if (element.dataset.updateKey === updateKey(item)) element.classList.remove("unread");
  });
  $$(".source-card").forEach((card) => {
    if (card.dataset.platform !== result.platform || String(card.dataset.uid) !== String(result.source_uid)) return;
    const count = card.querySelector(".source-unread-count");
    if (count) count.textContent = result.source_unread_count;
  });
}

async function markItemRead(item) {
  if (item.read) return true;
  try {
    const result = await api(`/api/updates/${encodeURIComponent(item.platform)}/${encodeURIComponent(item.article_id)}/mark-read`, { method: "POST" });
    item.read = true;
    syncReadCounters(result, item);
    return true;
  } catch (error) {
    showToast(`未读状态更新失败：${error.message}`, true);
    return false;
  }
}

function renderBriefing(item) {
  $("#briefing-title").textContent = item.title || "简报详情";
  $("#briefing-source").textContent = item.source || "未知作者";
  $("#briefing-meta").textContent = `发布时间：${item.published || "未知"} · 检测时间：${formatTime(item.detected_at)}`;
  $("#briefing-summary").textContent = summaryText(item) + (item.ai_completeness === "partial" ? (isImageNote(item) ? "\n\n图文读取范围有限，请结合原笔记查看。" : "\n\n画面分析不完整，请结合原视频查看。") : "")
    + (item.last_attempt_status === "error" && item.ai_summary ? `\n\n最近一次重新分析失败，当前展示上次简报：${item.last_attempt_error || "请重试"}` : "");
  const original = $("#briefing-original-link");
  const originalUrl = safeExternalUrl(item.link);
  original.href = originalUrl || "#";
  original.textContent = item.platform === "douyin" ? "打开抖音原作品" : item.platform === "xiaohongshu" ? "打开小红书原笔记" : item.platform === "zhihu" ? "打开知乎原内容" : "打开B站原视频";
  const note = $("#briefing-note");
  note.hidden = !["xiaohongshu", "douyin"].includes(item.platform);
  if (!note.hidden) {
    const actions = $("#briefing-note-actions");
    actions.innerHTML = noteAnalysisButtons(item);
    actions.querySelectorAll?.(".reanalyze-button").forEach(button => button.addEventListener("click", reanalyzeVideo));
    $("#briefing-note-image-help").hidden = !isImageNote(item);
    $("#briefing-note-original > summary").textContent = item.platform === "douyin" ? "查看作品配文与话题" : "查看笔记原文与话题";
    $("#briefing-note-scope").textContent = !isImageNote(item) ? (item.ai_analysis_warnings || []).join("；") || "结合视频语音、画面文字和关键画面提炼内容。" : `${item.ai_note_reading_mode === "full" ? "完整" : "快速"}模式 · 共 ${item.image_count || 0} 张，已读 ${item.ai_frame_count || 0} 张。${(item.ai_analysis_warnings || []).join("；") || item.fetch_warning || ""}`;
    const evidence = item.ai_note_evidence || [];
    $("#briefing-note-evidence-details").hidden = !evidence.length;
    $("#briefing-note-evidence").textContent = evidence.map(row => row.type === "image"
      ? `来源：第 ${Number(row.number)} 张中的文字${row.status === "error" ? "（读取失败）" : row.status === "ocr_error" ? "（识别未完成）" : ""}\n${row.ocr || "未识别到文字"}`
      : `来源：第 ${(row.numbers || []).map(Number).join("、")} 张的内容解读\n${row.text || ""}`).join("\n\n");
    $("#briefing-note-body").textContent = (item.description || "没有读取到正文") + ((item.tags || []).length ? `\n\n话题：${item.tags.join("、")}` : "");
  }
  original.hidden = !originalUrl;
}

async function openBriefing(item) {
  if (!item.read && !(await markItemRead(item))) return;
  state.briefingBack = state.currentView === "source-detail" ? "source-detail" : "dashboard";
  state.briefingItem = item;
  for (const selector of ["#briefing-note-original", "#briefing-note-evidence-details", "#briefing-note-options"]) $(selector).open = false;
  renderBriefing(item);
  setView("briefing");
  if (["pending", "processing"].includes(item.ai_status) || item.last_attempt_status === "processing") setTimeout(() => refreshNoteBriefing().catch(error => showToast(error.message, true)), 5000);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function backFromBriefing() {
  if (state.briefingBack === "source-detail" && state.sourcePlatform && state.sourceUid) {
    const contentType = state.sourceContentType;
    await loadSourceDetail(state.sourcePlatform, state.sourceUid);
    if (contentType) showSourceContentType(contentType);
    return;
  }
  setView("dashboard");
  await loadDashboard();
}

function platformLabel(platform) {
  return ({zhihu: "知乎", bilibili: "哔哩哔哩", xiaohongshu: "小红书", douyin: "抖音"})[platform] || platform;
}

function renderPlatforms(container, sources) {
  const platforms = [
    { key: "bilibili", label: "哔哩哔哩", mark: "B", caption: "视频与AI简报" },
    { key: "zhihu", label: "知乎", mark: "知", caption: "回答、文章、想法与视频" },
    { key: "douyin", label: "抖音", mark: "抖", caption: "视频、图文与AI简报" },
    { key: "xiaohongshu", label: "小红书", mark: "红", caption: "图文笔记与AI简报" },
  ];
  container.innerHTML = platforms.map((platform) => {
    const matched = sources.filter((source) => source.platform === platform.key);
    const unread = matched.reduce((sum, source) => sum + Number(source.unread_count || 0), 0);
    return `<button class="platform-card ${platform.key}" type="button" data-platform="${platform.key}">
      <span class="platform-mark">${platform.mark}</span>
      <span class="platform-card-main"><strong>${platform.label}</strong><span>${platform.caption}</span></span>
      <span class="platform-card-stats"><b>${matched.length}</b> 位博主${unread ? `<em>${unread} 未读</em>` : ""}</span>
    </button>`;
  }).join("");
  container.querySelectorAll(".platform-card").forEach((card) => card.addEventListener("click", () => showPlatformSources(card.dataset.platform)));
}

function renderSources(container, sources) {
  container.innerHTML = sources.length ? sources.map((source) => `<article class="source-card" data-platform="${escapeHtml(source.platform)}" data-uid="${escapeHtml(source.uid)}">
    <div class="source-card-top"><h3 class="source-name">${escapeHtml(source.name)}</h3><span class="source-platform">${escapeHtml(source.category || "B站")}</span></div>
    <div class="source-uid">${source.platform === "zhihu" ? "账号" : "账号编号"} · ${escapeHtml(source.uid)}</div>
    ${["xiaohongshu", "douyin"].includes(source.platform) ? `<div class="setting-help">${escapeHtml(collectionStatusText(source.collection_status))}</div>` : ""}
    <div class="source-stats"><div><strong>${source.new_video_count || 0}</strong>已收录内容</div><div><strong class="source-unread-count">${source.unread_count || 0}</strong>未读</div></div>
  </article>`).join("") : `<div class="empty-state">还没有关注对象，点击右上角添加第一位博主。</div>`;
  $$(".source-card").forEach((card) => card.addEventListener("click", () => loadSourceDetail(card.dataset.platform, card.dataset.uid)));
}

function showPlatformSources(platform) {
  state.sourcesStage = "sources";
  state.selectedPlatform = platform;
  const sources = state.sources.filter((source) => source.platform === platform);
  $("#sources-heading").textContent = platformLabel(platform);
  $("#sources-description").textContent = platform === "douyin" ? "选择博主，查看视频与图文的文字简报。" : platform === "xiaohongshu" ? "选择博主，查看图文笔记和AI简报。" : platform === "zhihu" ? "选择博主后，再按回答、文章、想法等内容类型查看。" : "选择博主，查看已收录视频和AI简报。";
  $("#back-to-platforms").hidden = false;
  $("#source-list").className = "source-grid";
  renderSources($("#source-list"), sources);
}

function showPlatformPicker() {
  state.sourcesStage = "platforms";
  state.selectedPlatform = null;
  $("#sources-heading").textContent = "选择平台";
  $("#sources-description").textContent = "进入平台后选择关注的博主。";
  $("#back-to-platforms").hidden = true;
  $("#source-list").className = "platform-grid";
  renderPlatforms($("#source-list"), state.sources);
}

function setView(view) {
  if (view !== "dashboard" && state.dashboardTimer) {
    clearTimeout(state.dashboardTimer);
    state.dashboardTimer = null;
  }
  if (view !== "source-detail" && state.sourceTimer) {
    clearTimeout(state.sourceTimer);
    state.sourceTimer = null;
  }
  state.currentView = view;
  $$(".view").forEach((element) => element.classList.toggle("active", element.id === `view-${view}`));
  $$(".nav-item").forEach((element) => element.classList.toggle("active", element.dataset.view === view));
  const titles = { dashboard: "最新消息", sources: "关注列表", accounts: "账号管理", settings: "设置", "source-detail": "博主详情", briefing: "简报详情" };
  $("#view-title").textContent = titles[view] || "赛博情报局";
}

function renderAutomationStatus(scheduler) {
  const toggle = $("#automation-toggle");
  const status = $("#automation-status");
  const description = $("#automation-description");
  const unsupported = scheduler?.state === "unsupported";
  const enabled = Boolean(scheduler?.available && scheduler.enabled);
  toggle.checked = enabled;
  toggle.disabled = unsupported;
  status.textContent = enabled ? "已开启" : "已关闭";
  description.textContent = enabled
    ? "系统会按设定间隔自动抓取，错过的任务可能在开机后补跑。"
    : "不会随开机或定时任务自动抓取，仍可手动检查。";
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    state.dashboard = data;
    $("#unread-count").textContent = data.unread_count;
    const last = data.last_check;
    $("#last-check-time").textContent = last ? formatTime(last.checked_at) : "暂无检查记录";
    $("#last-check-message").textContent = last?.message || "计划任务启动后会在这里显示结果";
    const task = data.scheduler;
    $("#task-status").textContent = task?.available
      ? (task.enabled ? (task.state === "Ready" ? "自动抓取正常" : `任务：${task.state}`) : "自动抓取已关闭")
      : "计划任务状态未知";
    renderAutomationStatus(task);
    $("#latest-caption").textContent = data.unread_count ? `有 ${data.unread_count} 条内容等待查看` : "最近一次更新内容";
    renderUnreadPanel(data.unread_updates || []);
    renderUpdates($("#dashboard-updates"), data.updates, "暂时没有更新内容。后台检查到新内容后会出现在这里。");
    const stillProcessing = data.updates.some((item) => ["pending", "processing"].includes(item.ai_status) || item.last_attempt_status === "processing");
    if (state.dashboardTimer) clearTimeout(state.dashboardTimer);
    state.dashboardTimer = stillProcessing ? setTimeout(() => {
      if (state.currentView === "dashboard") loadDashboard();
    }, 5000) : null;
  } catch (error) { showToast(error.message, true); }
}

async function loadSources() {
  try { const data = await api("/api/sources"); state.sources = data.sources || []; showPlatformPicker(); }
  catch (error) { showToast(error.message, true); }
}

async function loadAccounts() {
  try {
    const data = await api("/api/accounts");
    const accounts = data.accounts || [];
    $("#account-list").innerHTML = accounts.map((account) => {
      const isZhihu = account.platform === "zhihu";
      const isXhs = account.platform === "xiaohongshu", isDouyin = account.platform === "douyin";
      const identity = account.status === "logged_in"
        ? (account.account_name || (isDouyin ? "抖音登录状态" : isXhs ? "小红书登录状态" : isZhihu ? "知乎登录状态" : "B站账号"))
        : (["expired", "credential_error"].includes(account.status)
          ? "登录凭证不可用，点击重新登录"
          : (account.status === "verification_required" ? "请打开官方窗口完成验证，再保存登录状态" : account.status === "unknown" ? "状态检查失败，仍可重新登录" : (isDouyin ? "点击打开抖音官方登录窗口" : isXhs ? "点击打开小红书登录窗口" : isZhihu ? "点击打开知乎登录窗口" : "点击扫码登录")));
      const avatarUrl = safeExternalUrl(account.avatar);
      const image = avatarUrl
        ? `<img class="account-avatar" src="${escapeHtml(avatarUrl)}" alt="账号头像">`
        : `<div class="account-logo ${isDouyin ? "douyin" : isXhs ? "xiaohongshu" : isZhihu ? "zhihu" : ""}">${isDouyin ? "抖" : isXhs ? "红" : isZhihu ? "知" : "B"}</div>`;
      const storage = account.credential_storage || {};
      const canLogout = account.status === "logged_in" || storage.encrypted || storage.legacy_plaintext;
      const logout = canLogout ? `<button class="text-button account-logout-button" data-platform="${escapeHtml(account.platform)}" type="button">删除登录状态</button>` : "";
      return `<article class="account-card" data-account-platform="${escapeHtml(account.platform)}">${image}<div class="account-main"><h3>${escapeHtml(account.name)}</h3><div class="account-subtitle">${escapeHtml(identity)}</div></div><div class="account-card-actions"><span class="account-status ${escapeHtml(account.status)}">${escapeHtml(account.status_label)}</span>${logout}</div></article>`;
    }).join("");
    $$('[data-account-platform="bilibili"]').forEach((card) => card.addEventListener("click", startQrLogin));
    $$('[data-account-platform="zhihu"]').forEach((card) => card.addEventListener("click", startZhihuLogin));
    $$('[data-account-platform="xiaohongshu"]').forEach((card) => card.addEventListener("click", startXiaohongshuLogin));
    $$('[data-account-platform="douyin"]').forEach((card) => card.addEventListener("click", startDouyinLogin));
    $$(".account-logout-button").forEach((button) => button.addEventListener("click", logoutAccount));
    const bilibiliStorage = accounts.find((account) => account.platform === "bilibili")?.credential_storage;
    const zhihuStorage = accounts.find((account) => account.platform === "zhihu")?.credential_storage;
    if (bilibiliStorage?.needs_relogin) {
      $("#credential-protection").textContent = "本机保存的B站登录凭证已无法解密。点击上方B站账号重新扫码，新凭证会安全覆盖旧文件。";
    } else if (bilibiliStorage?.legacy_plaintext || zhihuStorage?.legacy_plaintext) {
      $("#credential-protection").textContent = "检测到旧版明文凭证；下次读取或扫码登录时会自动迁移到Windows加密存储。";
    } else {
      const protectedPlatforms = accounts.filter(account => account.credential_storage?.encrypted).map(account => account.name);
      $("#credential-protection").textContent = protectedPlatforms.length
        ? `${protectedPlatforms.join("和")}登录凭证已使用 Windows 当前用户加密保护。`
        : "尚未保存平台登录凭证。登录后将使用 Windows 当前用户加密保护。";
    }
  } catch (error) { showToast(error.message, true); }
}

function hasDesktopBridge() {
  return Boolean(window.pywebview?.api);
}

async function startZhihuLogin(event) {
  if (event?.target?.closest(".account-logout-button")) return;
  if (!hasDesktopBridge()) {
    showToast("自动登录仅在赛博情报局桌面窗口中可用；请双击启动程序", true);
    return;
  }
  $("#zhihu-login-modal").hidden = false;
  $("#zhihu-login-status").textContent = "正在打开知乎登录窗口…";
  try {
    const result = await window.pywebview.api.start_zhihu_login();
    $("#zhihu-login-status").textContent = result.message;
  } catch (error) {
    $("#zhihu-login-status").textContent = `登录窗口打开失败：${error.message || error}`;
  }
}

async function finishZhihuLogin() {
  if (!hasDesktopBridge()) return;
  const button = $("#finish-zhihu-login");
  button.disabled = true;
  $("#zhihu-login-status").textContent = "正在检查并加密保存知乎登录状态…";
  try {
    const result = await window.pywebview.api.finish_zhihu_login();
    $("#zhihu-login-status").textContent = result.message;
    if (result.saved) {
      showToast(result.message);
      setTimeout(async () => { $("#zhihu-login-modal").hidden = true; await loadAccounts(); }, 700);
    }
  } catch (error) {
    $("#zhihu-login-status").textContent = `登录状态保存失败：${error.message || error}`;
  } finally {
    button.disabled = false;
  }
}

async function closeZhihuLoginModal() {
  if (hasDesktopBridge()) await window.pywebview.api.cancel_zhihu_login().catch(() => {});
  $("#zhihu-login-modal").hidden = true;
}

async function startXiaohongshuLogin(event) {
  if (event?.target?.closest(".account-logout-button")) return;
  if (!hasDesktopBridge()) {
    showToast("自动登录仅在赛博情报局桌面窗口中可用；请双击启动程序", true);
    return;
  }
  $("#xiaohongshu-login-modal").hidden = false;
  $("#finish-xiaohongshu-login").disabled = false;
  $("#finish-xiaohongshu-login").textContent = "保存登录状态";
  $("#reopen-xiaohongshu-login").hidden = false;
  $("#xiaohongshu-login-status").textContent = "正在打开小红书登录窗口…";
  try {
    const result = await window.pywebview.api.start_xiaohongshu_login();
    $("#xiaohongshu-login-status").textContent = result.message;
  } catch (error) {
    $("#xiaohongshu-login-status").textContent = `登录窗口打开失败：${error.message || error}`;
  }
}

async function finishXiaohongshuLogin() {
  if (!hasDesktopBridge()) return;
  const button = $("#finish-xiaohongshu-login");
  button.disabled = true;
  $("#xiaohongshu-login-status").textContent = "正在检查并加密保存小红书登录状态…";
  try {
    const result = await window.pywebview.api.finish_xiaohongshu_login();
    $("#xiaohongshu-login-status").textContent = result.message;
    if (result.saved) {
      showToast(result.message);
      button.textContent = "已加密保存";
      $("#reopen-xiaohongshu-login").hidden = true;
      $("#xiaohongshu-login-status").textContent = result.message + "。可关闭此弹窗；后续实际读取会验证登录是否有效。";
      await loadAccounts();
    }
  } catch (error) {
    $("#xiaohongshu-login-status").textContent = `登录状态保存失败：${error.message || error}`;
  } finally {
    button.disabled = button.textContent === "已加密保存";
  }
}

async function closeXiaohongshuLoginModal() {
  if (hasDesktopBridge()) await window.pywebview.api.cancel_xiaohongshu_login().catch(() => {});
  $("#xiaohongshu-login-modal").hidden = true;
}

async function startDouyinLogin(event) {
  if (event?.target?.closest(".account-logout-button")) return;
  if (!hasDesktopBridge()) {
    showToast("自动登录仅在赛博情报局桌面窗口中可用；请双击启动程序", true);
    return;
  }
  $("#douyin-login-modal").hidden = false;
  $("#finish-douyin-login").disabled = false;
  $("#finish-douyin-login").textContent = "保存登录状态";
  $("#reopen-douyin-login").hidden = false;
  $("#douyin-login-status").textContent = "正在打开抖音登录窗口…";
  try {
    const result = await window.pywebview.api.start_douyin_login();
    $("#douyin-login-status").textContent = result.message;
  } catch (error) {
    $("#douyin-login-status").textContent = `登录窗口打开失败：${error.message || error}`;
  }
}

async function finishDouyinLogin() {
  if (!hasDesktopBridge()) return;
  const button = $("#finish-douyin-login");
  button.disabled = true;
  $("#douyin-login-status").textContent = "正在检查并加密保存抖音登录状态…";
  try {
    const result = await window.pywebview.api.finish_douyin_login();
    $("#douyin-login-status").textContent = result.message;
    if (result.saved) {
      showToast(result.message);
      button.textContent = "已加密保存";
      $("#reopen-douyin-login").hidden = true;
      $("#douyin-login-status").textContent = result.message + "。可关闭此弹窗；后续实际读取会验证登录是否有效。";
      await loadAccounts();
    }
  } catch (error) {
    $("#douyin-login-status").textContent = `登录状态保存失败：${error.message || error}`;
  } finally {
    button.disabled = button.textContent === "已加密保存";
  }
}

async function closeDouyinLoginModal() {
  if (hasDesktopBridge()) await window.pywebview.api.cancel_douyin_login().catch(() => {});
  $("#douyin-login-modal").hidden = true;
}

async function logoutAccount(event) {
  event.stopPropagation();
  const platform = event.currentTarget.dataset.platform;
  const platformLabel = ({zhihu: "知乎", bilibili: "B站", xiaohongshu: "小红书", douyin: "抖音"})[platform] || platform;
  if (!window.confirm(`确定删除本机保存的${platformLabel}登录状态吗？`)) return;
  if (platform === "bilibili") stopQrPolling();
  try {
    const result = await api(`/api/accounts/${encodeURIComponent(platform)}/logout`, { method: "POST" });
    showToast(result.message);
    await loadAccounts();
  } catch (error) { showToast(error.message, true); }
}

function syncSourceFormPlatform() {
  const platform = $("#source-platform").value;
  const isZhihu = platform === "zhihu", isXhs = platform === "xiaohongshu", isDouyin = platform === "douyin";
  $("#source-id-label").textContent = isXhs || isDouyin ? "博主主页链接或账号ID" : isZhihu ? "主页链接或网址标识" : "账号编号";
  $("#source-uid").inputMode = isZhihu || isXhs || isDouyin ? "url" : "numeric";
  $("#source-uid").placeholder = isDouyin ? "https://www.douyin.com/user/…" : isXhs ? "https://www.xiaohongshu.com/user/profile/…" : isZhihu ? "https://www.zhihu.com/org/zhi-hu-ri-bao-51-41" : "例如：21131684";
  $("#zhihu-cookie-field").hidden = !(isZhihu || isXhs || isDouyin);
  $("#zhihu-cookie-field .form-label").textContent = `手动填写${isDouyin ? "抖音" : isXhs ? "小红书" : "知乎"} Cookie（备用）`;
  $("#source-cookie").value = "";
}

function openSourceModal() {
  $("#source-form").reset();
  $("#source-form-error").textContent = "";
  syncSourceFormPlatform();
  $("#source-modal").hidden = false;
  $("#source-uid").focus();
}

function stopQrPolling() {
  if (state.qrTimer) clearInterval(state.qrTimer);
  state.qrTimer = null;
  state.qrKey = null;
}

async function startQrLogin() {
  stopQrPolling();
  $("#account-modal").hidden = false;
  $("#bilibili-qr").removeAttribute("src");
  $("#qr-status").textContent = "正在生成二维码…";
  try {
    const data = await api("/api/accounts/bilibili/qr/start", { method: "POST" });
    state.qrKey = data.qrcode_key;
    $("#bilibili-qr").src = data.image;
    $("#qr-status").textContent = "请使用哔哩哔哩手机客户端扫码";
    state.qrTimer = setInterval(pollQrLogin, 1800);
  } catch (error) { $("#qr-status").textContent = error.message; showToast(error.message, true); }
}

async function pollQrLogin() {
  if (!state.qrKey) return;
  try {
    const data = await api("/api/accounts/bilibili/qr/status", { method: "POST", body: JSON.stringify({ qrcode_key: state.qrKey }) });
    $("#qr-status").textContent = data.message;
    if (data.status === "success") {
      stopQrPolling();
      showToast("B站账号登录成功");
      setTimeout(async () => { $("#account-modal").hidden = true; await loadAccounts(); }, 700);
    } else if (data.status === "expired") {
      stopQrPolling();
    }
  } catch (error) { $("#qr-status").textContent = error.message; stopQrPolling(); }
}

async function loadSourceDetail(platform, uid) {
  try {
    const previousContentType = state.sourcePlatform === platform && state.sourceUid === uid ? state.sourceContentType : null;
    state.sourcePlatform = platform;
    state.sourceUid = uid;
    const data = await api(`/api/sources/${encodeURIComponent(platform)}/${encodeURIComponent(uid)}`);
    $("#detail-source-name").textContent = data.source.name;
    state.sourceName = data.source.name;
    const platformLabel = data.source.platform === "douyin" ? "抖音账号" : data.source.platform === "xiaohongshu" ? "小红书账号" : data.source.platform === "zhihu" ? "知乎账号" : "B站账号编号";
    $("#detail-source-meta").textContent = `${platformLabel} ${data.source.uid} · ${data.source.unread_count || 0} 条未读 · 添加时间：${formatTime(data.source.added_at)} · ${platform === "douyin" ? "首次展示最近3条作品，后续检查公开主页新内容；读取范围有限时会提示" : platform === "xiaohongshu" ? "首次展示3篇，之后增量补抓，每次最多读取20篇；超过范围会提示" : "最近3条初始展示，之后每次最多追加3条新内容"}${["xiaohongshu", "douyin"].includes(platform) ? "\n" + collectionStatusText(data.source.collection_status) : ""}`;
    state.sourceDetailData = data;
    state.sourceContentType = ["zhihu", "douyin"].includes(platform) ? previousContentType : "video";
    const typeOrder = ["图文", "文章", "回答", "想法", "提问", "视频", "其他"];
    const grouped = Object.groupBy ? Object.groupBy(data.videos, (item) => item.content_type_label || (item.platform === "bilibili" ? "视频" : "其他")) : data.videos.reduce((acc, item) => { const key = item.content_type_label || (item.platform === "bilibili" ? "视频" : "其他"); (acc[key] ||= []).push(item); return acc; }, {});
    const detailContainer = $("#source-videos");
    const typeMenu = $("#detail-content-types");
    if (["zhihu", "douyin"].includes(platform)) {
      typeMenu.hidden = false;
      typeMenu.innerHTML = typeOrder.filter((type) => grouped[type]?.length).map((type) => {
        const items = grouped[type];
        const unread = items.filter((item) => !item.read).length;
        const key = items[0].content_type || "other";
        return `<button class="content-type-card" type="button" data-content-type="${escapeHtml(key)}"><span><strong>${escapeHtml(type)}</strong><small>${items.length} 条内容</small></span>${unread ? `<b>${unread}<small>未读</small></b>` : "<i>已读</i>"}</button>`;
      }).join("") || `<div class="empty-state">暂时还没有已收录内容。</div>`;
      typeMenu.querySelectorAll(".content-type-card").forEach((button) => button.addEventListener("click", () => showSourceContentType(button.dataset.contentType)));
      detailContainer.innerHTML = `<div class="empty-state type-select-hint">请选择上方内容类型。</div>`;
      if (state.sourceContentType && data.videos.some((item) => (item.content_type || "other") === state.sourceContentType)) {
        showSourceContentType(state.sourceContentType);
      }
    } else {
      typeMenu.hidden = true;
      detailContainer.innerHTML = "";
      typeOrder.filter((type) => grouped[type]?.length).forEach((type) => {
      const section = document.createElement("section");
      section.className = "content-type-section";
      section.innerHTML = `<div class="panel-title"><h3>${escapeHtml(type)}</h3><span>${grouped[type].length} 条</span></div><div class="content-type-list"></div>`;
      detailContainer.appendChild(section);
      renderUpdates(section.querySelector(".content-type-list"), grouped[type], `暂无${type}。`);
      });
    }
    if (!data.videos.length) detailContainer.innerHTML = `<div class="empty-state">${platform === "xiaohongshu" ? "近期暂无图文笔记，之后发布的图文会自动收录。" : "暂时还没有可展示的内容。"}</div>`;
    const failedCount = data.videos.filter((video) => ["bilibili", "article", "zvideo", "video", "image_note"].includes(video.platform === "bilibili" ? "bilibili" : video.content_type) && video.ai_status === "error").length;
    $("#retry-source-summaries").hidden = failedCount === 0;
    $("#retry-source-summaries").textContent = `重试失败简报（${failedCount}）`;
    if (data.warning) showToast(data.warning, true);
    setView("source-detail");
    const stillProcessing = data.videos.some((video) => ["pending", "processing"].includes(video.ai_status) || video.last_attempt_status === "processing");
    if (state.sourceTimer) clearTimeout(state.sourceTimer);
    state.sourceTimer = stillProcessing ? setTimeout(() => {
      if (state.currentView === "source-detail") loadSourceDetail(state.sourcePlatform, state.sourceUid);
    }, 5000) : null;
  } catch (error) { showToast(error.message, true); }
}

function showSourceContentType(contentType) {
  const data = state.sourceDetailData;
  if (!data) return;
  state.sourceContentType = contentType;
  const items = data.videos.filter((item) => (item.content_type || "other") === contentType);
  const detailContainer = $("#source-videos");
  detailContainer.innerHTML = "";
  renderUpdates(detailContainer, items, "这个分类暂时没有内容。");
  $("#detail-content-types").querySelectorAll(".content-type-card").forEach((button) => button.classList.toggle("selected", button.dataset.contentType === contentType));
}

function closeDeleteSourceModal() {
  $("#delete-source-modal").hidden = true;
}

function openDeleteSourceModal() {
  if (!state.sourcePlatform || !state.sourceUid) return;
  $("#delete-source-message").textContent = `确定要从关注列表删除“${state.sourceName || "这位博主"}”吗？`;
  $("#delete-source-modal").hidden = false;
  $("#confirm-delete-source").focus();
}

async function confirmDeleteSource() {
  if (!state.sourcePlatform || !state.sourceUid) return;
  const button = $("#confirm-delete-source");
  button.disabled = true;
  try {
    const data = await api(`/api/sources/${encodeURIComponent(state.sourcePlatform)}/${encodeURIComponent(state.sourceUid)}`, { method: "DELETE" });
    closeDeleteSourceModal();
    if (state.sourceTimer) clearTimeout(state.sourceTimer);
    state.sourcePlatform = null;
    state.sourceUid = null;
    state.sourceName = null;
    showToast(data.message);
    setView("sources");
    await loadSources();
    await loadDashboard();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadSettings() {
  try {
    const data = await api("/api/settings");
    fillAiProviderForm(data.ai_provider || {});
    renderAiKeyStatus(data.ai_provider || {});
    fillVideoAnalysisForm(data.video_analysis || {});
    await loadMediaKnowledge();
    const current = data.interval_hours;
    $("#interval-options").innerHTML = data.interval_options.map((option) => `<label class="interval-option ${option.hours === current ? "selected" : ""}"><span>${escapeHtml(option.label)}</span><input type="radio" name="interval" value="${option.hours}" ${option.hours === current ? "checked" : ""}></label>`).join("");
    $$("input[name=interval]").forEach((input) => input.addEventListener("change", updateInterval));
    const history = data.check_history || [];
    $("#check-history").innerHTML = history.length ? history.map((item) => `<div class="history-row"><i class="history-state ${item.status === "error" ? "error" : (item.status === "warning" ? "warning" : "")}"></i><div><div class="history-time">${escapeHtml(formatTime(item.checked_at))}</div><div class="history-message">${escapeHtml(item.message)}</div></div></div>`).join("") : `<div class="empty-state">还没有结构化检查记录。</div>`;
  } catch (error) { showToast(error.message, true); }
}

let mediaKnowledgeEntries = [];

function fillVideoAnalysisForm(settings) {
  $("#video-analysis-enabled").checked = settings.enabled !== false;
  $("#video-input-mode").value = settings.input_mode || "images";
  $("#video-segment-seconds").value = settings.segment_seconds || 20;
  $("#video-max-segments").value = settings.max_segments || 10;
  $("#video-frames-per-segment").value = settings.frames_per_segment || 8;
  $("#video-ocr-enabled").checked = settings.ocr_enabled !== false;
  $("#video-online-lookup").checked = settings.online_lookup !== false;
}

function videoAnalysisDraft() {
  return {enabled: $("#video-analysis-enabled").checked, input_mode: $("#video-input-mode").value,
    segment_seconds: Number($("#video-segment-seconds").value), max_segments: Number($("#video-max-segments").value),
    frames_per_segment: Number($("#video-frames-per-segment").value), ocr_enabled: $("#video-ocr-enabled").checked,
    online_lookup: $("#video-online-lookup").checked};
}

async function testVideoInput(event) {
  const button = event.currentTarget;
  const draft = videoAnalysisDraft();
  button.disabled = true;
  $("#video-analysis-result").textContent = "正在测试所选输入方式…";
  try {
    const data = await api("/api/settings/video-analysis/test", {method: "POST", body: JSON.stringify(draft)});
    $("#video-analysis-result").textContent = JSON.stringify(draft) === JSON.stringify(videoAnalysisDraft()) ? data.message : "测试期间设置已变更，请重新测试。";
  } catch (error) { $("#video-analysis-result").textContent = error.message; }
  finally { button.disabled = false; }
}

async function updateVideoAnalysis(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const data = await api("/api/settings/video-analysis", {method: "PUT", body: JSON.stringify(videoAnalysisDraft())});
    fillVideoAnalysisForm(data.video_analysis);
    $("#video-analysis-result").textContent = data.message;
  } catch (error) { $("#video-analysis-result").textContent = error.message; }
  finally { button.disabled = false; }
}

async function loadMediaKnowledge() {
  const data = await api("/api/knowledge");
  mediaKnowledgeEntries = data.entries || [];
  const status = data.character_models || {};
  const modelInfo = `<p class="setting-help">人物原型识别：${status.ccip_ready ? "CCIP已安装" : "CCIP待安装"}；${status.wd_ready ? "WD辅助标签已安装" : "WD辅助标签待安装"}。已收录${mediaKnowledgeEntries.filter(e => e.reference_images?.length).length}个有图条目。图库外角色会保留未知，可在下方补充不同作品与形象版本。</p>`;
  $("#media-knowledge-list").innerHTML = mediaKnowledgeEntries.length ? mediaKnowledgeEntries.map(entry =>
    `<p class="setting-help"><strong>${escapeHtml(entry.name)}</strong>${entry.work ? ` · ${escapeHtml(entry.work)}` : ""} · ${escapeHtml(entry.variant || "默认形象")} · ${entry.reference_images?.length || 0}张参考图<br>${escapeHtml(entry.text.slice(0, 140))}</p>`
  ).join("") : `<p class="setting-help">尚未添加资料。仍可分析视频，并按需查询陌生作品。</p>`;
  $("#media-knowledge-list").innerHTML = modelInfo + `<details><summary>查看全部${mediaKnowledgeEntries.length}个资料条目</summary>${$("#media-knowledge-list").innerHTML}</details>`;
}

async function addMediaKnowledge(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const images = [...($("#knowledge-images").files || [])];
    if (images.length > 5 || images.some(file => file.size > 5 * 1024 * 1024)) throw new Error("参考图最多5张，每张不超过5 MB");
    const referenceImages = [];
    for (const file of images) {
      const uploaded = await api("/api/knowledge/reference-image", {method: "POST", body: file, headers: {"Content-Type": file.type || "application/octet-stream"}});
      referenceImages.push(uploaded.reference_image);
    }
    const entry = {id: `entry_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`, name: $("#knowledge-name").value.trim(),
      work: $("#knowledge-work").value.trim(), aliases: $("#knowledge-aliases").value.split(/[,，]/).map(value => value.trim()).filter(Boolean),
      variant: $("#knowledge-variant").value.trim() || "默认形象", appearance: $("#knowledge-appearance").value.trim(),
      text: $("#knowledge-text").value.trim(), source_url: $("#knowledge-source").value.trim(), reference_images: referenceImages, updated_at: new Date().toISOString()};
    const data = await api("/api/knowledge", {method: "POST", body: JSON.stringify({entries: [entry]})});
    $("#media-knowledge-result").textContent = data.message;
    event.currentTarget.reset();
    await loadMediaKnowledge();
  } catch (error) { $("#media-knowledge-result").textContent = error.message; }
  finally { button.disabled = false; }
}

function renderAiKeyStatus(provider) {
  const status = provider.api_key || {};
  const element = $("#ai-key-status");
  element.classList.toggle("setting-error", Boolean(status.needs_reconfigure));
  if (status.needs_reconfigure) {
    element.textContent = "旧版 AI 密钥无法解密，请重新输入并保存。保存后会覆盖旧凭证。";
  } else if (status.configured) {
    element.textContent = `已保存 ${provider.model || "AI 简报模型"} 的接入设置。请用“测试连接”确认模型可用。密钥内容不会显示。`;
  } else {
    element.textContent = "尚未配置 AI 密钥，AI 简报暂时无法生成。";
  }
}

function fillAiProviderForm(provider) {
  $("#ai-api-url").value = provider.api_url || "";
  $("#ai-model").value = provider.model || "";
  $("#ai-vision-enabled").checked = Boolean(provider.vision_enabled);
  $("#ai-vision-model").value = provider.vision_model || "";
  $("#ai-vision-model-field").hidden = !provider.vision_enabled;
}

function aiProviderDraft() {
  return {
    api_url: $("#ai-api-url").value.trim(), model: $("#ai-model").value.trim(),
    api_key: $("#api-key-input").value.trim(),
    vision_enabled: $("#ai-vision-enabled").checked,
    vision_model: $("#ai-vision-model").value.trim(),
  };
}

async function probeAiProvider(readModels = false) {
  const draft = aiProviderDraft();
  const result = $("#ai-test-result");
  result.classList.remove("setting-error");
  if (!draft.api_url || (!readModels && !draft.model)) {
    result.textContent = readModels ? "请填写接口 URL。" : "请填写接口 URL 和模型名称。";
    result.classList.add("setting-error");
    return;
  }
  const buttons = [$("#ai-test-connection"), $("#ai-read-models"), $("#api-key-form").querySelector("button[type=submit]")];
  buttons.forEach(button => { button.disabled = true; });
  result.textContent = readModels ? "正在读取可用模型…" : "正在测试实际生成，请稍候…";
  try {
    const data = await api(`/api/settings/ai-provider/${readModels ? "models" : "test"}`, {
      method: "POST", body: JSON.stringify(readModels ? { api_url: draft.api_url, api_key: draft.api_key } : draft),
    });
    if (JSON.stringify(aiProviderDraft()) !== JSON.stringify(draft)) {
      result.textContent = "测试期间设置已变更，请使用当前设置重新测试。";
      return;
    }
    if (readModels) {
      $("#ai-model-options").innerHTML = data.models.map(model => `<option value="${escapeHtml(model)}"></option>`).join("");
      if (!data.models.includes(draft.model)) $("#ai-model").value = data.models[0] || "";
      result.textContent = `${data.message} 可用模型：${data.models.join("、")}`;
    } else {
      result.textContent = `${data.message} ${data.checks.map(check => `${check.mode === "vision" ? "图片" : "文字"}：${check.model}（${(check.elapsed_ms / 1000).toFixed(1)} 秒）`).join("；")}`;
    }
  } catch (error) {
    result.textContent = error.message;
    result.classList.add("setting-error");
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}

async function updateAiProvider(event) {
  event.preventDefault();
  const input = $("#api-key-input");
  const button = event.currentTarget.querySelector("button[type=submit]");
  const result = $("#api-key-result");
  const apiUrl = $("#ai-api-url").value.trim();
  const model = $("#ai-model").value.trim();
  if (!apiUrl || !model) {
    result.textContent = "请填写接口 URL 和模型名称。";
    result.classList.add("setting-error");
    return;
  }
  button.disabled = true;
  result.classList.remove("setting-error");
  result.textContent = "正在保存 AI 接入设置…";
  try {
    const data = await api("/api/settings/ai-provider", { method: "PUT", body: JSON.stringify(aiProviderDraft()) });
    input.value = "";
    result.textContent = data.message;
    fillAiProviderForm(data.ai_provider);
    renderAiKeyStatus(data.ai_provider);
    showToast("AI 接入设置已更新");
  } catch (error) {
    result.textContent = error.message;
    result.classList.add("setting-error");
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function updateInterval(event) {
  const hours = Number(event.target.value);
  $$(".interval-option").forEach((item) => item.classList.toggle("selected", item.querySelector("input").checked));
  $("#interval-result").textContent = "正在更新Windows计划任务…";
  try {
    const data = await api("/api/settings/interval", { method: "PUT", body: JSON.stringify({ interval_hours: hours }) });
    $("#interval-result").textContent = data.scheduler?.enabled
      ? `已设置为${data.label}，计划任务已同步。`
      : `已设置为${data.label}，自动抓取仍保持关闭。`;
    showToast("检查间隔已更新");
  } catch (error) { $("#interval-result").textContent = error.message; showToast(error.message, true); }
}

async function updateAutomation(event) {
  const toggle = event.currentTarget;
  const requestedState = toggle.checked;
  toggle.disabled = true;
  $("#automation-status").textContent = requestedState ? "正在开启" : "正在关闭";
  try {
    const data = await api("/api/settings/automation", {
      method: "PUT",
      body: JSON.stringify({ enabled: requestedState }),
    });
    renderAutomationStatus({ ...(data.scheduler || {}), available: true, enabled: data.enabled });
    $("#task-status").textContent = data.enabled ? "自动抓取正常" : "自动抓取已关闭";
    showToast(data.message);
  } catch (error) {
    toggle.checked = !requestedState;
    toggle.disabled = false;
    $("#automation-status").textContent = toggle.checked ? "已开启" : "已关闭";
    showToast(error.message, true);
  }
}

async function addSource(event) {
  event.preventDefault();
  const isZhihu = $("#source-platform").value === "zhihu";
  $("#source-form-error").textContent = $("#source-platform").value === "douyin" ? "正在读取抖音博主的公开作品，请稍候…" : $("#source-platform").value === "xiaohongshu" ? "正在读取小红书博主的图文笔记，请稍候…" : isZhihu ? "正在验证知乎账号并读取当前内容…" : "正在验证B站账号并读取当前视频…";
  try {
    const data = await api("/api/sources", { method: "POST", body: JSON.stringify({ platform: $("#source-platform").value, uid: $("#source-uid").value.trim(), name: $("#source-name").value.trim() || null, cookie: $("#source-cookie").value.trim() || null }) });
    $("#source-modal").hidden = true;
    event.target.reset();
    syncSourceFormPlatform();
    $("#source-form-error").textContent = "";
    showToast(data.message);
    await loadSources();
    await loadDashboard();
  } catch (error) { $("#source-form-error").textContent = error.message; }
}

async function markRead() {
  try { await api("/api/updates/mark-read", { method: "POST" }); showToast("已将全部消息标记为已读"); await loadDashboard(); }
  catch (error) { showToast(error.message, true); }
}

async function retrySourceSummaries() {
  if (!state.sourcePlatform || !state.sourceUid) return;
  const button = $("#retry-source-summaries");
  button.disabled = true;
  try {
    const data = await api(`/api/sources/${encodeURIComponent(state.sourcePlatform)}/${encodeURIComponent(state.sourceUid)}/retry-summaries`, { method: "POST" });
    showToast(data.message);
    await loadSourceDetail(state.sourcePlatform, state.sourceUid);
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
}

async function checkNow() {
  try {
    const data = await api("/api/check-now", { method: "POST" });
    showToast(data.message);
    [2000, 6000, 12000, 25000].forEach((delay) => setTimeout(() => {
      if (state.currentView === "dashboard") loadDashboard();
    }, delay));
  }
  catch (error) { showToast(error.message, true); }
}

function bindEvents() {
  $("#sidebar-toggle").addEventListener("click", () => {
    const shell = $(".app-shell");
    const collapsed = shell.classList.toggle("sidebar-collapsed");
    const toggle = $("#sidebar-toggle");
    toggle.textContent = collapsed ? "›" : "‹";
    toggle.setAttribute("aria-label", collapsed ? "展开侧边栏" : "收起侧边栏");
    toggle.title = collapsed ? "展开侧边栏" : "收起侧边栏";
  });
  $$(".nav-item").forEach((item) => item.addEventListener("click", async () => { setView(item.dataset.view); if (item.dataset.view === "dashboard") await loadDashboard(); if (item.dataset.view === "sources") await loadSources(); if (item.dataset.view === "accounts") await loadAccounts(); if (item.dataset.view === "settings") await loadSettings(); }));
  $$('[data-view-target="sources"]').forEach((item) => item.addEventListener("click", async () => { setView("sources"); await loadSources(); }));
  $("#back-to-sources").addEventListener("click", async () => {
    const platform = state.sourcePlatform;
    setView("sources");
    await loadSources();
    if (platform) showPlatformSources(platform);
  });
  $("#back-to-platforms").addEventListener("click", showPlatformPicker);
  $("#unread-summary-card").addEventListener("click", () => toggleUnreadPanel());
  $("#unread-summary-card").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggleUnreadPanel();
    }
  });
  $("#close-unread-panel").addEventListener("click", () => toggleUnreadPanel(false));
  $("#mark-read-dashboard").addEventListener("click", (event) => {
    event.stopPropagation();
    markRead();
  });
  $("#back-from-briefing").addEventListener("click", backFromBriefing);
  $("#check-now").addEventListener("click", checkNow);
  $("#automation-toggle").addEventListener("change", updateAutomation);
  $("#open-add-source").addEventListener("click", openSourceModal);
  $("#close-source-modal").addEventListener("click", () => { $("#source-modal").hidden = true; });
  $("#source-modal").addEventListener("click", (event) => { if (event.target.id === "source-modal") event.currentTarget.hidden = true; });
  $("#source-form").addEventListener("submit", addSource);
  $("#source-platform").addEventListener("change", syncSourceFormPlatform);
  $("#retry-source-summaries").addEventListener("click", retrySourceSummaries);
  $("#delete-source").addEventListener("click", openDeleteSourceModal);
  $("#close-delete-source-modal").addEventListener("click", closeDeleteSourceModal);
  $("#cancel-delete-source").addEventListener("click", closeDeleteSourceModal);
  $("#confirm-delete-source").addEventListener("click", confirmDeleteSource);
  $("#delete-source-modal").addEventListener("click", (event) => { if (event.target.id === "delete-source-modal") closeDeleteSourceModal(); });
  $("#close-account-modal").addEventListener("click", () => { stopQrPolling(); $("#account-modal").hidden = true; });
  $("#account-modal").addEventListener("click", (event) => { if (event.target.id === "account-modal") { stopQrPolling(); event.currentTarget.hidden = true; } });
  $("#refresh-qr").addEventListener("click", startQrLogin);
  $("#finish-zhihu-login").addEventListener("click", finishZhihuLogin);
  $("#reopen-zhihu-login").addEventListener("click", startZhihuLogin);
  $("#close-zhihu-login-modal").addEventListener("click", closeZhihuLoginModal);
  $("#zhihu-login-modal").addEventListener("click", (event) => { if (event.target.id === "zhihu-login-modal") closeZhihuLoginModal(); });
  $("#finish-xiaohongshu-login").addEventListener("click", finishXiaohongshuLogin);
  $("#reopen-xiaohongshu-login").addEventListener("click", startXiaohongshuLogin);
  $("#close-xiaohongshu-login-modal").addEventListener("click", closeXiaohongshuLoginModal);
  $("#xiaohongshu-login-modal").addEventListener("click", (event) => { if (event.target.id === "xiaohongshu-login-modal") closeXiaohongshuLoginModal(); });
  $("#finish-douyin-login").addEventListener("click", finishDouyinLogin);
  $("#reopen-douyin-login").addEventListener("click", startDouyinLogin);
  $("#close-douyin-login-modal").addEventListener("click", closeDouyinLoginModal);
  $("#douyin-login-modal").addEventListener("click", (event) => { if (event.target.id === "douyin-login-modal") closeDouyinLoginModal(); });
  $("#api-key-form").addEventListener("submit", updateAiProvider);
  $("#video-analysis-form").addEventListener("submit", updateVideoAnalysis);
  $("#test-video-input").addEventListener("click", testVideoInput);
  $("#media-knowledge-form").addEventListener("submit", addMediaKnowledge);
  $("#ai-test-connection").addEventListener("click", () => probeAiProvider());
  $("#ai-read-models").addEventListener("click", () => probeAiProvider(true));
  ["#ai-api-url", "#api-key-input", "#ai-model", "#ai-vision-enabled", "#ai-vision-model"].forEach(selector => {
    $(selector).addEventListener("input", () => {
      $("#ai-test-result").textContent = "设置已变更，请重新测试连接。";
      $("#ai-test-result").classList.remove("setting-error");
      if (selector === "#ai-api-url" || selector === "#api-key-input") $("#ai-model-options").innerHTML = "";
    });
  });
  $("#ai-vision-enabled").addEventListener("change", (event) => {
    $("#ai-vision-model-field").hidden = !event.target.checked;
  });
  $("#interface-font-size").addEventListener("input", (event) => {
    applyFontSizes(event.target.value, $("#briefing-font-size").value, true);
  });
  $("#briefing-font-size").addEventListener("input", (event) => {
    applyFontSizes($("#interface-font-size").value, event.target.value, true);
  });
  $("#reset-font-settings").addEventListener("click", () => {
    applyFontSizes(FONT_DEFAULTS.interfaceSize, FONT_DEFAULTS.briefingSize, true);
    showToast("字体大小已恢复默认");
  });
}

function updateClock() { $("#current-time").textContent = new Date().toLocaleString("zh-CN", { hour12: false }); }

initializeFontSettings();
bindEvents();
updateClock();
setInterval(updateClock, 1000);
loadDashboard();
