"use strict";

// Every part of the harness is its own box. Boxes can sit inside other boxes (a loop around what it
// repeats, an agent around its own tools). Arrows are the paths between parts. ELK does the layout.
const ORDER = ["problem", "model", "context", "loop", "tools", "guardrails", "tests", "agents", "goal", "memory", "retrieval", "output", "observability"];
const KIND = { problem: "task", model: "model", context: "task", loop: "model", tools: "tools", guardrails: "guard", tests: "check", agents: "agents", goal: "task", memory: "memory", retrieval: "memory", output: "task", observability: "task" };
const WORD = { problem: "Problem", model: "Model", context: "Context", loop: "Loop", tools: "Tool", guardrails: "Guardrail", tests: "Check", agents: "Agent", goal: "Limit", memory: "Memory", retrieval: "Retrieval", output: "Output", observability: "Log" };
const HEADER = { loop: "repeats until done", agents: "its own role" };
const ACT = { UserPromptSubmit: ["task", "You asked"], PreToolUse: ["guard", "{a} wants to use"], PostToolUse: ["tools", "{a} used"], Stop: ["check", "{a} finished its turn"], SubagentStop: ["agents", "A subagent finished"] };
const W = 196, H = 78, NOTE_CHARS = 28;

let S = null;            // latest analysis from the server
let L = null;            // latest layout from ELK
let selected = null;     // {type: "part"|"kind", id}
let view = null;         // current viewBox
let userView = false;    // the viewer zoomed or panned, so keep their view
let panned = false;      // the last pointer press was a drag, not a click
let layoutRun = 0;
let near = new Set();
const fresh = new Set();
const elk = new ELK();

const $ = (q) => document.querySelector(q);
const NS = "http://www.w3.org/2000/svg";
function s(tag, a = {}, ...kids) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(a)) if (v != null) n.setAttribute(k, v);
  kids.flat().forEach((c) => c != null && n.append(c.nodeType ? c : document.createTextNode(String(c))));
  return n;
}
function el(tag, a = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(a)) {
    if (k === "class") n.className = v; else if (k === "style") n.setAttribute("style", v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else if (v != null) n.setAttribute(k, v);
  }
  kids.flat().forEach((c) => c != null && n.append(c.nodeType ? c : document.createTextNode(String(c))));
  return n;
}
const kv = (k) => `--k: var(--${k}); --soft: var(--${k}-soft); --c: var(--${k});`;
const clip = (t, n) => (t && t.length > n ? t.slice(0, n - 1) + "…" : t || "");
const part = (id) => S && S.parts.find((p) => p.id === id);
const where = (p) => (p && p.file ? `${p.file}${p.line ? ":" + p.line : ""}${p.symbol ? "  " + p.symbol : ""}` : "");
function wrap(text, n, lines) {
  const out = []; let cur = "";
  for (const w of (text || "").split(/\s+/).filter(Boolean)) {
    if ((cur + " " + w).trim().length > n && cur) { out.push(cur); cur = w; } else cur = (cur + " " + w).trim();
  }
  if (cur) out.push(cur);
  if (out.length > lines) { out.length = lines; out[lines - 1] = clip(out[lines - 1] + " …", n); }
  return out.map((t) => clip(t, n));
}

/* ------------------------------------------------------------------ layout */
function graph() {
  const kids = {};
  S.parts.forEach((p) => (kids[p.in || ""] ||= []).push(p));
  const mk = (p) => {
    const ch = kids[p.id];
    if (!ch) return { id: p.id, width: W, height: H };
    return {
      id: p.id, children: ch.map(mk),
      layoutOptions: { "elk.padding": "[top=52,left=18,bottom=18,right=18]", "elk.nodeSize.constraints": "MINIMUM_SIZE", "elk.nodeSize.minimum": `(${Math.max(230, p.label.length * 9 + 60)},90)` },
    };
  };
  return {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.edgeRouting": "ORTHOGONAL", "elk.spacing.nodeNode": "26", "elk.layered.spacing.nodeNodeBetweenLayers": "64",
      "elk.spacing.edgeNode": "18", "elk.spacing.edgeEdge": "12", "elk.layered.spacing.edgeNodeBetweenLayers": "18",
      "elk.edgeLabels.placement": "CENTER", "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.padding": "[top=30,left=30,bottom=30,right=30]", "elk.json.edgeCoords": "ROOT", "elk.json.shapeCoords": "ROOT",
    },
    children: (kids[""] || []).map(mk),
    edges: S.links.map((l, i) => ({ id: "e" + i, sources: [l.from], targets: [l.to],
      labels: l.label ? [{ id: "l" + i, text: l.label, width: l.label.length * 6.7 + 10, height: 17 }] : [] })),
  };
}

