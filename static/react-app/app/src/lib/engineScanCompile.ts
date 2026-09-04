import { compactChartSymbolKey, resolveChartIntentSymbol } from './chartIdentity';

export const COMPILE_ENGINE_IDS = [
  'engineA',
  'engineB',
  'sol',
  'opus',
  'kimi',
  'oxAlpha',
  'grok',
  'fable',
  'muse',
] as const;

export type CompileEngineId = (typeof COMPILE_ENGINE_IDS)[number];

export type CompileStance = 'pass' | 'watch' | 'fail' | 'absent';

export type CompileDirection = 'LONG' | 'SHORT' | 'NONE';

export type CompileAgreement = 'agree' | 'conflict' | 'mixed' | 'solo' | 'none';

export interface CompileInputRow {
  pair?: string | null;
  display?: string | null;
  symbol?: string | null;
  direction?: string | null;
  decision?: string | null;
  score?: number | null;
  maxScore?: number | null;
  reason?: string | null;
  stance?: CompileStance;
}

export interface EngineScanSnapshot {
  engine: CompileEngineId;
  scannedAt: string;
  rows: CompileInputRow[];
}

export interface CompileEngineHit {
  engine: CompileEngineId;
  stance: CompileStance;
  decision: string | null;
  direction: CompileDirection | null;
  score: number | null;
  maxScore: number | null;
  reason: string | null;
}

export interface CompileRow {
  key: string;
  display: string;
  hits: Record<CompileEngineId, CompileEngineHit | null>;
  passedEngines: CompileEngineId[];
  failedEngines: CompileEngineId[];
  watchEngines: CompileEngineId[];
  agreement: CompileAgreement;
  agreedDirection: 'LONG' | 'SHORT' | null;
}

export interface CompileBoard {
  scannedEngines: CompileEngineId[];
  scannedAt: Partial<Record<CompileEngineId, string>>;
  rows: CompileRow[];
}

export const COMPILE_ENGINE_META: Record<CompileEngineId, { label: string; short: string; panel: string }> = {
  engineA: { label: 'Engine A', short: 'A', panel: 'signals' },
  engineB: { label: 'Engine B', short: 'B', panel: 'signals' },
  sol: { label: 'SOL', short: 'SOL', panel: 'solEngine' },
  opus: { label: 'OPUS', short: 'OPUS', panel: 'opusEngine' },
  kimi: { label: 'KIMI', short: 'KIMI', panel: 'kimiEngine' },
  oxAlpha: { label: 'OX Alpha', short: 'OX', panel: 'oxAlpha' },
  grok: { label: 'GROK', short: 'GROK', panel: 'grokEngine' },
  fable: { label: 'FABLE', short: 'FBL', panel: 'fableEngine' },
  muse: { label: 'MUSE', short: 'MUSE', panel: 'museEngine' },
};

const PASS_DECISIONS = new Set(['TRADE', 'READY', 'PRIME']);
const WATCH_DECISIONS = new Set(['WATCH', 'ARMED', 'WATCHLIST', 'STAGE']);

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  if (value == null) return null;
  const out = String(value).trim();
  return out ? out : null;
}

function num(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function unixOrIso(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    const ms = value < 1e12 ? value * 1000 : value;
    return new Date(ms).toISOString();
  }
  const raw = text(value);
  if (raw) return raw;
  return new Date().toISOString();
}

export function compileDirection(value: unknown): CompileDirection | null {
  const raw = String(value || '').trim().toUpperCase();
  if (raw === 'LONG' || raw === 'BUY' || raw === 'BULL') return 'LONG';
  if (raw === 'SHORT' || raw === 'SELL' || raw === 'BEAR') return 'SHORT';
  if (raw === 'NONE' || raw === 'NEUTRAL' || raw === 'FLAT') return 'NONE';
  return null;
}

