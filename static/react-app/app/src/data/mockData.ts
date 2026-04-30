import type {
  Signal, Position, MarketData, BacktestResult,
  GuardianStatus, ScreenerResult, PerformanceMetrics,
  LotteryNumber, LotteryAnalysis, NewsItem, SessionHours
} from '@/types';

export const PAIRS = [
  'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD',
  'USDCHF', 'NZDUSD', 'EURGBP', 'EURJPY', 'GBPJPY',
  'XAUUSD', 'XAGUSD', 'US30', 'NAS100', 'SPX500',
  'BTCUSD', 'ETHUSD', 'SOLUSD', 'BNBUSD',
];

export const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1'];

export const ENGINES = [
  { id: 'A', name: 'Quant Factor', description: '10-vote confluence model' },
  { id: 'B', name: 'Naked Structure', description: 'Pure market structure, FVG, liquidity' },
  { id: 'C', name: 'Consensus', description: 'A+B blended with conviction gating' },
  { id: 'D', name: 'Scalp VP+', description: 'Fabio Valentini orderflow methodology' },
];

function rand(min: number, max: number) {
  return Math.random() * (max - min) + min;
}

function randInt(min: number, max: number) {
  return Math.floor(rand(min, max));
}

export function generateSignals(count = 8): Signal[] {
  const signals: Signal[] = [];
  for (let i = 0; i < count; i++) {
    const pair = PAIRS[randInt(0, PAIRS.length)];
    const direction = Math.random() > 0.5 ? 'LONG' : 'SHORT';
    const entry = rand(1.05, 1.45);
    const sl = direction === 'LONG' ? entry - rand(0.005, 0.02) : entry + rand(0.005, 0.02);
    const tp = direction === 'LONG' ? entry + rand(0.01, 0.04) : entry - rand(0.01, 0.04);
    const confidence = randInt(60, 98);
    signals.push({
      id: `sig_${i}`,
      pair,
      direction,
      entry: Number(entry.toFixed(5)),
      sl: Number(sl.toFixed(5)),
      tp: Number(tp.toFixed(5)),
      tp2: Number((direction === 'LONG' ? tp + 0.01 : tp - 0.01).toFixed(5)),
      tp3: Number((direction === 'LONG' ? tp + 0.02 : tp - 0.02).toFixed(5)),
      confidence,
      engine: `Engine ${ENGINES[randInt(0, ENGINES.length)].id}`,
      timeframe: TIMEFRAMES[randInt(2, 6)],
      rRatio: Number(rand(1.5, 4.5).toFixed(2)),
      votes: randInt(6, 11),
      timestamp: new Date(Date.now() - randInt(0, 3600000)).toISOString(),
      status: Math.random() > 0.3 ? 'active' : 'closed',
      factors: ['Trend Alignment', 'Volume Spike', 'FVG Fill', 'Liquidity Sweep'].slice(0, randInt(2, 5)),
      notes: Math.random() > 0.5 ? 'Strong confluence at key level' : undefined,
    });
  }
  return signals;
}

export function generatePositions(): Position[] {
  const positions: Position[] = [];
  for (let i = 0; i < 5; i++) {
    const pair = PAIRS[randInt(0, PAIRS.length)];
    const direction = Math.random() > 0.5 ? 'LONG' : 'SHORT';
    const entry = rand(1.05, 1.45);
    const pnlPercent = rand(-2.5, 4.5);
    const size = rand(0.1, 2.5);
    positions.push({
      id: `pos_${i}`,
      pair,
      direction,
      entry: Number(entry.toFixed(5)),
      size: Number(size.toFixed(2)),
      sl: Number((direction === 'LONG' ? entry - 0.01 : entry + 0.01).toFixed(5)),
      tp: Number((direction === 'LONG' ? entry + 0.025 : entry - 0.025).toFixed(5)),
      pnl: Number((pnlPercent * size * 100).toFixed(2)),
      pnlPercent: Number(pnlPercent.toFixed(2)),
      openTime: new Date(Date.now() - randInt(300000, 86400000)).toISOString(),
      broker: Math.random() > 0.5 ? 'MT5' : 'Bybit',
      type: ['market', 'limit', 'stop'][randInt(0, 3)] as Position['type'],
      status: 'open',
    });
  }
  return positions;
}

