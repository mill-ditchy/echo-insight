const state = { currentView: "dashboard", dashboard: null, dashboardRequestId: 0, sources: [], sourcesStage: "platforms", selectedPlatform: null, sourceContentType: null, sourceDetailData: null, sourceRequestId: 0, sourceRequestTarget: null, deleteSourceTarget: null, qrKey: null, qrTimer: null, sourceTimer: null, dashboardTimer: null, sourcePlatform: null, sourceUid: null, sourceName: null, unreadPanelOpen: false, briefingBack: "dashboard", briefingItem: null };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const FONT_DEFAULTS = { interfaceSize: 15, briefingSize: 16 };
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

function setSidebarCollapsed(collapsed, persist = false) {
  $(".app-shell").classList.toggle("sidebar-collapsed", collapsed);
  const toggle = $("#sidebar-toggle");
  const expanded = window.matchMedia("(max-width: 900px)").matches || !collapsed;
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute("aria-label", expanded ? "收起侧边栏" : "展开侧边栏");
  toggle.title = expanded ? "收起侧边栏" : "展开侧边栏";
  if (persist) {
    try { localStorage.setItem("cyber-intel-sidebar-collapsed", String(collapsed)); }
    catch (_error) { /* 无法保存时，收起操作仍在当前窗口生效。 */ }
  }
}

function initializeSidebar() {
  let collapsed = false;
  try { collapsed = localStorage.getItem("cyber-intel-sidebar-collapsed") === "true"; }
  catch (_error) { /* 使用默认展开状态。 */ }
  setSidebarCollapsed(collapsed);
  window.matchMedia("(max-width: 900px)").addEventListener("change", () => {
    setSidebarCollapsed($(".app-shell").classList.contains("sidebar-collapsed"));
  });
}

