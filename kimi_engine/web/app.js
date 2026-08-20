/* KIMI Engine dashboard client — SSE-driven state + candle chart.
 *
 * Presentation layer only. Every endpoint, request payload and state field
 * read here is unchanged from the previous build: scoring, gating, sizing and
 * execution routing all live server-side and are rendered, never re-derived.
 * The view-state below (filters, sort, expanded rows, active rail tab) is
 * local to the browser and never leaves it.
 */
"use strict";

/* Base prefix: "" when served standalone at "/", "/kimi" when embedded in Athena. */
const BASE = window.KIMI_BASE || "";

let state = null;
let selSymbol = null;
let selTf = "15m";
let chart = null, cSeries = null, vSeries = null, eSeries = null, vChartSymbol = null;
let livePriceLine = null;
let chartRequestSeq = 0;

/* ---- local view state (display only) ---- */
const view = {
  watchQuery: "",
  watchSort: "score",
  watchSide: "all",
  sigDir: "all",
  sigGrade: "all",
  sigSort: "score",
  openSigs: new Set(),
  railTab: "positions",
};

const $ = (id) => document.getElementById(id);
const fmt = (n, d = 2) => n === null || n === undefined || !Number.isFinite(Number(n)) ? "—" :
  Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPx = (p) => p >= 1000 ? fmt(p, 1) : p >= 10 ? fmt(p, 2) : fmt(p, 4);
const signed = (n, d = 2) => (Number(n) >= 0 ? "+" : "") + fmt(n, d);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hhmmss = (sec) => new Date(sec * 1000).toISOString().slice(11, 19);

/* display name: BTCUSDT → BTC/USDT, everything else (EURUSD, XAUUSD, US30) as-is */
const dispSym = (s) => s.endsWith("USDT") ?
  `${esc(s.slice(0, -4))}<span class="quote">/USDT</span>` : esc(s);

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
    layout: { background: { color: "transparent" }, textColor: "#7b88a3",
              fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 10 },
    grid: { vertLines: { color: "rgba(148,163,196,.055)" },
            horzLines: { color: "rgba(148,163,196,.055)" } },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#232c42" },
    rightPriceScale: { borderColor: "#232c42", scaleMargins: { top: 0.12, bottom: 0.12 } },
    crosshair: {
      mode: 0,
      vertLine: { color: "rgba(90,150,255,.45)", width: 1, style: 3, labelBackgroundColor: "#2f6fe0" },
      horzLine: { color: "rgba(90,150,255,.45)", width: 1, style: 3, labelBackgroundColor: "#2f6fe0" },
    },
  });
  cSeries = chart.addCandlestickSeries({
    upColor: "#2bb07a", downColor: "#e8514d", borderVisible: false,
    wickUpColor: "#2bb07a", wickDownColor: "#e8514d",
  });
  vSeries = chart.addLineSeries({ color: "rgba(31,214,240,.85)", lineWidth: 1, priceLineVisible: false,
                                  lastValueVisible: false, title: "VWAP" });
  eSeries = chart.addLineSeries({ color: "rgba(122,162,247,.55)", lineWidth: 1, lineStyle: 2,
                                  priceLineVisible: false, lastValueVisible: false, title: "EMA21" });
  const fit = () => chart.applyOptions({ width: $("chart").clientWidth, height: $("chart").clientHeight });
  window.addEventListener("resize", fit);
  if (window.ResizeObserver) new ResizeObserver(fit).observe($("chart"));
}

/* Sweep arrows cluster tightly on intraday data. Every marker is still drawn —
 * only the repeated "SWP" text is thinned so the labels stay readable. */
function chartMarkers(d) {
  const barSec = d.candles.length > 1 ? Math.max(1, d.candles[1].t - d.candles[0].t) : 60;
  const minLabelGap = barSec * 4;
  const lastLabelled = { belowBar: -Infinity, aboveBar: -Infinity };
  const markers = [];
  for (const e of d.events) {
    if (e.kind !== "sweep") continue;
    const pos = e.dir > 0 ? "belowBar" : "aboveBar";
    const label = e.t - lastLabelled[pos] >= minLabelGap;
    if (label) lastLabelled[pos] = e.t;
    markers.push({
      time: e.t, position: pos,
      color: e.dir > 0 ? "rgba(43,176,122,.8)" : "rgba(232,81,77,.8)",
      shape: e.dir > 0 ? "arrowUp" : "arrowDown",
      size: 0.7,
      text: label ? "SWP" : "",
    });
  }
  for (const s of d.signals) {
    markers.push({
      time: Math.floor(s.createdAt), position: s.direction === "LONG" ? "belowBar" : "aboveBar",
      color: "#5a96ff", shape: "circle", text: `${s.grade} ${s.score}`,
    });
  }
  markers.sort((a, b) => a.time - b.time);
  return markers;
}

