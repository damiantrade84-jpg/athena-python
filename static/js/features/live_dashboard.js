// Live Dashboard (Cyber Cockpit) — extracted from static/index.html (Tier 3 cleanup pass).
// Wires up panel-live-dashboard: 9-tab cockpit (Overview, Levels, Engine A/B/C/D, AI, Execute, Diagnostics).
// Paper/demo monitoring only. No execution. No real orders.
//
// This is a pure refactor — no logic changes. If this file misbehaves,
// diff against the pre-extraction inline block in git history.

// ── Live Dashboard v2 — Cyber Cockpit ────────────────────────────────────────
// Paper/demo monitoring only. No execution. No real orders.
(function() {
'use strict';

var _ldPollTimer = null;
var _ldLastSnapshot = null;
var _ldEventHistory = [];
var _ldSelectedSym = null;
var _ldCurrentTab = 'overview';

// ── Register panel callback ──────────────────────────────────────────────────────────
window._panelCallbacks = window._panelCallbacks || {};
window._panelCallbacks['live-dashboard'] = window._panelCallbacks['live-dashboard'] || [];
window._panelCallbacks['live-dashboard'].push(function(name) {
  if (name === 'live-dashboard') {
    ldLoadSnapshot();
    if (document.getElementById('ldAutoPoll') && document.getElementById('ldAutoPoll').checked) {
      ldStartAutoPoll();
    }
  } else {
    ldStopAutoPoll();
  }
});

function ldToggleAutoPoll(on) {
  if (on) ldStartAutoPoll(); else ldStopAutoPoll();
}
window.ldToggleAutoPoll = ldToggleAutoPoll;

function ldStartAutoPoll() {
  ldStopAutoPoll();
  _ldPollTimer = setInterval(ldLoadSnapshot, 5000);
}
function ldStopAutoPoll() {
  if (_ldPollTimer) { clearInterval(_ldPollTimer); _ldPollTimer = null; }
}

// ── Main snapshot loader ──────────────────────────────────────────────────────
function ldLoadSnapshot() {
  console.log('[Live Dashboard] ldLoadSnapshot called');
  var inp = document.getElementById('ldSymbolInput');
  var syms = inp ? inp.value.trim() : '';
  var url = '/api/live-dashboard/snapshot';
  if (syms) url += '?symbols=' + encodeURIComponent(syms);
  console.log('[Live Dashboard] Fetching:', url);
  fetch(url)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      _ldLastSnapshot = d;
      ldRender(d);
    })
    .catch(function(err) {
      console.warn('[LD] snapshot fetch failed:', err);
      var grid = document.getElementById('ldCardGrid');
      if (grid) grid.innerHTML = '<div style="color:var(--bear);font-size:11px;font-family:var(--mono);padding:12px;">Snapshot fetch error: ' + err + '</div>';
    });
}
window.ldLoadSnapshot = ldLoadSnapshot;

// ── Main render ───────────────────────────────────────────────────────────────
function ldRender(d) {
  if (!d) return;
  ldRenderStatusBar(d);
  ldRenderStatusTiles(d);
  ldApplyFilter();
  var evts = d.events || [];
  _ldEventHistory = evts.concat(_ldEventHistory).slice(0, 80);
  ldRenderEventFeed(_ldEventHistory);
  ldRenderScalpRadar(d.symbols || []);
  ldRenderPaperSummary(d.symbols || [], d.paperMode || {});
  var lu = document.getElementById('ld-last-updated');
  if (lu) lu.textContent = 'Updated ' + new Date().toLocaleTimeString();
  var ndot = document.getElementById('ld-nav-dot');
  if (ndot) { ndot.className = 'conn-dot'; ndot.style.background = '#22d3ee'; }
  // Refresh detail panel if a symbol is currently selected
  if (_ldSelectedSym) {
    var fresh = (d.symbols || []).find(function(s){ return s.symbol === _ldSelectedSym; });
    if (fresh) { ldUpdateDetailPrice(fresh); }
  }
}

// ── Status bar ────────────────────────────────────────────────────────────────
function ldRenderStatusBar(d) {
  var mt5El = document.getElementById('ld-mt5-badge');
  var bnEl  = document.getElementById('ld-binance-badge');
  var frEl  = document.getElementById('ld-freshness-badge');
  var conn  = d.connections || {};
  if (mt5El) {
    var mt5s = conn.mt5 || 'unknown';
    mt5El.className = 'ld-hbadge ' + (mt5s === 'connected' ? 'conn-ok' : mt5s === 'error' ? 'conn-err' : 'conn-unk');
    mt5El.textContent = 'MT5 ' + mt5s.toUpperCase();
  }
  if (bnEl) {
    var bns = conn.binanceWs || 'unknown';
    bnEl.className = 'ld-hbadge ' + (bns === 'live' ? 'conn-ok' : bns === 'error' ? 'conn-err' : 'conn-unk');
    bnEl.textContent = 'BINANCE ' + bns.toUpperCase();
  }
  if (frEl) {
    var allOk = d.freshnessAllOk !== false;
    frEl.className = 'ld-hbadge ' + (allOk ? 'fresh-ok' : 'fresh-warn');
    frEl.textContent = allOk ? '✓ FRESHNESS OK' : '⚠ FRESHNESS ISSUE';
  }
}

// ── Filter / sort + card grid render ─────────────────────────────────────────
function ldApplyFilter() {
  if (!_ldLastSnapshot) return;
  var filterEl = document.getElementById('ldFilterSelect');
  var sortEl   = document.getElementById('ldSortSelect');
  var filter   = filterEl ? filterEl.value : 'all';
  var sort     = sortEl   ? sortEl.value   : 'default';
  var syms = (_ldLastSnapshot.symbols || []).slice();
  if (filter === 'paper') {
    syms = syms.filter(function(s){ return s.finalState === 'PAPER CANDIDATE' || ((s.executableState||{}).canPaperExecute === true); });
  } else if (filter === 'watchlist') {
    syms = syms.filter(function(s){ return s.finalState === 'WATCHLIST' || ['WATCHLIST','A_ONLY','B_ONLY'].indexOf((s.engineC||{}).decisionState) >= 0; });
  } else if (filter === 'blocked') {
    syms = syms.filter(function(s){ return s.finalState === 'BLOCKED' || ((s.executableState||{}).disabledReason); });
  } else if (filter === 'engine_d') {
    syms = syms.filter(function(s){ return ['PASS','WATCHLIST','BLOCKED','DATA_MISSING'].indexOf((s.engineD||{}).gateResult) >= 0; });
  } else if (filter === 'crypto') {
    syms = syms.filter(function(s){ return s.asset_type === 'crypto'; });
  } else if (filter === 'index') {
    syms = syms.filter(function(s){ return s.asset_type === 'index'; });
  }
  if (sort === 'conviction_desc' || sort === 'score_desc') {
    syms.sort(function(a,b){ return ((b.engineC||{}).conviction || (b.engineA||{}).conviction || (b.engineA||{}).score || 0) - ((a.engineC||{}).conviction || (a.engineA||{}).conviction || (a.engineA||{}).score || 0); });
  } else if (sort === 'rr_desc') {
    syms.sort(function(a,b){ return ((b.levels||{}).rr || (b.engineB||{}).rr || 0) - ((a.levels||{}).rr || (a.engineB||{}).rr || 0); });
  } else if (sort === 'engine_c') {
    syms.sort(function(a,b){ return String((a.engineC||{}).decisionState || '').localeCompare(String((b.engineC||{}).decisionState || '')); });
  } else if (sort === 'freshness') {
    syms.sort(function(a,b){ return String((b.freshness||{}).gateDecision || '').localeCompare(String((a.freshness||{}).gateDecision || '')); });
  }
  ldRenderCards(syms);
}
window.ldApplyFilter = ldApplyFilter;

