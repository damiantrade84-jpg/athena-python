(function() {
  // Track the pair the user last clicked — captured by our function patches
  var _activePair = null;

  function patchSelectionFunctions(onUpdate) {
    if (window.ldSelectCard && !window.ldSelectCard._cPatch) {
      var orig = window.ldSelectCard;
      window.ldSelectCard = function(sym) {
        // sym is an object with a .symbol property
        _activePair = (sym && sym.symbol) ? sym.symbol : String(sym);
        orig.call(this, sym);
        setTimeout(onUpdate, 150);
      };
      window.ldSelectCard._cPatch = true;
    }
    if (window.selectPairBrowserPair && !window.selectPairBrowserPair._cPatch) {
      var origPb = window.selectPairBrowserPair;
      window.selectPairBrowserPair = function(symbol) {
        _activePair = symbol;
        origPb.call(this, symbol);
        setTimeout(onUpdate, 150);
      };
      window.selectPairBrowserPair._cPatch = true;
    }
  }

  function initWidget() {
    var container = document.getElementById('conductor-widget');
    if (container) return;

    container = document.createElement('div');
    container.id = 'conductor-widget';
    container.style.cssText = 'position:fixed;bottom:20px;right:20px;width:320px;background:#1a1a2e;border:1px solid #4a4a6a;border-radius:8px;padding:15px;z-index:2147483647;color:#fff;font-family:monospace;font-size:12px;box-shadow:0 4px 20px rgba(0,0,0,0.5);';
    container.innerHTML = [
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">',
      '  <strong style="color:#00d4ff;">🎼 Conductor</strong>',
      '  <span id="cw-status" style="color:#ffd700;font-size:10px;">waiting...</span>',
      '</div>',
      '<div id="cw-content" style="line-height:1.6;"><div style="color:#888;">Waiting for signal...</div></div>'
    ].join('');
    document.body.appendChild(container);

    var status = document.getElementById('cw-status');
    var content = document.getElementById('cw-content');

    function getActivePair() {
      // 1. Pair we captured from a click
      if (_activePair) return _activePair;
      // 2. Pair Browser state object IS on window
      if (window._pairBrowser && window._pairBrowser.selectedSymbol) {
        return window._pairBrowser.selectedSymbol;
      }
      return null;
    }

    async function update() {
      try {
        var pair = getActivePair();
        var url = pair ? '/api/conductor/last?pair=' + encodeURIComponent(pair) : '/api/conductor/last';
        var resp = await fetch(url);
        if (!resp.ok) { content.innerHTML = '<div style="color:#888;">API offline</div>'; return; }
        var data = await resp.json();
        if (!data.conductor) {
          content.innerHTML = '<div style="color:#888;">' + (data.message || 'No signal yet') + '</div>';
          status.textContent = 'waiting...';
          status.style.color = '#ffd700';
          return;
        }

        var r = data.conductor;
        status.textContent = r.skip_signal ? '⛔ SKIPPED' : '✅ ACTIVE';
        status.style.color = r.skip_signal ? '#ff4444' : '#44ff44';

        var dirColor = r.direction === 'LONG' ? '#44ff44' : r.direction === 'SHORT' ? '#ff4444' : '#ffd700';
        var html = '';
        html += '<div style="margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid #333;">';
        html += '<span style="color:#fff;font-weight:bold;">' + (r.pair || '?') + '</span>';
        html += ' <span style="color:' + dirColor + ';font-weight:bold;">' + (r.direction || '?') + '</span>';
        html += ' <span style="color:#aaa;font-size:10px;">' + (r.regime || '') + '</span>';
        html += '</div>';
        html += '<div><span style="color:#888;">Debate:</span> ' + (r.run_debate ? '🟢 YES' : '⚫ NO') + '</div>';
        html += '<div><span style="color:#888;">Vision:</span> ' + (r.run_vision ? '🟢 YES' : '⚫ NO') + '</div>';
        html += '<div><span style="color:#888;">Sentiment:</span> ' + (r.run_sentiment ? '🟢 YES' : '⚫ NO') + '</div>';
        html += '<div><span style="color:#888;">Weights:</span> A=' + (((r.engine_weights && r.engine_weights.engine_a) || 0.5) * 100).toFixed(0) + '% B=' + (((r.engine_weights && r.engine_weights.engine_b) || 0.5) * 100).toFixed(0) + '%</div>';
        html += '<div><span style="color:#888;">Score:</span> ' + ((r.score_pct || 0)).toFixed(1) + '%</div>';
        html += '<div style="margin-top:5px;font-size:10px;color:#aaa;border-top:1px solid #333;padding-top:5px;">' + ((r.reasons || []).join(' • ') || 'No reasons') + '</div>';

        content.innerHTML = html;
      } catch (e) {
        content.innerHTML = '<div style="color:#888;">Connection error</div>';
      }
    }

    // Patch now (functions may already be defined) and retry every second for up to 15s
    patchSelectionFunctions(update);
    var _attempts = 0;
    var _patchTimer = setInterval(function() {
      patchSelectionFunctions(update);
      if (++_attempts >= 15) clearInterval(_patchTimer);
    }, 1000);

    update();
    setInterval(update, 15000);
  }

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(initWidget, 2000);
  } else {
    window.addEventListener('DOMContentLoaded', function() { setTimeout(initWidget, 2000); });
  }
})();