async function loadChart() {
  if (!selSymbol) return;
  const symbol = selSymbol;
  const tf = selTf;
  const requestSeq = ++chartRequestSeq;
  const r = await fetch(`${BASE}/api/chart?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(tf)}`);
  const d = await r.json();
  // A slower response for the previously selected pair must never overwrite
  // the chart after the user has selected another one.
  if (requestSeq !== chartRequestSeq || symbol !== selSymbol || tf !== selTf) return;
  if (d.error) { $("chart-meta").textContent = d.error; return; }
  cSeries.setData(d.candles.map(k => ({ time: k.t, open: k.o, high: k.h, low: k.l, close: k.c })));
  vSeries.setData(d.vwap.map(p => ({ time: p.t, value: p.v })));
  eSeries.setData(d.ema21.map(p => ({ time: p.t, value: p.v })));
  cSeries.setMarkers(chartMarkers(d));
  $("chart-symbol").innerHTML = dispSym(d.symbol);
  $("chart-tf").textContent = d.tf;
  $("chart-meta").textContent = `src ${d.source} · ${d.candles.length} bars`;
  if (vChartSymbol !== symbol + tf) { chart.timeScale().fitContent(); vChartSymbol = symbol + tf; }
  renderLiveQuote();
}

/* ---------------- shared helpers ---------------- */
const COMP_MAX = { tide: 20, pulse: 15, structure: 18, flow: 12,
                   pressure: 10, vwap: 8, session: 9, vol: 8 };

function gradeClass(g) { return g === "A+" ? "ap" : g === "A" ? "a" : g === "B" ? "b" : "n"; }

function bestScore(d) {
  const L = Number(d && d.cards && d.cards.long && d.cards.long.total) || 0;
  const S = Number(d && d.cards && d.cards.short && d.cards.short.total) || 0;
  return Math.max(L, S);
}

function sideScore(d, side) {
  return Number(d && d.cards && d.cards[side] && d.cards[side].total) || 0;
}

function selectedQuote() {
  const row = state?.symbols?.[selSymbol];
  const price = Number(row?.price);
  return row && Number.isFinite(price) && price > 0 ? { row, price } : null;
}

function quoteAgeSec(row) {
  const tickerTs = Number(row?.ticker?.ts);
  return Number.isFinite(tickerTs) && tickerTs > 0 ?
    Math.max(0, Date.now() / 1000 - tickerTs) : Number(row?.priceAgeSec);
}

function quoteIsFresh(row) {
  const age = quoteAgeSec(row);
  const maxAge = Number(state?.config?.tickStaleSec);
  return row?.priceFresh !== false && Number.isFinite(age) &&
    (!Number.isFinite(maxAge) || age <= maxAge);
}

function emptyState(title, hint) {
  return `<div class="empty"><b>${esc(title)}</b>${hint ? esc(hint) : ""}</div>`;
}

/* ---------------- live quote on the chart ---------------- */
function renderLiveQuote() {
  const el = $("chart-live-price");
  const src = $("chart-live-source");
  const quote = selectedQuote();
  if (!quote) {
    if (el) { el.textContent = "—"; el.className = "mono"; }
    if (src) src.textContent = "no current quote";
    if (livePriceLine && cSeries) cSeries.removePriceLine(livePriceLine);
    livePriceLine = null;
    return;
  }
  const { row, price } = quote;
  const age = quoteAgeSec(row);
  const fresh = quoteIsFresh(row);
  const source = String(row.priceSource || row.ticker?.source || "unknown").toUpperCase();
  if (el) {
    el.textContent = fmtPx(price);
    el.className = `mono ${fresh ? "fresh" : "stale"}`;
  }
  if (src) {
    src.textContent = `${fresh ? "LIVE" : "STALE"} · ${source} · ` +
      `${Number.isFinite(age) ? Math.round(age) + "s" : "age unknown"}`;
  }
  const opts = {
    price,
    color: fresh ? "#1fd6f0" : "#f0a93a",
    lineWidth: 1,
    lineStyle: 2,
    axisLabelVisible: true,
    title: fresh ? `LIVE ${source}` : `STALE ${source}`,
  };
  if (livePriceLine) livePriceLine.applyOptions(opts);
  else if (cSeries) livePriceLine = cSeries.createPriceLine(opts);
}

