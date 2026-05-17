// Overview screen — engine status grid, KPIs, equity curve, funnel, regime strip, feed.
const { useMemo: useMemoO } = React;

function Sparkline({ data, color = "var(--accent)", w = 120, h = 36, fill = true }) {
  if (!data || !data.length) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const range = (max - min) || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return [x, y];
  });
  const d = "M " + pts.map(p => p.join(",")).join(" L ");
  const area = d + ` L ${w},${h} L 0,${h} Z`;
  const gradId = "sg-" + Math.abs(data[0] * 1000 | 0);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{width: '100%', height: h}}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35"/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient>
      </defs>
      {fill && <path d={area} fill={`url(#${gradId})`}/>}
      <path d={d} fill="none" stroke={color} strokeWidth="1.4"/>
    </svg>
  );
}

function EquityChart({ data, range }) {
  const w = 800, h = 220, padL = 44, padR = 12, padT = 10, padB = 26;
  const cw = w - padL - padR, ch = h - padT - padB;
  const min = Math.min(...data), max = Math.max(...data);
  const r = (max - min) || 1;
  const pts = data.map((v, i) => {
    const x = padL + (i / (data.length - 1)) * cw;
    const y = padT + (1 - (v - min) / r) * ch;
    return [x, y];
  });
  const d = "M " + pts.map(p => p.join(",")).join(" L ");
  const area = d + ` L ${padL + cw},${padT + ch} L ${padL},${padT + ch} Z`;
  const last = pts[pts.length - 1];
  const start = data[0], end = data[data.length - 1];
  const pct = ((end - start) / start) * 100;
  const color = pct >= 0 ? "var(--accent)" : "var(--short)";

  // Y-axis ticks
  const ticks = 4;
  const yTicks = [];
  for (let i = 0; i <= ticks; i++) {
    const v = min + (r * i) / ticks;
    const y = padT + (1 - i / ticks) * ch;
    yTicks.push({ v, y });
  }

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="equity-svg" preserveAspectRatio="none">
      <defs>
        <linearGradient id="equity-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.32"/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient>
      </defs>
      <g className="equity-grid">
        {yTicks.map((t, i) => <line key={i} x1={padL} x2={w-padR} y1={t.y} y2={t.y}/>)}
      </g>
      <g className="equity-axis">
        {yTicks.map((t, i) => (
          <text key={i} x={padL - 8} y={t.y + 3} textAnchor="end">${(t.v/1000).toFixed(1)}k</text>
        ))}
        <text x={padL} y={h - 8}>{range === '1D' ? '00:00' : 'd-30'}</text>
        <text x={(padL + w - padR)/2} y={h - 8} textAnchor="middle">{range === '1D' ? '12:00' : 'd-15'}</text>
        <text x={w - padR} y={h - 8} textAnchor="end">{range === '1D' ? 'now' : 'today'}</text>
      </g>
      <path d={area} fill="url(#equity-grad)"/>
      <path d={d} fill="none" stroke={color} strokeWidth="1.6"/>
      <circle cx={last[0]} cy={last[1]} r="3.5" className="equity-marker"/>
    </svg>
  );
}

function EngineCell({ e }) {
  const stateLabel = { active: 'ACTIVE', warn: 'DEGRADED', halt: 'HALTED', idle: 'IDLE' }[e.status];
  return (
    <div className="engine-cell">
      <div className="e-head">
        <span className="e-name">Engine {e.id}</span>
        <span className="pill" data-state={e.status}><span className="pdot"/>{stateLabel}</span>
      </div>
      <div>
        <div className="e-title">{e.title}</div>
        <div className="muted" style={{fontSize: 11, marginTop: 2}}>{e.desc}</div>
      </div>
      <div className="e-stats">
        <span className="lbl">scanned</span><span className="val">{e.scanned}</span>
        <span className="lbl">passed</span><span className="val" style={{color: e.passed > 0 ? 'var(--long)' : 'var(--muted)'}}>{e.passed}</span>
        <span className="lbl">score</span><span className="val">{e.score}</span>
        <span className="lbl">health</span><span className="val">{e.health}%</span>
      </div>
      <div className="healthbar"><i style={{width: e.health + '%'}}/></div>
    </div>
  );
}

