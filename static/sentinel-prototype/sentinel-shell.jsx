// Shell: sidebar, topbar, brand. Exports SentinelShell to window.
const { useState, useEffect, useMemo, useRef } = React;

function Icon({ name, size = 16 }) {
  const s = size;
  const stroke = "currentColor";
  const sw = 1.5;
  const common = { width: s, height: s, viewBox: "0 0 24 24", fill: "none", stroke, strokeWidth: sw, strokeLinecap: "round", strokeLinejoin: "round" };
  switch (name) {
    case 'overview': return <svg {...common}><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>;
    case 'signals':  return <svg {...common}><path d="M3 12h3l3-7 4 14 3-7h5"/></svg>;
    case 'positions':return <svg {...common}><path d="M3 3v18h18"/><path d="M7 14l3-4 4 3 6-7"/></svg>;
    case 'research': return <svg {...common}><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>;
    case 'audit':    return <svg {...common}><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>;
    case 'autopilot':return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>;
    case 'settings': return <svg {...common}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>;
    case 'bell':     return <svg {...common}><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10 21a2 2 0 0 0 4 0"/></svg>;
    case 'pause':    return <svg {...common}><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>;
    case 'play':     return <svg {...common}><path d="M5 3l14 9-14 9z"/></svg>;
    case 'close':    return <svg {...common}><path d="M18 6L6 18M6 6l12 12"/></svg>;
    case 'arrow-up': return <svg {...common}><path d="M5 12l7-7 7 7"/><path d="M12 5v14"/></svg>;
    case 'arrow-dn': return <svg {...common}><path d="M19 12l-7 7-7-7"/><path d="M12 5v14"/></svg>;
    default: return null;
  }
}

function BrandMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{color: 'var(--accent)'}}>
      <path d="M12 2 L20 6 V12 C20 17 16 21 12 22 C8 21 4 17 4 12 V6 Z" />
      <path d="M12 8 V14" />
      <circle cx="12" cy="16.5" r="1" fill="currentColor"/>
    </svg>
  );
}

function Sidebar({ active, onNav, signalCount }) {
  const items = [
    { id: 'overview',  label: 'Overview',     icon: 'overview' },
    { id: 'signals',   label: 'Live Signals', icon: 'signals', badge: signalCount },
    { id: 'positions', label: 'Positions',    icon: 'positions', badge: 6 },
    { id: 'research',  label: 'Research Lab', icon: 'research' },
    { id: 'autopilot', label: 'Autopilot',    icon: 'autopilot' },
    { id: 'audit',     label: 'Audit',        icon: 'audit' },
    { id: 'settings',  label: 'Settings',     icon: 'settings' },
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"><BrandMark /></div>
        <div>
          <div className="brand-name">Sentinel</div>
          <div className="brand-sub">v4.1 · operator</div>
        </div>
      </div>

      <div className="nav-section">Workspace</div>
      {items.slice(0, 3).map(it => (
        <div key={it.id} className={"nav-item" + (active === it.id ? " active" : "")} onClick={() => onNav(it.id)}>
          <span className="nav-icon"><Icon name={it.icon}/></span>
          <span>{it.label}</span>
          {it.badge != null && <span className="nav-badge">{it.badge}</span>}
        </div>
      ))}

      <div className="nav-section">Strategy</div>
      {items.slice(3, 6).map(it => (
        <div key={it.id} className={"nav-item" + (active === it.id ? " active" : "")} onClick={() => onNav(it.id)}>
          <span className="nav-icon"><Icon name={it.icon}/></span>
          <span>{it.label}</span>
        </div>
      ))}

      <div className="nav-section">System</div>
      {items.slice(6).map(it => (
        <div key={it.id} className={"nav-item" + (active === it.id ? " active" : "")} onClick={() => onNav(it.id)}>
          <span className="nav-icon"><Icon name={it.icon}/></span>
          <span>{it.label}</span>
        </div>
      ))}

      <div className="sidebar-footer">
        <div className="sf-row"><span>MT5</span><span style={{color: 'var(--long)'}}>● connected</span></div>
        <div className="sf-row"><span>Binance WS</span><span style={{color: 'var(--long)'}}>● live</span></div>
        <div className="sf-row"><span>Bybit WS</span><span style={{color: 'var(--long)'}}>● live</span></div>
        <div className="sf-row"><span>EODHD</span><span style={{color: 'var(--text-2)'}}>● cached</span></div>
      </div>
    </aside>
  );
}

function Topbar({ active, regime, ticking, setTicking, equity, dayPnl, openRisk, clock }) {
  const labels = {
    overview:  { trail: 'workspace', leaf: 'Overview' },
    signals:   { trail: 'workspace', leaf: 'Live Signals' },
    positions: { trail: 'workspace', leaf: 'Positions' },
    research:  { trail: 'strategy',  leaf: 'Research Lab' },
    autopilot: { trail: 'strategy',  leaf: 'Autopilot' },
    audit:     { trail: 'strategy',  leaf: 'Audit' },
    settings:  { trail: 'system',    leaf: 'Settings' },
  };
  const meta = labels[active] || labels.overview;

  return (
    <header className="topbar">
      <div className="crumbs">
        <span>{meta.trail}</span>
        <span style={{color: 'var(--muted-2)'}}>/</span>
        <strong>{meta.leaf}</strong>
      </div>
      <div className="live-pulse" data-state={ticking ? 'live' : 'paused'}>
        <span className="dot"/>
        <span>{ticking ? 'LIVE · ' + regime.toUpperCase() : 'PAUSED · ' + regime.toUpperCase()}</span>
      </div>

      <div className="topbar-spacer"/>

      <div className="topbar-stat">
        <span className="ts-label">Equity</span>
        <span className="ts-val">${SentinelData.fmtNum(equity, {decimals: 0})}</span>
      </div>
      <div className="topbar-stat">
        <span className="ts-label">Day P&L</span>
        <span className={"ts-val " + (dayPnl >= 0 ? 'up' : 'down')}>
          {dayPnl >= 0 ? '+' : ''}${SentinelData.fmtNum(dayPnl, {decimals: 0})}
        </span>
      </div>
      <div className="topbar-stat">
        <span className="ts-label">Open Risk</span>
        <span className="ts-val">{openRisk.toFixed(1)}R</span>
      </div>
      <div className="topbar-stat">
        <span className="ts-label">UTC</span>
        <span className="ts-val">{clock}</span>
      </div>

      <button className="topbar-icon-btn" title={ticking ? "Pause stream" : "Resume stream"} onClick={() => setTicking(t => !t)}>
        <Icon name={ticking ? 'pause' : 'play'} size={14}/>
      </button>
      <button className="topbar-icon-btn" title="Notifications">
        <Icon name="bell" size={14}/>
      </button>
    </header>
  );
}

window.Icon = Icon;
window.Sidebar = Sidebar;
window.Topbar = Topbar;