async function relayout() {
  const run = ++layoutRun;
  try {
    const out = await elk.layout(graph());
    if (run !== layoutRun) return;
    L = out;
    if (!userView || !view) fit();
    render();
  } catch (err) {
    console.error(err);
    $("#hint").hidden = false;
    $("#hint").textContent = `The diagram could not be laid out: ${err.message || err}`;
  }
}

/* ------------------------------------------------------------------ view (zoom and pan) */
// Fit the whole harness, but never shrink text below readable size: past that, start at the left and let people pan.
const MIN_SCALE = 0.72;
function fit() {
  if (!L) { view = null; return; }
  const svg = $("#diagram"), cw = svg.clientWidth || 1000, ch = svg.clientHeight || 600;
  const k = Math.max(L.width / cw, L.height / ch, 1 / 1.4);
  const kk = Math.min(k, 1 / MIN_SCALE);
  const w = cw * kk, h = ch * kk;
  view = { x: k > kk ? 0 : (L.width - w) / 2, y: (L.height - h) / 2, w, h };
}
function applyView() { if (view) $("#diagram").setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`); }
function scale() { const svg = $("#diagram"); return Math.max(view.w / svg.clientWidth, view.h / svg.clientHeight); }
function zoom(f, cx, cy) {
  if (!view) return;
  cx ??= view.x + view.w / 2; cy ??= view.y + view.h / 2;
  const w = Math.min(Math.max(view.w * f, 240), (L ? L.width : 2000) * 3);
  const r = w / view.w;
  view = { x: cx - (cx - view.x) * r, y: cy - (cy - view.y) * r, w, h: view.h * r };
  userView = true; applyView();
}
function setupView() {
  const svg = $("#diagram");
  let drag = null;
  svg.addEventListener("pointerdown", (e) => {
    panned = false;
    if (!view || e.target.closest(".node, .group")) return;
    drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    svg.setPointerCapture(e.pointerId); svg.classList.add("panning");
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 4) panned = true;
    const k = scale();
    view.x = drag.vx - (e.clientX - drag.x) * k; view.y = drag.vy - (e.clientY - drag.y) * k;
    userView = true; applyView();
  });
  const end = () => { drag = null; svg.classList.remove("panning"); };
  svg.addEventListener("pointerup", end); svg.addEventListener("pointercancel", end);
  svg.addEventListener("wheel", (e) => {
    if (!view) return;
    e.preventDefault();
    const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    zoom(e.deltaY > 0 ? 1.12 : 1 / 1.12, p.x, p.y);
  }, { passive: false });
  svg.addEventListener("click", (e) => { if (!panned && !e.target.closest(".node, .group") && selected) pick(null); });
  $("#zin").addEventListener("click", () => zoom(1 / 1.25));
  $("#zout").addEventListener("click", () => zoom(1.25));
  $("#zfit").addEventListener("click", () => { userView = false; fit(); applyView(); });
}

/* ------------------------------------------------------------------ drawing */
function flatten(n, depth = 0, out = []) {
  for (const c of n.children || []) { out.push({ ...c, depth, box: !!(c.children && c.children.length) }); flatten(c, depth + 1, out); }
  return out;
}
function pick(sel) { selected = sel; render(); }
function clickable(g, sel, label) {
  g.setAttribute("tabindex", 0); g.setAttribute("role", "button"); g.setAttribute("aria-label", label);
  g.addEventListener("click", (e) => { e.stopPropagation(); pick(sel); });
  g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(sel); } });
}

function drawGroup(n, p) {
  const k = KIND[p.feature];
  const cls = ["group", p.source === "external" ? "external" : "", selected?.id === p.id ? "selected" : "", fresh.has(p.id) ? "new" : "", near.has(p.id) ? "near" : ""].join(" ");
  const g = s("g", { class: cls, style: kv(k), transform: `translate(${n.x},${n.y})` });
  g.append(s("rect", { class: "frame", width: n.width, height: n.height, rx: 12 }));
  g.append(s("path", { class: "headbar", d: `M0,12 a12,12 0 0 1 12,-12 h${n.width - 24} a12,12 0 0 1 12,12 v28 h-${n.width} z` }));
  if (p.feature === "loop") g.append(s("path", { class: "loopicon", d: "M27,14 a7,7 0 1 1 -3,-5.6 M24.5,4 l-0.5,4.6 l4.6,0.6" }));
  const tx = p.feature === "loop" ? 40 : 16;
  g.append(s("text", { class: "gkind", x: tx, y: 16 }, WORD[p.feature].toUpperCase() + (HEADER[p.feature] ? " · " + HEADER[p.feature] : "") + (p.source === "external" ? " · from " + p.origin : "")),
    s("text", { class: "gtitle", x: tx, y: 33 }, clip(p.label, Math.floor((n.width - tx - 16) / 8.4))));
  clickable(g, { type: "part", id: p.id }, `${WORD[p.feature]}: ${p.label}`);
  return g;
}

function drawNode(n, p) {
  const k = KIND[p.feature];
  const cls = ["node", p.source === "code" ? "scanned" : "", p.source === "external" ? "external" : "", selected?.id === p.id ? "selected" : "", fresh.has(p.id) ? "new" : "", near.has(p.id) ? "near" : ""].join(" ");
  const g = s("g", { class: cls, style: kv(k), transform: `translate(${n.x},${n.y})` });
  const box = s("g", { class: "box" });
  box.append(s("rect", { class: "card", width: W, height: H, rx: p.feature === "model" ? 14 : 6 }));
  box.append(s("rect", { class: "band", width: 5, height: H - 16, x: 0, y: 8, rx: 2.5 }));
  g.append(box);
  g.append(s("text", { class: "kind", x: 16, y: 19 }, WORD[p.feature].toUpperCase()));
  if (p.source === "code") g.append(s("text", { class: "kind codetag", x: W - 12, y: 19, "text-anchor": "end" }, "FOUND IN CODE"));
  if (p.source === "external") g.append(s("text", { class: "kind exttag", x: W - 12, y: 19, "text-anchor": "end" }, clip(p.origin.toUpperCase(), 14)));
  g.append(s("text", { class: "title", x: 16, y: 39 }, clip(p.label, 22)));
  wrap(p.note, NOTE_CHARS, 2).forEach((line, i) => g.append(s("text", { class: "sub", x: 16, y: 57 + i * 15 }, line)));
  clickable(g, { type: "part", id: p.id }, `${WORD[p.feature]}: ${p.label}`);
  return g;
}

function render() {
  if (!S) return;
  renderTop();
  renderDetail();
  if (!L) return;
  const svg = $("#diagram");
  applyView();
  svg.replaceChildren(s("defs", {},
    ...["flow", "back", "fail", "data", "guard", "hot"].map((k) => s("marker", { id: "arrow-" + k, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" },
      s("path", { d: "M0,0 L10,5 L0,10 z", class: "tip " + k })))));
  const nodes = flatten(L);
  const sel = selected?.type === "part" ? selected.id : null;
  near = new Set();
  if (sel) S.links.forEach((l) => { if (l.from === sel) near.add(l.to); if (l.to === sel) near.add(l.from); });

  const groups = s("g", {}), edges = s("g", {}), labels = s("g", {}), leaves = s("g", {});
  nodes.filter((n) => n.box).sort((a, b) => a.depth - b.depth).forEach((n) => groups.append(drawGroup(n, part(n.id))));
  (L.edges || []).forEach((e, i) => {
    const l = S.links[i], sec = e.sections && e.sections[0];
    if (!l || !sec) return;
    const pts = [sec.startPoint, ...(sec.bendPoints || []), sec.endPoint];
    const hot = sel && (l.from === sel || l.to === sel);
    const state = sel ? (hot ? "hot" : "dim") : "";
    edges.append(s("path", { class: `edge ${l.kind} ${state}`, d: "M" + pts.map((p) => `${p.x},${p.y}`).join(" L"), "marker-end": `url(#arrow-${hot ? "hot" : l.kind})` }));
    const lb = e.labels && e.labels[0];
    if (lb && lb.x != null) labels.append(s("g", { class: `elabel ${l.kind} ${state}`, transform: `translate(${lb.x},${lb.y})` },
      s("rect", { width: lb.width, height: lb.height, rx: 4 }), s("text", { x: lb.width / 2, y: 12.5, "text-anchor": "middle" }, lb.text)));
  });
  nodes.filter((n) => !n.box).forEach((n) => leaves.append(drawNode(n, part(n.id))));
  svg.append(groups, edges, leaves, labels);
}

