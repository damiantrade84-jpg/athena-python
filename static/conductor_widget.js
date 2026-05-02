(function () {
  var _selectedPair = null;
  var _pairsCache = [];

  function initWidget() {
    if (document.getElementById('conductor-widget')) return;

    var container = document.createElement('div');
    container.id = 'conductor-widget';
    container.style.cssText = [
      'position:fixed;bottom:20px;right:20px;width:340px;',
      'background:#1a1a2e;border:1px solid #4a4a6a;border-radius:8px;',
      'padding:12px;z-index:2147483647;color:#fff;',
      'font-family:monospace;font-size:12px;',
      'box-shadow:0 4px 20px rgba(0,0,0,0.5);'
    ].join('');

    container.innerHTML = [
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">',
      '  <strong style="color:#00d4ff;">🎼 Conductor</strong>',
      '  <span id="cw-status" style="color:#ffd700;font-size:10px;">waiting...</span>',
      '</div>',
      // Pair selector row
      '<div id="cw-pair-row" style="margin-bottom:8px;display:none;">',
      '  <select id="cw-pair-select" style="width:100%;background:#0d0d1a;color:#fff;border:1px solid #4a4a6a;border-radius:4px;padding:4px;font-size:11px;font-family:monospace;">',
      '    <option value="">-- select pair --</option>',
      '  </select>',
      '</div>',
      '<div id="cw-content" style="line-height:1.7;"><div style="color:#888;">Run a scan to populate...</div></div>'
    ].join('');

    document.body.appendChild(container);

    var statusEl = document.getElementById('cw-status');
    var contentEl = document.getElementById('cw-content');
    var pairRow = document.getElementById('cw-pair-row');
    var pairSelect = document.getElementById('cw-pair-select');

    // Rebuild the dropdown from cached pair list
    function rebuildDropdown(pairs) {
      _pairsCache = pairs;
      pairSelect.innerHTML = '<option value="">-- select pair --</option>';
      pairs.forEach(function (p) {
        var opt = document.createElement('option');
        opt.value = p.pair;
        var dirLabel = p.skip_signal ? '⛔' : (p.direction === 'LONG' ? '▲' : p.direction === 'SHORT' ? '▼' : '—');
        opt.textContent = dirLabel + ' ' + p.pair + ' (' + (p.score_pct || 0).toFixed(0) + '%)';
        if (p.pair === _selectedPair) opt.selected = true;
        pairSelect.appendChild(opt);
      });
      pairRow.style.display = pairs.length > 0 ? 'block' : 'none';
    }

    pairSelect.addEventListener('change', function () {
      _selectedPair = pairSelect.value || null;
      fetchAndRender();
    });

    function renderRouting(r) {
      if (!r) {
        contentEl.innerHTML = '<div style="color:#888;">No data for this pair.</div>';
        statusEl.textContent = 'waiting...';
        statusEl.style.color = '#ffd700';
        return;
      }
      statusEl.textContent = r.skip_signal ? '⛔ SKIPPED' : '✅ ACTIVE';
      statusEl.style.color = r.skip_signal ? '#ff4444' : '#44ff44';

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
      var wA = (((r.engine_weights && r.engine_weights.engine_a) || 0.5) * 100).toFixed(0);
      var wB = (((r.engine_weights && r.engine_weights.engine_b) || 0.5) * 100).toFixed(0);
      html += '<div><span style="color:#888;">Weights:</span> A=' + wA + '% B=' + wB + '%</div>';
      html += '<div><span style="color:#888;">Score:</span> ' + (r.score_pct || 0).toFixed(1) + '%</div>';
      var reasons = (r.reasons || []).join(' • ') || 'No reasons';
      html += '<div style="margin-top:5px;font-size:10px;color:#aaa;border-top:1px solid #333;padding-top:5px;">' + reasons + '</div>';
      contentEl.innerHTML = html;
    }

    async function fetchAndRender() {
      try {
        var url = _selectedPair
          ? '/api/conductor/last?pair=' + encodeURIComponent(_selectedPair)
          : '/api/conductor/last';
        var resp = await fetch(url);
        if (!resp.ok) { contentEl.innerHTML = '<div style="color:#888;">API offline</div>'; return; }
        var data = await resp.json();
        renderRouting(data.conductor);
        if (data.message && !data.conductor) {
          contentEl.innerHTML = '<div style="color:#888;">' + data.message + '</div>';
          statusEl.textContent = 'waiting...';
          statusEl.style.color = '#ffd700';
        }
      } catch (e) {
        contentEl.innerHTML = '<div style="color:#888;">Connection error</div>';
      }
    }

    async function refreshPairs() {
      try {
        var resp = await fetch('/api/conductor/pairs');
        if (!resp.ok) return;
        var data = await resp.json();
        var pairs = data.pairs || [];
        if (pairs.length !== _pairsCache.length) {
          rebuildDropdown(pairs);
          // Auto-select top signal when a new scan arrives and user hasn't chosen
          if (!_selectedPair && pairs.length > 0) {
            _selectedPair = pairs[0].pair;
            pairSelect.value = _selectedPair;
          }
          fetchAndRender();
        }
      } catch (e) { /* ignore */ }
    }

    // Initial load + periodic refresh
    refreshPairs();
    fetchAndRender();
    setInterval(refreshPairs, 10000);
    setInterval(fetchAndRender, 15000);
  }

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(initWidget, 2000);
  } else {
    window.addEventListener('DOMContentLoaded', function () { setTimeout(initWidget, 2000); });
  }
})();
