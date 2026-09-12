"use strict";

const state = { scenarios: [], selected: null };

const el = (id) => document.getElementById(id);

function text(node, value) {
  node.textContent = value === null || value === undefined ? "—" : String(value);
  return node;
}

function make(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

const OK_STATUS = new Set(["ok", "verified", "completed", "written", "extracted", "succeeded"]);
const BAD_STATUS = new Set(["failed", "error", "unavailable", "missing", "rejected"]);

function tone(value) {
  const key = String(value ?? "").toLowerCase();
  if (OK_STATUS.has(key)) return "ok";
  if (BAD_STATUS.has(key)) return "bad";
  if (key === "" || key === "null") return "";
  return "warn";
}

function pill(value) {
  const node = make("span", `pill ${tone(value)}`);
  return text(node, value);
}

function stat(label, value) {
  const wrap = make("dl", "stat");
  wrap.append(make("dt", null, label));
  const dd = make("dd");
  if (value instanceof Node) dd.append(value);
  else text(dd, value);
  wrap.append(dd);
  return wrap;
}

async function loadUpstreams() {
  const target = el("upstreams");
  target.replaceChildren();
  try {
    const response = await fetch("/console/upstreams");
    const data = await response.json();
    for (const upstream of data.upstreams) {
      const node = make("div", `upstream ${upstream.reachable ? "" : "down"}`);
      node.append(`${upstream.label} `);
      node.append(pill(upstream.reachable ? "ok" : "unreachable"));
      node.title = `${upstream.base_url} — ${upstream.description}`;
      target.append(node);
    }
  } catch (error) {
    target.append(make("div", "upstream down", `console unreachable: ${error}`));
  }
}

function selectScenario(key) {
  const scenario = state.scenarios.find((item) => item.key === key);
  if (!scenario) return;
  state.selected = scenario;
  for (const button of document.querySelectorAll(".scenario")) {
    button.setAttribute("aria-pressed", String(button.dataset.key === key));
  }
  text(el("expectation"), scenario.expectation);
  text(el("target-url"), scenario.url);
  el("payload").value = scenario.payload
    ? JSON.stringify(scenario.payload, null, 2)
    : "// The synthetic fixture is not ready yet. Start the fixture service and reload.";
}

async function loadScenarios() {
  const response = await fetch("/console/scenarios");
  const data = await response.json();
  state.scenarios = data.scenarios;
  const list = el("scenarios");
  list.replaceChildren();
  for (const scenario of data.scenarios) {
    const button = make("button", "scenario");
    button.type = "button";
    button.dataset.key = scenario.key;
    button.setAttribute("aria-pressed", "false");
    button.append(make("strong", null, scenario.title));
    button.append(make("span", null, `POST ${scenario.upstream_label} /v1/reviews`));
    button.addEventListener("click", () => selectScenario(scenario.key));
    list.append(button);
  }
  selectScenario(state.selected ? state.selected.key : data.scenarios[0].key);
}

function renderCall(result) {
  const node = el("call");
  node.hidden = false;
  node.replaceChildren();
  node.append(`POST ${result.url} → `);
  node.append(pill(result.status_code));
  node.append(` ${result.elapsed_ms} ms · upstream ${result.upstream}`);
}

function renderOutcome(run) {
  const node = el("outcome");
  node.replaceChildren();
  node.append(stat("case", run.case_id));
  node.append(stat("workflow status", pill(run.status)));
  node.append(stat("artifact status", pill(run.artifact_status)));
  const summary = run.review && run.review.summary;
  node.append(
    stat(
      "total adjustment",
      summary && summary.total_adjustment_percent !== null
        ? `${summary.total_adjustment_percent} %`
        : "—",
    ),
  );
  if (run.case_review) node.append(stat("review status", pill(run.case_review.status)));
}

function renderVerification(run) {
  const node = el("verification");
  node.replaceChildren();
  const report = run.verification;
  if (!report) {
    node.append(make("p", "note", "The run reported no verification record."));
    return;
  }
  const line = make("p", "note");
  line.append("Verification: ");
  line.append(pill(report.status));
  node.append(line);
  for (const [label, items] of [
    ["Critical errors", report.critical_errors || []],
    ["Warnings", report.warnings || []],
  ]) {
    if (!items.length) continue;
    node.append(make("h3", null, label));
    const list = make("ul");
    for (const item of items) {
      list.append(make("li", null, typeof item === "string" ? item : JSON.stringify(item)));
    }
    node.append(list);
  }
}

function renderPipeline(run) {
  const node = el("pipeline");
  node.replaceChildren();
  const events = run.audit_events || [];
  if (!events.length) {
    node.append(make("li", null, "The run recorded no audit events."));
    return;
  }
  for (const event of events) {
    const item = make("li");
    item.append(make("span", "seq", `${event.sequence}`));
    const middle = make("div");
    middle.append(make("span", "what", event.event_type));
    const detail = [
      `tool: ${event.tool}`,
      event.rule_ids && event.rule_ids.length ? `rules: ${event.rule_ids.join(", ")}` : "",
      event.evidence_ids && event.evidence_ids.length
        ? `evidence: ${event.evidence_ids.join(", ")}`
        : "",
    ]
      .filter(Boolean)
      .join(" · ");
    middle.append(make("span", "tool", detail));
    item.append(middle);
    item.append(pill(event.status));
    item.title = JSON.stringify(event.details || {}, null, 2);
    node.append(item);
  }
}

function renderComparisons(run) {
  const node = el("comparisons");
  node.replaceChildren();
  const review = run.review;
  if (!review) {
    node.append(make("p", "note", "The run produced no factor comparison."));
    return;
  }
  const meta = make("p", "note");
  meta.textContent =
    `rule set ${review.rule_set_id} v${review.rule_version}` +
    (review.context
      ? ` · ${review.context.scope}: ${review.context.target_id} vs ${review.context.comparable_id}`
      : "");
  node.append(meta);

  const wrap = make("div", "scroll");
  const table = make("table");
  const head = make("tr");
  for (const column of [
    "factor",
    "target grade",
    "comparable grade",
    "adjustment %",
    "rule",
    "status",
    "calculation trace",
  ]) {
    head.append(make("th", null, column));
  }
  const thead = make("thead");
  thead.append(head);
  table.append(thead);
  const body = make("tbody");
  for (const result of review.results || []) {
    const row = make("tr");
    row.append(make("td", "mono", result.factor_id));
    row.append(make("td", "mono", result.target_grade ?? "—"));
    row.append(make("td", "mono", result.comparable_grade ?? "—"));
    row.append(make("td", "mono", result.adjustment_percent ?? "—"));
    row.append(make("td", "mono", result.rule_id ?? "—"));
    const status = make("td");
    status.append(pill(result.status));
    row.append(status);
    row.append(make("td", null, result.calculation_trace));
    body.append(row);
  }
  table.append(body);
  wrap.append(table);
  node.append(wrap);

  const hashes = review.source_hashes || {};
  if (Object.keys(hashes).length) {
    node.append(make("h3", null, "Source hashes"));
    const list = make("ul");
    list.style.fontFamily = "var(--mono)";
    list.style.fontSize = "11px";
    for (const [id, hash] of Object.entries(hashes)) {
      list.append(make("li", null, `${id}: ${hash}`));
    }
    node.append(list);
  }
}

const pageCache = new Map();

async function loadPage(documentId, number) {
  const key = `${documentId}:${number}`;
  if (!pageCache.has(key)) {
    const url = `/console/page?document_id=${encodeURIComponent(documentId)}&number=${number}`;
    pageCache.set(
      key,
      fetch(url).then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.error ? body.error.message : "render failed");
        return body;
      }),
    );
  }
  return pageCache.get(key);
}

