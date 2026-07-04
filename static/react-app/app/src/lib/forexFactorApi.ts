import { safeJson } from './safeJson';

const API_BASE = import.meta.env.VITE_API_BASE || '';
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const FX_SYMBOL = /^[A-Z]{6}$/;

export const G8_PAIRS: readonly string[] = [
  'EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'USDJPY', 'USDCHF', 'USDCAD',
  'EURGBP', 'EURJPY', 'EURCHF', 'EURCAD', 'EURAUD', 'EURNZD',
  'GBPJPY', 'GBPCHF', 'GBPCAD', 'GBPAUD', 'GBPNZD',
  'AUDJPY', 'AUDCHF', 'AUDCAD', 'AUDNZD',
  'NZDJPY', 'NZDCHF', 'NZDCAD',
  'CADJPY', 'CADCHF', 'CHFJPY',
];

export interface ForexDiagnostic {
  code?: string;
  message?: string;
  field?: string;
  severity?: string;
  [key: string]: unknown;
}

export interface ForexEnvelope<T> {
  success: boolean;
  data: T | null;
  status: string;
  diagnostics: ForexDiagnostic[];
  warnings: string[];
  generated_at: string;
}

export class ForexApiError extends Error {
  envelope?: Partial<ForexEnvelope<unknown>>;
  httpStatus?: number;

  constructor(message: string, envelope?: Partial<ForexEnvelope<unknown>>, httpStatus?: number) {
    super(message);
    this.name = 'ForexApiError';
    this.envelope = envelope;
    this.httpStatus = httpStatus;
  }
}

export function formatForexDiagnostics(diagnostics: ForexDiagnostic[] | undefined): string {
  if (!diagnostics?.length) return 'Request failed';
  return diagnostics
    .map((d) => {
      const code = d.code || d.field || 'error';
      const msg = d.message || JSON.stringify(d);
      return `${code}: ${msg}`;
    })
    .join('; ');
}

async function forexRequest<T>(url: string, options?: RequestInit): Promise<ForexEnvelope<T>> {
  const fullUrl = url.startsWith('http') ? url : `${API_BASE}${url}`;
  let resp: Response;
  try {
    resp = await fetch(fullUrl, options);
  } catch (err) {
    throw new ForexApiError(err instanceof Error ? err.message : 'Network error');
  }

  let body: ForexEnvelope<T>;
  try {
    body = (await safeJson(resp)) as ForexEnvelope<T>;
  } catch (err) {
    throw new ForexApiError(
      err instanceof Error ? err.message : 'Invalid JSON response',
      undefined,
      resp.status,
    );
  }

  if (!resp.ok || body.success === false) {
    throw new ForexApiError(
      formatForexDiagnostics(body.diagnostics) || body.status || `HTTP ${resp.status}`,
      body,
      resp.status,
    );
  }
  return body;
}

function buildQuery(params: Record<string, string | boolean | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, val] of Object.entries(params)) {
    if (val === undefined || val === '') continue;
    qs.set(key, val === true ? 'true' : val === false ? 'false' : String(val));
  }
  const s = qs.toString();
  return s ? `?${s}` : '';
}

// ── Response types (aligned with athena_app/api/routes_forex_factor.py) ─────

export interface FactorStatusData {
  latest_as_of_date?: string | null;
  decision_count?: number;
  action_counts?: Record<string, number>;
  swap_audit_valid_symbol_count?: number;
  data_freshness?: {
    swap_audit_latest_generated_at?: string | null;
    total_return_latest_date?: string | null;
    reer_latest_available_date?: string | null;
    decisions_latest_date?: string | null;
  };
}

export interface FactorScoreEntry {
  score?: number;
  direction?: string;
  points?: number;
  status?: string;
}

export interface FactorSignalRow {
  symbol?: string;
  direction?: string;
  action?: string;
  as_of_date?: string;
  family_scores?: Record<string, FactorScoreEntry>;
  factor_scores?: Record<string, FactorScoreEntry>;
  aligned_families?: string[];
  blocked_families?: string[];
  cot_overlay?: Record<string, unknown>;
  gate_summary?: {
    gates?: Record<string, {
      result?: string;
      reason_code?: string;
      recommended_action?: string;
      size_multiplier?: number;
    }>;
    volatility_action?: string;
    size_multiplier?: number;
  };
  gate_results?: Array<Record<string, unknown>>;
  total_score?: number;
  reason?: string;
  diagnostics?: ForexDiagnostic[];
}

