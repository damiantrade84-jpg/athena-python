import { useCallback, useMemo, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { ErrorBanner, SqnBadge } from '@/components/shared';
import { FlaskConical, Play, AlertTriangle, Trophy, Layers } from 'lucide-react';
import { AreaChart, Area, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { fmtNum, toNum } from '@/lib/utils';
import type { PairsResponse, PairListEntry } from '@/types/athena';

/**
 * Canonical backtest result shape returned by ALL backtest endpoints
 * (Engine A, B, C, D). All keys are camelCase per backtest_runner.py.
 */
interface BacktestResult {
  pair?: string;
  symbol?: string;
  type?: string;
  totalTrades?: number;
  wins?: number;
  losses?: number;
  winRate?: number; // 0-100
  profitFactor?: number;
  totalR?: number;
  expectancy?: number;
  sqn?: number;
  sharpe?: number;
  sortino?: number;
  avgWin?: number;
  avgLoss?: number;
  rSkew?: number;
  maxDrawdownPct?: number;
  maxRecoveryBars?: number;
  mcDD?: number;
  mcDdP50?: number;
  mcDdP95?: number;
  scoreBands?: Record<string, unknown>;
  regimeStats?: Record<string, unknown>;
  funnel?: Record<string, unknown>;
  wfSplit?: {
    is_sqn?: number | null;
    oos_sqn?: number | null;
    lowSampleSqnWarning?: boolean;
    lowSampleSqnTradeFloor?: number;
    [k: string]: unknown;
  };
  confluenceAnalysis?: Record<string, unknown>;
  btStyle?: string;
  btStyleRequested?: string;
  engine?: string;
  pairMaxScore?: number;
  bhReturn?: number | null;
  evalThreshold?: number;
  equityCurve?: number[] | { idx?: number; equity: number; date?: string }[];
  trades?: Array<Record<string, unknown>>;
  scalp_analysis?: {
    absorption?: { count: number; wr: number | null; avg_r: number | null };
    cvd_shift?: { count: number; wr: number | null; avg_r: number | null };
    rejection?: { count: number; wr: number | null; avg_r: number | null };
    mean_reversion?: { count: number; wr: number | null; avg_r: number | null };
    trend?: { count: number; wr: number | null; avg_r: number | null };
    grade_A?: { count: number; wr: number | null };
    grade_B?: { count: number; wr: number | null };
    grade_C?: { count: number; wr: number | null };
    [k: string]: { count: number; wr: number | null; avg_r?: number | null } | undefined;
  };
  vol_quality_pct?: number;
  vol_quality_warning?: boolean;
  structure_tf?: string;
  context_tf?: string;
  execution_tf?: string;
  vp_lookback_bars?: number;
  same_bar_both_hit?: number;
  exitDiagnostics?: Record<string, unknown>;
  engineCFunnel?: Record<string, unknown>;
  notes?: string;
  error?: string;
  success?: boolean;
  researchMetrics?: {
    tradeCount?: number;
    psr?: { available?: boolean; value?: number | null; note?: string; sampleCount?: number };
    dsr?: {
      available?: boolean;
      value?: number | null;
      assumptions?: string[];
      psrNote?: string;
    };
    pbo?: { available?: boolean; note?: string; method?: string };
    bootstrapCI?: { available?: boolean; assumptions?: string[] };
    assumptions?: string[];
    runtimeHeavy?: string[];
    sampleMoments?: Record<string, unknown>;
  };
  metricsInterpretationNotes?: string[];
  researchValidation?: Record<string, unknown>;
  [k: string]: unknown;
}

interface BacktestHistoryRow {
  id?: number;
  run_date?: string;
  pair?: string;
  asset_type?: string;
  engine?: string;
  trades?: number;
  win_rate?: number;
  profit_factor?: number;
  expectancy?: number;
  sqn?: number;
  sharpe?: number;
  sortino?: number;
  is_score?: number;
  oos_score?: number;
  max_dd_pct?: number;
  bt_min?: number;
  atr_source?: string;
  notes?: string;
}

interface AdvisoryRecommendation {
  id?: string | number;
  rec_id?: string | number;
  pair?: string;
  title?: string;
  subtitle?: string;
  scope_key?: string;
  engine?: string;
  metric?: string;
  current?: number;
  recommended?: number;
  current_value?: number;
  proposed_value?: number;
  status?: string;
  reason?: string;
  reasons?: string[];
  evidence?: string;
  created_at?: string;
  [k: string]: unknown;
}

interface AdvisoryResponse {
  summary?: Record<string, unknown>;
  current_policies?: Record<string, unknown>;
  pending?: AdvisoryRecommendation[];
  approved?: AdvisoryRecommendation[];
  rejected?: AdvisoryRecommendation[];
  recommendations?: AdvisoryRecommendation[];
  [k: string]: unknown;
}

type EngineKey = 'A' | 'B' | 'C' | 'D';

const ENGINE_OPTIONS: { value: EngineKey; label: string; help: string }[] = [
  { value: 'A', label: 'Engine A', help: 'factor scoring + asset addon (forex/crypto/equity/commodity/index)' },
  { value: 'B', label: 'Engine B', help: 'naked structure (BOS / CHoCH / FVG / OB / sweep)' },
  { value: 'C', label: 'Engine C', help: 'A + B consensus' },
  { value: 'D', label: 'Engine D (Scalp VP)', help: 'Fabio Valentini VP + orderflow (M15 stable proxy)' },
];

const STYLE_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'scalp', label: 'Scalp' },
  { value: 'intraday', label: 'Intraday' },
  { value: 'swing', label: 'Swing' },
];