function ldRenderCards(symbols) {
  var grid = document.getElementById('ldCardGrid');
  if (!grid) return;
  if (!symbols || !symbols.length) {
    grid.innerHTML = '<div class="ld-no-data">No symbols match the current filter.</div>';
    return;
  }
  grid.innerHTML = '';
  symbols.forEach(function(sym) { grid.appendChild(ldBuildCard(sym)); });
}

// ── Card builder ──────────────────────────────────────────────────────────────
function ldBuildCard(sym) {
  var wrap = document.createElement('div');
  var cState = ((sym.engineC || {}).decisionState || '').toLowerCase();
  var borderCls = '';
  if (cState === 'aligned') borderCls = ' aligned';
  else if (cState === 'conflict') borderCls = ' conflict';
  else if (cState === 'a_only' || cState === 'b_only' || cState === 'watchlist') borderCls = ' watchlist';
  if (sym.symbol === _ldSelectedSym) borderCls += ' selected';
  wrap.className = 'ld-card' + borderCls;
  wrap.setAttribute('data-sym', sym.symbol || '');
  wrap.onclick = function() { ldSelectCard(sym); };

  if (sym.error) {
    wrap.innerHTML = '<div class="ld-card-error">⚠ ' + ldEsc(sym.symbol) + ': ' + ldEsc(sym.error) + '</div>';
    return wrap;
  }

  // Direction badge
  var dir = (sym.engineA||{}).direction || (sym.engineB||{}).direction || '';
  var dirHtml = dir ? '<span class="ld-dir-badge ' + (dir==='LONG'?'long':'short') + '">' + ldEsc(dir) + '</span>' : '';

  // Price / change
  var chg = sym.change_pct != null ? sym.change_pct : null;
  var chgHtml = '';
  if (chg != null) {
    var chgNum = parseFloat(chg);
    chgHtml = '<span class="ld-card-chg ' + (chgNum >= 0 ? 'pos' : 'neg') + '">'
      + (chgNum >= 0 ? '+' : '') + chgNum.toFixed(2) + '%</span>';
  }
  var priceStr = sym.latest_price != null ? ldFmtPrice(sym.latest_price) : '—';

  var header = '<div class="ld-card-header">'
    + '<div style="display:flex;align-items:baseline;gap:0">'
    + '<span class="ld-card-sym">' + ldEsc(sym.symbol) + '</span>'
    + '<span class="ld-card-type">' + ldEsc(sym.asset_type || '') + '</span>'
    + dirHtml + '</div>'
    + '<div style="display:flex;align-items:baseline">'
    + '<span class="ld-card-price">' + priceStr + '</span>' + chgHtml
    + '</div></div>';

  var chartHtml = '<div class="ld-chart-wrap">' + ldBuildChartSvg(sym)
    + '<div class="ld-chart-ticket"><div>Qty: backend</div><div>Mode: <strong>Paper</strong></div><div>PnL: <strong>$0.00</strong></div></div>'
    + '</div>';

  // 2×2 engine pill grid
  var engGrid = '<div class="ld-engine-grid">'
    + ldBuildEPill('Engine A', sym.engineA, 'a')
    + ldBuildEPill('Engine B', sym.engineB, 'b')
    + ldBuildEPillC(sym.engineC)
    + ldBuildEPillD(sym.engineD)
    + '</div>';

  // Footer: freshness pill + reason + RR + paper tag
  var reason = (sym.engineC||{}).reason || '';
  if (!reason && (sym.engineA||{}).failReasons && (sym.engineA.failReasons||[])[0]) {
    reason = sym.engineA.failReasons[0];
  }
  var missingD = (sym.engineD||{}).gateResult === 'DATA_MISSING'
    ? ' Engine D missing: ' + (((sym.engineD||{}).missingData || []).join(', ') || 'cache/result')
    : '';
  var rrVal = (sym.levels||{}).rr || (sym.engineB||{}).rr || (sym.engineA||{}).rr;
  var rrHtml    = rrVal != null ? '<span class="ld-rr-pill">RR ' + parseFloat(rrVal).toFixed(1) + '</span>' : '';
  var paperHtml = (sym.paper||{}).hasOpenPaperPosition ? '<span class="ld-paper-pill">📄 PAPER</span>' : '';
  var setupBlock = '<div class="ld-card-setup"><div><div class="k">Setup</div><div class="v">' + ldEsc(sym.mainReason || reason || 'No setup') + '</div></div>'
    + (rrHtml || '<span class="ld-rr-pill">WAITING</span>') + '</div>';
  var footer = '<div class="ld-card-footer">'
    + ldBuildFreshnessPill(sym.freshness)
    + '<span class="ld-paper-pill">' + ldEsc(sym.finalState || 'NO SETUP') + '</span>'
    + '<span class="ld-card-reason" title="' + ldEsc((sym.mainReason || reason || '') + missingD) + '">' + ldEsc((sym.mainReason || reason || 'NO SETUP') + missingD) + '</span>'
    + rrHtml + paperHtml + '</div>';

  wrap.innerHTML = header + chartHtml + engGrid + setupBlock + footer;
  return wrap;
}