function renderTop() {
  const kinds = ORDER.filter((k) => S.features[k].on).length;
  $("#count").textContent = `${kinds} of 13 kinds`;
  $("#totals").textContent = `${S.parts.length} parts · ${S.links.length} paths`;
  $("#kinds").replaceChildren(...ORDER.map((k) => {
    const f = S.features[k];
    return el("button", { class: `kc ${f.on ? "on" : ""} ${selected?.type === "kind" && selected.id === k ? "selected" : ""}`, style: kv(KIND[k]), onclick: () => pick({ type: "kind", id: k }), title: f.meaning },
      el("i"), f.label, f.count ? el("b", {}, String(f.count)) : null);
  }));
  $("#name").textContent = S.name;
  $("#path").textContent = S.project.split("/").slice(-2).join("/");
  $("#path").title = S.project;
  document.title = `${S.name}: Harness Monitor`;
  const hint = $("#hint");
  const msg = S.map_error
    || (!S.has_map ? "Parts are found by scanning the code. Run /harnessmonitor in your coding agent so it also names each part as it builds." : "")
    || (S.links_source === "guessed" && S.links.length ? "Arrows show the usual paths for these kinds of parts. Your coding agent can list the real ones in .harness/map.json." : "");
  hint.hidden = !msg; hint.textContent = msg;
}

