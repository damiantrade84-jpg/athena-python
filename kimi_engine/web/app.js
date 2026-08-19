/* KIMI Engine dashboard client — SSE-driven state + candle chart. */
"use strict";

/* Base prefix: "" when served standalone at "/", "/kimi" when embedded in Athena. */
const BASE = window.KIMI_BASE || "";

let state = null;
let selSymbol = null;
let selTf = "15m";
let chart = null, cSeries = null, vSeries = null, eSeries = null, vChartSymbol = null;

const $ = (id) => document.getElementById(id);
const fmt = (n, d = 2) => n === null || n === undefined ? "—" :
  Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPx = (p) => p >= 1000 ? fmt(p, 1) : p >= 10 ? fmt(p, 2) : fmt(p, 4);
/* display name: BTCUSDT → BTC/USDT, everything else (EURUSD, XAUUSD, US30) as-is */
const dispSym = (s) => s.endsWith("USDT") ?
  `${s.slice(0, -4)}<span class="dim">/USDT</span>` : s;

/* ---------------- state stream ---------------- */
function connect() {
  if (window.EventSource) {
    const es = new EventSource(`${BASE}/api/stream`);
    es.onmessage = (m) => { state = JSON.parse(m.data); render(); };
    es.onerror = () => { es.close(); setTimeout(connect, 3000); };
  } else {
    const poll = () => fetch(`${BASE}/api/state`).then(r => r.json())
      .then(s => { state = s; render(); }).catch(() => {})
      .finally(() => setTimeout(poll, 3000));
    poll();
  }
}

/* ---------------- chart ---------------- */
function initChart() {
  chart = LightweightCharts.createChart($("chart"), {
    layout: { background: { color: "#0a0e14" }, textColor: "#6b7794" },
    grid: { vertLines: { color: "#121826" }, horzLines: { color: "#121826" } },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#1e2740" },
    rightPriceScale: { borderColor: "#1e2740" },
    crosshair: { mode: 0 },
  });
  cSeries = chart.addCandlestickSeries({
    upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
    wickUpColor: "#26a69a", wickDownColor: "#ef5350",
  });
  vSeries = chart.addLineSeries({ color: "#e5c07b", lineWidth: 1, priceLineVisible: false });
  eSeries = chart.addLineSeries({ color: "#4a5568", lineWidth: 1, lineStyle: 2, priceLineVisible: false });
  window.addEventListener("resize", () => chart.applyOptions({ width: $("chart").clientWidth, height: $("chart").clientHeight }));
}

async function loadChart() {
  if (!selSymbol) return;
  const r = await fetch(`${BASE}/api/chart?symbol=${selSymbol}&tf=${selTf}`);
  const d = await r.json();
  if (d.error) { $("chart-meta").textContent = d.error; return; }
  cSeries.setData(d.candles.map(k => ({ time: k.t, open: k.o, high: k.h, low: k.l, close: k.c })));
  vSeries.setData(d.vwap.map(p => ({ time: p.t, value: p.v })));
  eSeries.setData(d.ema21.map(p => ({ time: p.t, value: p.v })));
  const markers = [];
  for (const e of d.events) {
    if (e.kind === "sweep") markers.push({
      time: e.t, position: e.dir > 0 ? "belowBar" : "aboveBar",
      color: e.dir > 0 ? "#26a69a" : "#ef5350", shape: e.dir > 0 ? "arrowUp" : "arrowDown",
      text: "SWP",
    });
  }
  for (const s of d.signals) {
    markers.push({
      time: Math.floor(s.createdAt), position: s.direction === "LONG" ? "belowBar" : "aboveBar",
      color: "#e5c07b", shape: "circle", text: `${s.grade} ${s.score}`,
    });
  }
  markers.sort((a, b) => a.time - b.time);
  cSeries.setMarkers(markers);
  $("chart-symbol").textContent = d.symbol + " · " + d.tf;
  $("chart-meta").textContent = `src:${d.source} bars:${d.candles.length}`;
  if (vChartSymbol !== selSymbol + selTf) { chart.timeScale().fitContent(); vChartSymbol = selSymbol + selTf; }
}

/* ---------------- render ---------------- */
function gradeClass(g) { return g === "A+" ? "ap" : g === "A" ? "a" : "b"; }

function bestScore(d) {
  const L = Number(d && d.cards && d.cards.long && d.cards.long.total) || 0;
  const S = Number(d && d.cards && d.cards.short && d.cards.short.total) || 0;
  return Math.max(L, S);
}