// 2×2 pill helpers
function ldBuildEPill(label, eng, _type) {
  eng = eng || {};
  var passed = eng.passed === true || eng.confidencePassed === true;
  var dir    = eng.direction || '';
  var score  = eng.score != null ? parseFloat(eng.score).toFixed(2) : '—';
  var cls    = passed ? 'pass' : (eng.score != null && eng.score > 0) ? 'watch' : 'miss';
  return '<div class="ld-epill ' + cls + '">'
    + '<span class="ld-epill-label">' + ldEsc(label) + '</span>'
    + '<span class="ld-epill-val">' + (dir ? dir + ' ' : '') + ldEsc(score) + '</span>'
    + '</div>';
}
function ldBuildEPillC(engC) {
  engC = engC || {};
  var state = engC.decisionState || 'NO_SETUP';
  var cls = state === 'ALIGNED' ? 'pass' : state === 'CONFLICT' ? 'block'
          : (state === 'A_ONLY' || state === 'B_ONLY' || state === 'WATCHLIST') ? 'watch' : 'miss';
  return '<div class="ld-epill ' + cls + '">'
    + '<span class="ld-epill-label">Engine C</span>'
    + '<span class="ld-epill-val">' + ldEsc(state) + '</span>'
    + '</div>';
}
function ldBuildEPillD(engD) {
  engD = engD || {};
  var gate  = engD.gateResult || 'NO_DATA';
  var grade = engD.grade || '';
  var cls   = gate === 'PASS' ? 'pass' : gate === 'WATCHLIST' ? 'watch' : gate === 'BLOCKED' ? 'block' : 'miss';
  return '<div class="ld-epill ' + cls + '">'
    + '<span class="ld-epill-label">Engine D</span>'
    + '<span class="ld-epill-val">' + ldEsc(gate) + (grade ? ' ' + grade : '') + '</span>'
    + '</div>';
}

// ── MiniChart SVG (cyber cockpit style) ───────────────────────────────────────
function ldBuildChartSvg(sym) {
  var chart = sym.chart;
  if (!chart || !chart.candles || !chart.candles.length) {
    return '<svg class="ld-chart-svg" viewBox="0 0 300 110" xmlns="http://www.w3.org/2000/svg">'
      + '<rect width="300" height="110" fill="rgba(5,9,20,0.8)"/>'
      + '<text x="150" y="60" text-anchor="middle" fill="rgba(100,116,139,0.45)" font-family="monospace" font-size="10">NO CHART DATA</text>'
      + '</svg>';
  }
  var candles = chart.candles;
  if (candles.length > 30) candles = candles.slice(candles.length - 30);
  var n = candles.length;

  // Compute price range, include level lines
  var lo = Infinity, hi = -Infinity;
  candles.forEach(function(c) {
    var l = parseFloat(c.low), h = parseFloat(c.high);
    if (!isNaN(l) && l < lo) lo = l;
    if (!isNaN(h) && h > hi) hi = h;
  });
  var engB = sym.engineB || {}, engD = sym.engineD || {}, vp = engD.vp || {}, levels = sym.levels || {};
  [levels.entry || engB.entry, levels.sl || engB.sl, levels.tp1 || levels.tp || engB.tp, levels.tp2, vp.poc, vp.vah, vp.val, sym.latest_price].forEach(function(v) {
    if (v != null) { var pv = parseFloat(v); if (!isNaN(pv)) { lo = Math.min(lo, pv); hi = Math.max(hi, pv); } }
  });
  if (lo === Infinity || hi === -Infinity || lo === hi) {
    return '<svg class="ld-chart-svg" viewBox="0 0 300 110"><text x="150" y="60" text-anchor="middle" fill="#4a5568" font-family="monospace" font-size="10">RANGE ZERO</text></svg>';
  }
  var pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  var range = hi - lo;

  var W = 300, H = 108, mL = 2, mR = 36, mT = 5, mB = 5;
  var plotW = W - mL - mR, plotH = H - mT - mB;
  var candleW = Math.max(2, Math.floor(plotW / n) - 1);
  var gap = plotW / n;

  function py(price) { return mT + plotH - plotH * (price - lo) / range; }

  var parts = ['<svg class="ld-chart-svg" viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg">'];
  parts.push('<rect width="' + W + '" height="' + H + '" fill="rgba(5,9,20,0.85)"/>');

  // Horizontal grid lines at 25/50/75%
  [0.25, 0.50, 0.75].forEach(function(f) {
    var gy = mT + plotH * (1 - f);
    parts.push('<line x1="' + mL + '" y1="' + gy + '" x2="' + (W - mR) + '" y2="' + gy + '" stroke="rgba(34,211,238,0.06)" stroke-width="0.5"/>');
  });

  // Zone band (entry↔sl)
  if ((levels.entry || engB.entry) != null && (levels.sl || engB.sl) != null) {
    var zoneLo = Math.min(parseFloat(levels.entry || engB.entry), parseFloat(levels.sl || engB.sl));
    var zoneHi = Math.max(parseFloat(levels.entry || engB.entry), parseFloat(levels.sl || engB.sl));
    var zy1 = py(zoneHi), zy2 = py(zoneLo);
    if (!isNaN(zy1) && !isNaN(zy2) && zy2 > zy1) {
      parts.push('<rect x="' + mL + '" y="' + zy1 + '" width="' + plotW + '" height="' + (zy2-zy1) + '" fill="rgba(240,185,11,0.06)"/>');
    }
  }
  // VP band (vah↔val)
  if (vp.vah != null && vp.val != null) {
    var vpy1 = py(parseFloat(vp.vah)), vpy2 = py(parseFloat(vp.val));
    if (!isNaN(vpy1) && !isNaN(vpy2) && vpy2 > vpy1) {
      parts.push('<rect x="' + mL + '" y="' + vpy1 + '" width="' + plotW + '" height="' + (vpy2-vpy1) + '" fill="rgba(34,211,238,0.04)"/>');
    }
  }

  // Level lines with right-side labels
  function levelLine(price, color, dash, lbl) {
    if (price == null) return;
    var pv = parseFloat(price); if (isNaN(pv)) return;
    var ly = py(pv);
    if (ly < mT || ly > H - mB) return;
    var da = dash ? ' stroke-dasharray="' + dash + '"' : '';
    parts.push('<line x1="' + mL + '" y1="' + ly + '" x2="' + (W - mR) + '" y2="' + ly + '" stroke="' + color + '" stroke-width="0.8"' + da + '/>');
    parts.push('<text x="' + (W - mR + 2) + '" y="' + (ly + 3) + '" fill="' + color + '" font-family="monospace" font-size="6.5">' + ldEsc(lbl) + '</text>');
  }
  levelLine(levels.tp1 || levels.tp || engB.tp, 'rgba(14,203,129,0.7)', '4,2', 'TP1');
  levelLine(levels.tp2, 'rgba(14,203,129,0.45)', '2,3', 'TP2');
  levelLine(levels.entry || engB.entry, 'rgba(240,185,11,0.8)', null, 'ENT');
  levelLine(levels.sl || engB.sl, 'rgba(246,70,93,0.7)', '4,2', 'SL');
  levelLine(vp.poc,     'rgba(34,211,238,0.6)',  '2,3', 'POC');
  levelLine(vp.vah,     'rgba(34,211,238,0.35)', '1,4', 'VAH');
  levelLine(vp.val,     'rgba(34,211,238,0.35)', '1,4', 'VAL');
  // Paper position
  var paper = sym.paper || {};
  if (paper.hasOpenPaperPosition && paper.entry != null) {
    levelLine(paper.entry, 'rgba(240,185,11,0.95)', null, 'POS');
  }

  // Candlesticks
  candles.forEach(function(c, i) {
    var o = parseFloat(c.open), cl = parseFloat(c.close);
    var h2 = parseFloat(c.high), l2 = parseFloat(c.low);
    if (isNaN(o) || isNaN(cl) || isNaN(h2) || isNaN(l2)) return;
    var bull = cl >= o;
    var color = bull ? '#0ecb81' : '#f6465d';
    var cx = mL + i * gap + gap / 2;
    var bodyTop = py(bull ? cl : o);
    var bodyBot = py(bull ? o : cl);
    var bodyH = Math.max(1, bodyBot - bodyTop);
    parts.push('<line x1="' + cx + '" y1="' + py(h2) + '" x2="' + cx + '" y2="' + py(l2) + '" stroke="' + color + '" stroke-width="0.8" opacity="0.6"/>');
    parts.push('<rect x="' + (cx - candleW/2) + '" y="' + bodyTop + '" width="' + candleW + '" height="' + bodyH + '" fill="' + color + '" opacity="0.8"/>');
  });

  // Live price line + label
  if (sym.latest_price != null) {
    var livePrice = parseFloat(sym.latest_price);
    if (!isNaN(livePrice)) {
      var liveY = py(livePrice);
      if (liveY >= mT && liveY <= H - mB) {
        parts.push('<line x1="' + mL + '" y1="' + liveY + '" x2="' + (W - mR) + '" y2="' + liveY + '" stroke="rgba(240,185,11,0.8)" stroke-width="1" stroke-dasharray="2,2"/>');
        parts.push('<text x="' + (W - mR + 2) + '" y="' + (liveY + 3) + '" fill="#f0b90b" font-family="monospace" font-size="6.5" font-weight="700">LIVE</text>');
      }
    }
  }

  // TF label
  parts.push('<text x="' + (mL + 2) + '" y="' + (mT + 8) + '" fill="rgba(34,211,238,0.4)" font-family="monospace" font-size="7">' + ldEsc(sym.timeframe || 'H4') + '</text>');
  parts.push('</svg>');
  return parts.join('');
}