export function generateMarketData(): MarketData[] {
  return PAIRS.map(pair => ({
    pair,
    price: pair.includes('JPY') ? rand(140, 165) : pair.includes('XAU') ? rand(2300, 2450) : pair.includes('XAG') ? rand(27, 32) : pair.includes('BTC') ? rand(64000, 72000) : pair.includes('ETH') ? rand(3200, 3800) : rand(1.05, 1.45),
    change24h: Number(rand(-1.5, 1.5).toFixed(2)),
    high24h: 0,
    low24h: 0,
    volume24h: Number(rand(10000, 500000).toFixed(0)),
    spread: Number(rand(0.1, 3.5).toFixed(1)),
  })).map(m => ({
    ...m,
    high24h: Number((m.price * (1 + rand(0.005, 0.02))).toFixed(5)),
    low24h: Number((m.price * (1 - rand(0.005, 0.02))).toFixed(5)),
  }));
}

export function generateBacktestResults(): BacktestResult[] {
  const names = ['Engine A Default', 'Engine B Aggressive', 'Engine C Conservative', 'Engine D Scalp', 'Hybrid v2.1'];
  return names.map((name, i) => {
    const totalReturn = rand(-5, 35);
    const trades = randInt(50, 300);
    return {
      id: `bt_${i}`,
      name,
      pair: PAIRS[randInt(0, PAIRS.length)],
      period: '2024-01-01 to 2024-12-31',
      totalReturn: Number(totalReturn.toFixed(2)),
      maxDrawdown: Number(rand(2, 15).toFixed(2)),
      sharpe: Number(rand(0.5, 2.5).toFixed(2)),
      winRate: Number(rand(45, 72).toFixed(1)),
      profitFactor: Number(rand(1.1, 2.8).toFixed(2)),
      trades,
      avgTrade: Number((totalReturn / trades).toFixed(3)),
      sqn: Number(rand(1.5, 4.5).toFixed(2)),
      equityCurve: Array.from({ length: 30 }, (_, j) => ({
        date: `2024-01-${String(j + 1).padStart(2, '0')}`,
        equity: 10000 + rand(-500, 2000) + j * rand(50, 150),
      })),
    };
  });
}

export function generateGuardianStatus(): GuardianStatus {
  return {
    overall: Math.random() > 0.8 ? 'warning' : 'healthy',
    bootChecks: [
      { name: 'API Connectivity', status: 'pass', message: 'All endpoints responsive' },
      { name: 'Broker Connection', status: 'pass', message: 'MT5 and Bybit connected' },
      { name: 'Database', status: 'pass', message: 'SQLite operational' },
      { name: 'Risk Limits', status: Math.random() > 0.9 ? 'fail' : 'pass', message: 'Within daily limits' },
      { name: 'Market Data Feed', status: 'pass', message: 'Real-time prices active' },
    ],
    circuitBreaker: Math.random() > 0.95,
    dailyLoss: Number(rand(-50, 120).toFixed(2)),
    dailyLossLimit: 500,
    openRisk: Number(rand(100, 400).toFixed(2)),
    maxOpenRisk: 1000,
    divergence: Math.random() > 0.9,
  };
}

export function generateScreenerResults(): ScreenerResult[] {
  return PAIRS.slice(0, 10).map(pair => ({
    pair,
    trend: ['bullish', 'bearish', 'neutral'][randInt(0, 3)] as ScreenerResult['trend'],
    strength: Number(rand(20, 95).toFixed(1)),
    rsi: Number(rand(25, 75).toFixed(1)),
    macd: Number(rand(-2, 2).toFixed(3)),
    volume: Number(rand(1000, 50000).toFixed(0)),
    atr: Number(rand(0.001, 0.05).toFixed(5)),
    sentiment: ['risk_on', 'risk_off', 'neutral'][randInt(0, 3)] as ScreenerResult['sentiment'],
  }));
}

