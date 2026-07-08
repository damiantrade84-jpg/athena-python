import { describe, expect, it } from 'vitest';
import {
  confluencePct,
  engineAListScore,
  engineAScoreBreakdown,
  engineAThreshold,
  engineBScoreBreakdown,
} from '../athenaFormat';
import type { EngineASignal } from '@/types/athena';

describe('engineAScoreBreakdown', () => {
  it('derives decision score before intermarket blend for V3 payloads', () => {
    const sig = {
      engine: 'ENGINE_A_V3',
      contractVersion: '3.1.0',
      confluenceScore: 2.2661,
      confluenceThreshold: 2.2,
      maxScore: 3,
      intermarketEngineADelta: 0.18,
    } as EngineASignal;

    const breakdown = engineAScoreBreakdown(sig);
    expect(breakdown?.decisionScore).toBeCloseTo(2.0861, 4);
    expect(breakdown?.displayScore).toBeCloseTo(2.2661, 4);
    expect(breakdown?.decisionPasses).toBe(false);
    expect(breakdown?.displayPasses).toBe(true);
    expect(breakdown?.hasAdjustments).toBe(true);
  });

  it('uses pre_news_score minus intermarket for decision score when present', () => {
    const sig = {
      confluenceScore: 2.4,
      confluenceThreshold: 2.2,
      pre_news_score: 2.3,
      intermarketEngineADelta: 0.1,
      news_adjustment: 0.1,
    } as EngineASignal;

    const breakdown = engineAScoreBreakdown(sig);
    expect(breakdown?.decisionScore).toBeCloseTo(2.2, 4);
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
  it('anchors V3 bar to decision score not adjusted display score', () => {
    const sig = {
      engine: 'ENGINE_A_V3',
      contractVersion: '3.1.0',
      confluenceScore: 2.2661,
      confluenceThreshold: 2.2,
      maxScore: 3,
      intermarketEngineADelta: 0.18,
      confluencePct: 69,
    } as EngineASignal;

    expect(confluencePct(sig)).toBe(Math.round((2.0861 / 2.2) * 67));
  });
});

describe('engineBScoreBreakdown', () => {
  it('separates gate score from total score for pass floor', () => {
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
    expect(breakdown?.gatePasses).toBe(false);
    expect(breakdown?.totalPasses).toBe(true);
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
    expect(engineAListScore(sig)).toBeCloseTo(2.0861, 4);
  });
});
