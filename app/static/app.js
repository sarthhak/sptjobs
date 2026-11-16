// Single-page UI: chat, GTM audits, change sets, dashboards, reports.
const { renderChart, renderMarkdown, esc, fmt } = window.AE;
const view = document.getElementById("view");
let STATUS = {};

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (res.status === 401) { location.href = "/login"; throw new Error("Not logged in"); }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(data.detail || data || res.statusText);
  return data;
}
const post = (path, body = {}) => api(path, { method: "POST", body: JSON.stringify(body) });
const when = (ts) => new Date(ts * 1000).toLocaleString();
const sevPill = (s) => `<span class="pill sev-${s}">${s}</span>`;

function setNav(name) {
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
}

async function loadStatus() {
  try {
    STATUS = await api("/api/status");
    document.getElementById("status").innerHTML =
      `Model: ${esc(STATUS.model)}<br>Google: ${esc(STATUS.google.split(":")[0])}<br>GTM writes: ${STATUS.writes_enabled ? "on" : "off"}`;
  } catch (e) { /* shown on the Connection page */ }
}

// ---------------------------------------------------------------- router
async function route() {
  const [name, id] = (location.hash.slice(1) || "chat").split("/");
  setNav(name);
  const pages = { chat: chatPage, audits: auditsPage, ga4audits: ga4AuditsPage, changes: changesPage, dashboards: dashboardsPage, reports: reportsPage, settings: settingsPage };
  try {
    await (pages[name] || chatPage)(id);
  } catch (e) {
    view.innerHTML = `<div class="page"><div class="banner">${esc(e.message)}</div></div>`;
  }
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- chat
const EXAMPLES = [
  "Which GA4 properties and GTM containers can you see?",
  "How did sessions and key events change in the last 28 days vs the previous 28, by channel?",
  "Which landing pages have the worst engagement rate with meaningful traffic?",
  "Audit my GTM container and tell me what to fix first.",
  "Build a dashboard with traffic by channel, daily users and top landing pages.",
  "Audit my GA4 property and tell me what to fix first.",
];

async function chatPage(id) {
  const convs = await api("/api/conversations");
  view.innerHTML = `
    <div class="chat">
      <aside class="convs">
        <button class="primary" id="newConv" style="width:100%;margin-bottom:10px">New conversation</button>
        <div id="convList"></div>
      </aside>
      <section class="thread-wrap">
        <div class="thread"><div class="thread-inner" id="thread"></div></div>
        <div class="composer">
          <form id="composer">
            <textarea id="input" rows="2" placeholder="Ask about your data, or ask for an audit, report or dashboard..."></textarea>
            <button class="primary" id="send">Send</button>
          </form>
        </div>
      </section>
    </div>`;
  const showConvs = (list, active) => {
    document.getElementById("convList").innerHTML = list.map((c) =>
      `<div class="list-item ${String(c.id) === String(active) ? "active" : ""}" data-id="${c.id}">${esc(c.title)}</div>`).join("");
    document.querySelectorAll("#convList .list-item").forEach((el) => el.onclick = () => location.hash = `chat/${el.dataset.id}`);
  };
  showConvs(convs, id);
  document.getElementById("newConv").onclick = async () => { const c = await post("/api/conversations"); location.hash = `chat/${c.id}`; };

  const thread = document.getElementById("thread");
  if (!id) {
    thread.innerHTML = `<h1>What do you want to know?</h1>
      <p class="muted">I can analyse your GA4 data, audit and fix your GTM containers, and build reports and dashboards.</p>
      <div class="examples">${EXAMPLES.map((e) => `<button>${esc(e)}</button>`).join("")}</div>`;
    thread.querySelectorAll(".examples button").forEach((b) => b.onclick = () => { document.getElementById("input").value = b.textContent; });
  } else {
    const data = await api(`/api/conversations/${id}`);
    for (const item of data.items) renderItem(thread, item);
  }
  const form = document.getElementById("composer");
  const input = document.getElementById("input");
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    let cid = id;
    if (!cid) { cid = (await post("/api/conversations")).id; history.replaceState(null, "", `#chat/${cid}`); thread.innerHTML = ""; id = String(cid); }
    input.value = "";
    await sendMessage(cid, text, thread);
    showConvs(await api("/api/conversations"), cid);
  };
  scrollDown();
}

