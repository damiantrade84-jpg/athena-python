// FABLE — Narrative Liquidity Engine: API types and pure view helpers.

export type FableDecision = 'EXECUTE' | 'STAGE' | 'OBSERVE' | 'VOID';
export type FableTier = 'LEGEND' | 'SAGA' | 'TALE' | 'SKETCH';
export type FableExecutionMode = 'paper' | 'demo' | 'live';
export type FableActName = 'draw' | 'raid' | 'shift' | 'return' | 'chorus';
export type FableReturnState = 'inside' | 'pending' | 'through' | null;

export interface FableAct {
  name: FableActName;
  quality: number | null;
  weight: number;
  state: string;
  evidence: Record<string, unknown>;
}

export interface FableGate {
  name: string;
  passed: boolean;
  reason?: string | null;
  [key: string]: unknown;
}

export interface FablePool {
  price: number;
  side: 'buyside' | 'sellside';
  source: string;
  strength: number;
  time: number;
  touches: number;
}

export interface FableAnnotations {
  pools: FablePool[];
  raid: { time: number; reclaimTime: number; price: number; pool: FablePool } | null;
  shift: { time: number; brokenLevel: number; brokenTime: number; legEnd: number; legEndTime: number } | null;
  array: { kind: 'fvg' | 'order_block'; low: number; high: number; mid: number; index: number; time: number } | null;
  dealingRange: { low: number; high: number } | null;
}

export interface FableSession {
  nyClock: string;
  displayClock: string;
  displayTimezone: string;
  window: string | null;
  quality: number;
  fringe: boolean;
  weekend: boolean;
}

export interface FableSignal {
  signalId: string;
  contractVersion: string;
  engine: 'FABLE';
  pair: string;
  symbol: string;
  assetType: string;
  venue: 'mt5' | 'bybit';
  direction: 'LONG' | 'SHORT' | 'NONE';
  decision: FableDecision;
  decisionReason: string;
  tier: FableTier;
  coherence: number;
  coherencePotential: number;
  maxCoherence: number;
  executeThreshold: number;
  stageThreshold: number;
  acts: FableAct[];
  gates: FableGate[];
  voidReasons: string[];
  narrative: string;
  returnState: FableReturnState;
  stageReason: string | null;
  session: FableSession;
  timeframes: Record<string, string>;
  generatedAt: string;
  barClosedAt: string | null;
  scanClose: number | null;
  atr: number | null;
  atrPct: number | null;
  entry: number | null;
  stop: number | null;
  target: number | null;
  target2: number | null;
  rr: number | null;
  rr2: number | null;
  stopAtr: number | null;
  targetSource: string | null;
  target2Source: string | null;
  annotations: FableAnnotations;
  dataFreshness: Record<string, { status: string; lastBarIso: string | null; ageBuckets: number | null; source: string | null }>;
  dataProvenance: Record<string, Record<string, unknown>>;
  chorusContext: Record<string, unknown>;
}

export interface FableScanState {
  scanId: string;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED';
  startedAt: string;
  completedAt?: string | null;
  totalPairs: number;
  processedPairs: number;
  executeCount: number;
  stageCount: number;
  observeCount: number;
  voidCount: number;
  errorCount: number;
  topReasons?: { reason: string; count: number }[];
  errors?: { pair: string; error: string }[];
  error?: string;
}

export interface FableModeCapability {
  enabled: boolean;
  brokerOrder: boolean;
  requiresDemoAccount?: boolean;
  requiresRealAccount?: boolean;
  requiresServerConfirmation?: boolean;
}

export interface FableCapabilities {
  defaultMode: FableExecutionMode;
  globalExecutorMode: string;
  researchStatus: string;
  followGlobalExecutorMode?: boolean;
  riskFraction?: number;
  modes: Record<FableExecutionMode, FableModeCapability>;
}

export interface FableVenueAccount {
  venue: string;
  connected: boolean;
  environment?: string;
  demo?: boolean;
  testnet?: boolean;
  login?: string | number;
  server?: string;
  balance?: number;
  equity?: number;
  currency?: string;
  error?: string;
}