export function generatePerformanceMetrics(): PerformanceMetrics {
  const totalTrades = randInt(200, 800);
  const winRate = rand(48, 68);
  return {
    total_pnl: Number(rand(2000, 15000).toFixed(2)),
    win_rate: Number(winRate.toFixed(1)),
    profit_factor: Number(rand(1.3, 2.4).toFixed(2)),
    avg_win: Number(rand(80, 250).toFixed(2)),
    avg_loss: Number(rand(-40, -120).toFixed(2)),
    max_drawdown: Number(rand(3, 18).toFixed(2)),
    sharpe: Number(rand(0.8, 2.2).toFixed(2)),
    total_trades: totalTrades,
    sqn: Number(rand(0.5, 2.5).toFixed(2)),
    sortino: Number(rand(0.8, 2.5).toFixed(2)),
    daily_pnl: Array.from({ length: 30 }, (_, i) => ({
      date: `2024-12-${String(i + 1).padStart(2, '0')}`,
      pnl: Number(rand(-300, 600).toFixed(2)),
    })),
    equity_curve: Array.from({ length: 30 }, (_, i) => ({
      date: `2024-12-${String(i + 1).padStart(2, '0')}`,
      equity: 10000 + Number(rand(-500, 2500).toFixed(2)),
    })),
    by_engine: {},
    by_asset_class: {},
  };
}

export function generateLotteryNumbers(): LotteryNumber[] {
  const games = ['Powerball', 'Mega Millions', 'EuroMillions', 'Lotto'];
  return Array.from({ length: 20 }, () => ({
    numbers: Array.from({ length: 5 }, () => randInt(1, 69)).sort((a, b) => a - b),
    date: `2024-12-${String(randInt(1, 31)).padStart(2, '0')}`,
    game: games[randInt(0, games.length)],
  }));
}

export function generateLotteryAnalysis(): LotteryAnalysis {
  const allNumbers = Array.from({ length: 69 }, (_, i) => i + 1);
  return {
    hotNumbers: allNumbers.slice(0, 10).map(n => ({ number: n, frequency: randInt(15, 45) })),
    coldNumbers: allNumbers.slice(10, 20).map(n => ({ number: n, frequency: randInt(1, 8) })),
    overdueNumbers: allNumbers.slice(20, 30).map(n => ({ number: n, lastSeen: randInt(30, 90) })),
    sumStats: { avg: randInt(120, 180), min: randInt(50, 80), max: randInt(280, 350) },
    oddEvenRatio: { odd: Number(rand(0.4, 0.6).toFixed(2)), even: Number(rand(0.4, 0.6).toFixed(2)) },
  };
}

export function generateNews(): NewsItem[] {
  const headlines = [
    { title: 'Fed Signals Potential Rate Cuts in Q2 2025', currency: 'USD', sentiment: 'positive' as const, impact: 'high' as const },
    { title: 'ECB Holds Rates Steady Amid Inflation Concerns', currency: 'EUR', sentiment: 'neutral' as const, impact: 'medium' as const },
    { title: 'BOJ Intervention Spurs Yen Volatility', currency: 'JPY', sentiment: 'negative' as const, impact: 'high' as const },
    { title: 'UK GDP Growth Exceeds Expectations', currency: 'GBP', sentiment: 'positive' as const, impact: 'medium' as const },
    { title: 'RBA Minutes Signal Hawkish Tilt', currency: 'AUD', sentiment: 'positive' as const, impact: 'medium' as const },
    { title: 'Gold Breaks $2400 on Safe Haven Demand', currency: 'XAU', sentiment: 'positive' as const, impact: 'high' as const },
    { title: 'Bitcoin ETF Inflows Reach Monthly High', currency: 'BTC', sentiment: 'positive' as const, impact: 'medium' as const },
    { title: 'Oil Prices Surge on Supply Concerns', currency: 'CAD', sentiment: 'negative' as const, impact: 'low' as const },
  ];
  return headlines.map((h, i) => ({
    id: `news_${i}`,
    ...h,
    source: ['Bloomberg', 'Reuters', 'ForexLive', 'CNBC'][randInt(0, 4)],
    timestamp: new Date(Date.now() - randInt(0, 7200000)).toISOString(),
  }));
}

export function generateSessionHours(): SessionHours[] {
  return [
    { session: 'Sydney', start: '21:00', end: '06:00', active: false },
    { session: 'Tokyo', start: '23:00', end: '08:00', active: false },
    { session: 'London', start: '07:00', end: '16:00', active: true },
    { session: 'New York', start: '12:00', end: '21:00', active: true, overlap: 'London/NY Overlap' },
  ];
}

export function getLivePrice(pair: string): number {
  const base = pair.includes('JPY') ? 150 : pair.includes('XAU') ? 2400 : pair.includes('XAG') ? 30 : pair.includes('BTC') ? 68000 : pair.includes('ETH') ? 3500 : pair.includes('US30') ? 42000 : pair.includes('NAS') ? 18000 : pair.includes('SPX') ? 5500 : 1.25;
  return base + rand(-0.02, 0.02);
}
