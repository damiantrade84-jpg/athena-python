import { describe, expect, it } from 'vitest';

import { normalizeBacktestDirection, resolveDirectionBreakdown } from '../backtestDirection';

describe('resolveDirectionBreakdown', () => {
  it('normalizes server-provided Engine A direction counts', () => {
    expect(resolveDirectionBreakdown({ long: 35, SHORT: '26' }, [])).toEqual({
      LONG: 35,
      SHORT: 26,
      UNKNOWN: 0,
    });
  });

  it('falls back to counting the full trade list', () => {
    expect(resolveDirectionBreakdown(undefined, [
      { direction: 'LONG' },
      { direction: 'SHORT' },
      { direction: '' },
    ])).toEqual({
      LONG: 1,
      SHORT: 1,
      UNKNOWN: 1,
    });
  });

  it('normalizes buy and sell aliases used by some backtest rows', () => {
    expect(normalizeBacktestDirection('buy')).toBe('LONG');
    expect(normalizeBacktestDirection('SELL')).toBe('SHORT');
    expect(normalizeBacktestDirection(null)).toBe('UNKNOWN');
  });
});