/* ---------------- watchlist ---------------- */
function renderWatchlist() {
  const el = $("watchlist");
  const min = Number(state.minScore) || 0;
  const active = new Set(state.universe || []);
  const q = view.watchQuery;

  const all = Object.entries(state.symbols || {})
    .filter(([sym]) => !active.size || active.has(sym));

  let rows = all.filter(([sym, d]) => {
    if (q && !sym.includes(q)) return false;
    if (view.watchSide === "all") return bestScore(d) >= min;
    return sideScore(d, view.watchSide) >= min;
  });

  const key = view.watchSide === "all" ? bestScore : (d) => sideScore(d, view.watchSide);
  if (view.watchSort === "symbol") rows.sort((a, b) => a[0].localeCompare(b[0]));
  else if (view.watchSort === "change") {
    rows.sort((a, b) => (Number(b[1]?.ticker?.changePct24h) || -Infinity) -
                        (Number(a[1]?.ticker?.changePct24h) || -Infinity));
  } else rows.sort((a, b) => key(b[1]) - key(a[1]));

  $("watch-count").textContent = all.length ? `${rows.length}/${all.length}` : "—";

  el.innerHTML = rows.map(([sym, d]) => {
    const tk = d.ticker || {};
    const chg = tk.changePct24h;
    const chgHtml = chg === null || chg === undefined ?
      `<span class="sym-chg dim">—</span>` :
      `<span class="sym-chg ${chg >= 0 ? "pos" : "neg"}">${signed(chg, 1)}%</span>`;
    const L = d.cards?.long || { total: 0, grade: "—" };
    const S = d.cards?.short || { total: 0, grade: "—" };
    const lead = Number(L.total) >= Number(S.total) ? "long" : "short";
    const pxTitle = `${d.priceSource || "?"}${d.priceAgeSec != null ?
      " · quote " + d.priceAgeSec + "s old" : " · no quote timestamp"}`;
    const side = (tag, cls, card) => `
      <span class="side ${cls} ${lead === cls ? "lead" : ""}">
        <span class="side-tag">${tag}</span>
        <span class="track2"><i style="width:${Math.max(0, Math.min(100, Number(card.total) || 0))}%"></i></span>
        <span class="side-val grade ${gradeClass(card.grade)}">${fmt(card.total, 0)}</span>
      </span>`;
    return `<button class="sym ${sym === selSymbol ? "sel" : ""}" data-sym="${esc(sym)}">
      <span class="sym-top">
        <span class="sym-name">${dispSym(sym)}</span>
        ${d.fresh === false ? '<span class="flag">STALE BAR</span>' : ""}
        <span class="sym-px mono ${d.priceFresh === false ? "stale" : ""}" title="${esc(pxTitle)}">${fmtPx(d.price || 0)}</span>
        ${chgHtml}
      </span>
      <span class="sym-scores">${side("L", "long", L)}${side("S", "short", S)}</span>
    </button>`;
  }).join("") || emptyState(
    all.length ? "Nothing at this filter" : "Scanning…",
    all.length ? `${all.length} pairs scored · min score ${min}` : "waiting for the first scan");

  el.querySelectorAll(".sym").forEach(n => n.onclick = () => {
    selSymbol = n.dataset.sym; renderWatchlist(); loadChart();
  });
}

/* ---------------- signals ---------------- */
function execButtons(s) {
  const he = state.hostExec || {};
  const out = [];
  if (he.available) {
    const venueName = s.symbol.endsWith("USDT") ? "BYBIT" : "MT5";
    out.push(`<button class="btn mini primary" data-exec="${esc(s.id)}" data-venue="host"
      title="Athena pipeline: risk engine + guardian + managed execution">${venueName}${he.demoOnly ? " DEMO" : ""}</button>`);
  }
  out.push(`<button class="btn mini ghost" data-exec="${esc(s.id)}" data-venue="paper">PAPER</button>`);
  if (state.broker && state.broker.armed) {
    out.push(`<button class="btn mini ghost" data-exec="${esc(s.id)}" data-venue="bybit">BYBIT DEMO</button>`);
  }
  return out.join("");
}

