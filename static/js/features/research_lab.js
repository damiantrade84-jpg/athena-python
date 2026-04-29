// Research Lab — extracted from static/index.html (Tier 3 cleanup pass).
// Wires up the Research Lab panel: One-Click Autopilot Cockpit,
// Quick Discovery, Manual Override, results table, AI Review, AI Plan.
// Backend endpoints: /api/research-lab/*
//
// This is a pure refactor — no logic changes. If this file misbehaves,
// diff against the pre-extraction inline block in git history.

(function() {
  'use strict';

  let _rlCurrentRunId = null;
  let _rlPollTimer = null;
  let _rlCurrentRecommendations = [];

  // ── Panel lifecycle ────────────────────────────────────────────────────────
  window._onResearchPanelOpen = function() {
    window.rlLoadRuns();
  };

  window.rlStartStyleRun = async function() {
    console.log('[Research Lab] rlStartStyleRun called');
    // Reuse the One-Click Cockpit selectors (rl-sess-*).
    // The duplicate rl-market-group/rl-trading-style/rl-research-depth selectors
    // were removed in the Tier 2 cleanup pass.
    const market_group = document.getElementById('rl-sess-market-group').value;
    const trading_style = document.getElementById('rl-sess-trading-style').value;
    const research_depth = document.getElementById('rl-sess-research-depth').value;

    console.log('[Research Lab] Parameters:', { market_group, trading_style, research_depth });

    const btn = document.getElementById('rl-style-run-btn');
    if (btn) {
      btn.disabled = true;
      btn.style.opacity = '0.5';
    }

    _rlSetStatus(`Queuing Autopilot research for ${market_group} [${trading_style}]…`, '');
    document.getElementById('rl-status').style.display = 'block';
    document.getElementById('rl-results').style.display = 'none';
    document.getElementById('rl-ai-panel').style.display = 'none';

    try {
      console.log('[Research Lab] Fetching /api/research-lab/style-run');
      const res = await fetch('/api/research-lab/style-run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ market_group, trading_style, research_depth })
      });
      console.log('[Research Lab] Response status:', res.status);
      const data = await res.json();
      console.log('[Research Lab] Response data:', data);
      if (data.error) throw new Error(data.error);
      _rlCurrentRunId = data.run_id;
      _rlSetStatus(`Discovery started — mode: ${data.mode}`, data.run_id);
      _rlPollStatus(data.run_id);
    } catch (e) {
      console.error('[Research Lab] Error:', e);
      _rlSetStatus('Error starting autopilot run: ' + e.message, '');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.style.opacity = '1.0';
      }
    }
  };

  // ── Start a new run ────────────────────────────────────────────────────────

  window.rlStartRun = async function() {
    const mode        = document.getElementById('rl-mode').value;
    const direction   = document.getElementById('rl-direction').value;
    const aiReview    = document.getElementById('rl-ai-review').checked;
    const symbols     = document.getElementById('rl-symbols').value;
    const timeframes  = document.getElementById('rl-timeframes').value;
    const families    = document.getElementById('rl-families').value;
    const strategies  = document.getElementById('rl-strategies').value;

    _rlSetStatus('Queuing run…', '');
    document.getElementById('rl-status').style.display = 'block';
    document.getElementById('rl-results').style.display = 'none';
    document.getElementById('rl-ai-panel').style.display = 'none';

    try {
      const payload = {
        mode, 
        direction, 
        run_ai_review: aiReview
      };
      
      if (symbols && symbols.trim()) {
        payload.symbols = symbols.split(',').map(s => s.trim()).filter(Boolean);
      }
      if (timeframes && timeframes.trim()) {
        payload.timeframes = timeframes.split(',').map(s => s.trim()).filter(Boolean);
      }
      if (families && families.trim()) {
        payload.families = families.split(',').map(s => s.trim()).filter(Boolean);
      }
      if (strategies && strategies.trim()) {
        payload.strategies = strategies.split(',').map(s => s.trim()).filter(Boolean);
      }

      const res = await fetch('/api/research-lab/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      _rlCurrentRunId = data.run_id;
      _rlSetStatus(`Run started — mode: ${mode}`, data.run_id);
      _rlPollStatus(data.run_id);
    } catch (e) {
      _rlSetStatus('Error starting run: ' + e.message, '');
    }
  };

  window.rlApplyPreset = function(preset) {
    const s = document.getElementById('rl-symbols');
    const t = document.getElementById('rl-timeframes');
    const f = document.getElementById('rl-families');
    const st = document.getElementById('rl-strategies');
    const m = document.getElementById('rl-mode');
    const d = document.getElementById('rl-direction');
    
    if (preset === 'crypto_h4_bb') {
      s.value = 'BTC/USDT, ETH/USDT, SOL/USDT';
      t.value = 'H4';
      f.value = 'volatility';
      st.value = 'bollinger_touch';
      m.value = 'tiny';
      d.value = 'both';
    } else if (preset === 'engine_b_h4') {
      s.value = 'EUR/USD, GBP/USD, AUD/USD';
      t.value = 'H4';
      f.value = 'engine_b_proxy';
      st.value = 'structure_filters';
      m.value = 'tiny';
      d.value = 'both';
    } else if (preset === 'crypto_macd') {
      s.value = 'BTC/USDT, ETH/USDT';
      t.value = 'H4';
      f.value = 'trend_momentum';
      st.value = 'macd_direction';
      m.value = 'tiny';
      d.value = 'both';
    } else if (preset === 'xau_micro') {
      s.value = 'XAU/USD';
      t.value = 'M15, H1';
      f.value = 'engine_d_proxy';
      st.value = 'micro_breakout';
      m.value = 'tiny';
      d.value = 'both';
    }
  };

  window.rlValidateCandidate = async function() {
    const selected = document.querySelector('input[name="rl-selected-candidate"]:checked');
    if (!selected) {
      alert('Please select a candidate from the ranked strategies table first.');
      return;
    }
    
    const symbol = selected.getAttribute('data-symbol');
    const timeframe = selected.getAttribute('data-tf');
    const family = selected.getAttribute('data-family');
    const strategy = selected.getAttribute('data-strategy');
    const direction = selected.getAttribute('data-direction');

    _rlSetStatus('Queuing validation run…', '');
    document.getElementById('rl-status').style.display = 'block';

    try {
      const payload = {
        mode: 'tiny', // validation implies focused mode
        direction: direction || 'both',
        run_ai_review: document.getElementById('rl-ai-review').checked,
        symbols: symbol ? [symbol] : undefined,
        timeframes: timeframe ? [timeframe] : undefined,
        families: family ? [family] : undefined,
        strategies: strategy ? [strategy] : undefined
      };

      const res = await fetch('/api/research-lab/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      _rlCurrentRunId = data.run_id;
      _rlSetStatus(`Validation started — ${strategy} (${symbol})`, data.run_id);
      _rlPollStatus(data.run_id);
    } catch (e) {
      _rlSetStatus('Error starting validation: ' + e.message, '');
    }
  };

  let _rlCurrentPlan = null;

  window.rlGeneratePlan = async function() {
    if (!_rlCurrentRunId) { alert('No active run selected'); return; }
    const mode = document.getElementById('rl-plan-mode').value;
    
    _rlSetStatus('Generating research plan…', _rlCurrentRunId);
    
    try {
      const res = await fetch('/api/research-lab/auto-plan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          run_id: _rlCurrentRunId,
          planner_mode: mode,
          max_tests: 5,
          max_combinations_per_test: 300
        })
      });
      const plan = await res.json();
      if (plan.error) throw new Error(plan.error);
      
      _rlCurrentPlan = plan;
      
      const cardsEl = document.getElementById('rl-plan-cards');
      if (!plan.tests || plan.tests.length === 0) {
        cardsEl.innerHTML = '<div style="grid-column:1/-1;opacity:.5;font-size:.8rem;padding:12px">No recommended tests generated for this run.</div>';
        return;
      }
      
      cardsEl.innerHTML = plan.tests.map((t, idx) => `
        <div style="background:#111827;border:1px solid #2a3a50;border-radius:6px;padding:12px;font-size:.8rem;display:flex;flex-direction:column;gap:8px">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <span style="font-weight:600;color:#7fa8c8">#${t.priority} ${t.title}</span>
            <input type="checkbox" id="plan-chk-${idx}" ${t.selected_by_default ? 'checked' : ''} style="accent-color:#059669">
          </div>
          <div style="font-size:.75rem;opacity:.8">${t.purpose}</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:.72rem;background:#0a0f1a;padding:8px;border-radius:4px">
            <div><strong>Type:</strong> <span style="color:#fbbf24">${t.test_type}</span></div>
            <div><strong>Symbols:</strong> ${t.symbols ? t.symbols.join(', ') : 'N/A'}</div>
            <div><strong>Timeframes:</strong> ${t.timeframes ? t.timeframes.join(', ') : 'N/A'}</div>
            <div><strong>Families:</strong> ${t.families ? t.families.join(', ') : 'N/A'}</div>
            <div><strong>Mode:</strong> ${t.mode}</div>
            <div><strong>Estimated Combs:</strong> ${t.max_combinations}</div>
          </div>
          <div style="font-size:.72rem;opacity:.7"><strong>Why:</strong> ${t.reason}</div>
          <div style="font-size:.72rem;color:#6ee7b7"><strong>Acceptance:</strong> Trades >=${t.acceptance_criteria.min_trade_count}, PF >=${t.acceptance_criteria.min_profit_factor}</div>
        </div>
      `).join('');
      _rlSetStatus('Plan generated', _rlCurrentRunId);
    } catch (e) {
      alert('Plan Generation failed: ' + e.message);
      _rlSetStatus('Error: ' + e.message, _rlCurrentRunId);
    }
  };

  window.rlSavePlan = function() {
    if (!_rlCurrentPlan) { alert('No plan exists'); return; }
    localStorage.setItem(`rl_plan_${_rlCurrentPlan.source_run_id}`, JSON.stringify(_rlCurrentPlan));
    alert('Plan saved to local cache');
  };

  window.rlDownloadPlan = function() {
    if (!_rlCurrentPlan) { alert('No plan exists'); return; }
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(_rlCurrentPlan, null, 2));
    const dlAnchor = document.createElement('a');
    dlAnchor.setAttribute("href",     dataStr);
    dlAnchor.setAttribute("download", `${_rlCurrentPlan.plan_id}_autopilot.json`);
    document.body.appendChild(dlAnchor);
    dlAnchor.click();
    dlAnchor.remove();
  };

  // ── Poll run status ────────────────────────────────────────────────────────
  function _rlPollStatus(runId) {
    if (_rlPollTimer) clearInterval(_rlPollTimer);
    _rlPollTimer = setInterval(async () => {
      try {
        const res  = await fetch(`/api/research-lab/run/${runId}`);
        const data = await res.json();
        const st   = data.status;
        if (st === 'complete') {
          const n = data.results_count ?? (data.summary && data.summary.total) ?? '?';
          _rlSetStatus(`Complete — ${n} results`, runId);
        } else if (st === 'failed') {
          _rlSetStatus('Failed: ' + (data.error || 'unknown error — check server logs'), runId);
        } else {
          _rlSetStatus('Running…', runId);
        }
        if (st === 'complete' || st === 'failed') {
          clearInterval(_rlPollTimer);
          window.rlLoadRuns();

          if (st === 'complete') window.rlShowResults(runId);

        }
      } catch (_) {}
    }, 3000);
  }

  let _rlIsRunningPlan = false;

  window.rlRunAutoPlan = async function() {
    if (_rlIsRunningPlan) {
      console.log('[autopilot] Plan already executing, ignoring duplicate click');
      return;
    }
    
    if (!_rlCurrentPlan || !_rlCurrentPlan.tests) { alert('Generate a plan first.'); return; }
    
    const selectedTests = [];
    _rlCurrentPlan.tests.forEach((t, idx) => {
      const chk = document.getElementById(`plan-chk-${idx}`);
      if (chk && chk.checked) {
        selectedTests.push(t);
      }
    });
    
    if (selectedTests.length === 0) { alert('No tests selected.'); return; }
    
    const runBtn = document.getElementById('rl-run-plan-btn');
    if (runBtn) {
      runBtn.disabled = true;
      runBtn.style.opacity = '0.5';
    }
    
    _rlIsRunningPlan = true;
    _rlSetStatus('Queuing selected auto-plan validation runs…', _rlCurrentRunId);
    
    try {
      const res = await fetch('/api/research-lab/run-auto-plan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          plan_id: _rlCurrentPlan.plan_id,
          source_run_id: _rlCurrentRunId,
          tests: selectedTests
        })
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      
      const childIds = data.child_run_ids || [];
      _rlSetStatus(`Queue started. Parent: ${_rlCurrentRunId} — Launched ${childIds.length} validation runs.`, _rlCurrentRunId);
      
      // Update UI with running state
      const planCards = document.getElementById('rl-plan-cards');
      if (planCards) {
        const progressDiv = document.createElement('div');
        progressDiv.id = 'rl-autopilot-progress';
        progressDiv.style.gridColumn = '1/-1';
        progressDiv.style.background = '#0f172a';
        progressDiv.style.border = '1px solid #1e293b';
        progressDiv.style.borderRadius = '6px';
        progressDiv.style.padding = '12px';
        progressDiv.style.marginTop = '10px';
        progressDiv.innerHTML = `
          <div style="font-weight: 600; color:#38bdf8; margin-bottom: 6px;">🤖 Autopilot Plan Executing</div>
          <div style="font-size:.75rem; opacity:.8;"><strong>Parent Run:</strong> ${_rlCurrentRunId}</div>
          <div style="font-size:.75rem; opacity:.8;"><strong>Tests Selected:</strong> ${selectedTests.length}</div>
          <div style="font-size:.75rem; color:#fbbf24; margin-top:6px;" id="rl-autopilot-status">Polling child runs...</div>
          <div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:8px;" id="rl-autopilot-child-chips">
            ${childIds.map(cid => `<span id="chip-${cid}" style="font-size:.72rem; padding:3px 6px; background:#1e293b; color:#94a3b8; border-radius:4px; border:1px solid #334155;">${cid} (polling)</span>`).join('')}
          </div>
        `;
        planCards.appendChild(progressDiv);
      }
      
      // Begin polling child runs
      _rlPollChildRuns(childIds, _rlCurrentRunId);
      
    } catch (e) {
      alert('Failed to launch plan tests: ' + e.message);
      _rlSetStatus('Error: ' + e.message, _rlCurrentRunId);
      
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.style.opacity = '1.0';
      }
      _rlIsRunningPlan = false;
    }
  };

  function _rlPollChildRuns(childIds, parentId) {
    if (childIds.length === 0) {
      _rlFinishAutopilotExecution(parentId);
      return;
    }
    
    let pending = [...childIds];
    const statusText = document.getElementById('rl-autopilot-status');
    
    const pollInterval = setInterval(async () => {
      try {
        for (let i = pending.length - 1; i >= 0; i--) {
          const cid = pending[i];
          const res = await fetch(`/api/research-lab/run-status?run_id=${cid}`);
          const data = await res.json();
          
          if (data.status === 'complete' || data.status === 'failed') {
            pending.splice(i, 1);
            const chip = document.getElementById(`chip-${cid}`);
            if (chip) {
              chip.style.background = data.status === 'complete' ? '#064e3b' : '#7f1d1d';
              chip.style.color = data.status === 'complete' ? '#6ee7b7' : '#fca5a5';
              chip.textContent = `${cid} (${data.status})`;
            }
          }
        }
        
        if (statusText) {
          statusText.textContent = `Polling child runs... (${childIds.length - pending.length}/${childIds.length} complete)`;
        }
        
        if (pending.length === 0) {
          clearInterval(pollInterval);
          if (statusText) statusText.textContent = '🤖 All child runs finalized.';
          _rlFinishAutopilotExecution(parentId);
        }
      } catch (e) {
        console.error('[autopilot] error polling children', e);
      }
    }, 4000);
  }

  function _rlFinishAutopilotExecution(parentId) {
    const runBtn = document.getElementById('rl-run-plan-btn');
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.style.opacity = '1.0';
    }
    _rlIsRunningPlan = false;
    window.rlLoadRuns();

    
    const progressDiv = document.getElementById('rl-autopilot-progress');
    if (progressDiv) {
      const openBtn = document.createElement('button');
      openBtn.id = "rl-open-autopilot-result-btn";
      openBtn.textContent = '📂 Open Autopilot Result';
      openBtn.style.cssText = 'margin-top:10px; background:linear-gradient(135deg,#0369a1,#0284c7); color:#fff; border:none; padding:6px 12px; border-radius:5px; cursor:pointer; font-size:.75rem;';
      openBtn.onclick = () => window.rlOpenAutopilotResult(parentId);

      progressDiv.appendChild(openBtn);
    }
  }

  window.rlOpenAutopilotResult = async function(parentId) {
    console.log('[autopilot] Open button clicked', parentId);
    _rlSetStatus('Loading aggregated autopilot validation results…', parentId);
    
    try {
      const res = await fetch(`/api/research-lab/autopilot-result?parent_run_id=${parentId}`);
      console.log('[autopilot] Response received', res.status);
      const data = await res.json();
      
      if (data.error) throw new Error(data.error);
      
      console.log('[autopilot] Rendering aggregate summary results', data);
      
      const agg = data.aggregate_summary || {};
      const statusColor = data.aggregate_classification === 'CONFIRMED' ? '#6ee7b7' 
                        : data.aggregate_classification === 'WEAKENED' ? '#fbbf24' : '#f87171';
                        
      let mainFinding = data.aggregate_classification === 'CONFIRMED' ? 'At least one strategy configuration successfully passed validation criteria across expanded datasets.' : 'Strategies failed to meet strict validation thresholds on broader testing sets.';

      let aggregateHtml = `
        <div style="background:#111827; border:1px solid #38bdf8; padding:16px; border-radius:8px; margin-bottom:16px;">
          <div style="font-size:1.1rem; font-weight:700; color:#38bdf8; display:flex; align-items:center; gap:8px; margin-bottom:8px;">
            🤖 Autopilot Validation Results
          </div>
          <div style="font-size:.85rem; margin-bottom:12px;">
             <strong>Research Decision:</strong> <span style="color:${statusColor}; font-weight:bold; padding:2px 6px; background:#1e293b; border-radius:4px; margin-left:4px;">${data.aggregate_classification}</span><br/>
             <div style="margin-top:6px;"><strong>Main Finding:</strong> <span style="opacity:.8;">${mainFinding}</span></div>
          </div>
          <div style="display:grid; grid-template-columns: repeat(5, 1fr); gap:12px; margin-top:12px; font-size:.85rem;">
            <div style="background:#0f172a; padding:10px; border-radius:6px; border:1px solid #1e293b; text-align:center;">
              <div style="opacity:.6; font-size:.75rem;">COMPLETED</div>
              <div style="font-size:1.3rem; font-weight:600; color:#cdd6e0;">${agg.tests_completed || 0}</div>
            </div>
            <div style="background:#0f172a; padding:10px; border-radius:6px; border:1px solid #1e293b; text-align:center;">
              <div style="color:#6ee7b7; font-size:.75rem;">CONFIRMED</div>
              <div style="font-size:1.3rem; font-weight:600; color:#6ee7b7;">${agg.confirmed || 0}</div>
            </div>
            <div style="background:#0f172a; padding:10px; border-radius:6px; border:1px solid #1e293b; text-align:center;">
              <div style="color:#fbbf24; font-size:.75rem;">WEAKENED</div>
              <div style="font-size:1.3rem; font-weight:600; color:#fbbf24;">${agg.weakened || 0}</div>
            </div>
            <div style="background:#0f172a; padding:10px; border-radius:6px; border:1px solid #1e293b; text-align:center;">
              <div style="color:#f87171; font-size:.75rem;">REJECTED</div>
              <div style="font-size:1.3rem; font-weight:600; color:#f87171;">${agg.rejected || 0}</div>
            </div>
            <div style="background:#0f172a; padding:10px; border-radius:6px; border:1px solid #1e293b; text-align:center;">
              <div style="opacity:.6; font-size:.75rem;">NEEDS MORE</div>
              <div style="font-size:1.3rem; font-weight:600; color:#94a3b8;">${agg.needs_more_data || 0}</div>
            </div>
          </div>
        </div>
      `;
      
      const allStrats = [];
      (data.child_runs || []).forEach(cr => {
        (cr.ranked_strategies || []).forEach(s => {
          s._run_id = cr.run_id;
          allStrats.push(s);
        });
      });
      
      const groups = {};
      allStrats.forEach(s => {
        const key = `${s.strategy_name}|${s.family || s.family_name}|${s.timeframe}`;
        if (!groups[key]) {
          groups[key] = {
            strategy: s.strategy_name,
            family: s.family || s.family_name,
            timeframe: s.timeframe,
            strats: [],
            symbols: new Set(),
            confirmed: [],
            weakened: [],
            rejected: [],
            totalPF: 0,
            totalNet: 0,
            totalRobust: 0
          };
        }
        const g = groups[key];
        g.strats.push(s);
        g.symbols.add(s.symbol);
        if (s.status === 'STRONG_CANDIDATE') g.confirmed.push(s);
        else if (s.status === 'WEAK_CANDIDATE') g.weakened.push(s);
        else if (s.status === 'REJECT') g.rejected.push(s);
        
        g.totalPF += s.profit_factor || 0;
        g.totalNet += s.net_return || 0;
        g.totalRobust += s.robustness_score || 0;
      });
      
      let childListHtml = Object.values(groups).map(g => {
        const c = g.strats.length;
        const avgPF = (g.totalPF / c).toFixed(2);
        const avgNet = ((g.totalNet / c) * 100).toFixed(1) + '%';
        const avgRob = (g.totalRobust / c).toFixed(2);
        
        const bestSyms = g.confirmed.map(x => x.symbol).join(', ') || 'None';
        const weakSyms = [...g.weakened, ...g.rejected].map(x => x.symbol).join(', ') || 'None';
        
        let stratRowsHtml = g.strats.map(strat => `
          <tr style="font-size:.75rem; border-top:1px solid #1e293b;">
            <td style="padding:6px 8px; font-weight:600; color:#f3f4f6;">${strat.strategy_name || ''}</td>
            <td style="padding:6px 8px;">${strat.symbol || ''}</td>
            <td style="padding:6px 8px; opacity:.7;">${strat.timeframe || ''}</td>
            <td style="padding:6px 8px;">${strat.trade_count ?? 0}</td>
            <td style="padding:6px 8px;">${((strat.win_rate ?? 0) * 100).toFixed(1)}%</td>
            <td style="padding:6px 8px;">${(strat.profit_factor ?? 0).toFixed(2)}</td>
            <td style="padding:6px 8px; color:${(strat.net_return ?? 0) >= 0 ? '#6ee7b7' : '#f87171'}">${((strat.net_return ?? 0)*100).toFixed(1)}%</td>
            <td style="padding:6px 8px; opacity:.7;">${(strat.robustness_score ?? 0).toFixed(2)}</td>
            <td style="padding:6px 8px; font-weight:600; color:${strat.status === 'STRONG_CANDIDATE' ? '#6ee7b7' : strat.status === 'WEAK_CANDIDATE' ? '#fbbf24' : '#f87171'}">${strat.status || ''}</td>
            <td style="padding:6px 8px; font-family:monospace; cursor:pointer; color:#38bdf8; text-decoration:underline;" onclick="rlShowResults('${strat._run_id}')">📂 ${strat._run_id.slice(-4)}</td>
          </tr>
        `).join('');

        return `
          <div style="background:#0f172a; border:1px solid #1e293b; border-radius:8px; padding:12px; margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
              <div>
                <span style="color:#f3f4f6; font-weight:600; font-size:1rem;">${g.strategy}</span>
                <span style="opacity:.6; font-size:.8rem; margin-left:8px; padding-left:8px; border-left:1px solid #334155;">${g.family} • ${g.timeframe} • ${g.symbols.size} symbols</span>
              </div>
              <div style="font-size:.75rem; display:flex; gap:12px; padding:4px 8px; background:#111827; border-radius:4px;">
                <span style="color:#6ee7b7; font-weight:600;">Confirmed: ${g.confirmed.length}</span>
                <span style="color:#fbbf24;">Weakened: ${g.weakened.length}</span>
                <span style="color:#f87171;">Rejected: ${g.rejected.length}</span>
              </div>
            </div>
            
            <div style="display:flex; flex-wrap:wrap; gap:16px; font-size:.75rem; margin-bottom:12px; background:#111827; padding:10px; border-radius:4px; border:1px solid #1e293b;">
               <div><span style="opacity:.6; margin-right:4px;">Avg PF:</span> <strong>${avgPF}</strong></div>
               <div><span style="opacity:.6; margin-right:4px;">Avg Net:</span> <strong style="color:${g.totalNet >= 0 ? '#6ee7b7' : '#f87171'}">${avgNet}</strong></div>
               <div><span style="opacity:.6; margin-right:4px;">Avg Robustness:</span> <strong>${avgRob}</strong></div>
               <div style="margin-left:auto;"><span style="color:#6ee7b7; margin-right:4px;">Best:</span> ${bestSyms}</div>
               <div><span style="color:#fbbf24; margin-right:4px;">Weak:</span> ${weakSyms}</div>
            </div>
            
            <div style="overflow-x:auto;">
              <table style="width:100%; border-collapse:collapse; text-align:left;">
                <thead>
                  <tr style="font-size:.72rem; opacity:.6; text-transform:uppercase;">
                    <th style="padding:4px 8px;">Strategy</th>
                    <th style="padding:4px 8px;">Symbol</th>
                    <th style="padding:4px 8px;">TF</th>
                    <th style="padding:4px 8px;">Trades</th>
                    <th style="padding:4px 8px;">WR</th>
                    <th style="padding:4px 8px;">PF</th>
                    <th style="padding:4px 8px;">Net Ret</th>
                    <th style="padding:4px 8px;">Robust</th>
                    <th style="padding:4px 8px;">Status</th>
                    <th style="padding:4px 8px;">Source</th>
                  </tr>
                </thead>
                <tbody>
                  ${stratRowsHtml}
                </tbody>
              </table>
            </div>
            
            <div style="margin-top:12px; padding-top:12px; border-top:1px dashed #1e293b; font-size:.8rem; display:flex; justify-content:space-between; align-items:center;">
               <div>
                 <strong style="color:#38bdf8;">Next Research Action:</strong> 
                 <span style="opacity:.8; margin-left:4px;">${g.confirmed.length > 0 ? 'Expand symbols and time windows to confirm global robustness.' : 'Pause or compare against alternative strategy.'}</span>
               </div>
               <button onclick="rlGenerateFollowUp('${g.strategy}', '${g.timeframe}', '${g.family}')" style="background:#1e293b; color:#38bdf8; border:1px solid #38bdf8; padding:5px 12px; border-radius:4px; cursor:pointer; font-size:.75rem; font-weight:600; transition:all 0.2s;">⚡ Generate Follow-Up Plan</button>
            </div>
          </div>
        `;
      }).join('');
      
      const targetTable = document.getElementById('rl-ranked-table');
      if (targetTable) {
        targetTable.innerHTML = aggregateHtml + `
          <div style="font-size:.85rem; font-weight:600; opacity:.6; margin-bottom:12px; letter-spacing:.05em; margin-top:20px;">GROUPED STRATEGY OUTCOMES</div>
          ${childListHtml || '<div style="font-size:.8rem; opacity:.5;">No child results aggregated.</div>'}
        `;
      }
      
      _rlSetStatus('Aggregated validation results loaded successfully.', parentId);
      
    } catch (e) {
      console.error('[autopilot] Aggregate failure', e);
      _rlSetStatus('Failed loading autopilot metrics: ' + e.message, parentId);
      
      // Fallback
      const targetTable = document.getElementById('rl-ranked-table');
      if (targetTable) {
        targetTable.innerHTML = `
          <div style="background:#7f1d1d; color:#fca5a5; border:1px solid #f87171; padding:12px; border-radius:6px; margin-bottom:12px; font-size:.8rem;">
            ⚠️ Aggregation Error: ${e.message}. Individual child datasets may still be inspected in isolation via the primary sidebar hierarchy.
          </div>
        `;
      }
    }
  };

  // Launch a focused follow-up run for a different family on the same TF
  window.rlGenerateFollowUp = async function(strat, tf, fam) {
    const _altMap = {
      trend_momentum: ['pullback','mean_reversion'],
      pullback: ['trend_momentum','breakout'],
      breakout: ['mean_reversion','volatility'],
      mean_reversion: ['volatility','pullback'],
      volatility: ['engine_b_proxy','mean_reversion'],
      engine_b_proxy: ['engine_d_proxy','breakout'],
      engine_d_proxy: ['mean_reversion','breakout'],
    };
    const alts = _altMap[fam] || ['trend_momentum'];
    const altFamily = alts[0];
    _rlSetStatus(`Queuing follow-up: ${altFamily} on ${tf} (alternative to ${fam})…`, _rlCurrentRunId);
    document.getElementById('rl-status').style.display = 'block';
    try {
      const payload = { mode: 'tiny', direction: 'both', run_ai_review: false,
        families: [altFamily], timeframes: tf ? [tf] : undefined };
      const res = await fetch('/api/research-lab/run', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      _rlCurrentRunId = data.run_id;
      _rlSetStatus(`Follow-up started — testing ${altFamily}`, data.run_id);
      _rlPollStatus(data.run_id);
    } catch (e) {
      _rlSetStatus('Error starting follow-up: ' + e.message, '');
    }
  };

  // Launch a one-click alternative-family run (from the all-rejected banner)
  window.rlTryAlternative = async function(altFamily) {
    _rlSetStatus(`Queuing alternative run: ${altFamily}…`, '');
    document.getElementById('rl-status').style.display = 'block';
    document.getElementById('rl-results').style.display = 'none';
    document.getElementById('rl-ai-panel').style.display = 'none';
    try {
      const payload = { mode: 'tiny', direction: 'both', run_ai_review: false,
        families: [altFamily] };
      const res = await fetch('/api/research-lab/run', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      _rlCurrentRunId = data.run_id;
      _rlSetStatus(`Alternative run started — ${altFamily}`, data.run_id);
      _rlPollStatus(data.run_id);
    } catch (e) {
      _rlSetStatus('Error: ' + e.message, '');
    }
  };


  // ── Load runs list ─────────────────────────────────────────────────────────
  window.rlLoadRuns = async function() {
    const el = document.getElementById('rl-runs-list');
    try {
      const res  = await fetch('/api/research-lab/runs');
      const data = await res.json();
      const runs = data.runs || [];
      if (!runs.length) { el.textContent = 'No runs yet.'; return; }
      
      const parents = runs.filter(r => !r.parent_run_id);
      const children = runs.filter(r => r.parent_run_id);

      el.innerHTML = parents.slice(0, 15).map(r => {
        const myChildren = children.filter(c => c.parent_run_id === r.run_id);
        
        let parentHtml = `
          <div style="display:flex;gap:10px;align-items:center;padding:6px 10px;border-radius:6px;
                      background:#111827;margin-bottom:4px;cursor:pointer;border:1px solid #1a2535"
               onclick="rlShowResults('${r.run_id}')">
            <span style="font-family:monospace;opacity:.7;font-size:.75rem">${r.run_id}</span>
            <span style="font-size:.75rem;opacity:.5">${r.mode || ''}</span>
            <span style="margin-left:auto;display:flex;gap:8px">
              ${r.strong != null ? `<span style="color:#6ee7b7;font-size:.73rem">${r.strong} strong</span>` : ''}
              ${r.weak   != null ? `<span style="color:#fbbf24;font-size:.73rem">${r.weak} weak</span>`   : ''}
            </span>
          </div>`;
          
        if (myChildren.length > 0) {
          const childListHtml = myChildren.map(c => `
            <div style="display:flex;gap:10px;align-items:center;padding:4px 8px;border-radius:4px;
                        background:#0f172a;margin-bottom:2px;margin-left:20px;cursor:pointer;border:1px dashed #1e293b"
                 onclick="rlShowResults('${c.run_id}')">
              <span style="color:#38bdf8;font-size:.68rem;font-weight:600;">↳ Autopilot validation</span>
              <span style="font-family:monospace;opacity:.6;font-size:.7rem">${c.run_id}</span>
            </div>
          `).join('');
          parentHtml += `<div style="margin-bottom:6px;">${childListHtml}</div>`;
        }
        
        return parentHtml;
      }).join('');
    } catch (e) {
      el.textContent = 'Error loading runs.';
    }
  };

  function _rlRenderResearchAuditSections(recommendations, nextTests) {
    const recs = recommendations || [];
    _rlCurrentRecommendations = recs;
    const tests = nextTests || [];
    if (!recs.length && !tests.length) return '';

    const zones = ['scalp', 'intra', 'swing'];
    const zoneCards = zones.map(z => {
      const zRows = recs.filter(r => (r.timeframe_zone || r.zone || '') === z);
      const add = zRows.filter(r => String(r.recommendation || '').trim().toUpperCase() === 'ADD').length;
      const keep = zRows.filter(r => String(r.recommendation || '').trim().toUpperCase() === 'KEEP').length;
      const retest = zRows.filter(r => ['RETEST', 'WATCHLIST_ONLY'].includes(String(r.recommendation || '').trim().toUpperCase())).length;
      const remove = zRows.filter(r => ['REMOVE_OR_DEMOTE', 'REJECT'].includes(String(r.recommendation || '').trim().toUpperCase())).length;
      const best = zRows[0] || {};
      const zClr = z === 'scalp' ? '#f472b6' : z === 'intra' ? '#38bdf8' : '#a78bfa';
      return `<div style="background:#0f172a;border:1px solid ${zClr};border-radius:8px;padding:10px;">
        <div style="font-size:.68rem;color:${zClr};text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">${z}</div>
        <div style="font-size:.76rem;color:#cdd6e0">ADD ${add} · KEEP ${keep} · RETEST ${retest} · REMOVE ${remove}</div>
        <div style="font-size:.7rem;color:#94a3b8;margin-top:5px">Top: ${best.engine || '—'} ${best.strategy_name || '—'} ${best.structure_context ? `(${best.structure_context})` : ''}</div>
      </div>`;
    }).join('');

    const recTableRows = recs.slice(0, 12).map((r, idx) => {
      const rec = String(r.recommendation || '—').trim().toUpperCase();
      const canRetest = ['ADD', 'RETEST', 'WATCHLIST_ONLY'].includes(rec);
      const recClr = rec === 'ADD' ? '#6ee7b7' : rec === 'KEEP' ? '#38bdf8' : rec === 'REMOVE_OR_DEMOTE' || rec === 'REJECT' ? '#f87171' : rec === 'WATCHLIST_ONLY' ? '#fbbf24' : '#c4b5fd';
      const zone = r.timeframe_zone || r.zone || '—';
      const zClr = zone === 'scalp' ? '#f472b6' : zone === 'intra' ? '#38bdf8' : zone === 'swing' ? '#a78bfa' : '#64748b';
      const delta = Number(r.baseline_delta_oos);
      const deltaTxt = Number.isFinite(delta) ? delta.toFixed(4) : '—';
      return `<tr style="border-top:1px solid #1e293b">
        <td style="padding:6px 8px;text-align:center">${canRetest ? `<input type="checkbox" class="rl-retest-row" value="${idx}" style="accent-color:#8b5cf6">` : ''}</td>
        <td style="padding:6px 8px;color:${recClr};font-weight:700">${rec}</td>
        <td style="padding:6px 8px;color:#cdd6e0">${r.engine || '—'}</td>
        <td style="padding:6px 8px;color:#94a3b8">${r.engine_component || '—'}</td>
        <td style="padding:6px 8px;color:#e2e8f0">${r.strategy_name || '—'}</td>
        <td style="padding:6px 8px;color:#94a3b8">${r.pair_group || r.market_group || '—'}</td>
        <td style="padding:6px 8px;color:${zClr};font-weight:600">${zone}</td>
        <td style="padding:6px 8px;color:#94a3b8">${r.structure_context || '—'}</td>
        <td style="padding:6px 8px;color:#6ee7b7">${deltaTxt}</td>
      </tr>`;
    }).join('');

    const nextRows = tests.slice(0, 8).map(t => `<tr style="border-top:1px solid #1e293b">
      <td style="padding:6px 8px;color:#cdd6e0">${t.engine || '—'}</td>
      <td style="padding:6px 8px;color:#94a3b8">${t.engine_component || '—'}</td>
      <td style="padding:6px 8px;color:#94a3b8">${t.pair_group || t.market_group || '—'}</td>
      <td style="padding:6px 8px;color:#a78bfa">${t.timeframe_zone || '—'}</td>
      <td style="padding:6px 8px;color:#f87171">${t.failed_strategies || '—'}</td>
      <td style="padding:6px 8px;color:#6ee7b7">${t.suggested_strategies || '—'}</td>
    </tr>`).join('');

    return `<div style="background:#0b1220;border:1px solid #334155;border-radius:10px;padding:14px;margin-bottom:16px;">
      <div style="font-size:.95rem;font-weight:700;color:#93c5fd;margin-bottom:8px">Engine A/B Research Audit</div>
      <div style="font-size:.75rem;color:#94a3b8;margin-bottom:12px">Automated audit split by scalp, intra, and swing. Recommendations compare candidates against the current engine baseline for the same group and zone.</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px">${zoneCards}</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px">
        <button onclick="rlRetestSelectedCandidates()" style="background:linear-gradient(135deg,#1d4ed8,#38bdf8);color:#fff;border:none;padding:7px 14px;border-radius:7px;cursor:pointer;font-size:.78rem;font-weight:700">Retest Selected</button>
        <button onclick="rlRetestCandidates('ADD')" style="background:linear-gradient(135deg,#065f46,#059669);color:#fff;border:none;padding:7px 14px;border-radius:7px;cursor:pointer;font-size:.78rem;font-weight:700">Retest ADD Candidates</button>
        <button onclick="rlRetestCandidates('RETEST')" style="background:linear-gradient(135deg,#6d28d9,#8b5cf6);color:#fff;border:none;padding:7px 14px;border-radius:7px;cursor:pointer;font-size:.78rem;font-weight:700">Retest RETEST Rows</button>
        <span style="font-size:.72rem;color:#94a3b8">Queues focused validation runs for selected recommendation rows, split by engine, group, and zone.</span>
      </div>
      <div style="font-size:.72rem;color:#64748b;font-weight:600;margin-bottom:6px;text-transform:uppercase">Add / Keep / Remove / Retest</div>
      <div style="overflow-x:auto;margin-bottom:14px"><table style="width:100%;border-collapse:collapse;font-size:.74rem"><thead><tr style="color:#64748b;text-align:left"><th style="padding:4px 8px;text-align:center">Pick</th><th style="padding:4px 8px">Rec</th><th style="padding:4px 8px">Engine</th><th style="padding:4px 8px">Component</th><th style="padding:4px 8px">Strategy</th><th style="padding:4px 8px">Group</th><th style="padding:4px 8px">Zone</th><th style="padding:4px 8px">Structure</th><th style="padding:4px 8px">Delta OOS</th></tr></thead><tbody>${recTableRows || '<tr><td colspan="9" style="padding:8px;color:#64748b">No recommendations available.</td></tr>'}</tbody></table></div>
      <div style="font-size:.72rem;color:#64748b;font-weight:600;margin-bottom:6px;text-transform:uppercase">Automated Next Tests</div>
      <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.74rem"><thead><tr style="color:#64748b;text-align:left"><th style="padding:4px 8px">Engine</th><th style="padding:4px 8px">Failed Component</th><th style="padding:4px 8px">Group</th><th style="padding:4px 8px">Zone</th><th style="padding:4px 8px">Failed Strategies</th><th style="padding:4px 8px">Try Next</th></tr></thead><tbody>${nextRows || '<tr><td colspan="6" style="padding:8px;color:#64748b">No automated follow-up tests required.</td></tr>'}</tbody></table></div>
    </div>`;
  }

  function _rlCandidatePayload(rows) {
    return (rows || []).map(r => ({
      recommendation: r.recommendation,
      engine: r.engine,
      engine_component: r.engine_component,
      strategy_name: r.strategy_name,
      symbol: r.symbol,
      timeframe: r.timeframe,
      direction: r.direction,
      pair_group: r.pair_group,
      timeframe_zone: r.timeframe_zone || r.zone,
      structure_context: r.structure_context,
    }));
  }

  async function _rlReadJsonResponse(res, label) {
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : {};
    } catch (e) {
      const preview = text ? text.slice(0, 80).replace(/\s+/g, ' ') : 'empty response';
      throw new Error(`${label} returned non-JSON (${res.status}). Preview: ${preview}. Restart Flask and hard refresh if this route was just added.`);
    }
    if (!res.ok) {
      throw new Error(data.error || data.message || `${label} failed with HTTP ${res.status}`);
    }
    return data;
  }

  async function _rlBuildRetestPlan(payload, rec, hasSelectedRows) {
    const primary = await fetch('/api/research-lab/retest-plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    try {
      return await _rlReadJsonResponse(primary, 'Retest plan');
    } catch (e) {
      const canFallback = rec === 'ADD' && !hasSelectedRows;
      if (!canFallback) throw e;
      const legacy = await fetch('/api/research-lab/retest-add-plan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      return await _rlReadJsonResponse(legacy, 'Legacy ADD retest plan');
    }
  }

  function _rlRecoTypesFromRows(rows) {
    const out = [];
    for (const r of rows || []) {
      const v = r && (r.recommendation != null ? r.recommendation : r.rec);
      const t = String(v != null ? v : '').trim().toUpperCase();
      if (t && t !== '—' && t !== '-') out.push(t);
    }
    return out;
  }

  window.rlRetestCandidates = async function(recommendation = 'ADD', selectedRows = null) {
    if (!_rlCurrentRunId) { alert('No Research Lab run selected.'); return; }
    const rec = String(recommendation || 'ADD').toUpperCase();
    const selectedPayload = selectedRows ? _rlCandidatePayload(selectedRows) : null;
    const fromPick = selectedRows ? [...new Set(_rlRecoTypesFromRows(selectedRows))] : [];
    const recoFilter = selectedRows
      ? (fromPick.length ? fromPick : ['ADD', 'RETEST', 'WATCHLIST_ONLY'])
      : [rec];
    const statusLabel = selectedRows ? 'selected' : rec;
    _rlSetStatus(`Building ${statusLabel} candidate retest plan…`, _rlCurrentRunId);
    try {
      const planPayload = {
        run_id: _rlCurrentRunId,
        max_tests: 12,
        recommendations: recoFilter,
        candidates: selectedPayload || undefined,
      };
      const plan = await _rlBuildRetestPlan(planPayload, rec, Boolean(selectedRows));
      if (plan.error) throw new Error(plan.error);
      if (!plan.tests || !plan.tests.length) {
        alert(plan.message || `No ${statusLabel} candidates found to retest.`);
        _rlSetStatus(`No ${statusLabel} candidates found to retest.`, _rlCurrentRunId);
        return;
      }
      const runRes = await fetch('/api/research-lab/run-auto-plan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          plan_id: plan.plan_id,
          source_run_id: _rlCurrentRunId,
          tests: plan.tests,
          idempotency_key: `${_rlCurrentRunId}_${plan.plan_id}`
        })
      });
      const data = await _rlReadJsonResponse(runRes, 'Retest run launch');
      if (data.error) throw new Error(data.error);
      const childIds = data.child_run_ids || [];
      _rlSetStatus(`Retest queued — launched ${childIds.length} focused ${statusLabel} validation runs.`, _rlCurrentRunId);
      _rlPollChildRuns(childIds, _rlCurrentRunId);
    } catch (e) {
      alert(`Failed to retest ${statusLabel} candidates: ` + e.message);
      _rlSetStatus('Retest error: ' + e.message, _rlCurrentRunId);
    }
  };

  window.rlRetestAddCandidates = function() {
    return window.rlRetestCandidates('ADD');
  };

  window.rlRetestSelectedCandidates = function() {
    const selected = Array.from(document.querySelectorAll('.rl-retest-row:checked'))
      .map(cb => _rlCurrentRecommendations[Number(cb.value)])
      .filter(Boolean);
    if (!selected.length) {
      alert('Select one or more ADD/RETEST rows first.');
      return;
    }
    return window.rlRetestCandidates('SELECTED', selected);
  };

  // ── Show results for a run ─────────────────────────────────────────────────
  window.rlShowResults = async function(runId) {
    _rlCurrentRunId = runId;
    document.getElementById('rl-status').style.display = 'block';
    _rlSetStatus('Loading results…', runId);

  try {
    let metaData = {};
    try {
      const metaRes = await fetch(`/api/research-lab/run/${runId}`);
      metaData = await metaRes.json();

      const metaFileRes = await fetch(`/api/research-lab/download/${runId}/run_meta.json`);
      if (metaFileRes.ok) {
        const fileData = await metaFileRes.json();
        metaData = { ...metaData, ...fileData };
      }
    } catch (e) {
      console.warn('Metadata resolution fallback:', e);
    }

  const res  = await fetch(`/api/research-lab/ranked/${runId}`);
  const data = await res.json();
  const rows = data.ranked || [];
  const recRows = data.recommendations || [];
  const nextTests = data.automated_next_tests || [];

  if (!rows.length) {
    document.getElementById('rl-ranked-table').innerHTML = recRows.length
      ? _rlRenderResearchAuditSections(recRows, nextTests)
      : '<em style="opacity:.5">No ranked results yet.</em>';
  } else {
    const cols = ['engine','engine_component','strategy_name','symbol','market_group','pair_group',
                  'timeframe_zone','timeframe','structure_context','direction','status','recommendation',
                  'trade_count','win_rate','profit_factor','is_return','oos_return',
                  'baseline_delta_pf','baseline_delta_oos','robustness_score','sqn','skip_reason','verdict'];
    const th = '<th style="padding:5px 8px;opacity:.6">Select</th>' + cols.map(c => {
      let label = c.toUpperCase().replace(/_/g, ' ');
      if (c === 'profit_factor') label = 'PF';
      if (c === 'baseline_delta_pf') label = 'DELTA PF';
      if (c === 'baseline_delta_oos') label = 'DELTA OOS';
      if (c === 'timeframe_zone') label = 'ZONE';
      if (c === 'is_return') label = 'IS RETURN';
      if (c === 'oos_return') label = 'OOS RETURN';
      return `<th style="padding:5px 8px;white-space:nowrap;opacity:.6">${label}</th>`;
    }).join('');
    const trs = rows.map((r, idx) => {
      const statusColor = r.status === 'STRONG_CANDIDATE' ? '#6ee7b7'
                        : r.status === 'WEAK_CANDIDATE'   ? '#fbbf24' : '#f87171';
      const radio = `<input type="radio" name="rl-selected-candidate" data-symbol="${r.symbol || ''}" data-tf="${r.timeframe || ''}" data-family="${r.family_name || r.family || ''}" data-strategy="${r.strategy_name || ''}" data-direction="${r.direction || ''}">`;
      return '<tr>' + `<td style="padding:4px 8px;border-top:1px solid #1a2535;text-align:center;">${radio}</td>` + cols.map(c => {
        let v = r[c] ?? '';
        if (c === 'timeframe_zone' || c === 'zone') { const zClr = v === 'scalp' ? '#f472b6' : v === 'intra' ? '#38bdf8' : v === 'swing' ? '#a78bfa' : '#64748b'; v = v ? `<span style="color:${zClr};font-weight:600">${v}</span>` : '—'; }
        if (c === 'engine') { const eClr = v === 'ENGINE_A' ? '#60a5fa' : v === 'ENGINE_B' ? '#a78bfa' : '#94a3b8'; v = v ? `<span style="color:${eClr};font-weight:700">${v}</span>` : '—'; }
        if (c === 'recommendation') {
          const recClr = v === 'ADD' ? '#6ee7b7' : v === 'KEEP' ? '#38bdf8' : v === 'REMOVE_OR_DEMOTE' || v === 'REJECT' ? '#f87171' : v === 'WATCHLIST_ONLY' ? '#fbbf24' : '#c4b5fd';
          v = v ? `<span style="color:${recClr};font-weight:700">${v}</span>` : '—';
        }
        if (c === 'status') v = `<span style="color:${statusColor}">${v}</span>`;
        if (c === 'verdict') {
          const isConfirmed = r.status === 'STRONG_CANDIDATE' || r.status === 'WEAK_CANDIDATE';
          v = isConfirmed ? '<span style="color:#6ee7b7">CONFIRMS</span>' : '<span style="color:#f87171">REJECTS</span>';
        }
        if (typeof v === 'number') v = v.toFixed ? v.toFixed(4) : v;
        return `<td style="padding:4px 8px;border-top:1px solid #1a2535">${v}</td>`;
      }).join('') + '</tr>';
    }).join('');

    let styleProfileHtml = '';
    if (metaData.trading_style) {
      const style = metaData.trading_style;
      const zoneSet = metaData.zone_set || '';
      const focus = metaData.validation_focus || [];
      const mGroup = metaData.market_group || '';
      const focusTags = (focus || []).map(f => `<span style="background:#1e3a8a; color:#93c5fd; padding:3px 8px; border-radius:4px; font-size:.7rem; border:1px solid #3b82f6;">${f}</span>`).join(' ');

      styleProfileHtml = `
        <div style="background:linear-gradient(135deg, #0f172a, #1e1b4b); border:1px solid #581c87; padding:16px; border-radius:10px; margin-bottom:16px;">
          <div style="font-size:1.05rem; font-weight:700; color:#c084fc; display:flex; align-items:center; gap:8px; margin-bottom:10px;">
            🌌 ${style.toUpperCase()} Style Context Profile
          </div>
          <div style="display:grid; grid-template-columns: 1fr 2fr; gap:12px; font-size:.8rem;">
            <div style="background:rgba(0,0,0,0.2); padding:10px; border-radius:6px; border:1px solid #334155;">
              <strong>Market Group:</strong> <span style="color:#e2e8f0; margin-left:4px;">${mGroup.toUpperCase()}</span><br/>
              <strong>Zone Set:</strong> <span style="color:#fb923c; margin-left:4px;">${zoneSet}</span><br/>
              <strong>Timeframes:</strong> <span style="color:#38bdf8; margin-left:4px;">${(metaData.timeframes || []).join(', ')}</span>
            </div>
            <div style="background:rgba(0,0,0,0.2); padding:10px; border-radius:6px; border:1px solid #334155;">
              <strong style="display:block; margin-bottom:6px; opacity:.8;">Focus Metrics & Objectives:</strong>
              <div style="display:flex; flex-wrap:wrap; gap:6px;">${focusTags || '<span style="opacity:.5">N/A</span>'}</div>
            </div>
          </div>
        </div>
      `;
    }

    let childVerdictHtml = '';
    if (metaData.parent_run_id) {
      let confirmedCount = 0, weakenedCount = 0, rejectedCount = 0, needsMoreCount = 0;
      rows.forEach(r => {
        if (r.status === 'STRONG_CANDIDATE') confirmedCount++;
        else if (r.status === 'WEAK_CANDIDATE') weakenedCount++;
        else if (r.status === 'REJECT') rejectedCount++;
        else if (r.status === 'NEEDS_MORE_DATA') needsMoreCount++;
      });
      const overallChildVerdict = confirmedCount > 0 ? '<span style="color:#6ee7b7">CONFIRMED</span>'
                                : weakenedCount > 0 ? '<span style="color:#fbbf24">WEAKENED</span>'
                                : '<span style="color:#f87171">REJECTED</span>';

      childVerdictHtml = `
        <div style="background:#0f172a; border:1px solid #334155; padding:12px; border-radius:8px; margin-bottom:12px; font-size:.8rem">
          <div style="font-weight: 600; color:#38bdf8; margin-bottom: 6px;">🛡️ Autopilot Validation Verdict: ${overallChildVerdict}</div>
          <div style="font-size:.75rem; opacity:.8; display:flex; gap:12px;">
            <span><strong>Parent ID:</strong> ${metaData.parent_run_id}</span>
            <span style="color:#6ee7b7">Confirmed: ${confirmedCount}</span>
            <span style="color:#fbbf24">Weakened: ${weakenedCount}</span>
            <span style="color:#f87171">Rejected: ${rejectedCount}</span>
            <span style="opacity:.6">Needs More: ${needsMoreCount}</span>
          </div>
        </div>
      `;
    }

    document.getElementById('rl-ranked-table').innerHTML = styleProfileHtml + childVerdictHtml +
      _rlRenderResearchAuditSections(recRows, nextTests) +
      `<table style="border-collapse:collapse;width:100%"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;

    // If every result is REJECT or NEEDS_MORE_DATA, show the adaptive pivot banner
    const _allRejected = rows.length > 0 && rows.every(r => r.status === 'REJECT' || r.status === 'NEEDS_MORE_DATA');
    if (_allRejected) {
      const _detectedFam = rows[0] && (rows[0].family_name || rows[0].family || '');
      const _altMap = {
        trend_momentum: ['pullback','mean_reversion'],
        pullback: ['trend_momentum','breakout'],
        breakout: ['mean_reversion','volatility'],
        mean_reversion: ['volatility','pullback'],
        volatility: ['engine_b_proxy','mean_reversion'],
        engine_b_proxy: ['engine_d_proxy','breakout'],
        engine_d_proxy: ['mean_reversion','breakout'],
      };
      const _alts = (_altMap[_detectedFam] || ['trend_momentum','mean_reversion']);
      const _altBtns = _alts.map(f =>
        `<button onclick="rlTryAlternative('${f}')" style="background:#1e3a2a;color:#6ee7b7;border:1px solid #065f46;padding:6px 12px;border-radius:5px;cursor:pointer;font-size:.77rem;font-weight:600">${f.replace(/_/g,' ')}</button>`
      ).join('');
      const _banner = document.createElement('div');
      _banner.innerHTML = `<div style="background:#1c1917;border:1px solid #78350f;border-radius:8px;padding:14px;margin-top:14px;">
        <div style="font-size:.85rem;font-weight:700;color:#fb923c;margin-bottom:8px;">⚠️ No profitable strategies found — try an alternative approach</div>
        <div style="font-size:.77rem;opacity:.75;margin-bottom:10px;">All tested configurations were rejected or produced insufficient trades. The autopilot planner can suggest a different family — or pick one below to run now.</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <span style="font-size:.74rem;opacity:.55;margin-right:4px;">Try instead:</span>
          ${_altBtns}
          <button onclick="rlGeneratePlan()" style="background:#1e293b;color:#38bdf8;border:1px solid #1e40af;padding:6px 12px;border-radius:5px;cursor:pointer;font-size:.77rem;margin-left:8px;">💡 Generate AI Plan</button>
        </div>
      </div>`;
      document.getElementById('rl-ranked-table').appendChild(_banner.firstElementChild);
    }
  }

  document.getElementById('rl-results').style.display = 'block';
  // Only show download / AI buttons when the run actually produced files
  const hasFiles = rows.length > 0 && !data.note;
  document.getElementById('rl-validate-btn').style.display = hasFiles ? 'inline-block' : 'none';
  document.getElementById('rl-ai-btn').style.display = hasFiles ? 'inline-block' : 'none';
  document.getElementById('rl-report-link').style.display = hasFiles ? 'inline' : 'none';

  // AI Research Plan container activation
  const planPanel = document.getElementById('rl-plan-panel');
  if (planPanel) planPanel.style.display = hasFiles ? 'block' : 'none';

  if (hasFiles) {
    document.getElementById('rl-report-link').href =
      `/api/research-lab/download/${runId}/research_report.md`;
  }
  _rlSetStatus(hasFiles ? 'Loaded' : 'Complete — no ranked results (all data unavailable or < min trades)', runId);

  // Try to load existing AI review only if files exist
  if (hasFiles) window.rlLoadAiReview(runId);

  } catch (e) {
    _rlSetStatus('Error: ' + e.message, runId);
  }
};

  // ── Run AI review ──────────────────────────────────────────────────────────
  window.rlRunAiReview = async function() {
    if (!_rlCurrentRunId) return;
    const btn = document.getElementById('rl-ai-btn');
    btn.disabled = true;
    btn.textContent = '✨ Analysing…';
    try {
      const res  = await fetch(`/api/research-lab/analyze/${_rlCurrentRunId}`, {method:'POST',
        headers:{'Content-Type':'application/json'}, body:'{}'});
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      window.rlLoadAiReview(_rlCurrentRunId);

    } catch (e) {
      alert('AI review failed: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '✨ Run AI Review';
    }
  };

  // ── Load existing AI review ────────────────────────────────────────────────
  window.rlLoadAiReview = async function(runId) {
    try {
      const res  = await fetch(`/api/research-lab/ai-review/${runId}`);
      if (!res.ok) return;
      const data = await res.json();

      const panel = document.getElementById('rl-ai-panel');
      panel.style.display = 'block';

      const ap = data.action_plan || {};
      document.getElementById('rl-ai-verdict').textContent =
        ap.overall_verdict || '(No verdict)';

      // Top candidates
      const cands = (ap.top_candidates || []).slice(0, 5);
      document.getElementById('rl-ai-candidates').innerHTML = cands.length
        ? '<div style="font-size:.75rem;opacity:.55;margin-bottom:6px">TOP CANDIDATES</div>' +
          cands.map(c => {
            const col = c.label === 'STRONG_CANDIDATE' ? '#6ee7b7' : '#fbbf24';
            return `<span style="display:inline-block;margin:2px 4px;padding:3px 8px;
                    background:#111827;border:1px solid #2a3a50;border-radius:4px;font-size:.78rem">
                    <span style="color:${col}">●</span> ${c.strategy} · ${c.symbol} · ${c.tf}</span>`;
          }).join('') : '';

      // Engine recommendations
      const erDiv = document.getElementById('rl-ai-engine-recs');
      const engines = [['Engine A', ap.engine_a || {}], ['Engine B', ap.engine_b || {}], ['Engine D', ap.engine_d || {}]];
      erDiv.innerHTML = engines.map(([label, eng]) => `
        <div style="background:#111827;border:1px solid #1a2535;border-radius:8px;padding:10px">
          <div style="font-size:.75rem;font-weight:700;margin-bottom:8px;color:#7fa8c8">${label}</div>
          ${_rlEngineSection('Keep', eng.keep, '#6ee7b7')}
          ${_rlEngineSection('Remove', eng.remove_or_demote, '#f87171')}
          ${_rlEngineSection('Next tests', eng.next_tests, '#fbbf24')}
        </div>`).join('');

      // Next tiny test recommendation with one-click run
      const ntDiv = document.getElementById('rl-ai-next-test');
      const nt = ap.next_tiny_test || {};
      const doNot = (ap.do_not_do_next || []).slice(0, 3);
      window._rlNextTestRec = nt;   // stored so onclick can reference without serialising into attr
      if (nt.strategy_families || nt.symbols) {
        const ntSyms = (nt.symbols || []).join(', ') || 'auto';
        const ntTfs  = (nt.timeframes || []).join(', ') || 'H1,H4';
        const ntFams = (nt.strategy_families || []).join(', ') || 'trend_momentum';
        const reasonHtml = nt.reason
          ? '<div style="font-size:.73rem;opacity:.7;margin-bottom:10px">' + nt.reason + '</div>'
          : '';
        const doNotHtml = doNot.length
          ? '<div style="font-size:.73rem;color:#f87171;margin-bottom:10px"><strong>Avoid:</strong> ' + doNot.join(' \xb7 ') + '</div>'
          : '';
        ntDiv.innerHTML =
          '<div style="background:#0f1c2e;border:1px solid #1e3a5f;border-radius:8px;padding:12px">' +
          '<div style="font-size:.78rem;font-weight:700;color:#38bdf8;margin-bottom:8px">🎯 AI Recommended Next Test</div>' +
          '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:.75rem;margin-bottom:10px">' +
            '<div><span style="opacity:.5">Symbols:</span><br><strong>' + ntSyms + '</strong></div>' +
            '<div><span style="opacity:.5">Timeframes:</span><br><strong>' + ntTfs + '</strong></div>' +
            '<div><span style="opacity:.5">Families:</span><br><strong>' + ntFams + '</strong></div>' +
          '</div>' +
          reasonHtml + doNotHtml +
          '<button onclick="rlRunAiRecommendation(window._rlNextTestRec)" style="background:linear-gradient(135deg,#0369a1,#0284c7);color:#fff;border:none;padding:6px 14px;border-radius:5px;cursor:pointer;font-size:.78rem;font-weight:600">▶ Run This Recommendation</button>' +
          '</div>';
      } else {
        ntDiv.innerHTML = '';
      }

      // Full review text
      document.getElementById('rl-ai-raw').textContent = data.review_md || '(empty)';
    } catch (_) {}
  };

  // Run the AI's next-test recommendation directly
  window.rlRunAiRecommendation = async function(nt) {
    const syms = Array.isArray(nt.symbols) && nt.symbols.length ? nt.symbols : undefined;
    const tfs   = Array.isArray(nt.timeframes) && nt.timeframes.length ? nt.timeframes : undefined;
    const fams  = Array.isArray(nt.strategy_families) && nt.strategy_families.length ? nt.strategy_families : undefined;
    _rlSetStatus('Queuing AI-recommended run…', '');
    document.getElementById('rl-status').style.display = 'block';
    document.getElementById('rl-results').style.display = 'none';
    document.getElementById('rl-ai-panel').style.display = 'none';
    try {
      const payload = { mode: 'tiny', direction: 'both', run_ai_review: false };
      if (syms) payload.symbols = syms;
      if (tfs)  payload.timeframes = tfs;
      if (fams) payload.families = fams;
      const res = await fetch('/api/research-lab/run', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      _rlCurrentRunId = data.run_id;
      _rlSetStatus(`AI recommendation run started`, data.run_id);
      _rlPollStatus(data.run_id);
    } catch (e) {
      _rlSetStatus('Error: ' + e.message, '');
    }
  };

  function _rlEngineSection(label, items, color) {
    if (!items || !items.length) return '';
    return `<div style="margin-bottom:5px">
      <div style="font-size:.68rem;opacity:.5;margin-bottom:2px">${label}</div>
      ${items.map(i => `<div style="font-size:.75rem;color:${color};padding:1px 0">• ${i}</div>`).join('')}
    </div>`;
  }

  function _rlSetStatus(text, runId) {
    document.getElementById('rl-status-text').textContent = text;
    document.getElementById('rl-run-id').textContent = runId ? `Run: ${runId}` : '';
  }

  // ── One-Click Autopilot Cockpit ────────────────────────────────────────────
  let _rlSessId = null;
  let _rlSessPollTimer = null;

  window.rlSessStart = async function() {
    const market_group   = document.getElementById('rl-sess-market-group').value;
    const trading_style  = document.getElementById('rl-sess-trading-style').value;
    const research_depth = document.getElementById('rl-sess-research-depth').value;

    const startBtn = document.getElementById('rl-sess-start-btn');
    const stopBtn  = document.getElementById('rl-sess-stop-btn');
    startBtn.disabled = true;
    startBtn.textContent = '⏳ Starting…';
    stopBtn.style.display = 'none';

    try {
      const res = await fetch('/api/research-lab/session-autopilot/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ market_group, trading_style, research_depth }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      _rlSessId = data.session_id;
      document.getElementById('rl-sess-card').style.display = 'block';
      stopBtn.style.display = 'inline-block';
      _rlSessRenderStatus(data);
      _rlSessPoll(_rlSessId);
    } catch (e) {
      alert('Failed to start session: ' + e.message);
    } finally {
      startBtn.disabled = false;
      startBtn.textContent = '🚀 Start One-Click Autopilot';
    }
  };

  window.rlSessStop = async function() {
    if (!_rlSessId) return;
    try {
      const res = await fetch(`/api/research-lab/session-autopilot/stop/${_rlSessId}`, {method:'POST'});
      const data = await res.json();
      _rlSessRenderStatus(data);
      if (_rlSessPollTimer) { clearInterval(_rlSessPollTimer); _rlSessPollTimer = null; }
      document.getElementById('rl-sess-stop-btn').style.display = 'none';
    } catch (e) {
      console.error('[sess] stop failed', e);
    }
  };

  function _rlSessPoll(sessionId) {
    if (_rlSessPollTimer) clearInterval(_rlSessPollTimer);
    _rlSessPollTimer = setInterval(async () => {
      try {
        const res  = await fetch(`/api/research-lab/session-autopilot/status/${sessionId}`);
        const data = await res.json();
        _rlSessRenderStatus(data);
        const terminal = ['COMPLETE','FAILED','CANCELLED'];
        if (terminal.includes(data.status)) {
          clearInterval(_rlSessPollTimer);
          _rlSessPollTimer = null;
          document.getElementById('rl-sess-stop-btn').style.display = 'none';
          if (data.status === 'COMPLETE') {
            // Load final result for scoped rows
            const rres = await fetch(`/api/research-lab/session-autopilot/result/${sessionId}`);
            const rdata = await rres.json();
            _rlSessRenderStatus(rdata);
          }
        }
      } catch (e) {
        console.warn('[sess] poll error', e);
      }
    }, 5000);
  }

  function _rlSessRenderStatus(data) {
    if (!data) return;
    const $ = id => document.getElementById(id);
    $('rl-sess-id').textContent        = data.session_id || '';
    $('rl-sess-phase').textContent     = data.current_phase || data.status || '';
    $('rl-sess-pct').textContent       = (data.progress_pct != null) ? data.progress_pct + '%' : '';
    $('rl-sess-disc-run').textContent  = data.discovery_run_id || '—';
    $('rl-sess-val-runs').textContent  = (data.validation_run_ids || []).length
                                         ? (data.validation_run_ids || []).length + ' run(s)' : '—';
    $('rl-sess-ai-status').textContent = data.ai_review_status || '';

    const verdict = data.final_verdict || '';
    const verdictEl = $('rl-sess-verdict');
    if (verdict) {
      $('rl-sess-verdict-row').style.display = 'block';
      const colors = {
        IMPLEMENTATION_CANDIDATE: ['#065f46','#6ee7b7'],
        VALIDATION_CANDIDATE:     ['#1e3a8a','#93c5fd'],
        WEAK_EDGE:                ['#713f12','#fde68a'],
        REJECTED:                 ['#7f1d1d','#fca5a5'],
        NEEDS_MORE_DATA:          ['#1c1c1c','#9ca3af'],
        PIPELINE_FAILED:          ['#4c1d95','#c4b5fd'],
      };
      const [bg, fg] = colors[verdict] || ['#1a2535','#cdd6e0'];
      verdictEl.style.background = bg;
      verdictEl.style.color      = fg;
      verdictEl.textContent      = verdict.replace(/_/g,' ');
    } else {
      $('rl-sess-verdict-row').style.display = 'none';
    }

    if (data.final_summary) {
      $('rl-sess-summary-row').style.display = 'block';
      $('rl-sess-summary').textContent = data.final_summary;
    } else {
      $('rl-sess-summary-row').style.display = 'none';
    }

    // ── New Zone-Aware Layout ────────────────────────────────────────────
    const hasResults = (data.best_clusters||[]).length + (data.rejected_clusters||[]).length
                        + (data.do_not_implement||[]).length + (data.next_research_action||'').length
                        + Object.keys(data.zone_summary||{}).length;
    if (hasResults) {
      $('rl-sess-clusters-row').style.display = 'block';

      // Coverage stats line
      const cs = data.coverage_stats || {};
      const covPairs = cs.pairs || 0;
      const covStrats = cs.strategies || 0;
      const covZones = cs.zones || 0;

      // Build zone cards
      const zSum = data.zone_summary || {};
      const zones = ['scalp','intra','swing'];
      const zColors = {scalp:{bg:'#831843',border:'#f472b6',text:'#fbcfe8'}, intra:{bg:'#0c4a6e',border:'#38bdf8',text:'#bae6fd'}, swing:{bg:'#4c1d95',border:'#a78bfa',text:'#ddd6fe'}};
      const zCards = zones.map(z => {
        const info = zSum[z];
        if (!info) return `<div style="background:#0f172a;border:1px solid #1e293b;border-radius:6px;padding:10px;opacity:.5"><div style="font-size:.7rem;color:#64748b;text-transform:uppercase">${z}</div><div style="font-size:.75rem;color:#475569">No data</div></div>`;
        const c = zColors[z] || {bg:'#1e293b',border:'#475569',text:'#94a3b8'};
        const bestStrat = info.best_strategy || '—';
        const bestEngine = info.best_engine || '';
        const bestComponent = info.best_component || '';
        const bestContext = info.best_context || '';
        const bestRec = info.best_recommendation || '';
        const bestSym = info.best_symbol || '';
        const bestTf = info.best_timeframe || '';
        const ret = info.best_return != null ? `+${info.best_return}%` : '';
        return `<div style="background:${c.bg};border:1px solid ${c.border};border-radius:6px;padding:10px;">
          <div style="font-size:.65rem;color:${c.text};text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">${z}</div>
          <div style="font-size:.85rem;font-weight:700;color:${c.text};margin-bottom:4px">${info.strong||0} strong · ${info.weak||0} weak</div>
          <div style="font-size:.72rem;color:${c.text};opacity:.9">Best: <strong>${bestEngine} ${bestStrat}</strong></div>
          <div style="font-size:.68rem;color:${c.text};opacity:.85">${bestComponent} · ${bestContext} · ${bestRec}</div>
          <div style="font-size:.7rem;color:${c.text};opacity:.8">${bestSym} ${bestTf} <span style="color:#6ee7b7">${ret}</span></div>
        </div>`;
      }).join('');

      // Per-pair recommendations table
      const ppRecs = data.per_pair_recommendations || [];
      const ppRows = ppRecs.slice(0,6).map(r => {
        const sym = r.symbol || '—';
        const strat = r.strategy_name || r.strategy || '—';
        const engine = r.engine || '—';
        const group = r.pair_group || r.market_group || '—';
        const context = r.structure_context || '—';
        const rec = r.recommendation || '—';
        const tf = r.timeframe || '—';
        const zone = r.zone || '—';
        const dir = r.direction || 'LONG';
        const ret = r.net_return != null ? `${r.net_return.toFixed(1)}%` : '—';
        const wr = r.win_rate != null ? `${r.win_rate.toFixed(0)}%` : '—';
        const zoneClr = zone==='scalp'?'#f472b6':zone==='intra'?'#38bdf8':zone==='swing'?'#a78bfa':'#64748b';
        return `<tr style="font-size:.75rem;border-top:1px solid #1e293b">
          <td style="padding:6px 8px;font-weight:600;color:#e2e8f0">${sym}</td>
          <td style="padding:6px 8px;color:#93c5fd">${engine}</td>
          <td style="padding:6px 8px;color:#94a3b8">${strat}</td>
          <td style="padding:6px 8px;color:#94a3b8">${group}</td>
          <td style="padding:6px 8px;color:#94a3b8">${context}</td>
          <td style="padding:6px 8px;color:${zoneClr}">${zone}</td>
          <td style="padding:6px 8px;color:#94a3b8">${tf}</td>
          <td style="padding:6px 8px;color:${dir==='LONG'?'#6ee7b7':dir==='SHORT'?'#f87171':'#fbbf24'}">${dir}</td>
          <td style="padding:6px 8px;color:#c4b5fd">${rec}</td>
          <td style="padding:6px 8px;color:#6ee7b7">${ret}</td>
          <td style="padding:6px 8px;color:#38bdf8">${wr}</td>
        </tr>`;
      }).join('');
      const ppTable = ppRows ? `<table style="width:100%;border-collapse:collapse"><thead><tr style="font-size:.68rem;color:#64748b;text-transform:uppercase"><th style="padding:4px 8px;text-align:left">Symbol</th><th style="padding:4px 8px;text-align:left">Engine</th><th style="padding:4px 8px;text-align:left">Strategy</th><th style="padding:4px 8px;text-align:left">Group</th><th style="padding:4px 8px;text-align:left">Structure</th><th style="padding:4px 8px;text-align:left">Zone</th><th style="padding:4px 8px;text-align:left">TF</th><th style="padding:4px 8px;text-align:left">Dir</th><th style="padding:4px 8px;text-align:left">Rec</th><th style="padding:4px 8px;text-align:left">Ret</th><th style="padding:4px 8px;text-align:left">WR</th></tr></thead><tbody>${ppRows}</tbody></table>` : '<div style="font-size:.75rem;color:#64748b">No per-pair recommendations yet.</div>';

      // Engine comparison (Engine A, Engine B, ADX Gate)
      const eng = data.engine_comparison || {};
      const ea = eng.engine_a || {};
      const eb = eng.engine_b || {};
      const adx = eng.adx_gate || {};
      const eaLive = ea.live_floor != null ? ea.live_floor.toFixed(1) : '—';
      const eaDisc = ea.discovery_proxy != null ? ea.discovery_proxy.toFixed(2) : '—';
      const ebLive = eb.live_min_score != null ? eb.live_min_score.toFixed(1) : '—';
      const ebDisc = eb.discovery_proxy != null ? eb.discovery_proxy.toFixed(2) : '—';
      const adxLive = adx.live_trend_min != null ? adx.live_trend_min.toFixed(0) : '—';
      const adxFail = adx.live_hard_fail != null ? adx.live_hard_fail.toFixed(0) : '—';
      const adxDisc = adx.discovery_proxy != null ? adx.discovery_proxy.toFixed(0) : '—';
      const engComp = `<div style="display:flex;flex-direction:column;gap:8px;font-size:.73rem;margin-top:8px">
        <div style="display:flex;gap:8px;align-items:center">
          <span style="color:#64748b;width:70px">Engine A:</span>
          <span style="background:#0f172a;border:1px solid #1e293b;border-radius:4px;padding:4px 8px">Live: <strong style="color:#fbbf24">${eaLive}</strong></span>
          <span style="background:#0f172a;border:1px solid #1e293b;border-radius:4px;padding:4px 8px">Discovery: <strong style="color:${eaDisc!='—'?'#6ee7b7':'#94a3b8'}">${eaDisc}</strong></span>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <span style="color:#64748b;width:70px">Engine B:</span>
          <span style="background:#0f172a;border:1px solid #1e293b;border-radius:4px;padding:4px 8px">Live: <strong style="color:#fbbf24">${ebLive}</strong></span>
          <span style="background:#0f172a;border:1px solid #1e293b;border-radius:4px;padding:4px 8px">Discovery: <strong style="color:${ebDisc!='—'?'#6ee7b7':'#94a3b8'}">${ebDisc}</strong></span>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <span style="color:#64748b;width:70px">ADX Gate:</span>
          <span style="background:#0f172a;border:1px solid #1e293b;border-radius:4px;padding:4px 8px">Trend Min: <strong style="color:#fbbf24">${adxLive}</strong></span>
          <span style="background:#0f172a;border:1px solid #1e293b;border-radius:4px;padding:4px 8px">Hard Fail: <strong style="color:#f87171">${adxFail}</strong></span>
          <span style="background:#0f172a;border:1px solid #1e293b;border-radius:4px;padding:4px 8px">Discovery: <strong style="color:${adxDisc!='—'?'#6ee7b7':'#94a3b8'}">${adxDisc}</strong></span>
        </div>
      </div>`;

      // Build full clusters HTML
      $('rl-sess-clusters-row').innerHTML = `
        <!-- Coverage Stats -->
        <div style="font-size:.75rem;color:#94a3b8;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid #1e293b">
          Coverage: <strong style="color:#e2e8f0">${covPairs} pairs</strong> · <strong style="color:#e2e8f0">${covStrats} strategies</strong> · <strong style="color:#e2e8f0">${covZones} zones</strong>
        </div>

        <!-- Zone Performance Cards -->
        <div style="font-size:.72rem;color:#64748b;font-weight:600;margin-bottom:8px;text-transform:uppercase;letter-spacing:.03em">Zone Performance</div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px">${zCards}</div>

        <!-- Per-Pair Recommendations -->
        <div style="font-size:.72rem;color:#64748b;font-weight:600;margin-bottom:8px;text-transform:uppercase;letter-spacing:.03em">Per-Pair Recommendations</div>
        <div style="background:#0f172a;border:1px solid #1e293b;border-radius:6px;padding:8px;margin-bottom:16px;overflow-x:auto">${ppTable}</div>

        <!-- Engine Comparison -->
        <div style="font-size:.72rem;color:#64748b;font-weight:600;margin-bottom:4px;text-transform:uppercase;letter-spacing:.03em">Engine Comparison</div>
        <div style="margin-bottom:12px">${engComp}</div>

        <!-- Do Not Implement -->
        ${(data.do_not_implement||[]).length ? `<div style="margin-bottom:12px">
          <div style="font-size:.72rem;color:#fb923c;font-weight:600;margin-bottom:4px">🚫 Do Not Implement</div>
          <div style="font-size:.75rem;color:#fed7aa;line-height:1.5">${(data.do_not_implement||[]).join(', ')}</div>
        </div>` : ''}

        <!-- Next Research Action -->
        <div style="margin-bottom:8px">
          <div style="font-size:.72rem;color:#38bdf8;font-weight:600;margin-bottom:4px">🔭 Next Research Action</div>
          <div style="font-size:.75rem;color:#bae6fd;line-height:1.5;margin-bottom:8px">${data.next_research_action || '—'}</div>
          <button id="rl-sess-run-next-btn" onclick="rlSessRunNext()" style="display:none; background:linear-gradient(135deg,#065f46,#059669); color:#fff; border:none; padding:7px 16px; border-radius:7px; cursor:pointer; font-size:.82rem; font-weight:600">▶ Run Next Action</button>
          <div id="rl-sess-run-next-summary" style="font-size:.7rem; color:#6ee7b7; margin-top:5px; opacity:.75;"></div>
        </div>
      `;

      // Re-bind Run Next Action button visibility
      const nrp = data.next_research_params || {};
      const runNextBtn = $('rl-sess-run-next-btn');
      const runNextSummary = $('rl-sess-run-next-summary');
      if (runNextBtn && nrp && nrp.symbols && nrp.symbols.length > 0 && data.status === 'COMPLETE') {
        window._rlSessNextParams = nrp;
        runNextBtn.style.display = 'inline-block';
        const syms = (nrp.symbols || []).join(', ');
        const tfs  = (nrp.timeframes || []).join(', ');
        const fams = (nrp.families || []).join(', ');
        runNextSummary.textContent = `${nrp.market_group || ''} · ${nrp.trading_style || ''} · ${nrp.research_depth || 'quick'} · ${syms} · ${tfs} · ${fams}`;
      }
    } else {
      $('rl-sess-clusters-row').style.display = 'none';
    }
  }

  window.rlSessRunNext = async function() {
    const nrp = window._rlSessNextParams;
    if (!nrp) return;

    // Pre-fill the cockpit selectors so the user can see what's being launched
    const mg = document.getElementById('rl-sess-market-group');
    const ts = document.getElementById('rl-sess-trading-style');
    const rd = document.getElementById('rl-sess-research-depth');
    if (mg && nrp.market_group) mg.value = nrp.market_group;
    if (ts && nrp.trading_style) ts.value = nrp.trading_style;
    if (rd && nrp.research_depth) rd.value = nrp.research_depth;

    const btn = document.getElementById('rl-sess-run-next-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Starting…';

    try {
      const payload = {
        market_group:   nrp.market_group   || 'crypto',
        trading_style:  nrp.trading_style  || 'intra',
        research_depth: nrp.research_depth || 'quick',
        symbols:    nrp.symbols    || undefined,
        timeframes: nrp.timeframes || undefined,
        families:   nrp.families   || undefined,
      };
      const res = await fetch('/api/research-lab/session-autopilot/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      _rlSessId = data.session_id;
      document.getElementById('rl-sess-card').style.display = 'block';
      document.getElementById('rl-sess-stop-btn').style.display = 'inline-block';
      _rlSessRenderStatus(data);
      _rlSessPoll(_rlSessId);
      window._rlSessNextParams = null;
    } catch (e) {
      alert('Failed to start follow-up session: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '▶ Run Next Action';
    }
  };

})();
