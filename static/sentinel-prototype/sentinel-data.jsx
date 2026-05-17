// Mock data + helpers for the Sentinel console.
// Exposes builders on window so other Babel scripts can use them.

const PAIRS = [
  // FX
  { sym: 'EURUSD',  name: 'Euro / US Dollar',          mkt: 'fx',     px: 1.08412, prov: 'MT5',     vol: 0.42 },
  { sym: 'GBPJPY',  name: 'British Pound / Yen',       mkt: 'fx',     px: 196.842, prov: 'MT5',     vol: 0.71 },
  { sym: 'USDJPY',  name: 'US Dollar / Yen',           mkt: 'fx',     px: 154.318, prov: 'MT5',     vol: 0.55 },
  { sym: 'AUDUSD',  name: 'Aussie / US Dollar',        mkt: 'fx',     px: 0.66124, prov: 'MT5',     vol: 0.38 },
  { sym: 'XAUUSD',  name: 'Gold Spot',                 mkt: 'commod', px: 3162.40, prov: 'MT5',     vol: 0.88 },
  { sym: 'XAGUSD',  name: 'Silver Spot',               mkt: 'commod', px: 38.214,  prov: 'MT5',     vol: 0.92 },
  // Crypto
  { sym: 'BTCUSDT', name: 'Bitcoin',                   mkt: 'crypto', px: 96412.5, prov: 'Binance', vol: 1.20 },
  { sym: 'ETHUSDT', name: 'Ether',                     mkt: 'crypto', px: 3284.2,  prov: 'Bybit',   vol: 1.42 },
  { sym: 'SOLUSDT', name: 'Solana',                    mkt: 'crypto', px: 184.62,  prov: 'Bybit',   vol: 1.78 },
  // Indices
  { sym: 'US500',   name: 'S&P 500 CFD',               mkt: 'idx',    px: 5824.10, prov: 'EODHD',   vol: 0.49 },
  { sym: 'NAS100',  name: 'Nasdaq 100 CFD',            mkt: 'idx',    px: 20418.6, prov: 'EODHD',   vol: 0.62 },
  { sym: 'GER40',   name: 'DAX 40 CFD',                mkt: 'idx',    px: 18642.0, prov: 'EODHD',   vol: 0.41 },
];

// Seeded RNG so the page is deterministic per regime
function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ t >>> 15, t | 1);
    t ^= t + Math.imul(t ^ t >>> 7, t | 61);
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

function fmtNum(n, opts = {}) {
  const { decimals = 2, sign = false, comma = true } = opts;
  if (!isFinite(n)) return '—';
  const abs = Math.abs(n);
  let s = abs.toFixed(decimals);
  if (comma) {
    const [int, dec] = s.split('.');
    s = int.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (dec ? '.' + dec : '');
  }
  if (n < 0) return '-' + s;
  return (sign ? '+' : '') + s;
}

function fmtPct(n, decimals = 2) { return fmtNum(n, { decimals, sign: true }) + '%'; }
function fmtPx(n, sym) {
  if (sym?.includes('JPY')) return fmtNum(n, { decimals: 3 });
  if (sym === 'XAUUSD' || sym === 'XAGUSD') return fmtNum(n, { decimals: 2 });
  if (sym?.startsWith('BTC')) return fmtNum(n, { decimals: 1 });
  if (sym === 'US500' || sym === 'NAS100' || sym === 'GER40') return fmtNum(n, { decimals: 1 });
  if (n > 100) return fmtNum(n, { decimals: 2 });
  return fmtNum(n, { decimals: 5, comma: false });
}

function timeAgo(mins) {
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const h = Math.floor(mins / 60);
  if (h < 24) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}

// Equity curve generator — regime affects shape
function buildEquityCurve(regime, rng, points = 120, base = 50000) {
  const out = [];
  let v = base;
  for (let i = 0; i < points; i++) {
    let drift, vol;
    if (regime === 'calm')      { drift = 60;  vol = 110;  }
    else if (regime === 'volatile') { drift = 50;  vol = 350; }
    else /* drawdown */         { drift = -45; vol = 280; }
    const noise = (rng() - 0.5) * vol * 2;
    v += drift + noise;
    out.push(v);
  }
  return out;
}

