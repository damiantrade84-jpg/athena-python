// Live Signals + Open Positions screen.
const { useState: useStateS, useMemo: useMemoS } = React;

function ScoreCell({ score }) {
  return (
    <span>
      <span className="score-bar"><i style={{width: score + '%'}}/></span>
      <span style={{color: 'var(--text)'}}>{score}</span>
    </span>
  );
}

function GatesCell({ gates }) {
  return (
    <span className="gates">
      {gates.map((g, i) => <span key={i} className={"gate " + (g === 1 ? 'pass' : 'fail')}/>)}
    </span>
  );
}

function SignalsTable({ signals, selected, onSelect, filter, dirFilter, engineFilter }) {
  const filtered = signals.filter(s => {
    if (filter === 'execute' && s.score < 70) return false;
    if (filter === 'watch' && (s.score >= 70 || s.score < 50)) return false;
    if (filter === 'reject' && s.score >= 50) return false;
    if (dirFilter !== 'all' && s.side !== dirFilter) return false;
    if (engineFilter !== 'all' && !s.engine.includes(engineFilter)) return false;
    return true;
  });
  return (
    <div>
      <table className="tbl">
        <thead>
          <tr>
            <th style={{width: 90}}>Symbol</th>
            <th style={{width: 60}}>Dir</th>
            <th style={{width: 70}}>Engine</th>
            <th style={{width: 50}}>TF</th>
            <th style={{width: 130}}>Score</th>
            <th style={{width: 90}}>Gates</th>
            <th className="right" style={{width: 90}}>Price</th>
            <th className="right" style={{width: 90}}>Stop</th>
            <th className="right" style={{width: 90}}>Target</th>
            <th className="right" style={{width: 60}}>RR</th>
            <th>Note</th>
            <th className="right" style={{width: 80}}>Action</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(s => (
            <tr key={s.sym + s.engine} className={selected === s.sym ? 'selected' : ''} onClick={() => onSelect(s.sym)}>
              <td className="ticker">{s.sym}</td>
              <td><span className={"dir " + s.side}>{s.side === 'long' ? '▲ LONG' : '▼ SHORT'}</span></td>
              <td>{s.engine}</td>
              <td>{s.tf}</td>
              <td><ScoreCell score={s.score}/></td>
              <td><GatesCell gates={s.gates}/></td>
              <td className="right num">{SentinelData.fmtPx(s.px, s.sym)}</td>
              <td className="right num">{SentinelData.fmtPx(s.sl, s.sym)}</td>
              <td className="right num">{SentinelData.fmtPx(s.tp, s.sym)}</td>
              <td className="right num">{s.rr.toFixed(1)}</td>
              <td style={{color: 'var(--muted)'}}>{s.note}</td>
              <td className="right">
                {s.score >= 70
                  ? <button className="btn primary" style={{padding: '4px 10px'}} onClick={(e)=>{e.stopPropagation();}}>EXECUTE</button>
                  : <button className="btn ghost" style={{padding: '4px 10px'}} onClick={(e)=>{e.stopPropagation();}}>STAGE</button>}
              </td>
            </tr>
          ))}
          {filtered.length === 0 && (
            <tr><td colSpan={12} style={{textAlign: 'center', padding: 32, color: 'var(--muted)'}}>No signals match the current filters.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function PositionsTable({ positions, onSelect, selected, onClose }) {
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th style={{width: 90}}>Symbol</th>
          <th style={{width: 60}}>Dir</th>
          <th style={{width: 70}}>Engine</th>
          <th className="right" style={{width: 70}}>Size</th>
          <th className="right" style={{width: 90}}>Entry</th>
          <th className="right" style={{width: 90}}>Mark</th>
          <th className="right" style={{width: 80}}>Pips</th>
          <th className="right" style={{width: 100}}>P&L $</th>
          <th className="right" style={{width: 80}}>P&L %</th>
          <th className="right" style={{width: 90}}>Stop</th>
          <th className="right" style={{width: 90}}>Target</th>
          <th style={{width: 80}}>Held</th>
          <th className="right" style={{width: 90}}>Action</th>
        </tr>
      </thead>
      <tbody>
        {positions.map(p => (
          <tr key={p.sym} className={selected === p.sym ? 'selected' : ''} onClick={() => onSelect(p.sym)}>
            <td className="ticker">{p.sym}</td>
            <td><span className={"dir " + p.side}>{p.side === 'long' ? '▲ LONG' : '▼ SHORT'}</span></td>
            <td>{p.engine}</td>
            <td className="right num">{p.size.toFixed(2)}</td>
            <td className="right num">{SentinelData.fmtPx(p.entry, p.sym)}</td>
            <td className="right num">{SentinelData.fmtPx(p.px, p.sym)}</td>
            <td className={"right num " + (p.pip >= 0 ? 'up' : 'down')}>{p.pip >= 0 ? '+' : ''}{p.pip.toFixed(1)}</td>
            <td className={"right num " + (p.pnl >= 0 ? 'up' : 'down')}>{p.pnl >= 0 ? '+' : '-'}${SentinelData.fmtNum(Math.abs(p.pnl), {decimals: 0})}</td>
            <td className={"right num " + (p.pnlPct >= 0 ? 'up' : 'down')}>{p.pnlPct >= 0 ? '+' : ''}{p.pnlPct.toFixed(2)}%</td>
            <td className="right num">{SentinelData.fmtPx(p.sl, p.sym)}</td>
            <td className="right num">{SentinelData.fmtPx(p.tp, p.sym)}</td>
            <td style={{color: 'var(--muted)'}}>{SentinelData.timeAgo(p.opened)}</td>
            <td className="right">
              <button className="btn danger" style={{padding: '4px 10px'}} onClick={(e)=>{e.stopPropagation(); onClose(p.sym);}}>CLOSE</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SignalDrawer({ signal, onClose }) {
  if (!signal) return null;
  const gateLabels = ['Trend','Structure','Momentum','Volatility','RR','Confirm'];
  return (
    <div className={"drawer " + (signal ? 'open' : '')}>
      <div className="drawer-head">
        <div>
          <div style={{fontSize: 11, color: 'var(--muted)', fontFamily: 'JetBrains Mono', letterSpacing: '.12em', textTransform: 'uppercase'}}>Signal detail</div>
          <div style={{fontSize: 22, fontFamily: 'JetBrains Mono', marginTop: 4}}>
            {signal.sym} <span className={"dir " + signal.side} style={{fontSize: 11, marginLeft: 8, verticalAlign: 'middle'}}>{signal.side.toUpperCase()}</span>
          </div>
          <div className="muted" style={{fontSize: 12, fontFamily: 'JetBrains Mono', marginTop: 4}}>{signal.engine} · {signal.tf}</div>
        </div>
        <button className="topbar-icon-btn" onClick={onClose}><Icon name="close" size={14}/></button>
      </div>
      <div className="drawer-body">
        <div className="drawer-row"><span className="lbl">Score</span><span className="val">{signal.score}/100</span></div>
        <div className="drawer-row"><span className="lbl">RR</span><span className="val">{signal.rr.toFixed(2)}</span></div>
        <div className="drawer-row"><span className="lbl">Entry</span><span className="val">{SentinelData.fmtPx(signal.px, signal.sym)}</span></div>
        <div className="drawer-row"><span className="lbl">Stop</span><span className="val" style={{color: 'var(--short)'}}>{SentinelData.fmtPx(signal.sl, signal.sym)}</span></div>
        <div className="drawer-row"><span className="lbl">Target</span><span className="val" style={{color: 'var(--long)'}}>{SentinelData.fmtPx(signal.tp, signal.sym)}</span></div>

        <div style={{marginTop: 22, marginBottom: 8, fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--muted)', fontFamily: 'JetBrains Mono'}}>Gate breakdown</div>
        {gateLabels.map((g, i) => (
          <div key={g} className="drawer-row">
            <span className="lbl">{g}</span>
            <span className="val" style={{color: signal.gates[i] ? 'var(--long)' : 'var(--short)'}}>
              {signal.gates[i] ? '● PASS' : '● FAIL'}
            </span>
          </div>
        ))}

        <div style={{marginTop: 22, marginBottom: 8, fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--muted)', fontFamily: 'JetBrains Mono'}}>Reasoning</div>
        <div style={{fontSize: 12, color: 'var(--text-2)', lineHeight: 1.6}}>{signal.note}. Confirmed bar D1 alignment, intermarket factor +0.42σ. Risk basis {signal.rr.toFixed(2)}R from structural stop; expectancy {(signal.rr * 0.4 - 0.6).toFixed(2)}R at 50% win-rate prior.</div>
      </div>
      <div className="drawer-actions">
        <button className="btn primary" style={{flex: 1}}>EXECUTE NOW</button>
        <button className="btn">STAGE</button>
        <button className="btn ghost">REJECT</button>
      </div>
    </div>
  );
}

function SignalsScreen({ regime, ticking }) {
  const rng = useMemoS(() => SentinelData.mulberry32(regime === 'calm' ? 12 : (regime === 'volatile' ? 24 : 36)), [regime]);
  const signals = useMemoS(() => SentinelData.buildSignals(regime, rng), [regime, rng]);

  const [filter, setFilter] = useStateS('all');
  const [dirFilter, setDirFilter] = useStateS('all');
  const [engineFilter, setEngineFilter] = useStateS('all');
  const [selected, setSelected] = useStateS(null);

  const stats = useMemoS(() => {
    const exec = signals.filter(s => s.score >= 70).length;
    const watch = signals.filter(s => s.score >= 50 && s.score < 70).length;
    const reject = signals.filter(s => s.score < 50).length;
    return { total: signals.length, exec, watch, reject };
  }, [signals]);

  const selectedSig = signals.find(s => s.sym === selected);

  return (
    <div className="stack-md">
      <div className="page-head">
        <div>
          <h1 className="page-title">Live Signals</h1>
          <div className="page-sub">{stats.total} candidates · {stats.exec} executable · {ticking ? 'updating every 5s' : 'paused'}</div>
        </div>
        <div className="row-flex" style={{gap: 8}}>
          <button className="btn">EXPORT</button>
          <button className="btn primary">SCAN NOW</button>
        </div>
      </div>

      <div className="positions-summary">
        <div className="ps-cell"><span className="ps-lbl">Total</span><span className="ps-val">{stats.total}</span></div>
        <div className="ps-cell"><span className="ps-lbl">Executable</span><span className="ps-val" style={{color: 'var(--long)'}}>{stats.exec}</span></div>
        <div className="ps-cell"><span className="ps-lbl">Watch</span><span className="ps-val" style={{color: 'var(--warn)'}}>{stats.watch}</span></div>
        <div className="ps-cell"><span className="ps-lbl">Below threshold</span><span className="ps-val" style={{color: 'var(--muted)'}}>{stats.reject}</span></div>
      </div>

      <div className="card flush">
        <div className="filterbar">
          <span style={{color: 'var(--muted)'}}>FILTER</span>
          <div className="seg">
            <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>ALL</button>
            <button className={filter === 'execute' ? 'active' : ''} onClick={() => setFilter('execute')}>EXEC ≥70</button>
            <button className={filter === 'watch' ? 'active' : ''} onClick={() => setFilter('watch')}>WATCH 50-69</button>
            <button className={filter === 'reject' ? 'active' : ''} onClick={() => setFilter('reject')}>REJECT &lt;50</button>
          </div>
          <span className="sep">·</span>
          <div className="seg">
            <button className={dirFilter === 'all' ? 'active' : ''} onClick={() => setDirFilter('all')}>BOTH</button>
            <button className={dirFilter === 'long' ? 'active' : ''} onClick={() => setDirFilter('long')}>LONG</button>
            <button className={dirFilter === 'short' ? 'active' : ''} onClick={() => setDirFilter('short')}>SHORT</button>
          </div>
          <span className="sep">·</span>
          <div className="seg">
            <button className={engineFilter === 'all' ? 'active' : ''} onClick={() => setEngineFilter('all')}>ALL ENGINES</button>
            <button className={engineFilter === 'A' ? 'active' : ''} onClick={() => setEngineFilter('A')}>A</button>
            <button className={engineFilter === 'B' ? 'active' : ''} onClick={() => setEngineFilter('B')}>B</button>
            <button className={engineFilter === 'C' ? 'active' : ''} onClick={() => setEngineFilter('C')}>C</button>
            <button className={engineFilter === 'D' ? 'active' : ''} onClick={() => setEngineFilter('D')}>D</button>
          </div>
          <span className="count">click row for detail · <span className="kbd">↵</span> execute</span>
        </div>

        <SignalsTable signals={signals} selected={selected} onSelect={setSelected} filter={filter} dirFilter={dirFilter} engineFilter={engineFilter}/>
      </div>

      <SignalDrawer signal={selectedSig} onClose={() => setSelected(null)}/>
    </div>
  );
}

function PositionsScreen({ regime, ticking }) {
  const rng = useMemoS(() => SentinelData.mulberry32(regime === 'calm' ? 91 : (regime === 'volatile' ? 71 : 53)), [regime]);
  const [positions, setPositions] = useStateS(() => SentinelData.buildPositions(regime, rng));
  const [selected, setSelected] = useStateS(null);

  // Reset positions when regime changes
  React.useEffect(() => {
    setPositions(SentinelData.buildPositions(regime, SentinelData.mulberry32(regime === 'calm' ? 91 : (regime === 'volatile' ? 71 : 53))));
    setSelected(null);
  }, [regime]);

  const totals = useMemoS(() => {
    const total = positions.reduce((a, p) => a + p.pnl, 0);
    const winners = positions.filter(p => p.pnl > 0).length;
    const losers = positions.filter(p => p.pnl < 0).length;
    const exposure = positions.reduce((a, p) => a + Math.abs(p.size * p.entry), 0);
    return { total, winners, losers, exposure };
  }, [positions]);

  function closeOne(sym) {
    setPositions(positions.filter(p => p.sym !== sym));
  }

  return (
    <div className="stack-md">
      <div className="page-head">
        <div>
          <h1 className="page-title">Open Positions</h1>
          <div className="page-sub">{positions.length} open · {totals.winners} winners · {totals.losers} losers</div>
        </div>
        <div className="row-flex" style={{gap: 8}}>
          <button className="btn">FLATTEN ALL</button>
          <button className="btn primary">NEW MANUAL</button>
        </div>
      </div>

      <div className="positions-summary">
        <div className="ps-cell">
          <span className="ps-lbl">Net P&L (open)</span>
          <span className="ps-val" style={{color: totals.total >= 0 ? 'var(--long)' : 'var(--short)'}}>
            {totals.total >= 0 ? '+' : '-'}${SentinelData.fmtNum(Math.abs(totals.total), {decimals: 0})}
          </span>
        </div>
        <div className="ps-cell"><span className="ps-lbl">Open positions</span><span className="ps-val">{positions.length}</span></div>
        <div className="ps-cell"><span className="ps-lbl">Gross exposure</span><span className="ps-val">${SentinelData.fmtNum(totals.exposure, {decimals: 0})}</span></div>
        <div className="ps-cell"><span className="ps-lbl">Open R</span><span className="ps-val">{(regime === 'drawdown' ? -2.4 : (regime === 'volatile' ? 1.8 : 0.9)).toFixed(1)}R</span></div>
      </div>

      <div className="card flush">
        <div className="filterbar">
          <span style={{color: 'var(--muted)'}}>SORT BY</span>
          <div className="seg"><button className="active">P&L</button><button>HELD</button><button>SYMBOL</button></div>
          <span className="count">click row for detail</span>
        </div>
        <PositionsTable positions={positions} onSelect={setSelected} selected={selected} onClose={closeOne}/>
      </div>
    </div>
  );
}

window.SignalsScreen = SignalsScreen;
window.PositionsScreen = PositionsScreen;