// ── Legacy pill builders — kept for backward compat and tests ─────────────────
function ldBuildEnginePill(label, eng) {
  eng = eng || {};
  var passed = eng.passed || eng.confidencePassed;
  var dir = eng.direction || '';
  var cls = passed ? 'pass' : 'miss';
  var text = label + (dir ? ' ' + dir : '') + (passed ? ' ✓' : '');
  return '<span class="ld-pill ' + cls + '" title="Engine ' + label + ' score: ' + (eng.score || '—') + '">' + ldEsc(text) + '</span>';
}
function ldBuildEngineCPill(engC) {
  engC = engC || {};
  var state = engC.decisionState || 'NO_SETUP';
  var cls = 'miss';
  if (state === 'ALIGNED') cls = 'aligned-c';
  else if (state === 'CONFLICT') cls = 'conflict-c';
  else if (state === 'A_ONLY' || state === 'B_ONLY' || state === 'WATCHLIST') cls = 'watch';
  return '<span class="ld-pill ' + cls + '">' + ldEsc('C:' + state) + '</span>';
}
function ldBuildFreshnessPill(fr) {
  fr = fr || {};
  var status = fr.consistencyStatus || 'DATA_MISSING';
  var gate   = fr.gateDecision || 'BLOCK';
  var cls = 'miss', label = status;
  if (status === 'OK') { cls = 'fresh-ok'; label = '✓ OK'; }
  else if (status === 'CONFIRMED_ONLY_OK') { cls = 'fresh-conf'; label = 'CONF-ONLY OK'; }
  else if (gate === 'BLOCK') { cls = 'fresh-bad'; }
  return '<span class="ld-pill ' + cls + '" title="' + ldEsc(status) + '">' + ldEsc(label) + '</span>';
}
function ldBuildEngineDPill(engD) {
  engD = engD || {};
  var gate = engD.gateResult || 'DATA_MISSING', grade = engD.grade || '';
  var cls = gate === 'PASS' ? 'pass' : gate === 'WATCHLIST' ? 'watch' : gate === 'BLOCKED' ? 'block' : 'miss';
  return '<span class="ld-pill ' + cls + '">' + ldEsc('D:' + gate + (grade ? ' ' + grade : '')) + '</span>';
}

// ── Card selection & detail panel ─────────────────────────────────────────────
function ldSelectCard(sym) {
  _ldSelectedSym = sym.symbol;
  document.querySelectorAll('#ldCardGrid .ld-card').forEach(function(el) {
    el.classList.toggle('selected', el.getAttribute('data-sym') === _ldSelectedSym);
  });
  ldShowDetail(sym);
}
window.ldSelectCard = ldSelectCard;

function ldShowDetail(sym) {
  var sidePanel   = document.getElementById('ldSidePanel');
  var detailPanel = document.getElementById('ldDetailPanel');
  if (sidePanel)   sidePanel.style.display   = '';
  if (detailPanel) detailPanel.style.display = 'block';
  ldUpdateDetailPrice(sym);
  ldRenderDetailTab(sym, _ldCurrentTab);
}
window.ldShowDetail = ldShowDetail;

function ldUpdateDetailPrice(sym) {
  var symEl   = document.getElementById('ldDetailSym');
  var priceEl = document.getElementById('ldDetailPrice');
  if (symEl)   symEl.textContent   = sym.symbol || '—';
  if (priceEl) priceEl.textContent = sym.latest_price != null ? ldFmtPrice(sym.latest_price) : '—';
}

function ldCloseDetail() {
  _ldSelectedSym = null;
  _ldCurrentTab  = 'overview';
  var sidePanel   = document.getElementById('ldSidePanel');
  var detailPanel = document.getElementById('ldDetailPanel');
  if (sidePanel)   sidePanel.style.display   = '';
  if (detailPanel) detailPanel.style.display = 'none';
  document.querySelectorAll('#ldCardGrid .ld-card').forEach(function(el){ el.classList.remove('selected'); });
}
window.ldCloseDetail = ldCloseDetail;