// Funnel
function buildFunnel(regime, rng) {
  if (regime === 'calm') {
    return [
      { lbl: 'SCANNED',   n: 142, max: 142 },
      { lbl: 'SHORTLIST', n: 38,  max: 142 },
      { lbl: 'GATED',     n: 14,  max: 142 },
      { lbl: 'ENGINE C',  n: 6,   max: 142 },
      { lbl: 'EXECUTED',  n: 2,   max: 142 },
    ];
  }
  if (regime === 'volatile') {
    return [
      { lbl: 'SCANNED',   n: 168, max: 168 },
      { lbl: 'SHORTLIST', n: 71,  max: 168 },
      { lbl: 'GATED',     n: 32,  max: 168 },
      { lbl: 'ENGINE C',  n: 14,  max: 168 },
      { lbl: 'EXECUTED',  n: 5,   max: 168 },
    ];
  }
  return [
    { lbl: 'SCANNED',   n: 124, max: 124 },
    { lbl: 'SHORTLIST', n: 22,  max: 124 },
    { lbl: 'GATED',     n: 6,   max: 124 },
    { lbl: 'ENGINE C',  n: 1,   max: 124 },
    { lbl: 'EXECUTED',  n: 0,   max: 124 },
  ];
}

// Engine status
function buildEngines(regime) {
  const base = [
    { id: 'A', title: 'Factor Scoring',     desc: 'EMA / ADX / regime', score: 78, gates: 4, scanned: 142 },
    { id: 'B', title: 'Naked Structure',    desc: 'Liquidity + FVG',    score: 64, gates: 5, scanned: 142 },
    { id: 'C', title: 'Execution',          desc: 'Trigger + RR',       score: 71, gates: 6, scanned: 14 },
    { id: 'D', title: 'Scalp / Microstr.',  desc: 'Orderflow context',  score: 52, gates: 4, scanned: 68 },
  ];
  if (regime === 'calm') {
    return base.map((e, i) => ({
      ...e, status: i < 3 ? 'active' : 'idle',
      passed: [38, 22, 6, 4][i], rejected: [104, 116, 8, 12][i],
      health: [96, 92, 88, 70][i],
    }));
  }
  if (regime === 'volatile') {
    return base.map((e, i) => ({
      ...e, status: i === 3 ? 'warn' : 'active',
      passed: [71, 49, 14, 18][i], rejected: [97, 19, 35, 22][i],
      health: [98, 90, 86, 62][i],
      score: [86, 78, 72, 48][i],
    }));
  }
  // drawdown
  return base.map((e, i) => ({
    ...e, status: i === 2 ? 'halt' : (i === 3 ? 'warn' : 'active'),
    passed: [22, 6, 0, 1][i], rejected: [102, 16, 6, 22][i],
    health: [88, 60, 32, 54][i],
    score: [62, 38, 18, 28][i],
  }));
}

// Open positions
function buildPositions(regime, rng) {
  const samples = [
    { sym: 'EURUSD', side: 'long',  size: 1.5, entry: 1.07921, sl: 1.07640, tp: 1.08720, opened: 64,  engine: 'A+B' },
    { sym: 'BTCUSDT',side: 'long',  size: 0.18, entry: 94820.0, sl: 92400.0, tp: 99800.0, opened: 142, engine: 'B' },
    { sym: 'XAUUSD', side: 'long',  size: 0.4, entry: 3148.20, sl: 3128.00, tp: 3198.40, opened: 28,  engine: 'A+B' },
    { sym: 'GBPJPY', side: 'short', size: 0.8, entry: 197.420, sl: 198.150, tp: 195.260, opened: 6,   engine: 'B' },
    { sym: 'NAS100', side: 'short', size: 0.5, entry: 20620.0, sl: 20780.0, tp: 20240.0, opened: 12,  engine: 'C' },
    { sym: 'SOLUSDT',side: 'long',  size: 12.0, entry: 178.40, sl: 172.00,  tp: 198.00,  opened: 88,  engine: 'D' },
  ];
  const reg = regime === 'drawdown' ? -1 : (regime === 'volatile' ? 1.6 : 1);
  return samples.map((p, i) => {
    const drift = (p.side === 'long' ? 1 : -1) * (rng() - 0.35) * 0.012 * reg;
    const px = p.entry * (1 + drift);
    const pip = (px - p.entry) * (p.sym.includes('JPY') ? 100 : 10000);
    const pnlPct = ((px - p.entry) / p.entry) * 100 * (p.side === 'long' ? 1 : -1);
    const pnl = (pnlPct / 100) * p.entry * p.size * (p.sym.startsWith('BTC') ? 1 : (p.sym.startsWith('SOL') ? 1 : 1000));
    return { ...p, px, pnl: pnl * (regime === 'drawdown' ? -1.4 : 1), pnlPct: pnlPct * (regime === 'drawdown' ? -1.4 : 1), pip };
  });
}

