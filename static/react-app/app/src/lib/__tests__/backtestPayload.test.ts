import { describe, expect, it } from 'vitest';

import { buildBacktestRequest } from '../backtestPayload';

describe('buildBacktestRequest', () => {
  it('routes Engine A to the primary backtest endpoint', () => {
    const request = buildBacktestRequest({
      engine: 'A',
      pair: 'EURUSD',
      style: 'intraday',
    });

    expect(request).toEqual({
      endpoint: '/api/v3/backtest',
      payload: {
        pair: 'EURUSD',
        style: 'intraday',
      },
    });
  });

  it('keeps Engine B on the naked endpoint with style payload', () => {
    const request = buildBacktestRequest({
      engine: 'B',
      pair: 'EURUSD',
      style: 'intraday',
    });

    expect(request).toEqual({
      endpoint: '/api/v3/backtest-naked',
      payload: {
        pair: 'EURUSD',
        style: 'intraday',
      },
    });
  });
});