function ldShowTab(tab) {
  _ldCurrentTab = tab;
  document.querySelectorAll('#ldDetailTabs .ld-tab').forEach(function(btn) {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tab);
  });
  if (!_ldLastSnapshot || !_ldSelectedSym) return;
  var sym = (_ldLastSnapshot.symbols||[]).find(function(s){ return s.symbol === _ldSelectedSym; });
  if (sym) ldRenderDetailTab(sym, tab);
}
window.ldShowTab = ldShowTab;

function ldRenderDetailTab(sym, tab) {
  var body = document.getElementById('ldDetailBody');
  if (!body) return;
  var html = '';
  tab = tab || 'overview';
  if      (tab === 'overview')     html = ldTabOverview(sym);
  else if (tab === 'levels')       html = ldTabLevels(sym);
  else if (tab === 'engine-a')     html = ldTabEngineA(sym);
  else if (tab === 'engine-b')     html = ldTabEngineB(sym);
  else if (tab === 'engine-c')     html = ldTabEngineC(sym);
  else if (tab === 'engine-d')     html = ldTabEngineD(sym);
  else if (tab === 'ai')           html = ldTabAI(sym);
  else if (tab === 'execute')      html = ldTabExecute(sym);
  else if (tab === 'diagnostics')  html = ldTabDiagnostics(sym);
  body.innerHTML = html;
  if (tab === 'execute') {
    var btn = document.getElementById('ldExecPaperBtn');
    if (btn) btn.onclick = function(){ ldExecutePaper(sym); };
  }
}

// ── Tab content renderers ─────────────────────────────────────────────────────
function ldTabOverview(sym) {
  var a = sym.engineA||{}, b = sym.engineB||{}, c = sym.engineC||{}, d = sym.engineD||{}, fr = sym.freshness||{};
  var ex = sym.executableState || {};
  return '<div class="ld-section-head">Control State</div>'
    + ldKV('Final State', sym.finalState || 'NO SETUP', sym.finalState==='PAPER CANDIDATE'?'bull':sym.finalState==='WATCHLIST'?'warn':sym.finalState==='BLOCKED'?'bear':'')
    + ldKV('Main Reason', sym.mainReason || 'not verified')
    + ldKV('Block Reason', sym.blockReason || ex.disabledReason || 'none', (sym.blockReason||ex.disabledReason)?'bear':'')
    + '<div class="ld-section-head">Market</div>'
    + ldKV('Symbol', sym.symbol)
    + ldKV('Asset Type', sym.asset_type)
    + ldKV('Source', sym.source)
    + ldKV('Timeframe', sym.timeframe)
    + ldKV('Live Price', sym.latest_price != null ? ldFmtPrice(sym.latest_price) : '—')
    + ldKV('Change', sym.change_pct != null ? (parseFloat(sym.change_pct)>=0?'+':'') + parseFloat(sym.change_pct).toFixed(2)+'%' : '—',
            sym.change_pct != null ? (parseFloat(sym.change_pct)>=0 ? 'bull' : 'bear') : '')
    + '<div class="ld-section-head">Engine Summary</div>'
    + ldKV('Engine A Dir',    a.direction || '—', a.direction==='LONG'?'bull':a.direction==='SHORT'?'bear':'')
    + ldKV('Engine A Score',  a.score != null ? parseFloat(a.score).toFixed(2) : '—', a.passed?'bull':'')
    + ldKV('Engine B Passed', b.confidencePassed===true?'YES':'NO', b.confidencePassed===true?'bull':'bear')
    + ldKV('Engine C State',  c.decisionState||'NO_SETUP', c.decisionState==='ALIGNED'?'bull':c.decisionState==='CONFLICT'?'bear':'')
    + ldKV('Conviction', c.conviction != null ? parseFloat(c.conviction).toFixed(2) : (a.conviction != null ? a.conviction : '---'))
    + ldKV('RR', (sym.levels||{}).rr != null ? parseFloat((sym.levels||{}).rr).toFixed(2) : '---', 'cyan')
    + ldKV('Engine D Gate',   (d.gateResult||'—') + (d.grade?' '+d.grade:''), d.gateResult==='PASS'?'bull':'')
    + '<div class="ld-section-head">Safety</div>'
    + ldKV('Freshness Gate', fr.gateDecision||'—', fr.gateDecision==='ALLOW'?'bull':'bear')
    + ldKV('Paper Position', (sym.paperPosition||sym.paper||{}).hasOpenPaperPosition ? 'OPEN' : 'NONE', (sym.paperPosition||sym.paper||{}).hasOpenPaperPosition?'warn':'')
    + ldKV('Paper Execute', ex.canPaperExecute ? 'ENABLED' : 'DISABLED', ex.canPaperExecute?'bull':'bear')
    + ldKV('Real Orders', ex.canRealExecute ? 'ENABLED' : 'BLOCKED', ex.canRealExecute?'bear':'bull');
}

function ldTabLevels(sym) {
  var b = sym.engineB||{}, d = sym.engineD||{}, vp = d.vp||{};
  return '<div class="ld-section-head">Engine B Levels</div>'
    + ldKV('Entry',     b.entry != null ? ldFmtPrice(b.entry) : '—', 'warn')
    + ldKV('Stop Loss', b.sl    != null ? ldFmtPrice(b.sl)    : '—', 'bear')
    + ldKV('Take Profit', b.tp  != null ? ldFmtPrice(b.tp)    : '—', 'bull')
    + ldKV('R:R', b.rr != null ? parseFloat(b.rr).toFixed(2) : '—', 'cyan')
    + (vp.poc != null ? '<div class="ld-section-head">Engine D VP Levels</div>'
      + ldKV('POC', ldFmtPrice(vp.poc), 'cyan')
      + ldKV('VAH', vp.vah != null ? ldFmtPrice(vp.vah) : '—')
      + ldKV('VAL', vp.val != null ? ldFmtPrice(vp.val) : '—')
      + ldKV('Market State', d.marketState || '—') : '');
}

