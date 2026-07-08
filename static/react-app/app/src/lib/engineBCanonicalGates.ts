import type { EngineBNakedResult, LdEngineBRow } from '@/types/athena';

export type EngineBCanonicalGates = {
  canonicalStructureOk: boolean;
  canonicalLocationOk: boolean;
  canonicalTriggerOk: boolean;
  canonicalRoomOk: boolean;
  canonicalRrOk: boolean;
  canonicalRoomRrOk: boolean;
  canonicalTradeOk: boolean;
  canonicalStatus: string | null;
  canonicalPrimaryRejectReason: string | null;
  canonicalSecondaryRejectReasons: string[];
  confidencePassed: boolean;
  confidenceDisplayLabel: string;
  structuralTpTooClose: boolean;
  suggestedLevelsExecutable: boolean;
};

type GateSource = EngineBNakedResult | LdEngineBRow | null | undefined;

function readConf(data: EngineBNakedResult): Record<string, unknown> {
  return (data.confidence || {}) as Record<string, unknown>;
}

function readChecklist(data: EngineBNakedResult): Record<string, unknown> {
  const conf = readConf(data);
  return (data.checklist || conf.checklist || {}) as Record<string, unknown>;
}

function firstBool(...values: unknown[]): boolean | undefined {
  for (const v of values) {
    if (v !== undefined && v !== null) return Boolean(v);
  }
  return undefined;
}

function zoneBounds(zone: unknown): { low: number | null; high: number | null } {
  if (!zone || typeof zone !== 'object') return { low: null, high: null };
  const z = zone as Record<string, unknown>;
  const low = z.lower != null ? Number(z.lower) : null;
  const high = z.upper != null ? Number(z.upper) : null;
  return {
    low: Number.isFinite(low) ? low : null,
    high: Number.isFinite(high) ? high : null,
  };
}

function priceInZone(low: number | null, high: number | null, price: number | null): boolean {
  if (price == null || low == null || high == null) return false;
  const lo = Math.min(low, high);
  const hi = Math.max(low, high);
  return price >= lo && price <= hi;
}

function reasonCodes(data: EngineBNakedResult, conf: Record<string, unknown>): string[] {
  const diag = conf.engine_b_diagnostics;
  if (diag && typeof diag === 'object') {
    const codes = (diag as Record<string, unknown>).reason_codes;
    if (Array.isArray(codes)) return codes.map(String);
  }
  const raw = conf.reason_codes;
  if (Array.isArray(raw)) return raw.map(String);
  return [];
}

/** Client-side fallback when canonical_* fields are absent (older cache rows). */
function deriveFallbackGates(data: EngineBNakedResult): Omit<
  EngineBCanonicalGates,
  'canonicalStatus' | 'canonicalPrimaryRejectReason' | 'canonicalSecondaryRejectReasons'
> {
  const conf = readConf(data);
  const checklist = readChecklist(data);
  const direction = String(data.direction || conf.direction || '').toUpperCase();
  const entry = Number(
    data.entry ?? data.price ?? conf.entry ?? conf.current_price ?? NaN,
  );
  const entryPrice = Number.isFinite(entry) ? entry : null;
  const tp = Number(
    data.tp ?? conf.execution_tp1 ?? conf.execution_tp ?? NaN,
  );
  const tpPrice = Number.isFinite(tp) ? tp : null;

  const sup = zoneBounds(
    (data as Record<string, unknown>).nearest_support_zone
      ?? conf.nearest_support_zone,
  );
  const res = zoneBounds(
    (data as Record<string, unknown>).nearest_resistance_zone
      ?? conf.nearest_resistance_zone,
  );

  const codes = reasonCodes(data, conf);
  const supportTooClose = codes.includes('support_too_close');
  const resistanceTooClose = codes.includes('resistance_too_close');
  const structuralTpTooClose =
    codes.includes('structural_tp_too_close')
    || Boolean(conf.structural_tp_too_close ?? data.structural_tp_too_close);
  const noTriggerPattern = codes.includes('no_trigger_pattern');

  const entryInsideSupport = priceInZone(sup.low, sup.high, entryPrice);
  const entryInsideResistance = priceInZone(res.low, res.high, entryPrice);
  const tpInsideSupport = priceInZone(sup.low, sup.high, tpPrice);
  const tpInsideResistance = priceInZone(res.low, res.high, tpPrice);

  const rawStructureOk = Boolean(checklist.structure_ok ?? data.structure_ok);
  const rawLocationOk = Boolean(checklist.location_ok ?? data.location_ok);
  const rawTriggerOk = Boolean(
    checklist.entry_ok ?? checklist.trigger_ok ?? data.entry_ok,
  );
  const rawRoomOk = Boolean(
    checklist.room_rr_ok ?? checklist.room_ok ?? data.room_rr_ok,
  );
  const rawRrOk = checklist.rr_ok != null ? Boolean(checklist.rr_ok) : true;

  const locationBlocked =
    (direction === 'LONG' && entryInsideResistance)
    || (direction === 'SHORT' && entryInsideSupport)
    || (direction === 'LONG' && resistanceTooClose)
    || (direction === 'SHORT' && supportTooClose);

  const roomBlocked =
    rawRoomOk === false
    || structuralTpTooClose
    || (direction === 'LONG' && resistanceTooClose)
    || (direction === 'SHORT' && supportTooClose)
    || (direction === 'LONG' && tpInsideResistance)
    || (direction === 'SHORT' && tpInsideSupport);

  const canonicalStructureOk = rawStructureOk;
  const canonicalLocationOk = !locationBlocked && rawLocationOk;
  const canonicalTriggerOk = rawTriggerOk && !noTriggerPattern;
  const canonicalRoomOk = !roomBlocked && rawRoomOk;
  const canonicalRrOk = rawRrOk;
  const canonicalRoomRrOk =
    canonicalRoomOk && canonicalRrOk && !structuralTpTooClose;

  const confidencePassed = Boolean(conf.passed ?? data.passed);
  const canonicalTradeOk = Boolean(
    conf.canonical_trade_ok
    ?? conf.engine_b_canonical_actionable
    ?? data.canonical_trade_ok
    ?? data.engine_b_canonical_actionable
    ?? false,
  );

  return {
    canonicalStructureOk,
    canonicalLocationOk,
    canonicalTriggerOk,
    canonicalRoomOk,
    canonicalRrOk,
    canonicalRoomRrOk,
    canonicalTradeOk,
    confidencePassed,
    confidenceDisplayLabel: confidenceBadgeLabel({ confidencePassed, canonicalTradeOk }),
    structuralTpTooClose,
    suggestedLevelsExecutable: canonicalTradeOk,
  };
}

