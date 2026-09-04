import { describe, expect, it } from 'vitest';

import {
  museDecisionClass,
  musePhaseIndex,
  musePrismLabel,
  museScoreText,
  museSetupLabel,
  museSignalMatchesQuery,
  museWeakestPrism,
  type MuseSignal,
} from '../museEngine';
import { snapshotFromMuseSignals } from '../engineScanCompile';

function signal(overrides: Partial<MuseSignal> = {}): MuseSignal {
  return {
    signalId: 'muse_abc',
    contractVersion: 'muse.v1',
    engine: 'MUSE',
    pair: 'EUR/USD',
    symbol: 'EURUSD',
    assetType: 'forex',
    venue: 'mt5',
    direction: 'LONG',
    setup: 'HAVEN_TAP',
    phase: 'RELEASE',
    decision: 'PRIME',
    decisionReason: 'ok',
    score: 80.2,
    maxScore: 100,
    primeThreshold: 74,
    stageThreshold: 58,
    conviction: 0.77,
    timingFactor: 1.0,
    haloModifier: 1.03,
    tide: { window: 'meridian_surge', kind: 'surge', quality: 1.0 },
    halo: {},
    spark: {},
    entry: 1.1,
    stop: 1.09,
    target: 1.12,
    rr: 2.0,
    atr: 0.001,
    prisms: [
      { name: 'echo', quality: 0.6, evidence: {} },
      { name: 'surge', quality: 0.86, evidence: {} },
      { name: 'haven', quality: 0.86, evidence: {} },
      { name: 'compass', quality: 0.85, evidence: {} },
    ],
    gates: [],
    blockingReasons: [],
    generatedAt: new Date().toISOString(),
    barClosedAt: new Date().toISOString(),
    timeframes: { atlas: 'D1', current: 'H4', vector: 'M15', spark: 'M5' },
    dataProvenance: {},
    ...overrides,
  };
}

describe('museEngine helpers', () => {
  it('labels prisms and setups without copying grok/fable vocabulary', () => {
    expect(musePrismLabel('echo')).toBe('Undertow Echo');
    expect(museSetupLabel('HAVEN_TAP')).toBe('Haven tap');
    expect(museScoreText(80.21, 100)).toBe('80.2 / 100');
    expect(museDecisionClass('PRIME')).toContain('cyan');
    expect(musePhaseIndex('RELEASE')).toBe(4);
  });

  it('finds the harmonic anchor (weakest prism)', () => {
    expect(museWeakestPrism(signal())?.name).toBe('echo');
  });

  it('matches queries across pair, setup and decision', () => {
    expect(museSignalMatchesQuery(signal(), 'haven')).toBe(true);
    expect(museSignalMatchesQuery(signal(), 'prime')).toBe(true);
    expect(museSignalMatchesQuery(signal(), 'gbp')).toBe(false);
  });

  it('compiles PRIME/STAGE/DORMANT/BLOCKED into board stances', () => {
    const snap = snapshotFromMuseSignals(
      [signal(), signal({ signalId: 'm2', decision: 'STAGE' }), signal({ signalId: 'm3', decision: 'BLOCKED' })],
      Date.now(),
    );
    expect(snap.engine).toBe('muse');
    expect(snap.rows.map((r) => r.stance)).toEqual(['pass', 'watch', 'fail']);
  });
});
