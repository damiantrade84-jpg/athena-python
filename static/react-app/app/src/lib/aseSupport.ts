import type { ASEShadowContext } from '@/types/athena';
import type { Tone } from '@/components/shared/primitives';

export interface ASESupportDisplay {
  label: string;
  tone: Tone;
  detail: string;
  reason: string;
  title: string;
  supported: boolean;
}

const REASON_LABELS: Record<string, string> = {
  ase_trade_aligned_positive: 'Aligned ASE TRADE with positive expected net R',
  ase_trade_opposed: 'ASE TRADE direction opposes Engine B',
  ase_watch_only: 'ASE is WATCH only, not trade permission',
  ase_decision_flat: 'ASE has no active directional trade view',
  ase_expected_net_r_not_positive: 'ASE expected net R is not positive',
  ase_data_or_model_unhealthy: 'ASE data quality or model health did not pass',
  ase_decision_error: 'ASE decision returned an error',
  ase_metrics_invalid: 'ASE probability or expectancy is unavailable',
  ase_decision_status_unknown: 'ASE decision status is not recognised',
  ase_signal_stale: 'Matching ASE decision is stale',
  ase_asof_signal_missing: 'No ASE decision existed at this signal time',
  ase_horizon_signal_missing: 'No ASE decision exists for this style horizon',
  ase_horizon_missing: 'ASE journal row has no horizon',
  ase_horizon_unresolved: 'Engine B style could not be mapped to an ASE horizon',
  ase_journal_empty: 'No ASE journal decision is available',
  ase_shadow_internal_error: 'ASE shadow context could not be resolved',
  ase_signal_unavailable: 'ASE context is unavailable',
};

function finite(value: unknown): number | null {
  const parsed = Number(value);
  return value != null && value !== '' && Number.isFinite(parsed) ? parsed : null;
}

function metricDetail(shadow: ASEShadowContext): string {
  const parts: string[] = [];
  const direction = String(shadow.direction || '').toUpperCase();
  if (direction === 'LONG' || direction === 'SHORT') parts.push(direction);

  const expectedNetR = finite(shadow.expectedNetR);
  if (expectedNetR != null) {
    parts.push(`E[net R] ${expectedNetR >= 0 ? '+' : ''}${expectedNetR.toFixed(3)}`);
  }

  const probability = finite(shadow.probabilityPositive);
  if (probability != null) {
    const pct = probability >= 0 && probability <= 1 ? probability * 100 : probability;
    parts.push(`P+ ${pct.toFixed(0)}%`);
  }

  if (shadow.horizon) parts.push(String(shadow.horizon).toUpperCase());
  return parts.join(' · ');
}

function result(
  shadow: ASEShadowContext,
  label: string,
  tone: Tone,
  supported: boolean,
): ASESupportDisplay {
  const reason = REASON_LABELS[String(shadow.reason || '')]
    || String(shadow.reason || 'ASE context is unavailable').replaceAll('_', ' ');
  const detail = metricDetail(shadow);
  return {
    label,
    tone,
    detail,
    reason,
    supported,
    title: `${reason}. Advisory only: no Engine B score, gate, eligibility, level, or execution change.`,
  };
}

/**
 * Convert the backend's shadow contract into conservative display semantics.
 * Malformed or contradictory payloads never render as positive support.
 */
export function resolveASESupportDisplay(
  shadow: ASEShadowContext | null | undefined,
): ASESupportDisplay | null {
  if (!shadow) return null;

  const state = String(shadow.supportState || '').toUpperCase();
  const status = String(shadow.decisionStatus || '').toUpperCase();
  const alignment = String(shadow.alignment || '').toUpperCase();
  const expectedNetR = finite(shadow.expectedNetR);

  if (state === 'SUPPORTED') {
    const contractValid = shadow.supportEligible === true
      && status === 'TRADE'
      && alignment === 'ALIGNED'
      && expectedNetR != null
      && expectedNetR > 0
      && shadow.dataQualityOk === true
      && shadow.modelHealthOk === true
      && shadow.affectsEngineBScore === false;
    return contractValid
      ? result(shadow, 'ASE SUPPORTS', 'long', true)
      : result(
          { ...shadow, reason: 'ase_shadow_internal_error' },
          'ASE UNAVAILABLE',
          'muted',
          false,
        );
  }
  if (state === 'OPPOSED') return result(shadow, 'ASE OPPOSES', 'warning', false);
  if (state === 'WATCH_ALIGNED') return result(shadow, 'ASE WATCH ALIGNED', 'accent', false);
  if (state === 'WATCH_OPPOSED') return result(shadow, 'ASE WATCH OPPOSED', 'warning', false);
  if (state === 'NEUTRAL') return result(shadow, 'ASE NEUTRAL', 'muted', false);
  if (state === 'NOT_POSITIVE') return result(shadow, 'ASE NO POSITIVE EDGE', 'muted', false);
  if (state === 'UNAVAILABLE') return result(shadow, 'ASE UNAVAILABLE', 'muted', false);

  // Legacy v1 payloads had only an alignment. Show the observation, but never
  // promote it to the stricter v2 "supports" state.
  if (alignment === 'ALIGNED') return result(shadow, 'ASE ALIGNED', 'accent', false);
  if (alignment === 'OPPOSED') return result(shadow, 'ASE OPPOSED', 'warning', false);
  return result(shadow, 'ASE NEUTRAL', 'muted', false);
}