export function confidenceBadgeLabel({
  confidencePassed,
  canonicalTradeOk,
}: {
  confidencePassed: boolean;
  canonicalTradeOk: boolean;
}): string {
  if (confidencePassed && !canonicalTradeOk) return 'SCORE PASSED / GATE FAILED';
  if (confidencePassed) return 'CONFIDENCE PASSED';
  return 'CONFIDENCE FAILED';
}

export function roomRrBadgeOk({
  canonicalRoomOk,
  canonicalRrOk,
  structuralTpTooClose,
}: {
  canonicalRoomOk: boolean;
  canonicalRrOk: boolean;
  structuralTpTooClose: boolean;
}): boolean {
  return canonicalRoomOk && canonicalRrOk && !structuralTpTooClose;
}

export function readEngineBCanonicalGatesFromNaked(
  data: EngineBNakedResult | null | undefined,
): EngineBCanonicalGates | null {
  if (!data) return null;
  const conf = readConf(data);
  const hasCanonical =
    conf.canonical_trade_ok != null
    || conf.canonical_structure_ok != null
    || data.canonical_trade_ok != null
    || data.canonical_structure_ok != null;

  const fallback = deriveFallbackGates(data);
  if (!hasCanonical) {
    return {
      ...fallback,
      canonicalStatus:
        (conf.engine_b_canonical_status as string | undefined)
        ?? (data.engine_b_canonical_status as string | undefined)
        ?? null,
      canonicalPrimaryRejectReason:
        (conf.canonical_primary_reject_reason as string | undefined)
        ?? (data.canonical_primary_reject_reason as string | undefined)
        ?? null,
      canonicalSecondaryRejectReasons:
        (conf.canonical_secondary_reject_reasons as string[] | undefined)
        ?? (data.canonical_secondary_reject_reasons as string[] | undefined)
        ?? [],
    };
  }

  const canonicalTradeOk = Boolean(
    firstBool(
      conf.canonical_trade_ok,
      data.canonical_trade_ok,
      conf.engine_b_canonical_actionable,
      data.engine_b_canonical_actionable,
    ),
  );
  const confidencePassed = Boolean(
    firstBool(conf.confidence_passed, data.confidence_passed, conf.passed, data.passed),
  );
  const structuralTpTooClose = Boolean(
    firstBool(conf.structural_tp_too_close, data.structural_tp_too_close),
  );
  const canonicalRoomOk = Boolean(
    firstBool(conf.canonical_room_ok, data.canonical_room_ok) ?? fallback.canonicalRoomOk,
  );
  const canonicalRrOk = Boolean(
    firstBool(conf.canonical_rr_ok, data.canonical_rr_ok) ?? fallback.canonicalRrOk,
  );
  const canonicalRoomRrOk = Boolean(
    firstBool(conf.canonical_room_rr_ok, data.canonical_room_rr_ok)
    ?? roomRrBadgeOk({ canonicalRoomOk, canonicalRrOk, structuralTpTooClose }),
  );

  return {
    canonicalStructureOk: Boolean(
      firstBool(conf.canonical_structure_ok, data.canonical_structure_ok)
      ?? fallback.canonicalStructureOk,
    ),
    canonicalLocationOk: Boolean(
      firstBool(conf.canonical_location_ok, data.canonical_location_ok)
      ?? fallback.canonicalLocationOk,
    ),
    canonicalTriggerOk: Boolean(
      firstBool(conf.canonical_trigger_ok, data.canonical_trigger_ok)
      ?? fallback.canonicalTriggerOk,
    ),
    canonicalRoomOk,
    canonicalRrOk,
    canonicalRoomRrOk,
    canonicalTradeOk,
    canonicalStatus:
      (conf.canonical_status as string | undefined)
      ?? (data.canonical_status as string | undefined)
      ?? (conf.engine_b_canonical_status as string | undefined)
      ?? (data.engine_b_canonical_status as string | undefined)
      ?? null,
    canonicalPrimaryRejectReason:
      (conf.canonical_primary_reject_reason as string | undefined)
      ?? (data.canonical_primary_reject_reason as string | undefined)
      ?? null,
    canonicalSecondaryRejectReasons:
      (conf.canonical_secondary_reject_reasons as string[] | undefined)
      ?? (data.canonical_secondary_reject_reasons as string[] | undefined)
      ?? [],
    confidencePassed,
    confidenceDisplayLabel: confidenceBadgeLabel({ confidencePassed, canonicalTradeOk }),
    structuralTpTooClose,
    suggestedLevelsExecutable: Boolean(
      firstBool(conf.suggested_levels_executable, data.suggested_levels_executable)
      ?? canonicalTradeOk,
    ),
  };
}

