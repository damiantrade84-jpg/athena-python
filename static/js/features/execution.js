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

  async function executeSignalStyle(symbol, pipMode, buttonId) {
    var sid = (symbol || "").replace(/[\/=^.]/g, "_");
    var sig = _resolveSignal(symbol);
    if (!sig) {
      window.showToast("Signal not found for " + symbol, true);
      return;
    }
    var btn = document.getElementById(
      buttonId || "exec-style-" + (pipMode || "swing") + "-" + sid
    );
    var payload = {
      signal: window.buildLiveSignalPayload(sig),
      engine_b: {},
      pip_mode: pipMode || "swing",
    };
    await postQuickExecute(
      payload,
      btn,
      "Executed " + symbol + " (" + (pipMode || "swing").toUpperCase() + ")"
    );
  }

  async function quickExecute(symbol, engineBData, buttonId, pipMode) {
    var sid = (symbol || "").replace(/[\/=^.]/g, "_");
    var sig = _resolveSignal(symbol);
    if (!sig) return;
    var btn = document.getElementById(
      buttonId || "qexecbtn-" + (pipMode || "swing") + "-" + sid
    );
    var payload = {
      signal: window.buildLiveSignalPayload(sig),
      engine_b: engineBData || {},
      pip_mode: pipMode || "swing",
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
    };
    var engineB = consensus.engine_b_raw || {};
    engineB.recommended_stop_loss = consensus.sl;
    engineB.recommended_take_profit = consensus.tp;
    await postQuickExecute(
      {
        signal: signal,
        engine_b: engineB,
        pip_mode: pipMode || "swing",
        sizing_override: consensus.sizing_override,
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
  };
})();