async function toggleLocation(button, holder, evidence) {
  if (holder.dataset.open === "true") {
    holder.dataset.open = "false";
    holder.replaceChildren();
    button.textContent = "Locate on page";
    return;
  }
  button.disabled = true;
  button.textContent = "Rendering…";
  try {
    const page = await loadPage(evidence.document_id, evidence.page);
    const frame = make("div", "page-frame");
    const image = make("img");
    image.src = page.image;
    image.alt = `${page.document_id} page ${page.page}`;
    frame.append(image);
    const [x0, y0, x1, y1] = evidence.bbox;
    const box = make("div", "page-box");
    // The parser reports regions in PDF bottom-left space; the render is top-left.
    box.style.left = `${(x0 / page.width) * 100}%`;
    box.style.width = `${((x1 - x0) / page.width) * 100}%`;
    box.style.top = `${((page.height - y1) / page.height) * 100}%`;
    box.style.height = `${((y1 - y0) / page.height) * 100}%`;
    frame.append(box);
    holder.replaceChildren(frame);
    holder.append(
      make(
        "p",
        "note",
        `${page.document_id} v${page.version} (${page.role}) · page ${page.page} · ` +
          `${Math.round(page.width)} × ${Math.round(page.height)} pt · ${page.coordinate_system}`,
      ),
    );
    holder.dataset.open = "true";
    button.textContent = "Hide page";
  } catch (error) {
    holder.replaceChildren(make("p", "note", `Could not render the page: ${error.message}`));
    holder.dataset.open = "true";
    button.textContent = "Hide page";
  } finally {
    button.disabled = false;
  }
}