export function readEngineBCanonicalGatesFromRow(
  row: LdEngineBRow | null | undefined,
): EngineBCanonicalGates | null {
  if (!row) return null;
  const canonicalTradeOk = Boolean(row.canonicalTradeOk ?? false);
  const confidencePassed = Boolean(row.confidencePassed);
  const structuralTpTooClose = Boolean(row.structuralTpTooClose);
  const canonicalRoomOk = Boolean(row.canonicalRoomOk ?? row.room_rr_ok);
  const canonicalRrOk = Boolean(row.canonicalRrOk ?? true);
  const canonicalRoomRrOk = Boolean(
    row.canonicalRoomRrOk
    ?? roomRrBadgeOk({ canonicalRoomOk, canonicalRrOk, structuralTpTooClose }),
  );

  return {
    canonicalStructureOk: Boolean(row.canonicalStructureOk ?? row.structure_ok),
    canonicalLocationOk: Boolean(row.canonicalLocationOk ?? row.location_ok),
    canonicalTriggerOk: Boolean(row.canonicalTriggerOk ?? row.entry_ok),
    canonicalRoomOk,
    canonicalRrOk,
    canonicalRoomRrOk,
    canonicalTradeOk: Boolean(row.canonicalTradeOk ?? false),
    canonicalStatus: row.canonicalStatus ?? null,
    canonicalPrimaryRejectReason: row.canonicalPrimaryRejectReason ?? null,
    canonicalSecondaryRejectReasons: row.canonicalSecondaryRejectReasons ?? [],
    confidencePassed,
    confidenceDisplayLabel:
      row.confidenceDisplayLabel
      ?? confidenceBadgeLabel({ confidencePassed, canonicalTradeOk }),
    structuralTpTooClose,
    suggestedLevelsExecutable: Boolean(row.suggestedLevelsExecutable ?? canonicalTradeOk),
  };
}

export function executableLevels(
  data: EngineBNakedResult,
  gates: EngineBCanonicalGates,
  pair?: string,
  type?: string,
): {
  showExecutable: boolean;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  diagnosticEntry: number | null;
  diagnosticSl: number | null;
  diagnosticTp: number | null;
} {
  const conf = readConf(data);
  const entry = (data.entry ?? data.price ?? conf.entry) as number | null | undefined;
  const sl = (data.sl ?? conf.execution_sl) as number | null | undefined;
  const tp = (data.tp ?? conf.execution_tp1 ?? conf.execution_tp) as number | null | undefined;
  const diagEntry = entry != null ? Number(entry) : null;
  const diagSl = sl != null ? Number(sl) : null;
  const diagTp = tp != null ? Number(tp) : null;

  return {
    showExecutable: gates.suggestedLevelsExecutable,
    entry: gates.suggestedLevelsExecutable ? diagEntry : null,
    sl: gates.suggestedLevelsExecutable ? diagSl : null,
    tp: gates.suggestedLevelsExecutable ? diagTp : null,
    diagnosticEntry: diagEntry,
    diagnosticSl: diagSl,
    diagnosticTp: diagTp,
  };
}

export function readEngineBCanonicalGates(source: GateSource): EngineBCanonicalGates | null {
  if (!source) return null;
  if ('structuralVerdict' in source) {
    return readEngineBCanonicalGatesFromRow(source as LdEngineBRow);
  }
  return readEngineBCanonicalGatesFromNaked(source as EngineBNakedResult);
}
