import type { EngineASignal } from '@/types/athena';

type RawSignal = Record<string, unknown>;

function pickField<T>(
  raw: RawSignal,
  topKey: string,
  stubKey: string,
): T | undefined {
  const fromStub = raw[stubKey];
  if (fromStub !== undefined && fromStub !== null) return fromStub as T;
  const fromTop = raw[topKey];
  if (fromTop !== undefined && fromTop !== null) return fromTop as T;
  return undefined;
}

/** True for native V3 payloads and Engine B rows that preserved blocked V3 diagnostics. */
export function isEngineAV3Signal(signal: EngineASignal | null | undefined): boolean {
  if (!signal) return false;
  const raw = signal as RawSignal;
  if (
    String(raw.engine || '').toUpperCase() === 'ENGINE_A_V3'
    && String(raw.contractVersion || '').startsWith('3.')
  ) {
    return true;
  }
  if (raw.engine_a_v3_blocked === true) return true;
  if (raw.engine_a_setupId != null || raw.engine_a_decision != null) return true;
  return false;
}

/** Merge engine_a_* stub fields into a V3-shaped signal for cards and chart panels. */
export function resolveEngineAV3Signal(signal: EngineASignal): EngineASignal {
  if (
    String(signal.engine || '').toUpperCase() === 'ENGINE_A_V3'
    && String(signal.contractVersion || '').startsWith('3.')
  ) {
    return signal;
  }
  if (!isEngineAV3Signal(signal)) return signal;

  const raw = signal as RawSignal;
  return {
    ...signal,
    engine: 'ENGINE_A_V3',
    contractVersion: pickField<string>(raw, 'contractVersion', 'engine_a_contractVersion') ?? '3.0.0',
    setupId: pickField(raw, 'setupId', 'engine_a_setupId'),
    decision: pickField(raw, 'decision', 'engine_a_decision'),
    rejectionReasons: pickField(raw, 'rejectionReasons', 'engine_a_rejectionReasons'),
    predicates: pickField(raw, 'predicates', 'engine_a_predicates'),
    qualified: pickField(raw, 'qualified', 'engine_a_qualified'),
    price: pickField(raw, 'price', 'engine_a_price'),
    sl: pickField(raw, 'sl', 'engine_a_sl'),
    tp1: pickField(raw, 'tp1', 'engine_a_tp1'),
    tp2: pickField(raw, 'tp2', 'engine_a_tp2'),
    rr1: pickField(raw, 'rr1', 'engine_a_rr1'),
    rr2: pickField(raw, 'rr2', 'engine_a_rr2'),
    entryZone: pickField(raw, 'entryZone', 'engine_a_entryZone'),
    invalidation: pickField(raw, 'invalidation', 'engine_a_invalidation'),
    family: pickField(raw, 'family', 'engine_a_family'),
    subclass: pickField(raw, 'subclass', 'engine_a_subclass'),
    horizon: pickField(raw, 'horizon', 'engine_a_horizon'),
    validationStatus: pickField(raw, 'validationStatus', 'engine_a_validationStatus'),
    validationArtifact: pickField(raw, 'validationArtifact', 'engine_a_validationArtifact'),
    engineATradeEnabled: pickField(raw, 'engineATradeEnabled', 'engine_a_engineATradeEnabled'),
    direction: pickField(raw, 'direction', 'engine_a_direction') ?? signal.direction,
  };
}

/** Sort key when confluenceScore is absent: TRADE > WATCH > NO_SIGNAL. */
export function engineAV3DecisionRank(signal: EngineASignal | null | undefined): number {
  if (!signal || !isEngineAV3Signal(signal)) return -1;
  const decision = String(resolveEngineAV3Signal(signal).decision || 'NO_SIGNAL').toUpperCase();
  if (decision === 'TRADE') return 3;
  if (decision === 'WATCH') return 2;
  if (decision === 'NO_SIGNAL') return 1;
  return 0;
}

export function engineAV3ListLabel(signal: EngineASignal | null | undefined): string | null {
  if (!signal || !isEngineAV3Signal(signal)) return null;
  const resolved = resolveEngineAV3Signal(signal);
  const decision = String(resolved.decision || 'NO_SIGNAL').toUpperCase();
  const predicates = Array.isArray(resolved.predicates) ? resolved.predicates : [];
  const passed = predicates.filter((p) => p?.passed).length;
  const total = predicates.length;
  if (total > 0) return `${decision} · ${passed}/${total} rules`;
  return decision;
}
