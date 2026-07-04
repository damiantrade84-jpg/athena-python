export type BacktestEngineKey = 'A' | 'B' | 'C' | 'D' | 'ASE';
export type ASEBacktestHorizon = 'both' | 'intraday' | 'swing';

export interface BacktestRequestInput {
  engine: BacktestEngineKey;
  pair: string;
  style: string;
  validationMode: string;
  aseHorizon: ASEBacktestHorizon;
  aseLookbackDays: number;
}

export interface BacktestRequest {
  endpoint: string;
  payload: Record<string, unknown>;
}

export function backtestEndpointForEngine(engine: BacktestEngineKey): string {
  if (engine === 'B') return '/api/backtest-naked';
  if (engine === 'C') return '/api/backtest-consensus';
  if (engine === 'D') return '/api/backtest-scalp';
  if (engine === 'ASE') return '/api/backtest-ase';
  return '/api/backtest';
}

export function buildBacktestRequest(input: BacktestRequestInput): BacktestRequest {
  const endpoint = backtestEndpointForEngine(input.engine);
  const payload: Record<string, unknown> = {
    pair: input.pair,
    validation_mode: input.validationMode,
  };

  if (input.engine === 'ASE') {
    payload.horizon = input.aseHorizon;
    if (Number.isFinite(input.aseLookbackDays) && input.aseLookbackDays > 0) {
      payload.lookbackDays = Math.trunc(input.aseLookbackDays);
    }
    return { endpoint, payload };
  }

  payload.style = input.style;
  return { endpoint, payload };
}
