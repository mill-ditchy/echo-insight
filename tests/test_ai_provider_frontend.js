const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {harness, check} = require('./test_source_navigation_frontend');

const html = fs.readFileSync(path.join(__dirname, '../web/static/index.html'), 'utf8');
assert.match(html, /<select id="ai-model"/);
assert.match(html, /<select id="ai-vision-model"/);
assert.doesNotMatch(html, /<input id="ai-(?:vision-)?model"/);

const first = {id: 'a', name: '日常分析', provider_name: '服务商 A', api_url: 'https://same.example/v1/chat/completions',
  model: 'text-a', vision_enabled: true, vision_model: 'vision-a', api_key: {configured: true}, active: true};
const second = {...first, id: 'b', name: '视频实验', provider_name: '服务商 B', model: 'text-b', active: false};
function catalog(profiles = [first, second], active = 'a') { return {profiles, active_profile_id: active}; }
function response(profiles = [first, second], active = 'a', saved = 'a') {
  return {message: '已保存', ai_provider: profiles.find(p => p.id === active), ai_providers: catalog(profiles, active), profile_id: saved};
}
function setup() {
  const h = harness();
  h.n = selector => h.nodes.get(selector);
  h.submit = {};
  h.n('#api-key-form').querySelector = () => h.submit;
  h.context.applyAiProfiles(catalog());
  h.context.editAiProfile('a');
  h.save = () => h.context.updateAiProvider({preventDefault() {}});
  return h;
}
function lastRequest(h) { return h.requests.at(-1); }
function body(h) { return JSON.parse(lastRequest(h).options.body); }