export interface FableAccounts {
  success: boolean;
  venues: Record<string, FableVenueAccount>;
  brokerCapabilities: FableCapabilities;
}

export interface FableWindow {
  name: string;
  quality: number;
  startNy: string;
  endNy: string;
  startDisplay: string;
  endDisplay: string;
  active: boolean;
}

export interface FableHealth {
  success: boolean;
  engine: 'FABLE';
  contractVersion: string;
  enabled: boolean;
  researchStatus: string;
  scanStatus: string;
  lastScanCompletedAt?: string | null;
  session: FableSession;
  windows: FableWindow[];
  timeframes: Record<string, string>;
  thresholds: { execute: number; stage: number; tiers: Record<string, number> };
  brokerCapabilities: FableCapabilities;
}

export interface FablePreview {
  success?: boolean;
  executable: boolean;
  error?: string | null;
  detail?: string;
  gates: FableGate[];
  quote?: { venue: string; symbol: string; bid: number; ask: number; mid: number; spreadBps: number; timestamp: string; ageSec: number; source: string };
  executableEntry?: number;
  liveRr?: number;
  liveStop?: number | null;
  liveTarget?: number | null;
  liveTargetSource?: string | null;
}

export interface FableExecutionRecord {
  execution_id: string;
  signal_id: string;
  idempotency_key: string;
  mode: FableExecutionMode;
  venue: string;
  status: 'PENDING' | 'SUCCESS' | 'REJECTED' | 'FAILED';
  requested_at: string;
  completed_at?: string | null;
  request: { confirmLive?: boolean; signal?: Partial<FableSignal> };
  result: Record<string, unknown> & { success?: boolean; error?: string; detail?: string; ticket?: string | number; entryPrice?: number; mode?: string };
  idempotent?: boolean;
}

export interface FablePosition {
  venue: string;
  ticket: string | number;
  pair: string;
  symbol: string;
  direction: string;
  volume: number;
  entry: number;
  currentPrice?: number | null;
  sl?: number | null;
  tp?: number | null;
  profit?: number | null;
  riskAmount?: number | null;
  openTime?: number | null;
  signalId?: string | null;
  tier?: FableTier | null;
  coherence?: number | null;
}

export interface FablePositions {
  success: boolean;
  venues: Record<string, { connected: boolean; count?: number; error?: string }>;
  positions: FablePosition[];
  count: number;
}

export interface FableChronicle {
  success?: boolean;
  pair: string;
  assetType?: string;
  evidenceStatus: 'INSUFFICIENT_DATA' | 'INSUFFICIENT_SAMPLE' | 'SAMPLE_OK';
  note?: string;
  bars: number;
  barsEvaluated?: number;
  firstBarAt?: string;
  lastBarAt?: string;
  decisions: Record<string, number>;
  summary: {
    trades: number;
    wins?: number;
    losses?: number;
    winRate?: number | null;
    totalR?: number;
    expectancyR?: number | null;
    averageWinR?: number | null;
    averageLossR?: number | null;
    outcomes?: Record<string, number>;
    tiers?: Record<string, number>;
    minimumTradesForEvidence?: number;
  };
  chapters: {
    signalId: string;
    decisionAt: string;
    direction: string;
    tier: FableTier;
    coherence: number;
    entry: number;
    stop: number;
    target: number;
    plannedRr: number | null;
    outcome: string;
    rMultiple: number;
    barsHeld: number;
    exitPrice: number;
    raidPool?: string | null;
  }[];
}

export interface FableChart {
  success: boolean;
  signalId: string;
  pair: string;
  timeframe: string;
  candles: { time: number; open: number; high: number; low: number; close: number; volume: number | null }[];
  annotations: FableAnnotations;
  levels: { entry: number | null; stop: number | null; target: number | null; target2: number | null };
  direction: string;
}

