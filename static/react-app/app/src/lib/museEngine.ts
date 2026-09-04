// MUSE — Meridian Undertow Synthesis Engine: API types and pure view helpers.

export type MuseDecision = 'PRIME' | 'STAGE' | 'DORMANT' | 'BLOCKED';
export type MuseExecutionMode = 'paper' | 'demo' | 'live';
export type MuseSetup = 'TIDAL_SLING' | 'UNDERTOW_RECLAIM' | 'ARC_CONTINUATION' | 'HAVEN_TAP' | 'NONE';
export type MusePhase = 'DRIFT' | 'PULL' | 'SURGE' | 'SETTLE' | 'RELEASE';
export type MusePrismName = 'echo' | 'surge' | 'haven' | 'compass';

export interface MusePrism {
  name: MusePrismName;
  quality: number;
  evidence: Record<string, unknown>;
}

export interface MuseGate {
  name: string;
  passed: boolean;
  reason?: string | null;
  [key: string]: unknown;
}

export interface MuseTide {
  window: string;
  kind: 'drift' | 'tide' | 'surge' | 'slack' | string;
  quality: number;
  fringe?: boolean;
  nyTime?: string;
  weekday?: number;
}

export interface MuseSignal {
  signalId: string;
  contractVersion: string;
  engine: 'MUSE';
  pair: string;
  symbol: string;
  assetType: string;
  venue: 'mt5' | 'bybit';
  direction: 'LONG' | 'SHORT' | 'NONE';
  setup: MuseSetup;
  phase: MusePhase;
  decision: MuseDecision;
  decisionReason: string;
  score: number;
  maxScore: number;
  primeThreshold: number;
  stageThreshold: number;
  conviction: number;
  timingFactor: number;
  haloModifier: number;
  tide: MuseTide;
  halo: Record<string, unknown>;
  spark: Record<string, unknown>;
  entry: number | null;
  stop: number | null;
  target: number | null;
  rr: number | null;
  atr: number | null;
  prisms: MusePrism[];
  gates: MuseGate[];
  blockingReasons: string[];
  generatedAt: string;
  barClosedAt: string;
  timeframes: Record<'atlas' | 'current' | 'vector' | 'spark', string>;
  dataProvenance: Record<string, Record<string, unknown>>;
}

export interface MuseScanState {
  scanId: string;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED';
  startedAt: string;
  completedAt?: string | null;
  totalPairs: number;
  primeCount: number;
  stageCount: number;
  dormantCount: number;
  blockedCount: number;
  errorCount: number;
  topBlockingReasons?: { reason: string; count: number }[];
}

export interface MusePreview {
  executable: boolean;
  checks: { name: string; passed: boolean; reason?: string | null }[];
  quote: Record<string, unknown> | null;
}

export interface MuseHealth {
  engine: string;
  contractVersion: string;
  enabled: boolean;
  timeframes: Record<string, string>;
  tideSchedule: { name: string; kind: string; quality: number; nyStart: string; nyEnd: string }[];
  capabilities: Record<string, unknown>;
  scan: Partial<MuseScanState>;
  asOf: string;
}

const PRISM_META: Record<MusePrismName, { label: string; blurb: string }> = {
  echo: { label: 'Undertow Echo', blurb: 'Sweep depth × reclaim velocity' },
  surge: { label: 'Surge Arc', blurb: 'Post-echo displacement efficiency' },
  haven: { label: 'Haven Lattice', blurb: 'Fresh unfilled imbalance to tap' },
  compass: { label: 'Compass Rose', blurb: 'H4 channel drift alignment' },
};

export function musePrismLabel(name: string): string {
  return PRISM_META[name as MusePrismName]?.label ?? name;
}

export function musePrismBlurb(name: string): string {
  return PRISM_META[name as MusePrismName]?.blurb ?? '';
}

export function museDecisionClass(decision: string): string {
  switch (decision) {
    case 'PRIME':
      return 'bg-cyan-400/15 text-cyan-200 border-cyan-300/30';
    case 'STAGE':
      return 'bg-amber-400/10 text-amber-200 border-amber-300/30';
    case 'DORMANT':
      return 'bg-slate-400/10 text-slate-300 border-slate-400/20';
    default:
      return 'bg-rose-400/10 text-rose-200 border-rose-300/25';
  }
}

export function musePhaseSteps(): { id: MusePhase; label: string }[] {
  return [
    { id: 'DRIFT', label: 'Drift' },
    { id: 'PULL', label: 'Pull' },
    { id: 'SURGE', label: 'Surge' },
    { id: 'SETTLE', label: 'Settle' },
    { id: 'RELEASE', label: 'Release' },
  ];
}

export function musePhaseIndex(phase: string): number {
  return Math.max(0, musePhaseSteps().findIndex((s) => s.id === phase));
}

export function museSetupLabel(setup: string): string {
  switch (setup) {
    case 'TIDAL_SLING':
      return 'Tidal sling';
    case 'UNDERTOW_RECLAIM':
      return 'Undertow reclaim';
    case 'ARC_CONTINUATION':
      return 'Arc continuation';
    case 'HAVEN_TAP':
      return 'Haven tap';
    default:
      return 'No setup';
  }
}

export function museTideLabel(tide?: MuseTide | null): string {
  if (!tide?.window) return 'Off tide';
  return tide.window.replace(/_/g, ' ');
}

export function musePrice(value: unknown, digits = 5): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return value.toFixed(digits);
}

export function museScoreText(score: number | null, max: number | null): string {
  if (score == null || !Number.isFinite(score)) return '—';
  if (max == null || !Number.isFinite(max) || max <= 0) return score.toFixed(1);
  return `${score.toFixed(1)} / ${max.toFixed(0)}`;
}

export function museSignalMatchesQuery(signal: MuseSignal, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [signal.pair, signal.symbol, signal.setup, signal.decision, signal.direction]
    .filter(Boolean)
    .some((v) => String(v).toLowerCase().includes(needle));
}

export function museScanProgress(scan: Partial<MuseScanState> | null | undefined): number | null {
  if (!scan || !scan.totalPairs) return null;
  const done = (scan.primeCount ?? 0) + (scan.stageCount ?? 0) + (scan.dormantCount ?? 0) + (scan.blockedCount ?? 0);
  return Math.min(1, done / Math.max(1, scan.totalPairs));
}

export function museWeakestPrism(signal: MuseSignal): MusePrism | null {
  if (!Array.isArray(signal.prisms) || !signal.prisms.length) return null;
  return [...signal.prisms].sort((a, b) => a.quality - b.quality)[0] ?? null;
}