function renderSignals() {
  const el = $("signals");
  const min = Number(state.minScore) || 0;
  const now = Date.now() / 1000;

  let sigs = (state.signals || []).filter(s => Number(s.score) >= min);
  if (view.sigDir !== "all") sigs = sigs.filter(s => s.direction === view.sigDir);
  if (view.sigGrade === "A+") sigs = sigs.filter(s => s.grade === "A+");
  else if (view.sigGrade === "A") sigs = sigs.filter(s => s.grade === "A+" || s.grade === "A");

  if (view.sigSort === "ttl") sigs.sort((a, b) => a.validUntil - b.validUntil);
  else if (view.sigSort === "new") sigs.sort((a, b) => b.createdAt - a.createdAt);
  else if (view.sigSort === "symbol") sigs.sort((a, b) => a.symbol.localeCompare(b.symbol));
  else sigs.sort((a, b) => b.score - a.score);

  const total = (state.signals || []).filter(s => Number(s.score) >= min).length;
  $("sig-count").textContent = total ? `${sigs.length}/${total}` : "—";

  el.innerHTML = sigs.map(s => {
    const open = view.openSigs.has(s.id);
    const ttlMin = Math.max(0, Math.round((s.validUntil - now) / 60));
    const comps = Object.entries(s.components).map(([k, v]) => {
      const max = COMP_MAX[k] || 10;
      return `<div class="comp">
        <div class="cl"><span>${esc(k)}</span><b>${fmt(v, 1)}<span class="dim">/${max}</span></b></div>
        <div class="track2"><i style="width:${Math.round(Math.max(0, Math.min(1, v / max)) * 100)}%"></i></div>
      </div>`;
    }).join("");

    const quoteRow = state.symbols?.[s.symbol];
    const livePrice = Number(quoteRow?.price);
    const liveSource = String(quoteRow?.priceSource || quoteRow?.ticker?.source || "").toUpperCase();
    const liveHtml = Number.isFinite(livePrice) && livePrice > 0 ?
      `<span class="sig-live ${quoteIsFresh(quoteRow) ? "fresh" : "stale"}">${fmtPx(livePrice)}<small>${esc(liveSource || "UNKNOWN")}</small></span>` :
      `<span class="sig-live stale">—<small>NO QUOTE</small></span>`;

    return `<div class="sig ${open ? "open" : ""}" data-sig="${esc(s.id)}">
      <div class="sig-row" data-toggle="${esc(s.id)}">
        <span class="dir ${s.direction.toLowerCase()}">${s.direction === "LONG" ? "▲" : "▼"} ${s.direction}</span>
        <span class="sig-sym">${dispSym(s.symbol)}</span>
        <span class="sig-score">
          <span class="grade-chip ${gradeClass(s.grade)}">${esc(s.grade)} ${fmt(s.score, 1)}</span>
          <span class="track2"><i style="width:${Math.max(0, Math.min(100, Number(s.score) || 0))}%"></i></span>
        </span>
        <span class="levels">
          <span class="lvl"><span class="lk">Entry</span><span class="lv">${fmtPx(s.entry)}</span></span>
          <span class="lvl sl"><span class="lk">SL</span><span class="lv">${fmtPx(s.sl)}</span></span>
          <span class="lvl tp tp1"><span class="lk">TP1</span><span class="lv">${fmtPx(s.tp1)}</span></span>
          <span class="lvl tp tp2"><span class="lk">TP2</span><span class="lv">${fmtPx(s.tp2)}</span></span>
        </span>
        ${liveHtml}
        <span class="ttl ${ttlMin <= 5 ? "soon" : ""}" title="time remaining before the signal expires">${ttlMin}m</span>
        <span class="sig-actions">${execButtons(s)}</span>
        <span class="chev">▶</span>
      </div>
      <div class="sig-detail">
        <div class="comps">${comps}</div>
        <div class="reasons">${(s.reasons || []).map(r => `<span class="reason">${esc(r)}</span>`).join("")}</div>
      </div>
    </div>`;
  }).join("") || emptyState(
    total ? "No signal matches this filter" : "No active signals",
    total ? `${total} active · adjust direction, grade or min score` : "engine scanning");

  el.querySelectorAll("[data-toggle]").forEach(n => n.onclick = (ev) => {
    if (ev.target.closest("[data-exec]")) return;
    const id = n.dataset.toggle;
    view.openSigs.has(id) ? view.openSigs.delete(id) : view.openSigs.add(id);
    n.closest(".sig").classList.toggle("open");
  });
  el.querySelectorAll("[data-exec]").forEach(b => b.onclick = async (ev) => {
    ev.stopPropagation();
    b.disabled = true; b.textContent = "…";
    const r = await fetch(`${BASE}/api/execute`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: b.dataset.exec, venue: b.dataset.venue }) }).then(x => x.json());
    if (!r.ok) { b.textContent = "✕ " + (r.error || "failed"); setTimeout(render, 2500); }
    refresh();
  });
}

