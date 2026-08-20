export type SolDecision = 'READY' | 'WATCH' | 'BLOCKED';
export type SolExecutionMode = 'paper' | 'demo' | 'live';
export type SolSetup = 'SWEEP_RECLAIM' | 'COMPRESSION_RELEASE' | 'SESSION_SWEEP' | 'NONE';

export interface SolComponent {
  name: string;
  score: number;
  maxScore: number;
  quality: number;
  evidence: Record<string, unknown>;
}

export interface SolGate {
  name: string;
  passed: boolean;
  reason?: string | null;
  [key: string]: unknown;
}

export interface SolSignal {
  signalId: string;
  contractVersion: string;
  pair: string;
  symbol: string;
  assetType: string;
  venue: 'mt5' | 'bybit';
  direction: 'LONG' | 'SHORT' | 'NONE';
  setup: SolSetup;
  sessionClock?: {
    localClock?: string;
    displayClock?: string;
    primaryWindow?: string | null;
    primaryKind?: string;
    quality?: number;
    inWindow?: boolean;
  };
  decision: SolDecision;
  decisionReason: string;
  score: number;
  maxScore: number;
  readyThreshold: number;
  watchThreshold: number;
  generatedAt: string;
  barClosedAt: string;
  setupEventAt: string | null;
  timeframes: Record<'context' | 'value' | 'setup' | 'trigger', string>;
  entry: number | null;
  stop: number | null;
  target: number | null;
  rr: number | null;
  atr: number | null;
  components: SolComponent[];
  gates: SolGate[];
  blockingReasons: string[];
  indicatorState: Record<string, unknown>;
  dataProvenance: Record<string, Record<string, unknown>>;
}

export interface SolScanState {
  scanId: string;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED';
  startedAt: string;
  completedAt?: string | null;
  totalPairs: number;
  processedPairs: number;
  readyCount: number;
  watchCount: number;
  blockedCount: number;
  errorCount: number;
  topBlockingReasons?: { reason: string; count: number }[];
  errors?: { pair: string; error: string }[];
  error?: string;
}

export interface SolModeCapability {
  enabled: boolean;
  brokerOrder: boolean;
  requiresDemoAccount?: boolean;
  requiresRealAccount?: boolean;
  requiresServerConfirmation?: boolean;
}

export interface SolCapabilities {
  defaultMode: SolExecutionMode;
  globalExecutorMode: string;
  researchStatus: string;
  followGlobalExecutorMode?: boolean;
  modes: Record<SolExecutionMode, SolModeCapability>;
}

export interface SolVenueAccount {
  venue: string;
  connected: boolean;
  environment?: string;
  demo?: boolean;
  testnet?: boolean;
  login?: string | number;
  server?: string;
  equity?: number;
  currency?: string;
  error?: string;
}

export interface SolAccounts {
  success: boolean;
  venues: Record<string, SolVenueAccount>;
  brokerCapabilities: SolCapabilities;
}

export interface SolHealth {
  success: boolean;
  engine: 'SOL';
  contractVersion: string;
  enabled: boolean;
  researchStatus: string;
  scanStatus: string;
  brokerCapabilities: SolCapabilities;
}

export interface SolPreview {
  success?: boolean;
  executable: boolean;
  error?: string | null;
  detail?: string;
  executableEntry?: number;
  liveRr?: number;
  liveStop?: number | null;
  liveTarget?: number | null;
  quote?: {
    venue: string;
    symbol: string;
    bid: number;
    ask: number;
    mid: number;
    spreadBps: number;
    timestamp: string;
    ageSec: number;
    source: string;
  };
  gates: SolGate[];
}

export interface SolExecutionRecord {
  execution_id: string;
  signal_id: string;
  idempotency_key: string;
  mode: SolExecutionMode;
  venue: string;
  status: 'PENDING' | 'SUCCESS' | 'REJECTED' | 'FAILED';
  requested_at: string;
  completed_at?: string | null;
  request: Record<string, unknown>;
  result: Record<string, unknown>;
}

export interface SolReplayResult {
  pair: string;
  causal: true;
  sameBarPolicy: 'STOP_FIRST';
  evidenceStatus: 'SAMPLE_SUFFICIENT' | 'INSUFFICIENT_SAMPLE';
  minimumTradesForEvidence: number;
  metrics: {
    tradeCount: number;
    wins: number;
    losses: number;
    winRatePct: number | null;
    averageR: number | null;
    totalR: number;
    maxDrawdownR: number;
  };
}

const COMPONENT_LABELS: Record<string, string> = {
  regime_orbit: 'Regime orbit',
  value_location: 'Value location',
  liquidity_event: 'Liquidity event',
  displacement: 'Displacement',
  participation: 'Participation',
  execution_geometry: 'Execution geometry',
};

export function solComponentLabel(name: string): string {
  return COMPONENT_LABELS[name] || name.replaceAll('_', ' ');
}

export function solDecisionClass(decision: SolDecision): string {
  if (decision === 'READY') return 'text-long border-long/35 bg-long/10';
  if (decision === 'WATCH') return 'text-warning border-warning/35 bg-warning/10';
  return 'text-short border-short/35 bg-short/10';
}

export function solSetupLabel(setup: SolSignal['setup']): string {
  if (setup === 'SWEEP_RECLAIM') return 'Sweep → reclaim';
  if (setup === 'SESSION_SWEEP') return 'Session sweep → reclaim';
  if (setup === 'COMPRESSION_RELEASE') return 'Compression → release';
  return 'No active setup';
}

export function solCanAttest(decision: SolDecision | undefined): boolean {
  return decision === 'READY' || decision === 'WATCH';
}

export function solPreferredMode(capabilities: SolCapabilities | null | undefined): SolExecutionMode {
  if (!capabilities) return 'paper';
  const order: SolExecutionMode[] = [capabilities.defaultMode, 'demo', 'paper', 'live'];
  return order.find((mode) => capabilities.modes[mode]?.enabled) ?? 'paper';
}

export function solScanProgress(scan: SolScanState | null): number {
  if (!scan || scan.totalPairs <= 0) return 0;
  if (!Number.isFinite(scan.processedPairs) || !Number.isFinite(scan.totalPairs)) return 0;
  return Math.max(0, Math.min(100, (scan.processedPairs / scan.totalPairs) * 100));
}

export function solPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const absolute = Math.abs(value);
  const digits = absolute >= 1000 ? 2 : absolute >= 10 ? 4 : absolute >= 1 ? 5 : 7;
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}