// Live signals (candidates)
function buildSignals(regime, rng) {
  const seeds = [
    { sym: 'USDJPY', side: 'short', engine: 'A+B', score: 82, rr: 2.4, tf: 'H4',  px: 154.32, sl: 154.96, tp: 152.78, gates: [1,1,1,1,1,0], note: 'D1 bearish PD-array conflict cleared' },
    { sym: 'AUDUSD', side: 'long',  engine: 'A',   score: 71, rr: 1.8, tf: 'H4',  px: 0.66124,sl: 0.65820,tp: 0.66680,gates: [1,1,1,1,0,0], note: 'EMA hysteresis confirmed, ADX 24' },
    { sym: 'ETHUSDT',side: 'long',  engine: 'B',   score: 67, rr: 2.1, tf: 'H1',  px: 3284.2, sl: 3232.0, tp: 3392.0, gates: [1,1,1,0,1,0], note: 'FVG mitigation, fast path' },
    { sym: 'XAGUSD', side: 'long',  engine: 'A+B', score: 88, rr: 3.1, tf: 'D1',  px: 38.214, sl: 37.420, tp: 40.460, gates: [1,1,1,1,1,1], note: 'Pristine — execution candidate' },
    { sym: 'GER40',  side: 'short', engine: 'C',   score: 58, rr: 1.6, tf: 'H1',  px: 18642.0,sl: 18742.0,tp: 18482.0,gates: [1,1,0,1,0,0], note: 'Trigger pending; RR basis OK' },
    { sym: 'BTCUSDT',side: 'short', engine: 'D',   score: 42, rr: 1.2, tf: 'M15', px: 96412.5,sl: 97120.0,tp: 95080.0,gates: [1,0,1,0,0,0], note: 'Microstructure: thin book bid side' },
    { sym: 'EURJPY', side: 'short', engine: 'B',   score: 64, rr: 2.0, tf: 'H4',  px: 167.42, sl: 168.18, tp: 165.80, gates: [1,1,1,1,0,0], note: 'Rejection wick/body 1.32' },
    { sym: 'US500',  side: 'long',  engine: 'A',   score: 49, rr: 1.5, tf: 'H4',  px: 5824.10,sl: 5786.20,tp: 5882.40,gates: [1,1,0,0,1,0], note: 'Borderline — intermarket weak' },
    { sym: 'SOLUSDT',side: 'long',  engine: 'B',   score: 73, rr: 2.8, tf: 'H1',  px: 184.62, sl: 178.20, tp: 200.40, gates: [1,1,1,1,1,0], note: 'Liquidity sweep + reclaim' },
  ];
  // shuffle scores by regime
  const scoreShift = regime === 'volatile' ? 8 : (regime === 'drawdown' ? -14 : 0);
  return seeds.map(s => ({ ...s, score: Math.max(10, Math.min(96, s.score + scoreShift + Math.round((rng()-0.5)*6))) }));
}