function initializeDialogs() {
  const backdrops = $$(".modal-backdrop");
  let returnFocus = null;
  let activeBackdrop = null;
  backdrops.forEach((backdrop) => {
    const dialog = backdrop.querySelector(".modal");
    const heading = dialog.querySelector("h2");
    if (!heading.id) heading.id = `${backdrop.id}-title`;
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", heading.id);
    backdrop.querySelector(".modal-close")?.setAttribute("aria-label", "关闭弹窗");
  });
  document.addEventListener("focusin", (event) => {
    if (!event.target.closest(".modal-backdrop")) returnFocus = event.target;
  });
  const update = () => {
    const previous = activeBackdrop;
    activeBackdrop = backdrops.find((backdrop) => !backdrop.hidden) || null;
    document.body.classList.toggle("modal-open", Boolean(activeBackdrop));
    if (previous && !activeBackdrop && returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    if (activeBackdrop && !activeBackdrop.contains(document.activeElement)) {
      activeBackdrop.querySelector(".modal-close")?.focus({ preventScroll: true });
    }
  };
  const observer = new MutationObserver(update);
  backdrops.forEach((backdrop) => observer.observe(backdrop, { attributes: true, attributeFilter: ["hidden"] }));
  document.addEventListener("keydown", (event) => {
    if (!activeBackdrop) return;
    if (event.key === "Escape") {
      event.preventDefault();
      activeBackdrop.querySelector(".modal-close")?.click();
    } else if (event.key === "Tab") {
      const focusable = [...activeBackdrop.querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]')]
        .filter((element) => !element.disabled && element.getClientRects().length);
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  update();
}

function renderUnreadCount(count) {
  $("#unread-count").textContent = count;
  const badge = $("#mobile-unread-badge");
  badge.hidden = !count;
  badge.textContent = count > 99 ? "99+" : String(count);
}

async function api(path, options = {}) {
  if (typeof window !== "undefined" && window.CyberHost?.request) return window.CyberHost.request(path, options);
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
  return ["xiaohongshu", "douyin"].includes(item.platform) && item.content_type === "image_note";
}

function noteSummaryText(value) {
  // Keep source markers in the stored summary; the reading view shows clean prose.
  return normalizeDisplaySummary(value).replace(/\[(?:图片\s*\d+|正文)\]/g, "").trim();
}

function aiFailureDiagnosis(error) {
  const raw = String(error || "");
  const message = raw.toLowerCase();
  if (!raw) return null;
  let title, reason, action;
  if (/insufficient_quota|insufficient_balance|creditserror|insufficient credit|credits exhausted|余额不足|额度不足|http\s*402/.test(message)) {
    title = "AI 服务额度不足";
    reason = "服务商返回余额或额度不足，当前请求无法完成。";
    action = "到服务商后台检查余额、套餐和密钥额度；补充额度，或启用另一套有可用额度的配置，再测试连接。";
  } else if (/model_not_found|model (?:is )?unavailable|model.*(?:does not exist|not found)|模型不可用|模型不存在/.test(message)) {
    title = "所选 AI 模型不可用";
    reason = "接口拒绝了所选模型。可能是模型 ID 不匹配、该接口不提供此模型，或账号没有访问权限；仅凭此错误无法确定具体原因，也不能判断视频是否已读取成功。";
    action = "编辑对应配置 → 读取可用模型 → 在下拉列表中重新选择文字模型和视觉模型 → 测试连接 → 保存。模型出现在列表中仍需测试实际调用权限；列表没有目标模型时，请向服务商核对支持范围。";
  } else if (/http\s*401|invalid_api_key|authentication_error|api key.*(?:无效|失效)|尚未配置.*(?:密钥|api key)/.test(message)) {
    title = "AI 密钥未配置或验证失败";
    reason = "接口未接受当前 API Key，可能是密钥缺失、失效，或与接口地址不对应。";
    action = "编辑对应配置，核对接口 URL 和服务商签发的 API Key，填写正确密钥 → 测试连接 → 保存。";
  } else if (/http\s*403/.test(message)) {
    title = "AI 接口拒绝访问";
    reason = "服务商拒绝了请求，可能涉及模型权限、账号限制或接口访问策略。";
    action = "核对对应配置的接口 URL、密钥和模型权限，并到服务商后台查看限制；测试连接通过后保存。";
  } else if (/http\s*429|rate.limit|请求过于频繁/.test(message)) {
    title = "AI 请求触发频率限制";
    reason = "服务商限制了当前请求频率或并发量。";
    action = "稍后重试；若持续发生，请降低分析并发或向服务商核对限速，也可启用另一套配置并测试连接。";
  } else if (/http\s*408|timeout|timed out|连接ai接口失败|读取模型连接失败/.test(message)) {
    title = "AI 接口连接失败或超时";
    reason = "本次请求未能正常连接接口，或未在规定时间内收到结果。";
    action = "检查网络、代理和接口 URL 后重试；若持续失败，在对应配置中测试连接，并核对服务商状态。";
  } else if (/http\s*5\d\d/.test(message)) {
    title = "AI 服务暂时异常";
    reason = "接口或上游服务返回服务器错误。";
    action = "稍后重试并查看服务商状态；持续失败时，可启用另一套配置并测试连接。";
  } else if (/ai\s*(?:api|接口)|读取模型失败|模型列表接口/.test(message)) {
    title = "AI 接口请求未通过";
    reason = "接口未接受请求或返回格式不符合要求，需结合技术详情核对配置。";
    action = "编辑对应配置，核对接口 URL、协议和所选模型的输入能力 → 读取可用模型 → 测试连接 → 保存。普通 HTTP 400 也可能是请求参数不兼容。";
  } else return null;
  const context = raw.match(/请求模型：(\"(?:[^\"\\]|\\.)*\")；输入：(文字|画面)；/);
  let request = "这条历史记录未标明实际失败模型，请分别测试文字模型与视觉模型。";
  if (context) {
    try { request = `本次失败请求：${context[2]}输入，模型 ${JSON.parse(context[1])}。`; }
    catch (_error) { /* Older or incomplete diagnostics may not contain valid JSON. */ }
  }
  return {title, reason, action, request, raw};
}

function aiItemErrors(item) {
  return [...new Set([
    item.last_attempt_status === "error" ? item.last_attempt_error : "",
    item.ai_status === "error" ? item.ai_error : "",
    item.visual_error,
    ...(item.ai_analysis_warnings || []),
  ].filter(Boolean))].filter(error => aiFailureDiagnosis(error));
}

const pendingReanalyses = new Set();

function reanalysisBusy(item) {
  return pendingReanalyses.has(updateKey(item)) || ["pending", "processing"].includes(item.last_attempt_status) || ["pending", "processing"].includes(item.ai_status);
}

function hasBriefingFailure(item) {
  return item.ai_status === "error" || item.last_attempt_status === "error" || aiItemErrors(item).length > 0;
}

function reanalysisButton(item, label = "重新分析") {
  if (!item.article_id || !["bilibili", "xiaohongshu", "douyin", "zhihu"].includes(item.platform)) return "";
  const busy = reanalysisBusy(item);
  const waiting = item.last_attempt_status === "pending" || item.ai_status === "pending";
  const mode = isImageNote(item) ? item.requested_note_reading_mode || item.ai_note_reading_mode : "";
  return `<button type="button" class="text-button reanalyze-button" data-platform="${escapeHtml(item.platform)}" data-article-id="${escapeHtml(item.article_id)}" ${["quick", "full"].includes(mode) ? `data-reading-mode="${mode}"` : ""} ${busy ? "disabled" : ""}>${busy ? waiting ? "等待分析…" : "正在分析…" : escapeHtml(label)}</button>`;
}

function bindReanalysisActions(container) {
  container.querySelectorAll?.(".reanalyze-button").forEach(button => button.addEventListener("click", reanalyzeVideo));
}

function aiFailurePanel(item) {
  const errors = aiItemErrors(item);
  if (!errors.length) return "";
  const diagnoses = errors.map(aiFailureDiagnosis);
  const guidance = diagnoses.filter((entry, index) => diagnoses.findIndex(other => other.title === entry.title && other.request === entry.request) === index);
  return `<aside class="ai-failure" aria-label="AI 分析问题与处理方法">
    ${item.ai_summary ? '<p>本次分析未完全成功，已保留现有简报。</p>' : ""}
    ${guidance.map(entry => `<strong>${escapeHtml(entry.title)}</strong><p>${escapeHtml(entry.reason)}</p><p>${escapeHtml(entry.request)}</p><p>处理方法：设置 → AI 接入 → ${escapeHtml(entry.action)}</p>`).join("")}
    <p>历史请求可能使用之前启用的配置，请核对后编辑；如果要换配置，先启用目标项。修复后返回这条内容重新分析。</p>
    <div class="ai-retry-actions">${reanalysisButton(item)}<button type="button" class="text-button ai-fix-config">修改 AI 配置</button></div>
    <details><summary>查看技术详情</summary><pre>${escapeHtml(errors.join("\n\n"))}</pre></details>
  </aside>`;
}

async function openAiFailureSettings() {
  if (state.currentView === "settings" && state.settingsPage === "ai-profile") return;
  setView("settings");
  showSettingsPage("ai");
  try {
    const data = await api("/api/settings");
    if (state.currentView !== "settings" || state.settingsPage !== "ai") return;
    aiProviderDefaults = data.ai_provider || {};
    applyAiProfiles(data.ai_providers || {profiles: [], active_profile_id: null});
  } catch (error) { showToast(`配置读取失败：${error.message}`, true); }
}

function bindAiFailureActions(container) {
  container.querySelectorAll?.(".ai-fix-config").forEach(button => button.addEventListener("click", openAiFailureSettings));
}

function summaryText(item) {
  if (item.ai_status === "cancelled") return "分析已取消。需要时可重新分析。";
  if (!item.ai_summary && item.ai_status === "error" && aiFailureDiagnosis(item.ai_error || item.last_attempt_error)) return "简报暂未生成，请按下方提示处理后重新分析。";
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

function updateSummaryToggle(summaryElement) {
  const card = summaryElement.closest(".update-card");
  const button = card?.querySelector(".summary-toggle");
  if (button && summaryElement.clientHeight) {
    button.hidden = !card.classList.contains("expanded") && summaryElement.scrollHeight <= summaryElement.clientHeight + 1;
  }
}

const summaryResizeObserver = typeof ResizeObserver !== "undefined"
  ? new ResizeObserver(entries => entries.forEach(entry => updateSummaryToggle(entry.target)))
  : null;

function updateCard(item) {
  const summary = summaryText(item);
  const sourceLabel = item.is_baseline ? `${item.source} · 初始展示` : item.source;
  let analysisLabel = "";
  if (item.ai_summary && item.ai_analysis_mode === "visual") analysisLabel = `视觉+文字 · ${item.ai_frame_count || 0}帧`;
  else if (item.ai_summary && item.visual_error) analysisLabel = "纯文字回退";
  else if (item.ai_summary) analysisLabel = "纯文字";
  if (item.ai_segment_count) analysisLabel = `${item.ai_input_mode === "video" ? "采样视频" : "分段画面"} · ${item.ai_segment_count}段 · ${item.ai_frame_count || 0}帧`;
  if (item.ai_completeness === "partial") analysisLabel += " · 分析不完整";
  if (["xiaohongshu", "douyin"].includes(item.platform) && item.ai_summary) analysisLabel = item.ai_completeness === "partial" ? "摘要范围有限" : "摘要已生成";
  const diagnosedErrors = aiItemErrors(item);
  const analysisNote = [...new Set([item.fetch_warning, item.visual_error, ...(["xiaohongshu", "douyin"].includes(item.platform) ? [] : item.ai_analysis_warnings || []), item.last_attempt_status === "error" ? item.last_attempt_error : ""].filter(note => note && !diagnosedErrors.includes(note) && note !== item.ai_error))].join("；");
  const analysisBadge = analysisLabel ? `<span class="analysis-badge ${item.ai_analysis_mode === "visual" ? "visual" : ""}" title="${escapeHtml(analysisNote || analysisLabel)}">${escapeHtml(analysisLabel)}</span>` : "";
  const analysisWarning = analysisNote ? `<div class="setting-help">${escapeHtml(analysisNote)}</div>` : "";
  const reanalyzeButton = diagnosedErrors.length ? "" : hasBriefingFailure(item) || reanalysisBusy(item)
    ? reanalysisButton(item) : item.platform === "bilibili" && item.link ? reanalysisButton(item, "重新视觉分析") : "";
  const summaryToggle = `<button class="text-button summary-toggle" hidden>展开简报</button>`;
  const originalLabel = item.platform === "douyin" ? "打开抖音原作品 ↗" : item.platform === "xiaohongshu" ? "打开原笔记 ↗" : item.platform === "zhihu" ? "打开原内容 ↗" : "打开原视频 ↗";
  const originalUrl = safeExternalUrl(item.link);
  const originalLink = originalUrl ? `<a class="original-link" href="${escapeHtml(originalUrl)}" target="_blank" rel="noopener noreferrer">${originalLabel}</a>` : "";
  const evidenceDetails = item.platform === "bilibili" && item.ai_segment_count ? `<details class="video-evidence" data-platform="${escapeHtml(item.platform)}" data-article-id="${escapeHtml(item.article_id)}"><summary>查看分段分析依据与资料出处</summary><div class="video-evidence-body setting-help"></div></details>` : "";
  const contentBadge = item.content_type_label ? `<span class="analysis-badge">${escapeHtml(item.content_type_label)}</span>` : "";
  return `<article class="update-card ${item.read ? "" : "unread"}" data-update-key="${escapeHtml(updateKey(item))}">
    <div class="update-top"><span class="update-source">${escapeHtml(sourceLabel)}</span><span class="unread-badge">未读</span></div>
    <h3 class="update-title"><button class="update-title-button briefing-link" type="button">${escapeHtml(item.title)}</button></h3>
    <div class="update-meta">发布时间：${escapeHtml(item.published || "未知")} · 检测于：${escapeHtml(formatTime(item.detected_at))}</div>
    <div class="update-summary">${escapeHtml(summary)}</div>
    ${analysisWarning}
    ${aiFailurePanel(item)}
    ${evidenceDetails}
    <div class="analysis-footer"><div class="analysis-context">${contentBadge}${analysisBadge}${originalLink}</div><div class="analysis-actions">${summaryToggle}${reanalyzeButton}</div></div>
  </article>`;
}

function renderUpdates(container, items, emptyText) {
  container.querySelectorAll(".update-summary").forEach(element => summaryResizeObserver?.unobserve(element));
  container.innerHTML = items.length ? items.map(updateCard).join("") : `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
  bindAiFailureActions(container);
  container.querySelectorAll(".update-summary").forEach(element => {
    updateSummaryToggle(element);
    summaryResizeObserver?.observe(element);
  });
  bindReanalysisActions(container);
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
  const key = updateKey({platform: button.dataset.platform, article_id: button.dataset.articleId});
  if (button.disabled || pendingReanalyses.has(key)) return;
  pendingReanalyses.add(key);
  const origin = {view: state.currentView, platform: state.sourcePlatform, uid: state.sourceUid, item: state.briefingItem};
  const matching = [...new Set([button, ...$$(".reanalyze-button").filter(other => other.dataset.platform === button.dataset.platform && other.dataset.articleId === button.dataset.articleId)])];
  const labels = matching.map(other => other.textContent);
  matching.forEach(other => { other.disabled = true; other.textContent = "正在加入队列…"; });
  let submitted = false;
  try {
    const data = await api(`/api/videos/${encodeURIComponent(button.dataset.platform)}/${encodeURIComponent(button.dataset.articleId)}/reanalyze`, { method: "POST", ...(button.dataset.readingMode ? {body: JSON.stringify({reading_mode: button.dataset.readingMode})} : {}) });
    submitted = true;
    matching.forEach(other => { other.textContent = "等待分析…"; });
    showToast(data.message);
    // Queue state is now persisted; fresh views can render its real status.
    pendingReanalyses.delete(key);
    if (state.currentView !== origin.view) return;
    if (origin.view === "briefing" && state.briefingItem === origin.item) await refreshNoteBriefing();
    else if (origin.view === "source-detail" && state.sourcePlatform === origin.platform && state.sourceUid === origin.uid) await loadSourceDetail(origin.platform, origin.uid);
    else if (origin.view === "dashboard") await loadDashboard();
  } catch (error) {
    showToast(submitted ? `已提交分析，状态刷新失败：${error.message}。请刷新页面查看。` : `重新分析未提交：${error.message}`, true);
    if (!submitted) matching.forEach((other, index) => { other.disabled = false; other.textContent = labels[index]; });
  } finally {
    pendingReanalyses.delete(key);
  }
}

function noteAnalysisButtons(item) {
  if (!item.link) return "";
  const busy = reanalysisBusy(item);
  if (["xiaohongshu", "douyin"].includes(item.platform) && item.content_type === "video") return `<button type="button" class="text-button reanalyze-button" data-platform="${escapeHtml(item.platform)}" data-article-id="${escapeHtml(item.article_id)}" ${busy ? "disabled" : ""}>${busy ? "正在分析视频…" : "重新分析视频"}</button>`;
  if (busy) return `<button class="text-button" type="button" disabled>正在分析图文…</button>`;
  return ["quick", "full"].map(mode => `<button type="button" class="text-button reanalyze-button" data-platform="${escapeHtml(item.platform)}" data-article-id="${escapeHtml(item.article_id)}" data-reading-mode="${mode}" ${busy ? "disabled" : ""}>${busy ? "正在分析…" : mode === "full" ? "完整分析图文" : "重新分析图文（快速）"}</button>`).join("");
}

async function refreshNoteBriefing() {
  const item = state.briefingItem;
  if (!item || state.currentView !== "briefing") return;
  const data = await api(`/api/history/${encodeURIComponent(item.platform)}/${encodeURIComponent(item.article_id)}`);
  if (state.currentView !== "briefing" || state.briefingItem !== item) return;
  const updated = data.item;
  if (updated) { state.briefingItem = updated; renderBriefing(updated); }
  if (updated && reanalysisBusy(updated)) {
    setTimeout(() => {
      if (state.currentView === "briefing" && state.briefingItem === updated) refreshNoteBriefing().catch(error => showToast(error.message, true));
    }, 5000);
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
  renderUnreadCount(result.unread_count);
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
    + (item.last_attempt_status === "error" && item.ai_summary && !aiFailureDiagnosis(item.last_attempt_error) ? `\n\n最近一次重新分析失败，当前展示上次简报：${item.last_attempt_error || "请重试"}` : "");
  const failure = $("#briefing-ai-failure");
  failure.innerHTML = aiFailurePanel(item);
  failure.hidden = !failure.innerHTML;
  bindAiFailureActions(failure);
  bindReanalysisActions(failure);
  const retry = $("#briefing-retry-actions");
  retry.innerHTML = !failure.innerHTML && (hasBriefingFailure(item) || reanalysisBusy(item)) ? reanalysisButton(item) : "";
  retry.hidden = !retry.innerHTML;
  bindReanalysisActions(retry);
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
  state.briefingScroll = window.scrollY;
  state.briefingBack = state.currentView === "source-detail" ? "source-detail" : "dashboard";
  state.briefingItem = item;
  for (const selector of ["#briefing-note-original", "#briefing-note-evidence-details", "#briefing-note-options"]) $(selector).open = false;
  renderBriefing(item);
  setView("briefing");
  if (reanalysisBusy(item)) setTimeout(() => refreshNoteBriefing().catch(error => showToast(error.message, true)), 5000);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function backFromBriefing() {
  if (state.briefingBack === "source-detail" && state.sourcePlatform && state.sourceUid) {
    const contentType = state.sourceContentType;
    if (!await loadSourceDetail(state.sourcePlatform, state.sourceUid)) return;
    if (contentType) showSourceContentType(contentType);
    window.scrollTo({ top: state.briefingScroll || 0, behavior: "instant" });
    return;
  }
  setView("dashboard");
  await loadDashboard();
  window.scrollTo({ top: state.briefingScroll || 0, behavior: "instant" });
}

function platformLabel(platform) {
  return ({zhihu: "知乎", bilibili: "哔哩哔哩", xiaohongshu: "小红书", douyin: "抖音"})[platform] || platform;
}

function platformIcon(platform, className) {
  const icons = {bilibili: "bilibili", zhihu: "zhihu", douyin: "douyin", xiaohongshu: "xiaohongshu"};
  const icon = icons[platform];
  if (!icon) return "";
  return `<img class="platform-icon ${escapeHtml(className)}" src="/static/icons/${icon}.jpg" alt="${escapeHtml(platformLabel(platform))}" decoding="async">`;
}

function renderPlatforms(container, sources) {
  const platforms = [
    { key: "bilibili", label: "哔哩哔哩", caption: "视频与AI简报" },
    { key: "zhihu", label: "知乎", caption: "回答、文章、想法与视频" },
    { key: "douyin", label: "抖音", caption: "视频、图文与AI简报" },
    { key: "xiaohongshu", label: "小红书", caption: "视频、图文与AI简报" },
  ];
  container.innerHTML = platforms.map((platform) => {
    const matched = sources.filter((source) => source.platform === platform.key);
    const unread = matched.reduce((sum, source) => sum + Number(source.unread_count || 0), 0);
    return `<button class="platform-card ${platform.key}" type="button" data-platform="${platform.key}">
      ${platformIcon(platform.key, "platform-mark")}
      <span class="platform-card-main"><strong>${platform.label}</strong><span>${platform.caption}</span></span>
      <span class="platform-card-stats"><b>${matched.length}</b> 位博主${unread ? `<em>${unread} 未读</em>` : ""}</span>
    </button>`;
  }).join("");
  container.querySelectorAll(".platform-card").forEach((card) => card.addEventListener("click", () => showPlatformSources(card.dataset.platform)));
}

function sourceAvatarContents(source) {
  const url = window.CyberHost?.showRemoteAvatars === false ? "" : safeExternalUrl(source.avatar);
  const initial = [...String(source.name || "博主").trim()][0] || "博";
  return `<span class="source-avatar-placeholder">${escapeHtml(initial)}</span>${url ? `<img class="source-avatar-image" src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : ""}`;
}

function bindSourceAvatarFallbacks(container) {
  container.querySelectorAll(".source-avatar-image").forEach(image => {
    image.addEventListener("error", () => image.remove(), { once: true });
  });
}

function renderSources(container, sources) {
  container.innerHTML = sources.length ? sources.map((source) => `<article class="source-card" data-platform="${escapeHtml(source.platform)}" data-uid="${escapeHtml(source.uid)}">
    <div class="source-card-top"><span class="source-avatar" aria-hidden="true">${sourceAvatarContents(source)}</span><h3 class="source-name">${escapeHtml(source.name)}</h3></div>
    ${source.following === false ? '<p class="source-following-status">不再关注 · 点击查看历史简报</p>' : ["xiaohongshu", "douyin"].includes(source.platform) ? `<div class="setting-help">${escapeHtml(collectionStatusText(source.collection_status))}</div>` : ""}
    <div class="source-stats"><div><strong>${source.new_video_count || 0}</strong>已收录内容</div><div><strong class="source-unread-count">${source.unread_count || 0}</strong>未读</div></div>
  </article>`).join("") : `<div class="empty-state">还没有关注对象，点击右上角添加第一位博主。</div>`;
  $$(".source-card").forEach((card) => card.addEventListener("click", () => loadSourceDetail(card.dataset.platform, card.dataset.uid)));
  bindSourceAvatarFallbacks(container);
}

const sourceAvatarRequests = new Map();

async function loadSourceAvatars(sources) {
  if (window.CyberHost?.showRemoteAvatars === false) return;
  const pending = sources.filter(source => !safeExternalUrl(source.avatar));
  async function worker() {
    while (pending.length) {
      const source = pending.shift();
      if (state.sourcesStage !== "sources" || state.selectedPlatform !== source.platform) return;
      const key = JSON.stringify([source.platform, String(source.uid)]);
      const cached = sourceAvatarRequests.get(key);
      if (!cached || Date.now() - cached.requestedAt >= 600000) {
        sourceAvatarRequests.set(key, {
          requestedAt: Date.now(),
          promise: api(`/api/sources/${encodeURIComponent(source.platform)}/${encodeURIComponent(source.uid)}/avatar`, { method: "POST" }).catch(() => ({ avatar: "" })),
        });
      }
      const data = await sourceAvatarRequests.get(key).promise;
      const avatar = safeExternalUrl(data.avatar);
      const current = state.sources.find(item => item.platform === source.platform && String(item.uid) === String(source.uid));
      if (!avatar || !current) continue;
      current.avatar = avatar;
      if (state.currentView !== "sources" || state.sourcesStage !== "sources" || state.selectedPlatform !== source.platform) continue;
      $$(".source-card").forEach(card => {
        if (card.dataset.platform === source.platform && String(card.dataset.uid) === String(source.uid)) {
          const slot = card.querySelector(".source-avatar");
          if (slot) slot.innerHTML = sourceAvatarContents(current);
          bindSourceAvatarFallbacks(card);
        }
      });
    }
  }
  await Promise.all([worker(), worker()]);
}

function showPlatformSources(platform) {
  invalidateSourceRequests();
  state.sourcesStage = "sources";
  state.selectedPlatform = platform;
  const sources = state.sources.filter((source) => source.platform === platform);
  $("#sources-heading").textContent = platformLabel(platform);
  $("#sources-description").textContent = ["douyin", "xiaohongshu"].includes(platform) ? "选择博主，查看视频与图文的文字简报。" : platform === "zhihu" ? "选择博主后，再按回答、文章、想法等内容类型查看。" : "选择博主，查看已收录视频和AI简报。";
  $("#back-to-platforms").hidden = false;
  $("#source-list").className = "source-grid";
  renderSources($("#source-list"), sources);
  loadSourceAvatars(sources);
}

function showPlatformPicker() {
  invalidateSourceRequests();
  state.sourcesStage = "platforms";
  state.selectedPlatform = null;
  $("#sources-heading").textContent = "选择平台";
  $("#sources-description").textContent = "进入平台后选择关注的博主。";
  $("#back-to-platforms").hidden = true;
  $("#source-list").className = "platform-grid";
  renderPlatforms($("#source-list"), state.sources);
}

function showSettingsPage(page = null, focus = true) {
  const panels = $$('[data-settings-panel]');
  const selected = panels.find(panel => panel.dataset.settingsPanel === page);
  if (page && !selected) return;
  const previous = state.settingsPage;
  if (previous === "ai-profile" && page !== "ai-profile") leaveAiProfileEditor();
  state.settingsPage = page;
  $("#settings-index").hidden = Boolean(page);
  $("#settings-detail").hidden = !page;
  panels.forEach(panel => { panel.hidden = panel !== selected; });
  $("#back-to-settings").textContent = page === "ai-profile" ? "← AI 接入" : "← 全部设置";
  const heading = selected?.querySelector("h3");
  $("#view-title").textContent = heading?.textContent || "设置";
  if (!focus) return;
  window.scrollTo({ top: 0, behavior: "instant" });
  if (heading) {
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
  } else if (previous) {
    $$('[data-settings-page]').find(button => button.dataset.settingsPage === previous)?.focus({ preventScroll: true });
  }
}

function invalidateSourceRequests() {
  state.sourceRequestId += 1;
  state.sourceRequestTarget = null;
  if (state.sourceTimer) clearTimeout(state.sourceTimer);
  state.sourceTimer = null;
}

function setView(view) {
  if (state.currentView !== view && window.matchMedia("(max-width: 620px)").matches) {
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  if (view !== "dashboard" && state.dashboardTimer) {
    clearTimeout(state.dashboardTimer);
    state.dashboardTimer = null;
  }
  if (view !== "source-detail") invalidateSourceRequests();
  if (view !== "settings" && state.settingsPage === "ai-profile") showSettingsPage("ai", false);
  state.currentView = view;
  if (view !== "video-lab") window.VideoLab?.leave();
  $$(".view").forEach((element) => element.classList.toggle("active", element.id === `view-${view}`));
  const navView = view === "source-detail" ? "sources" : view === "briefing" ? state.briefingBack : view;
  $$(".nav-item").forEach((element) => {
    const active = element.dataset.view === (navView === "source-detail" ? "sources" : navView);
    element.classList.toggle("active", active);
    if (active) element.setAttribute("aria-current", "page");
    else element.removeAttribute("aria-current");
  });
  const titles = { dashboard: "最新消息", sources: "关注列表", accounts: "账号管理", settings: "设置", "source-detail": "博主详情", briefing: "简报详情", "video-lab": "视频实验" };
  $("#view-title").textContent = titles[view] || "赛博情报局";
  if (view === "settings") showSettingsPage(null, false);
  if (view === "video-lab") window.VideoLab?.load().catch(error => showToast(error.message, true));
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

function mergeContent(existing, incoming, newestFirst = false) {
  const fresh = new Map(incoming.map(item => [updateKey(item), item]));
  const ordered = newestFirst ? [...incoming, ...existing] : [...existing, ...incoming];
  const merged = new Map();
  for (const item of ordered) {
    const key = updateKey(item);
    if (!merged.has(key)) merged.set(key, fresh.get(key) || item);
  }
  return [...merged.values()];
}


async function loadDashboard() {
  const requestId = ++state.dashboardRequestId;
  const previous = state.dashboard;
  try {
    const limit = Math.max(100, previous?.updates?.length || 0);
    const data = await api(limit > 100 ? `/api/dashboard?limit=${limit}` : "/api/dashboard");
    if (requestId !== state.dashboardRequestId) return;
    const loaded = state.dashboard || previous;
    const sameScope = loaded && (data.unread_count > 0 && loaded.unread_count > 0 || !data.unread_count && !loaded.unread_count && data.updates[0]?.batch_id === loaded.updates?.[0]?.batch_id);
    if (sameScope) data.updates = mergeContent(loaded.updates || [], data.updates, true);
    state.dashboard = data;
    $("#dashboard-load-more").hidden = !data.has_more;
    $("#dashboard-load-more").disabled = false;
    renderUnreadCount(data.unread_count);
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
    const stillProcessing = data.updates.some(reanalysisBusy);
    if (state.dashboardTimer) clearTimeout(state.dashboardTimer);
    state.dashboardTimer = stillProcessing ? setTimeout(() => {
      if (state.currentView === "dashboard" && state.dashboardRequestId === requestId) loadDashboard();
    }, 5000) : null;
  } catch (error) { showToast(error.message, true); }
}

async function loadSources() {
  const requestId = state.sourceRequestId;
  try {
    const data = await api("/api/sources");
    state.sources = data.sources || [];
    if (state.currentView === "sources" && state.sourceRequestId === requestId) showPlatformPicker();
  }
  catch (error) { showToast(error.message, true); }
}

async function loadAccounts() {
  try {
    const data = await api("/api/accounts");
    const accounts = data.accounts || [];
    $("#account-list").innerHTML = accounts.map((account) => {
      const isZhihu = account.platform === "zhihu";
      const isXhs = account.platform === "xiaohongshu", isDouyin = account.platform === "douyin";
      let identity = account.status === "logged_in"
        ? (account.account_name || (isDouyin ? "抖音登录状态" : isXhs ? "小红书登录状态" : isZhihu ? "知乎登录状态" : "B站账号"))
        : (["expired", "credential_error"].includes(account.status)
          ? "登录凭证不可用，点击重新登录"
          : (account.status === "verification_required" ? "请打开官方窗口完成验证，再保存登录状态" : account.status === "unknown" ? "状态检查失败，仍可重新登录" : (isDouyin ? "点击打开抖音官方登录窗口" : isXhs ? "点击打开小红书登录窗口" : isZhihu ? "点击打开知乎登录窗口" : "点击扫码登录")));
      if (window.CyberHost?.canLogin === false && account.status !== "logged_in") identity = "请在电脑端完成登录";
      const avatarUrl = window.CyberHost?.showRemoteAvatars === false ? "" : safeExternalUrl(account.avatar);
      const image = avatarUrl
        ? `<img class="account-avatar" src="${escapeHtml(avatarUrl)}" alt="账号头像">`
        : platformIcon(account.platform, "account-logo");
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

function allowHostLogin() {
  if (window.CyberHost?.canLogin !== false) return true;
  showToast("请在电脑端“账号管理”完成登录，再回到手机刷新。");
  return false;
}

function handleHostBack() {
  const modal = $$(".modal-backdrop").find(node => !node.hidden);
  if (modal) { modal.querySelector(".modal-close")?.click(); return true; }
  if (state.currentView === "briefing") { backFromBriefing(); return true; }
  if (state.currentView === "source-detail") { $("#back-to-sources").click(); return true; }
  if (state.currentView === "sources" && state.sourcesStage === "sources") { showPlatformPicker(); return true; }
  if (state.currentView === "settings" && state.settingsPage) { showSettingsPage(); return true; }
  if (state.currentView !== "dashboard") { setView("dashboard"); loadDashboard(); return true; }
  if (state.unreadPanelOpen) { toggleUnreadPanel(false); return true; }
  return false;
}

function initializeHost() {
  window.CyberApp = Object.freeze({back: handleHostBack});
  const host = window.CyberHost;
  if (!host) return;
  if (host.openExternal) document.addEventListener("click", event => {
    const link = event.target.closest("a[href]");
    if (link && /^https?:/.test(link.href)) { event.preventDefault(); host.openExternal(link.href); }
  });
  if (host.kind !== "android") return;
  document.body.classList.add("android-companion");
  const banner = document.createElement("div");
  banner.className = "android-connection";
  banner.setAttribute("role", "status");
  banner.innerHTML = '<span>正在连接电脑后台…</span><button type="button">连接设置</button>';
  banner.querySelector("button").onclick = host.changeConnection;
  $(".topbar").after(banner);
  host.subscribeConnection((ok, message) => {
    banner.classList.toggle("offline", !ok);
    banner.querySelector("span").textContent = message;
  });
  const entry = document.createElement("button");
  entry.type = "button"; entry.className = "settings-entry";
  entry.innerHTML = '<span class="settings-entry-icon" aria-hidden="true">⌁</span><span><strong>电脑连接</strong><small></small></span><span class="settings-entry-arrow" aria-hidden="true">›</span>';
  entry.querySelector("small").textContent = host.connectionInfo();
  entry.onclick = host.changeConnection;
  $(".settings-index-grid").prepend(entry);
  $("#accounts-description").textContent = "查看电脑上的平台登录状态。登录或重新登录请在 Windows 端完成。";
  $("#account-storage-description").textContent = "平台凭证和 AI Key 加密保存在电脑上。手机只保存连接电脑的配对信息，可在“设置 → 电脑连接”中删除。";
}


function hasDesktopBridge() {
  return window.CyberHost?.canLogin !== false && Boolean(window.pywebview?.api);
}

async function startZhihuLogin(event) {
  if (!allowHostLogin()) return;
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
  if (!allowHostLogin()) return;
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
  if (!allowHostLogin()) return;
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
  const focusTarget = window.matchMedia("(max-width: 620px)").matches ? "#source-modal-title" : "#source-uid";
  $(focusTarget).focus({ preventScroll: true });
}

function stopQrPolling() {
  if (state.qrTimer) clearInterval(state.qrTimer);
  state.qrTimer = null;
  state.qrKey = null;
}

async function startQrLogin() {
  if (!allowHostLogin()) return;
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

async function loadSourceDetail(platform, uid, options = {}) {
  const previous = state.sourcePlatform === platform && state.sourceUid === uid ? state.sourceDetailData : null;
  invalidateSourceRequests();
  const requestId = state.sourceRequestId;
  state.sourceRequestTarget = { platform, uid };
  try {
    const base = `/api/sources/${encodeURIComponent(platform)}/${encodeURIComponent(uid)}`;
    const limit = Math.max(200, previous?.videos?.length || 0);
    let data = await api(limit > 200 ? `${base}?limit=${limit}` : base);
    if (state.sourceRequestId !== requestId) return false;
    if (data.needs_preparation && data.source.following !== false && options.prepare !== false) {
      data = await api(`${base}/prepare`, {method: "POST"});
      if (state.sourceRequestId !== requestId) return false;
    }
    if (previous) data.videos = mergeContent(previous.videos, data.videos, true);
    const previousContentType = state.sourcePlatform === platform && state.sourceUid === uid ? state.sourceContentType : null;
    // Commit the displayed identity and its data together, only for the latest request.
    Object.assign(state, { sourcePlatform: platform, sourceUid: uid, sourceName: data.source.name, sourceDetailData: data });
    renderSourceCollection(data, platform, previousContentType);
    if (data.warning) showToast(data.warning, true);
    setView("source-detail");
    const stillProcessing = data.videos.some(video => (data.source.following !== false && reanalysisBusy(video)) || ["pending", "processing"].includes(video.last_attempt_status));
    if (state.sourceTimer) clearTimeout(state.sourceTimer);
    state.sourceTimer = stillProcessing ? setTimeout(() => {
      if (state.currentView === "source-detail" && state.sourceRequestId === requestId) loadSourceDetail(platform, uid, {prepare: false});
    }, 5000) : null;
    return true;
  } catch (error) {
    if (state.sourceRequestId === requestId) {
      state.sourceRequestTarget = state.sourcePlatform ? { platform: state.sourcePlatform, uid: state.sourceUid } : null;
      $("#source-load-more").disabled = false;
      showToast(error.message, true);
    }
    return false;
  }
}

function renderSourceCollection(data, platform, previousContentType) {
  $("#detail-source-name").textContent = data.source.name;
  const following = data.source.following !== false;
  $("#source-following-status").hidden = following;
  $("#toggle-source-following").textContent = following ? "不再关注" : "重新关注";
  $("#toggle-source-following").disabled = false;
  $("#detail-source-meta").textContent = `${platformLabel(data.source.platform)} · ${data.source.unread_count || 0} 条未读 · 添加时间：${formatTime(data.source.added_at)} · ${platform === "douyin" ? "首次展示最近3条作品，后续检查公开主页新内容；读取范围有限时会提示" : platform === "xiaohongshu" ? "首次展示最近3条视频或图文，之后增量补抓，每次最多读取20条；超过范围会提示" : "首次展示3条，后续按分页补抓；范围未确认时会提示"}${["xiaohongshu", "douyin"].includes(platform) ? "\n" + collectionStatusText(data.source.collection_status) : ""}`;
  if (!following) $("#detail-source-meta").textContent = `${platformLabel(data.source.platform)} · ${data.source.new_video_count ?? data.videos.length} 条历史内容 · ${data.source.unread_count || 0} 条未读`;
  if (following && data.source.scan_warning) $("#detail-source-meta").textContent += "\n读取范围：" + data.source.scan_warning;
  $("#source-load-more").hidden = !data.has_more;
  $("#source-load-more").disabled = false;
  state.sourceContentType = ["zhihu", "douyin", "xiaohongshu"].includes(platform) ? previousContentType : "video";
  const typeOrder = ["图文", "文章", "回答", "想法", "提问", "视频", "其他"];
  const grouped = Object.groupBy ? Object.groupBy(data.videos, (item) => item.content_type_label || (item.platform === "bilibili" ? "视频" : "其他")) : data.videos.reduce((acc, item) => { const key = item.content_type_label || (item.platform === "bilibili" ? "视频" : "其他"); (acc[key] ||= []).push(item); return acc; }, {});
  const detailContainer = $("#source-videos");
  const typeMenu = $("#detail-content-types");
  if (["zhihu", "douyin", "xiaohongshu"].includes(platform)) {
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
  if (!data.videos.length) detailContainer.innerHTML = `<div class="empty-state">${following ? "暂时还没有可展示的内容，之后发布的视频与图文会自动收录。" : "这位博主已不再关注，暂无保留的历史内容。重新关注后才会继续抓取。"}</div>`;
  const failedCount = data.videos.filter((video) => ["bilibili", "article", "zvideo", "video", "image_note"].includes(video.platform === "bilibili" ? "bilibili" : video.content_type) && video.ai_status === "error").length;
  $("#retry-source-summaries").hidden = failedCount === 0 || !following;
  $("#retry-source-summaries").textContent = `重试失败简报（${failedCount}）`;
}


async function loadMoreSourceContent(event) {
  const button = event.currentTarget;
  const { sourcePlatform: platform, sourceUid: uid, sourceDetailData: details, sourceRequestId: requestId } = state;
  if (!details || state.currentView !== "source-detail" || button.disabled) return;
  const isCurrent = () => state.currentView === "source-detail" && state.sourceRequestId === requestId && state.sourceDetailData === details;
  button.disabled = true;
  try {
    const data = await api(`/api/history?platform=${encodeURIComponent(platform)}&source_uid=${encodeURIComponent(uid)}&offset=${details.next_offset || 0}&limit=100`);
    if (!isCurrent()) return;
    const existing = mergeContent(details.videos, data.items);
    details.videos = existing;
    details.next_offset = data.next_offset;
    details.has_more = data.has_more;
    renderSourceCollection(details, platform, state.sourceContentType);
    button.hidden = !data.has_more;
  } catch (error) {
    if (isCurrent()) showToast(error.message, true);
  } finally {
    if (isCurrent()) button.disabled = false;
  }
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
  state.deleteSourceTarget = null;
}

function openDeleteSourceModal() {
  if (!state.sourcePlatform || !state.sourceUid) return;
  state.deleteSourceTarget = { platform: state.sourcePlatform, uid: state.sourceUid, name: state.sourceName };
  $("#delete-source-message").textContent = `确定要删除“${state.deleteSourceTarget.name || "这位博主"}”及其历史内容和简报吗？`;
  $("#delete-source-modal").hidden = false;
  $("#confirm-delete-source").focus();
}

async function confirmDeleteSource() {
  const target = state.deleteSourceTarget;
  const button = $("#confirm-delete-source");
  if (!target || button.disabled) return;
  const requestId = state.sourceRequestId;
  button.disabled = true;
  try {
    const data = await api(`/api/sources/${encodeURIComponent(target.platform)}/${encodeURIComponent(target.uid)}?purge_history=true`, { method: "DELETE" });
    if (state.deleteSourceTarget === target) closeDeleteSourceModal();
    if (
      state.currentView === "source-detail" && state.sourceRequestId === requestId
      && state.sourceRequestTarget?.platform === target.platform && state.sourceRequestTarget?.uid === target.uid
      && state.sourcePlatform === target.platform && state.sourceUid === target.uid
    ) {
      Object.assign(state, { sourcePlatform: null, sourceUid: null, sourceName: null, sourceDetailData: null, sourceContentType: null });
      setView("sources");
    }
    showToast(data.message);
    await loadSources();
    await loadDashboard();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

let sourceFollowingRequestId = 0;

async function toggleSourceFollowing() {
  const source = state.sourceDetailData?.source;
  const button = $("#toggle-source-following");
  if (!source || button.disabled) return;
  const requestId = ++sourceFollowingRequestId;
  const detailRequestId = state.sourceRequestId;
  button.disabled = true;
  try {
    const data = await api(`/api/sources/${encodeURIComponent(source.platform)}/${encodeURIComponent(source.uid)}/following`, {
      method: "PUT", body: JSON.stringify({ following: source.following === false }),
    });
    if (state.currentView === "source-detail" && state.sourceRequestId === detailRequestId
      && state.sourcePlatform === source.platform && state.sourceUid === source.uid) {
      state.sourceDetailData.source = data.source;
      renderSourceCollection(state.sourceDetailData, source.platform, state.sourceContentType);
    }
    showToast(data.message);
    await loadSources();
  } catch (error) { showToast(error.message, true); }
  finally { if (sourceFollowingRequestId === requestId) button.disabled = false; }
}

async function loadTaskStatus() {
  const labels = {queued: "等待处理", running: "正在分析", retry_wait: "等待重试", failed: "需要处理", succeeded: "已完成", cancelled: "已取消"};
  try {
    const data = await api("/api/tasks");
    $("#task-counts").innerHTML = Object.entries(labels).map(([key, label]) => `<div class="task-count ${key === "failed" && data.counts[key] > 0 ? "needs-attention" : ""}"><span>${label}</span><strong>${escapeHtml(data.counts[key] || 0)}</strong></div>`).join("");
    if (data.budget) {
      $("#task-daily-budget").value = data.budget.max_daily_attempts;
      $("#task-budget-status").textContent = `今日已开始 ${data.budget.used_today} 次分析${data.budget.max_daily_attempts ? `，上限 ${data.budget.max_daily_attempts}` : "，未设置上限"}。每次分析可能包含多次模型请求。`;
    }
    $("#task-list").innerHTML = data.jobs.length ? data.jobs.map(job => `<div class="task-row"><div><strong>${escapeHtml(job.title || job.content_id)}</strong><div class="setting-help">${escapeHtml(platformLabel(job.platform))}${job.source ? ` · ${escapeHtml(job.source)}` : ""} · ${escapeHtml(labels[job.state] || job.state)} · 尝试 ${job.attempts}/${job.max_attempts}${job.state === "retry_wait" ? ` · 下次重试 ${escapeHtml(formatTime(new Date(job.due_at * 1000).toISOString()))}` : ""}</div>${job.error ? `<div class="setting-help">${escapeHtml(job.error)}</div>` : ""}</div>${["queued", "retry_wait"].includes(job.state) ? `<button class="text-button cancel-task" data-job-id="${job.id}" type="button">取消</button>` : ""}</div>`).join("") : `<div class="empty-state">暂无后台任务。</div>`;
    $("#task-list").querySelectorAll(".cancel-task").forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      try { await api(`/api/tasks/${button.dataset.jobId}/cancel`, {method: "POST"}); await loadTaskStatus(); }
      catch (error) { showToast(error.message, true); button.disabled = false; }
    }));
  } catch (error) { $("#task-counts").textContent = `任务状态读取失败：${error.message}`; }
}

async function loadSettings() {
  try {
    const data = await api("/api/settings");
    aiProviderDefaults = data.ai_provider || {};
    applyAiProfiles(data.ai_providers || { profiles: [], active_profile_id: null });
    await loadTaskStatus();
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
  if (!mediaKnowledgeEntries.length) {
    $("#media-knowledge-list").innerHTML = `<p class="setting-help">尚未添加资料。可在下方添加自己的资料，也可直接使用视频分析。</p>`;
    return;
  }
  const modelInfo = `<details><summary>可选参考图匹配状态</summary><p class="setting-help">${status.ccip_ready ? "CCIP已安装" : "CCIP未安装"}；${status.wd_ready ? "WD辅助标签已安装" : "WD辅助标签未安装"}。已收录${mediaKnowledgeEntries.filter(e => e.reference_images?.length).length}个有图条目。这些模型主要用于插画角色匹配，不代表通用人物识别能力；资料文本的使用不依赖这些模型。</p></details>`;
  $("#media-knowledge-list").innerHTML = mediaKnowledgeEntries.map(entry =>
    `<p class="setting-help"><strong>${escapeHtml(entry.name)}</strong>${entry.work ? ` · ${escapeHtml(entry.work)}` : ""} · ${escapeHtml(entry.variant || "默认形象")} · ${entry.reference_images?.length || 0}张参考图<br>${escapeHtml(entry.text.slice(0, 140))}</p>`
  ).join("");
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
    element.textContent = "当前启用配置的密钥无法读取，请编辑这套配置并重新保存 API Key。";
  } else if (status.configured) {
    element.textContent = `当前启用：${provider.name || "原有配置"} · 文字模型 ${provider.model || "未选择"}${provider.vision_enabled ? ` · 视觉模型 ${provider.vision_model || provider.model}` : ""}。`;
  } else {
    element.textContent = "尚未配置 AI 密钥，AI 简报暂时无法生成。";
  }
}

let aiModelCatalog = { apiUrl: "", models: [] };
let aiProfiles = { profiles: [], active_profile_id: null };
let aiProviderDefaults = {};
let aiEditingProfileId = null;
let aiSourceProfileId = null;
let aiEditorVersion = 0;
let aiFormBusy = false;
let aiProfilesBusy = false;

function renderAiProfiles() {
  $("#ai-profiles-list").innerHTML = aiProfiles.profiles.length ? aiProfiles.profiles.map(profile => {
    const active = profile.id === aiProfiles.active_profile_id;
    const keyStatus = profile.api_key?.configured ? "密钥已保存" : "密钥需要重新配置";
    return `<article class="ai-profile-card${active ? " is-active" : ""}">
      <div class="ai-profile-card-heading"><div><strong>${escapeHtml(profile.name)}</strong><div class="ai-profile-provider">${escapeHtml(profile.provider_name)}</div></div>
        <label class="ai-profile-switch"><input type="checkbox" role="switch" data-ai-activate="${escapeHtml(profile.id)}" aria-label="启用 ${escapeHtml(profile.name)}" ${active ? "checked" : ""} ${active || aiProfilesBusy ? "disabled" : ""}><span>${active ? "已启用" : "启用"}</span></label></div>
      <div class="ai-profile-details"><p>${escapeHtml(profile.api_url)}</p><p>文字：${escapeHtml(profile.model)}</p><p>画面：${profile.vision_enabled ? escapeHtml(profile.vision_model || profile.model) : "未启用"} · ${keyStatus}</p></div>
      <div class="ai-profile-actions"><button type="button" class="text-button" data-ai-action="edit" data-profile-id="${escapeHtml(profile.id)}">编辑</button>
        <button type="button" class="text-button" data-ai-action="copy" data-profile-id="${escapeHtml(profile.id)}">复制</button>
        <button type="button" class="text-button ai-profile-delete" data-ai-action="delete" data-profile-id="${escapeHtml(profile.id)}" ${active || aiProfilesBusy ? "disabled" : ""} ${active ? 'title="请先启用另一套配置再删除"' : ""}>删除</button></div></article>`;
  }).join("") : '<div class="empty-state">还没有保存的配置，先添加一套 AI 接入。</div>';
}

function applyAiProfiles(catalog) {
  aiProfiles = catalog;
  renderAiProfiles();
  const active = aiProfiles.profiles.find(profile => profile.id === aiProfiles.active_profile_id);
  renderAiKeyStatus(active || {});
  const toggle = $("#ai-profile-activate");
  const editingActive = Boolean(aiEditingProfileId && aiEditingProfileId === aiProfiles.active_profile_id);
  if (editingActive || toggle.disabled) toggle.checked = editingActive;
  toggle.disabled = editingActive;
  if (aiEditingProfileId) $("#ai-profile-editor-help").textContent = editingActive
    ? "正在编辑当前启用项，保存后用于新开始的分析。"
    : "保存不会切换当前接口；也可勾选“保存后启用”。";
}

function editAiProfile(profileId = null, copy = false) {
  const profile = profileId ? aiProfiles.profiles.find(item => item.id === profileId) : aiProfiles.profiles.length ? {} : aiProviderDefaults;
  if (!profile) return;
  aiEditingProfileId = copy ? null : profileId;
  aiSourceProfileId = copy ? profileId : null;
  aiEditorVersion += 1;
  resetAiModelChoices();
  fillAiProviderForm(profile);
  $("#ai-profile-name").value = copy ? `${profile.name} 副本`.slice(0, 80) : profileId ? profile.name : "";
  $("#ai-provider-name").value = profile.provider_name || "";
  $("#api-key-input").value = "";
  $("#api-key-input").placeholder = profileId ? "留空保留已保存的 Key" : "输入这套配置的 API Key";
  const active = Boolean(!copy && profileId && profileId === aiProfiles.active_profile_id);
  $("#ai-profile-activate").checked = active || !aiProfiles.active_profile_id;
  $("#ai-profile-activate").disabled = active;
  $("#ai-profile-editor-title").textContent = copy ? "复制配置" : profileId ? `编辑：${profile.name}` : "新增配置";
  $("#ai-profile-editor-help").textContent = copy ? "已复制接口与模型，Key 留空可沿用；更换接口地址时请填写新 Key。" : active ? "正在编辑当前启用项，保存后用于新开始的分析。" : "保存不会切换当前接口；也可勾选“保存后启用”。";
  ["#ai-test-result", "#api-key-result"].forEach(selector => {
    $(selector).textContent = "";
    $(selector).classList.remove("setting-error");
  });
  renderAiProfiles();
  showSettingsPage("ai-profile");
}

function leaveAiProfileEditor() {
  // Invalidate pending tests and saves before leaving or opening another draft.
  aiEditorVersion += 1;
  aiEditingProfileId = null;
  aiSourceProfileId = null;
  $("#api-key-input").value = "";
}

function refreshAiButtons() {
  ["#ai-test-connection", "#ai-read-models"].forEach(selector => { $(selector).disabled = aiFormBusy; });
  $("#api-key-form").querySelector("button[type=submit]").disabled = aiFormBusy || aiProfilesBusy;
  renderAiProfiles();
}

async function changeAiProfile(profileId, action) {
  if (aiProfilesBusy) return;
  aiProfilesBusy = true;
  refreshAiButtons();
  const result = $("#ai-profiles-result");
  result.classList.remove("setting-error");
  result.textContent = action === "activate" ? "正在切换配置…" : "正在删除配置…";
  try {
    const data = await api(`/api/settings/ai-providers/${encodeURIComponent(profileId)}${action === "activate" ? "/activate" : ""}`, { method: action === "activate" ? "POST" : "DELETE" });
    applyAiProfiles(data.ai_providers);
    if (action === "delete" && (aiEditingProfileId === profileId || aiSourceProfileId === profileId)) showSettingsPage("ai");
    result.textContent = data.message;
    showToast(data.message);
  } catch (error) {
    result.textContent = error.message;
    result.classList.add("setting-error");
  } finally {
    aiProfilesBusy = false;
    refreshAiButtons();
  }
}

function aiModelServiceUrl(value) {
  try {
    const url = new URL(value.trim());
    const path = url.pathname.replace(/\/+$/, "");
    url.pathname = !path ? "/v1/chat/completions" : path.endsWith("/chat/completions") ? path : `${path}/chat/completions`;
    return url.href;
  } catch (_error) { return value.trim(); }
}

function renderAiModelChoices(models, textModel = "", visionModel = "", saved = false) {
  const textSelect = $("#ai-model");
  const visionSelect = $("#ai-vision-model");
  // Before reading a catalog, keep saved models visible so existing settings
  // remain usable. Once read, both selectors contain only the returned IDs.
  const choices = [...new Set(saved ? [...models, textModel, visionModel].filter(Boolean) : models)];
  const options = choices.map(model => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join("");
  textSelect.innerHTML = `<option value="" disabled>${choices.length ? "请选择文字模型" : "请先读取可用模型"}</option>${options}`;
  visionSelect.innerHTML = `<option value="">与文字模型相同</option>${options}`;
  textSelect.value = choices.includes(textModel) ? textModel : choices[0] || "";
  visionSelect.value = choices.includes(visionModel) ? visionModel : "";
  textSelect.disabled = choices.length === 0;
  visionSelect.disabled = choices.length === 0;
}

function resetAiModelChoices() {
  aiModelCatalog = { apiUrl: "", models: [] };
  renderAiModelChoices([]);
}

function fillAiProviderForm(provider) {
  $("#ai-api-url").value = provider.api_url || "";
  const sameService = aiModelCatalog.apiUrl === aiModelServiceUrl(provider.api_url || "");
  if (!sameService) aiModelCatalog = { apiUrl: "", models: [] };
  renderAiModelChoices(sameService ? aiModelCatalog.models : [], provider.model || "", provider.vision_model || "", !sameService);
  $("#ai-vision-enabled").checked = Boolean(provider.vision_enabled);
  $("#ai-vision-model-field").hidden = !provider.vision_enabled;
}

function aiProviderDraft() {
  return {
    profile_id: aiEditingProfileId || aiSourceProfileId,
    api_url: $("#ai-api-url").value.trim(), model: $("#ai-model").value.trim(),
    api_key: $("#api-key-input").value.trim(),
    vision_enabled: $("#ai-vision-enabled").checked,
    vision_model: $("#ai-vision-model").value.trim(),
  };
}

async function probeAiProvider(readModels = false) {
  if (aiFormBusy) return;
  const draft = aiProviderDraft();
  const editorVersion = aiEditorVersion;
  const result = $("#ai-test-result");
  result.classList.remove("setting-error");
  if (!draft.api_url || (!readModels && !draft.model)) {
    result.textContent = readModels ? "请填写接口 URL。" : "请填写接口 URL，读取可用模型并选择文字模型。";
    result.classList.add("setting-error");
    return;
  }
  if (!draft.api_key && !draft.profile_id) {
    result.textContent = "请填写新配置的 API Key，或从已有配置复制。";
    result.classList.add("setting-error");
    return;
  }
  aiFormBusy = true;
  refreshAiButtons();
  result.textContent = readModels ? "正在读取可用模型…" : "正在测试实际生成，请稍候…";
  try {
    const data = await api(`/api/settings/ai-provider/${readModels ? "models" : "test"}`, {
      method: "POST", body: JSON.stringify(readModels ? { api_url: draft.api_url, api_key: draft.api_key, profile_id: draft.profile_id } : draft),
    });
    if (editorVersion !== aiEditorVersion) return;
    if (JSON.stringify(aiProviderDraft()) !== JSON.stringify(draft)) {
      result.textContent = "测试期间设置已变更，请使用当前设置重新测试。";
      return;
    }
    if (readModels) {
      aiModelCatalog = { apiUrl: aiModelServiceUrl(draft.api_url), models: data.models };
      renderAiModelChoices(data.models, draft.model, draft.vision_model);
      result.textContent = `已读取 ${data.models.length} 个模型，请在下拉列表中选择文字模型和视觉模型，再测试连接。`;
    } else {
      result.textContent = `${data.message} ${data.checks.map(check => `${check.mode === "vision" ? "图片" : "文字"}：${check.model}（${(check.elapsed_ms / 1000).toFixed(1)} 秒）`).join("；")}`;
    }
  } catch (error) {
    if (editorVersion !== aiEditorVersion) return;
    if (JSON.stringify(aiProviderDraft()) !== JSON.stringify(draft)) {
      result.textContent = "测试期间设置已变更，请使用当前设置重新测试。";
      return;
    }
    const diagnosis = aiFailureDiagnosis(error.message);
    result.textContent = diagnosis ? `${diagnosis.title}：${diagnosis.reason}\n本次测试使用当前正在编辑的配置。\n处理方法：${diagnosis.action}\n技术详情：${diagnosis.raw}` : error.message;
    result.classList.add("setting-error");
  } finally {
    aiFormBusy = false;
    refreshAiButtons();
  }
}

async function updateAiProvider(event) {
  event.preventDefault();
  if (aiFormBusy || aiProfilesBusy) return;
  const input = $("#api-key-input");
  const result = $("#api-key-result");
  const draft = aiProviderDraft();
  const editorVersion = aiEditorVersion;
  const fields = { name: $("#ai-profile-name").value.trim(), provider_name: $("#ai-provider-name").value.trim(), activate: $("#ai-profile-activate").checked };
  const stillEditing = () => editorVersion === aiEditorVersion && JSON.stringify(aiProviderDraft()) === JSON.stringify(draft)
    && fields.name === $("#ai-profile-name").value.trim() && fields.provider_name === $("#ai-provider-name").value.trim() && fields.activate === $("#ai-profile-activate").checked;
  if (!draft.api_url || !draft.model || !fields.name) {
    result.textContent = "请填写配置名称、接口 URL，读取可用模型并选择文字模型。";
    result.classList.add("setting-error");
    return;
  }
  aiFormBusy = true;
  aiProfilesBusy = true;
  refreshAiButtons();
  result.classList.remove("setting-error");
  result.textContent = "正在保存 AI 接入设置…";
  try {
    const { profile_id, ...access } = draft;
    const data = await api(`/api/settings/ai-providers${aiEditingProfileId ? `/${encodeURIComponent(aiEditingProfileId)}` : ""}`, {
      method: aiEditingProfileId ? "PUT" : "POST", body: JSON.stringify({ ...access, ...fields, source_profile_id: aiSourceProfileId }),
    });
    const unchanged = stillEditing();
    applyAiProfiles(data.ai_providers);
    const savedMessage = data.profile_id === data.ai_providers.active_profile_id ? "配置已保存并启用，将用于新开始的分析。" : "配置已保存，当前启用项未切换。";
    if (unchanged) {
      input.value = "";
      showSettingsPage("ai");
    } else if (editorVersion === aiEditorVersion) {
      // Keep edits typed while saving, and attach a newly created draft to its
      // saved ID so a retry updates it rather than adding another profile.
      aiEditingProfileId = data.profile_id;
      aiSourceProfileId = null;
      if (input.value.trim() === draft.api_key) input.value = "";
      applyAiProfiles(data.ai_providers);
      result.textContent = "已保存提交时的内容。当前编辑内容有变更，请再次保存。";
    }
    $("#ai-profiles-result").textContent = savedMessage;
    $("#ai-profiles-result").classList.remove("setting-error");
    showToast(savedMessage);
  } catch (error) {
    if (stillEditing()) {
      result.textContent = error.message;
      result.classList.add("setting-error");
    }
    showToast(error.message, true);
  } finally {
    aiFormBusy = false;
    aiProfilesBusy = false;
    refreshAiButtons();
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
  $("#source-form-error").textContent = $("#source-platform").value === "douyin" ? "正在读取抖音博主的公开作品，请稍候…" : $("#source-platform").value === "xiaohongshu" ? "正在读取小红书博主的视频与图文，请稍候…" : isZhihu ? "正在验证知乎账号并读取当前内容…" : "正在验证B站账号并读取当前视频…";
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
  const { sourcePlatform: platform, sourceUid: uid, sourceRequestId: requestId } = state;
  const button = $("#retry-source-summaries");
  button.disabled = true;
  try {
    const data = await api(`/api/sources/${encodeURIComponent(platform)}/${encodeURIComponent(uid)}/retry-summaries`, { method: "POST" });
    showToast(data.message);
    if (state.currentView === "source-detail" && state.sourceRequestId === requestId) await loadSourceDetail(platform, uid);
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; }
}

async function checkNow() {
  const buttons = [$("#check-now"), $("#mobile-check-now")];
  if (buttons.some(button => button.disabled)) return;
  buttons.forEach(button => { button.disabled = true; button.setAttribute("aria-busy", "true"); });
  try {
    const data = await api("/api/check-now", { method: "POST" });
    showToast(data.message);
    [2000, 6000, 12000, 25000].forEach((delay) => setTimeout(() => {
      if (state.currentView === "dashboard") loadDashboard();
    }, delay));
  }
  catch (error) { showToast(error.message, true); }
  finally { buttons.forEach(button => { button.disabled = false; button.removeAttribute("aria-busy"); }); }
}

async function loadMoreDashboardContent(event) {
  const button = event.currentTarget;
  const previous = state.dashboard, requestId = state.dashboardRequestId;
  if (!previous || button.disabled) return;
  const isCurrent = () => state.currentView === "dashboard" && state.dashboard === previous && state.dashboardRequestId === requestId;
  button.disabled = true;
  try {
    const data = await api(`/api/dashboard?offset=${previous.next_offset || 0}`);
    if (!isCurrent()) return;
    state.dashboard = {...data, updates: mergeContent(previous.updates || [], data.updates)};
    renderUpdates($("#dashboard-updates"), state.dashboard.updates, "暂无消息");
    button.hidden = !data.has_more;
  } catch (error) { if (isCurrent()) showToast(error.message, true); }
  finally { if (state.dashboardRequestId === requestId) button.disabled = false; }
}


function bindEvents() {
  $$('[data-settings-page]').forEach(button => button.addEventListener("click", () => showSettingsPage(button.dataset.settingsPage)));
  $("#back-to-settings").addEventListener("click", () => showSettingsPage(state.settingsPage === "ai-profile" ? "ai" : null));
  $("#preview-cache").addEventListener("click", async () => {
    try {
      const data = await api("/api/cache/preview?days=30");
      $("#cache-preview-status").textContent = `${data.file_count} 个超过30天的原始媒体文件，约 ${(data.bytes / 1048576).toFixed(1)} MB。成功简报与分析依据会保留。`;
      $("#cleanup-cache").hidden = data.file_count === 0;
    } catch (error) { showToast(error.message, true); }
  });
  $("#cleanup-cache").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try { const data = await api("/api/cache/cleanup", {method: "POST", body: JSON.stringify({days: 30})}); $("#cache-preview-status").textContent = `已清理 ${data.removed} 个原始媒体文件。`; button.hidden = true; }
    catch (error) { showToast(error.message, true); }
    finally { button.disabled = false; }
  });
  $("#task-budget-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    button.disabled = true;
    try { await api("/api/tasks/budget", {method: "PUT", body: JSON.stringify({max_daily_attempts: Number($("#task-daily-budget").value)})}); await loadTaskStatus(); showToast("分析预算已保存"); }
    catch (error) { showToast(error.message, true); }
    finally { button.disabled = false; }
  });
  $("#refresh-tasks").addEventListener("click", loadTaskStatus);
  $("#dashboard-load-more").addEventListener("click", loadMoreDashboardContent);
  $("#source-load-more").addEventListener("click", loadMoreSourceContent);
  $("#sidebar-toggle").addEventListener("click", () => {
    setSidebarCollapsed(!$(".app-shell").classList.contains("sidebar-collapsed"), true);
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
    if (event.target !== event.currentTarget) return;
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
  $("#mobile-check-now").addEventListener("click", checkNow);
  $("#automation-toggle").addEventListener("change", updateAutomation);
  $("#open-add-source").addEventListener("click", openSourceModal);
  $("#close-source-modal").addEventListener("click", () => { $("#source-modal").hidden = true; });
  $("#source-modal").addEventListener("click", (event) => { if (event.target.id === "source-modal") event.currentTarget.hidden = true; });
  $("#source-form").addEventListener("submit", addSource);
  $("#source-platform").addEventListener("change", syncSourceFormPlatform);
  $("#retry-source-summaries").addEventListener("click", retrySourceSummaries);
  $("#delete-source").addEventListener("click", openDeleteSourceModal);
  $("#toggle-source-following").addEventListener("click", toggleSourceFollowing);
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
  $("#ai-profile-new").addEventListener("click", () => editAiProfile());
  $("#ai-profile-cancel").addEventListener("click", () => showSettingsPage("ai"));
  $("#ai-profiles-list").addEventListener("change", event => {
    const profileId = event.target.dataset.aiActivate;
    if (profileId && event.target.checked) changeAiProfile(profileId, "activate");
  });
  $("#ai-profiles-list").addEventListener("click", event => {
    const button = event.target.closest("button[data-ai-action]");
    if (!button || button.disabled) return;
    if (button.dataset.aiAction === "delete") changeAiProfile(button.dataset.profileId, "delete");
    else editAiProfile(button.dataset.profileId, button.dataset.aiAction === "copy");
  });
  $("#video-analysis-form").addEventListener("submit", updateVideoAnalysis);
  $("#test-video-input").addEventListener("click", testVideoInput);
  $("#media-knowledge-form").addEventListener("submit", addMediaKnowledge);
  $("#ai-test-connection").addEventListener("click", () => probeAiProvider());
  $("#ai-read-models").addEventListener("click", () => probeAiProvider(true));
  ["#ai-api-url", "#api-key-input", "#ai-model", "#ai-vision-enabled", "#ai-vision-model"].forEach(selector => {
    $(selector).addEventListener("input", () => {
      $("#ai-test-result").textContent = "设置已变更，请重新测试连接。";
      $("#ai-test-result").classList.remove("setting-error");
      if (selector === "#ai-api-url" || selector === "#api-key-input") resetAiModelChoices();
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
initializeSidebar();
initializeDialogs();
bindEvents();
updateClock();
setInterval(updateClock, 1000);
initializeHost();
loadDashboard();