function flattenPairs(p: PairsResponse | null | undefined): PairListEntry[] {
  if (!p) return [];
  if (Array.isArray(p.pairs)) return p.pairs;

  // New /api/pairs format: { groups: { Forex: [{sym, label, enabled}, ...], ... } }
  const grouped = (p as Record<string, unknown>).groups;
  if (grouped && typeof grouped === 'object') {
    const out: PairListEntry[] = [];
    const typeMap: Record<string, string> = {
      Forex: 'forex',
      Crypto: 'crypto',
      'US Stocks': 'stock',
      Indices: 'index',
      Commodities: 'commodity',
      ETFs: 'etf',
    };
    for (const [label, items] of Object.entries(grouped)) {
      const type = typeMap[label] || label.toLowerCase();
      if (Array.isArray(items)) {
        for (const item of items) {
          if (item && typeof item === 'object') {
            out.push({
              symbol: String((item as Record<string, unknown>).sym || ''),
              display: String((item as Record<string, unknown>).label || (item as Record<string, unknown>).sym || ''),
              type,
              enabled: Boolean((item as Record<string, unknown>).enabled),
            });
          }
        }
      }
    }
    return out;
  }

  // Legacy bucketed shape
  const out: PairListEntry[] = [];
  const legacyGroups: { list?: string[]; type: string }[] = [
    { list: p.forex, type: 'forex' },
    { list: p.crypto, type: 'crypto' },
    { list: p.stocks, type: 'stock' },
    { list: p.indices, type: 'index' },
    { list: p.commodities, type: 'commodity' },
    { list: p.etf, type: 'etf' },
  ];
  for (const g of legacyGroups) for (const sym of g.list || []) out.push({ symbol: sym, display: sym, type: g.type });
  return out;
}

