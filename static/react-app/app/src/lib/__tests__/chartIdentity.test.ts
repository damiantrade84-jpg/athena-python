import { describe, expect, it } from 'vitest';
import { resolveChartIntentSymbol } from '../chartIdentity';

describe('resolveChartIntentSymbol', () => {
  it('uses the spot display identity for a continuous-futures commodity proxy', () => {
    expect(resolveChartIntentSymbol({ symbol: 'GC=F', display: 'XAU/USD' })).toBe('XAU/USD');
  });

  it('retains ordinary chart symbols and manual futures input without a display alias', () => {
    expect(resolveChartIntentSymbol({ symbol: 'EUR/USD', display: 'EUR/USD' })).toBe('EUR/USD');
    expect(resolveChartIntentSymbol({ symbol: 'GC=F' })).toBe('GC=F');
  });
});
