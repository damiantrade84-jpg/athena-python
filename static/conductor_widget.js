(function() {
  function initWidget() {
    const WIDGET_HTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
      <strong style="color:#00d4ff;">🎼 Conductor</strong>
      <span id="cw-status" style="color:#ffd700;font-size:10px;">waiting...</span>
    </div>
    <div id="cw-content" style="line-height:1.6;">
      <div style="color:#888;">Waiting for signal...</div>
    </div>`;

    let container = document.getElementById('conductor-widget');
    if (container) return; // Already exists

    container = document.createElement('div');
    container.id = 'conductor-widget';
    container.style.cssText = 'position:fixed;bottom:20px;right:20px;width:320px;background:#1a1a2e;border:1px solid #4a4a6a;border-radius:8px;padding:15px;z-index:2147483647;color:#fff;font-family:monospace;font-size:12px;box-shadow:0 4px 20px rgba(0,0,0,0.5);';
    container.innerHTML = WIDGET_HTML;
    document.body.appendChild(container);

    const status = document.getElementById('cw-status');
    const content = document.getElementById('cw-content');

    function getActivePair() {
      // Live dashboard selection
      if (window._ldSelectedSym) return window._ldSelectedSym;
      // Pair Browser selection
      if (window._pairBrowser && window._pairBrowser.selectedSymbol) return window._pairBrowser.selectedSymbol;
      return null;
    }

    async function update() {
      try {
        const pair = getActivePair();
        const url = pair ? `/api/conductor/last?pair=${encodeURIComponent(pair)}` : '/api/conductor/last';
        const resp = await fetch(url);
        if (!resp.ok) { content.innerHTML = '<div style="color:#888;">API offline</div>'; return; }
        const data = await resp.json();
        if (!data.conductor) {
          content.innerHTML = `<div style="color:#888;">${data.message || 'No signal yet'}</div>`;
          status.textContent = 'waiting...';
          status.style.color = '#ffd700';
          return;
        }

        const r = data.conductor;
        status.textContent = r.skip_signal ? '⛔ SKIPPED' : '✅ ACTIVE';
        status.style.color = r.skip_signal ? '#ff4444' : '#44ff44';

        const dirColor = r.direction === 'LONG' ? '#44ff44' : r.direction === 'SHORT' ? '#ff4444' : '#ffd700';
        let html = '';
        html += `<div style="margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid #333;">`;
        html += `<span style="color:#fff;font-weight:bold;">${r.pair || '?'}</span>`;
        html += ` <span style="color:${dirColor};font-weight:bold;">${r.direction || '?'}</span>`;
        html += ` <span style="color:#aaa;font-size:10px;">${r.regime || ''}</span>`;
        html += `</div>`;
        html += `<div><span style="color:#888;">Debate:</span> ${r.run_debate ? '🟢 YES' : '⚫ NO'}</div>`;
        html += `<div><span style="color:#888;">Vision:</span> ${r.run_vision ? '🟢 YES' : '⚫ NO'}</div>`;
        html += `<div><span style="color:#888;">Sentiment:</span> ${r.run_sentiment ? '🟢 YES' : '⚫ NO'}</div>`;
        html += `<div><span style="color:#888;">Weights:</span> A=${((r.engine_weights?.engine_a || 0.5) * 100).toFixed(0)}% B=${((r.engine_weights?.engine_b || 0.5) * 100).toFixed(0)}%</div>`;
        html += `<div><span style="color:#888;">Score:</span> ${(r.score_pct || 0).toFixed(1)}%</div>`;
        html += `<div style="margin-top:5px;font-size:10px;color:#aaa;border-top:1px solid #333;padding-top:5px;">${(r.reasons || []).join(' • ') || 'No reasons'}</div>`;

        content.innerHTML = html;
      } catch (e) {
        content.innerHTML = '<div style="color:#888;">Connection error</div>';
      }
    }

    // Poll on interval
    update();
    setInterval(update, 15000);

    // Also re-fetch immediately when the user selects a different signal/pair.
    // We patch the two known selection functions rather than polling the DOM.
    const _origLd = window.ldSelectCard;
    if (typeof _origLd === 'function') {
      window.ldSelectCard = function(sym) {
        _origLd.call(this, sym);
        setTimeout(update, 100);
      };
    }

    const _origPb = window.selectPairBrowserPair;
    if (typeof _origPb === 'function') {
      window.selectPairBrowserPair = function(symbol) {
        _origPb.call(this, symbol);
        setTimeout(update, 100);
      };
    }

    // If functions load after us, set up a one-time watch
    let _patchAttempts = 0;
    const _patchTimer = setInterval(function() {
      _patchAttempts++;
      if (!window.ldSelectCard.__conductor_patched && typeof window.ldSelectCard === 'function') {
        const _orig = window.ldSelectCard;
        window.ldSelectCard = function(sym) { _orig.call(this, sym); setTimeout(update, 100); };
        window.ldSelectCard.__conductor_patched = true;
      }
      if (!window.selectPairBrowserPair.__conductor_patched && typeof window.selectPairBrowserPair === 'function') {
        const _orig = window.selectPairBrowserPair;
        window.selectPairBrowserPair = function(symbol) { _orig.call(this, symbol); setTimeout(update, 100); };
        window.selectPairBrowserPair.__conductor_patched = true;
      }
      if (_patchAttempts >= 20) clearInterval(_patchTimer);
    }, 500);
  }

  // Wait for React to mount, then inject
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(initWidget, 2000);
  } else {
    window.addEventListener('DOMContentLoaded', () => setTimeout(initWidget, 2000));
  }
})();
