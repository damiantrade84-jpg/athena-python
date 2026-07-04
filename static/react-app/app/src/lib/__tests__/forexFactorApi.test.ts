import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
  formatForexDiagnostics,
  ForexApiError,
  getFactorStatus,
  getForexTradingStatus,
  getLatestForexScan,
  postForexExecuteCandidate,
  postForexScan,
  parseSymbolList,
  validateForexBacktestRequest,
  G8_PAIRS,
} from '../forexFactorApi';

describe('forexFactorApi', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('getFactorStatus returns success envelope', async () => {
    const envelope = {
      success: true,
      data: { latest_as_of_date: '2026-01-15', action_counts: { BUY: 1 } },
      status: 'ok',
      diagnostics: [],
      warnings: [],
      generated_at: '2026-01-15T12:00:00+00:00',
    };
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => envelope,
    } as Response);

    const result = await getFactorStatus();
    expect(result.success).toBe(true);
    expect(result.data?.latest_as_of_date).toBe('2026-01-15');
    expect(fetch).toHaveBeenCalledWith('/api/forex/factor-status', undefined);
  });

  it('throws ForexApiError on error envelope', async () => {
    const envelope = {
      success: false,
      data: null,
      status: 'invalid_request',
      diagnostics: [{ field: 'symbol', message: 'required' }],
      warnings: [],
      generated_at: '2026-01-15T12:00:00+00:00',
    };
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => envelope,
    } as Response);

    await expect(getFactorStatus()).rejects.toBeInstanceOf(ForexApiError);
    await expect(getFactorStatus()).rejects.toThrow('symbol: required');
  });

  it('throws ForexApiError on network failure', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('Failed to fetch'));
    await expect(getFactorStatus()).rejects.toThrow('Failed to fetch');
  });

  it('formatForexDiagnostics joins diagnostic messages', () => {
    expect(formatForexDiagnostics([{ code: 'missing_carry', message: 'stale' }])).toBe(
      'missing_carry: stale',
    );
    expect(formatForexDiagnostics([])).toBe('Request failed');
  });

  it('postForexScan calls scan endpoint with scanner options', async () => {
    const envelope = {
      success: true,
      data: { scan_id: 'scan-1', candidates: [] },
      status: 'ok',
      diagnostics: [],
      warnings: [],
      generated_at: '2026-01-15T12:00:00+00:00',
    };
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => envelope } as Response);

    await postForexScan({ symbols: ['EURUSD'], refreshDecisions: false, includeBlocked: true });

    expect(fetch).toHaveBeenCalledWith('/api/forex/scan', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ symbols: ['EURUSD'], refreshDecisions: false, includeBlocked: true }),
    }));
  });

  it('getLatestForexScan and trading status call read endpoints', async () => {
    const envelope = {
      success: true,
      data: {},
      status: 'ok',
      diagnostics: [],
      warnings: [],
      generated_at: '2026-01-15T12:00:00+00:00',
    };
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => envelope } as Response);

    await getLatestForexScan();
    await getForexTradingStatus();

    expect(fetch).toHaveBeenNthCalledWith(1, '/api/forex/scan/latest', undefined);
    expect(fetch).toHaveBeenNthCalledWith(2, '/api/forex/trading-status', undefined);
  });

  it('postForexExecuteCandidate defaults to dry run', async () => {
    const envelope = {
      success: true,
      data: { dry_run: true, reason: 'dry_run' },
      status: 'ok',
      diagnostics: [],
      warnings: [],
      generated_at: '2026-01-15T12:00:00+00:00',
    };
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => envelope } as Response);
    const candidate = { symbol: 'EURUSD', action: 'BUY' };

    await postForexExecuteCandidate({ candidate });

    expect(fetch).toHaveBeenCalledWith('/api/forex/execute-candidate', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ candidate, dryRun: true, confirm: true }),
    }));
  });
});

describe('validateForexBacktestRequest', () => {
  it('accepts valid G8 backtest payload', () => {
    const result = validateForexBacktestRequest({
      start: '2020-01-01',
      end: '2024-12-31',
      symbols: ['EURUSD', 'GBPUSD'],
      strictFactorData: true,
      costModelEnabled: true,
      slippagePips: 0.5,
    });
    expect(result.valid).toBe(true);
    expect(result.payload).toMatchObject({
      start: '2020-01-01',
      end: '2024-12-31',
      symbols: ['EURUSD', 'GBPUSD'],
      strict_factor_data: true,
      cost_model_enabled: true,
      slippage_pips: 0.5,
    });
  });

  it('rejects missing dates and empty symbols', () => {
    const result = validateForexBacktestRequest({
      start: '',
      end: 'bad',
      symbols: [],
    });
    expect(result.valid).toBe(false);
    expect(result.errors.length).toBeGreaterThanOrEqual(3);
  });

  it('rejects invalid symbol and inverted date range', () => {
    const result = validateForexBacktestRequest({
      start: '2025-01-01',
      end: '2024-01-01',
      symbols: ['EURO'],
    });
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes('Invalid FX symbol'))).toBe(true);
    expect(result.errors.some((e) => e.includes('Start date'))).toBe(true);
  });

  it('defaults strict_factor_data to true when omitted', () => {
    const result = validateForexBacktestRequest({
      start: '2020-01-01',
      end: '2024-12-31',
      symbols: ['EURUSD'],
    });
    expect(result.payload?.strict_factor_data).toBe(true);
    expect(result.payload?.cost_model_enabled).toBe(true);
  });
});

describe('parseSymbolList', () => {
  it('parses comma and whitespace separated symbols', () => {
    expect(parseSymbolList('EURUSD, gbpusd ; USDJPY')).toEqual(['EURUSD', 'GBPUSD', 'USDJPY']);
  });
});

describe('G8_PAIRS preset', () => {
  it('contains 28 G8 crosses', () => {
    expect(G8_PAIRS).toHaveLength(28);
    expect(G8_PAIRS).toContain('EURUSD');
  });
});
