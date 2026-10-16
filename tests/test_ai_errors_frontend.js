const assert = require('node:assert/strict');
const {harness, check} = require('./test_source_navigation_frontend');

const legacy = 'AI API错误 HTTP 400: 当前模型不可用，请在设置中读取可用模型，填写准确的模型 ID 后测试连接。Upstream request failed: Model is unavailable.';
const failed = {platform: 'bilibili', article_id: 'test', title: '视频', ai_status: 'error', ai_error: legacy,
  last_attempt_status: 'error', last_attempt_error: legacy, visual_error: legacy};
const count = (text, value) => text.split(value).length - 1;

(async () => {
  await check('legacy model failures have one diagnosis, one escaped technical detail and actionable steps', () => {
    const h = harness();
    const card = h.context.updateCard(failed);
    assert.equal(count(card, 'Upstream request failed'), 1);
    assert.equal(count(card, '所选 AI 模型不可用'), 1);
    assert.match(card, /读取可用模型.*下拉列表.*测试连接.*保存/);
    assert.match(card, /未标明实际失败模型/);
    assert.match(card, /历史请求可能使用之前启用的配置/);
    assert.match(card, /<details><summary>查看技术详情/);
    assert.match(card, /ai-fix-config/);
    h.context.renderBriefing(failed);
    assert.equal(count(h.nodes.get('#briefing-ai-failure').innerHTML, 'Upstream request failed'), 1);
    assert.doesNotMatch(h.nodes.get('#briefing-summary').textContent, /Model is unavailable/);
    const malicious = {...failed, ai_error: 'AI API错误 HTTP 400: <img src=x onerror=alert(1)>', last_attempt_error: '', visual_error: ''};
    assert.doesNotMatch(h.context.updateCard(malicious), /<img/);
    assert.match(h.context.updateCard(malicious), /&lt;img/);
    assert.doesNotThrow(() => h.context.aiFailureDiagnosis('请求模型："bad\\x"；输入：文字；AI API错误 HTTP 400'));
  });

  await check('actual request input is reported without guessing from current profiles', () => {
    const h = harness();
    for (const input of ['文字', '画面']) {
      const error = `请求模型："old-model"；输入：${input}；AI API错误 HTTP 400: Model is unavailable.`;
      assert.match(h.context.aiFailureDiagnosis(error).request, new RegExp(`${input}输入，模型 old-model`));
    }
    const cases = [
      ['AI API错误 HTTP 400: invalid parameter', 'AI 接口请求未通过'],
      ['AI API错误 HTTP 401: rejected', 'AI 密钥未配置或验证失败'],
      ['AI API错误 HTTP 403: rejected', 'AI 接口拒绝访问'],
      ['AI API错误 HTTP 429: insufficient_quota', 'AI 服务额度不足'],
      ['AI API错误 HTTP 429: slow down', 'AI 请求触发频率限制'],
      ['连接AI接口失败: timed out', 'AI 接口连接失败或超时'],
      ['AI API错误 HTTP 503: upstream down', 'AI 服务暂时异常'],
    ];
    for (const [error, title] of cases) assert.equal(h.context.aiFailureDiagnosis(error).title, title);
    assert.equal(h.context.aiFailureDiagnosis('需要登录小红书'), null);
  });

  await check('successful briefings survive failed retries and text-only fallback stays readable', () => {
    const h = harness();
    const item = {...failed, ai_status: 'available', ai_summary: '上次成功简报', ai_error: ''};
    h.context.renderBriefing(item);
    assert.equal(h.nodes.get('#briefing-summary').textContent, '上次成功简报');
    assert.match(h.nodes.get('#briefing-ai-failure').innerHTML, /已保留现有简报/);
    h.context.renderBriefing({...item, last_attempt_status: 'available', last_attempt_error: '', visual_error: ''});
    assert.equal(h.nodes.get('#briefing-ai-failure').hidden, true);
    h.context.renderBriefing({...item, last_attempt_status: 'available', visual_error: legacy});
    assert.equal(h.nodes.get('#briefing-ai-failure').hidden, false);
  });

  await check('card and detail buttons open the refreshed profile list without switching a provider', async () => {
    const h = harness();
    const button = {addEventListener(type, listener) { this[type] = listener; }};
    const container = {querySelectorAll(selector) { return selector === '.ai-fix-config' ? [button] : []; }};
    h.context.renderUpdates(container, [failed], '空');
    assert.equal(typeof button.click, 'function');
    const pending = button.click();
    assert.equal(h.state.currentView, 'settings');
    assert.equal(h.state.settingsPage, 'ai');
    assert.equal(h.requests.at(-1).url, '/api/settings');
    // The original configuration has since been deleted; never open or activate a guessed replacement.
    h.requests.at(-1).resolve({ai_providers: {active_profile_id: 'new', profiles: [{id: 'new', name: '另一套', model: 'new-model'}]}});
    await pending;
    assert.equal(h.state.settingsPage, 'ai');
    assert.match(h.nodes.get('#ai-profiles-list').innerHTML, /另一套/);
    assert.equal(h.requests.filter(request => request.options.method).length, 0);
    h.context.editAiProfile('new');
    h.nodes.get('#api-key-input').value = 'unsaved-secret';
    await h.context.openAiFailureSettings();
    assert.equal(h.state.settingsPage, 'ai-profile');
    assert.equal(h.nodes.get('#api-key-input').value, 'unsaved-secret');
    const detail = h.nodes.get('#briefing-ai-failure');
    detail.querySelectorAll = container.querySelectorAll;
    h.context.renderBriefing(failed);
    assert.equal(typeof button.click, 'function');
  });

  await check('test failures explain edits in the current profile', async () => {
    const h = harness();
    h.context.editAiProfile();
    h.nodes.get('#ai-api-url').value = 'https://example.com/v1/chat/completions';
    h.nodes.get('#ai-model').value = 'bad-model';
    h.nodes.get('#api-key-input').value = 'unsaved-key';
    const pending = h.context.probeAiProvider();
    h.requests.at(-1).reject(new Error(legacy));
    await pending;
    const message = h.nodes.get('#ai-test-result').textContent;
    assert.match(message, /所选 AI 模型不可用/);
    assert.match(message, /当前正在编辑的配置/);
    assert.match(message, /下拉列表.*测试连接.*保存/);
    assert.equal(h.nodes.get('#api-key-input').value, 'unsaved-key');
  });
})().catch(error => {console.error(error); process.exitCode = 1;});