function renderWatchlist() {
  const el = $("watchlist");
  const min = Number(state.minScore) || 0;
  const all = Object.entries(state.symbols || {});
  const syms = all
    .filter(([, d]) => bestScore(d) >= min)
    .sort((a, b) => bestScore(b[1]) - bestScore(a[1]));
  const wc = $("watch-count");
  if (wc) wc.textContent = all.length ? `${syms.length}/${all.length} ≥ ${min}` : "";
  el.innerHTML = syms.map(([sym, d]) => {
    const tk = d.ticker || {};
    const chg = tk.changePct24h;
    const chgHtml = chg === null || chg === undefined ?
      `<span class="dim mono">—</span>` :
      `<span class="chg mono ${chg >= 0 ? "pos" : "neg"}">${chg >= 0 ? "+" : ""}${fmt(chg, 1)}%</span>`;
    const L = d.cards?.long || { total: 0, grade: "—" };
    const S = d.cards?.short || { total: 0, grade: "—" };
    return `<div class="sym ${sym === selSymbol ? "sel" : ""}" data-sym="${sym}">
      <div class="row1"><span class="name">${dispSym(sym)}${d.fresh === false ? '<span class="dim"> ·stale</span>' : ""}</span>
        <span class="mono ${d.priceFresh === false ? "px-stale" : ""}" title="${d.priceSource || "?"}${d.priceAgeSec != null ? " · quote " + d.priceAgeSec + "s old" : " · no quote timestamp"}">${fmtPx(d.price || 0)}${d.priceFresh === false ? " ⚠" : ""}</span>
        ${chgHtml}</div>
      <div class="row2">
        <div class="scorebar"><div class="lbl"><span>L</span><span class="grade ${gradeClass(L.grade)}">${L.grade} ${fmt(L.total, 0)}</span></div>
          <div class="bar long"><div style="width:${L.total}%"></div></div></div>
        <div class="scorebar"><div class="lbl"><span>S</span><span class="grade ${gradeClass(S.grade)}">${S.grade} ${fmt(S.total, 0)}</span></div>
          <div class="bar short"><div style="width:${S.total}%"></div></div></div>
      </div></div>`;
  }).join("") || `<div class="dim" style="padding:10px">${all.length ? "no pairs at min score " + min : "scanning…"}</div>`;
  el.querySelectorAll(".sym").forEach(n => n.onclick = () => {
    selSymbol = n.dataset.sym; renderWatchlist(); loadChart();
  });
}

function renderSignals() {
  const el = $("signals");
  const min = Number(state.minScore) || 0;
  const sigs = (state.signals || []).filter(s => Number(s.score) >= min);
  el.innerHTML = sigs.map(s => {
    const ttl = Math.max(0, Math.round((s.validUntil - Date.now() / 1000) / 60));
    const comps = Object.entries(s.components).map(([k, v]) => {
      const max = { tide: 20, pulse: 15, structure: 18, flow: 12, pressure: 10, vwap: 8, session: 9, vol: 8 }[k] || 10;
      return `<div class="comp"><div class="cl"><span>${k}</span><span>${v}</span></div>
        <div class="bar"><div style="width:${Math.round(v / max * 100)}%"></div></div></div>`;
    }).join("");
    const liveBtn = (state.broker && state.broker.armed) ?
      `<button class="btn exec" data-exec="${s.id}" data-venue="bybit">EXECUTE DEMO</button>` : "";
    const he = state.hostExec || {};
    const hostBtn = he.available ?
      `<button class="btn exec" data-exec="${s.id}" data-venue="host" title="Athena pipeline: risk engine + guardian + managed execution">EXECUTE ${s.symbol.endsWith("USDT") ? "BYBIT" : "MT5"}${he.demoOnly ? " DEMO" : ""}</button>` : "";
    return `<div class="sig">
      <div class="top">
        <span class="dir-${s.direction.toLowerCase()}">${s.direction}</span>
        <b>${s.symbol}</b>
        <span class="grade ${gradeClass(s.grade)}">${s.grade} ${s.score}</span>
        <span class="lvl mono">entry ${fmtPx(s.entry)} · SL ${fmtPx(s.sl)} · TP1 ${fmtPx(s.tp1)} · TP2 ${fmtPx(s.tp2)}</span>
        <span class="ttl">⏱ ${ttl}m</span>
        ${hostBtn}
        <button class="btn exec" data-exec="${s.id}" data-venue="paper">EXECUTE PAPER</button>
        ${liveBtn}
      </div>
      <div class="comps">${comps}</div>
      <div class="reasons">${(s.reasons || []).join(" · ")}</div>
    </div>`;
  }).join("") || `<div class="dim" style="padding:6px 12px">no active signals — engine scanning</div>`;
  el.querySelectorAll("[data-exec]").forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = "…";
    const r = await fetch(`${BASE}/api/execute`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: b.dataset.exec, venue: b.dataset.venue }) }).then(x => x.json());
    if (!r.ok) { b.textContent = "✕ " + (r.error || "failed"); setTimeout(render, 2500); }
    refresh();
  });
}

