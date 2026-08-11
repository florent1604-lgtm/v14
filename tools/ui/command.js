"use strict";

const $ = (selector) => document.querySelector(selector);
const text = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);
const number = (value, digits = 0) => Number(value || 0).toLocaleString("fr-FR", { maximumFractionDigits: digits });
const when = (value) => value ? new Date(value).toLocaleString("fr-FR") : "aucune activité";
const node = (tag, className, content) => {
  const item = document.createElement(tag); if (className) item.className = className;
  if (content !== undefined) item.textContent = content; return item;
};

let DATA = null;

function renderKpis(data) {
  const p = data.profitability || {}, loop = p.loop || {}, tests = data.tests || {}, account = data.account || {};
  $("#k-mode").textContent = data.meta?.mode || "DEMO_ONLY";
  $("#k-equity").textContent = account.connected ? `${number(account.equity, 2)} ${account.currency || ""}` : "déconnecté";
  $("#k-account").textContent = account.login ? `${account.login} · ${account.server || "MT5"}` : "MT5 indisponible";
  $("#k-trades").textContent = number(p.closed_trades);
  $("#k-contexts").textContent = `${number(p.positive_contexts)} contexte(s) porteur(s) / ${number(p.contexts)}`;
  $("#k-promo").textContent = `${number(p.promotable_cells)} / ${number(p.promotion_cells)}`;
  $("#k-tests").textContent = tests.total ? `${number(tests.total)} · ${number(tests.echecs)} échec` : "—";
  $("#k-test-time").textContent = tests.quand || "aucun relevé";
  $("#k-loop").textContent = loop.running ? (loop.armed ? "ARMÉE DÉMO" : "observation") : "arrêtée";
  $("#k-heartbeat").textContent = `${number(loop.tours)} tours · ${number(loop.sent)} ordre(s)`;
}

function renderGovernance(items) {
  const root = $("#governance"); root.replaceChildren();
  (items || []).forEach((agent) => {
    const card = node("article", `agent ${agent.status || "idle"}`);
    const header = node("header"); header.append(node("i", "dot"), node("strong", "", agent.name));
    card.append(header, node("p", "", agent.role), node("small", "", `${agent.status} · ${when(agent.last_activity)}`));
    root.append(card);
  });
}

function renderRuntime(runtime) {
  const root = $("#runtime"); root.replaceChildren();
  const rows = [];
  Object.entries(runtime?.core || {}).forEach(([name, status]) => rows.push([name, status.running, `${status.instances} instance(s)`]));
  rows.push(["CollabHub", runtime?.collab_hub?.running, "port 8770"], ["Hermes MCP", runtime?.hermes_mcp?.running, "port 8766"]);
  const proc = runtime?.processes || {};
  rows.push(["Prime Agent", Boolean(proc.prime_clients || proc.prime_daemons), `${proc.prime_clients || 0} client(s) · ${proc.prime_daemons || 0} démon(s)`]);
  rows.forEach(([label, ok, detail]) => {
    const row = node("div", "service"); row.append(node("span", "", label));
    const badge = node("span", `badge ${ok ? "ok" : "bad"}`, ok ? `actif · ${detail}` : `hors ligne · ${detail}`); row.append(badge); root.append(row);
  });
}

function renderFunnel(p) {
  const flow = p?.funnel?.flow || {}, loop = p?.loop || {};
  const stages = [["Catalogue", flow.catalogue], ["Sélectionnés", flow.selectionnes], ["Portables", flow.portables],
    ["Évalués", loop.evaluated], ["ENTER", loop.enter], ["Ordres démo", loop.sent]];
  const root = $("#funnel"); root.replaceChildren();
  stages.forEach(([label, value]) => { const stage = node("div", "stage"); stage.append(node("span", "", label), node("strong", "", number(value))); root.append(stage); });
  $("#funnel-note").textContent = `${number(loop.tours)} tours · ${number(p?.rejected_lines)} ligne(s) journal rejetée(s)`;
  const refus = $("#refus"); refus.replaceChildren();
  const codes = p?.funnel?.gate_code || {};
  Object.entries(codes).sort((a,b) => b[1]-a[1]).slice(0,8).forEach(([key, value]) => refus.append(node("span", "badge", `${key} · ${value}`)));
}