function scrollDown() { const t = document.querySelector(".thread"); if (t) t.scrollTop = t.scrollHeight; }

function eventCard(ev) {
  const div = document.createElement("div");
  div.className = "event";
  if (ev.type === "changeset") div.innerHTML = `<b>Change set #${ev.id} proposed.</b> Nothing has changed in GTM yet. <a href="#changes/${ev.id}">Review it</a>`;
  else if (ev.type === "audit") div.innerHTML = `<b>Audit #${ev.id}</b>${ev.container ? " of " + esc(ev.container) : ""}${ev.score != null ? ` scored ${ev.score}/100` : ""}. <a href="#audits/${ev.id}">Open the findings</a>`;
  else if (ev.type === "ga4audit") div.innerHTML = `<b>GA4 audit #${ev.id}</b>${ev.container ? " of " + esc(ev.container) : ""}${ev.score != null ? ` scored ${ev.score}/100` : ""}. <a href="#ga4audits/${ev.id}">Open the findings</a>`;
  else if (ev.type === "report") div.innerHTML = `<b>Report saved.</b> <a href="/reports/${ev.id}" target="_blank">Open report #${ev.id}</a>`;
  else if (ev.type === "dashboard") div.innerHTML = `<b>Dashboard saved.</b> <a href="#dashboards/${ev.id}">Open dashboard #${ev.id}</a>`;
  return div;
}

function toolLine(name, input) {
  const div = document.createElement("div");
  div.className = "tool";
  div.innerHTML = `<details><summary>Used <code>${esc(name)}</code></summary><pre>${esc(JSON.stringify(input, null, 2))}</pre></details>`;
  return div;
}

function renderItem(thread, item) {
  if (item.role === "user") {
    const d = document.createElement("div"); d.className = "msg user"; d.textContent = item.text; thread.appendChild(d);
  } else if (item.role === "assistant") {
    const d = document.createElement("div"); d.className = "msg assistant"; renderMarkdown(d, item.text); thread.appendChild(d);
  } else if (item.role === "tool") {
    thread.appendChild(toolLine(item.name, item.input));
  } else if (item.type === "chart") {
    renderChart(thread, item.spec);
  } else {
    thread.appendChild(eventCard(item));
  }
}