function ldTabEngineA(sym) {
  var a = sym.engineA||{}, fs = a.factorScores||{};
  return '<div class="ld-section-head">Engine A v2 — Factor Scores</div>'
    + ldKV('Direction',  a.direction||'—', a.direction==='LONG'?'bull':a.direction==='SHORT'?'bear':'')
    + ldKV('Score',      a.score != null ? parseFloat(a.score).toFixed(3) : '—')
    + ldKV('Max Score',  a.maxScore != null ? a.maxScore : '—')
    + ldKV('Threshold',  a.threshold != null ? a.threshold : '—')
    + ldKV('Passed',     a.passed===true?'YES':'NO', a.passed===true?'bull':'bear')
    + ldKV('Conviction', a.conviction||'—')
    + '<div class="ld-section-head">Factor Breakdown</div>'
    + ldKV('Trend',    fs.trend    != null ? parseFloat(fs.trend).toFixed(3)    : '—')
    + ldKV('Momentum', fs.momentum != null ? parseFloat(fs.momentum).toFixed(3) : '—')
    + ldKV('Addon',    fs.addon    != null ? parseFloat(fs.addon).toFixed(3)    : '—')
    + '<div class="ld-section-head">Indicators</div>'
    + ldKV('ADX Value',       a.adxValue != null ? parseFloat(a.adxValue).toFixed(1) : '—')
    + ldKV('Trend Coherence', a.trendCoherence != null ? parseFloat(a.trendCoherence).toFixed(3) : '—')
    + ldKV('Session Mult',    a.sessionMultiplier != null ? parseFloat(a.sessionMultiplier).toFixed(2) : '—')
    + (a.failReasons && a.failReasons.length
        ? '<div class="ld-section-head">Fail Reasons</div>' + a.failReasons.map(function(r){
            return '<div style="color:#f6465d;font-size:9px;padding:2px 0;">• ' + ldEsc(r) + '</div>'; }).join('') : '');
}

function ldTabEngineB(sym) {
  var b = sym.engineB||{};
  var hf = b.hardFailReasons||[], sw = b.softWarnings||[], dn = b.diagnosticNotes||[];
  return '<div class="ld-section-head">Engine B — Naked Structure</div>'
    + ldKV('Confidence Passed', b.confidencePassed===true?'YES':'NO', b.confidencePassed===true?'bull':'bear')
    + ldKV('Direction', b.direction||'—', b.direction==='LONG'?'bull':b.direction==='SHORT'?'bear':'')
    + ldKV('Entry', b.entry != null ? ldFmtPrice(b.entry) : '—', 'warn')
    + ldKV('SL',    b.sl    != null ? ldFmtPrice(b.sl)    : '—', 'bear')
    + ldKV('TP',    b.tp    != null ? ldFmtPrice(b.tp)    : '—', 'bull')
    + ldKV('RR',    b.rr    != null ? parseFloat(b.rr).toFixed(2) : '—', 'cyan')
    + '<div class="ld-section-head">Hard Failures</div>'
    + (hf.length ? hf.map(function(r){ return '<div style="color:#f6465d;font-size:9px;padding:2px 0;">✗ ' + ldEsc(r) + '</div>'; }).join('') : '<div style="color:var(--muted);font-size:9px;">None</div>')
    + '<div class="ld-section-head">Soft Warnings</div>'
    + (sw.length ? sw.map(function(r){ return '<div style="color:#f0b90b;font-size:9px;padding:2px 0;">⚠ ' + ldEsc(r) + '</div>'; }).join('') : '<div style="color:var(--muted);font-size:9px;">None</div>')
    + '<div class="ld-section-head">Diagnostic Notes</div>'
    + (dn.length ? dn.map(function(r){ return '<div style="color:var(--muted);font-size:9px;padding:2px 0;">• ' + ldEsc(r) + '</div>'; }).join('') : '<div style="color:var(--muted);font-size:9px;">None</div>');
}

function ldTabEngineC(sym) {
  var c = sym.engineC||{};
  var allStates = ['ALIGNED','A_ONLY','B_ONLY','CONFLICT','WATCHLIST','BLOCKED','NO_SETUP'];
  var stateRow = allStates.map(function(st) {
    var active = c.decisionState === st;
    var col = active ? (st==='ALIGNED'?'#0ecb81':st==='CONFLICT'||st==='BLOCKED'?'#f6465d':'#f0b90b') : 'rgba(159,176,197,0.2)';
    return '<span style="margin-right:5px;color:' + col + ';font-size:8px;font-weight:' + (active?'700':'400') + ';">' + st + '</span>';
  }).join('');
  return '<div class="ld-section-head">Engine C — Consensus</div>'
    + '<div style="padding:5px 0 8px;">' + stateRow + '</div>'
    + ldKV('Decision State', c.decisionState||'NO_SETUP', c.decisionState==='ALIGNED'?'bull':c.decisionState==='CONFLICT'?'bear':'')
    + ldKV('Tier',           c.tier||'—')
    + ldKV('Trade Allowed',  'NO — Paper/Demo only', 'bear')
    + ldKV('Reason',         c.reason||'—');
}

function ldTabEngineD(sym) {
  var d = sym.engineD||{}, vp = d.vp||{};
  return '<div class="ld-section-head">Engine D — Scalp VP</div>'
    + ldKV('Gate Result', d.gateResult||'NO_DATA', d.gateResult==='PASS'?'bull':d.gateResult==='BLOCKED'?'bear':'')
    + ldKV('AI Grade',    d.grade||'—', d.grade==='A'||d.grade==='B'?'bull':d.grade==='C'?'warn':'bear')
    + ldKV('Setup Type',  d.setupType||'—')
    + ldKV('Direction',   d.direction||'—', d.direction==='LONG'?'bull':d.direction==='SHORT'?'bear':'')
    + '<div class="ld-section-head">Volume Profile</div>'
    + ldKV('POC', vp.poc != null ? ldFmtPrice(vp.poc) : '—', 'cyan')
    + ldKV('VAH', vp.vah != null ? ldFmtPrice(vp.vah) : '—')
    + ldKV('VAL', vp.val != null ? ldFmtPrice(vp.val) : '—')
    + ldKV('Market State',  d.marketState||'—')
    + ldKV('CVD Available', d.cvdAvailable===true?'YES':'NO')
    + ldKV('CVD Bias', d.cvdBias || '---')
    + ldKV('Absorption', d.absorptionDetected===true?'YES':'NO')
    + ldKV('Spread', d.spread != null ? d.spread : '---')
    + ldKV('RR', d.rr != null ? parseFloat(d.rr).toFixed(2) : '---')
    + '<div class="ld-section-head">Missing Data</div>'
    + ldList(d.missingData || [])
    + '<div class="ld-section-head">Fail Reasons</div>'
    + ldList(d.failReasons || [])
    + '<div class="ld-section-head">Diagnostic Notes</div>'
    + ldList(d.diagnosticNotes || [])
    + (d.softWarnings && d.softWarnings.length
        ? '<div class="ld-section-head">Soft Warnings</div>' + d.softWarnings.map(function(w){
            return '<div style="color:#f0b90b;font-size:9px;padding:2px 0;">⚠ ' + ldEsc(w) + '</div>'; }).join('') : '');
}

