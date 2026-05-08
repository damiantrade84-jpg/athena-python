import { useMemo } from 'react';
import { useApiPoll } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from 'recharts';
import { ErrorBanner, SqnBadge } from '@/components/shared';
import { TrendingUp, BarChart3, PieChart } from 'lucide-react';
import { fmtNum } from '@/lib/utils';
import type { PerformanceMetrics, PerformanceEngineRow } from '@/types';

function num(v: unknown, fallback = 0): number {
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/** API returns plain win-rate % per bucket; tolerate legacy `{ trades, win_rate }`. */
function winRateSlicePercent(v: unknown): number | null {
  if (v == null) return null;
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'object' && v !== null) {
    const o = v as { win_rate?: unknown; trades?: unknown };
    if (typeof o.win_rate === 'number') return num(o.win_rate);
  }
  return null;
}

export default function PerformancePanel() {
  const { data: perf, loading, error, refresh } = useApiPoll<PerformanceMetrics>('/api/performance', 30000);

  const equityData = useMemo(() => {
    const raw = perf?.equity_curve;
    if (!Array.isArray(raw) || raw.length === 0) return [] as { idx: number; equity: number }[];
    const first = raw[0];
    if (typeof first === 'number') {
      return (raw as number[]).map((v, i) => ({ idx: i + 1, equity: num(v) }));
    }
    if (first && typeof first === 'object' && 'equity' in (first as object)) {
      return (raw as { equity: number }[]).map((p, i) => ({ idx: i + 1, equity: num(p.equity) }));
    }
    return [];
  }, [perf?.equity_curve]);

  const dailyPnl = useMemo(() => (Array.isArray(perf?.daily_pnl) ? perf!.daily_pnl : []) as { date?: string; pnl?: number }[], [perf?.daily_pnl]);

  const metricNotes = perf?.metric_interpretation_notes;

  const byEngineEntries: [string, PerformanceEngineRow][] = useMemo(() => {
    const src = perf?.performance_by_engine || perf?.by_engine;
    if (!src || typeof src !== 'object') return [];
    return Object.entries(src) as [string, PerformanceEngineRow][];
  }, [perf?.performance_by_engine, perf?.by_engine]);

  const winRatePct = num(perf?.win_rate, 0);
  const maxDdPct = num(perf?.max_drawdown_pct ?? perf?.max_drawdown, 0);
  const profitFactor = perf?.profit_factor;
  const sharpe = perf?.sharpe;

  const winRateByRegime = perf?.win_rate_by_regime || {};
  const winRateByAsset = perf?.win_rate_by_asset_type || {};
  const regimeRows = Object.entries(winRateByRegime)
    .map(([k, v]) => ({ key: k, wr: winRateSlicePercent(v) }))
    .filter((r) => r.wr != null) as { key: string; wr: number }[];
  const assetRows = Object.entries(winRateByAsset)
    .map(([k, v]) => ({ key: k, wr: winRateSlicePercent(v) }))
    .filter((r) => r.wr != null) as { key: string; wr: number }[];

  return (
    <div className="space-y-5">
      {error && <ErrorBanner message={error} onRetry={refresh} />}

      {Array.isArray(metricNotes) && metricNotes.length > 0 && (
        <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground space-y-1">
          <p className="font-semibold text-foreground/80 uppercase tracking-wider text-[10px]">Metrics note</p>
          {metricNotes.map((note, i) => (
            <p key={`mn-${i}`}>{note}</p>
          ))}
        </div>
      )}

      <div className="grid grid-cols-6 gap-3">
        <KpiCard title="Total Trades" value={num(perf?.total_trades).toString()} loading={loading} />
        <KpiCard title="Win Rate" value={`${winRatePct.toFixed(1)}%`} loading={loading} />
        <KpiCard title="Profit Factor" value={profitFactor == null ? '—' : fmtNum(profitFactor, 2)} loading={loading} />
        <KpiCard title="Total R" value={fmtNum(perf?.total_r, 2)} loading={loading} />
        <KpiCard title="Sharpe" value={sharpe == null ? '—' : fmtNum(sharpe, 2)} loading={loading} />
        <KpiCard title="Max DD" value={`${maxDdPct.toFixed(1)}%`} loading={loading} />
      </div>

      <div className="grid grid-cols-2 gap-5">
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
              <TrendingUp className="w-4 h-4 text-primary" />
              Equity Curve (Cumulative R)
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? <Skeleton className="h-[260px] w-full" /> : equityData.length > 0 ? (
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityData}>
                    <defs>
                      <linearGradient id="perfEquityGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="idx" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} width={50} />
                    <Tooltip
                      contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '6px', fontSize: '11px' }}
                      formatter={(value: number) => [`${fmtNum(value, 2)}R`, 'Cumulative R']}
                      labelFormatter={(label) => `Trade #${label}`}
                    />
                    <Area type="monotone" dataKey="equity" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#perfEquityGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="text-center text-muted-foreground py-12 text-sm">No equity data — waiting for closed trades</div>
            )}
          </CardContent>
        </Card>

        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
              <BarChart3 className="w-4 h-4 text-primary" />
              Daily P&amp;L
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? <Skeleton className="h-[260px] w-full" /> : dailyPnl.length > 0 ? (
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={dailyPnl}>
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} width={50} />
                    <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '6px', fontSize: '11px' }} formatter={(value: number) => [`$${fmtNum(value, 2)}`, 'P&L']} />
                    <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                      {dailyPnl.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={num(entry.pnl) >= 0 ? 'hsl(var(--long))' : 'hsl(var(--short))'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="text-center text-muted-foreground py-12 text-sm">No daily P&amp;L breakdown available</div>
            )}
          </CardContent>
        </Card>
      </div>

      {byEngineEntries.length > 0 && (
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
              <PieChart className="w-4 h-4 text-primary" />
              By Engine
            </CardTitle>
          </CardHeader>
          <CardContent>
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border/40">
                  <th className="text-[10px] uppercase py-2 text-muted-foreground">Engine</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Trades</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">WR</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Avg R</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Total R</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">SQN</th>
                </tr>
              </thead>
              <tbody>
                {byEngineEntries.map(([engine, stats]) => (
                  <tr key={engine} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                    <td className="py-2 text-xs font-mono capitalize">{engine}</td>
                    <td className="py-2 text-xs font-mono text-right">{num(stats.trades)}</td>
                    <td className="py-2 text-xs font-mono text-right">{fmtNum(stats.win_rate_pct, 1)}%</td>
                    <td className={`py-2 text-xs font-mono text-right ${num(stats.avg_r) >= 0 ? 'text-long' : 'text-short'}`}>{fmtNum(stats.avg_r, 2)}R</td>
                    <td className={`py-2 text-xs font-mono text-right ${num(stats.total_r) >= 0 ? 'text-long' : 'text-short'}`}>{fmtNum(stats.total_r, 2)}R</td>
                    <td className="py-2 text-right"><SqnBadge sqn={num(stats.sqn)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-5">
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>By Regime</CardTitle>
          </CardHeader>
          <CardContent>
            {regimeRows.length > 0 ? (
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border/40">
                    <th className="text-[10px] uppercase py-2 text-muted-foreground text-left">Regime / trend</th>
                    <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Win rate</th>
                  </tr>
                </thead>
                <tbody>
                  {regimeRows.map(({ key, wr }) => (
                    <tr key={key} className="border-b border-border/20">
                      <td className="py-2 text-xs font-mono capitalize">{key}</td>
                      <td className="py-2 text-xs font-mono text-right">{fmtNum(wr, 1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-center text-muted-foreground py-8 text-xs">
                No regime breakdown yet (needs closed trades with regime/trend on audit rows).
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>By Asset Class</CardTitle>
          </CardHeader>
          <CardContent>
            {assetRows.length > 0 ? (
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border/40">
                    <th className="text-[10px] uppercase py-2 text-muted-foreground text-left">Asset</th>
                    <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Win rate</th>
                  </tr>
                </thead>
                <tbody>
                  {assetRows.map(({ key, wr }) => (
                    <tr key={key} className="border-b border-border/20">
                      <td className="py-2 text-xs font-mono capitalize">{key}</td>
                      <td className="py-2 text-xs font-mono text-right">{fmtNum(wr, 1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-center text-muted-foreground py-8 text-xs">
                No asset-class breakdown yet (needs decisive closed trades · coarse type from pair name).
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function KpiCard({ title, value, loading }: { title: string; value: string | number; loading?: boolean }) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
        {loading ? <Skeleton className="h-7 w-20 mt-1" /> : (
          <p className="text-2xl font-mono font-bold mt-1">{value}</p>
        )}
      </CardContent>
    </Card>
  );
}
