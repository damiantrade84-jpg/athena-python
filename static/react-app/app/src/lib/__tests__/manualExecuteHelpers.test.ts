import { describe, expect, it } from 'vitest';
import {
  aiLevelOverrideFromReview,
  buildQuickExecutePayload,
  computeLevelOverrideRR,
  parseAiLevelString,
} from '../manualExecuteHelpers';
import type { AiTextReviewResponse, EngineASignal } from '@/types/athena';

describe('parseAiLevelString', () => {
  it('parses plain numeric strings', () => {
    expect(parseAiLevelString('0.05106112804373732')).toBeCloseTo(0.051061128);
    expect(parseAiLevelString('SL 52855.12')).toBeCloseTo(52855.12);
  });

  it('returns null for empty or invalid values', () => {
    expect(parseAiLevelString(null)).toBeNull();
    expect(parseAiLevelString('no price here')).toBeNull();
  });
});

describe('aiLevelOverrideFromReview', () => {
  it('returns override for adjust/reject when suggested levels parse', () => {
    const review = {
      levelsVerdict: 'adjust',
      suggestedSL: '52114',
      suggestedTP: '54100',
    } as AiTextReviewResponse;

    expect(aiLevelOverrideFromReview(review)).toEqual({
      sl: 52114,
      tp1: 54100,
      tp2: 54100,
      source: 'marcus_ai',
    });
  });

  it('returns override for accept using invalidation and fallback TP', () => {
    const review = {
      levelsVerdict: 'accept',
      invalidation: '0.05106112804373732',
    } as AiTextReviewResponse;

    expect(aiLevelOverrideFromReview(review, { fallbackTp1: 0.0477 })).toEqual({
      sl: 0.05106112804373732,
      tp1: 0.0477,
      tp2: 0.0477,
      source: 'marcus_ai',
    });
  });

  it('returns null when adjust/reject lacks parseable suggested levels', () => {
    const review = {
      levelsVerdict: 'reject',
      suggestedSL: 'below zone',
      suggestedTP: '54100',
    } as AiTextReviewResponse;

    expect(aiLevelOverrideFromReview(review)).toBeNull();
  });
});

describe('buildQuickExecutePayload level_override', () => {
  const signal = {
    pair: 'SEI/USDT',
    display: 'SEI/USDT',
    symbol: 'SEIUSDT',
    type: 'crypto',
    direction: 'SHORT',
    price: 0.04984,
    entry: 0.04984,
    sl: 0.051,
    tp1: 0.0477,
    style: 'intraday',
  } as EngineASignal;

  it('includes level_override when provided for non-v3 signals', () => {
    const payload = buildQuickExecutePayload({
      signal,
      pipMode: 'intraday',
      levelOverride: { sl: 0.05106, tp1: 0.0477, source: 'marcus_ai' },
    });

    expect(payload.level_override).toEqual({
      sl: 0.05106,
      tp1: 0.0477,
      tp2: 0.0477,
      source: 'marcus_ai',
    });
  });
});

describe('computeLevelOverrideRR', () => {
  it('computes reward/risk for short setup', () => {
    expect(computeLevelOverrideRR(0.04984, 0.05106, 0.0477)).toBeCloseTo(1.75, 1);
  });
});
