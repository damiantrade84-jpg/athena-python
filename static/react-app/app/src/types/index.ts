export interface Signal {
  id: string;
  pair: string;
  symbol?: string;
  direction: 'LONG' | 'SHORT';
  entry: number;
  sl: number;
  tp: number;
  tp2?: number;
  tp3?: number;
  confidence: number;
  engine: string;
  source?: string;
  model?: string;
  tf?: string;
  timeframe: string;
  rRatio: number;
  votes?: number;
  timestamp: string;
  livePrice?: number;
  pnl?: number;
  status: 'active' | 'closed' | 'pending';
  factors?: string[];
  notes?: string;
  style?: string;
}

export interface Position {
  id: string;
  pair: string;
  symbol?: string;
  direction: 'LONG' | 'SHORT';
  entry: number;
  size: number;
  volume?: number;
  sl: number;
  tp: number;
  pnl: number;
  pnlPercent?: number;
  openTime: string;
  open_time?: string;
  broker: 'MT5' | 'Bybit';
  type: 'market' | 'limit' | 'stop';
  status: 'open' | 'closed';
  ticket?: string;
  swap?: number;
  closeTime?: string;
  close_price?: number;
  profit?: number;
  duration?: string;
}

export interface MarketData {
  pair: string;
  price: number;
  change24h: number;
  high24h: number;
  low24h: number;
  volume24h: number;
  spread: number;
  bid?: number;
  ask?: number;
}

export interface BacktestResult {
  id: string;
  name: string;
  pair: string;
  period: string;
  totalReturn: number;
  maxDrawdown: number;
  sharpe: number;
  winRate: number;
  profitFactor: number;
  trades: number;
  avgTrade: number;
  sqn: number;
  sortino?: number;
  equityCurve: { date: string; equity: number }[];
  dailyPnl?: { date: string; pnl: number }[];
}

export interface GuardianStatus {
  overall: 'healthy' | 'warning' | 'critical' | 'unknown';
  bootChecks: {
    name: string;
    status: 'pass' | 'fail' | 'pending';
    message: string;
  }[];
  circuitBreaker: boolean;
  dailyLoss: number;
  dailyLossLimit: number;
  openRisk: number;
  maxOpenRisk: number;
  divergence: boolean;
}

export interface ScreenerResult {
  pair: string;
  trend: 'bullish' | 'bearish' | 'neutral';
  strength: number;
  rsi: number;
  macd: number;
  volume: number;
  atr: number;
  sentiment: 'risk_on' | 'risk_off' | 'neutral';
}

export interface PerformanceMetrics {
  total_trades: number;
  win_rate: number;
  profit_factor: number;
  sqn: number;
  sharpe: number;
  sortino: number;
  total_pnl: number;
  max_drawdown: number;
  avg_win: number;
  avg_loss: number;
  by_engine: Record<string, { trades?: number; win_rate?: number; profit_factor?: number; sqn?: number; pnl?: number }>;
  by_asset_class: Record<string, { trades?: number; win_rate?: number; profit_factor?: number; pnl?: number }>;
  daily_pnl: { date: string; pnl: number }[];
  equity_curve: { date: string; equity: number }[];
}

export type PanelId =
  | 'dashboard'
  | 'signals'
  | 'pairBrowser'
  | 'scanConfig'
  | 'trades'
  | 'engineC'
  | 'scalpLab'
  | 'tvChart'
  | 'backtest'
  | 'screener'
  | 'lotteryLab'
  | 'researchLab'
  | 'performance'
  | 'markets'
  | 'guardian';

export interface LotteryNumber {
  numbers: number[];
  date: string;
  game: string;
}

export interface LotteryAnalysis {
  hotNumbers: { number: number; frequency: number }[];
  coldNumbers: { number: number; frequency: number }[];
  overdueNumbers: { number: number; lastSeen: number }[];
  sumStats: { avg: number; min: number; max: number };
  oddEvenRatio: { odd: number; even: number };
}

export interface NewsItem {
  id: string;
  title: string;
  source: string;
  timestamp: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  impact: 'high' | 'medium' | 'low';
  currency: string;
}

export interface SessionHours {
  session: string;
  start: string;
  end: string;
  active: boolean;
  overlap?: string;
}

export interface HealthStatus {
  status: string;
  paper_mode: boolean;
  real_orders_allowed: boolean;
  uptime_seconds: number;
  scan_count: number;
}

export interface MT5Status {
  connected: boolean;
  account_balance: number;
  account_equity: number;
  margin_level: number;
  server: string;
}

export interface BybitStatus {
  connected: boolean;
  balance_usdt: number;
}

export interface GuardianApiStatus {
  overall: 'healthy' | 'warning' | 'critical';
  guardian: { passed: boolean };
  shield: { circuit_breaker_open: boolean };
  divergence: { divergence_count: number };
  forensics: Record<string, unknown>;
}

export interface OpenTrade {
  ticket: string;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  volume: number;
  open_price: number;
  sl: number;
  tp: number;
  profit: number;
  open_time: string;
  duration: string;
  swap?: number;
}

export interface ScalpSignal {
  setup_type: string;
  grade: 'A' | 'B' | 'C' | 'D';
  score: number;
  sl: number;
  tp: number;
  tp2?: number;
  entry: number;
  confluenceScore: number;
  executable: boolean;
  candidate_fail_reasons: string[];
  rr_actual: number;
  size_multiplier: number;
  components?: Record<string, number>;
}

export interface LotteryDraw {
  date: string;
  numbers: number[];
  powerball?: number;
}

export interface FactorAttribution {
  factor: string;
  ic_score: number;
  win_rate: number;
  trades: number;
}