// Activity feed
function buildFeed(regime) {
  const t = (n) => n;
  if (regime === 'calm') return [
    { time: '14:42:08', kind: 'signal', msg: <span><strong>XAGUSD</strong> Engine A+B score 88, gates 6/6 — execution candidate</span> },
    { time: '14:38:51', kind: 'exec',   msg: <span>Filled <strong>EURUSD</strong> long @ 1.07921, size 1.5 lots</span> },
    { time: '14:31:14', kind: 'risk',   msg: <span>Daily risk budget 32% used, throttle nominal</span> },
    { time: '14:18:02', kind: 'signal', msg: <span><strong>USDJPY</strong> short candidate — D1 conflict window cleared</span> },
    { time: '14:02:58', kind: 'exec',   msg: <span>Closed <strong>AUDUSD</strong> long @ 0.66428, +18.2 pips, hold 4h12m</span> },
    { time: '13:48:39', kind: 'signal', msg: <span><strong>NAS100</strong> short reclaim, RR 1.6, gate 4/6</span> },
  ];
  if (regime === 'volatile') return [
    { time: '14:42:08', kind: 'exec',   msg: <span>Filled <strong>BTCUSDT</strong> long @ 94820, partial 0.18</span> },
    { time: '14:41:22', kind: 'signal', msg: <span><strong>SOLUSDT</strong> sweep + reclaim, score 73</span> },
    { time: '14:39:14', kind: 'risk',   msg: <span>Vol spike 3.4σ on NAS100 — sizing factor 0.6</span> },
    { time: '14:36:04', kind: 'signal', msg: <span><strong>GBPJPY</strong> rejection wick 1.42, score 76</span> },
    { time: '14:32:18', kind: 'exec',   msg: <span>Filled <strong>NAS100</strong> short @ 20620, size 0.5</span> },
    { time: '14:24:50', kind: 'signal', msg: <span><strong>XAUUSD</strong> liquidity grab D1 high, gate 5/6</span> },
  ];
  return [
    { time: '14:42:08', kind: 'halt',   msg: <span>Engine C halted — gates fail rate 78% above tolerance</span> },
    { time: '14:38:00', kind: 'risk',   msg: <span>Open DD -2.4R, throttle to 0.4× sizing</span> },
    { time: '14:24:11', kind: 'exec',   msg: <span>Stopped <strong>GBPJPY</strong> short, -22.4 pips</span> },
    { time: '14:14:02', kind: 'risk',   msg: <span>Correlation cluster: USD pairs 0.78, capping exposure</span> },
    { time: '13:58:18', kind: 'exec',   msg: <span>Stopped <strong>EURUSD</strong> long, -14.8 pips</span> },
    { time: '13:42:39', kind: 'signal', msg: <span><strong>XAUUSD</strong> long retained, awaiting confirmed trigger</span> },
  ];
}

// Microstructure regime strip
function buildRegimeStrip(regime) {
  if (regime === 'calm')  return [
    { l: 'VIX',        v: '14.20' },
    { l: 'DXY',        v: '104.18' },
    { l: 'YIELD 10Y',  v: '4.182%' },
    { l: 'SPX BREADTH',v: '+0.62' },
    { l: 'CRYPTO DOM', v: 'BTC 54%' },
  ];
  if (regime === 'volatile') return [
    { l: 'VIX',        v: '23.40' },
    { l: 'DXY',        v: '106.42' },
    { l: 'YIELD 10Y',  v: '4.382%' },
    { l: 'SPX BREADTH',v: '-0.18' },
    { l: 'CRYPTO DOM', v: 'BTC 58%' },
  ];
  return [
    { l: 'VIX',        v: '32.18' },
    { l: 'DXY',        v: '108.92' },
    { l: 'YIELD 10Y',  v: '4.612%' },
    { l: 'SPX BREADTH',v: '-0.74' },
    { l: 'CRYPTO DOM', v: 'BTC 61%' },
  ];
}

// Spark line builder
function buildSpark(rng, points = 24, trend = 0) {
  const out = [];
  let v = 50 + rng()*20;
  for (let i = 0; i < points; i++) {
    v += trend + (rng()-0.5)*8;
    out.push(v);
  }
  return out;
}

window.SentinelData = {
  PAIRS, mulberry32,
  fmtNum, fmtPct, fmtPx, timeAgo,
  buildEquityCurve, buildFunnel, buildEngines,
  buildPositions, buildSignals, buildFeed, buildRegimeStrip, buildSpark,
};