/* ---------------- right rail ---------------- */
function renderPositions() {
  const ps = state.account?.positions || [];
  $("n-positions").textContent = ps.length;
  $("positions").innerHTML = ps.map(p => {
    const pnl = (p.unrealized ?? 0) + (p.realized ?? 0);
    return `<div class="card">
      <div class="card-top">
        <span class="dir ${p.direction.toLowerCase()}" style="width:auto">${p.direction === "LONG" ? "▲" : "▼"} ${p.direction}</span>
        <b>${dispSym(p.symbol)}</b>
        ${p.broker === "bybit" ? '<span class="btag">DEMO</span>' : ""}
        <span class="money ${pnl >= 0 ? "pos" : "neg"}">${signed(pnl)}</span>
      </div>
      <div class="card-grid">
        <div class="kv"><span>Qty</span><b>${fmt(p.qty, 5)}</b></div>
        <div class="kv"><span>Score</span><b>${fmt(p.score, 1)}</b></div>
        <div class="kv"><span>Entry</span><b>${fmtPx(p.entry)}</b></div>
        <div class="kv"><span>Mark</span><b>${fmtPx(p.mark || 0)}</b></div>
        <div class="kv"><span>SL</span><b class="neg">${fmtPx(p.sl)}</b></div>
        <div class="kv"><span>TP2</span><b class="pos">${fmtPx(p.tp2)}</b></div>
      </div>
      <div class="card-foot">
        <span style="flex:1"></span>
        <button class="btn mini ghost" data-close="${esc(p.id)}">CLOSE</button>
      </div>
    </div>`;
  }).join("") || emptyState("Flat", "no open positions");

  $("positions").querySelectorAll("[data-close]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    await fetch(`${BASE}/api/close`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: b.dataset.close }) });
    refresh();
  });
}

function renderTrades() {
  const ts = (state.account?.trades || []).slice(0, 40);
  $("n-trades").textContent = (state.account?.trades || []).length;
  $("trades").innerHTML = ts.map(t => `<div class="card">
      <div class="card-top">
        <span class="dir ${t.direction.toLowerCase()}" style="width:auto">${t.direction === "LONG" ? "▲" : "▼"} ${t.direction}</span>
        <b>${dispSym(t.symbol)}</b>
        <span class="money ${t.pnl >= 0 ? "pos" : "neg"}">${signed(t.pnl)}</span>
      </div>
      <div class="card-grid">
        <div class="kv"><span>Entry</span><b>${fmtPx(t.entry)}</b></div>
        <div class="kv"><span>Exit</span><b>${fmtPx(t.exit)}</b></div>
        <div class="kv"><span>Reason</span><b>${esc(t.reason)}</b></div>
        <div class="kv"><span>Closed</span><b>${t.closedAt ? hhmmss(t.closedAt) + "Z" : "—"}</b></div>
      </div>
    </div>`).join("") || emptyState("No trades yet", "closed paper/broker fills land here");
}