export function compileStanceFromDecision(decision: unknown, fallback: CompileStance = 'fail'): CompileStance {
  const raw = String(decision || '').trim().toUpperCase();
  if (!raw) return fallback;
  if (PASS_DECISIONS.has(raw)) return 'pass';
  if (WATCH_DECISIONS.has(raw)) return 'watch';
  return 'fail';
}

export function compileInstrumentKey(row: CompileInputRow): string | null {
  const display = text(row.display);
  const pair = text(row.pair);
  const symbol = text(row.symbol);
  const resolved = resolveChartIntentSymbol({
    symbol: symbol || pair || display || '',
    display: display || pair || '',
  });
  return compactChartSymbolKey(resolved)
    || compactChartSymbolKey(pair)
    || compactChartSymbolKey(display)
    || compactChartSymbolKey(symbol);
}

function preferredDisplay(rows: CompileInputRow[], key: string): string {
  const candidates = rows.flatMap((row) => [row.display, row.pair, row.symbol].map(text).filter((v): v is string => Boolean(v)));
  const slash = candidates.find((value) => value.includes('/'));
  if (slash) return slash.toUpperCase().replace(/\s+/g, '');
  const resolved = rows
    .map((row) => resolveChartIntentSymbol({
      symbol: text(row.symbol) || text(row.pair) || '',
      display: text(row.display) || text(row.pair) || '',
    }))
    .find((value) => value && !value.includes('='));
  if (resolved) return resolved;
  return candidates[0] || key;
}

function firstReason(...values: unknown[]): string | null {
  for (const value of values) {
    if (Array.isArray(value)) {
      const hit = value.map(text).find(Boolean);
      if (hit) return hit;
      continue;
    }
    const hit = text(value);
    if (hit) return hit;
  }
  return null;
}

function stanceRank(stance: CompileStance): number {
  if (stance === 'pass') return 3;
  if (stance === 'watch') return 2;
  if (stance === 'fail') return 1;
  return 0;
}

function pickPreferredRow(current: CompileInputRow | undefined, next: CompileInputRow): CompileInputRow {
  if (!current) return next;
  const currentStance = current.stance || compileStanceFromDecision(current.decision);
  const nextStance = next.stance || compileStanceFromDecision(next.decision);
  if (stanceRank(nextStance) !== stanceRank(currentStance)) {
    return stanceRank(nextStance) > stanceRank(currentStance) ? next : current;
  }
  const currentScore = num(current.score) ?? -Infinity;
  const nextScore = num(next.score) ?? -Infinity;
  return nextScore > currentScore ? next : current;
}

function emptyHits(): Record<CompileEngineId, CompileEngineHit | null> {
  return {
    engineA: null,
    engineB: null,
    sol: null,
    opus: null,
    kimi: null,
    oxAlpha: null,
    grok: null,
    fable: null,
    muse: null,
  };
}

function classifyAgreement(passed: CompileEngineHit[], failed: CompileEngineId[]): {
  agreement: CompileAgreement;
  agreedDirection: 'LONG' | 'SHORT' | null;
} {
  const passDirs = new Set(
    passed
      .map((hit) => hit.direction)
      .filter((dir): dir is 'LONG' | 'SHORT' => dir === 'LONG' || dir === 'SHORT'),
  );
  if (passed.length >= 2 && passDirs.size > 1) {
    return { agreement: 'conflict', agreedDirection: null };
  }
  const agreedDirection = passDirs.size === 1 ? [...passDirs][0] : (passed.length === 1 && (passed[0].direction === 'LONG' || passed[0].direction === 'SHORT') ? passed[0].direction : null);
  if (passed.length >= 1 && failed.length >= 1) {
    return { agreement: 'mixed', agreedDirection };
  }
  if (passed.length >= 2) {
    return { agreement: 'agree', agreedDirection };
  }
  if (passed.length === 1) {
    return { agreement: 'solo', agreedDirection };
  }
  return { agreement: 'none', agreedDirection: null };
}

