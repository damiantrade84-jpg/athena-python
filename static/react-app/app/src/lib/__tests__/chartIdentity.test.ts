import { describe, expect, it } from 'vitest';
import { compactChartSymbolKey, resolveChartIntentSymbol } from '../chartIdentity';

describe('resolveChartIntentSymbol', () => {
  it('uses the spot display identity for a continuous-futures commodity proxy', () => {
    expect(resolveChartIntentSymbol({ symbol: 'GC=F', display: 'XAU/USD' })).toBe('XAU/USD');
  });

  it('uses the Athena display identity for a Yahoo index ticker', () => {
    expect(resolveChartIntentSymbol({ symbol: '^AXJO', display: 'ASX 200' })).toBe('ASX 200');
    expect(resolveChartIntentSymbol({ symbol: '^AXJO', display: '^AXJO' })).toBe('ASX 200');
    expect(resolveChartIntentSymbol({ symbol: '^AXJO' })).toBe('ASX 200');
  });

  it('retains ordinary chart symbols and unmapped futures input without a display alias', () => {
    expect(resolveChartIntentSymbol({ symbol: 'EUR/USD', display: 'EUR/USD' })).toBe('EUR/USD');
    expect(resolveChartIntentSymbol({ symbol: 'UNK=F' })).toBe('UNK=F');
  });
});

describe('compactChartSymbolKey', () => {
  it('treats ASX 200 Yahoo, display, and broker aliases as one instrument', () => {
    expect(compactChartSymbolKey('^AXJO')).toBe('ASX200');
    expect(compactChartSymbolKey('ASX 200')).toBe('ASX200');
    expect(compactChartSymbolKey('ASX200')).toBe('ASX200');
    expect(compactChartSymbolKey('AUS200')).toBe('ASX200');
    expect(compactChartSymbolKey('AUS200.s')).toBe('ASX200');
  });

  it('leaves ordinary forex compact keys unchanged', () => {
    expect(compactChartSymbolKey('EUR/USD')).toBe('EURUSD');
    expect(compactChartSymbolKey('EURUSD=X')).toBe('EURUSD');
  });
});