function renderEvents() {
  const es = (state.events || []).slice(0, 40);
  $("n-events").textContent = (state.events || []).length;
  $("events").innerHTML = es.map(e => {
    const money = e.pnl !== undefined ? `<b class="mono ${e.pnl >= 0 ? "pos" : "neg"}">${signed(e.pnl)}</b>` :
      e.qty !== undefined ? `<b class="mono dim">qty ${fmt(e.qty, 5)}</b>` : "";
    const t = e.t || e.closedAt;
    const dir = e.side || e.direction || "";
    return `<div class="evt">
      <span class="evt-type ${String(e.type).toLowerCase() === "close" ? "close" : ""}">${esc(e.type)}</span>
      <span class="evt-body">
        ${e.symbol ? `<b>${dispSym(e.symbol)}</b>` : ""}
        ${dir ? `<span class="${dir === "LONG" ? "pos" : "neg"}" style="font-size:10px;font-weight:700">${esc(dir)}</span>` : ""}
        ${e.venue ? `<span class="dim" style="font-size:10px">${esc(e.venue)}</span>` : ""}
        ${money}
      </span>
      <span class="evt-time">${t ? hhmmss(t) + "Z" : ""}</span>
    </div>`;
  }).join("") || emptyState("No events", "fills, closes and rejects appear here");
}

function renderErrors() {
  const errs = state.errors || [];
  const n = $("n-errors");
  n.textContent = errs.length;
  n.className = errs.length ? "n alert" : "n";
  $("errlist").innerHTML = errs.map(e => `<div class="errline">${esc(e)}</div>`).join("") ||
    emptyState("No errors", "the last feed and scan errors appear here");
  $("errors").textContent = errs[0] || "";
}

function renderRailTab() {
  document.querySelectorAll("#rail-tabs .tab").forEach(t =>
    t.classList.toggle("on", t.dataset.tab === view.railTab));
  const panes = { positions: "positions", trades: "trades", events: "events", errors: "errlist" };
  Object.entries(panes).forEach(([tab, id]) => { $(id).hidden = tab !== view.railTab; });
}

/* ---------------- equity curve ---------------- */
function drawSpark() {
  const cv = $("spark");
  const curve = state.account?.equityCurve || [];
  if (!cv || !curve.length || !cv.clientWidth) return;
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = 38;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  cv.style.height = h + "px";
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const vals = curve.map(p => p.equity);
  const lo = Math.min(...vals), hi = Math.max(...vals), rg = hi - lo || 1;
  const x = (i) => i / (vals.length - 1 || 1) * w;
  const y = (v) => h - 3 - (v - lo) / rg * (h - 6);
  const up = vals[vals.length - 1] >= vals[0];
  const stroke = up ? "#2bb07a" : "#e8514d";

  const line = new Path2D();
  vals.forEach((v, i) => (i ? line.lineTo(x(i), y(v)) : line.moveTo(x(i), y(v))));

  const fill = new Path2D(line);
  fill.lineTo(w, h); fill.lineTo(0, h); fill.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, up ? "rgba(43,176,122,.28)" : "rgba(232,81,77,.28)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = grad; ctx.fill(fill);

  ctx.strokeStyle = stroke; ctx.lineWidth = 1.5;
  ctx.lineJoin = "round"; ctx.stroke(line);
}

/* ---------------- header + metrics ---------------- */
function envTag(x) {
  const e = x && x.environment;
  const cls = e === "demo" ? "demo" : e === "real" ? "real" : "";
  const txt = e === "demo" ? "DEMO" : e === "real" ? "REAL" : "ENV?";
  return `<span class="env-tag ${cls}">${txt}</span>`;
}