const COLUMNS = [["backlog","À faire"],["in_progress","En cours"],["review","Revue"],["done","Terminé"],["blocked","Bloqué"]];
const NEXT = { backlog: ["in_progress","Démarrer"], in_progress: ["review","Mettre en revue"], review: ["done","Terminer"], blocked: ["in_progress","Reprendre"] };

async function transition(id, status) {
  const response = await fetch("/api/tasks", { method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ action:"update", id, status, actor:"dashboard" }) });
  const result = await response.json(); if (!response.ok) throw new Error(result.error || "mise à jour refusée");
  DATA.tasks = result.snapshot; renderBoard(DATA.tasks);
}

function renderBoard(snapshot) {
  const root = $("#board"); root.replaceChildren(); const tasks = snapshot?.tasks || [];
  COLUMNS.forEach(([status,label]) => {
    const column = node("section", "column"); const title = node("h3");
    title.append(node("span", "", label), node("span", "", String(tasks.filter(t => t.status === status).length))); column.append(title);
    tasks.filter(t => t.status === status).forEach((task) => {
      const card = node("article", "task"); const meta = node("div", "meta");
      meta.append(node("span", "priority", task.priority), node("span", "owner", task.owner));
      card.append(meta, node("b", "", task.title)); if (task.description) card.append(node("p", "", task.description));
      if (NEXT[status]) { const actions = node("div", "actions"); const button = node("button", "", NEXT[status][1]);
        button.addEventListener("click", () => transition(task.id, NEXT[status][0]).catch(showError)); actions.append(button); card.append(actions); }
      column.append(card);
    });
    if (!tasks.some(t => t.status === status)) column.append(node("div", "empty", "aucune tâche")); root.append(column);
  });
}

function renderTimeline(selector, items, mapper) {
  const root = $(selector); root.replaceChildren();
  if (!items?.length) return root.append(node("div", "empty", "aucune preuve enregistrée"));
  items.forEach((item) => { const mapped = mapper(item); const event = node("article", "event");
    const time = node("time", "", when(mapped.at));
    event.append(time, node("strong", "", mapped.title), node("p", "", mapped.content)); root.append(event); });
}

function showError(error) { const box = $("#task-error"); box.textContent = String(error.message || error); box.hidden = false; }

async function load() {
  try {
    const response = await fetch("/api/command-center"); if (!response.ok) throw new Error(`serveur ${response.status}`);
    DATA = await response.json(); renderKpis(DATA); renderGovernance(DATA.governance); renderRuntime(DATA.runtime);
    renderFunnel(DATA.profitability); renderBoard(DATA.tasks);
    renderTimeline("#prime-reports", DATA.prime_reports, r => ({at:r.modified_at,title:`${r.run} / ${r.file}`,content:r.excerpt}));
    renderTimeline("#activity", DATA.activity, m => ({at:m.at,title:`${m.from} → ${m.to} · ${m.task || m.type}`,content:m.content}));
    $("#safety").textContent = `MODE DÉMO UNIQUEMENT · ${DATA.safety?.message || "Aucune exécution depuis cette page."}`;
    $("#generated").textContent = `État généré ${when(DATA.meta?.generated_at)}`;
  } catch (error) { $("#safety").textContent = `Command Center indisponible : ${error}`; $("#safety").classList.add("error"); }
}

$("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("#task-error").hidden = true;
  try {
    const response = await fetch("/api/tasks", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({
      action:"create", actor:"dashboard", title:$("#task-title").value, owner:$("#task-owner").value,
      priority:$("#task-priority").value, source:"command-center" }) });
    const result = await response.json(); if (!response.ok) throw new Error(result.error || "création refusée");
    $("#task-title").value = ""; DATA.tasks = result.snapshot; renderBoard(DATA.tasks);
  } catch (error) { showError(error); }
});
$("#refresh").addEventListener("click", load);
load(); setInterval(load, 15000);
