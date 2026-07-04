import { describe, expect, it } from 'vitest';

import { buildBacktestRequest } from '../backtestPayload';

describe('buildBacktestRequest', () => {
  it('routes ASE single-pair runs to the ASE endpoint with horizon and lookback', () => {
    const request = buildBacktestRequest({
      engine: 'ASE',
      pair: 'BTCUSDT',
      style: 'auto',
      validationMode: 'standard',
      aseHorizon: 'swing',
      aseLookbackDays: 90,
    });

    expect(request).toEqual({
      endpoint: '/api/backtest-ase',
      payload: {
        pair: 'BTCUSDT',
        validation_mode: 'standard',
        horizon: 'swing',
        lookbackDays: 90,
      },
    });
  });

  it('keeps Engine B on the naked endpoint with style payload', () => {
    const request = buildBacktestRequest({
      engine: 'B',
      pair: 'EURUSD',
      style: 'intraday',
      validationMode: 'walk_forward',
      aseHorizon: 'both',
      aseLookbackDays: 365,
    });

    expect(request).toEqual({
      endpoint: '/api/backtest-naked',
      payload: {
        pair: 'EURUSD',
        style: 'intraday',
        validation_mode: 'walk_forward',
      },
    });
  });
});