function renderPositions() {
  const el = $("positions");
  const ps = state.account?.positions || [];
  el.innerHTML = ps.map(p => {
    const pnl = (p.unrealized ?? 0) + (p.realized ?? 0);
    return `<div class="pos">
      <div class="r1"><b>${p.symbol}</b>
        <span class="dir-${p.direction.toLowerCase()}">${p.direction}</span>
        ${p.broker === "bybit" ? '<span class="btag demo">DEMO</span>' : ""}
        <span class="pnl mono ${pnl >= 0 ? "pos" : "neg"}">${pnl >= 0 ? "+" : ""}${fmt(pnl)}$</span></div>
      <div class="dim mono">qty ${fmt(p.qty, 5)} @ ${fmtPx(p.entry)} → ${fmtPx(p.mark || 0)}</div>
      <div class="dim mono">SL ${fmtPx(p.sl)} TP2 ${fmtPx(p.tp2)}</div>
      <div class="r1"><span class="dim">score ${p.score}</span>
        <button class="btn" data-close="${p.id}">CLOSE</button></div>
    </div>`;
  }).join("") || `<div class="dim" style="padding:6px 12px">flat</div>`;
  el.querySelectorAll("[data-close]").forEach(b => b.onclick = async () => {
    await fetch(`${BASE}/api/close`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: b.dataset.close }) });
    refresh();
  });
}

function renderTrades() {
  const el = $("trades");
  el.innerHTML = (state.account?.trades || []).slice(0, 20).map(t =>
    `<div class="trd"><div class="r1"><b>${t.symbol}</b>
      <span class="dir-${t.direction.toLowerCase()}">${t.direction}</span>
      <span class="pnl mono ${t.pnl >= 0 ? "pos" : "neg"}">${t.pnl >= 0 ? "+" : ""}${fmt(t.pnl)}$</span></div>
      <div class="dim mono">${fmtPx(t.entry)} → ${fmtPx(t.exit)} · ${t.reason} · ${new Date(t.closedAt * 1000).toISOString().slice(11, 19)}Z</div>
    </div>`).join("") || `<div class="dim" style="padding:6px 12px">no trades yet</div>`;
}

function renderEvents() {
  const el = $("events");
  el.innerHTML = (state.events || []).slice(0, 12).map(e =>
    `<div class="evt"><b>${e.type}</b> ${e.symbol || ""} ${e.side || e.direction || ""}
      <span class="mono">${e.pnl !== undefined ? (e.pnl >= 0 ? "+" : "") + fmt(e.pnl) + "$" : e.qty !== undefined ? fmt(e.qty, 5) : ""}</span>
      <div class="dim mono">${e.t || e.closedAt ? new Date((e.t || e.closedAt) * 1000).toISOString().slice(11, 19) + "Z" : ""}</div></div>`
  ).join("") || `<div class="dim" style="padding:6px 12px">—</div>`;
}