(async () => {
  await check('saved profiles render and edit without activating; keys are cleared between editors', async () => {
    const h = setup();
    assert.match(h.n('#ai-profiles-list').innerHTML, /role="switch"/);
    assert.match(h.n('#ai-key-status').textContent, /日常分析/);
    assert.equal(h.n('#ai-profile-activate').disabled, true);
    h.n('#api-key-input').value = 'private-draft';
    const edit = {disabled: false, dataset: {aiAction: 'edit', profileId: 'b'}};
    h.n('#ai-profiles-list').listeners.get('click')({target: {closest: () => edit}});
    assert.equal(h.n('#ai-profile-name').value, '视频实验');
    assert.equal(h.n('#ai-model').value, 'text-b');
    assert.equal(h.n('#api-key-input').value, '');
    assert.equal(h.n('#ai-profile-activate').checked, false);
    assert.equal(h.requests.length, 0);
  });

  await check('editing an inactive profile saves its own ID without switching', async () => {
    const h = setup();
    h.context.editAiProfile('b');
    h.n('#ai-model').value = 'text-c';
    h.n('#api-key-input').value = 'test-secret';
    const pending = h.save();
    assert.equal(lastRequest(h).url, '/api/settings/ai-providers/b');
    assert.equal(lastRequest(h).options.method, 'PUT');
    assert.equal(body(h).api_key, 'test-secret');
    assert.equal(body(h).activate, false);
    lastRequest(h).resolve(response([first, {...second, model: 'text-c'}], 'a', 'b'));
    await pending;
    assert.equal(h.n('#api-key-input').value, '');
    assert.equal(h.n('#ai-model').value, 'text-c');
    assert.match(h.n('#api-key-result').textContent, /当前启用项未切换/);
    assert.match(h.n('#ai-key-status').textContent, /日常分析/);
    assert.equal(h.submit.disabled, false);
  });

  await check('new and copied profiles default to save only; a copy references the source key', async () => {
    const h = setup();
    h.context.editAiProfile('b', true);
    assert.equal(h.n('#ai-profile-name').value, '视频实验 副本');
    assert.equal(h.n('#ai-profile-activate').checked, false);
    const pending = h.save();
    assert.equal(lastRequest(h).url, '/api/settings/ai-providers');
    assert.equal(lastRequest(h).options.method, 'POST');
    assert.equal(body(h).source_profile_id, 'b');
    assert.equal(body(h).api_key, '');
    const copy = {...second, id: 'c', name: '视频实验 副本'};
    lastRequest(h).resolve(response([first, second, copy], 'a', 'c'));
    await pending;
    assert.equal(h.context.aiProviderDraft().profile_id, 'c');
    h.n('#ai-profile-new').listeners.get('click')();
    assert.equal(h.n('#ai-profile-name').value, '');
    assert.equal(h.n('#ai-model').value, '');
    assert.equal(h.context.aiProviderDraft().profile_id, null);
    assert.equal(h.n('#ai-profile-activate').checked, false);
    h.n('#ai-api-url').value = first.api_url;
    const count = h.requests.length;
    await h.context.probeAiProvider(true);
    assert.equal(h.requests.length, count, 'a new empty-key draft cannot silently borrow the active key');
    assert.match(h.n('#ai-test-result').textContent, /填写新配置的 API Key/);
  });

  await check('activation changes the active item while preserving an unrelated draft', async () => {
    const h = setup();
    h.n('#ai-profile-name').value = '还没保存的名字';
    h.n('#api-key-input').value = 'draft-secret';
    h.n('#ai-profiles-list').listeners.get('change')({target: {dataset: {aiActivate: 'b'}, checked: true}});
    assert.equal(lastRequest(h).url, '/api/settings/ai-providers/b/activate');
    lastRequest(h).resolve(response([first, second], 'b', null));
    await new Promise(resolve => setImmediate(resolve));
    assert.match(h.n('#ai-key-status').textContent, /视频实验/);
    assert.equal(h.n('#ai-profile-name').value, '还没保存的名字');
    assert.equal(h.n('#api-key-input').value, 'draft-secret');
    assert.equal(h.n('#ai-profile-activate').checked, false);
    assert.equal(h.n('#ai-profile-activate').disabled, false);
    const pending = h.save();
    assert.equal(body(h).activate, false);
    lastRequest(h).resolve(response([first, second], 'b', 'a'));
    await pending;
  });

  await check('failed activation restores switches and retains the active profile', async () => {
    const h = setup();
    const pending = h.context.changeAiProfile('b', 'activate');
    lastRequest(h).reject(new Error('密钥不可用'));
    await pending;
    assert.match(h.n('#ai-key-status').textContent, /日常分析/);
    assert.match(h.n('#ai-profiles-result').textContent, /密钥不可用/);
    assert.match(h.n('#ai-profiles-list').innerHTML, /data-ai-activate="a"[^>]*checked/);
    assert.doesNotMatch(h.n('#ai-profiles-list').innerHTML, /data-ai-activate="b"[^>]*checked/);
    assert.equal(h.submit.disabled, false);
  });

  await check('testing and model reads use the edited profile, keep secrets unsaved, and retain dropdown choices', async () => {
    const h = setup();
    h.context.editAiProfile('b');
    let pending = h.context.probeAiProvider(true);
    assert.equal(body(h).profile_id, 'b');
    assert.equal(body(h).api_key, '');
    lastRequest(h).resolve({models: ['text-b', 'vision-a', 'other']});
    await pending;
    assert.equal(h.n('#ai-model').value, 'text-b');
    assert.equal(h.n('#ai-vision-model').value, 'vision-a');
    h.n('#ai-model').value = 'other';
    h.n('#api-key-input').value = 'probe-secret';
    pending = h.context.probeAiProvider();
    assert.equal(body(h).profile_id, 'b');
    assert.equal(body(h).api_key, 'probe-secret');
    assert.equal(body(h).model, 'other');
    assert.equal(body(h).vision_model, 'vision-a');
    lastRequest(h).resolve({message: '连接成功', checks: [{mode: 'vision', model: 'vision-a', elapsed_ms: 100}]});
    await pending;
    assert.match(h.n('#ai-test-result').textContent, /图片：vision-a/);
    assert.equal(h.n('#api-key-input').value, 'probe-secret');
    assert.match(h.n('#ai-key-status').textContent, /日常分析/);
    pending = h.save();
    lastRequest(h).resolve(response([first, {...second, model: 'other'}], 'a', 'b'));
    await pending;
    assert.match(h.n('#ai-model').innerHTML, /text-b/);
    assert.match(h.n('#ai-vision-model').innerHTML, /other/);
    assert.equal(h.n('#ai-vision-model').value, 'vision-a');
  });

  await check('old model responses and errors cannot replace a different editor', async () => {
    for (const fail of [false, true]) {
      const h = setup();
      const pending = h.context.probeAiProvider(true);
      const request = lastRequest(h);
      h.context.editAiProfile('b');
      if (fail) request.reject(new Error('old error'));
      else request.resolve({models: ['stale-model']});
      await pending;
      assert.equal(h.n('#ai-model').value, 'text-b');
      assert.doesNotMatch(h.n('#ai-model').innerHTML, /stale-model/);
      assert.equal(h.n('#ai-test-result').textContent, '');
      assert.equal(h.n('#ai-read-models').disabled, false);
    }
  });

  await check('late save responses update the list but preserve the newer editor and its key', async () => {
    const h = setup();
    const pending = h.save();
    const request = lastRequest(h);
    h.context.editAiProfile('b');
    h.n('#api-key-input').value = 'newer-draft-key';
    request.resolve(response([first, second], 'a', 'a'));
    await pending;
    assert.equal(h.n('#ai-profile-name').value, '视频实验');
    assert.equal(h.n('#api-key-input').value, 'newer-draft-key');
    assert.equal(h.n('#api-key-result').textContent, '');
    assert.equal(h.submit.disabled, false);
  });

  await check('typing during a create keeps the draft and attaches the saved ID for the next save', async () => {
    const h = setup();
    h.context.editAiProfile('b', true);
    h.n('#ai-profile-activate').checked = true;
    let pending = h.save();
    assert.equal(body(h).activate, true);
    h.n('#ai-profile-name').value = '保存期间修改的名字';
    const saved = {...second, id: 'c', name: '视频实验 副本'};
    lastRequest(h).resolve(response([first, second, saved], 'c', 'c'));
    await pending;
    assert.equal(h.n('#ai-profile-name').value, '保存期间修改的名字');
    assert.equal(h.context.aiProviderDraft().profile_id, 'c');
    assert.match(h.n('#api-key-result').textContent, /请再次保存/);
    pending = h.save();
    assert.equal(lastRequest(h).url, '/api/settings/ai-providers/c');
    assert.equal(lastRequest(h).options.method, 'PUT');
    lastRequest(h).resolve(response([first, second, {...saved, name: '保存期间修改的名字'}], 'c', 'c'));
    await pending;
    assert.match(h.n('#api-key-result').textContent, /保存并启用/);
  });

  await check('failed saves preserve the draft key; failed model reads preserve choices', async () => {
    const h = setup();
    h.n('#api-key-input').value = 'retry-secret';
    let pending = h.save();
    lastRequest(h).reject(new Error('could not save'));
    await pending;
    assert.equal(h.n('#api-key-input').value, 'retry-secret');
    assert.equal(h.n('#api-key-result').textContent, 'could not save');
    assert.equal(h.submit.disabled, false);
    const choices = h.n('#ai-model').innerHTML;
    pending = h.context.probeAiProvider(true);
    lastRequest(h).reject(new Error('unavailable'));
    await pending;
    assert.equal(h.n('#ai-model').innerHTML, choices);
    assert.equal(h.n('#ai-test-result').textContent, 'unavailable');
    assert.equal(h.n('#ai-read-models').disabled, false);
  });

  await check('changed URLs and keys reset model choices; changed drafts reject old reads', async () => {
    const h = setup();
    let pending = h.context.probeAiProvider(true);
    h.n('#ai-api-url').value = 'http://127.0.0.1:1234/v1';
    h.n('#ai-api-url').listeners.get('input')();
    lastRequest(h).resolve({models: ['stale-model']});
    await pending;
    assert.equal(h.n('#ai-model').disabled, true);
    assert.doesNotMatch(h.n('#ai-model').innerHTML, /stale-model/);
    assert.match(h.n('#ai-test-result').textContent, /设置已变更/);
    await h.save();
    assert.match(h.n('#api-key-result').textContent, /读取可用模型/);
    h.n('#api-key-input').value = 'new-secret';
    pending = h.context.probeAiProvider(true);
    lastRequest(h).resolve({models: ['new-model']});
    await pending;
    assert.equal(h.n('#ai-model').value, 'new-model');
    assert.equal(h.n('#ai-vision-model').value, '');
    h.n('#api-key-input').listeners.get('input')();
    assert.equal(h.n('#ai-model').disabled, true);
  });

  await check('deleting an inactive editor returns to the active profile', async () => {
    const h = setup();
    h.context.editAiProfile('b');
    const pending = h.context.changeAiProfile('b', 'delete');
    assert.equal(lastRequest(h).options.method, 'DELETE');
    lastRequest(h).resolve(response([first], 'a', null));
    await pending;
    assert.equal(h.context.aiProviderDraft().profile_id, 'a');
    assert.doesNotMatch(h.n('#ai-profiles-list').innerHTML, /视频实验/);
  });

  await check('names, providers and model IDs are escaped in profile cards and dropdowns', async () => {
    const h = setup();
    const malicious = {...first, name: '<img src=x>', provider_name: '"<script>', model: '<model&"'};
    h.context.applyAiProfiles(catalog([malicious]));
    assert.doesNotMatch(h.n('#ai-profiles-list').innerHTML, /<img|<script>|<model/);
    assert.match(h.n('#ai-profiles-list').innerHTML, /&lt;img/);
    h.context.renderAiModelChoices(['vision<&"model'], 'vision<&"model');
    assert.match(h.n('#ai-model').innerHTML, /vision&lt;&amp;&quot;model/);
    assert.match(h.n('#ai-vision-model').innerHTML, /vision&lt;&amp;&quot;model/);
    assert.equal(h.context.aiModelServiceUrl('http://127.0.0.1:1234/v1/'), h.context.aiModelServiceUrl('http://127.0.0.1:1234/v1/chat/completions'));
  });
})().catch(error => {console.error(error); process.exitCode = 1;});
