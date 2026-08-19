export type GrokDecision = 'READY' | 'WATCH' | 'BLOCKED';
export type GrokExecutionMode = 'paper' | 'demo' | 'live';
export type GrokSetup = 'SILVER_BULLET' | 'JUDAS_RECLAIM' | 'CISD_CONTINUATION' | 'OTE_DELIVERY' | 'NONE';
export type GrokNarrative = 'RANGE_BUILD' | 'RAID' | 'DISPLACE' | 'RETRACE' | 'DELIVER';

export interface GrokComponent {
  name: string;
  score: number;
  maxScore: number;
  quality: number;
  evidence: Record<string, unknown>;
}

export interface GrokGate {
  name: string;
  passed: boolean;
  reason?: string | null;
  [key: string]: unknown;
}

export interface GrokSignal {
  signalId: string;
  contractVersion: string;
  pair: string;
  symbol: string;
  assetType: string;
  venue: 'mt5' | 'bybit';
  direction: 'LONG' | 'SHORT' | 'NONE';
  setup: GrokSetup;
  narrative: GrokNarrative;
  decision: GrokDecision;
  decisionReason: string;
  score: number;
  maxScore: number;
  readyThreshold: number;
  watchThreshold: number;
  generatedAt: string;
  barClosedAt: string;
  setupEventAt: string | null;
  timeframes: Record<'bias' | 'session' | 'setup' | 'trigger', string>;
  entry: number | null;
  stop: number | null;
  target: number | null;
  rr: number | null;
  atr: number | null;
  components: GrokComponent[];
  gates: GrokGate[];
  blockingReasons: string[];
  sessionClock: Record<string, unknown>;
  indicatorState: Record<string, unknown>;
  dataProvenance: Record<string, Record<string, unknown>>;
}

export interface GrokScanState {
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

export interface GrokModeCapability {
  enabled: boolean;
  brokerOrder: boolean;
  requiresDemoAccount?: boolean;
  requiresRealAccount?: boolean;
  requiresServerConfirmation?: boolean;
}

export interface GrokCapabilities {
  defaultMode: GrokExecutionMode;
  globalExecutorMode: string;
  researchStatus: string;
  modes: Record<GrokExecutionMode, GrokModeCapability>;
}

export interface GrokHealth {
  success: boolean;
  engine: 'GROK';
  contractVersion: string;
  enabled: boolean;
  researchStatus: string;
  scanStatus: string;
  brokerCapabilities: GrokCapabilities;
  universe?: {
    activePairs: number;
    byType: Record<string, number>;
    symbols?: string[];
  };
}

export interface GrokVenueAccount {
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

export interface GrokAccounts {
  success: boolean;
  universe: {
    activePairs: number;
    byType: Record<string, number>;
    symbols?: string[];
  };
  venues: Record<string, GrokVenueAccount>;
  brokerCapabilities: GrokCapabilities;
}

export interface GrokPreview {
  success?: boolean;
  executable: boolean;
  error?: string | null;
  detail?: string;
  executableEntry?: number;
  liveRr?: number;
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
  gates: GrokGate[];
}

export interface GrokExecutionRecord {
  execution_id: string;
  signal_id: string;
  idempotency_key: string;
  mode: GrokExecutionMode;
  venue: string;
  status: 'PENDING' | 'SUCCESS' | 'REJECTED' | 'FAILED';
  requested_at: string;
  completed_at?: string | null;
  request: Record<string, unknown>;
  result: Record<string, unknown>;
}

export interface GrokReplayResult {
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
  killzone_clock: 'Killzone clock',
  liquidity_raid: 'Liquidity raid',
  impulse_vector: 'Displacement',
  void_alignment: 'Delivery void',
  dealing_range: 'Dealing range',
  cisd_state: 'CISD state',
  geometry: 'Execution geometry',
};

const SETUP_LABELS: Record<GrokSetup, string> = {
  SILVER_BULLET: 'Silver Bullet',
  JUDAS_RECLAIM: 'Judas reclaim',
  CISD_CONTINUATION: 'CISD continuation',
  OTE_DELIVERY: 'OTE delivery',
  NONE: 'No active delivery',
};

const NARRATIVE_STEPS: GrokNarrative[] = ['RANGE_BUILD', 'RAID', 'DISPLACE', 'RETRACE', 'DELIVER'];

export function grokComponentLabel(name: string): string {
  return COMPONENT_LABELS[name] || name.replaceAll('_', ' ');
}

export function grokDecisionClass(decision: GrokDecision): string {
  if (decision === 'READY') return 'text-long border-long/35 bg-long/10';
  if (decision === 'WATCH') return 'text-warning border-warning/35 bg-warning/10';
  return 'text-short border-short/35 bg-short/10';
}

export function grokSetupLabel(setup: GrokSetup | string): string {
  return SETUP_LABELS[setup as GrokSetup] || String(setup).replaceAll('_', ' ');
}

export function grokNarrativeSteps(): GrokNarrative[] {
  return NARRATIVE_STEPS;
}

export function grokScanProgress(scan: GrokScanState | null): number {
  if (!scan || scan.totalPairs <= 0) return 0;
  if (!Number.isFinite(scan.processedPairs) || !Number.isFinite(scan.totalPairs)) return 0;
  return Math.max(0, Math.min(100, (scan.processedPairs / scan.totalPairs) * 100));
}

export function grokPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const absolute = Math.abs(value);
  const digits = absolute >= 1000 ? 2 : absolute >= 10 ? 4 : absolute >= 1 ? 5 : 7;
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function grokDisplayZoneLabel(clock: Record<string, unknown> | undefined): string {
  const tz = typeof clock?.displayTimezone === 'string' ? clock.displayTimezone : '';
  if (tz === 'Africa/Johannesburg' || tz === 'Africa/Harare') return 'SAST';
  const tail = tz.split('/').pop();
  return tail ? tail.replaceAll('_', ' ') : 'local';
}

export function grokClockLabel(clock: Record<string, unknown> | undefined): string {
  const windowName = typeof clock?.primaryWindow === 'string' ? clock.primaryWindow : 'off-session';
  const kind = typeof clock?.primaryKind === 'string' ? clock.primaryKind : 'off';
  const base = `${windowName.replaceAll('_', ' ')} · ${kind.replaceAll('_', ' ')}`;
  const displayClock = typeof clock?.displayClock === 'string' ? clock.displayClock : '';
  const localClock = typeof clock?.localClock === 'string' ? clock.localClock : '';
  if (!displayClock || !localClock) return base;
  return `${base} · ${displayClock} ${grokDisplayZoneLabel(clock)} / ${localClock} NY`;
}

export interface GrokWindowScheduleRow {
  name: string;
  kind: string;
  quality: number;
  nyStart: string;
  nyEnd: string;
  displayStart: string;
  displayEnd: string;
}

export function grokWindowSchedule(clock: Record<string, unknown> | undefined): GrokWindowScheduleRow[] {
  const rows = clock?.windowSchedule;
  if (!Array.isArray(rows)) return [];
  return rows.filter((row): row is GrokWindowScheduleRow => {
    if (!row || typeof row !== 'object') return false;
    const item = row as Record<string, unknown>;
    return (
      typeof item.name === 'string'
      && typeof item.kind === 'string'
      && typeof item.nyStart === 'string'
      && typeof item.nyEnd === 'string'
      && typeof item.displayStart === 'string'
      && typeof item.displayEnd === 'string'
    );
  });
}