function renderFindings(run) {
  const node = el("findings");
  node.replaceChildren();
  const findings = (run.case_review && run.case_review.findings) || [];
  if (!findings.length) {
    node.append(make("p", "note", "The run recorded no findings."));
    return;
  }
  for (const finding of findings) {
    const card = make("div", "finding");
    const head = make("div", "finding-head");
    head.append(pill(finding.status));
    head.append(make("span", null, finding.kind));
    head.append(make("span", null, finding.id));
    if (finding.rule_id) head.append(make("span", null, `rule ${finding.rule_id}`));
    card.append(head);
    card.append(make("p", null, finding.trace));
    if (finding.observed !== null || finding.expected !== null) {
      card.append(
        make("p", "note", `observed: ${finding.observed ?? "—"} · expected: ${finding.expected ?? "—"}`),
      );
    }
    const seen = new Set();
    for (const evidence of finding.evidence || []) {
      const key = `${evidence.document_id}:${evidence.page}:${evidence.region_id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const quote = make("div", "evidence");
      const where = `${evidence.document_id} v${evidence.version} · page ${evidence.page}` +
        (evidence.bbox ? ` · bbox [${evidence.bbox.map((n) => Math.round(n)).join(", ")}]` : "");
      const head2 = make("div", "evidence-head");
      head2.append(make("span", null, where));
      if (evidence.bbox && evidence.document_id) {
        const locate = make("button", "ghost small", "Locate on page");
        const holder = make("div", "page-holder");
        locate.addEventListener("click", () => toggleLocation(locate, holder, evidence));
        head2.append(locate);
        quote.append(head2);
        const excerpt = make("q");
        excerpt.textContent = (evidence.excerpt || "").trim();
        quote.append(excerpt);
        quote.append(holder);
      } else {
        quote.append(head2);
        const excerpt = make("q");
        excerpt.textContent = (evidence.excerpt || "").trim();
        quote.append(excerpt);
      }
      card.append(quote);
    }
    node.append(card);
  }
}

function renderCoverage(run) {
  const node = el("coverage");
  node.replaceChildren();
  const coverage = run.case_review && run.case_review.coverage;
  if (!coverage) {
    node.append(make("p", "note", "The run reported no coverage record."));
    return;
  }
  const grid = make("div", "coverage");
  for (const key of ["required", "verified", "missing", "unsupported"]) {
    const items = coverage[key] || [];
    const box = make("div");
    const title = make("div");
    title.append(`${key} `);
    title.append(pill(items.length));
    box.append(title);
    if (items.length) {
      const list = make("ul");
      for (const item of items) list.append(make("li", null, item));
      box.append(list);
    }
    grid.append(box);
  }
  node.append(grid);
}

function renderArtifact(run) {
  const node = el("artifact");
  node.replaceChildren();
  const line = make("p", "note");
  line.append("artifact_status: ");
  line.append(pill(run.artifact_status));
  node.append(line);
  if (run.pdf_error) {
    node.append(make("pre", null, JSON.stringify(run.pdf_error, null, 2)));
  }
  if (run.artifact_status === "written" && run.output_pdf_uri) {
    const link = make("a", null, "Download the written PDF");
    link.href = `/console/artifact?uri=${encodeURIComponent(run.output_pdf_uri)}`;
    link.target = "_blank";
    link.rel = "noopener";
    const wrap = make("p");
    wrap.append(link);
    node.append(wrap);
    node.append(make("p", "note", run.output_pdf_uri));
  }
  if (run.pdf_result) {
    node.append(
      make(
        "p",
        "note",
        `fields written: ${(run.pdf_result.written_field_ids || []).join(", ") || "—"} · pages: ${run.pdf_result.page_count ?? "—"}`,
      ),
    );
  }
}

function renderError(body) {
  el("results").hidden = false;
  const node = el("outcome");
  node.replaceChildren();
  node.append(stat("result", pill("error")));
  el("verification").replaceChildren(
    make("pre", null, JSON.stringify(body, null, 2)),
  );
  for (const id of ["pipeline", "comparisons", "findings", "coverage", "artifact"]) {
    el(id).replaceChildren(make("p", "note", "No review result was returned."));
  }
}

async function run() {
  if (!state.selected) return;
  const button = el("run");
  button.disabled = true;
  button.textContent = "Running…";
  try {
    let payload;
    try {
      payload = JSON.parse(el("payload").value);
    } catch (error) {
      renderCall({ url: state.selected.url, status_code: "invalid json", elapsed_ms: 0, upstream: state.selected.upstream_label });
      renderError({ error: { code: "invalid_json", message: String(error) } });
      return;
    }
    const response = await fetch("/console/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: state.selected.key, payload }),
    });
    const result = await response.json();
    if (!response.ok) {
      renderCall({ url: state.selected.url, status_code: response.status, elapsed_ms: 0, upstream: state.selected.upstream_label });
      renderError(result);
      return;
    }
    renderCall(result);
    el("raw").textContent = JSON.stringify(result.body, null, 2);
    if (result.status_code !== 200 || !result.body || !result.body.case_id) {
      renderError(result.body);
      return;
    }
    el("results").hidden = false;
    renderOutcome(result.body);
    renderVerification(result.body);
    renderPipeline(result.body);
    renderComparisons(result.body);
    renderFindings(result.body);
    renderCoverage(result.body);
    renderArtifact(result.body);
  } finally {
    button.disabled = false;
    button.textContent = "Send review request";
    loadUpstreams();
  }
}

el("run").addEventListener("click", run);
el("reset").addEventListener("click", () => loadScenarios());

loadUpstreams();
loadScenarios();
setInterval(loadUpstreams, 15000);