export interface FactorSignalsData {
  as_of_date?: string | null;
  signals?: FactorSignalRow[];
}

export interface SwapAuditRow {
  symbol?: string;
  swap_long?: number;
  swap_short?: number;
  swap_mode?: number;
  annualized_swap_return_long?: number;
  annualized_swap_return_short?: number;
  theoretical_carry?: number;
  broker_markup_long?: number;
  broker_markup_short?: number;
  carry_long_eligible?: boolean;
  carry_short_eligible?: boolean;
  markup_warning?: boolean;
  conversion_warning?: boolean;
  stale_swap_warning?: boolean;
  status?: string;
  diagnostics?: ForexDiagnostic[];
}

export interface TotalReturnRow {
  date?: string;
  symbol?: string;
  spot_return?: number;
  long_swap_return?: number;
  short_swap_return?: number;
  long_total_return_index?: number;
  short_total_return_index?: number;
  data_quality_status?: string;
}

export interface TotalReturnData {
  symbol?: string;
  rows?: TotalReturnRow[];
  production_momentum_uses_total_return?: boolean;
}

export interface CotCurrencyRow {
  currency?: string;
  percentile?: number;
  net?: number;
  report_date?: string;
  n_weeks?: number;
  [key: string]: unknown;
}

export interface CotPositioningData {
  view?: 'currency' | 'pair';
  as_of_date?: string;
  symbol?: string;
  currencies?: CotCurrencyRow[];
  overlays?: Array<{ direction?: string; overlay?: Record<string, unknown> }>;
}

export interface ReerCurrencyRow {
  currency?: string;
  z_score?: number;
  z?: number;
  status_label?: string;
  observation_month?: string;
  available_date?: string;
  lag_method?: string;
  reer_value?: number;
  history_count?: number;
}

export interface ReerValueData {
  as_of_date?: string;
  currencies?: ReerCurrencyRow[];
}

export interface VolCostGateRow {
  symbol?: string;
  gate_results?: Array<{
    gate_name?: string;
    result?: string;
    reason_code?: string;
    numeric_inputs?: Record<string, unknown>;
    recommended_action?: string;
    size_multiplier?: number;
  }>;
  volatility_action?: string;
  cost_diagnostics?: Record<string, unknown>;
}

export interface VolCostGatesData {
  gate_only?: boolean;
  as_of_date?: string | null;
  symbols?: VolCostGateRow[];
}

export interface BacktestReport {
  equity_curve?: Array<{ date?: string; equity?: number; value?: number } | number>;
  trades?: Array<Record<string, unknown>>;
  summary?: Record<string, unknown>;
  factor_family_pnl?: Record<string, unknown>;
  pair_pnl?: Record<string, unknown>;
  pair_sanity_flags?: Record<string, unknown>;
  validation?: Record<string, unknown>;
  request?: Record<string, unknown>;
  diagnostics?: ForexDiagnostic[];
}

export interface BacktestRunData {
  run_id?: string;
  status?: string;
  request?: Record<string, unknown>;
  report?: BacktestReport;
}

export interface ValidationLatestData {
  run_id?: string;
  validation?: Record<string, unknown> | null;
  summary?: Record<string, unknown> | null;
  pair_sanity_flags?: Record<string, unknown> | null;
}

export interface DiagnosticsData {
  as_of_date?: string | null;
  diagnostics?: ForexDiagnostic[];
  parity?: Record<string, unknown>;
}

// ── API functions ───────────────────────────────────────────────────────────

export function getFactorStatus() {
  return forexRequest<FactorStatusData>('/api/forex/factor-status');
}

export function getFactorSignals(params: {
  symbol?: string;
  family?: string;
  direction?: string;
  eligible_only?: boolean;
  blocked_only?: boolean;
} = {}) {
  return forexRequest<FactorSignalsData>(
    `/api/forex/factor-signals${buildQuery(params)}`,
  );
}

export function getSwapAudit(symbol?: string) {
  return forexRequest<{ rows?: SwapAuditRow[] }>(
    `/api/forex/swap-audit${buildQuery({ symbol })}`,
  );
}