function drawSpark() {
  const cv = $("spark");
  const curve = state.account?.equityCurve || [];
  if (!curve.length) return;
  const ctx = cv.getContext("2d");
  const w = cv.width = cv.clientWidth, h = cv.height;
  ctx.clearRect(0, 0, w, h);
  const vals = curve.map(p => p.equity);
  const lo = Math.min(...vals), hi = Math.max(...vals), rg = hi - lo || 1;
  ctx.beginPath();
  vals.forEach((v, i) => {
    const x = i / (vals.length - 1 || 1) * w, y = h - 3 - (v - lo) / rg * (h - 6);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = vals[vals.length - 1] >= vals[0] ? "#26a69a" : "#ef5350";
  ctx.lineWidth = 1.5; ctx.stroke();
}

function render() {
  if (!state) return;
  const a = state.account || {};

  // Real broker equity, per venue. MT5 is in the account currency and Bybit
  // in USDT, so they are never combined into one figure — and the paper
  // ledger gets its own tile rather than sitting under a bare "EQUITY" label
  // that reads as the real balance.
  const acc = state.accounts || {};
  const mt5 = acc.mt5, byb = acc.bybit;
  const envNote = (x) => x.environment === "demo" ? "DEMO"
    : x.environment === "real" ? "REAL" : "ENV UNKNOWN";

  if (mt5 && mt5.available) {
    $("k-equity-label").textContent = (mt5.currency || "MT5") + " EQUITY";
    $("k-equity").textContent = fmt(mt5.equity ?? 0);
    const sub = $("k-equity-sub");
    sub.textContent = `${envNote(mt5)} · ${mt5.login ?? "?"} · ${mt5.server ?? ""}`;
    sub.className = "k-sub mono " + (mt5.environment === "demo" ? "dim" : "warn");
  } else {
    $("k-equity-label").textContent = "MT5 EQUITY";
    $("k-equity").textContent = "—";
    $("k-equity-sub").textContent = (mt5 && mt5.error) || acc.reason || "not connected";
    $("k-equity-sub").className = "k-sub mono dim";
  }

  if (byb && byb.available) {
    $("k-bybit").textContent = fmt(byb.equity ?? 0);
    const sub = $("k-bybit-sub");
    sub.textContent = `${envNote(byb)} · USDT${byb.testnet ? " · TESTNET" : ""}`;
    sub.className = "k-sub mono " + (byb.environment === "demo" ? "dim" : "warn");
  } else {
    $("k-bybit").textContent = "—";
    $("k-bybit-sub").textContent = (byb && byb.error) || acc.reason || "not connected";
    $("k-bybit-sub").className = "k-sub mono dim";
  }

  $("k-paper").textContent = "$" + fmt(a.equity ?? 0);
  const up = a.unrealized ?? 0;
  $("k-upnl").innerHTML = `<span class="${up >= 0 ? "pnl pos" : "pnl neg"}">${up >= 0 ? "+" : ""}${fmt(up)}</span>`;
  $("k-dloss").textContent = fmt(state.dailyLossPct ?? 0) + "%";
  $("k-open").textContent = (a.positions || []).length;
  const min = Number(state.minScore) || 0;
  $("k-sigs").textContent = (state.signals || []).filter(s => Number(s.score) >= min).length;
  const ms = $("min-score");
  if (ms && document.activeElement !== ms && state.minScore != null) ms.value = state.minScore;
  $("clock").textContent = new Date().toISOString().slice(11, 19) + " UTC";
  const pill = $("scan-pill");
  const age = Date.now() / 1000 - (state.lastScan || 0);
  pill.textContent = state.killSwitch ? "KILLED" : age < 15 ? "LIVE" : "STALE";
  pill.className = "pill " + (state.killSwitch || age >= 15 ? "dim" : "ok");
  const kill = $("kill");
  kill.classList.toggle("on", !!state.killSwitch);
  kill.textContent = state.killSwitch ? "KILLED — RESUME" : "KILL SWITCH";
  const chip = $("broker-chip");
  const br = state.broker || {};
  if (br.armed) {
    chip.textContent = `BYBIT DEMO · $${fmt(br.equity ?? 0, 0)}`;
    chip.className = "pill armed"; chip.title = "demo account connected";
  } else {
    chip.textContent = "PAPER ONLY";
    chip.className = "pill dim"; chip.title = br.error || "broker not armed";
  }
  const sn = $("scan-now");
  sn.classList.toggle("busy", !!state.scanning);
  sn.textContent = state.scanning ? "⟳ …" : "⟳ SCAN";
  $("feed-status").textContent = `feeds: binance · mt5 · yahoo · scan #${state.scanCount} · broker ${state.brokerMode}${state.liveAvailable ? " (live armed)" : ""}`;
  $("errors").textContent = (state.errors || [])[0] || "";
  if (!selSymbol && state.symbols) {
    const first = Object.keys(state.symbols)[0];
    if (first) { selSymbol = first; loadChart(); }
  }
  renderWatchlist(); renderSignals(); renderPositions(); renderTrades(); renderEvents(); drawSpark();
}

/* ---------------- controls ---------------- */
$("kill").onclick = async () => {
  await fetch(`${BASE}/api/kill`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ on: !(state && state.killSwitch) }) });
  refresh();
};
$("min-score").onchange = async (e) => {
  await fetch(`${BASE}/api/config`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ minScore: Number(e.target.value) }) });
  refresh();
};
$("auto-trade").onchange = async (e) => {
  await fetch(`${BASE}/api/config`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ autoTrade: e.target.checked }) });
};
document.querySelectorAll(".tfbtn").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tfbtn").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); selTf = b.dataset.tf; loadChart();
});