export function compileEngineScanBoard(snapshots: EngineScanSnapshot[]): CompileBoard {
  const scannedEngines = COMPILE_ENGINE_IDS.filter((id) => snapshots.some((snap) => snap.engine === id));
  const scannedAt: Partial<Record<CompileEngineId, string>> = {};
  const byEngine = new Map<CompileEngineId, Map<string, CompileInputRow>>();
  const displaySource = new Map<string, CompileInputRow[]>();

  for (const snapshot of snapshots) {
    if (!COMPILE_ENGINE_IDS.includes(snapshot.engine)) continue;
    scannedAt[snapshot.engine] = snapshot.scannedAt;
    const rows = new Map<string, CompileInputRow>();
    for (const raw of snapshot.rows || []) {
      const key = compileInstrumentKey(raw);
      if (!key) continue;
      rows.set(key, pickPreferredRow(rows.get(key), raw));
      const bucket = displaySource.get(key) || [];
      bucket.push(raw);
      displaySource.set(key, bucket);
    }
    byEngine.set(snapshot.engine, rows);
  }

  const keys = new Set<string>();
  for (const rows of byEngine.values()) {
    for (const key of rows.keys()) keys.add(key);
  }

  const compiled: CompileRow[] = [...keys].map((key) => {
    const hits = emptyHits();
    for (const engine of scannedEngines) {
      const match = byEngine.get(engine)?.get(key);
      if (!match) {
        hits[engine] = {
          engine,
          stance: 'absent',
          decision: null,
          direction: null,
          score: null,
          maxScore: null,
          reason: 'not in this scan',
        };
        continue;
      }
      const stance = match.stance || compileStanceFromDecision(match.decision);
      hits[engine] = {
        engine,
        stance,
        decision: text(match.decision)?.toUpperCase() ?? null,
        direction: compileDirection(match.direction),
        score: num(match.score),
        maxScore: num(match.maxScore),
        reason: text(match.reason),
      };
    }

    const passed = COMPILE_ENGINE_IDS.map((engine) => hits[engine]).filter((hit): hit is CompileEngineHit => hit?.stance === 'pass');
    const failed = COMPILE_ENGINE_IDS.filter((engine) => hits[engine]?.stance === 'fail' || hits[engine]?.stance === 'absent');
    const watch = COMPILE_ENGINE_IDS.filter((engine) => hits[engine]?.stance === 'watch');
    const { agreement, agreedDirection } = classifyAgreement(passed, failed);

    return {
      key,
      display: preferredDisplay(displaySource.get(key) || [], key),
      hits,
      passedEngines: passed.map((hit) => hit.engine),
      failedEngines: failed,
      watchEngines: watch,
      agreement,
      agreedDirection,
    };
  });

  compiled.sort((a, b) => {
    if (b.passedEngines.length !== a.passedEngines.length) return b.passedEngines.length - a.passedEngines.length;
    if (a.agreement !== b.agreement) {
      const order: Record<CompileAgreement, number> = { conflict: 0, mixed: 1, agree: 2, solo: 3, none: 4 };
      return order[a.agreement] - order[b.agreement];
    }
    return a.display.localeCompare(b.display);
  });

  return { scannedEngines, scannedAt, rows: compiled };
}

function engineAStance(row: Record<string, unknown>): CompileStance {
  const tier = String(row.signalTier || row.scan_tier || row.signalClass || '').toLowerCase();
  if (tier === 'trade') return 'pass';
  if (tier.includes('watch')) return 'watch';
  if (tier && tier !== 'trade') return 'fail';
  return compileStanceFromDecision(row.decision);
}

