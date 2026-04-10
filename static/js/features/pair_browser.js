(function () {
  var state = window._pairBrowser = window._pairBrowser || {
    pairs: [],
    selectedSymbol: "",
    assetClass: "",
    query: "",
    style: "auto",
    directionMode: "auto",
    filters: { news: true, inter: true, graph: true, aiVision: true },
    engineA: null,
    engineB: null,
    compare: null,
    news: null,
    vision: null,
    intermarket: null,
    intermarketError: "",
    intermarketLoading: false,
    loadedPairs: false,
    loadingPairs: false,
  };

  function json(resp) {
    return window.safeJson ? window.safeJson(resp) : resp.json();
  }

  function selectedPair() {
    return (state.pairs || []).find(function (pair) {
      return pair.symbol === state.selectedSymbol;
    }) || null;
  }

  function activeSignal() {
    return state.engineA || null;
  }

  function activeEngineB() {
    return state.engineB || null;
  }

  function symbolId(symbol) {
    return String(symbol || "").replace(/[\/=^.]/g, "_");
  }

  function jsQuote(value) {
    return String(value == null ? "" : value)
      .replace(/\\/g, "\\\\")
      .replace(/'/g, "\\'");
  }

  function typeFromGroup(groupName) {
    var raw = String(groupName || "").toLowerCase();
    if (raw.indexOf("forex") >= 0) return "forex";
    if (raw.indexOf("crypto") >= 0) return "crypto";
    if (raw.indexOf("commodit") >= 0) return "commodity";
    if (raw.indexOf("indice") >= 0) return "index";
    if (raw.indexOf("etf") >= 0) return "etf";
    return "stock";
  }

  function resetResults() {
    state.engineA = null;
    state.engineB = null;
    state.compare = null;
    state.news = null;
    state.vision = null;
  }

  function setStatus(message, isError) {
    var el = document.getElementById("pairBrowserStatus");
    if (!el) return;
    el.textContent = String(message || "");
    el.style.color = isError ? "var(--bear)" : "var(--muted)";
  }

  function setButtonsDisabled(disabled) {
    [
      "pairBrowserRunA",
      "pairBrowserRunB",
      "pairBrowserRunCompare",
      "pairBrowserOpenChart",
      "pairBrowserOpenTV",
      "pairBrowserRunVision",
    ].forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.disabled = !!disabled;
    });
  }

  function buildSeed(requireDirection) {
    var pair = selectedPair();
    if (!pair) return null;

    var signal = activeSignal();
    var seed = signal
      ? window.buildLiveSignalPayload(signal)
      : {
          symbol: pair.symbol,
          pair: pair.display,
          display: pair.display,
          type: pair.type,
        };

    seed.symbol = seed.symbol || pair.symbol;
    seed.pair = seed.pair || pair.display;
    seed.display = seed.display || pair.display;
    seed.type = seed.type || pair.type;
    seed.style = state.style || "auto";

    if (state.directionMode === "LONG" || state.directionMode === "SHORT") {
      seed.direction = state.directionMode;
    } else if (signal && signal.direction) {
      seed.direction = signal.direction;
    } else if (requireDirection) {
      return null;
    }
    return seed;
  }

  async function runEngineA(options) {
    options = options || {};
    var pair = selectedPair();
    if (!pair) {
      window.showToast("Select a pair first.", true);
      return null;
    }

    var btn = document.getElementById("pairBrowserRunA");
    var prevText = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "ENGINE A...";
    }
    setStatus("Running Engine A for " + pair.display + "...", false);

    try {
      var resp = await fetch("/api/pair-scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: pair.symbol,
          style: state.style || "auto",
        }),
      });
      var data = await json(resp);
      if (!resp.ok) throw new Error(data.error || ("HTTP " + resp.status));

      state.engineA = data.signal || null;
      state.engineB = null;
      state.compare = null;
      state.vision = null;
      renderPairBrowser();
      setStatus("Engine A ready for " + pair.display + ".", false);
      if (!options.silent) window.showToast("Engine A scan ready for " + pair.display);
      return state.engineA;
    } catch (err) {
      setStatus("Engine A failed: " + err.message, true);
      if (!options.silent) window.showToast("Engine A failed: " + err.message, true);
      return null;
    } finally {
      if (btn) {
        btn.disabled = !selectedPair();
        btn.textContent = prevText || "ENGINE A SCAN";
      }
    }
  }

  window.runPairBrowserEngineA = function () {
    return runEngineA();
  };

  window.runPairBrowserEngineB = async function () {
    var pair = selectedPair();
    if (!pair) {
      window.showToast("Select a pair first.", true);
      return;
    }
    if (state.directionMode === "auto" && !state.engineA) {
      var baseSignal = await runEngineA({ silent: true });
      if (!baseSignal) return;
    }

    var seed = buildSeed(true);
    if (!seed) {
      window.showToast("Run Engine A first or choose LONG/SHORT for Engine B.", true);
      return;
    }

    var btn = document.getElementById("pairBrowserRunB");
    var prevText = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "ENGINE B...";
    }
    setStatus("Running Engine B for " + pair.display + "...", false);

    try {
      var resp = await fetch("/api/naked-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ signal: seed, force_ai: true }),
      });
      var data = await json(resp);
      if (!resp.ok || data.error) throw new Error(data.error || ("HTTP " + resp.status));

      state.engineB = data;
      state.compare = null;
      state.vision = null;
      window["_engineB_" + symbolId(pair.symbol)] = data;
      window["_nakedSig_" + symbolId(pair.symbol)] = seed;
      renderPairBrowser();
      setStatus("Engine B ready for " + pair.display + ".", false);
      window.showToast("Engine B scan ready for " + pair.display);
    } catch (err) {
      setStatus("Engine B failed: " + err.message, true);
      window.showToast("Engine B failed: " + err.message, true);
    } finally {
      if (btn) {
        btn.disabled = !selectedPair();
        btn.textContent = prevText || "ENGINE B SCAN";
      }
    }
  };

  window.runPairBrowserCompare = async function () {
    var pair = selectedPair();
    if (!pair) {
      window.showToast("Select a pair first.", true);
      return;
    }

    var seed =
      buildSeed(false) || {
        symbol: pair.symbol,
        pair: pair.display,
        display: pair.display,
        type: pair.type,
        style: state.style || "auto",
      };

    var btn = document.getElementById("pairBrowserRunCompare");
    var prevText = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "COMPARE...";
    }
    setStatus("Comparing Engine A and Engine B for " + pair.display + "...", false);

    try {
      var resp = await fetch("/api/compare-engines", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ signal: seed, style: state.style || "auto" }),
      });
      var data = await json(resp);
      if (!resp.ok || data.error) throw new Error(data.error || ("HTTP " + resp.status));

      state.compare = data;
      state.engineA = data.engineA || state.engineA;
      state.engineB = data.engineB || state.engineB;
      state.vision = null;
      if (state.engineB) {
        window["_engineB_" + symbolId(pair.symbol)] = state.engineB;
      }
      renderPairBrowser();
      setStatus("Compare ready for " + pair.display + ".", false);
      window.showToast("Compare ready for " + pair.display);
    } catch (err) {
      setStatus("Compare failed: " + err.message, true);
      window.showToast("Compare failed: " + err.message, true);
    } finally {
      if (btn) {
        btn.disabled = !selectedPair();
        btn.textContent = prevText || "COMPARE A VS B";
      }
    }
  };

  window.runPairBrowserNews = async function () {
    var pair = selectedPair();
    if (!pair) {
      window.showToast("Select a pair first.", true);
      return;
    }
    setStatus("Loading news sentiment for " + pair.display + "...", false);
    try {
      var resp = await fetch("/api/news-sentiment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: pair.symbol }),
      });
      var data = await json(resp);
      if (!resp.ok || data.error) throw new Error(data.error || ("HTTP " + resp.status));
      state.news = data;
      renderPairBrowserSections();
      setStatus("News sentiment ready for " + pair.display + ".", false);
      window.showToast("News sentiment ready for " + pair.display);
    } catch (err) {
      setStatus("News sentiment failed: " + err.message, true);
      window.showToast("News sentiment failed: " + err.message, true);
    }
  };

  window.runPairBrowserVision = async function () {
    var pair = selectedPair();
    if (!pair) {
      window.showToast("Select a pair first.", true);
      return;
    }

    var signal = state.engineA;
    if (!signal) {
      signal = await runEngineA({ silent: true });
      if (!signal) return;
    }

    var btn = document.getElementById("pairBrowserRunVision");
    var prevText = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "AI VISION...";
    }
    setStatus("Running chart AI for " + pair.display + "...", false);

    try {
      var resp = await fetch("/api/chart-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: signal.symbol,
          tf: "H4",
          signal: window.buildLiveSignalPayload(signal),
          engineB: activeEngineB(),
          server_render: true,
          chart_source: "pair_browser",
          candles: signal.h4Candles || [],
          candles_d1: signal.d1Candles || [],
          candles_h1: signal.h1Candles || [],
          entry: window.getSignalLivePrice(signal) || signal.price,
          sl: signal.sl,
          tp: signal.tp1,
        }),
      });
      var data = await json(resp);
      if (!resp.ok || data.error) throw new Error(data.error || ("HTTP " + resp.status));

      state.vision = data;
      if (state.engineA) window._persistChartAiResult(state.engineA, data);
      if (state.compare && state.compare.engineA) window._persistChartAiResult(state.compare.engineA, data);
      renderPairBrowserSections();
      setStatus("AI vision ready for " + pair.display + ".", false);
      window.showToast("AI vision ready for " + pair.display);
    } catch (err) {
      setStatus("AI vision failed: " + err.message, true);
      window.showToast("AI vision failed: " + err.message, true);
    } finally {
      if (btn) {
        btn.disabled = !selectedPair();
        btn.textContent = prevText || "AI VISION";
      }
    }
  };

  window.openPairBrowserChart = function (tf) {
    var pair = selectedPair();
    if (!pair) {
      window.showToast("Select a pair first.", true);
      return;
    }
    var signal =
      buildSeed(false) || {
        symbol: pair.symbol,
        pair: pair.display,
        display: pair.display,
        type: pair.type,
      };
    window.openAcm(pair.symbol, tf || "H4", signal);
  };

  window.openPairBrowserTV = function () {
    var pair = selectedPair();
    if (!pair) {
      window.showToast("Select a pair first.", true);
      return;
    }
    window.openTVChart(pair.display, "240");
  };

  function renderList() {
    var listEl = document.getElementById("pairBrowserList");
    var countEl = document.getElementById("pairBrowserCount");
    if (!listEl) return;

    var needle = String(state.query || "").trim().toLowerCase();
    var rows = (state.pairs || []).filter(function (pair) {
      if (state.assetClass && pair.type !== state.assetClass) return false;
      if (!needle) return true;
      return (
        String(pair.display || "").toLowerCase().indexOf(needle) >= 0 ||
        String(pair.symbol || "").toLowerCase().indexOf(needle) >= 0
      );
    });

    if (countEl) {
      countEl.textContent = rows.length + " pair" + (rows.length === 1 ? "" : "s");
    }

    if (!rows.length) {
      listEl.innerHTML = '<div class="empty-msg">No pairs match the current filters.</div>';
      return;
    }

    listEl.innerHTML = rows
      .map(function (pair) {
        var active = pair.symbol === state.selectedSymbol ? " active" : "";
        var disabled = pair.enabled ? "" : " disabled";
        var pillCls = pair.enabled ? "pair-browser-pill" : "pair-browser-pill watch";
        return (
          '<button type="button" class="pair-browser-item' +
          active +
          disabled +
          '" onclick="selectPairBrowserPair(\'' +
          jsQuote(pair.symbol) +
          "')\">" +
          "<div>" +
          '<div class="pair-browser-item-title">' +
          window.escSigHtml(pair.display) +
          "</div>" +
          '<div class="pair-browser-item-meta">' +
          window.escSigHtml(pair.symbol) +
          " | " +
          window.escSigHtml(pair.type.toUpperCase()) +
          " | " +
          window.escSigHtml(pair.group) +
          "</div>" +
          "</div>" +
          '<span class="' +
          pillCls +
          '">' +
          (pair.enabled ? "ACTIVE" : "WATCH") +
          "</span>" +
          "</button>"
        );
      })
      .join("");
  }

  function renderHeader() {
    var header = document.getElementById("pairBrowserHeader");
    if (!header) return;
    var pair = selectedPair();
    if (!pair) {
      header.className = "pair-browser-header empty";
      header.innerHTML =
        '<div class="pair-browser-title">Select a pair</div>' +
        '<div class="pair-browser-note">Choose a tracked pair from the left to run Engine A, Engine B, compare both engines, open the full chart, or request AI vision feedback.</div>';
      return;
    }

    var signal = activeSignal();
    var live = signal ? window.getSignalLivePrice(signal) : null;
    if (live == null) live = ((window._latestPrices || {})[pair.display] || {}).price;

    var status = pair.enabled
      ? '<span class="pair-browser-pill">ACTIVE</span>'
      : '<span class="pair-browser-pill watch">WATCH</span>';
    var bits = [pair.type.toUpperCase(), pair.symbol, pair.group];
    if (signal && signal.direction) bits.push(signal.direction);
    if (signal && (signal.regimeName || signal.trendState)) bits.push(signal.regimeName || signal.trendState);

    header.className = "pair-browser-header";
    header.innerHTML =
      "<div>" +
      '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">' +
      '<div class="pair-browser-title">' +
      window.escSigHtml(pair.display) +
      "</div>" +
      status +
      "</div>" +
      '<div class="pair-browser-sub">' +
      bits.map(window.escSigHtml).join(" | ") +
      "</div>" +
      '<div class="pair-browser-sub">Style: ' +
      window.escSigHtml(String(state.style || "auto").toUpperCase()) +
      " | Engine B direction: " +
      window.escSigHtml(String(state.directionMode || "auto").toUpperCase()) +
      "</div>" +
      "</div>" +
      "<div>" +
      '<div class="pair-browser-price">' +
      (live != null ? window.fmtPriceForSig(live, signal || pair) : "n/a") +
      "</div>" +
      '<div class="pair-browser-sub" style="text-align:right;">Latest visible price</div>' +
      "</div>";
  }

  function esc(value) {
    return window.escSigHtml ? window.escSigHtml(value == null ? "" : String(value)) : String(value == null ? "" : value);
  }

  function visiblePairs() {
    var needle = String(state.query || "").trim().toLowerCase();
    return (state.pairs || []).filter(function (pair) {
      if (state.assetClass && pair.type !== state.assetClass) return false;
      if (!needle) return true;
      return String(pair.display || "").toLowerCase().indexOf(needle) >= 0 || String(pair.symbol || "").toLowerCase().indexOf(needle) >= 0;
    });
  }

  function activeVision() {
    return state.vision || (state.engineA && state.engineA.chart_ai) || (state.compare && state.compare.engineA && state.compare.engineA.chart_ai) || null;
  }

  function fmtPriceFor(value, sig) {
    if (value == null || value === "") return "n/a";
    return window.fmtPriceForSig ? window.fmtPriceForSig(value, sig || selectedPair() || {}) : String(value);
  }

  function fmtNum(value, digits) {
    var num = Number(value);
    return isFinite(num) ? num.toFixed(digits == null ? 2 : digits) : "n/a";
  }

  function metric(label, value, color) {
    return '<div class="pair-browser-stat"><div class="pair-browser-stat-label">' + esc(label) + '</div><div class="pair-browser-stat-val"' + (color ? ' style="color:' + color + ';"' : "") + ">" + esc(value == null || value === "" ? "n/a" : String(value)) + "</div></div>";
  }

  function section(title, body) {
    return '<section class="pair-browser-section"><h3>' + esc(title) + "</h3>" + body + "</section>";
  }

  function toneForDirection(direction) {
    if (direction === "LONG") return "var(--bull)";
    if (direction === "SHORT") return "var(--bear)";
    return "var(--accent)";
  }

  function engineBStop(res) {
    return res && (res.recommended_stop_loss != null ? res.recommended_stop_loss : (res.stop_loss != null ? res.stop_loss : res.sl));
  }

  function engineBTarget(res) {
    return res && (res.recommended_take_profit != null ? res.recommended_take_profit : (res.take_profit != null ? res.take_profit : (res.tp1 != null ? res.tp1 : res.tp)));
  }

  async function loadIntermarketMatrix(force) {
    if (state.intermarketLoading) return;
    if (!force && state.intermarket) return;
    state.intermarketLoading = true;
    state.intermarketError = "";
    renderPairBrowserSections();
    try {
      var resp = await fetch("/api/intermarket-matrix?limit=200");
      var data = await json(resp);
      if (!resp.ok || data.error || data.success === false) throw new Error(data.error || ("HTTP " + resp.status));
      state.intermarket = data;
    } catch (err) {
      state.intermarket = null;
      state.intermarketError = err.message;
    } finally {
      state.intermarketLoading = false;
      renderPairBrowserSections();
    }
  }

  function renderEngineASection() {
    var pair = selectedPair();
    var signal = state.engineA;
    if (!pair) return "";
    if (!signal) return section("ENGINE A", '<div class="pair-browser-note">Run Engine A to load direction, confluence, levels, and attached signal context for this pair.</div>');
    var ai = signal.aiAnalysis && window.buildAIContent ? '<div style="margin-top:12px;">' + window.buildAIContent(signal.aiAnalysis, signal.symbol || pair.symbol, "pair-browser-a") + "</div>" : "";
    return section("ENGINE A",
      '<div class="pair-browser-grid">' +
        metric("Direction", signal.direction || "n/a", toneForDirection(signal.direction)) +
        metric("Confluence", window.formatConfluenceText ? window.formatConfluenceText(signal) : "n/a") +
        metric("Entry", fmtPriceFor(window.getSignalLivePrice(signal), signal)) +
        metric("Stop Loss", fmtPriceFor(signal.sl, signal)) +
        metric("Take Profit", fmtPriceFor(signal.tp1, signal)) +
        metric("Regime", signal.regimeName || signal.trendState || "n/a") +
        metric("Session", (signal.session && signal.session.name) || "n/a") +
        metric("Signal State", signal.is_forming ? "FORMING" : "CONFIRMED", signal.is_forming ? "var(--warn)" : "var(--bull)") +
      "</div><div style=\"margin-top:12px;\">" + (window.buildStyleLevelsTab ? window.buildStyleLevelsTab(signal) : '<div class="pair-browser-note">Style levels unavailable.</div>') + "</div>" + ai
    );
  }

  function renderEngineBSection() {
    var pair = selectedPair();
    var res = state.engineB;
    if (!pair) return "";
    if (!res) return section("ENGINE B", '<div class="pair-browser-note">Run Engine B to inspect live structure, confidence, and Engine B AI feedback for this pair.</div>');
    var ai = res.ai_analysis && window.buildAIContent ? '<div style="margin-top:12px;">' + window.buildAIContent(res.ai_analysis, pair.symbol, "pair-browser-b") + "</div>" : "";
    return section("ENGINE B",
      '<div class="pair-browser-grid">' +
        metric("Direction", res.direction || "n/a", toneForDirection(res.direction)) +
        metric("Structure", res.current_swing_sequence || "n/a") +
        metric("Macro", res.macro_swing_sequence || "n/a") +
        metric("Confluence", window.formatConfluenceText ? window.formatConfluenceText(res) : "n/a") +
        metric("Checklist", res.passed ? "PASS" : "FAIL", res.passed ? "var(--bull)" : "var(--bear)") +
        metric("Style", String(res.style || "n/a").toUpperCase()) +
        metric("Current Price", fmtPriceFor(res.current_price, res.type ? res : pair)) +
        metric("Stop Loss", fmtPriceFor(engineBStop(res), res.type ? res : pair)) +
        metric("Take Profit", fmtPriceFor(engineBTarget(res), res.type ? res : pair)) +
        metric("R:R", res.rr != null ? fmtNum(res.rr, 2) + "R" : "n/a") +
      "</div>" + ai
    );
  }

  function renderCompareSection() {
    var pair = selectedPair();
    var data = state.compare;
    if (!pair) return "";
    if (!data) return section("COMPARE", '<div class="pair-browser-note">Run Compare to refresh both engines live and verify whether Engine B aligns with Engine A.</div>');
    var sum = data.summary || {};
    var a = data.engineA || state.engineA || {};
    var b = data.engineB || state.engineB || {};
    return section("COMPARE",
      '<div class="pair-browser-grid">' +
        metric("Verdict", sum.verdict || "n/a", sum.verdict === "ALIGNED" ? "var(--bull)" : "var(--bear)") +
        metric("Same Direction", sum.sameDirection ? "YES" : "NO", sum.sameDirection ? "var(--bull)" : "var(--bear)") +
        metric("Structure Aligned", sum.structureAligned ? "YES" : "NO", sum.structureAligned ? "var(--bull)" : "var(--bear)") +
        metric("Macro Aligned", sum.macroAligned ? "YES" : "NO", sum.macroAligned ? "var(--bull)" : "var(--bear)") +
        metric("Engine A Style", String(sum.engineAStyle || "n/a").toUpperCase()) +
        metric("Engine B Style", String(sum.engineBStyle || "n/a").toUpperCase()) +
        metric("Engine B Min Score", sum.engineBMinScore != null ? String(sum.engineBMinScore) : "n/a") +
        metric("AI Review", sum.aiReviewIncluded ? "INCLUDED" : "NOT INCLUDED", sum.aiReviewIncluded ? "var(--bull)" : "var(--accent)") +
      '</div><div style="margin-top:12px;overflow:auto;"><table class="pair-browser-table"><thead><tr><th>Metric</th><th>Engine A</th><th>Engine B</th></tr></thead><tbody>' +
        "<tr><td>Direction</td><td>" + esc(a.direction || "n/a") + "</td><td>" + esc(b.direction || "n/a") + "</td></tr>" +
        "<tr><td>Structure</td><td>" + esc(a.direction || "n/a") + "</td><td>" + esc(b.current_swing_sequence || "n/a") + "</td></tr>" +
        "<tr><td>Entry</td><td>" + esc(fmtPriceFor(window.getSignalLivePrice(a), a)) + "</td><td>" + esc(fmtPriceFor(b.current_price, b.type ? b : pair)) + "</td></tr>" +
        "<tr><td>Stop Loss</td><td>" + esc(fmtPriceFor(a.sl, a)) + "</td><td>" + esc(fmtPriceFor(engineBStop(b), b.type ? b : pair)) + "</td></tr>" +
        "<tr><td>Take Profit</td><td>" + esc(fmtPriceFor(a.tp1, a)) + "</td><td>" + esc(fmtPriceFor(engineBTarget(b), b.type ? b : pair)) + "</td></tr>" +
        "<tr><td>Confluence</td><td>" + esc(window.formatConfluenceText ? window.formatConfluenceText(a) : "n/a") + "</td><td>" + esc(window.formatConfluenceText ? window.formatConfluenceText(b) : "n/a") + "</td></tr>" +
      "</tbody></table></div>"
    );
  }

  function renderNewsSection() {
    var pair = selectedPair();
    if (!pair || !state.filters.news) return "";
    if (!state.news) {
      return section("NEWS", '<div class="pair-browser-note">Load the structured news sentiment read for this pair using the existing backend endpoint.</div><div style="margin-top:10px;"><button type="button" class="bt-btn bt-btn-sm" onclick="runPairBrowserNews()">LOAD NEWS SENTIMENT</button></div>');
    }
    var s = state.news.structured || {};
    var themes = Array.isArray(s.key_themes) ? s.key_themes.filter(Boolean).join(", ") : "";
    return section("NEWS",
      '<div class="pair-browser-grid">' +
        metric("Direction", s.direction || "n/a", String(s.direction || "").toLowerCase() === "bullish" ? "var(--bull)" : String(s.direction || "").toLowerCase() === "bearish" ? "var(--bear)" : "var(--accent)") +
        metric("Sentiment Score", s.sentiment_score != null ? fmtNum(s.sentiment_score, 2) : "n/a") +
        metric("Confidence", s.confidence != null ? fmtNum(Number(s.confidence) * 100, 1) + "%" : "n/a") +
        metric("Confluence Vote", state.news.confluenceVote != null ? fmtNum(state.news.confluenceVote, 4) : "n/a") +
        metric("Major Event", s.major_event_detected ? "YES" : "NO", s.major_event_detected ? "var(--warn)" : "var(--muted)") +
        metric("Articles Used", s.article_count_used != null ? String(s.article_count_used) : "n/a") +
        metric("Ticker", state.news.eodhdTicker || "n/a") +
        metric("EODHD Agreement", s.eodhd_agreement || "n/a") +
      "</div>" +
      (themes ? '<div class="pair-browser-note" style="margin-top:10px;"><strong style="color:var(--text);">Themes:</strong> ' + esc(themes) + "</div>" : "") +
      (s.major_event_description ? '<div class="pair-browser-note" style="margin-top:10px;"><strong style="color:var(--text);">Major Event:</strong> ' + esc(s.major_event_description) + "</div>" : "") +
      (s.reasoning_summary ? '<div class="pair-browser-note" style="margin-top:10px;">' + esc(s.reasoning_summary) + "</div>" : "")
    );
  }

  function renderIntermarketSection() {
    var pair = selectedPair();
    if (!pair || !state.filters.inter) return "";
    var signal = activeSignal();
    var attached = signal && window.buildIntermarketConfirmationBox ? window.buildIntermarketConfirmationBox(signal) : "";
    if (!state.intermarket && !state.intermarketLoading) loadIntermarketMatrix(false);
    var matrixHtml = "";
    if (state.intermarketLoading && !state.intermarket) {
      matrixHtml = '<div class="pair-browser-note">Loading the current H4 intermarket matrix...</div>';
    } else if (state.intermarketError) {
      matrixHtml = '<div class="pair-browser-note" style="color:var(--bear)">Intermarket matrix failed: ' + esc(state.intermarketError) + '</div><div style="margin-top:10px;"><button type="button" class="bt-btn bt-btn-sm" onclick="loadPairBrowserIntermarket()">RETRY INTERMARKET</button></div>';
    } else if (state.intermarket) {
      var rows = ((state.intermarket.relationships || []).filter(function (row) {
        return row.target === pair.display || row.driver === pair.display;
      })).sort(function (a, b) {
        return Number(b.strength || 0) - Number(a.strength || 0);
      }).slice(0, 10);
      matrixHtml = rows.length
        ? '<div style="margin-top:12px;overflow:auto;"><table class="pair-browser-table"><thead><tr><th>Related Pair</th><th>Relation</th><th>Correlation</th><th>Stability</th><th>Window</th><th>Regime</th></tr></thead><tbody>' +
            rows.map(function (row) {
              var related = row.target === pair.display ? row.driver : row.target;
              var rel = String(row.relation || "neutral").toLowerCase();
              var relColor = rel === "inverse" ? "#38bdf8" : rel === "positive" ? "var(--accent)" : "var(--muted)";
              return "<tr><td>" + esc(related || "n/a") + '</td><td style="color:' + relColor + ';">' + esc(rel.toUpperCase()) + "</td><td>" + esc(fmtNum(row.correlation, 4)) + "</td><td>" + esc(fmtNum(row.stability, 4)) + "</td><td>" + esc(row.bestWindow || "n/a") + "</td><td>" + esc(row.regimeLabel || "n/a") + "</td></tr>";
            }).join("") +
          "</tbody></table></div>"
        : '<div class="pair-browser-note">No current intermarket relationship rows were returned for this pair.</div>';
    }
    if (!attached) attached = '<div class="pair-browser-note">Attached Engine A intermarket confirmation is not on this signal. That is expected when the scan ran without intermarket attachment enabled. The matrix below is read-only context only.</div>';
    return section("INTERMARKET", attached + matrixHtml);
  }

  function renderGraphSection() {
    var pair = selectedPair();
    if (!pair || !state.filters.graph) return "";
    var signal = activeSignal();
    var live = signal ? window.getSignalLivePrice(signal) : null;
    if (live == null) live = ((window._latestPrices || {})[pair.display] || {}).price;
    return section("GRAPH",
      '<div class="pair-browser-grid">' +
        metric("Pair", pair.display) +
        metric("Latest Price", live != null ? fmtPriceFor(live, signal || pair) : "n/a") +
        metric("Engine A Entry", signal ? fmtPriceFor(window.getSignalLivePrice(signal), signal) : "n/a") +
        metric("Engine A Stop", signal ? fmtPriceFor(signal.sl, signal) : "n/a") +
        metric("Engine A Target", signal ? fmtPriceFor(signal.tp1, signal) : "n/a") +
        metric("Chart AI", activeVision() ? "LOADED" : "NOT RUN", activeVision() ? "var(--bull)" : "var(--accent)") +
      '</div><div class="pair-browser-note" style="margin-top:10px;">FULL CHART opens the existing Athena chart modal for this pair. TV CHART opens the TradingView view for the same symbol.</div>'
    );
  }

  function renderVisionSection() {
    var pair = selectedPair();
    if (!pair || !state.filters.aiVision) return "";
    var result = activeVision();
    var signal = activeSignal();
    if (!result) {
      return section("AI VISION", '<div class="pair-browser-note">Run AI vision to send the current chart context for this pair through the existing chart-analysis route.</div><div style="margin-top:10px;"><button type="button" class="bt-btn bt-btn-sm" onclick="runPairBrowserVision()">RUN AI VISION</button></div>');
    }
    var structured = result.structured || {};
    return section("AI VISION",
      '<div class="pair-browser-grid">' +
        metric("Model", result.model || "n/a") +
        metric("Timeframe", result.tf || "n/a") +
        metric("Overall Rating", structured.rating || "n/a", structured.rating === "STRONG" ? "var(--bull)" : structured.rating === "CONTRADICTS" ? "var(--bear)" : "var(--accent)") +
        metric("Direction Check", structured.confirms_direction === false ? "CONTRADICTS" : "CONFIRMS", structured.confirms_direction === false ? "var(--bear)" : "var(--bull)") +
        metric("Right Edge", structured.right_edge_status || "n/a") +
        metric("Scalp", String(((structured.style_ratings || {}).scalp || "n/a")).toUpperCase()) +
        metric("Intraday", String(((structured.style_ratings || {}).intraday || "n/a")).toUpperCase()) +
        metric("Swing", String(((structured.style_ratings || {}).swing || "n/a")).toUpperCase()) +
      "</div><div style=\"margin-top:12px;\">" +
        (window._buildVerdictBanner ? window._buildVerdictBanner(result) : "") +
        (window._buildRightEdgeBadge ? window._buildRightEdgeBadge(result) : "") +
        '<div class="pair-browser-note" style="white-space:pre-wrap;color:var(--text);margin-top:10px;">' + esc(result.analysis || "No chart analysis text returned.") + "</div>" +
        (signal && window._buildChartAiLevelHtml ? window._buildChartAiLevelHtml(signal, result) : "") +
      "</div>"
    );
  }

  function renderPairBrowserSections() {
    var el = document.getElementById("pairBrowserSections");
    if (!el) return;
    if (!selectedPair()) {
      el.innerHTML = '<div class="empty-state"><div class="icon">&#9635;</div><h3>Pair Workbench Ready</h3><p>Select a pair to inspect its individual scan path, compare the engines, open the chart, and request AI feedback.</p></div>';
      return;
    }
    el.innerHTML = renderEngineASection() + renderEngineBSection() + renderCompareSection() + renderGraphSection() + renderNewsSection() + renderIntermarketSection() + renderVisionSection();
  }

  function renderPairBrowser() {
    ["pairBrowserAssetClass", "pairBrowserStyle", "pairBrowserDirection"].forEach(function (id, i) {
      var el = document.getElementById(id);
      if (!el) return;
      if (i === 0) el.value = state.assetClass || "";
      if (i === 1) el.value = state.style || "auto";
      if (i === 2) el.value = state.directionMode || "auto";
    });
    var search = document.getElementById("pairBrowserSearch");
    if (search) search.value = state.query || "";
    Object.keys(state.filters || {}).forEach(function (name) {
      var btn = document.getElementById("pairBrowserFilter-" + name);
      if (btn) btn.classList.toggle("active", !!state.filters[name]);
    });
    renderList();
    renderHeader();
    setButtonsDisabled(!selectedPair());
    renderPairBrowserSections();
  }

  window.loadPairBrowserPairs = async function (force) {
    if (state.loadingPairs) return;
    if (state.loadedPairs && !force) return renderPairBrowser();
    state.loadingPairs = true;
    try {
      var resp = await fetch("/api/pairs");
      var data = await json(resp);
      if (!resp.ok || data.error) throw new Error(data.error || ("HTTP " + resp.status));
      var groups = data.groups || {};
      var order = ["Forex", "Crypto", "Commodities", "Indices", "US Stocks", "ETFs"];
      var seen = {};
      var pairs = [];
      order.concat(Object.keys(groups)).forEach(function (groupName) {
        if (!groups[groupName] || seen[groupName]) return;
        seen[groupName] = true;
        (groups[groupName] || []).forEach(function (row) {
          pairs.push({ symbol: row.sym, display: row.label, enabled: row.enabled !== false, type: typeFromGroup(groupName), group: groupName });
        });
      });
      pairs.sort(function (a, b) {
        if (a.enabled !== b.enabled) return a.enabled ? -1 : 1;
        if (a.group !== b.group) return String(a.group).localeCompare(String(b.group));
        return String(a.display).localeCompare(String(b.display));
      });
      state.pairs = pairs;
      state.loadedPairs = true;
      if (state.selectedSymbol && !pairs.some(function (pair) { return pair.symbol === state.selectedSymbol; })) {
        state.selectedSymbol = "";
        resetResults();
      }
      renderPairBrowser();
    } catch (err) {
      setStatus("Pair list failed: " + err.message, true);
      var list = document.getElementById("pairBrowserList");
      if (list) list.innerHTML = '<div class="empty-msg" style="color:var(--bear)">Failed to load pairs: ' + esc(err.message) + "</div>";
    } finally {
      state.loadingPairs = false;
    }
  };

  window.refreshPairBrowserPairs = function () { return window.loadPairBrowserPairs(true); };
  window.loadPairBrowserIntermarket = function () { return loadIntermarketMatrix(true); };
  window.selectPairBrowserPair = function (symbol) {
    var pair = (state.pairs || []).find(function (row) { return row.symbol === symbol; });
    if (!pair) return;
    if (state.selectedSymbol !== pair.symbol) {
      state.selectedSymbol = pair.symbol;
      resetResults();
      setStatus("Selected " + pair.display + ". Run the engine path you want from this workspace.", false);
    }
    renderPairBrowser();
  };
  window.pairBrowserUpdateAssetFilter = function (value) { state.assetClass = String(value || "").trim().toLowerCase(); renderPairBrowser(); };
  window.pairBrowserUpdateStyle = function (value) { state.style = String(value || "auto").trim().toLowerCase() || "auto"; renderPairBrowser(); };
  window.pairBrowserUpdateDirection = function (value) { state.directionMode = value === "LONG" || value === "SHORT" ? value : "auto"; renderPairBrowser(); };
  window.pairBrowserUpdateQuery = function (value) { state.query = String(value || ""); renderPairBrowser(); };
  window.togglePairBrowserSection = function (name) {
    if (!Object.prototype.hasOwnProperty.call(state.filters, name)) return;
    state.filters[name] = !state.filters[name];
    renderPairBrowser();
  };
})();