function ldTabAI(sym) {
  var a = sym.engineA||{};
  return '<div class="ld-section-head">AI Context</div>'
    + '<div style="color:rgba(159,176,197,0.5);font-size:9px;line-height:1.6;padding:6px 0;">'
    + 'AI narrative is populated from the last full Engine A/B scan.<br>'
    + 'Run a scan then check the signal panel for Marcus Reid evidence review.<br><br>'
    + 'Conviction: <strong style="color:#22d3ee">' + ldEsc(a.conviction||'—') + '</strong><br>'
    + 'Score: <strong style="color:#22d3ee">' + (a.score != null ? parseFloat(a.score).toFixed(3) : '—') + '</strong>'
    + '</div>';
}

function ldTabExecute(sym) {
  var c = sym.engineC||{}, a = sym.engineA||{}, b = sym.engineB||{}, l = sym.levels||{}, ex = sym.executableState||{};
  var canExec = ex.canPaperExecute === true;
  var dir   = a.direction || b.direction || '—';
  var entry = l.entry || b.entry || a.entry;
  var sl    = l.sl || b.sl || a.sl;
  var tp    = l.tp1 || l.tp || b.tp || a.tp;
  var rr    = l.rr || b.rr || a.rr;
  return '<div class="ld-exec-warn">'
    + '📄 <strong>PAPER MODE ONLY</strong> — This logs a paper trade entry.<br>'
    + 'No real orders will be placed. REAL_ORDERS_ALLOWED=false enforced server-side.'
    + '</div>'
    + '<div class="ld-section-head">Signal</div>'
    + ldKV('Symbol',      sym.symbol)
    + ldKV('Direction',   dir, dir==='LONG'?'bull':dir==='SHORT'?'bear':'')
    + ldKV('Entry',       entry != null ? ldFmtPrice(entry) : '—', 'warn')
    + ldKV('Stop Loss',   sl != null ? ldFmtPrice(sl) : '—', 'bear')
    + ldKV('Take Profit', tp != null ? ldFmtPrice(tp) : '—', 'bull')
    + ldKV('RR',          rr != null ? parseFloat(rr).toFixed(2) : '—', 'cyan')
    + ldKV('Suggested Qty', ex.volume || ex.suggestedQty || 'backend risk_check')
    + ldKV('Risk %', ex.riskPct || ex.riskStatus || 'NOT_CHECKED')
    + ldKV('Engine C',    c.decisionState||'NO_SETUP', c.decisionState==='ALIGNED'?'bull':'bear')
    + ldKV('Freshness',   (sym.freshness||{}).gateDecision||'—', canExec?'bull':'bear')
    + '<div class="ld-section-head">Gates</div>'
    + ldKV('Freshness Gate', ex.freshnessStatus || (sym.freshness||{}).gateDecision || '---', (sym.freshness||{}).gateDecision==='ALLOW'?'bull':'bear')
    + ldKV('Risk Gate', ex.riskStatus || 'NOT_CHECKED', ex.riskStatus==='OK'?'bull':ex.riskStatus==='NOT_CHECKED'?'warn':'bear')
    + ldKV('Paper Mode', ex.paperMode ? 'ON' : 'OFF', ex.paperMode?'bull':'bear')
    + ldKV('Real Orders', ex.realOrdersAllowed ? 'ALLOWED' : 'BLOCKED', ex.realOrdersAllowed?'bear':'bull')
    + ldKV('Disabled Reason', ex.disabledReason || 'none', ex.disabledReason?'bear':'bull')
    + '<button class="ld-exec-btn" type="button">ADD TO WATCHLIST</button>'
    + '<button class="ld-exec-btn" type="button">IGNORE</button>'
    + '<div id="ldExecResult"></div>'
    + '<button class="ld-exec-btn" id="ldExecPaperBtn"'
    + (!canExec ? ' disabled title="' + ldEsc(ex.disabledReason || 'Paper execute blocked') + '"' : '')
    + '>PAPER EXECUTE</button>';
}

function ldTabDiagnostics(sym) {
  var fr = sym.freshness||{};
  var snap = _ldLastSnapshot || {};
  return '<div class="ld-section-head">Payload</div>'
    + ldKV('payloadVersion', snap.payloadVersion || '---')
    + ldKV('source', sym.source || '---')
    + ldKV('offset hours', (sym.chart||{}).h4GridOffsetHours != null ? (sym.chart||{}).h4GridOffsetHours : '---')
    + '<div class="ld-section-head">Freshness</div>'
    + ldKV('Consistency Status', fr.consistencyStatus||'—', fr.gateDecision==='ALLOW'?'bull':'bear')
    + ldKV('Policy Status',      fr.policyStatus||'—')
    + ldKV('Gate Decision',      fr.gateDecision||'—', fr.gateDecision==='ALLOW'?'bull':'bear')
    + ldKV('Block Reason',       fr.blockReason||'none')
    + ldKV('Latest Candle',      fr.lastBarIso || (sym.chart||{}).latestConfirmedCandleIso || '---')
    + ldKV('Confirmed/Forming',  (sym.chart||{}).candlePolicy || '---')
    + ldKV('candleFetchMeta',    JSON.stringify((sym.chart||{}).candleFetchMeta || {}))
    + ldKV('candleConsistency',  JSON.stringify(fr.candleConsistency || {}))
    + ldKV('Age (secs)',          fr.ageSecs != null ? fr.ageSecs : '—')
    + '<div class="ld-section-head">Data Source</div>'
    + ldKV('Source',     sym.source||'—', 'cyan')
    + ldKV('Asset Type', sym.asset_type||'—')
    + ldKV('Timeframe',  sym.timeframe||'—')
    + ldKV('Bid',        sym.bid  != null ? ldFmtPrice(sym.bid)  : '—')
    + ldKV('Ask',        sym.ask  != null ? ldFmtPrice(sym.ask)  : '—')
    + ldKV('Spread',     sym.spread != null ? sym.spread : '—');
}