function engineBStance(row: Record<string, unknown>, naked: Record<string, unknown> | null): CompileStance {
  const tradeOk = naked?.canonical_trade_ok ?? row.canonical_trade_ok
    ?? naked?.engine_b_canonical_actionable ?? row.engine_b_canonical_actionable;
  if (tradeOk === true) return 'pass';
  if (tradeOk === false) return 'fail';
  if (row.passed === true || naked?.passed === true) return 'pass';
  const decision = compileStanceFromDecision(row.decision, 'fail');
  return decision;
}

function engineBReason(row: Record<string, unknown>, naked: Record<string, unknown> | null): string | null {
  const conf = asRecord(naked?.confidence) || asRecord(row.confidence);
  return firstReason(
    naked?.canonical_primary_reject_reason,
    row.canonical_primary_reject_reason,
    conf?.canonical_primary_reject_reason,
    naked?.no_trigger_classification,
    row.no_trigger_classification,
    (naked?.hard_fail_reasons as unknown[] | undefined)?.[0],
    (row.hard_fail_reasons as unknown[] | undefined)?.[0],
    (row.rejectionReasons as unknown[] | undefined)?.[0],
  );
}

export function snapshotFromEngineA(
  signals: unknown[] | null | undefined,
  scannedAt?: string | null,
): EngineScanSnapshot | null {
  if (!Array.isArray(signals)) return null;
  return {
    engine: 'engineA',
    scannedAt: unixOrIso(scannedAt),
    rows: signals.map((item) => {
      const row = asRecord(item) || {};
      return {
        pair: text(row.pair),
        display: text(row.display) || text(row.pair),
        symbol: text(row.symbol),
        direction: text(row.direction),
        decision: text(row.decision) || text(row.signalClass),
        score: num(row.score) ?? num(row.confluenceScore) ?? num(row.authoritativeScore),
        maxScore: num(row.maxScore),
        reason: firstReason(row.rejectionReasons, row.decisionReason),
        stance: engineAStance(row),
      };
    }),
  };
}

export function snapshotFromEngineB(
  signals: unknown[] | null | undefined,
  scannedAt?: string | null,
  rejected?: unknown[] | null,
): EngineScanSnapshot | null {
  if (!Array.isArray(signals) && !Array.isArray(rejected)) return null;
  const rows: CompileInputRow[] = [];
  const seen = new Set<string>();

  const push = (item: unknown, forceFail = false) => {
    const row = asRecord(item) || {};
    const naked = asRecord(row.naked_data) || asRecord(row.engine_b);
    const mapped: CompileInputRow = {
      pair: text(row.pair),
      display: text(row.display) || text(row.pair),
      symbol: text(row.symbol),
      direction: text(row.direction) || text(naked?.direction),
      decision: text(row.decision) || text(naked?.canonical_status) || text(row.canonical_status),
      score: num(row.score) ?? num(asRecord(row.confidence)?.score) ?? num(naked?.score) ?? num(row.confidence_score),
      maxScore: num(row.maxScore) ?? num(asRecord(row.confidence)?.max_score) ?? num(naked?.max_score),
      reason: engineBReason(row, naked),
      stance: forceFail ? 'fail' : engineBStance(row, naked),
    };
    const key = compileInstrumentKey(mapped);
    if (!key || seen.has(key)) return;
    seen.add(key);
    rows.push(mapped);
  };

  for (const item of signals || []) push(item);
  for (const item of rejected || []) push(item, true);
  return {
    engine: 'engineB',
    scannedAt: unixOrIso(scannedAt),
    rows,
  };
}

export function snapshotFromSolLikeSignals(
  engine: 'sol' | 'grok',
  signals: unknown[] | null | undefined,
  scannedAt?: string | number | null,
): EngineScanSnapshot {
  return {
    engine,
    scannedAt: unixOrIso(scannedAt),
    rows: (signals || []).map((item) => {
      const row = asRecord(item) || {};
      return {
        pair: text(row.pair),
        display: text(row.display) || text(row.pair),
        symbol: text(row.symbol),
        direction: text(row.direction),
        decision: text(row.decision),
        score: num(row.score),
        maxScore: num(row.maxScore),
        reason: firstReason(row.decisionReason, row.blockingReasons, row.reason),
      };
    }),
  };
}