async function sendMessage(cid, text, thread) {
  const sendBtn = document.getElementById("send");
  sendBtn.disabled = true;
  renderItem(thread, { role: "user", text });
  let current = null, buffer = "", thinking = null;
  const newText = () => { current = document.createElement("div"); current.className = "msg assistant"; thread.appendChild(current); buffer = ""; };
  const status = document.createElement("div"); status.className = "tool"; status.textContent = "Thinking...";
  thread.appendChild(status);
  scrollDown();
  try {
    const res = await fetch(`/api/conversations/${cid}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const chunks = pending.split("\n\n");
      pending = chunks.pop();
      for (const chunk of chunks) {
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "text") {
          if (!current) newText();
          thinking = null;
          buffer += ev.text;
          renderMarkdown(current, buffer);
        } else if (ev.type === "thinking") {
          if (!thinking) { thinking = document.createElement("div"); thinking.className = "thinking"; thread.insertBefore(thinking, status); }
          thinking.textContent += ev.text;
        } else if (ev.type === "tool_pending") {
          status.textContent = `Preparing ${ev.name}...`;
        } else if (ev.type === "tool_start") {
          current = null; thinking = null;
          thread.insertBefore(toolLine(ev.name, ev.input), status);
          status.textContent = `Running ${ev.name}...`;
        } else if (ev.type === "tool_end") {
          status.textContent = ev.ok ? "Thinking..." : `${ev.name} returned an error; continuing...`;
        } else if (ev.type === "chart") {
          const holder = document.createElement("div"); thread.insertBefore(holder, status); renderChart(holder, ev.spec);
        } else if (["changeset", "audit", "ga4audit", "report", "dashboard"].includes(ev.type)) {
          thread.insertBefore(eventCard(ev), status);
        } else if (ev.type === "error") {
          const d = document.createElement("div"); d.className = "banner"; d.textContent = ev.message; thread.insertBefore(d, status);
        }
        thread.appendChild(status); // keep the status line last
        scrollDown();
      }
    }
  } catch (e) {
    const d = document.createElement("div"); d.className = "banner"; d.textContent = e.message; thread.appendChild(d);
  } finally {
    status.remove();
    sendBtn.disabled = false;
    scrollDown();
  }
}

// ---------------------------------------------------------------- audits (shared pieces)
const SEVERITIES = ["critical", "high", "medium", "low", "info"];

function scoreCard(score, counts, sub) {
  const pills = SEVERITIES.filter((s) => counts[s]).map((s) => `${sevPill(s)} ${counts[s]}`).join(" &nbsp; ");
  return `<div class="card row" style="gap:28px">
    <div><div class="score">${score}</div><div class="muted">health score / 100</div></div>
    <div>${pills || "No findings"}<div class="muted" style="margin-top:6px">${sub}</div></div></div>`;
}

function filterBar(id) {
  return `<div class="row" style="margin:10px 0">
    <select id="${id}-sev"><option value="">All severities</option>${SEVERITIES.map((s) => `<option>${s}</option>`).join("")}</select>
    <input id="${id}-q" placeholder="Search names, details..." style="flex:1;min-width:200px"></div>`;
}

function wireFilter(id, selector) {
  const apply = () => {
    const sev = document.getElementById(`${id}-sev`)?.value || "";
    const q = (document.getElementById(`${id}-q`)?.value || "").toLowerCase();
    view.querySelectorAll(selector).forEach((el) => {
      const okSev = !sev || el.dataset.sev === sev;
      el.style.display = okSev && (!q || el.textContent.toLowerCase().includes(q)) ? "" : "none";
    });
  };
  document.getElementById(`${id}-sev`)?.addEventListener("change", apply);
  document.getElementById(`${id}-q`)?.addEventListener("input", apply);
}

function tabs(names) {
  return `<div class="tabs">${names.map(([key, label], i) => `<button class="tab ${i ? "" : "active"}" data-tab="${key}">${label}</button>`).join("")}</div>`;
}

function wireTabs() {
  view.querySelectorAll(".tab").forEach((b) => b.onclick = () => {
    view.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
    view.querySelectorAll("[data-panel]").forEach((p) => p.style.display = p.dataset.panel === b.dataset.tab ? "" : "none");
  });
}

const list = (items) => (items && items.length ? items.map((x) => `<div>${esc(x)}</div>`).join("") : '<span class="muted">-</span>');

function findingCard(f, i, extra = "") {
  return `<div class="finding" data-sev="${f.severity}">
    <div class="finding-head">
      ${f.fixable ? `<input type="checkbox" value="${esc(f.id)}" title="Include the automatic fix">` : ""}
      <span class="muted">#${i}</span> ${sevPill(f.severity)} <b>${esc(f.title)}</b>
      <span class="tag-chip">${esc(f.category || "")}</span>${f.fixable ? '<span class="tag-chip ok">Auto-fix available</span>' : ""}
    </div>
    <div class="finding-grid">
      <div><div class="label">What's happening</div>${esc(f.detail)}</div>
      <div><div class="label">Why it matters</div>${esc(f.impact || "")}</div>
      <div><div class="label">How to fix</div>${esc(f.recommendation)}<div class="muted" style="margin-top:4px">Owner: ${esc(f.owner || "")}</div></div>
    </div>${extra}</div>`;
}

// ---------------------------------------------------------------- GTM audits
async function auditsPage(id) {
  if (id) return auditDetail(id);
  const audits = await api("/api/audits");
  view.innerHTML = `<div class="page">
    <h1>GTM audits</h1>
    <div class="card">
      <div class="row">
        <select id="container" style="min-width:320px"><option>Loading containers...</option></select>
        <button class="primary" id="run">Run audit</button>
        <span class="muted" id="runStatus"></span>
      </div>
      <p class="muted" style="margin-bottom:0">Audits read the live (published) container version. They never change anything.</p>
    </div>
    <div class="card" style="padding:0">${audits.length ? audits.map((a) => `
      <div class="list-item" data-id="${a.id}"><span><b>#${a.id}</b> ${esc(a.container_name)} <span class="muted">version ${esc(a.version_id)}</span></span>
      <span>${a.score}/100 <span class="muted">${when(a.created)}</span></span></div>`).join("") : '<p class="muted" style="padding:12px">No audits yet.</p>'}</div>
  </div>`;
  view.querySelectorAll(".list-item").forEach((el) => el.onclick = () => location.hash = `audits/${el.dataset.id}`);
  const sel = document.getElementById("container");
  try {
    const cs = await api("/api/containers");
    sel.innerHTML = cs.map((c) => `<option value="${esc(c.container_path)}">${esc(c.account_name)} / ${esc(c.container_name)} (${esc(c.public_id)})</option>`).join("") || "<option>No containers found</option>";
  } catch (e) { sel.innerHTML = `<option>${esc(e.message)}</option>`; }
  document.getElementById("run").onclick = async () => {
    document.getElementById("runStatus").textContent = "Running...";
    try { const a = await post("/api/audits", { container_path: sel.value }); location.hash = `audits/${a.id}`; }
    catch (e) { document.getElementById("runStatus").textContent = e.message; }
  };
}

function affectedTable(items) {
  if (!items || !items.length) return "";
  return `<div style="overflow-x:auto;margin-top:10px"><table class="compact"><thead><tr><th>Item</th><th>Name</th><th>Type</th><th>Status</th><th>Fires on</th><th>Details</th></tr></thead><tbody>
    ${items.map((a) => `<tr><td>${esc(a.kind)}</td><td><b>${esc(a.name)}</b> <span class="muted">#${esc(a.id)}</span></td><td>${esc(a.type)}</td>
      <td>${esc(a.status || "")}</td><td>${esc(a.triggers || "")}</td><td class="details">${esc(a.details || "")}</td></tr>`).join("")}</tbody></table></div>`;
}

async function auditDetail(id) {
  const a = await api(`/api/audits/${id}`);
  const inv = a.inventory;
  const issueCell = (xs) => xs.length ? `<span class="pill sev-high" title="${esc(xs.join("\n"))}">${xs.length}</span>` : "";
  view.innerHTML = `<div class="page">
    <p><a href="#audits">&larr; GTM audits</a></p>
    <div class="row" style="justify-content:space-between">
      <h1>${esc(a.container)} <span class="muted">(${esc(a.public_id)}, version ${esc(a.version_id)})</span></h1>
      <a href="/api/audits/${id}/export.xlsx"><button>Download Excel</button></a>
    </div>
    ${scoreCard(a.score, a.counts, `${a.totals.tags} tags, ${a.totals.triggers} triggers, ${a.totals.variables} variables. Run ${when(a.created)}.`)}
    ${tabs([["findings", `Findings (${a.findings.length})`], ["tags", `Tags (${inv.tags.length})`], ["triggers", `Triggers (${inv.triggers.length})`], ["variables", `Variables (${inv.variables.length})`]])}
    <div data-panel="findings">
      <div class="row" style="justify-content:space-between">
        ${filterBar("f")}
        <span class="row"><button id="selectFixable">Select all auto-fixes</button><button class="primary" id="propose">Create change set from selected</button></span>
      </div>
      ${a.findings.map((f, i) => findingCard(f, i + 1, affectedTable(f.affected))).join("") || '<p class="muted">No findings. Nice.</p>'}
    </div>
    <div data-panel="tags" style="display:none"><div class="card" style="overflow-x:auto"><table class="compact">
      <thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Fires on</th><th>Blocked by</th><th>Consent</th><th>Details</th><th>Issues</th></tr></thead><tbody>
      ${inv.tags.map((t) => `<tr><td><b>${esc(t.name)}</b><div class="muted">#${esc(t.id)}${t.folder ? " · " + esc(t.folder) : ""}</div></td><td>${esc(t.type)}</td><td>${esc(t.status)}</td>
        <td>${list(t.fires_on)}</td><td>${list(t.blocked_by)}</td><td>${esc(t.consent)}</td><td class="details">${esc(t.details)}</td><td>${issueCell(t.issues)}</td></tr>`).join("")}
    </tbody></table></div></div>
    <div data-panel="triggers" style="display:none"><div class="card" style="overflow-x:auto"><table class="compact">
      <thead><tr><th>Name</th><th>Type</th><th>Conditions</th><th>Used by tags</th><th>Issues</th></tr></thead><tbody>
      ${inv.triggers.map((t) => `<tr><td><b>${esc(t.name)}</b><div class="muted">#${esc(t.id)}${t.folder ? " · " + esc(t.folder) : ""}</div></td><td>${esc(t.type)}</td>
        <td class="details">${esc(t.conditions)}</td><td>${list(t.used_by)}</td><td>${issueCell(t.issues)}</td></tr>`).join("")}
    </tbody></table>
    <p class="muted">Built-in triggers in use: ${esc(inv.built_in_triggers_used.join(", ") || "none")}</p></div></div>
    <div data-panel="variables" style="display:none"><div class="card" style="overflow-x:auto"><table class="compact">
      <thead><tr><th>Name</th><th>Type</th><th>Details</th><th>Used by</th><th>Issues</th></tr></thead><tbody>
      ${inv.variables.map((v) => `<tr><td><b>${esc(v.name)}</b><div class="muted">#${esc(v.id)}${v.folder ? " · " + esc(v.folder) : ""}</div></td><td>${esc(v.type)}</td>
        <td class="details">${esc(v.details)}</td><td>${list(v.used_by)}</td><td>${issueCell(v.issues)}</td></tr>`).join("")}
    </tbody></table>
    <p class="muted">Enabled built-in variables: ${esc(inv.built_in_variables.join(", ") || "none")}</p></div></div>
  </div>`;
  wireTabs();
  wireFilter("f", ".finding");
  const boxes = () => [...view.querySelectorAll(".finding input[type=checkbox]")];
  document.getElementById("selectFixable").onclick = () => boxes().forEach((b) => b.checked = true);
  document.getElementById("propose").onclick = async () => {
    const ids = boxes().filter((b) => b.checked).map((b) => b.value);
    if (!ids.length) return alert("Tick at least one finding that has an automatic fix.");
    try {
      const r = await post(`/api/audits/${id}/changeset`, { finding_ids: ids });
      if (r.errors?.length) alert("Some fixes were skipped:\n" + r.errors.join("\n"));
      location.hash = `changes/${r.id}`;
    } catch (e) { alert(e.message); }
  };
}

// ---------------------------------------------------------------- GA4 audits
async function ga4AuditsPage(id) {
  if (id) return ga4AuditDetail(id);
  const audits = await api("/api/ga4-audits");
  view.innerHTML = `<div class="page">
    <h1>GA4 audits</h1>
    <div class="card">
      <div class="row">
        <select id="property" style="min-width:320px"><option>Loading properties...</option></select>
        <button class="primary" id="run">Run audit</button>
        <span class="muted" id="runStatus"></span>
      </div>
      <p class="muted" style="margin-bottom:0">Checks the property's settings and the quality of the last 28 days of data. Read-only.</p>
    </div>
    <div class="card" style="padding:0">${audits.length ? audits.map((a) => `
      <div class="list-item" data-id="${a.id}"><span><b>#${a.id}</b> ${esc(a.property_name)} <span class="muted">${esc(a.property)}</span></span>
      <span>${a.score}/100 <span class="muted">${when(a.created)}</span></span></div>`).join("") : '<p class="muted" style="padding:12px">No GA4 audits yet.</p>'}</div>
  </div>`;
  view.querySelectorAll(".list-item").forEach((el) => el.onclick = () => location.hash = `ga4audits/${el.dataset.id}`);
  const sel = document.getElementById("property");
  try {
    const ps = await api("/api/properties");
    sel.innerHTML = ps.map((p) => `<option value="${esc(p.property)}">${esc(p.account)} / ${esc(p.property_name)} (${esc(p.property)})</option>`).join("") || "<option>No properties found</option>";
  } catch (e) { sel.innerHTML = `<option>${esc(e.message)}</option>`; }
  document.getElementById("run").onclick = async () => {
    document.getElementById("runStatus").textContent = "Running... (about 10-30 seconds)";
    try { const a = await post("/api/ga4-audits", { property: sel.value }); location.hash = `ga4audits/${a.id}`; }
    catch (e) { document.getElementById("runStatus").textContent = e.message; }
  };
}

async function ga4AuditDetail(id) {
  const a = await api(`/api/ga4-audits/${id}`);
  const ov = a.overview || {};
  const num = (v) => (typeof v === "number" ? fmt.format(v) : "-");
  const evidence = (f) => f.evidence?.length ? `<div style="margin-top:10px"><div class="label">Evidence</div><ul class="evidence">${f.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : "";
  view.innerHTML = `<div class="page">
    <p><a href="#ga4audits">&larr; GA4 audits</a></p>
    <div class="row" style="justify-content:space-between">
      <h1>${esc(a.property_name)} <span class="muted">(${esc(a.property)})</span></h1>
      <a href="/api/ga4-audits/${id}/export.xlsx"><button>Download Excel</button></a>
    </div>
    ${scoreCard(a.score, a.counts, `${esc(a.period)}: ${num(ov.sessions)} sessions, ${num(ov.totalUsers)} users, ${typeof ov.engagementRate === "number" ? (ov.engagementRate * 100).toFixed(1) + "%" : "-"} engagement, ${num(ov.keyEvents)} key events. Run ${when(a.created)}.`)}
    ${Object.keys(a.errors || {}).length ? `<div class="banner">Some checks could not run: ${esc(Object.entries(a.errors).map(([k, v]) => `${k} (${v})`).join("; "))}</div>` : ""}
    ${tabs([["findings", `Findings (${a.findings.length})`], ["settings", "Property settings"], ["data", "Traffic & events"]])}
    <div data-panel="findings">${filterBar("g")}
      ${a.findings.map((f, i) => findingCard(f, i + 1, evidence(f))).join("") || '<p class="muted">No findings. Nice.</p>'}</div>
    <div data-panel="settings" style="display:none"><div class="card"><table class="compact">
      ${Object.entries(a.settings).map(([k, v]) => `<tr><th style="width:220px">${esc(k)}</th><td class="details">${esc(v || "-")}</td></tr>`).join("")}</table></div></div>
    <div data-panel="data" style="display:none"><div class="grid">
      <div class="card"><b>Sessions by channel</b><table class="compact">${(a.channels || []).map((r) => `<tr><td>${esc(r.sessionDefaultChannelGroup)}</td><td class="num">${num(r.sessions)}</td></tr>`).join("")}</table></div>
      <div class="card"><b>Events</b><table class="compact">${(a.top_events || []).map((r) => `<tr><td>${esc(r.eventName)}</td><td class="num">${num(r.eventCount)}</td></tr>`).join("")}</table></div>
    </div></div>
  </div>`;
  wireTabs();
  wireFilter("g", ".finding");
}

// ---------------------------------------------------------------- change sets
const STATUS_TEXT = {
  proposed: "Proposed - review below, then apply to a new GTM workspace.",
  applying: "Applying...",
  applied: "Applied to a GTM workspace (not live yet). Check it in GTM Preview, then publish.",
  published: "Published and live.",
  failed: "Failed - nothing was published. See the log.",
  discarded: "Discarded.",
  rolled_back: "Rolled back to the previous version.",
};

async function changesPage(id) {
  if (id) return changeDetail(id);
  const list = await api("/api/changesets");
  view.innerHTML = `<div class="page"><h1>Changes</h1>
    <p class="muted">Change sets proposed by the assistant or created from an audit. Nothing reaches GTM until you apply it, and nothing goes live until you publish.</p>
    <div class="card" style="padding:0">${list.length ? list.map((c) => `
      <div class="list-item" data-id="${c.id}"><span><b>#${c.id}</b> ${esc(c.summary)}</span><span><span class="pill">${esc(c.status)}</span> <span class="muted">${when(c.updated)}</span></span></div>`).join("")
      : '<p class="muted" style="padding:12px">No change sets yet.</p>'}</div></div>`;
  view.querySelectorAll(".list-item").forEach((el) => el.onclick = () => location.hash = `changes/${el.dataset.id}`);
}

function changedView(op) {
  if (op.op === "delete") return `<pre>${esc(JSON.stringify(op.before, null, 2))}</pre>`;
  if (op.op === "create") return `<pre>${esc(JSON.stringify(op.after, null, 2))}</pre>`;
  return Object.keys(op.patch).map((k) => `<div><b>${esc(k)}</b></div><div class="diff">
      <div><div class="muted">Before</div><pre>${esc(JSON.stringify(op.before[k], null, 2) ?? "(not set)")}</pre></div>
      <div><div class="muted">After</div><pre>${esc(JSON.stringify(op.after[k], null, 2))}</pre></div></div>`).join("");
}

async function changeDetail(id) {
  const cs = await api(`/api/changesets/${id}`);
  const w = STATUS.writes_enabled;
  const btn = (action, label, cls = "") => `<button class="${cls}" data-action="${action}" ${w ? "" : "disabled"}>${label}</button>`;
  const actions = {
    proposed: btn("apply", "Apply to new workspace", "primary") + `<button data-action="discard">Discard</button>`,
    applied: btn("publish", "Publish", "primary") + btn("discard", "Discard workspace"),
    failed: `<button data-action="discard">Discard</button>`,
    published: btn("rollback", "Roll back", "danger"),
  }[cs.status] || "";
  view.innerHTML = `<div class="page">
    <p><a href="#changes">&larr; Changes</a></p>
    <h1>Change set #${cs.id}</h1>
    ${w ? "" : '<div class="banner">GTM writes are switched off on the server (ALLOW_GTM_WRITES). You can review, but not apply or publish.</div>'}
    <div class="card">
      <p style="margin-top:0">${esc(cs.summary)}</p>
      <p><span class="pill">${esc(cs.status)}</span> ${esc(STATUS_TEXT[cs.status] || "")}</p>
      <p class="muted">Container ${esc(cs.container_path)} &middot; based on audit #${cs.audit_id} of version ${esc(cs.version_id)}
      ${cs.workspace_url ? ` &middot; <a href="${esc(cs.workspace_url)}" target="_blank">Open workspace in GTM</a>` : ""}</p>
      <div class="row">${actions}</div>
      <p class="muted" id="actionStatus"></p>
    </div>
    ${cs.errors?.length ? `<div class="banner">Skipped when this change set was built:<ul>${cs.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : ""}
    <h3>${cs.ops.length} operation${cs.ops.length === 1 ? "" : "s"}</h3>
    ${cs.ops.map((o) => `<div class="op"><header><span class="pill">${esc(o.op)}</span> <b>${esc(o.kind)}: ${esc(o.name)}</b> <span class="muted">${esc(o.reason)}</span></header>
      <div class="body">${changedView(o)}</div></div>`).join("")}
    ${cs.log?.length ? `<div class="card"><b>Log</b><ul class="log">${cs.log.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></div>` : ""}
  </div>`;
  view.querySelectorAll("[data-action]").forEach((b) => b.onclick = async () => {
    const action = b.dataset.action;
    let body = {};
    if (action === "publish") {
      const typed = prompt("This makes the changes live on your site. Type PUBLISH to confirm.");
      if (typed !== "PUBLISH") return;
      body = { confirm: "PUBLISH" };
    }
    if (action === "rollback") {
      const typed = prompt("This re-publishes the version that was live before this change set. Type ROLLBACK to confirm.");
      if (typed !== "ROLLBACK") return;
      body = { confirm: "ROLLBACK" };
    }
    if (action === "apply" && !confirm("Create a new GTM workspace and apply these changes there? Nothing goes live until you publish.")) return;
    view.querySelectorAll("[data-action]").forEach((x) => x.disabled = true);
    document.getElementById("actionStatus").textContent = "Working... (GTM API calls are rate-limited, this can take a minute)";
    try { await post(`/api/changesets/${id}/${action}`, body); }
    catch (e) { alert(e.message); }
    changeDetail(id);
  });
}

// ---------------------------------------------------------------- dashboards & reports
async function dashboardsPage(id) {
  if (!id) {
    const list = await api("/api/dashboards");
    view.innerHTML = `<div class="page"><h1>Dashboards</h1>
      <p class="muted">Ask the assistant to build one, e.g. "Build a dashboard with daily users, sessions by channel and top landing pages for the last 28 days".</p>
      <div class="card" style="padding:0">${list.length ? list.map((d) => `<div class="list-item" data-id="${d.id}"><span><b>${esc(d.name)}</b> <span class="muted">${esc(d.description)}</span></span><span class="muted">${esc(d.property)}</span></div>`).join("")
        : '<p class="muted" style="padding:12px">No dashboards yet.</p>'}</div></div>`;
    view.querySelectorAll(".list-item").forEach((el) => el.onclick = () => location.hash = `dashboards/${el.dataset.id}`);
    return;
  }
  view.innerHTML = `<div class="page"><p><a href="#dashboards">&larr; Dashboards</a></p><p class="muted">Loading live data...</p></div>`;
  const d = await api(`/api/dashboards/${id}/data`);
  view.innerHTML = `<div class="page"><p><a href="#dashboards">&larr; Dashboards</a></p>
    <div class="row" style="justify-content:space-between"><h1>${esc(d.name)}</h1><span class="row"><button id="refresh">Refresh</button><button class="danger" id="del">Delete</button></span></div>
    <p class="muted">${esc(d.description)} &middot; ${esc(d.property)}</p><div class="grid" id="widgets"></div></div>`;
  const grid = document.getElementById("widgets");
  for (const w of d.data) {
    if (w.error) { grid.insertAdjacentHTML("beforeend", `<div class="chart-card"><h4>${esc(w.title)}</h4><p class="sev-high">${esc(w.error)}</p></div>`); continue; }
    const cell = document.createElement("div"); grid.appendChild(cell); renderChart(cell, w.spec);
    if (w.warnings?.length) cell.insertAdjacentHTML("beforeend", `<p class="muted">${esc(w.warnings.join(" "))}</p>`);
  }
  document.getElementById("refresh").onclick = () => dashboardsPage(id);
  document.getElementById("del").onclick = async () => { if (confirm("Delete this dashboard?")) { await api(`/api/dashboards/${id}`, { method: "DELETE" }); location.hash = "dashboards"; } };
}

async function reportsPage() {
  const list = await api("/api/reports");
  view.innerHTML = `<div class="page"><h1>Reports</h1>
    <p class="muted">Ask the assistant for a report, e.g. "Write a monthly performance report for October with recommendations".</p>
    <div class="card" style="padding:0">${list.length ? list.map((r) => `<a class="list-item" href="/reports/${r.id}" target="_blank" style="text-decoration:none;color:inherit"><b>${esc(r.title)}</b><span class="muted">${when(r.created)}</span></a>`).join("")
      : '<p class="muted" style="padding:12px">No reports yet.</p>'}</div></div>`;
}

const SIGNIN_MESSAGES = {
  connected: "Google account connected.",
  cancelled: "Google sign-in was cancelled.",
  failed: "Google sign-in failed. Check that every permission box was ticked, then try again.",
  expired: "The sign-in link expired. Click Connect Google account again.",
};

async function settingsPage(result) {
  await loadStatus();
  const s = STATUS;
  let google = esc(s.google);
  if (s.google_uses_service_account) {
    google += '<div class="muted">Using the service-account key. Remove GOOGLE_SERVICE_ACCOUNT_JSON to use your own Google login instead.</div>';
  } else if (s.google_signin_available) {
    google += `<div class="row" style="margin-top:8px">
      <a href="/oauth/google/start"><button class="primary" type="button">${s.google_signed_in ? "Reconnect" : "Connect"} Google account</button></a>
      ${s.google_signed_in ? '<button type="button" id="disconnect">Disconnect</button>' : ""}</div>
      <div class="muted" style="margin-top:6px">Sign in with the Google account that has access to your GA4 properties and GTM containers.</div>`;
  } else {
    google += `<div class="muted" style="margin-top:6px">To sign in with your Google account, set GOOGLE_OAUTH_CLIENT_ID and
      GOOGLE_OAUTH_CLIENT_SECRET. When creating the OAuth client, add this authorized redirect URI:</div>
      <pre>${esc(s.google_redirect_uri)}</pre>`;
  }
  view.innerHTML = `<div class="page"><h1>Connection</h1>
    ${SIGNIN_MESSAGES[result] ? `<div class="banner">${esc(SIGNIN_MESSAGES[result])}</div>` : ""}
    <div class="card"><table>
      <tr><th>Claude</th><td>${s.claude_configured ? "Configured" : "Not configured: set ANTHROPIC_API_KEY on the server"} (model ${esc(s.model)})</td></tr>
      <tr><th>Google</th><td>${google}</td></tr>
      <tr><th>GTM writes</th><td>${s.writes_enabled ? "Enabled: change sets can be applied and published after your approval" : "Disabled (read-only). Set ALLOW_GTM_WRITES=true to enable apply/publish."}</td></tr>
      <tr><th>Post-publish site check</th><td>${s.site_url ? esc(s.site_url) : "Off (set SITE_URL)"}</td></tr>
      <tr><th>Password</th><td>${s.password_protected ? "On" : "Off: only reachable from this machine"}</td></tr>
    </table></div>
    <form method="post" action="/logout"><button>Log out</button></form></div>`;
  const dc = document.getElementById("disconnect");
  if (dc) dc.onclick = async () => {
    if (!confirm("Disconnect your Google account from this app?")) return;
    await post("/api/google/disconnect");
    location.hash = "settings";
  };
}

loadStatus().then(route);
