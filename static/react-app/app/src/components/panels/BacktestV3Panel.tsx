/**
 * Backtest V3 — separate from the Backtesting v2 platform panel.
 * Uses /api/v3/backtest and /api/v3/backtest-naked only.
 */
import { memo, useCallback, useState } from 'react';
import {
  BarChart3,
  Database,
  FlaskConical,
  Play,
  RefreshCw,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { useStore } from '@/hooks/useStore';
import { buildBacktestRequest, type BacktestEngineKey } from '@/lib/backtestPayload';
import { cn } from '@/lib/utils';

type V3HistoryRow = {
  id?: number;
  run_date?: string;
  pair?: string;
  symbol?: string;
  engine?: string;
  style?: string;
  trades?: number;
  win_rate?: number;
  profit_factor?: number;
  expectancy?: number;
  sqn?: number;
  total_r?: number;
  verdict?: string;
  wall_time_sec?: number;
};

type V3RunResult = {
  error?: string;
  totalTrades?: number;
  winRate?: number;
  profitFactor?: number;
  expectancyR?: number;
  sqn?: number;
  totalR?: number;
  maxDrawdownR?: number;
  entryTimeframe?: string;
  timeframePolicyVersion?: string;
  policyKey?: string;
  structureTf?: string;
  setupTf?: string;
  triggerTf?: string;
  executionTf?: string;
  m5Policy?: string;
  missingPolicyTimeframes?: string[];
  validation?: { verdict?: string; trialCount?: number };
  wallTimeSec?: number;
  runId?: number | string;
};

function metricValue(value: number | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toFixed(digits);
}

function formatDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-black/20 p-3">
      <div className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{label}</div>
      <div className="mt-1 font-mono text-xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

function BacktestV3Panel() {
  const { showToast } = useStore();
  const [engine, setEngine] = useState<BacktestEngineKey>('A');
  const [style, setStyle] = useState('intraday');
  const [pair, setPair] = useState('EUR/USD');
  const [result, setResult] = useState<V3RunResult | null>(null);

  const historyPoll = useApiPoll<V3HistoryRow[]>('/api/v3/backtest-history', 10_000);
  const runMutation = useApiPost<V3RunResult>();
  const history = Array.isArray(historyPoll.data) ? historyPoll.data : [];

  const runBacktest = useCallback(async () => {
    const token = pair.trim();
    if (!token) {
      showToast('Select a pair for the V3 backtest', 'error');
      return;
    }
    const request = buildBacktestRequest({ engine, pair: token, style });
    const response = await runMutation.post(request.endpoint, {
      ...request.payload,
      persist: true,
    });
    if (!response || response.error) {
      showToast(response?.error || runMutation.error || 'V3 backtest failed', 'error');
      setResult(response);
      return;
    }
    setResult(response);
    await historyPoll.refresh();
    showToast(
      `V3 Engine ${engine} · ${token}: ${response.totalTrades ?? 0} trades · setup ${response.setupTf || '—'} · trigger ${response.triggerTf || '—'}`,
      'success',
    );
  }, [pair, engine, style, runMutation, historyPoll, showToast]);

  return (
    <div className="space-y-5 p-4 md:p-6">
      <div className="relative overflow-hidden rounded-2xl border border-primary/20 bg-card/70 p-5 backdrop-blur-xl">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_8%_0%,hsl(var(--primary)/0.16),transparent_38%)]" />
        <div className="relative flex flex-col justify-between gap-4 lg:flex-row lg:items-center">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <FlaskConical className="h-5 w-5 text-primary" />
              <h1 className="text-lg font-semibold tracking-[0.08em] text-foreground">BACKTEST V3</h1>
              <Badge variant="outline" className="border-emerald-400/30 bg-emerald-400/10 text-[10px] text-emerald-200">
                SEPARATE FROM V2
              </Badge>
            </div>
            <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
              Single-pair Engine A/B runner on /api/v3/*. Uses the universal TF policy (D1/H4/H4/H1/M15).
              Does not share jobs, datasets, or catalog with Backtesting v2.
            </p>
          </div>
          <Badge variant="outline" className="border-border/70 bg-black/20 font-mono text-[10px]">
            POLICY timeframe_policy.v4
          </Badge>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Card className="panel-glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Play className="h-4 w-4 text-primary" />
              Run V3 backtest
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              {(['A', 'B'] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setEngine(value)}
                  className={cn(
                    'rounded-xl border p-3 text-left transition',
                    engine === value
                      ? 'border-primary/55 bg-primary/12'
                      : 'border-border/60 bg-black/15 hover:border-primary/30',
                  )}
                >
                  <div className="text-sm font-semibold">Engine {value}</div>
                  <div className="mt-1 text-[10px] text-muted-foreground">
                    {value === 'A' ? 'POST /api/v3/backtest' : 'POST /api/v3/backtest-naked'}
                  </div>
                </button>
              ))}
              <div className="space-y-2">
                <Label>Style</Label>
                <Select value={style} onValueChange={setStyle}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {['intraday', 'swing'].map((item) => (
                      <SelectItem key={item} value={item}>
                        {item}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label>Pair (display or symbol)</Label>
              <Input value={pair} onChange={(event) => setPair(event.target.value)} placeholder="EUR/USD" />
            </div>
            <div className="rounded-xl border border-border/60 bg-black/15 p-3 text-[11px] text-muted-foreground">
              Fixed policy roles:{' '}
              <span className="font-mono text-foreground">
                regime D1 · bias H4 · structure H4 · setup H1 · trigger M15
              </span>
            </div>
            <Button className="w-full" onClick={runBacktest} disabled={runMutation.loading}>
              {runMutation.loading ? (
                <>
                  <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                  Running V3…
                </>
              ) : (
                <>
                  <Play className="mr-2 h-4 w-4" />
                  Run V3 backtest
                </>
              )}
            </Button>
            {runMutation.error && (
              <div className="rounded-lg border border-rose-400/30 bg-rose-400/10 p-3 text-xs text-rose-200">
                {runMutation.error}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="panel-glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <BarChart3 className="h-4 w-4 text-primary" />
              Latest V3 result
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {!result ? (
              <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
                Run a V3 backtest to see trades, SQN, and TF policy fields.
              </div>
            ) : result.error ? (
              <div className="rounded-xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-200">
                {result.error}
                {result.missingPolicyTimeframes?.length ? (
                  <div className="mt-2 font-mono text-xs">
                    Missing: {result.missingPolicyTimeframes.join(', ')}
                  </div>
                ) : null}
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <MetricTile label="Trades" value={String(result.totalTrades ?? 0)} />
                  <MetricTile label="SQN" value={metricValue(result.sqn, 2)} />
                  <MetricTile label="Total R" value={metricValue(result.totalR, 2)} />
                  <MetricTile
                    label="Win rate"
                    value={result.winRate == null ? '—' : `${(result.winRate * 100).toFixed(1)}%`}
                  />
                  <MetricTile label="Expectancy" value={metricValue(result.expectancyR, 3)} />
                  <MetricTile label="Wall time" value={formatDuration(result.wallTimeSec)} />
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {result.timeframePolicyVersion || 'policy?'}
                  </Badge>
                  <Badge variant="outline" className="font-mono text-[10px]">
                    struct {result.structureTf || '—'}
                  </Badge>
                  <Badge variant="outline" className="font-mono text-[10px]">
                    setup {result.setupTf || result.entryTimeframe || '—'}
                  </Badge>
                  <Badge variant="outline" className="font-mono text-[10px]">
                    trigger {result.triggerTf || '—'}
                  </Badge>
                  <Badge variant="outline" className="font-mono text-[10px]">
                    m5 {result.m5Policy || '—'}
                  </Badge>
                  {result.validation?.verdict && (
                    <Badge variant="outline" className="text-[10px]">
                      {result.validation.verdict}
                    </Badge>
                  )}
                </div>
                {result.policyKey && (
                  <div className="font-mono text-[10px] text-muted-foreground">{result.policyKey}</div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="panel-glass">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <Database className="h-4 w-4 text-primary" />
              V3 history
            </CardTitle>
            <Button variant="outline" size="sm" onClick={historyPoll.refresh}>
              <RefreshCw className={cn('mr-2 h-3.5 w-3.5', historyPoll.isRefreshing && 'animate-spin')} />
              Refresh
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">backtest_runs_v3 via /api/v3/backtest-history</p>
        </CardHeader>
        <CardContent>
          {historyPoll.loading && !history.length ? (
            <Skeleton className="h-40 w-full" />
          ) : !history.length ? (
            <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
              No V3 runs stored yet.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border/50">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead>
                    <TableHead>Pair</TableHead>
                    <TableHead>Engine</TableHead>
                    <TableHead>Style</TableHead>
                    <TableHead>Trades</TableHead>
                    <TableHead>SQN</TableHead>
                    <TableHead>Total R</TableHead>
                    <TableHead>Verdict</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {history.slice(0, 50).map((row) => (
                    <TableRow key={`${row.id}-${row.pair}-${row.run_date}`}>
                      <TableCell className="text-[10px] text-muted-foreground">
                        {row.run_date?.replace('T', ' ').slice(0, 19) || '—'}
                      </TableCell>
                      <TableCell className="font-medium">{row.pair || row.symbol}</TableCell>
                      <TableCell>{row.engine}</TableCell>
                      <TableCell className="text-[10px] uppercase">{row.style}</TableCell>
                      <TableCell className="font-mono">{row.trades ?? '—'}</TableCell>
                      <TableCell className="font-mono">
                        {row.sqn == null ? '—' : Number(row.sqn).toFixed(2)}
                      </TableCell>
                      <TableCell className="font-mono">
                        {row.total_r == null ? '—' : Number(row.total_r).toFixed(2)}
                      </TableCell>
                      <TableCell className="text-[10px]">{row.verdict || '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default memo(BacktestV3Panel);