function resolvePairKey(token: string, allPairs: PairListEntry[]): string {
  const t = token.trim();
  if (!t) return t;
  const u = t.toUpperCase().replace(/\s+/g, '');
  const tCompact = u.replace(/\//g, '').replace(/-/g, '');
  for (const p of allPairs) {
    const disp = String(p.display || '').trim();
    const sym = String(p.symbol || '').trim();
    if (!disp && !sym) continue;
    const dNorm = disp.toUpperCase().replace(/\s+/g, '');
    const sNorm = sym.toUpperCase().replace(/\s+/g, '');
    const dCompact = dNorm.replace(/\//g, '').replace(/-/g, '');
    const sCompact = sNorm.replace(/\//g, '').replace(/-/g, '');
    if (u === dNorm || u === sNorm || tCompact === dCompact || tCompact === sCompact) {
      return sym || disp || t;
    }
  }
  return t;
}

export default function BacktestPanel() {
  const { showToast } = useStore();
  const [pair, setPair] = useState('EURUSD');
  const [batchMode, setBatchMode] = useState(false);
  const [batchList, setBatchList] = useState('');
  type BatchRow = { pairKey: string; ok: boolean; error?: string; result?: BacktestResult };
  const [batchRows, setBatchRows] = useState<BatchRow[]>([]);
  const [engine, setEngine] = useState<EngineKey>('A');
  const [style, setStyle] = useState('auto');
  const [validationMode, setValidationMode] = useState('standard');
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [activeTab, setActiveTab] = useState<'run' | 'history' | 'best' | 'advisory'>('run');

  const { data: pairsData } = useApiPoll<PairsResponse>('/api/pairs', 0);
  const allPairs = useMemo(() => flattenPairs(pairsData), [pairsData]);

  const { post: postBacktest, loading: running, error: btError } = useApiPost<BacktestResult>();
  const { data: history, loading: historyLoading, error: historyError, refresh: refreshHistory } =
    useApiPoll<BacktestHistoryRow[]>('/api/backtest-history', 0);
  const { data: bestData, loading: bestLoading, error: bestError } =
    useApiPoll<BacktestHistoryRow[]>('/api/backtest-best', 0);
  const { data: advisory, loading: advisoryLoading, error: advisoryError, refresh: refreshAdvisory } =
    useApiPoll<AdvisoryResponse>('/api/advisory-thresholds', 0);
  const { post: postApprove } = useApiPost<{ ok?: boolean; error?: string }>();

  const handleRun = useCallback(async () => {
    let endpoint = '/api/backtest';
    if (engine === 'B') endpoint = '/api/backtest-naked';
    else if (engine === 'C') endpoint = '/api/backtest-consensus';
    else if (engine === 'D') endpoint = '/api/backtest-scalp';

    const singlePayload = (pairKey: string): Record<string, unknown> => ({
      pair: pairKey,
      style,
      validation_mode: validationMode,
    });

    if (batchMode) {
      const tokens = batchList.split(/[,;\n\r]+/).map((x) => x.trim()).filter(Boolean);
      if (tokens.length === 0) {
        showToast('Batch list is empty.', 'info');
        return;
      }
      const rowsOut: BatchRow[] = [];
      for (const tok of tokens) {
        const pk = resolvePairKey(tok, allPairs);
        const payload = singlePayload(pk);
        const res = await postBacktest(endpoint, payload);
        if (!res || res.error) {
          rowsOut.push({ pairKey: pk, ok: false, error: String(res?.error || 'unknown'), result: res || undefined });
        } else {
          rowsOut.push({ pairKey: pk, ok: true, result: res });
        }
      }
      setBatchRows(rowsOut);
      setResult(null);
      const okN = rowsOut.filter((r) => r.ok).length;
      showToast(`Batch: ${okN}/${rowsOut.length} pairs OK`, okN === rowsOut.length ? 'success' : 'info');
      refreshHistory();
      return;
    }

    const res = await postBacktest(endpoint, singlePayload(pair));
    if (!res || res.error) {
      showToast(`Backtest failed: ${res?.error || 'unknown'}`, 'error');
      return;
    }
    setBatchRows([]);
    setResult(res);
    showToast(
      `Engine ${engine}: ${res.totalTrades ?? 0} trades · WR ${fmtNum(res.winRate, 1)}% · SQN ${fmtNum(res.sqn, 2)}`,
      'success',
    );
    refreshHistory();
  }, [
    pair, engine, style, validationMode, postBacktest, showToast, refreshHistory,
    batchMode, batchList, allPairs,
  ]);

  const handleApprove = useCallback(async (rec: AdvisoryRecommendation) => {
    const id = rec.rec_id ?? rec.id;
    if (id == null) return;
    const r = await postApprove(`/api/advisory-thresholds/${id}/approve`, {});
    if (!r) showToast('Approve failed: no response', 'error');
    else if (r.error) showToast(`Approve failed: ${r.error}`, 'error');
    else showToast('Threshold recommendation approved', 'success');
    refreshAdvisory();
  }, [postApprove, showToast, refreshAdvisory]);

  const handleReject = useCallback(async (rec: AdvisoryRecommendation) => {
    const id = rec.rec_id ?? rec.id;
    if (id == null) return;
    const r = await postApprove(`/api/advisory-thresholds/${id}/reject`, {});
    if (!r) showToast('Reject failed: no response', 'error');
    else if (r.error) showToast(`Reject failed: ${r.error}`, 'error');
    else showToast('Threshold recommendation rejected', 'info');
    refreshAdvisory();
  }, [postApprove, showToast, refreshAdvisory]);

  const insufficient = !!(result && result.totalTrades != null && result.totalTrades < 30 && !result.error);

  const equityChart = useMemo(() => {
    const ec = result?.equityCurve;
    if (!Array.isArray(ec) || ec.length === 0) return [] as { idx: number; equity: number }[];
    if (typeof ec[0] === 'number') return (ec as number[]).map((equity, idx) => ({ idx, equity }));
    return (ec as { idx?: number; equity: number; date?: string }[]).map((p, idx) => ({
      idx: typeof p.idx === 'number' ? p.idx : idx,
      equity: Number(p.equity) || 0,
    }));
  }, [result?.equityCurve]);

  return (
    <div className="space-y-5">
      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as typeof activeTab)}>
        <TabsList>
          <TabsTrigger value="run" className="text-xs">Run Backtest</TabsTrigger>
          <TabsTrigger value="history" className="text-xs">History</TabsTrigger>
          <TabsTrigger value="best" className="text-xs">Best Pairs</TabsTrigger>
          <TabsTrigger value="advisory" className="text-xs">Advisory Thresholds</TabsTrigger>
        </TabsList>

        <TabsContent value="run" className="mt-3 space-y-4">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <FlaskConical className="w-4 h-4 text-primary" />
                Backtest Config
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-2 flex-wrap w-full">
                {!batchMode ? (
                  <Select value={pair} onValueChange={setPair}>
                    <SelectTrigger className="w-[180px] h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent className="max-h-72">
                      {allPairs.length === 0 && <SelectItem value="EURUSD">EURUSD</SelectItem>}
                      {allPairs.map((p) => (
                        <SelectItem key={p.symbol || p.display} value={p.symbol || p.display || ''}>
                          {p.display || p.symbol} {p.type ? `· ${p.type}` : ''}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Textarea
                    value={batchList}
                    onChange={(e) => setBatchList(e.target.value)}
                    placeholder="One pair per line or comma-separated (e.g. EUR/USD, GBPUSD, XAU/USD)"
                    className="min-h-[72px] flex-1 min-w-[220px] text-xs font-mono"
                  />
                )}
                <Select value={engine} onValueChange={(v) => setEngine(v as EngineKey)}>
                  <SelectTrigger className="w-[200px] h-8 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {ENGINE_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {engine !== 'D' && (
                  <Select value={style} onValueChange={setStyle}>
                    <SelectTrigger className="w-[120px] h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {STYLE_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                )}
                <Select value={validationMode} onValueChange={setValidationMode}>
                  <SelectTrigger className="w-[150px] h-8 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="standard">standard</SelectItem>
                    <SelectItem value="walk_forward">walk_forward</SelectItem>
                    <SelectItem value="purged_cv">purged_cv</SelectItem>
                  </SelectContent>
                </Select>
                <Button size="sm" className="h-8 gap-1 text-xs" onClick={handleRun} disabled={running}>
                  <Play className="w-3 h-3" />
                  {running ? 'Backtesting…' : batchMode ? 'Run batch' : 'Run Backtest'}
                </Button>
              </div>
              <label className="flex items-center gap-2 text-[11px] text-muted-foreground cursor-pointer select-none">
                <input
                  type="checkbox"
                  className="rounded border-border"
                  checked={batchMode}
                  onChange={(e) => {
                    setBatchMode(e.target.checked);
                    setBatchRows([]);
                  }}
                />
                Batch multiple pairs (runs sequentially — same payloads as single-pair mode)
              </label>
              <p className="text-[10px] text-muted-foreground">
                {ENGINE_OPTIONS.find((o) => o.value === engine)?.help}
              </p>
              {btError && <ErrorBanner message={btError} onRetry={handleRun} />}
            </CardContent>
          </Card>

          {batchRows.length > 0 && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>Batch summary</CardTitle>
              </CardHeader>
              <CardContent className="max-h-[240px] overflow-y-auto">
                <table className="w-full text-left text-[11px]">
                  <thead>
                    <tr className="border-b border-border/40">
                      <th className="py-2 text-muted-foreground">Pair</th>
                      <th className="py-2 text-muted-foreground text-right">OK</th>
                      <th className="py-2 text-muted-foreground text-right">Trades</th>
                      <th className="py-2 text-muted-foreground text-right">WR%</th>
                      <th className="py-2 text-muted-foreground text-right">SQN</th>
                      <th className="py-2 text-muted-foreground">Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {batchRows.map((row, i) => (
                      <tr key={`${row.pairKey}-${i}`} className="border-b border-border/20 hover:bg-muted/20">
                        <td className="py-2 font-mono">{row.pairKey}</td>
                        <td className="py-2 text-right">{row.ok ? '✓' : '—'}</td>
                        <td className="py-2 text-right font-mono">{row.ok ? row.result?.totalTrades ?? '—' : '—'}</td>
                        <td className="py-2 text-right font-mono">{row.ok && row.result?.winRate != null ? `${fmtNum(row.result.winRate, 1)}%` : '—'}</td>
                        <td className="py-2 text-right font-mono">{row.ok && row.result?.sqn != null ? fmtNum(row.result.sqn, 2) : '—'}</td>
                        <td className="py-2 text-short text-[10px]">{row.error || ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          )}

          {batchRows.length === 0 && insufficient && (
            <div className="flex items-center gap-2 p-3 rounded-md bg-warning/20 border border-warning/40">
              <AlertTriangle className="w-4 h-4 text-warning" />
              <span className="text-xs text-warning">
                Only {result?.totalTrades} trades — results are not statistically valid (minimum 30 recommended).
              </span>
            </div>
          )}

          {result?.error && !running && (
            <Card className="border-short/40 bg-short/10">
              <CardContent className="p-3 text-xs text-short">{result.error}</CardContent>
            </Card>
          )}

          {result && !result.error && (
            <>
              {/* KPIs */}
              <div className="grid grid-cols-6 gap-3">
                <Stat title="Trades" value={result.totalTrades ?? '—'} />
                <Stat title="Win rate" value={result.winRate != null ? `${fmtNum(result.winRate, 1)}%` : '—'} />
                <Stat
                  title="Profit factor"
                  value={result.profitFactor == null ? '—' : fmtNum(result.profitFactor, 2)}
                  accent={(result.profitFactor ?? 0) >= 1.2 ? 'text-long' : 'text-warning'}
                />
                <Stat title="Expectancy R" value={result.expectancy == null ? '—' : fmtNum(result.expectancy, 2)} />
                <Stat title="SQN" value={result.sqn == null ? '—' : fmtNum(result.sqn, 2)} accent={sqnAccent(result.sqn)} />
                <Stat title="Max DD" value={result.maxDrawdownPct == null ? '—' : `${fmtNum(result.maxDrawdownPct, 1)}%`} accent="text-warning" />
              </div>

              <div className="grid grid-cols-6 gap-3">
                <Stat title="Sharpe" value={result.sharpe == null ? '—' : fmtNum(result.sharpe, 2)} />
                <Stat title="Sortino" value={result.sortino == null ? '—' : fmtNum(result.sortino, 2)} />
                <Stat title="Avg win" value={result.avgWin == null ? '—' : fmtNum(result.avgWin, 2)} accent="text-long" />
                <Stat title="Avg loss" value={result.avgLoss == null ? '—' : fmtNum(result.avgLoss, 2)} accent="text-short" />
                <Stat title="IS SQN" value={result.wfSplit?.is_sqn == null ? '—' : fmtNum(result.wfSplit.is_sqn, 2)} />
                <Stat title="OOS SQN" value={result.wfSplit?.oos_sqn == null ? '—' : fmtNum(result.wfSplit.oos_sqn, 2)} />
              </div>

              {result.wfSplit?.lowSampleSqnWarning === true && (
                <div className="flex items-center gap-2 p-3 rounded-md bg-warning/15 border border-warning/40 text-warning text-xs">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>
                    Low sample warning: fewer than {result.wfSplit?.lowSampleSqnTradeFloor ?? 30} realised trades — treat SQN /
                    risk metrics as indicative only.
                  </span>
                </div>
              )}

              {Array.isArray(result.metricsInterpretationNotes) && result.metricsInterpretationNotes.length > 0 && (
                <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground space-y-1">
                  <p className="font-semibold text-foreground/80 uppercase tracking-wider text-[10px]">Interpretation</p>
                  {result.metricsInterpretationNotes.map((line, i) => (
                    <p key={`mi-${i}`}>{line}</p>
                  ))}
                </div>
              )}

              {result.researchMetrics && (
                <Card className="border-border/60 bg-card/50">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>Research integrity (PSR / DSR / PBO)</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-[11px] text-muted-foreground">
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="outline" className="text-[10px] font-mono">
                        trades {result.researchMetrics.tradeCount ?? '—'}
                      </Badge>
                      <Badge variant="outline" className="text-[10px] font-mono">
                        PSR {result.researchMetrics.psr?.available ? `Φ=${fmtNum(Number(result.researchMetrics.psr?.value), 3)}` : 'n/a'}
                      </Badge>
                      <Badge variant="outline" className="text-[10px] font-mono">
                        DSR {result.researchMetrics.dsr?.available ? `Φ=${fmtNum(Number(result.researchMetrics.dsr?.value), 3)}` : 'n/a'}
                      </Badge>
                    </div>
                    {result.researchMetrics.psr?.note && (
                      <p><span className="text-foreground/80">PSR note:</span> {String(result.researchMetrics.psr.note)}</p>
                    )}
                    {result.researchMetrics.pbo?.note && (
                      <p><span className="text-foreground/80">PBO:</span> {String(result.researchMetrics.pbo.note)} — {result.researchMetrics.pbo?.available ? fmtNum(Number(result.researchMetrics.pbo?.value), 2) : 'not computed'}</p>
                    )}
                    {Array.isArray(result.researchMetrics.assumptions) && result.researchMetrics.assumptions.length > 0 && (
                      <div>
                        <p className="text-foreground/80 text-[10px] uppercase tracking-wider mb-1">Assumptions / flags</p>
                        <ul className="list-disc pl-4 space-y-0.5 font-mono text-[10px]">
                          {result.researchMetrics.assumptions.slice(0, 12).map((a) => (
                            <li key={a}>{a}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}

              {/* Equity curve */}
              <Card className="border-border/60 bg-card/50">
                <CardHeader className="pb-2">
                  <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>Equity Curve</CardTitle>
                </CardHeader>
                <CardContent>
                  {equityChart.length === 0 ? (
                    <div className="text-xs text-muted-foreground py-12 text-center">No equity curve data</div>
                  ) : (
                    <div className="h-[280px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={equityChart} margin={{ left: 0, right: 0, top: 4, bottom: 0 }}>
                          <defs>
                            <linearGradient id="btEqGrad" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.5} />
                              <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <XAxis dataKey="idx" hide />
                          <YAxis tick={{ fontSize: 10 }} width={50} domain={['auto', 'auto']} />
                          <Tooltip
                            contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', fontSize: 11 }}
                            formatter={(v: number) => [`$${fmtNum(v, 2)}`, 'Equity']}
                          />
                          <Area type="monotone" dataKey="equity" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#btEqGrad)" />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Engine-specific blocks */}
              {engine === 'D' && result.scalp_analysis && (
                <ScalpAnalysisBlock
                  data={result.scalp_analysis}
                  vol_quality_pct={result.vol_quality_pct}
                  vol_quality_warning={result.vol_quality_warning}
                  same_bar_both_hit={result.same_bar_both_hit}
                  vp_lookback_bars={result.vp_lookback_bars}
                />
              )}

              {result.confluenceAnalysis && Object.keys(result.confluenceAnalysis).length > 0 && (
                <Card className="border-border/60 bg-card/50">
                  <CardHeader className="pb-2"><CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>Confluence Analysis (Engine B)</CardTitle></CardHeader>
                  <CardContent>
                    <pre className="text-[10px] font-mono whitespace-pre-wrap p-2 bg-muted/20 rounded border border-border/40 max-h-72 overflow-y-auto">
                      {JSON.stringify(result.confluenceAnalysis, null, 2)}
                    </pre>
                  </CardContent>
                </Card>
              )}

              {result.engineCFunnel && Object.keys(result.engineCFunnel).length > 0 && (
                <Card className="border-border/60 bg-card/50">
                  <CardHeader className="pb-2"><CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>Engine C Funnel</CardTitle></CardHeader>
                  <CardContent>
                    <pre className="text-[10px] font-mono whitespace-pre-wrap p-2 bg-muted/20 rounded border border-border/40 max-h-72 overflow-y-auto">
                      {JSON.stringify(result.engineCFunnel, null, 2)}
                    </pre>
                  </CardContent>
                </Card>
              )}

              {/* Trade table */}
              {Array.isArray(result.trades) && result.trades.length > 0 && (
                <TradeTable trades={result.trades.slice(0, 100)} totalCount={result.trades.length} />
              )}
            </>
          )}
        </TabsContent>

        <TabsContent value="history" className="mt-3">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>Backtest History (last 500)</CardTitle>
            </CardHeader>
            <CardContent>
              {historyError && <ErrorBanner message={historyError} />}
              <ScrollArea className="h-[520px]">
                {historyLoading ? (
                  <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
                ) : history && history.length > 0 ? (
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-border/40">
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Pair</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Engine</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Trades</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">WR</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">PF</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">SQN</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">DD%</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Min</th>
                        <th className="text-[10px] uppercase py-2 text-muted-foreground">Date</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((h, i) => (
                        <tr key={`${h.id ?? i}`} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                          <td className="py-2 text-xs font-mono">{h.pair || '—'}</td>
                          <td className="py-2 text-[10px] text-muted-foreground">{h.engine || '—'}</td>
                          <td className="py-2 text-xs font-mono text-right">{h.trades ?? '—'}</td>
                          <td className="py-2 text-xs font-mono text-right">{h.win_rate != null ? `${fmtNum(h.win_rate, 1)}%` : '—'}</td>
                          <td className="py-2 text-xs font-mono text-right">{h.profit_factor != null ? fmtNum(h.profit_factor, 2) : '—'}</td>
                          <td className="py-2 text-right"><SqnBadge sqn={toNum(h.sqn)} /></td>
                          <td className="py-2 text-xs font-mono text-right">{h.max_dd_pct != null ? `${fmtNum(h.max_dd_pct, 1)}` : '—'}</td>
                          <td className="py-2 text-xs font-mono text-right">{h.bt_min != null ? fmtNum(h.bt_min, 2) : '—'}</td>
                          <td className="py-2 text-[10px] text-muted-foreground">
                            {h.run_date ? new Date(h.run_date).toLocaleString() : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="text-center text-muted-foreground py-12 text-sm">No backtest history</div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="best" className="mt-3">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Trophy className="w-4 h-4 text-primary" />
                Best Performing Pairs (latest run per pair, sorted by SQN)
              </CardTitle>
            </CardHeader>
            <CardContent>
              {bestError && <ErrorBanner message={bestError} />}
              {bestLoading ? <Skeleton className="h-20 w-full" /> : (
                <ScrollArea className="h-[520px]">
                  {bestData && bestData.length > 0 ? (
                    <div className="space-y-2">
                      {bestData.map((bp, i) => (
                        <div key={`${bp.pair || i}-${i}`} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                          <div className="flex items-center gap-3 min-w-0">
                            <span className="text-[10px] font-mono text-muted-foreground w-6">#{i + 1}</span>
                            <span className="text-xs font-mono font-bold">{bp.pair || '—'}</span>
                            <Badge variant="outline" className="text-[10px]">{bp.engine || '—'}</Badge>
                          </div>
                          <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                            <span>{bp.trades ?? '—'} trades</span>
                            <span>WR {fmtNum(bp.win_rate, 1)}%</span>
                            <span>PF {fmtNum(bp.profit_factor, 2)}</span>
                            <SqnBadge sqn={toNum(bp.sqn)} />
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center text-muted-foreground py-12 text-sm">No best-pair data</div>
                  )}
                </ScrollArea>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="advisory" className="mt-3">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Layers className="w-4 h-4 text-primary" />
                Advisory Thresholds
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-[11px] text-muted-foreground">
                Recommendations are generated from `backtest_results` and closed `audit_log` trades. Approval modifies live
                `BT_MIN`, `MIN_CONFLUENCE_CLASS`, or `NAKED_ENGINE.style_profiles.min_score` — never auto-applied.
              </p>
              {advisoryError && <ErrorBanner message={advisoryError} />}
              {advisoryLoading ? <Skeleton className="h-20 w-full" /> : (
                <>
                  {advisory?.summary && (
                    <div className="rounded-md bg-muted/20 border border-border/40 p-3 text-[11px] space-y-1 font-mono">
                      <div>
                        <span className="text-muted-foreground">Pending queued: </span>
                        <span>{String((advisory.summary as Record<string, unknown>).pending ?? '—')}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">Approved records: </span>
                        <span>{String((advisory.summary as Record<string, unknown>).approved ?? '—')}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">Rejected records: </span>
                        <span>{String((advisory.summary as Record<string, unknown>).rejected ?? '—')}</span>
                      </div>
                      {(advisory.summary as Record<string, unknown>).last_recomputed != null && (
                        <div className="text-[10px] text-muted-foreground pt-1">
                          Last recomputed: {String((advisory.summary as Record<string, unknown>).last_recomputed)}
                        </div>
                      )}
                    </div>
                  )}
                  {advisory?.current_policies && (
                    <details className="rounded-md bg-muted/20 border border-border/40">
                      <summary className="cursor-pointer p-3 text-[11px] font-semibold">Current policy snapshot (read-only)</summary>
                      <pre className="p-3 pt-0 text-[10px] font-mono overflow-x-auto max-h-[220px] overflow-y-auto whitespace-pre-wrap">
                        {JSON.stringify(advisory.current_policies, null, 2)}
                      </pre>
                    </details>
                  )}
                  <AdvisoryGroup
                    title="Pending"
                    rows={(advisory?.pending || advisory?.recommendations || []) as AdvisoryRecommendation[]}
                    onApprove={handleApprove}
                    onReject={handleReject}
                    showActions
                  />
                </>
              )}
              {advisory?.approved && advisory.approved.length > 0 && (
                <AdvisoryGroup title="Approved" rows={advisory.approved} />
              )}
              {advisory?.rejected && advisory.rejected.length > 0 && (
                <AdvisoryGroup title="Rejected" rows={advisory.rejected} />
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function sqnAccent(sqn?: number): string {
  const n = toNum(sqn);
  if (n >= 2) return 'text-long';
  if (n >= 1) return 'text-warning';
  return 'text-short';
}

function ScalpAnalysisBlock({
  data, vol_quality_pct, vol_quality_warning, same_bar_both_hit, vp_lookback_bars,
}: {
  data: NonNullable<BacktestResult['scalp_analysis']>;
  vol_quality_pct?: number;
  vol_quality_warning?: boolean;
  same_bar_both_hit?: number;
  vp_lookback_bars?: number;
}) {
  const triggers: { key: 'absorption' | 'cvd_shift' | 'rejection'; label: string }[] = [
    { key: 'absorption', label: 'Absorption' },
    { key: 'cvd_shift', label: 'CVD Shift' },
    { key: 'rejection', label: 'Rejection' },
  ];
  const setups: { key: 'mean_reversion' | 'trend'; label: string }[] = [
    { key: 'mean_reversion', label: 'Mean Reversion' },
    { key: 'trend', label: 'Trend' },
  ];
  const grades: { key: 'grade_A' | 'grade_B' | 'grade_C'; label: string }[] = [
    { key: 'grade_A', label: 'Grade A' },
    { key: 'grade_B', label: 'Grade B' },
    { key: 'grade_C', label: 'Grade C' },
  ];

  return (
    <Card className="border-border/60 bg-card/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>Engine D — Scalp Analysis</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-4 gap-3">
          <Stat title="VP volume quality" value={vol_quality_pct != null ? `${fmtNum(vol_quality_pct, 0)}%` : '—'} accent={vol_quality_warning ? 'text-warning' : 'text-foreground'} />
          <Stat title="Same-bar both hit" value={same_bar_both_hit ?? 0} />
          <Stat title="VP lookback" value={vp_lookback_bars ?? '—'} />
          <Stat title="Exec proxy" value="M15" />
        </div>

        <div>
          <p className="text-[10px] uppercase text-muted-foreground mb-1">By Trigger</p>
          <div className="grid grid-cols-3 gap-2">
            {triggers.map((t) => (
              <SubsetCard key={t.key} label={t.label} count={data[t.key]?.count} wr={data[t.key]?.wr} avgR={data[t.key]?.avg_r} />
            ))}
          </div>
        </div>

        <div>
          <p className="text-[10px] uppercase text-muted-foreground mb-1">By Setup</p>
          <div className="grid grid-cols-2 gap-2">
            {setups.map((t) => (
              <SubsetCard key={t.key} label={t.label} count={data[t.key]?.count} wr={data[t.key]?.wr} avgR={data[t.key]?.avg_r} />
            ))}
          </div>
        </div>

        <div>
          <p className="text-[10px] uppercase text-muted-foreground mb-1">By AI Grade</p>
          <div className="grid grid-cols-3 gap-2">
            {grades.map((t) => (
              <SubsetCard key={t.key} label={t.label} count={data[t.key]?.count} wr={data[t.key]?.wr} />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function SubsetCard({ label, count, wr, avgR }: { label: string; count?: number; wr?: number | null; avgR?: number | null }) {
  return (
    <div className="p-2 rounded-md bg-muted/30">
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase text-muted-foreground">{label}</span>
        <span className="text-[10px] font-mono">{count ?? 0}</span>
      </div>
      <div className="mt-1 flex items-center gap-3 text-[11px] font-mono">
        <span>WR <span className={wrAccent(wr)}>{wr == null ? '—' : `${fmtNum(wr, 1)}%`}</span></span>
        {avgR != null && <span>avgR {fmtNum(avgR, 2)}</span>}
      </div>
    </div>
  );
}

function wrAccent(wr?: number | null): string {
  if (wr == null) return 'text-muted-foreground';
  if (wr >= 55) return 'text-long';
  if (wr >= 45) return 'text-warning';
  return 'text-short';
}

function TradeTable({ trades, totalCount }: { trades: Array<Record<string, unknown>>; totalCount: number }) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
          Trade Log {totalCount > trades.length && <span className="text-[10px] text-muted-foreground">(showing first {trades.length} of {totalCount})</span>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[320px]">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border/40">
                <th className="text-[10px] uppercase py-2 text-muted-foreground">Entry</th>
                <th className="text-[10px] uppercase py-2 text-muted-foreground">Dir</th>
                <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Score</th>
                <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">SL</th>
                <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">TP</th>
                <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">R</th>
                <th className="text-[10px] uppercase py-2 text-muted-foreground">Exit</th>
                <th className="text-[10px] uppercase py-2 text-muted-foreground">Style</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => {
                const r = (t.resultR ?? t.r_multiple ?? 0) as number;
                const dir = String(t.direction || '').toUpperCase();
                return (
                  <tr key={i} className="border-b border-border/20 hover:bg-muted/30">
                    <td className="py-1 text-[10px] text-muted-foreground">{String(t.entry_time || t.entryTime || t.timestamp || '—').slice(0, 16)}</td>
                    <td className="py-1 text-[10px]">
                      <span className={dir === 'LONG' || dir === 'BUY' ? 'text-long' : 'text-short'}>{dir || '—'}</span>
                    </td>
                    <td className="py-1 text-[10px] font-mono text-right">{fmtNum(t.score as number, 2)}</td>
                    <td className="py-1 text-[10px] font-mono text-right">{fmtNum(t.sl as number, 5)}</td>
                    <td className="py-1 text-[10px] font-mono text-right">{fmtNum((t.tp ?? t.tp1) as number, 5)}</td>
                    <td className={`py-1 text-[10px] font-mono text-right ${r > 0 ? 'text-long' : r < 0 ? 'text-short' : ''}`}>
                      {fmtNum(r, 2)}
                    </td>
                    <td className="py-1 text-[10px] text-muted-foreground">{String(t.exit_reason || t.exitReason || '—')}</td>
                    <td className="py-1 text-[10px] text-muted-foreground">{String(t.style || t.setup_type || '—')}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}

function AdvisoryGroup({
  title, rows, onApprove, onReject, showActions,
}: {
  title: string;
  rows: AdvisoryRecommendation[];
  onApprove?: (r: AdvisoryRecommendation) => void;
  onReject?: (r: AdvisoryRecommendation) => void;
  showActions?: boolean;
}) {
  if (!rows || rows.length === 0) {
    return (
      <div className="p-3 rounded-md bg-muted/20 text-[11px] text-muted-foreground">{title}: none</div>
    );
  }
  return (
    <div className="space-y-2">
      <p className="text-[10px] uppercase text-muted-foreground">{title}</p>
      {rows.map((rec, i) => {
        const cur =
          typeof rec.current_value === 'number'
            ? rec.current_value
            : typeof rec.current === 'number'
              ? rec.current
              : null;
        const proposed =
          typeof rec.proposed_value === 'number'
            ? rec.proposed_value
            : typeof rec.recommended === 'number'
              ? rec.recommended
              : null;
        const headline =
          typeof rec.title === 'string'
            ? rec.title
            : [rec.pair, rec.scope_key].filter(Boolean).join(' ') || 'Recommendation';
        const reasonLines: string[] = Array.isArray(rec.reasons)
          ? rec.reasons.map(String)
          : rec.reason
            ? [String(rec.reason)]
            : [];
        return (
        <div key={`${rec.id ?? rec.rec_id ?? i}-${i}`} className="p-3 rounded-md bg-muted/30 flex items-center justify-between gap-3">
          <div className="text-xs space-y-1 min-w-0">
            <div>
              <span className="font-semibold">{headline}</span>
              {typeof rec.subtitle === 'string' && rec.subtitle && (
                <span className="text-muted-foreground text-[10px] ml-2">{rec.subtitle}</span>
              )}
              <Badge variant="outline" className="ml-2 text-[10px]">
                {typeof rec.engine === 'string'
                  ? rec.engine
                  : typeof rec.scope_type === 'string'
                    ? rec.scope_type
                    : '—'}
              </Badge>
              {typeof rec.metric === 'string' && !!rec.metric && (
                <Badge variant="outline" className="ml-1 text-[10px]">{rec.metric}</Badge>
              )}
              {typeof rec.status === 'string' && !!rec.status && (
                <Badge className="ml-1 text-[10px] badge-neutral">{rec.status}</Badge>
              )}
            </div>
            <div className="text-[10px] text-muted-foreground font-mono">
              {cur == null ? '—' : fmtNum(cur, 4)}
              {' → '}
              <span className="text-foreground">{proposed == null ? '—' : fmtNum(proposed, 4)}</span>
              {reasonLines.length > 0 && (
                <ul className="list-disc ml-4 mt-1 font-sans normal-case">
                  {reasonLines.map((ln, j) => <li key={j}>{ln}</li>)}
                </ul>
              )}
              {rec.evidence != null && rec.evidence !== '' && (
                <span className="block mt-1">
                  Evidence:{' '}
                  {typeof rec.evidence === 'string'
                    ? rec.evidence
                    : JSON.stringify(rec.evidence)}
                </span>
              )}
            </div>
          </div>
          {showActions && (
            <div className="flex gap-1 shrink-0">
              <Button size="sm" variant="outline" className="h-7 text-[10px]" onClick={() => onApprove?.(rec)}>Approve</Button>
              <Button size="sm" variant="outline" className="h-7 text-[10px]" onClick={() => onReject?.(rec)}>Reject</Button>
            </div>
          )}
        </div>
        );
      })}
    </div>
  );
}

function Stat({ title, value, accent }: { title: string; value: string | number; accent?: string }) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
        <p className={`text-xl font-mono font-bold mt-1 ${accent || ''}`}>{value}</p>
      </CardContent>
    </Card>
  );
}
