import { describe, expect, it } from 'vitest';
import {
  confluencePct,
  confluenceThresholdPct,
  engineAListScore,
  engineAScoreBreakdown,
  engineAThreshold,
  engineBScoreBreakdown,
  signalConviction,
  unifiedListSortKey,
} from '../athenaFormat';
import type { EngineASignal } from '@/types/athena';

describe('engineAScoreBreakdown', () => {
  it('uses backend-canonical post-intermarket score for V3 decisions', () => {
    const sig = {
      engine: 'ENGINE_A_V3',
      contractVersion: '3.1.0',
      confluenceScore: 2.2661,
      confluenceThreshold: 2.2,
      maxScore: 3,
      intermarketEngineADelta: 0.18,
    } as EngineASignal;

    const breakdown = engineAScoreBreakdown(sig);
    expect(breakdown?.decisionScore).toBeCloseTo(2.2661, 4);
    expect(breakdown?.displayScore).toBeCloseTo(2.2661, 4);
    expect(breakdown?.decisionPasses).toBe(true);
    expect(breakdown?.displayPasses).toBe(true);
    expect(breakdown?.hasAdjustments).toBe(true);
  });

  it('keeps news display adjustment separate from canonical V3 decision score', () => {
    const sig = {
      engine: 'ENGINE_A_V3',
      contractVersion: '3.1.0',
      confluenceScore: 2.3,
      score: 2.4,
      confluenceThreshold: 2.2,
      pre_news_score: 2.3,
      intermarketEngineADelta: 0.1,
      news_adjustment: 0.1,
    } as EngineASignal;

    const breakdown = engineAScoreBreakdown(sig);
    expect(breakdown?.decisionScore).toBeCloseTo(2.3, 4);
    expect(breakdown?.displayScore).toBeCloseTo(2.4, 4);
  });

  it('legacy rows use post-blend display score for pass checks', () => {
    const sig = {
      confluenceScore: 2.4,
      confluenceThreshold: 2.2,
      pre_news_score: 2.3,
      intermarketEngineADelta: 0.1,
      news_adjustment: 0.1,
    } as EngineASignal;

    const breakdown = engineAScoreBreakdown(sig);
    expect(breakdown?.displayPasses).toBe(true);
    expect(breakdown?.decisionPasses).toBe(true);
  });
});

describe('engineAThreshold', () => {
  it('prefers confluenceThreshold for V3 payloads', () => {
    const sig = {
      confluenceThreshold: 2.2,
      scanThreshold: 0,
    } as EngineASignal;
    expect(engineAThreshold(sig)).toBe(2.2);
  });
});

describe('confluencePct', () => {
  it('anchors V3 bar to backend-canonical decision score', () => {
    const sig = {
      engine: 'ENGINE_A_V3',
      contractVersion: '3.1.0',
      confluenceScore: 2.2661,
      confluenceThreshold: 2.2,
      maxScore: 3,
      intermarketEngineADelta: 0.18,
      confluencePct: 69,
    } as EngineASignal;

    expect(confluencePct(sig)).toBe(Math.round((2.2661 / 3) * 100));
  });

  it('is comparable across score groups with different thresholds', () => {
    // Threshold-anchored percentages made a 2.24/3.00 index signal (threshold
    // 1.5) read 100% while a perfect 3.00/3.00 forex signal (threshold 2.1)
    // read 96%. Both must now report their true share of the max.
    const indexSig = {
      confluenceScore: 2.24,
      confluenceThreshold: 1.5,
      maxScore: 3,
    } as EngineASignal;
    const forexSig = {
      confluenceScore: 3.0,
      confluenceThreshold: 2.1,
      maxScore: 3,
    } as EngineASignal;

    expect(confluencePct(indexSig)).toBe(75);
    expect(confluencePct(forexSig)).toBe(100);
    expect(confluenceThresholdPct(indexSig)).toBe(50);
    expect(confluenceThresholdPct(forexSig)).toBe(70);
  });
});

describe('engineBScoreBreakdown', () => {
  it('prefers penalty-adjusted net quality over gross quality', () => {
    const breakdown = engineBScoreBreakdown({
      confidence: {
        score: 5.4,
        max_possible: 6,
        quality_pct: 80,
        quality_pct_net: 40,
      },
    });

    expect(breakdown?.qualityPct).toBe(40);
  });

  it('applies the score floor to total score and keeps final confidence separate', () => {
    const breakdown = engineBScoreBreakdown({
      confidence: {
        passed: false,
        gate_score: 4.0,
        gate_max_possible: 5,
        score: 5.5,
        max_possible: 6,
        min_score_scaled: 4.5,
        bonus_points: 1.5,
      },
    });

    expect(breakdown?.gateScore).toBe(4);
    expect(breakdown?.totalScore).toBe(5.5);
    expect(breakdown?.scoreFloorPasses).toBe(true);
    expect(breakdown?.confidencePasses).toBe(false);
  });

  it('reads Engine B-only scanner score fields and final confidence status', () => {
    const breakdown = engineBScoreBreakdown({
      engine_source: 'ENGINE_B',
      engine_b_score: 5.25,
      engine_b_max: 6,
      engine_b_gate_score: 4,
      engine_b_gate_max: 4,
      engine_b_min_score_scaled: 4.5,
      engine_b_status: { passed: true },
    });

    expect(breakdown?.totalScore).toBe(5.25);
    expect(breakdown?.totalMax).toBe(6);
    expect(breakdown?.scoreFloorPasses).toBe(true);
    expect(breakdown?.confidencePasses).toBe(true);
  });
});

describe('Engine B conviction consumers', () => {
  const row = {
    engine_source: 'ENGINE_B',
    engine: 'B',
    combinedConviction: 0,
    engine_b_conviction: 0.4,
    engine_b_quality_pct_net: 40,
    engine_b_quality_pct: 80,
  } as unknown as EngineASignal;

  it('sorts on the same net conviction used by execution', () => {
    expect(unifiedListSortKey(row)).toBeCloseTo(0.4, 6);
  });

  it('does not let the B-only combined placeholder hide net conviction', () => {
    expect(signalConviction(row)).toBeCloseTo(0.4, 6);
  });
});

describe('engineAListScore', () => {
  it('uses decision score for V3 list sort', () => {
    const sig = {
      engine: 'ENGINE_A_V3',
      contractVersion: '3.1.0',
      confluenceScore: 2.2661,
      intermarketEngineADelta: 0.18,
    } as EngineASignal;
    expect(engineAListScore(sig)).toBeCloseTo(2.2661, 4);
  });
});