export const FABLE_ACT_TITLES: Record<FableActName, { numeral: string; title: string; blurb: string }> = {
  draw: { numeral: 'I', title: 'Draw', blurb: 'Where the higher-timeframe range wants price to go' },
  raid: { numeral: 'II', title: 'Raid', blurb: 'The liquidity pool that was just swept' },
  shift: { numeral: 'III', title: 'Shift', blurb: 'Displacement that changed structure after the raid' },
  return: { numeral: 'IV', title: 'Return', blurb: 'Price coming back into the imbalance the shift left' },
  chorus: { numeral: 'V', title: 'Chorus', blurb: 'Quantitative and external voices that agree or dissent' },
};

export const FABLE_DECISION_ORDER: FableDecision[] = ['EXECUTE', 'STAGE', 'OBSERVE', 'VOID'];

export function fableDecisionClass(decision: FableDecision | string | null | undefined): string {
  switch (decision) {
    case 'EXECUTE':
      return 'fbl-decision--execute';
    case 'STAGE':
      return 'fbl-decision--stage';
    case 'OBSERVE':
      return 'fbl-decision--observe';
    default:
      return 'fbl-decision--void';
  }
}

export function fableTierClass(tier: FableTier | string | null | undefined): string {
  switch (tier) {
    case 'LEGEND':
      return 'fbl-tier--legend';
    case 'SAGA':
      return 'fbl-tier--saga';
    case 'TALE':
      return 'fbl-tier--tale';
    default:
      return 'fbl-tier--sketch';
  }
}

export function fableCanSeal(signal: Pick<FableSignal, 'decision'> | null | undefined): boolean {
  return signal?.decision === 'EXECUTE';
}

export function fableCanAttest(decision: FableDecision | string | null | undefined): boolean {
  return decision === 'EXECUTE' || decision === 'STAGE';
}

export function fablePreferredMode(capabilities: FableCapabilities | null | undefined): FableExecutionMode {
  if (!capabilities) return 'paper';
  const preferred = capabilities.defaultMode;
  if (preferred && capabilities.modes?.[preferred]?.enabled) return preferred;
  if (capabilities.modes?.demo?.enabled) return 'demo';
  return 'paper';
}

export function fablePrice(value: number | null | undefined, assetType?: string): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const magnitude = Math.abs(value);
  if (assetType === 'crypto' || magnitude >= 1000) return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (magnitude >= 100) return value.toFixed(3);
  if (magnitude >= 10) return value.toFixed(4);
  return value.toFixed(5);
}

export function fableScanProgress(scan: FableScanState | null | undefined): number {
  if (!scan || !scan.totalPairs) return 0;
  return Math.max(0, Math.min(100, Math.round((scan.processedPairs / scan.totalPairs) * 100)));
}

export function fableActQuality(signal: FableSignal | null | undefined, act: FableActName): number | null {
  const found = signal?.acts?.find((item) => item.name === act);
  return found?.quality ?? null;
}

export function fableStoryGlyphs(signal: FableSignal | null | undefined): { act: FableActName; quality: number | null; state: string }[] {
  const order: FableActName[] = ['draw', 'raid', 'shift', 'return', 'chorus'];
  return order.map((act) => {
    const found = signal?.acts?.find((item) => item.name === act);
    return { act, quality: found?.quality ?? null, state: found?.state ?? 'untold' };
  });
}

export function fableReturnLabel(state: FableReturnState | undefined): string {
  switch (state) {
    case 'inside':
      return 'Price inside the imbalance';
    case 'pending':
      return 'Awaiting the return';
    case 'through':
      return 'Return failed';
    default:
      return 'No return yet';
  }
}

export function fableMakeIdempotencyKey(signalId: string): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `fable-ui:${signalId}:${random}`;
}

export function fableShortTime(value?: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function fableRelativeAge(value?: string | null, now: number = Date.now()): string {
  if (!value) return '—';
  const parsed = new Date(value).getTime();
  if (Number.isNaN(parsed)) return '—';
  const seconds = Math.max(0, Math.round((now - parsed) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