function FunnelView({ rows }) {
  const max = rows[0]?.max || 1;
  return (
    <div className="funnel">
      {rows.map((r, i) => {
        const pct = (r.n / max) * 100;
        const dropPct = i > 0 ? ((rows[i-1].n - r.n) / Math.max(rows[i-1].n, 1)) * 100 : 0;
        return (
          <div key={r.lbl} className={"funnel-row" + (r.n === 0 ? " dim" : "")}>
            <span className="fn-lbl">{r.lbl}</span>
            <div className="fn-bar" style={{width: pct + '%', minWidth: r.n > 0 ? 12 : 0}}/>
            <span className="fn-num">{r.n}</span>
            <span className="fn-pct">{i === 0 ? '—' : '-' + dropPct.toFixed(0) + '%'}</span>
          </div>
        );
      })}
    </div>
  );
}

function RegimeStrip({ items }) {
  return (
    <div className="regime-strip">
      {items.map(r => (
        <div key={r.l} className="regime-cell">
          <span className="rl">{r.l}</span>
          <span className="rv">{r.v}</span>
        </div>
      ))}
    </div>
  );
}

function ActivityFeed({ items }) {
  return (
    <div className="feed">
      {items.map((f, i) => (
        <div key={i} className="feed-row">
          <span className="feed-time">{f.time}</span>
          <span className="feed-msg">{f.msg}</span>
          <span className="feed-tag" data-kind={f.kind}>{f.kind}</span>
        </div>
      ))}
    </div>
  );
}