function renderCapital() {
  const acc = state.accounts || {};
  const a = state.account || {};
  const mt5 = acc.mt5, byb = acc.bybit;

  if (mt5 && mt5.available) {
    $("mt5-name").textContent = "MT5";
    $("mt5-val").innerHTML = `${fmt(mt5.equity ?? 0)}<span class="venue-cur">${esc(mt5.currency || "")}</span>`;
    // The env tag is a safety signal (demo vs real) and stays visible at every
    // size; only the account identifier collapses on short viewports.
    $("mt5-sub").innerHTML = `${envTag(mt5)}<span class="venue-id"> ${esc(String(mt5.login ?? "?"))} · ${esc(mt5.server || "")}</span>`;
    $("mt5-sub").className = "venue-sub" + (mt5.environment === "demo" ? "" : " warn");
  } else {
    $("mt5-val").innerHTML = `—`;
    $("mt5-sub").textContent = (mt5 && mt5.error) || acc.reason || "not connected";
    $("mt5-sub").className = "venue-sub";
  }

  if (byb && byb.available) {
    $("bybit-val").innerHTML = `${fmt(byb.equity ?? 0)}<span class="venue-cur">${esc(byb.currency || "USDT")}</span>`;
    $("bybit-sub").innerHTML = `${envTag(byb)}<span class="venue-id">${byb.testnet ? " TESTNET" : ""}</span>`;
    $("bybit-sub").className = "venue-sub" + (byb.environment === "demo" ? "" : " warn");
  } else {
    $("bybit-val").innerHTML = `—`;
    $("bybit-sub").textContent = (byb && byb.error) || acc.reason || "not connected";
    $("bybit-sub").className = "venue-sub";
  }

  $("paper-val").innerHTML = `${fmt(a.equity ?? 0)}<span class="venue-cur">USD</span>`;
  const up = a.unrealized ?? 0;
  $("paper-sub").innerHTML =
    `<span class="${up >= 0 ? "pos" : "neg"}">${signed(up)}</span>` +
    `<span class="venue-id"> unreal · cash ${fmt(a.cash ?? 0, 0)}</span>`;
  $("paper-sub").className = "venue-sub";
  $("curve-val").textContent = fmt(a.equity ?? 0, 0);
}

function renderRisk() {
  const loss = Number(state.dailyLossPct ?? 0);
  const cap = Number(state.config?.maxDailyLossPct);
  const ratio = Number.isFinite(cap) && cap > 0 ? loss / cap : 0;
  const cls = ratio >= 1 ? "over" : ratio >= 0.7 ? "near" : "ok";
  $("dloss-val").textContent = fmt(loss, 2) + "%";
  $("dloss-val").className = ratio >= 1 ? "neg" : ratio >= 0.7 ? "warn" : "";
  $("dloss-cap").textContent = Number.isFinite(cap) ? `/ ${fmt(cap, 2)}% cap` : "/ no cap set";
  const bar = $("dloss-bar");
  bar.className = cls;
  bar.style.width = Math.max(0, Math.min(100, ratio * 100)) + "%";
  const note = $("dloss-note");
  if (!Number.isFinite(cap) || cap <= 0) { note.textContent = "no daily loss cap configured"; note.className = "meter-note"; }
  else if (ratio >= 1) { note.textContent = "DAILY LOSS CAP BREACHED"; note.className = "meter-note breach"; }
  else { note.textContent = `${fmt(cap - loss, 2)}% of today's allowance left`; note.className = "meter-note"; }
}

function renderHeader() {
  const min = Number(state.minScore) || 0;
  const sigCount = (state.signals || []).filter(s => Number(s.score) >= min).length;
  $("c-signals").textContent = sigCount;
  $("c-open").textContent = (state.account?.positions || []).length;
  $("c-scanned").textContent = Object.keys(state.symbols || {}).length;

  const ms = $("min-score");
  if (ms && document.activeElement !== ms && state.minScore != null) ms.value = state.minScore;
  const at = $("auto-trade");
  if (at && document.activeElement !== at) at.checked = !!state.autoTrade;

  $("clock").textContent = new Date().toISOString().slice(11, 19) + " UTC";

  const age = Date.now() / 1000 - (state.lastScan || 0);
  const pill = $("scan-pill");
  const dead = !!state.killSwitch;
  const stale = age >= 15;
  $("scan-pill-text").textContent = dead ? "KILLED" : state.scanning ? "SCANNING" : stale ? "STALE" : "LIVE";
  pill.className = "pill " + (dead ? "dead" : stale ? "stale" : "live");
  $("scan-meta").textContent = `scan #${state.scanCount ?? 0} · ${Math.round(age)}s ago`;

  const kill = $("kill");
  kill.classList.toggle("on", dead);
  kill.textContent = dead ? "KILLED — RESUME" : "KILL SWITCH";

  const he = state.hostExec || {};
  const hostChip = $("host-chip");
  hostChip.textContent = he.available ? `HOST ${he.demoOnly ? "DEMO" : "ARMED"}` : "HOST OFF";
  hostChip.className = "pill " + (he.available ? "armed" : "");
  hostChip.title = he.available ?
    "Athena pipeline: risk engine + guardian + managed execution" :
    (he.reason || "host execution unavailable — engine stays paper");

  const br = state.broker || {};
  const chip = $("broker-chip");
  if (br.armed) {
    chip.textContent = `BYBIT DEMO · ${fmt(br.equity ?? 0, 0)}`;
    chip.className = "pill armed"; chip.title = "demo account connected";
  } else {
    chip.textContent = "PAPER ONLY";
    chip.className = "pill"; chip.title = br.error || "broker not armed";
  }

  const sn = $("scan-now");
  sn.classList.toggle("busy", !!state.scanning);
  sn.textContent = state.scanning ? "⟳ …" : "⟳ SCAN";

  const cfg = state.config || {};
  $("feed-status").textContent =
    `quotes bybit · mt5 · fallbacks binance/yahoo · tf ${cfg.tfBias || "?"}/${cfg.tfContext || "?"}/${cfg.tfEntry || "?"} · ` +
    `risk ${fmt(cfg.riskPerTradePct, 1)}%/trade · max ${cfg.maxPositions ?? "?"} pos · broker ${state.brokerMode}` +
    `${state.liveAvailable ? " (live armed)" : ""}`;
}

