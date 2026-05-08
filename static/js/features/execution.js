// Execution feature module (compatibility-first extraction).
(function initExecutionFeature() {
  function _normSymbol(v) {
    return String(v || "").toUpperCase().replace(/[\/=^.]/g, "");
  }

  function _resolveSignal(symbol) {
    var list = window.allSignals || [];
    var target = _normSymbol(symbol);
    var sig = list.find(function (s) {
      return (
        _normSymbol(s.symbol) === target ||
        _normSymbol(s.display) === target ||
        _normSymbol(s.pair) === target
      );
    });
    if (sig) return sig;
    var sid = String(symbol || "").replace(/[\/=^.]/g, "_");
    return window["_nakedSig_" + sid] || null;
  }

  function _resolveSignalRef(signalRef) {
    var ref = String(signalRef || "");
    var exact = (window._execSignalsByKey || {})[ref];
    if (exact) return exact;
    if (window["_nakedSig_" + ref]) return window["_nakedSig_" + ref];
    return _resolveSignal(signalRef);
  }

  function getExecutionSizingOverride() {
    var el = document.getElementById("execRiskSizing");
    var v = el ? parseFloat(el.value, 10) : NaN;
    if (!(v >= 0.25 && v <= 1)) return 1.0;
    return v;
  }

  function _signalSid(sig, fallback) {
    var base =
      (sig && (sig.symbol || sig.display || sig.pair)) || String(fallback || "");
    return String(base).replace(/[\/=^.]/g, "_");
  }

  async function postQuickExecute(payload, btn, successMsg) {
    const old = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Executing...";
    }
    try {
      const resData = await window.apiClient.postJson("/api/quick-execute", payload);
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Success! " + resData.ticket;
        setTimeout(function () {
          btn.style.display = "none";
        }, 2000);
      }
      window.showToast(
        successMsg || "Executed successfully. Ticket: " + resData.ticket
      );
      return resData;
    } catch (err) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = old || "⚡ RETRY QUICK EXEC";
      }
      window.showToast("Execute Error: " + err.message, true);
      throw err;
    }
  }

  async function executeSignalStyle(signalRef, pipMode, buttonId) {
    var sig = _resolveSignalRef(signalRef);
    if (!sig) {
      window.showToast("Signal not found for " + signalRef, true);
      return;
    }
    var sid = _signalSid(sig, signalRef);
    var symbol = sig.display || sig.pair || sig.symbol || signalRef;
    var btn = document.getElementById(
      buttonId || "exec-style-" + (pipMode || "swing") + "-" + sid
    );
    var payload = {
      signal: window.buildLiveSignalPayload(sig),
      engine_b: {},
      pip_mode: pipMode || "swing",
      sizing_override: getExecutionSizingOverride(),
    };
    await postQuickExecute(
      payload,
      btn,
      "Executed " + symbol + " (" + (pipMode || "swing").toUpperCase() + ")"
    );
  }

  async function quickExecute(signalRef, engineBData, buttonId, pipMode) {
    var sig = _resolveSignalRef(signalRef);
    if (!sig) return;
    var sid = _signalSid(sig, signalRef);
    var symbol = sig.display || sig.pair || sig.symbol || signalRef;
    var btn = document.getElementById(
      buttonId || "qexecbtn-" + (pipMode || "swing") + "-" + sid
    );
    var payload = {
      signal: window.buildLiveSignalPayload(sig),
      engine_b: engineBData || {},
      pip_mode: pipMode || "swing",
      sizing_override: getExecutionSizingOverride(),
    };
    await postQuickExecute(
      payload,
      btn,
      "Quick executed " +
        symbol +
        " (" +
        (pipMode || "swing").toUpperCase() +
        ")"
    );
  }

  async function executeEngineC(sid, pipMode) {
    var consensus = null;
    ["aligned", "a_only", "b_only"].forEach(function (cat) {
      ((window._ecResults || {})[cat] || []).forEach(function (c) {
        var cSid = (c.symbol || c.display || "").replace(/[\/=^.]/g, "_");
        if (cSid === sid) consensus = c;
      });
    });
    if (!consensus || !consensus.trade) {
      window.showToast("Cannot execute — no valid consensus signal", true);
      return;
    }
    var signal = {
      pair: consensus.display,
      display: consensus.display,
      symbol: consensus.symbol,
      type: consensus.type,
      direction: consensus.direction,
      price: consensus.entry,
      sl: consensus.sl,
      tp1: consensus.tp,
      tp2: consensus.tp,
      style: pipMode || consensus.style || "swing",
      confluenceScore: consensus.conviction,
      ts: new Date().toISOString(),
      engine: "engine_c",
      trade: consensus.trade,
      verdict: consensus.verdict,
      tier: consensus.tier,
      signalTier: consensus.signalTier,
      decision_state: consensus.decision_state,
      decision_state_reason: consensus.decision_state_reason,
      components: consensus.components,
      engine_b_status: consensus.engine_b_status,
      rr: consensus.rr,
    };
    var engineB = consensus.engine_b_raw || {};
    engineB.recommended_stop_loss = consensus.sl;
    engineB.recommended_take_profit = consensus.tp;
    var userSz = getExecutionSizingOverride();
    var co = consensus.sizing_override;
    var mult = co != null && !isNaN(Number(co)) ? Number(co) : 1;
    var combined = Math.max(0.25, Math.min(1.0, userSz * mult));
    await postQuickExecute(
      {
        signal: signal,
        engine_b: engineB,
        pip_mode: pipMode || "swing",
        sizing_override: combined,
      },
      null,
      "Engine C: " +
        consensus.direction +
        " " +
        consensus.display +
        " executed (" +
        (pipMode || "swing").toUpperCase() +
        ", " +
        (consensus.tier || "") +
        ")"
    );
  }

  window.ExecutionFeature = {
    postQuickExecute,
    executeSignalStyle,
    quickExecute,
    executeEngineC,
    getExecutionSizingOverride,
  };
})();