function linkRow(id, arrow, label) {
  const p = part(id);
  if (!p) return null;
  return el("button", { class: "lk", style: kv(KIND[p.feature]), onclick: () => pick({ type: "part", id }) },
    el("span", { class: "ar" }, arrow), el("span", { class: "nm" }, p.label), label ? el("em", {}, label) : null);
}

function renderDetail() {
  const box = $("#detail");
  let sel = selected;
  if (!sel || (sel.type === "part" && !part(sel.id))) {
    const last = S.parts.slice().sort((a, b) => (S.first_seen?.[b.id] || 0) - (S.first_seen?.[a.id] || 0))[0];
    sel = last ? { type: "part", id: last.id } : { type: "kind", id: "problem" };
  }
  if (sel.type === "kind") {
    const f = S.features[sel.id], mine = S.parts.filter((p) => p.feature === sel.id);
    box.setAttribute("style", kv(KIND[sel.id]));
    box.replaceChildren(el("div", { class: "k" }, f.on ? `${mine.length} in this harness` : "Not built yet"), el("h2", {}, f.label),
      el("p", { class: "meaning" }, f.meaning),
      ...mine.map((p) => linkRow(p.id, "•", "")),
      ...(!f.on ? [el("p", { class: "none" }, "Nothing in the project does this yet. When it gets built, it appears on the diagram.")] : []));
    return;
  }
  const p = part(sel.id), f = S.features[p.feature];
  const outs = S.links.filter((l) => l.from === p.id), ins = S.links.filter((l) => l.to === p.id);
  const inside = p.in && part(p.in), kids = S.parts.filter((c) => c.in === p.id);
  const sec = (title, rows) => (rows.length ? el("div", { class: "sec" }, el("h4", {}, title), ...rows) : null);
  box.setAttribute("style", kv(KIND[p.feature]));
  box.replaceChildren(...[
    el("div", { class: "k" }, `${WORD[p.feature]} · ${p.source === "code" ? "Found in code" : p.source === "external" ? "Comes from " + p.origin : "Named by the agent"}`),
    el("h2", {}, p.label),
    p.note ? el("p", { class: "meaning" }, p.note) : null,
    p.source === "external" ? el("p", { class: "none" }, `${p.origin} provides this part. This project starts or configures it; the code for it is not in this project.`) : null,
    where(p) ? el("div", { class: "where" }, where(p)) : null,
    sec("Sits inside", inside ? [linkRow(inside.id, "▢", WORD[inside.feature])] : []),
    sec(`Holds ${kids.length}`, kids.map((c) => linkRow(c.id, "▸", WORD[c.feature]))),
    sec("Sends to", outs.map((l) => linkRow(l.to, "→", l.label))),
    sec("Gets from", ins.map((l) => linkRow(l.from, "←", l.label))),
    ...(p.evidence || []).slice(0, 5).map((e) => el("div", { class: "item" }, el("div", { class: "where" }, `${e.file}:${e.line}`), el("pre", {}, e.text))),
    el("p", { class: "kindnote" }, el("b", {}, `${f.label}: `), f.meaning)].filter(Boolean));
}

