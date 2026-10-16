const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/static/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../web/static/index.html"), "utf8");
assert.match(html, /<select id="ai-model"/);
assert.match(html, /<select id="ai-vision-model"/);
assert.doesNotMatch(html, /<input id="ai-(?:vision-)?model"/);
const nodes = new Map();
const context = { URL, setTimeout: () => {}, document: { querySelector: (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, { value: "", classList: { add() {}, remove() {}, toggle() {} }, style: {} });
  return nodes.get(selector);
} } };
vm.runInNewContext(source.slice(0, source.lastIndexOf("\ninitializeFontSettings();")), context);

(async () => {
  const provider = { api_url: "https://custom.example/v1/chat/completions", model: "my-model", vision_enabled: false, vision_model: "my-model", api_key: { configured: true } };
  context.fillAiProviderForm(provider);
  assert.equal(nodes.get("#ai-api-url").value, provider.api_url);
  assert.equal(nodes.get("#ai-model").value, "my-model");
  assert.equal(nodes.get("#ai-vision-model-field").hidden, true);
  context.document.querySelector("#api-key-input").value = "test-secret";
  const button = {};
  const event = { preventDefault() {}, currentTarget: { querySelector: () => button } };
  let sent;
  context.api = async (url, options) => {
    sent = { url, body: JSON.parse(options.body) };
    return { message: "settings saved", ai_provider: provider };
  };
  await context.updateAiProvider(event);
  assert.equal(sent.url, "/api/settings/ai-provider");
  assert.equal(sent.body.model, "my-model");
  assert.equal(sent.body.api_key, "test-secret");
  assert.equal(sent.body.vision_enabled, false);
  assert.equal(nodes.get("#api-key-input").value, "");
  assert.equal(button.disabled, false);
  context.document.querySelector("#api-key-form").querySelector = () => button;
  nodes.get("#api-key-input").value = "draft-secret";
  context.api = async (url, options) => {
    sent = { url, body: JSON.parse(options.body) };
    return { message: "连接成功", checks: [{ mode: "text", model: "my-model", elapsed_ms: 500 }] };
  };
  await context.probeAiProvider();
  assert.equal(sent.url, "/api/settings/ai-provider/test");
  assert.equal(sent.body.api_key, "draft-secret");
  assert.equal(nodes.get("#api-key-input").value, "draft-secret");
  assert.match(nodes.get("#ai-test-result").textContent, /连接成功/);
  assert.equal(nodes.get("#ai-test-connection").disabled, false);
  context.api = async () => { throw new Error("模型不可用"); };
  await context.probeAiProvider();
  assert.equal(nodes.get("#ai-test-result").textContent, "模型不可用");
  assert.equal(nodes.get("#ai-test-connection").disabled, false);
  context.api = async () => ({ models: ["available-model", "other-model"], message: "读取成功" });
  await context.probeAiProvider(true);
  assert.equal(nodes.get("#ai-model").value, "available-model");
  assert.match(nodes.get("#ai-model").innerHTML, /available-model/);
  assert.match(nodes.get("#ai-vision-model").innerHTML, /available-model/);
  assert.match(nodes.get("#ai-vision-model").innerHTML, /other-model/);
  assert.equal(nodes.get("#ai-model").disabled, false);
  assert.equal(nodes.get("#ai-vision-model").disabled, false);
  assert.equal(nodes.get("#ai-vision-model").value, "");
  nodes.get("#ai-model").value = "other-model";
  nodes.get("#ai-vision-model").value = "available-model";
  nodes.get("#ai-vision-enabled").checked = true;
  await context.probeAiProvider(true);
  assert.equal(nodes.get("#ai-model").value, "other-model");
  assert.equal(nodes.get("#ai-vision-model").value, "available-model");

  context.api = async (url, options) => {
    sent = { url, body: JSON.parse(options.body) };
    return { message: "连接成功", checks: [{ mode: "text", model: "other-model", elapsed_ms: 100 }, { mode: "vision", model: "available-model", elapsed_ms: 100 }] };
  };
  await context.probeAiProvider();
  assert.equal(sent.body.model, "other-model");
  assert.equal(sent.body.vision_model, "available-model");
  assert.equal(sent.body.vision_enabled, true);

  context.api = async (url, options) => {
    sent = { url, body: JSON.parse(options.body) };
    return { message: "settings saved", ai_provider: { ...provider, model: "other-model", vision_enabled: true, vision_model: "available-model" } };
  };
  await context.updateAiProvider(event);
  assert.equal(sent.body.vision_model, "available-model");
  assert.match(nodes.get("#ai-model").innerHTML, /available-model/);
  assert.match(nodes.get("#ai-vision-model").innerHTML, /other-model/);
  assert.equal(nodes.get("#ai-vision-model").value, "available-model");

  context.api = async () => {
    nodes.get("#ai-model").value = "available-model";
    return { message: "连接成功", checks: [{ mode: "text", model: "available-model", elapsed_ms: 100 }] };
  };
  await context.probeAiProvider();
  assert.match(nodes.get("#ai-test-result").textContent, /设置已变更/);
  assert.equal(nodes.get("#api-key-result").textContent, "settings saved");

  nodes.get("#api-key-input").value = "retry-secret";
  context.api = async () => { throw new Error("could not save"); };
  await context.updateAiProvider(event);
  assert.equal(nodes.get("#api-key-input").value, "retry-secret");
  assert.equal(nodes.get("#api-key-result").textContent, "could not save");
  assert.equal(button.disabled, false);

  const beforeFailure = nodes.get("#ai-model").innerHTML;
  await context.probeAiProvider(true);
  assert.equal(nodes.get("#ai-model").innerHTML, beforeFailure);

  context.api = async () => ({ models: ["other-model"], message: "读取成功" });
  await context.probeAiProvider(true);
  assert.equal(nodes.get("#ai-model").value, "other-model");
  assert.equal(nodes.get("#ai-vision-model").value, "");
  assert.doesNotMatch(nodes.get("#ai-model").innerHTML, /available-model/);

  context.resetAiModelChoices();
  assert.equal(nodes.get("#ai-model").value, "");
  assert.equal(nodes.get("#ai-model").disabled, true);
  assert.equal(nodes.get("#ai-vision-model").disabled, true);
  let called = false;
  context.api = async () => { called = true; };
  await context.probeAiProvider();
  await context.updateAiProvider(event);
  assert.equal(called, false);
  assert.match(nodes.get("#api-key-result").textContent, /读取可用模型/);

  context.api = async () => {
    nodes.get("#ai-api-url").value = "http://127.0.0.1:1234/v1";
    context.resetAiModelChoices();
    return { models: ["stale-model"], message: "读取成功" };
  };
  await context.probeAiProvider(true);
  assert.doesNotMatch(nodes.get("#ai-model").innerHTML, /stale-model/);

  context.renderAiModelChoices(['vision<&"model'], 'vision<&"model');
  assert.match(nodes.get("#ai-model").innerHTML, /vision&lt;&amp;&quot;model/);
  assert.match(nodes.get("#ai-vision-model").innerHTML, /vision&lt;&amp;&quot;model/);

  assert.equal(context.aiModelServiceUrl("http://127.0.0.1:1234/v1/"), context.aiModelServiceUrl("http://127.0.0.1:1234/v1/chat/completions"));
  console.log("AI provider form checks passed (text and vision dropdowns, selection retention, save, connection test, model refresh, stale response and failure recovery).");
})().catch((error) => { console.error(error); process.exitCode = 1; });