/** MUSE decisions (PRIME / STAGE / DORMANT / BLOCKED) map onto the compile stances explicitly. */
export function snapshotFromMuseSignals(
  signals: unknown[] | null | undefined,
  scannedAt?: string | number | null,
): EngineScanSnapshot {
  return {
    engine: 'muse',
    scannedAt: unixOrIso(scannedAt),
    rows: (signals || []).map((item) => {
      const row = asRecord(item) || {};
      const decision = text(row.decision);
      const stance: CompileStance = decision === 'PRIME' ? 'pass' : decision === 'STAGE' ? 'watch' : 'fail';
      return {
        pair: text(row.pair),
        display: text(row.display) || text(row.pair),
        symbol: text(row.symbol),
        direction: text(row.direction),
        decision,
        score: num(row.score),
        maxScore: num(row.maxScore) ?? 100,
        reason: firstReason(row.decisionReason, row.blockingReasons, row.reason),
        stance,
      };
    }),
  };
}

/** FABLE decisions (EXECUTE / STAGE / OBSERVE / VOID) map onto the compile stances explicitly. */
export function snapshotFromFableSignals(
  signals: unknown[] | null | undefined,
  scannedAt?: string | number | null,
): EngineScanSnapshot {
  return {
    engine: 'fable',
    scannedAt: unixOrIso(scannedAt),
    rows: (signals || []).map((item) => {
      const row = asRecord(item) || {};
      const decision = text(row.decision);
      const stance: CompileStance = decision === 'EXECUTE' ? 'pass' : decision === 'STAGE' ? 'watch' : 'fail';
      return {
        pair: text(row.pair),
        display: text(row.display) || text(row.pair),
        symbol: text(row.symbol),
        direction: text(row.direction),
        decision,
        score: num(row.coherence),
        maxScore: num(row.maxCoherence) ?? 100,
        reason: firstReason(row.decisionReason, row.voidReasons, row.reason),
        stance,
      };
    }),
  };
}

export function snapshotFromOpusScan(
  signals: unknown[] | null | undefined,
  scannedAt?: string | number | null,
): EngineScanSnapshot {
  return {
    engine: 'opus',
    scannedAt: unixOrIso(scannedAt),
    rows: (signals || []).map((item) => {
      const row = asRecord(item) || {};
      const decision = text(row.decision);
      const readiness = text(row.readiness);
      return {
        pair: text(row.symbol),
        display: text(row.display) || text(row.symbol),
        symbol: text(row.symbol),
        direction: text(row.direction),
        decision,
        score: num(row.expectancyR) ?? num(row.conviction),
        maxScore: null,
        reason: firstReason(row.blockingGates, row.notes, readiness && readiness !== 'READY' ? readiness : null),
        stance: compileStanceFromDecision(decision),
      };
    }),
  };
}

export function snapshotFromOxEnvelope(envelope: {
  scannedAt?: number | string;
  signals?: unknown[];
} | null | undefined): EngineScanSnapshot {
  const signals = envelope?.signals || [];
  return {
    engine: 'oxAlpha',
    scannedAt: unixOrIso(envelope?.scannedAt),
    rows: signals.map((item) => {
      const row = asRecord(item) || {};
      return {
        pair: text(row.display),
        display: text(row.display),
        symbol: text(row.symbol),
        direction: text(row.direction),
        decision: text(row.decision),
        score: num(row.score),
        maxScore: null,
        reason: firstReason(row.entryReadinessReason, row.playType),
        stance: compileStanceFromDecision(row.decision),
      };
    }),
  };
}

