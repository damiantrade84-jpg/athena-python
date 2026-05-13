// Top-level Sentinel app — wires shell, screens, tweaks panel, and live state.
const { useState: useStateA, useEffect: useEffectA, useMemo: useMemoA } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "dark",
  "density": "comfortable",
  "accentHue": 220,
  "regime": "calm",
  "ticking": true
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [active, setActive] = useStateA('overview');
  const [clock, setClock] = useStateA(() => formatClock(new Date()));
  const [tickN, setTickN] = useStateA(0);

  // Apply theme + density + accent hue to root
  useEffectA(() => {
    const r = document.documentElement;
    r.setAttribute('data-theme', t.theme);
    r.setAttribute('data-density', t.density);
    r.style.setProperty('--accent', `oklch(${t.theme === 'dark' ? '74%' : '50%'} 0.14 ${t.accentHue})`);
    r.style.setProperty('--accent-soft', `oklch(${t.theme === 'dark' ? '74%' : '50%'} 0.14 ${t.accentHue} / 0.18)`);
    r.style.setProperty('--accent-line', `oklch(${t.theme === 'dark' ? '74%' : '50%'} 0.14 ${t.accentHue} / 0.45)`);
    r.style.setProperty('--info', `oklch(${t.theme === 'dark' ? '72%' : '50%'} 0.10 ${t.accentHue})`);
    r.style.setProperty('--grid-glow', `oklch(${t.theme === 'dark' ? '74%' : '50%'} 0.14 ${t.accentHue} / ${t.theme === 'dark' ? 0.08 : 0.04})`);
  }, [t.theme, t.density, t.accentHue]);

  // UTC clock
  useEffectA(() => {
    const id = setInterval(() => setClock(formatClock(new Date())), 1000);
    return () => clearInterval(id);
  }, []);

  // "Live" tick — drives a counter so derived metrics nudge
  useEffectA(() => {
    if (!t.ticking) return;
    const id = setInterval(() => setTickN(n => n + 1), 3000);
    return () => clearInterval(id);
  }, [t.ticking]);

  // Equity curve & day P&L derived from regime
  const equityData = useMemoA(() => {
    const rng = SentinelData.mulberry32(t.regime === 'calm' ? 7 : (t.regime === 'volatile' ? 19 : 41));
    return SentinelData.buildEquityCurve(t.regime, rng, 90, 50000);
  }, [t.regime]);
  const equity = equityData[equityData.length - 1] + (t.ticking ? Math.sin(tickN * 0.7) * 80 : 0);
  const dayPnl = (t.regime === 'calm' ? 1240 : (t.regime === 'volatile' ? 3120 : -1840))
                  + (t.ticking ? Math.sin(tickN * 0.5) * 60 : 0);
  const openRisk = t.regime === 'drawdown' ? 3.4 : (t.regime === 'volatile' ? 2.1 : 1.4);

  const signals = useMemoA(() => SentinelData.buildSignals(t.regime, SentinelData.mulberry32(99)), [t.regime]);
  const signalCount = signals.filter(s => s.score >= 50).length;

  let screen = null;
  if (active === 'overview') {
    screen = <OverviewScreen regime={t.regime} equityData={equityData} dayPnl={dayPnl} equity={equity} ticking={t.ticking}/>;
  } else if (active === 'signals') {
    screen = <SignalsScreen regime={t.regime} ticking={t.ticking}/>;
  } else if (active === 'positions') {
    screen = <PositionsScreen regime={t.regime} ticking={t.ticking}/>;
  } else {
    screen = <Placeholder name={active}/>;
  }

  return (
    <div className="app">
      <Sidebar active={active} onNav={setActive} signalCount={signalCount}/>
      <Topbar active={active} regime={t.regime}
        ticking={t.ticking}
        setTicking={(v) => setTweak('ticking', typeof v === 'function' ? v(t.ticking) : v)}
        equity={equity} dayPnl={dayPnl} openRisk={openRisk} clock={clock}/>
      <main className="main">{screen}</main>

      <TweaksPanel title="Tweaks">
        <TweakSection label="Appearance"/>
          <TweakRadio label="Theme" value={t.theme} options={['dark','light']} onChange={(v) => setTweak('theme', v)}/>
          <TweakRadio label="Density" value={t.density} options={['comfortable','compact']} onChange={(v) => setTweak('density', v)}/>
          <TweakColor label="Accent" value={t.accentHue}
            options={[220, 75, 300, 145]}
            onChange={(v) => setTweak('accentHue', v)}/>
        <TweakSection label="Mock state"/>
          <TweakRadio label="Regime" value={t.regime} options={['calm','volatile','drawdown']} onChange={(v) => setTweak('regime', v)}/>
          <TweakToggle label="Live ticking" value={t.ticking} onChange={(v) => setTweak('ticking', v)}/>
      </TweaksPanel>
    </div>
  );
}

function Placeholder({ name }) {
  const labels = {
    research:  { title: 'Research Lab',     sub: 'Backtest matrix, strategy explorer, parameter sweeps' },
    autopilot: { title: 'Autopilot',        sub: 'Session history, AI analyst review queue' },
    audit:     { title: 'Audit',            sub: 'Engine diagnostics, freshness, gate forensics' },
    settings:  { title: 'Settings',         sub: 'Engine knobs, broker config, paper-mode toggles' },
  };
  const m = labels[name] || { title: name, sub: '' };
  return (
    <div className="stack-md">
      <div className="page-head">
        <div>
          <h1 className="page-title">{m.title}</h1>
          <div className="page-sub">{m.sub}</div>
        </div>
      </div>
      <div className="card" style={{padding: 56, textAlign: 'center'}}>
        <div style={{fontFamily: 'JetBrains Mono', fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 12}}>Not in this prototype</div>
        <div style={{fontSize: 14, color: 'var(--text-2)', maxWidth: 520, margin: '0 auto', lineHeight: 1.6}}>
          This prototype focuses on Overview, Live Signals, and Positions. The {m.title} surface exists in the Sentinel/Athena codebase ({Object.keys({routes_audit:1,routes_backtest:1,routes_lottery:1}).length}+ route modules) and would map here next.
        </div>
      </div>
    </div>
  );
}

function formatClock(d) {
  const pad = n => String(n).padStart(2, '0');
  return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds());
}

ReactDOM.createRoot(document.getElementById('root')).render(<App/>);
