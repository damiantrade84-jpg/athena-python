// Backtesting is Engine A + Engine B only (v3 rebuild). The former C, D
// (scalp), and ASE backtest endpoints are retired and return 410 GONE.
export type BacktestEngineKey = 'A' | 'B';

export interface BacktestRequestInput {
  engine: BacktestEngineKey;
  pair: string;
  style: string;
}

export interface BacktestRequest {
  endpoint: string;
  payload: Record<string, unknown>;
}

export function backtestEndpointForEngine(engine: BacktestEngineKey): string {
  return engine === 'B' ? '/api/backtest-naked' : '/api/backtest';
}

export function buildBacktestRequest(input: BacktestRequestInput): BacktestRequest {
  return {
    endpoint: backtestEndpointForEngine(input.engine),
    payload: {
      pair: input.pair,
      style: input.style,
    },
  };
}