/* ------------------------------------------------------------------ live updates */
function stamp(t) { return new Date((t || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
function feed(k, what, detail, t) {
  const box = $("#feed");
  box.querySelector(".empty")?.remove();
  box.prepend(el("div", { class: "ev", style: kv(k) }, el("span", { class: "t" }, stamp(t)),
    el("div", {}, el("b", {}, what), detail ? el("div", { class: "d" }, detail) : null)));
  while (box.children.length > 120) box.lastChild.remove();
}
let bannerTimer = 0;
function banner(p, extra) {
  const b = $("#banner");
  b.setAttribute("style", kv(KIND[p.feature]));
  b.replaceChildren(`New ${WORD[p.feature].toLowerCase()}: ${p.label}`, el("span", {}, extra ? `  ${extra}` : ""));
  b.hidden = false;
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => (b.hidden = true), 4500);
}

function onMap(ev) {
  if (!ev.state) return;
  const restart = !S || ev.reason === "Started watching";
  S = ev.state;
  fresh.clear();
  if (restart) { userView = false; selected = null; }
  if (ev.reason === "Started watching") $("#feed").replaceChildren(el("p", { class: "empty" }, "Waiting for your coding agent. Ask it to build something."));
  const added = ev.added || [];
  added.forEach((id) => fresh.add(id));
  if (added.length) {
    const p = part(added[added.length - 1]);
    if (p) { selected = { type: "part", id: p.id }; banner(p, where(p) ? `in ${where(p)}` : ""); }
    added.forEach((id) => { const q = part(id); if (q) feed(KIND[q.feature], `New ${WORD[q.feature].toLowerCase()}: ${q.label}`, where(q)); });
  }
  (ev.removed || []).forEach((r) => feed("guard", `Part gone: ${r.label || r}`, ""));
  relayout();
  setTimeout(() => { fresh.clear(); document.querySelectorAll(".new").forEach((n) => n.classList.remove("new")); }, 1800);
}

function onActivity(item) {
  if (item.kind === "error") return feed("guard", item.text, "");
  const [k, raw] = ACT[item.event] || ["task", item.event];
  const what = raw.replace("{a}", item.agent || "The agent");
  if (item.event === "PreToolUse") return; // PostToolUse says the same thing once it is done
  feed(k, item.tool ? `${what} ${item.tool}` : what, item.detail, item.t);
}

function connect() {
  const es = new EventSource("/api/events");
  es.onopen = () => { $("#conn").dataset.state = "on"; $("#conn").textContent = "Watching"; };
  es.onerror = () => { $("#conn").dataset.state = "off"; $("#conn").textContent = "Monitor not running"; };
  es.onmessage = (m) => {
    try {
      const ev = JSON.parse(m.data);
      if (ev.type === "map") onMap(ev);
      if (ev.type === "activity") onActivity(ev.item);
    } catch (err) { console.error(err); }
  };
}
window.addEventListener("resize", () => applyView());
setupView();
fetch("/api/state").then((r) => r.json()).then((d) => (d.activity || []).forEach(onActivity)).catch(() => {});
connect();
