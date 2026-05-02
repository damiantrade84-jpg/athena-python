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

    async function update() {
      try {
        const resp = await fetch('/api/conductor/last');
        if (!resp.ok) { content.innerHTML = '<div style="color:#888;">API offline</div>'; return; }
        const data = await resp.json();
        if (!data.conductor) { content.innerHTML = '<div style="color:#888;">No signal yet</div>'; return; }

        const r = data.conductor;
        status.textContent = r.skip_signal ? '⛔ SKIPPED' : '✅ ACTIVE';
        status.style.color = r.skip_signal ? '#ff4444' : '#44ff44';

        let html = '';
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

    update();
    setInterval(update, 15000);
  }

  // Wait for React to mount, then inject
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(initWidget, 2000);
  } else {
    window.addEventListener('DOMContentLoaded', () => setTimeout(initWidget, 2000));
  }
})();
