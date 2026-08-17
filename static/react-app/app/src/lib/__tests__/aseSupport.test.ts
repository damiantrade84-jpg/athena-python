import { describe, expect, it } from 'vitest';
import { resolveASESupportDisplay } from '../aseSupport';
import type { ASEShadowContext } from '@/types/athena';

const supported: ASEShadowContext = {
  schemaVersion: 'ase_engine_b_shadow.v2',
  direction: 'LONG',
  candidateDirection: 'LONG',
  decisionStatus: 'TRADE',
  probabilityPositive: 0.61,
  expectedNetR: 0.14,
  alignment: 'ALIGNED',
  supportState: 'SUPPORTED',
  supportEligible: true,
  reason: 'ase_trade_aligned_positive',
  horizon: 'intraday',
  dataQualityOk: true,
  modelHealthOk: true,
  advisoryOnly: true,
  affectsEngineBScore: false,
};

describe('resolveASESupportDisplay', () => {
  it('shows support only for the complete positive advisory contract', () => {
    const display = resolveASESupportDisplay(supported);

    expect(display?.label).toBe('ASE SUPPORTS');
    expect(display?.supported).toBe(true);
    expect(display?.detail).toContain('E[net R] +0.140');
    expect(display?.detail).toContain('P+ 61%');
    expect(display?.title).toContain('no Engine B score, gate, eligibility, level, or execution change');
  });

  it('fails closed in the UI when a supported payload is contradictory', () => {
    const display = resolveASESupportDisplay({
      ...supported,
      affectsEngineBScore: true,
    });

    expect(display?.label).toBe('ASE UNAVAILABLE');
    expect(display?.supported).toBe(false);
  });

  it('keeps aligned WATCH as advisory rather than support', () => {
    const display = resolveASESupportDisplay({
      ...supported,
      decisionStatus: 'WATCH',
      supportState: 'WATCH_ALIGNED',
      supportEligible: false,
      reason: 'ase_watch_only',
    });

    expect(display?.label).toBe('ASE WATCH ALIGNED');
    expect(display?.supported).toBe(false);
    expect(display?.reason).toContain('WATCH only');
  });

  it('renders FLAT and stale states without a positive badge', () => {
    const neutral = resolveASESupportDisplay({
      supportState: 'NEUTRAL',
      alignment: 'ASE_FLAT',
      decisionStatus: 'FLAT',
      reason: 'ase_decision_flat',
    });
    const stale = resolveASESupportDisplay({
      supportState: 'UNAVAILABLE',
      alignment: 'ASE_FLAT',
      reason: 'ase_signal_stale',
    });

    expect(neutral?.label).toBe('ASE NEUTRAL');
    expect(neutral?.supported).toBe(false);
    expect(stale?.label).toBe('ASE UNAVAILABLE');
    expect(stale?.reason).toContain('stale');
  });

  it('does not promote a legacy aligned payload to v2 support', () => {
    const display = resolveASESupportDisplay({
      direction: 'LONG',
      alignment: 'ALIGNED',
      expectedNetR: 0.2,
      probabilityPositive: 0.65,
    });

    expect(display?.label).toBe('ASE ALIGNED');
    expect(display?.supported).toBe(false);
  });
});
