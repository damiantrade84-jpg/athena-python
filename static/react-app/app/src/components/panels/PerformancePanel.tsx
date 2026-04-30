import { useState } from 'react';
import { useApiPoll } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from 'recharts';
import { ErrorBanner, SqnBadge } from '@/components/shared';
import { TrendingUp, BarChart3, PieChart } from 'lucide-react';
import type { PerformanceMetrics } from '@/types';

export default function PerformancePanel() {
  const { data: perf, loading, error, refresh } = useApiPoll<PerformanceMetrics>('/api/performance', 30000);

  const dailyPnl = perf?.daily_pnl || [];
  const equityCurve = perf?.equity_curve || [];

  return (
    <div className="space-y-5">
      {error && <ErrorBanner message={error} onRetry={refresh} />}

      {/* KPI Cards */}
      <div className="grid grid-cols-6 gap-3">
        <KpiCard title="Total Trades" value={perf?.total_trades ?? 0} loading={loading} />
        <KpiCard title="Win Rate" value={`${((perf?.win_rate ?? 0) * 100).toFixed(1)}%`} loading={loading} />
        <KpiCard title="Profit Factor" value={(perf?.profit_factor ?? 0).toFixed(2)} loading={loading} />
        <KpiCard title="SQN" value={(perf?.sqn ?? 0).toFixed(2)} loading={loading} sqn />
        <KpiCard title="Sharpe" value={(perf?.sharpe ?? 0).toFixed(2)} loading={loading} />
        <KpiCard title="Max DD" value={`${((perf?.max_drawdown ?? 0) * 100).toFixed(1)}%`} loading={loading} />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-2 gap-5">
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-primary" />
              Equity Curve
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? <Skeleton className="h-[260px] w-full" /> : equityCurve.length > 0 ? (
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityCurve}>
                    <defs>
                      <linearGradient id="perfEquityGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} width={50} />
                    <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '6px', fontSize: '11px' }} formatter={(value: number) => [`$${value.toFixed(2)}`, 'Equity']} />
                    <Area type="monotone" dataKey="equity" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#perfEquityGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="text-center text-muted-foreground py-12 text-sm">No equity data</div>
            )}
          </CardContent>
        </Card>

        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-primary" />
              Daily P&L
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? <Skeleton className="h-[260px] w-full" /> : dailyPnl.length > 0 ? (
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={dailyPnl}>
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} width={50} />
                    <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '6px', fontSize: '11px' }} formatter={(value: number) => [`$${value.toFixed(2)}`, 'P&L']} />
                    <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                      {dailyPnl.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.pnl >= 0 ? 'hsl(var(--long))' : 'hsl(var(--short))'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="text-center text-muted-foreground py-12 text-sm">No daily P&L data</div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* By Engine */}
      {perf?.by_engine && (
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
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
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">PF</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">SQN</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">P&L</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(perf.by_engine).map(([engine, stats]) => (
                  <tr key={engine} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                    <td className="py-2 text-xs font-mono capitalize">{engine}</td>
                    <td className="py-2 text-xs font-mono text-right">{stats.trades || 0}</td>
                    <td className="py-2 text-xs font-mono text-right">{((stats.win_rate || 0) * 100).toFixed(1)}%</td>
                    <td className="py-2 text-xs font-mono text-right">{(stats.profit_factor || 0).toFixed(2)}</td>
                    <td className="py-2 text-right"><SqnBadge sqn={stats.sqn || 0} /></td>
                    <td className={`py-2 text-xs font-mono font-bold text-right ${(stats.pnl || 0) >= 0 ? 'text-long' : 'text-short'}`}>
                      {(stats.pnl || 0) >= 0 ? '+' : ''}${(stats.pnl || 0).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {/* By Asset Class */}
      {perf?.by_asset_class && (
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold">By Asset Class</CardTitle>
          </CardHeader>
          <CardContent>
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border/40">
                  <th className="text-[10px] uppercase py-2 text-muted-foreground">Asset Class</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Trades</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">WR</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">PF</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">P&L</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(perf.by_asset_class).map(([cls, stats]) => (
                  <tr key={cls} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                    <td className="py-2 text-xs font-mono capitalize">{cls}</td>
                    <td className="py-2 text-xs font-mono text-right">{stats.trades || 0}</td>
                    <td className="py-2 text-xs font-mono text-right">{((stats.win_rate || 0) * 100).toFixed(1)}%</td>
                    <td className="py-2 text-xs font-mono text-right">{(stats.profit_factor || 0).toFixed(2)}</td>
                    <td className={`py-2 text-xs font-mono font-bold text-right ${(stats.pnl || 0) >= 0 ? 'text-long' : 'text-short'}`}>
                      {(stats.pnl || 0) >= 0 ? '+' : ''}${(stats.pnl || 0).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function KpiCard({ title, value, loading, sqn }: { title: string; value: string | number; loading?: boolean; sqn?: boolean }) {
  let colorClass = '';
  if (sqn && typeof value === 'string') {
    const n = parseFloat(value);
    colorClass = n >= 2 ? 'text-long' : n >= 1 ? 'text-warning' : 'text-short';
  }
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
        {loading ? <Skeleton className="h-7 w-20 mt-1" /> : (
          <p className={`text-2xl font-mono font-bold mt-1 ${colorClass}`}>{value}</p>
        )}
      </CardContent>
    </Card>
  );
}