// ── Paper execute ─────────────────────────────────────────────────────────────
function ldExecutePaper(sym) {
  var btn = document.getElementById('ldExecPaperBtn');
  var resultEl = document.getElementById('ldExecResult');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Submitting...'; }
  var a = sym.engineA||{}, b = sym.engineB||{};
  var payload = {
    symbol:        sym.symbol,
    direction:     a.direction || b.direction || null,
    entry:         b.entry || a.entry || sym.latest_price || null,
    sl:            b.sl || null,
    tp:            b.tp || null,
    rr:            b.rr || null,
    engineC_state: (sym.engineC||{}).decisionState || null,
    asset_type:    sym.asset_type || null,
  };
  fetch('/api/live-dashboard/paper-execute', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  })
  .then(function(r){ return r.json(); })
  .then(function(d){
    if (resultEl) {
      if (d.ok) {
        resultEl.className = 'ld-exec-result ok';
        resultEl.innerHTML = '✓ Paper entry logged. Log ID: ' + ldEsc(String(d.log_id||'—'));
      } else {
        resultEl.className = 'ld-exec-result err';
        resultEl.innerHTML = '✗ ' + ldEsc(d.error || 'Unknown error');
      }
    }
    if (btn) { btn.disabled = !((sym.executableState||{}).canPaperExecute); btn.textContent = 'PAPER EXECUTE'; }
  })
  .catch(function(err){
    if (resultEl) { resultEl.className = 'ld-exec-result err'; resultEl.innerHTML = '✗ ' + ldEsc(String(err)); }
    if (btn) { btn.disabled = !((sym.executableState||{}).canPaperExecute); btn.textContent = 'PAPER EXECUTE'; }
  });
}
window.ldExecutePaper = ldExecutePaper;

// ── KV row helper ─────────────────────────────────────────────────────────────
function ldKV(key, val, valCls) {
  return '<div class="ld-kv">'
    + '<span class="ld-kv-key">' + ldEsc(key) + '</span>'
    + '<span class="ld-kv-val' + (valCls ? ' ' + valCls : '') + '">' + ldEsc(String(val != null ? val : '—')) + '</span>'
    + '</div>';
}

function ldRenderStatusTiles(d) {
  var el = document.getElementById('ldStatusTiles');
  if (!el) return;
  var symbols = d.symbols || [];
  var paperReady = symbols.filter(function(s){ return (s.executableState||{}).canPaperExecute === true; }).length;
  var blocked = symbols.filter(function(s){ return s.finalState === 'BLOCKED'; }).length;
  var fresh = d.freshnessAllOk !== false ? 'POLICY OK' : 'CHECK BLOCKS';
  el.innerHTML = ''
    + '<div class="ld-status-tile"><div class="k">Dashboard Status</div><div class="v">LIVE</div></div>'
    + '<div class="ld-status-tile"><div class="k">Freshness Gate</div><div class="v">' + ldEsc(fresh) + '</div></div>'
    + '<div class="ld-status-tile"><div class="k">Paper Candidates</div><div class="v">' + paperReady + '</div></div>'
    + '<div class="ld-status-tile"><div class="k">Blocked</div><div class="v">' + blocked + '</div></div>';
}

function ldList(items) {
  items = items || [];
  if (!items.length) return '<div style="color:var(--muted);font-size:9px;">None</div>';
  return items.map(function(x){
    return '<div style="color:var(--muted);font-size:9px;padding:2px 0;">- ' + ldEsc(x) + '</div>';
  }).join('');
}

// ── Event feed ────────────────────────────────────────────────────────────────
function ldRenderEventFeed(events) {
  var el = document.getElementById('ldEventFeed');
  if (!el) return;
  if (!events || !events.length) {
    el.innerHTML = '<div class="ld-no-data">No events yet.</div>';
    return;
  }
  var html = '';
  events.slice(0, 30).forEach(function(ev) {
    var sev = ev.severity || 'info';
    var ts  = ev.timestamp ? ev.timestamp.slice(11, 19) : '';
    html += '<div class="ld-event ' + ldEsc(sev) + '">'
      + '<span class="ld-event-sym">' + ldEsc(ev.symbol || '') + '</span>'
      + '<span class="ld-event-msg">' + ldEsc((ts ? '[' + ts + '] ' : '') + (ev.message || '')) + '</span>'
      + '</div>';
  });
  el.innerHTML = html;
}

// ── Engine D radar ────────────────────────────────────────────────────────────
function ldRenderScalpRadar(symbols) {
  var el = document.getElementById('ldScalpRadar');
  if (!el) return;
  var active = symbols.filter(function(s) {
    var g = (s.engineD||{}).gateResult;
    return g === 'PASS' || g === 'WATCHLIST';
  });
  if (!active.length) { el.innerHTML = '<div class="ld-no-data">No Engine D setups.</div>'; return; }
  var html = '';
  active.forEach(function(s) {
    var d = s.engineD||{};
    var grade = d.grade || '—';
    var gradeCls = (grade === 'A' || grade === 'B') ? 'pass' : grade === 'C' ? 'watch' : 'miss';
    html += '<div class="ld-scalp-row">'
      + '<span style="color:#e4e4e7;font-weight:700;font-size:10px;">' + ldEsc(s.symbol) + '</span>'
      + '<span class="ld-pill ' + gradeCls + '">' + ldEsc(grade) + '</span>'
      + '<span style="color:var(--muted);font-size:9px;">' + ldEsc(d.setupType || d.gateResult || '') + '</span>'
      + '</div>';
  });
  el.innerHTML = html;
}

// ── Paper soak summary ────────────────────────────────────────────────────────
function ldRenderPaperSummary(symbols, paperMode) {
  var el = document.getElementById('ldPaperSummary');
  if (!el) return;
  var openCount = symbols.filter(function(s){ return (s.paper||{}).hasOpenPaperPosition; }).length;
  var pm = paperMode || {};
  el.innerHTML = '<div style="font-size:9px;font-family:var(--mono);line-height:1.7;">'
    + '<div>Paper Mode: <strong style="color:' + (pm.enabled ? '#0ecb81' : '#f6465d') + '">' + (pm.enabled ? 'ON' : 'OFF') + '</strong></div>'
    + '<div>Real Orders: <strong style="color:' + (!pm.realOrdersAllowed ? '#0ecb81' : '#f6465d') + '">' + (pm.realOrdersAllowed ? 'ALLOWED ⚠' : 'BLOCKED ✓') + '</strong></div>'
    + '<div>Open Paper Pos: <strong style="color:#22d3ee">' + openCount + '</strong></div>'
    + '</div>';
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function ldEsc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function ldFmtPrice(p) {
  var n = parseFloat(p);
  if (isNaN(n)) return '—';
  if (n >= 10000) return n.toLocaleString(undefined, {maximumFractionDigits: 1});
  if (n >= 100)   return n.toLocaleString(undefined, {maximumFractionDigits: 2});
  if (n >= 1)     return n.toFixed(4);
  return n.toPrecision(5);
}

})(); // end Live Dashboard IIFE