export function getTotalReturn(params: { symbol: string; start?: string; end?: string }) {
  return forexRequest<TotalReturnData>(
    `/api/forex/total-return${buildQuery(params)}`,
  );
}

export function getCotPositioning(params: { symbol?: string; currency?: string } = {}) {
  return forexRequest<CotPositioningData>(
    `/api/forex/cot-positioning${buildQuery(params)}`,
  );
}

export function getReerValue(asOf?: string) {
  return forexRequest<ReerValueData>(
    `/api/forex/reer-value${buildQuery({ as_of: asOf })}`,
  );
}

export function getVolCostGates() {
  return forexRequest<VolCostGatesData>('/api/forex/vol-cost-gates');
}

export function runForexBacktest(payload: Record<string, unknown>) {
  return forexRequest<{ run_id?: string; report?: BacktestReport }>(
    '/api/forex/backtest/run',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
}

export function getForexBacktest(runId: string) {
  return forexRequest<BacktestRunData>(`/api/forex/backtest/${encodeURIComponent(runId)}`);
}

export function getValidationLatest() {
  return forexRequest<ValidationLatestData>('/api/forex/validation/latest');
}

export function getForexDiagnostics() {
  return forexRequest<DiagnosticsData>('/api/forex/diagnostics');
}

export function postForexRefreshDecisions(payload: { symbols?: string[]; as_of?: string } = {}) {
  return forexRequest<Record<string, unknown>>('/api/forex/refresh-decisions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function postForexRebalance(payload: {
  executeTrades?: boolean;
  dryRun?: boolean;
  symbols?: string[];
  as_of?: string;
} = {}) {
  return forexRequest<Record<string, unknown>>('/api/forex/rebalance', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      executeTrades: payload.executeTrades ?? false,
      dryRun: payload.dryRun ?? false,
      symbols: payload.symbols,
      as_of: payload.as_of,
    }),
  });
}

// ── Backtest form validation (pure, testable) ───────────────────────────────

export interface ForexBacktestFormInput {
  start: string;
  end: string;
  symbols: string[];
  strictFactorData?: boolean;
  costModelEnabled?: boolean;
  slippagePips?: number | '';
  spreadPipsBySymbol?: Record<string, number>;
}

export interface ForexBacktestValidationResult {
  valid: boolean;
  errors: string[];
  payload?: Record<string, unknown>;
}

export function parseSymbolList(raw: string): string[] {
  return raw
    .split(/[\s,;]+/)
    .map((s) => s.replace('/', '').trim().toUpperCase())
    .filter(Boolean);
}

export function validateForexBacktestRequest(
  input: ForexBacktestFormInput,
): ForexBacktestValidationResult {
  const errors: string[] = [];
  const start = (input.start || '').trim();
  const end = (input.end || '').trim();

  if (!ISO_DATE.test(start)) errors.push('Start date is required (YYYY-MM-DD)');
  if (!ISO_DATE.test(end)) errors.push('End date is required (YYYY-MM-DD)');
  if (ISO_DATE.test(start) && ISO_DATE.test(end) && start > end) {
    errors.push('Start date must be on or before end date');
  }

  const symbols = (input.symbols || [])
    .map((s) => s.replace('/', '').trim().toUpperCase())
    .filter(Boolean);

  if (!symbols.length) errors.push('At least one symbol is required');
  for (const sym of symbols) {
    if (!FX_SYMBOL.test(sym)) errors.push(`Invalid FX symbol: ${sym}`);
  }

  if (errors.length) return { valid: false, errors };

  const payload: Record<string, unknown> = {
    start,
    end,
    symbols,
    strict_factor_data: input.strictFactorData !== false,
    cost_model_enabled: input.costModelEnabled !== false,
  };

  if (input.slippagePips !== '' && input.slippagePips != null && Number.isFinite(Number(input.slippagePips))) {
    payload.slippage_pips = Number(input.slippagePips);
  }
  if (input.spreadPipsBySymbol && Object.keys(input.spreadPipsBySymbol).length) {
    payload.spread_pips_by_symbol = input.spreadPipsBySymbol;
  }

  return { valid: true, errors: [], payload };
}

export function signalFamilyScores(row: FactorSignalRow): Record<string, FactorScoreEntry> {
  return row.family_scores || row.factor_scores || {};
}