export function snapshotFromKimiState(state: {
  lastScan?: number | string | null;
  scanCount?: number | null;
  minScore?: number | null;
  signals?: unknown[] | null;
  symbols?: Record<string, unknown> | null;
} | null | undefined): EngineScanSnapshot | null {
  if (!state) return null;
  const signals = Array.isArray(state.signals) ? state.signals : [];
  const symbols = asRecord(state.symbols) || {};
  if (!signals.length && !Object.keys(symbols).length) return null;

  const signalBySymbol = new Map<string, Record<string, unknown>>();
  for (const item of signals) {
    const row = asRecord(item);
    const symbol = text(row?.symbol)?.toUpperCase();
    if (row && symbol) signalBySymbol.set(symbol, row);
  }

  const rows: CompileInputRow[] = [];
  const seen = new Set<string>();

  for (const [symbol, item] of signalBySymbol) {
    const reasons = item.reasons;
    rows.push({
      symbol,
      display: symbol.length === 6 ? `${symbol.slice(0, 3)}/${symbol.slice(3)}` : symbol,
      direction: text(item.direction),
      decision: 'READY',
      score: num(item.score),
      reason: firstReason(item.grade, reasons),
      stance: 'pass',
    });
    seen.add(symbol);
  }

  for (const [symbolRaw, cardWrap] of Object.entries(symbols)) {
    const symbol = symbolRaw.toUpperCase();
    if (seen.has(symbol)) continue;
    const wrap = asRecord(cardWrap);
    const cards = asRecord(wrap?.cards);
    const long = asRecord(cards?.long);
    const short = asRecord(cards?.short);
    const longScore = num(long?.total) ?? -Infinity;
    const shortScore = num(short?.total) ?? -Infinity;
    const useShort = shortScore > longScore;
    const card = useShort ? short : long;
    rows.push({
      symbol,
      display: symbol.length === 6 ? `${symbol.slice(0, 3)}/${symbol.slice(3)}` : symbol,
      direction: useShort ? 'SHORT' : 'LONG',
      decision: 'NO_SIGNAL',
      score: num(card?.total),
      reason: firstReason(card?.reasons, card?.grade),
      stance: 'fail',
    });
  }

  return {
    engine: 'kimi',
    scannedAt: unixOrIso(state.lastScan),
    rows,
  };
}

export function buildCompileSnapshots(input: {
  scanCacheA?: unknown[] | null;
  scanCacheAMeta?: { scannedAt?: string } | null;
  scanCacheB?: unknown[] | null;
  scanCacheBMeta?: { scannedAt?: string; rejectedDiagnostics?: unknown[] } | null;
  extra?: Partial<Record<CompileEngineId, EngineScanSnapshot | null | undefined>>;
}): EngineScanSnapshot[] {
  const out: EngineScanSnapshot[] = [];
  if (input.scanCacheA) {
    const snap = snapshotFromEngineA(input.scanCacheA, input.scanCacheAMeta?.scannedAt);
    if (snap) out.push(snap);
  }
  if (input.scanCacheB || input.scanCacheBMeta?.rejectedDiagnostics) {
    const snap = snapshotFromEngineB(
      input.scanCacheB,
      input.scanCacheBMeta?.scannedAt,
      input.scanCacheBMeta?.rejectedDiagnostics,
    );
    if (snap) out.push(snap);
  }
  for (const engine of COMPILE_ENGINE_IDS) {
    if (engine === 'engineA' || engine === 'engineB') continue;
    const extra = input.extra?.[engine];
    if (extra) out.push(extra);
  }
  return out;
}

export function formatCompileScore(score: number | null, maxScore: number | null): string {
  if (score == null || !Number.isFinite(score)) return '—';
  const scoreText = Number.isInteger(score) ? String(score) : score.toFixed(2);
  if (maxScore == null || !Number.isFinite(maxScore) || maxScore <= 0) return scoreText;
  const maxText = Number.isInteger(maxScore) ? String(maxScore) : maxScore.toFixed(2);
  return `${scoreText}/${maxText}`;
}
