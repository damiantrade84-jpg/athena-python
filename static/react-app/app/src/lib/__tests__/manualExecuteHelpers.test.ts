import { describe, expect, it } from 'vitest';
import {
  aiLevelOverrideFromReview,
  buildQuickExecutePayload,
  canExecuteEngineASignalTier,
  canExecuteEngineBSignal,
  computeLevelOverrideRR,
  engineBExecuteBlockReason,
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

describe('engine B execute gating', () => {
  const bchLike = {
    pair: 'BCH/USDT',
    display: 'BCH/USDT',
    type: 'crypto',
    direction: 'LONG',
    is_naked: true,
    executable: true,
    price: 237.2,
    sl: 228.42,
    tp1: 241.02,
    confidence_passed: true,
    canonical_trade_ok: false,
    engine_b_canonical_status: 'REJECT_LEARNING_CONTEXT',
    naked_data: {
      passed: true,
      confidence: { passed: true, score: 130, min_score_scaled: 129 },
      canonical_trade_ok: false,
      engine_b_canonical_actionable: false,
      engine_b_canonical_status: 'REJECT_LEARNING_CONTEXT',
      execution_sl: 228.42,
      execution_tp1: 241.02,
      current_price: 237.2,
    },
  } as EngineASignal;

  it('blocks execute when score passes but canonical trade is false', () => {
    expect(canExecuteEngineBSignal(bchLike)).toBe(false);
    expect(engineBExecuteBlockReason(bchLike)).toBe('NO ENTRY · REJECT_LEARNING_CONTEXT');
  });

  it('allows execute when canonical trade is true and levels are present', () => {
    const actionable = {
      ...bchLike,
      canonical_trade_ok: true,
      engine_b_canonical_status: 'ACTIONABLE',
      naked_data: {
        ...bchLike.naked_data,
        canonical_trade_ok: true,
        engine_b_canonical_actionable: true,
        engine_b_canonical_status: 'ACTIONABLE',
      },
    } as EngineASignal;
    expect(canExecuteEngineBSignal(actionable)).toBe(true);
    expect(engineBExecuteBlockReason(actionable)).toBeNull();
  });
});

describe('Engine A V3 scanner tier gating', () => {
  it('blocks raw V3 TRADE fields when the scanner tier is watchlist', () => {
    const signal = {
      engine: 'ENGINE_A_V3',
      contractVersion: '3.1.0',
      decision: 'TRADE',
      qualified: true,
      engineATradeEnabled: true,
      signalTier: 'watchlist',
    } as EngineASignal;

    expect(canExecuteEngineASignalTier(signal)).toBe(false);
  });

  it('routes a V3 chart review to the signal entry timeframe', async () => {
    const panel = await import('../../components/panels/SignalsPanel') as {
      preferredTvChartTf?: (signal: EngineASignal) => string;
    };
    const signal = {
      engine: 'ENGINE_A_V3',
      contractVersion: '3.1.0',
      entryTimeframe: 'H4',
      style: 'intraday',
    } as EngineASignal;

    expect(typeof panel.preferredTvChartTf).toBe('function');
    expect(panel.preferredTvChartTf?.(signal)).toBe('H4');
  });
});