/* ---------------- scan now + pair selection ---------------- */
$("scan-now").onclick = async () => {
  await fetch(`${BASE}/api/scan`, { method: "POST" });
  setTimeout(refresh, 2500);
};

let pairsCache = null;
// Selection lives here, not in the DOM. Reading it back off the checkboxes
// meant anything ticked was silently discarded the moment the filter box
// re-rendered the list, and it made "select all" impossible to express.
let pairsSel = new Set();

function pairsBook() {
  // Athena's current active book, plus anything already in this KIMI scan.
  // The exchange catalog is NOT part of the book — that is the 512-symbol dump.
  return [...new Set([
    ...(pairsCache.portfolio || []),
    ...(pairsCache.active || []),
  ])];
}

function pairsShown() {
  const f = $("pairs-filter").value.trim().toUpperCase();
  const book = pairsBook();
  if (!f) return book;
  const fromBook = book.filter(s => s.includes(f));
  const fromAvail = (pairsCache.available || []).filter(s => s.includes(f));
  return [...new Set([...fromBook, ...fromAvail])];
}

function renderPairsList() {
  if (!pairsCache) return;
  const shown = pairsShown();
  const book = pairsBook();
  const max = pairsCache.maxSymbols || 200;
  const over = pairsSel.size > max;
  $("pairs-count").innerHTML = `${pairsSel.size} selected · ${book.length} active book · max ${max}` +
    (over ? ` <b style="color:var(--short)">over limit</b>` : "");
  $("pairs-save").disabled = over || pairsSel.size === 0;
  $("pairs-list").innerHTML = shown.map(s =>
    `<label><input type="checkbox" data-sym="${s}" ${pairsSel.has(s) ? "checked" : ""}>
       ${dispSym(s)}</label>`).join("") ||
    `<div class="dim">no active book — type to search the exchange catalog</div>`;
}

$("pairs-list").onchange = (e) => {
  const box = e.target.closest("input[data-sym]");
  if (!box) return;
  if (box.checked) pairsSel.add(box.dataset.sym);
  else pairsSel.delete(box.dataset.sym);
  renderPairsList();
};

$("pairs-open").onclick = async () => {
  $("pairs-modal").classList.remove("hidden");
  $("pairs-list").innerHTML = `<div class="dim">loading exchange symbols…</div>`;
  pairsCache = await fetch(`${BASE}/api/symbols`).then(r => r.json());
  pairsSel = new Set(pairsCache.active || []);
  renderPairsList();
};
$("pairs-filter").oninput = renderPairsList;

$("pairs-all-portfolio").onclick = () => {
  const portfolio = pairsCache && pairsCache.portfolio || [];
  if (!portfolio.length) {
    alert("No active book available — Athena has not published its enabled pair list.");
    return;
  }
  // Replace rather than merge: ALL ACTIVE is the current book, not extras.
  pairsSel = new Set(portfolio);
  $("pairs-filter").value = "";
  renderPairsList();
};
$("pairs-all-shown").onclick = () => {
  const shown = pairsShown();
  if (!shown.length) {
    alert("Nothing listed to select. Open the picker from Athena so the active book is published.");
    return;
  }
  // Replace, do not union the exchange catalog into an existing selection.
  pairsSel = new Set(shown);
  renderPairsList();
};
$("pairs-clear").onclick = () => { pairsSel = new Set(); renderPairsList(); };

$("pairs-add").onclick = () => {
  const v = $("pairs-custom").value.trim().toUpperCase();
  if (!v || !pairsCache) return;
  if (!pairsCache.available.includes(v)) pairsCache.available.unshift(v);
  pairsSel.add(v);
  $("pairs-custom").value = "";
  renderPairsList();
};
$("pairs-cancel").onclick = () => $("pairs-modal").classList.add("hidden");
$("pairs-save").onclick = async () => {
  const sel = [...pairsSel];
  if (!sel.length) { alert("Select at least one symbol."); return; }
  const r = await fetch(`${BASE}/api/symbols`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols: sel }),
  }).then(x => x.json());
  if (r.ok) { $("pairs-modal").classList.add("hidden"); refresh(); }
  else alert(r.error || "failed to save universe");
};

async function refresh() {
  try { state = await fetch(`${BASE}/api/state`).then(r => r.json()); render(); } catch (e) {}
}

initChart();
connect();
setInterval(loadChart, 20000);
