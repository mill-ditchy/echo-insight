const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/static/app.js"), "utf8");
const nodes = new Map();
const context = { URL, document: { querySelector: (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, {});
  return nodes.get(selector);
} } };
vm.runInNewContext(source.slice(0, source.lastIndexOf("\ninitializeFontSettings();")), context);

const maliciousUrls = [
  "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\nscript:alert(1)",
  "data:text/html,<script>alert(1)</script>", "file:///C:/secret",
  "vbscript:msgbox(1)", "//attacker.example", "/api/accounts",
  "https://user:password@example.com/", " https://example.com/",
];
for (const link of maliciousUrls) {
  assert.equal(context.safeExternalUrl(link), "", link);
  const item = { link, title: "<img src=x onerror=alert(1)>" };
  assert.ok(!context.updateCard(item).includes('class="original-link"'), link);
  assert.ok(!context.updateCard(item).includes("<img src=x"));
  context.renderBriefing(item);
  assert.equal(nodes.get("#briefing-original-link").hidden, true);
  assert.equal(nodes.get("#briefing-original-link").href, "#");
}
for (const link of ["https://www.bilibili.com/video/BV123", "https://zhuanlan.zhihu.com/p/100", "http://example.com/"]) {
  assert.equal(context.safeExternalUrl(link), link);
  assert.ok(context.updateCard({ link }).includes('class="original-link"'));
  context.renderBriefing({ link });
  assert.equal(nodes.get("#briefing-original-link").hidden, false);
  assert.equal(nodes.get("#briefing-original-link").href, link);
}
console.log("Frontend security checks passed (13 URL cases; list and detail links).");
