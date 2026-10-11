const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/static/app.js"), "utf8");
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
  assert.match(nodes.get("#ai-model-options").innerHTML, /available-model/);
  context.api = async () => {
    nodes.get("#ai-model").value = "edited-during-test";
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
  console.log("AI provider form checks passed (save, connection test, model list, failure recovery).");
})().catch((error) => { console.error(error); process.exitCode = 1; });
