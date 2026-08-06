import { fmtNum } from '@/lib/utils';
import type { EngineASignal } from '@/types/athena';

export type DiagnosticDisplay = { text: string; isUnavailable: boolean };

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() !== '') {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value.trim() !== '') return value;
  }
  return null;
}

function unavailable(reason: string): DiagnosticDisplay {
  return { text: `Unavailable — ${reason}`, isUnavailable: true };
}

function available(text: string): DiagnosticDisplay {
  return { text, isUnavailable: false };
}

export function resolveNumericDisplay(
  value: unknown,
  decimals: number,
  missingReason: string,
): DiagnosticDisplay {
  const numeric = firstNumber(value);
  if (numeric === null) return unavailable(missingReason);
  return available(fmtNum(numeric, decimals));
}

export function resolveTextDisplay(value: unknown, missingReason: string): DiagnosticDisplay {
  const text = firstString(value);
  if (text === null) return unavailable(missingReason);
  return available(text);
}

export function resolveDirectionalRampDisplay(signal: EngineASignal | null): DiagnosticDisplay {
  if (!signal) {
    return unavailable('no Engine A candidate for chart symbol');
  }
  const diagnostics = signal.factorDiagnostics;
  if (diagnostics == null || typeof diagnostics !== 'object' || Array.isArray(diagnostics)) {
    return unavailable('factorDiagnostics missing from signal payload');
  }
  const record = diagnostics as Record<string, unknown>;
  const numeric = firstNumber(
    record.directionalRampMult,
    record.directionalRampMultiplier,
    record.directional_ramp_multiplier,
  );
  if (numeric === null) {
    return unavailable('factorDiagnostics.directionalRampMult missing from payload');
  }
  return available(fmtNum(numeric, 2));
}

export function resolveTrendCoherenceRows(diagnostics: Record<string, unknown>): {
  agreement: DiagnosticDisplay;
  ratio: DiagnosticDisplay;
} {
  const trendCoherence = asRecord(diagnostics.trendCoherence);
  return {
    agreement: resolveNumericDisplay(
      trendCoherence.agreement_count,
      0,
      'factorDiagnostics.trendCoherence.agreement_count missing from payload',
    ),
    ratio: resolveNumericDisplay(
      trendCoherence.coherence_ratio,
      5,
      'factorDiagnostics.trendCoherence.coherence_ratio missing from payload',
    ),
  };
}

export function resolveTrendCoherenceTimeframeRows(
  diagnostics: Record<string, unknown>,
): Array<{ timeframe: string; direction: string }> {
  const trendCoherence = asRecord(diagnostics.trendCoherence);
  const perTf = asRecord(trendCoherence.per_tf);
  const rows: Array<{ timeframe: string; direction: string }> = [];

  if (Object.keys(perTf).length > 0) {
    for (const [timeframe, raw] of Object.entries(perTf)) {
      const detail = asRecord(raw);
      const direction = firstString(detail.direction, detail.label, detail.state);
      if (direction) rows.push({ timeframe: timeframe.toUpperCase(), direction });
    }
    return rows;
  }

  for (const [key, raw] of Object.entries(trendCoherence)) {
    if (!/^(d1|h4|h1|m30|m15|m5|m1)$/i.test(key)) continue;
    const direction = firstString(raw);
    if (direction) rows.push({ timeframe: key.toUpperCase(), direction });
  }
  return rows;
}

export function resolveFeedAddonDisplay(diagnostics: Record<string, unknown>): DiagnosticDisplay {
  const feedStatus = asRecord(diagnostics.feedStatus);
  return resolveTextDisplay(feedStatus.addon, 'factorDiagnostics.feedStatus.addon missing from payload');
}

export function resolveAtrProvenanceRows(atrDiagnostics: Record<string, unknown>): {
  timeframe: DiagnosticDisplay;
  source: DiagnosticDisplay;
  candleLastTs: DiagnosticDisplay;
  ageSeconds: DiagnosticDisplay;
  confirmedOnly: DiagnosticDisplay;
} {
  return {
    timeframe: resolveTextDisplay(
      firstString(atrDiagnostics.atr_tf, atrDiagnostics.atrTimeframe),
      'atrDiagnostics.atr_tf missing from payload',
    ),
    source: resolveTextDisplay(
      firstString(atrDiagnostics.atr_source, atrDiagnostics.atrSource),
      'atrDiagnostics.atr_source missing from payload',
    ),
    candleLastTs: resolveTextDisplay(
      atrDiagnostics.atr_candle_last_ts,
      'atrDiagnostics.atr_candle_last_ts missing from payload',
    ),
    ageSeconds: resolveNumericDisplay(
      atrDiagnostics.atr_age_seconds,
      1,
      'atrDiagnostics.atr_age_seconds missing from payload',
    ),
    confirmedOnly: resolveTextDisplay(
      atrDiagnostics.atr_confirmed_only != null ? String(atrDiagnostics.atr_confirmed_only) : null,
      'atrDiagnostics.atr_confirmed_only missing from payload',
    ),
  };
}

export function resolveCandleFetchMetaRows(
  candleFetchMeta: unknown,
): Array<{ label: string; display: DiagnosticDisplay }> {
  const meta = asRecord(candleFetchMeta);
  if (!Object.keys(meta).length) {
    return [{ label: 'Candle fetch meta', display: unavailable('candleFetchMeta missing from signal payload') }];
  }

  const rows: Array<{ label: string; display: DiagnosticDisplay }> = [];
  for (const tf of ['D1', 'H4', 'H1'] as const) {
    const tfMeta = asRecord(meta[tf]);
    if (!Object.keys(tfMeta).length) continue;
    const cacheHit = tfMeta.cacheHit;
    if (cacheHit === true || cacheHit === false) {
      rows.push({
        label: `${tf} cache hit`,
        display: available(String(cacheHit)),
      });
    } else {
      rows.push({
        label: `${tf} cache hit`,
        display: unavailable(`candleFetchMeta.${tf}.cacheHit missing from payload`),
      });
    }
  }

  const pairSource = firstString(meta.pairSource);
  if (pairSource) {
    rows.push({ label: 'Pair source', display: available(pairSource) });
  }

  return rows.length
    ? rows
    : [{ label: 'Candle fetch meta', display: unavailable('candleFetchMeta has no recognized fields') }];
}

export function isFrontendDebugVisible(): boolean {
  if (import.meta.env.DEV) return true;
  if (typeof window === 'undefined') return false;
  return new URLSearchParams(window.location.search).has('debug');
}

export function resolveFrontendBuildLabel(): string | null {
  const meta = (window as Window & { __ATHENA_FRONTEND__?: { buildId?: string; bundle?: string } }).__ATHENA_FRONTEND__;
  if (!meta) return null;
  const parts = [meta.bundle, meta.buildId].filter(Boolean);
  return parts.length ? parts.join(' · ') : null;
}