/* ---------------- render ---------------- */
function render() {
  if (!state) return;
  renderHeader();
  renderCapital();
  renderRisk();

  const universe = state.universe || [];
  if ((!selSymbol || (universe.length && !universe.includes(selSymbol))) && state.symbols) {
    const first = universe.find(sym => state.symbols[sym]) || Object.keys(state.symbols)[0];
    if (first) { selSymbol = first; loadChart(); }
  }

  renderWatchlist(); renderLiveQuote(); renderSignals();
  renderPositions(); renderTrades(); renderEvents(); renderErrors();
  renderRailTab(); drawSpark();
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
$("tf-switch").onclick = (e) => {
  const b = e.target.closest(".tfbtn");
  if (!b) return;
  document.querySelectorAll(".tfbtn").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); selTf = b.dataset.tf; loadChart();
};

/* watchlist + signal view controls — local display state only */
$("watch-search").oninput = (e) => {
  view.watchQuery = e.target.value.trim().toUpperCase();
  if (state) renderWatchlist();
};
$("watch-sort").onchange = (e) => { view.watchSort = e.target.value; if (state) renderWatchlist(); };
$("watch-side").onclick = (e) => {
  const b = e.target.closest("button[data-side]");
  if (!b) return;
  view.watchSide = b.dataset.side;
  $("watch-side").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
  if (state) renderWatchlist();
};
$("sig-dir").onclick = (e) => {
  const b = e.target.closest("button[data-dir]");
  if (!b) return;
  view.sigDir = b.dataset.dir;
  $("sig-dir").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
  if (state) renderSignals();
};
$("sig-grade").onclick = (e) => {
  const b = e.target.closest("button[data-grade]");
  if (!b) return;
  view.sigGrade = b.dataset.grade;
  $("sig-grade").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
  if (state) renderSignals();
};
$("sig-sort").onchange = (e) => { view.sigSort = e.target.value; if (state) renderSignals(); };
$("rail-tabs").onclick = (e) => {
  const b = e.target.closest(".tab");
  if (!b) return;
  view.railTab = b.dataset.tab;
  renderRailTab();
};

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
    (over ? ` <b class="err">over limit</b>` : "");
  $("pairs-save").disabled = over || pairsSel.size === 0;
  $("pairs-list").innerHTML = shown.map(s =>
    `<label><input type="checkbox" data-sym="${esc(s)}" ${pairsSel.has(s) ? "checked" : ""}>
       ${dispSym(s)}</label>`).join("") ||
    `<div class="empty">no active book — type to search the exchange catalog</div>`;
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
  $("pairs-list").innerHTML = `<div class="empty">loading exchange symbols…</div>`;
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
  if (r.ok) {
    if (!sel.includes(selSymbol)) selSymbol = sel[0];
    chartRequestSeq += 1;
    pairsCache.active = sel;
    $("pairs-modal").classList.add("hidden");
    await refresh();
    loadChart();
  }
  else alert(r.error || "failed to save universe");
};

async function refresh() {
  try { state = await fetch(`${BASE}/api/state`).then(r => r.json()); render(); } catch (e) {}
}

initChart();
connect();
setInterval(loadChart, 20000);
