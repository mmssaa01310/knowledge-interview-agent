/* The JSON is the source of facts; this file only renders its visual structure. */
(function () {
  "use strict";

  const fallback = { system: { purpose: "KIKIORIのコードベースマップを読み込めませんでした。", boundary: "codebase-map.jsonを確認してください。", runtimePath: [], status: "埋め込みの古い情報は表示しません。" }, components: [], flows: [], states: [], integrations: [], risks: [], tests: [], codeMap: [], sources: [] };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const $ = (id) => document.getElementById(id);
  const list = (items, render, separator = "") => (items || []).map(render).join(separator);
  const docsRoute = (href) => String(href || "").replace(/\.md(?=\/|#|$)/, "/");

  function render(model) {
    const system = model.system || {};
    $("system-purpose").textContent = system.purpose || "コードベースマップ";
    $("boundary-note").textContent = system.boundary || "責務境界を確認してください。";
    $("system-status").textContent = system.status || "";
    $("runtime-rail").innerHTML = list(system.runtimePath, (node, index) => `${index ? '<span class="runtime-arrow">→</span>' : ""}<span class="runtime-node">${esc(node)}</span>`);
    const high = (model.risks || []).filter((risk) => risk.level === "HIGH").length;
    const gaps = (model.tests || []).filter((test) => test.gap).length;
    $("kpis").innerHTML = [[model.components.length, "主要コンポーネント"], [model.flows.length, "可視化フロー"], [high, "HIGH risk"], [gaps, "検証ギャップ"]].map(([value, label]) => `<div class="kpi"><div class="kpi-value">${esc(value)}</div><div class="kpi-label">${esc(label)}</div></div>`).join("");
    $("flow-cards").innerHTML = list(model.flows, (flow) => `<article class="flow-card kind-${esc(flow.kind || "normal")}"><div><div class="flow-label">${esc(flow.label)}</div><p class="flow-summary">${esc(flow.summary)}</p></div><div><div class="step-rail">${list(flow.steps, (step, index) => `${index ? '<span class="step-arrow">→</span>' : ""}<span class="step">${esc(step)}</span>`)}</div>${flow.source ? `<a class="inline-source" data-doc-href="${esc(flow.source)}" href="${esc(flow.source)}">関連ドキュメント ↗</a>` : ""}</div></article>`);
    $("component-grid").innerHTML = list(model.components, (item) => `<article class="component-card" data-layer="${esc(item.layer)}"><span class="layer-tag">${esc(item.layer)}</span><div class="component-name">${esc(item.name)}</div><p class="component-role">${esc(item.role)}</p><div class="ownership"><div><b>Owns</b> ${esc(item.owns)}</div><div><b>Not owns</b> ${esc(item.notOwns)}</div></div><div class="depends">${list(item.dependsOn, (dependency) => `<span>${esc(dependency)}</span>`)}</div><span class="code-link" title="${esc(item.code)}">${esc(item.code)}</span></article>`);
    $("state-grid").innerHTML = list(model.states, (item) => `<article class="state-card"><h3>${esc(item.name)}</h3><div class="state-rail">${list(item.states, (state, index) => `${index ? '<span class="state-arrow">→</span>' : ""}<span class="state-pill">${esc(state)}</span>`)}</div><p class="state-meta"><b>Transitions</b> ${esc(item.transitions.join(" · "))}</p><p class="state-meta"><b>Guards</b> ${esc(item.guards.join(" · "))}</p><p class="state-meta"><b>Cancel</b> ${esc(item.cancel)}</p></article>`);
    $("integration-grid").innerHTML = list(model.integrations, (item) => `<article class="integration-card"><div class="integration-group">${esc(item.group)}</div><div class="integration-name">${esc(item.name)}</div><dl><dt>Purpose</dt><dd>${esc(item.purpose)}</dd><dt>Boundary</dt><dd>${esc(item.boundary)}</dd><dt>Failure / fallback</dt><dd>${esc(item.failure)}</dd></dl></article>`);
    $("risk-list").innerHTML = list(model.risks, (item) => `<article class="risk-item"><div class="risk-top"><span class="level level-${esc(item.level)}">${esc(item.level)}</span><span class="risk-topic">${esc(item.topic)}</span></div><p class="risk-impact">${esc(item.impact)}</p><div class="risk-action"><b>Next</b> ${esc(item.action)}</div></article>`);
    $("test-list").innerHTML = list(model.tests, (item) => `<article class="test-item"><div class="test-risk">${esc(item.risk)}</div><p class="test-behavior">${esc(item.behavior)}</p><div class="test-row"><b>Existing</b> ${esc(item.existing)}</div><div class="test-row"><b>Gap</b> ${esc(item.gap)}</div></article>`);
    $("code-list").innerHTML = list(model.codeMap, (item) => `<div class="code-item"><span class="code-path" title="${esc(item.path)}">${esc(item.path)}</span><span class="code-why">${esc(item.why)}</span></div>`);
    $("source-list").innerHTML = list(model.sources, (item) => `<a data-doc-href="${esc(item.href)}" href="${esc(item.href)}">${esc(item.label)} ↗</a>`);
    document.querySelectorAll("[data-doc-href]").forEach((anchor) => {
      const directHref = anchor.getAttribute("data-doc-href");
      if (!directHref || !directHref.split("#")[0].endsWith(".md")) return;
      const routeHref = docsRoute(directHref);
      fetch(routeHref, { method: "HEAD" })
        .then((response) => { if (response.ok) anchor.setAttribute("href", routeHref); })
        .catch(() => { /* Direct Markdown links remain usable on the source dashboard server. */ });
    });
  }

  fetch("codebase-map.json", { cache: "no-store" })
    .then((response) => response.ok ? response.json() : Promise.reject(new Error("map unavailable")))
    .then(render)
    .catch(() => { render(fallback); document.body.dataset.fallback = "true"; });
}());