function OverviewScreen({ regime, equityData, dayPnl, equity, ticking }) {
  const rng = SentinelData.mulberry32(regime === 'calm' ? 7 : (regime === 'volatile' ? 19 : 41));
  const engines = useMemoO(() => SentinelData.buildEngines(regime), [regime]);
  const funnel = useMemoO(() => SentinelData.buildFunnel(regime, SentinelData.mulberry32(11)), [regime]);
  const feed = useMemoO(() => SentinelData.buildFeed(regime), [regime]);
  const strip = useMemoO(() => SentinelData.buildRegimeStrip(regime), [regime]);

  const winRate = regime === 'calm' ? 62 : (regime === 'volatile' ? 58 : 41);
  const expR = regime === 'calm' ? 0.42 : (regime === 'volatile' ? 0.31 : -0.18);
  const trades7 = regime === 'calm' ? 38 : (regime === 'volatile' ? 64 : 22);
  const sparkA = useMemoO(() => SentinelData.buildSpark(SentinelData.mulberry32(3), 24, regime === 'drawdown' ? -0.4 : 0.3), [regime]);
  const sparkB = useMemoO(() => SentinelData.buildSpark(SentinelData.mulberry32(5), 24, 0), [regime]);
  const sparkC = useMemoO(() => SentinelData.buildSpark(SentinelData.mulberry32(8), 24, regime === 'drawdown' ? -0.6 : 0.1), [regime]);

  const pctChange = ((equityData[equityData.length-1] - equityData[0]) / equityData[0]) * 100;

  return (
    <div className="stack-md">
      <div className="page-head">
        <div>
          <h1 className="page-title">Operator Overview</h1>
          <div className="page-sub">{ticking ? 'streaming · last update 0s ago' : 'paused · resume to refresh'}</div>
        </div>
        <div className="seg">
          <button className="active">DARK</button>
          <button>LIGHT</button>
        </div>
      </div>

      <RegimeStrip items={strip}/>

      {/* KPI row */}
      <div className="grid-overview">
        <div className="card col-3">
          <div className="kpi">
            <span className="kpi-label">Equity</span>
            <span className="kpi-value">${SentinelData.fmtNum(equity, {decimals: 0})}</span>
            <span className={"kpi-delta " + (pctChange >= 0 ? 'up' : 'down')}>
              <Icon name={pctChange >= 0 ? 'arrow-up' : 'arrow-dn'} size={12}/>
              {pctChange >= 0 ? '+' : ''}{pctChange.toFixed(2)}% · 30d
            </span>
            <div className="kpi-spark"><Sparkline data={equityData.slice(-30)} color={pctChange >= 0 ? 'var(--accent)' : 'var(--short)'} h={36}/></div>
          </div>
        </div>
        <div className="card col-3">
          <div className="kpi">
            <span className="kpi-label">Day P&L</span>
            <span className="kpi-value" style={{color: dayPnl >= 0 ? 'var(--long)' : 'var(--short)'}}>
              {dayPnl >= 0 ? '+' : '-'}${SentinelData.fmtNum(Math.abs(dayPnl), {decimals: 0})}
            </span>
            <span className="muted" style={{fontSize: 11, fontFamily: 'JetBrains Mono'}}>
              {trades7} trades · 7d
            </span>
            <div className="kpi-spark"><Sparkline data={sparkA} color="var(--long)" h={36}/></div>
          </div>
        </div>
        <div className="card col-3">
          <div className="kpi">
            <span className="kpi-label">Win Rate · 30d</span>
            <span className="kpi-value">{winRate}<span style={{fontSize: 16, color: 'var(--muted)'}}>%</span></span>
            <span className="muted" style={{fontSize: 11, fontFamily: 'JetBrains Mono'}}>
              expectancy {expR >= 0 ? '+' : ''}{expR.toFixed(2)}R
            </span>
            <div className="kpi-spark"><Sparkline data={sparkB} color="var(--info)" h={36}/></div>
          </div>
        </div>
        <div className="card col-3">
          <div className="kpi">
            <span className="kpi-label">Open Risk</span>
            <span className="kpi-value">{(regime === 'drawdown' ? 3.4 : (regime === 'volatile' ? 2.1 : 1.4)).toFixed(1)}<span style={{fontSize: 16, color: 'var(--muted)'}}>R</span></span>
            <span className="muted" style={{fontSize: 11, fontFamily: 'JetBrains Mono'}}>
              budget cap 4.0R · 6 open
            </span>
            <div className="kpi-spark"><Sparkline data={sparkC} color="var(--warn)" h={36}/></div>
          </div>
        </div>
      </div>

      {/* Engines */}
      <div>
        <div className="row-flex between" style={{marginBottom: 10}}>
          <div className="card-title"><span className="dot"/>Engine status</div>
          <span className="muted" style={{fontSize: 11, fontFamily: 'JetBrains Mono'}}>last scan 14s ago · interval 5m</span>
        </div>
        <div className="engine-grid">
          {engines.map(e => <EngineCell key={e.id} e={e}/>)}
        </div>
      </div>

      {/* Equity + Funnel */}
      <div className="grid-overview">
        <div className="card col-8">
          <div className="card-head">
            <div className="card-title"><span className="dot"/>Equity curve</div>
            <div className="seg">
              <button>1D</button>
              <button className="active">30D</button>
              <button>90D</button>
              <button>YTD</button>
            </div>
          </div>
          <div className="equity-wrap">
            <EquityChart data={equityData} range="30D"/>
          </div>
          <div className="row-flex" style={{marginTop: 12, gap: 24, fontFamily: 'JetBrains Mono', fontSize: 11}}>
            <span className="muted">PEAK <span style={{color: 'var(--text)'}}>${SentinelData.fmtNum(Math.max(...equityData), {decimals: 0})}</span></span>
            <span className="muted">TROUGH <span style={{color: 'var(--text)'}}>${SentinelData.fmtNum(Math.min(...equityData), {decimals: 0})}</span></span>
            <span className="muted">MAX DD <span style={{color: regime === 'drawdown' ? 'var(--short)' : 'var(--text)'}}>{regime === 'drawdown' ? '-8.4%' : '-2.1%'}</span></span>
            <span className="muted">SHARPE <span style={{color: 'var(--text)'}}>{regime === 'drawdown' ? '0.82' : (regime === 'volatile' ? '1.74' : '2.18')}</span></span>
          </div>
        </div>

        <div className="card col-4">
          <div className="card-head">
            <div className="card-title"><span className="dot"/>Signal funnel · today</div>
            <span className="muted" style={{fontSize: 10, fontFamily: 'JetBrains Mono'}}>5m cycle</span>
          </div>
          <FunnelView rows={funnel}/>
          <div style={{marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--line-soft)', fontSize: 11, color: 'var(--muted)', fontFamily: 'JetBrains Mono', lineHeight: 1.7}}>
            <div className="row-flex between"><span>Avg gate score</span><span style={{color: 'var(--text)'}}>{regime === 'drawdown' ? '38' : (regime === 'volatile' ? '64' : '71')}</span></div>
            <div className="row-flex between"><span>Top reject reason</span><span style={{color: 'var(--text)'}}>D1 conflict</span></div>
            <div className="row-flex between"><span>Throttle factor</span><span style={{color: 'var(--text)'}}>{regime === 'drawdown' ? '0.4×' : '1.0×'}</span></div>
          </div>
        </div>
      </div>

      {/* Activity feed */}
      <div className="card">
        <div className="card-head">
          <div className="card-title"><span className="dot"/>Activity feed</div>
          <div className="row-flex" style={{gap: 8}}>
            <div className="seg">
              <button className="active">ALL</button>
              <button>SIGNALS</button>
              <button>EXEC</button>
              <button>RISK</button>
            </div>
            <span className="card-action">view log →</span>
          </div>
        </div>
        <ActivityFeed items={feed}/>
      </div>
    </div>
  );
}

window.OverviewScreen = OverviewScreen;
window.Sparkline = Sparkline;
