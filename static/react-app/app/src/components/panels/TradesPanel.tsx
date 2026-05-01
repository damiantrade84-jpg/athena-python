import { useState, useCallback, useMemo } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { ErrorBanner, SqnBadge } from '@/components/shared';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';
import { X, AlertTriangle } from 'lucide-react';
import { fmtNum } from '@/lib/utils';
import type { PerformanceMetrics, PerformanceEngineRow } from '@/types';

interface OpenTradesTimedResp {
  positions?: Record<string, unknown>[];
  count?: number;
  error?: string;
}

type AnyPos = Record<string, unknown>;

function asArray<T = AnyPos>(x: unknown): T[] {
  if (Array.isArray(x)) return x as T[];
  if (x && typeof x === 'object' && Array.isArray((x as { positions?: unknown }).positions)) {
    return (x as { positions: T[] }).positions;
  }
  return [];
}

function num(v: unknown, fallback = 0): number {
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export default function TradesPanel() {
  const { showToast } = useStore();
  const [activeTab, setActiveTab] = useState('open');
  const [closeTicket, setCloseTicket] = useState<string | null>(null);
  const [closePair, setClosePair] = useState<string>('');
  const [closeVolume, setCloseVolume] = useState<number>(0);
  const [closeExchange, setCloseExchange] = useState<string>('mt5');
  const [closeDirection, setCloseDirection] = useState<string>('LONG');

  const { data: openTradesResp, loading: openLoading, error: openError, refresh: refreshOpen } = useApiPoll<OpenTradesTimedResp>('/api/open-trades-timed', 15000);
  const { data: performance, loading: perfLoading, error: perfError, refresh: refreshPerf } = useApiPoll<PerformanceMetrics>('/api/performance', 0);
  const { data: autoLog } = useApiPoll<unknown>('/api/auto-trade/log', 0);
  const { data: failedExecs } = useApiPoll<unknown>('/api/failed-executions', 0);

  const { post: postClose, loading: closing } = useApiPost<{ success: boolean; error?: string }>();

  const handleClose = useCallback(async () => {
    if (!closeTicket) return;
    const exch = closeExchange.toLowerCase();
    const payload: Record<string, unknown> = {
      exchange: exch,
      volume: closeVolume,
    };
    if (exch === 'mt5') {
      payload.ticket = closeTicket;
    } else {
      payload.pair = closePair;
      payload.direction = closeDirection;
    }
    const result = await postClose('/api/close-position', payload);
    if (result?.success) {
      showToast(`Closed position ${closePair || closeTicket}`, 'success');
      refreshOpen();
    } else {
      showToast(`Close failed: ${result?.error || 'Unknown'}`, 'error');
    }
    setCloseTicket(null);
  }, [closeTicket, closePair, closeVolume, closeExchange, closeDirection, postClose, showToast, refreshOpen]);

  // open-trades-timed is enriched with audit/timed-exit metadata and already includes MT5 + Bybit.
  const openTrades = useMemo<AnyPos[]>(
    () => asArray(openTradesResp),
    [openTradesResp],
  );

  // Equity curve: backend returns flat number[] of cumulative R. Convert to recharts shape.
  const equityData = useMemo(() => {
    const raw = performance?.equity_curve;
    if (!Array.isArray(raw)) return [] as { idx: number; equity: number }[];
    if (raw.length === 0) return [];
    const first = raw[0];
    if (typeof first === 'number') {
      return (raw as number[]).map((v, i) => ({ idx: i + 1, equity: num(v) }));
    }
    if (first && typeof first === 'object' && 'equity' in first) {
      return (raw as { date?: string; equity: number }[]).map((p, i) => ({
        idx: i + 1,
        equity: num((p as { equity?: number }).equity),
      }));
    }
    return [];
  }, [performance?.equity_curve]);

  const autoLogList = useMemo(() => asArray<Record<string, unknown>>(autoLog), [autoLog]);
  const failedList = useMemo(() => asArray<Record<string, unknown>>(failedExecs), [failedExecs]);

  // performance_by_engine is the real key; by_engine is legacy fallback.
  const byEngineEntries: [string, PerformanceEngineRow][] = useMemo(() => {
    const src = performance?.performance_by_engine || performance?.by_engine;
    if (!src || typeof src !== 'object') return [];
    return Object.entries(src) as [string, PerformanceEngineRow][];
  }, [performance?.performance_by_engine, performance?.by_engine]);

  // Backend percentages already pre-multiplied. Don't multiply again.
  const winRatePct = num(performance?.win_rate, 0);
  const maxDdPct = num(performance?.max_drawdown_pct ?? performance?.max_drawdown, 0);
  const profitFactor = performance?.profit_factor;
  const sharpe = performance?.sharpe;

  return (
    <div className="space-y-4">
      {(openError || perfError) && (
        <ErrorBanner
          message={[openError, perfError].filter(Boolean).join(' | ')}
          onRetry={() => { refreshOpen(); refreshPerf(); }}
        />
      )}

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="open" className="text-xs">Open Positions</TabsTrigger>
          <TabsTrigger value="history" className="text-xs">Trade History</TabsTrigger>
          <TabsTrigger value="performance" className="text-xs">Performance</TabsTrigger>
          <TabsTrigger value="autolog" className="text-xs">Auto-Trade Log</TabsTrigger>
          <TabsTrigger value="failed" className="text-xs">Failed</TabsTrigger>
        </TabsList>

        <TabsContent value="open" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center justify-between">
                <span>Open Positions</span>
                <Badge variant="outline" className="text-[10px]">{openTrades.length} open</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[500px]">
                {openLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                  </div>
                ) : openTrades.length === 0 ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">No open positions</div>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="text-[10px] uppercase">Symbol</TableHead>
                        <TableHead className="text-[10px] uppercase">Dir</TableHead>
                        <TableHead className="text-[10px] uppercase">Exch</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Volume</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Open</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">P&amp;L</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">SL</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">TP</TableHead>
                        <TableHead className="text-[10px] uppercase">Style</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Action</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {openTrades.map((p, idx) => {
                        const ticket = String(p.ticket ?? p.id ?? idx);
                        const pairLabel = String(p.pair || p.symbol || '—');
                        const direction = String(p.direction || '');
                        const exch = String(p.exchange || (p._bybit ? 'bybit' : 'mt5'));
                        const volume = num(p.volume ?? p.size);
                        const openPx = num(p.entry ?? p.open_price);
                        const pnl = num(p.profit ?? p.pnl);
                        const sl = num(p.sl);
                        const tp = num(p.tp);
                        const style = String(p.style || p.audit_engine || '—');
                        return (
                          <TableRow key={ticket}>
                            <TableCell className="text-xs font-mono">{pairLabel}</TableCell>
                            <TableCell>
                              <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                                {direction}
                              </span>
                            </TableCell>
                            <TableCell className="text-[10px] uppercase text-muted-foreground">{exch}</TableCell>
                            <TableCell className="text-xs font-mono text-right">{fmtNum(volume, volume < 1 ? 3 : 2)}</TableCell>
                            <TableCell className="text-xs font-mono text-right">{fmtNum(openPx, 5)}</TableCell>
                            <TableCell className={`text-xs font-mono font-bold text-right ${pnl >= 0 ? 'text-long' : 'text-short'}`}>
                              {pnl >= 0 ? '+' : ''}${fmtNum(pnl, 2)}
                            </TableCell>
                            <TableCell className="text-xs font-mono text-right text-short">{fmtNum(sl, 5)}</TableCell>
                            <TableCell className="text-xs font-mono text-right text-long">{fmtNum(tp, 5)}</TableCell>
                            <TableCell className="text-[10px] font-mono text-muted-foreground">{style}</TableCell>
                            <TableCell className="text-right">
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-6 w-6 p-0"
                                onClick={() => {
                                  setCloseTicket(ticket);
                                  setClosePair(pairLabel === '—' ? '' : pairLabel);
                                  setCloseVolume(volume);
                                  setCloseExchange(exch);
                                  setCloseDirection(direction || 'LONG');
                                }}
                              >
                                <X className="w-3 h-3 text-short" />
                              </Button>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="history" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Last 20 Closed Trades</CardTitle>
            </CardHeader>
            <CardContent>
              {Array.isArray(performance?.last_20_trades) && performance!.last_20_trades.length > 0 ? (
                <ScrollArea className="h-[400px]">
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="text-[10px] uppercase">Pair</TableHead>
                        <TableHead className="text-[10px] uppercase">Dir</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Entry</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Exit</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">P&amp;L</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">R</TableHead>
                        <TableHead className="text-[10px] uppercase">Reason</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {performance!.last_20_trades.map((t, i) => (
                        <TableRow key={i}>
                          <TableCell className="text-xs font-mono">{String(t.pair || '—')}</TableCell>
                          <TableCell className="text-xs font-mono">{String(t.direction || '—')}</TableCell>
                          <TableCell className="text-xs font-mono text-right">{fmtNum(t.entry_price, 5)}</TableCell>
                          <TableCell className="text-xs font-mono text-right">{fmtNum(t.exit_price, 5)}</TableCell>
                          <TableCell className={`text-xs font-mono text-right ${num(t.pnl) >= 0 ? 'text-long' : 'text-short'}`}>
                            {num(t.pnl) >= 0 ? '+' : ''}${fmtNum(t.pnl, 2)}
                          </TableCell>
                          <TableCell className={`text-xs font-mono text-right ${num(t.r_multiple) >= 0 ? 'text-long' : 'text-short'}`}>
                            {fmtNum(t.r_multiple, 2)}R
                          </TableCell>
                          <TableCell className="text-[10px] font-mono text-muted-foreground">{String(t.exit_reason || '—')}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </ScrollArea>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No closed trades yet</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="performance" className="mt-2 space-y-4">
          <div className="grid grid-cols-6 gap-3">
            <StatBox title="Total Trades" value={num(performance?.total_trades).toString()} loading={perfLoading} />
            <StatBox title="Win Rate" value={`${winRatePct.toFixed(1)}%`} loading={perfLoading} />
            <StatBox title="Profit Factor" value={profitFactor == null ? '—' : fmtNum(profitFactor, 2)} loading={perfLoading} />
            <StatBox title="Total R" value={fmtNum(performance?.total_r, 2)} loading={perfLoading} />
            <StatBox title="Sharpe" value={sharpe == null ? '—' : fmtNum(sharpe, 2)} loading={perfLoading} />
            <StatBox title="Max DD" value={`${maxDdPct.toFixed(1)}%`} loading={perfLoading} />
          </div>

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Equity Curve (Cumulative R)</CardTitle>
            </CardHeader>
            <CardContent>
              {perfLoading ? (
                <Skeleton className="h-[260px] w-full" />
              ) : equityData.length > 0 ? (
                <div className="h-[260px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={equityData}>
                      <defs>
                        <linearGradient id="equityGrad2" x1="0" y1="0" x2="0" y2="1">
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
                      <Area type="monotone" dataKey="equity" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#equityGrad2)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No equity data — waiting for closed trades</div>
              )}
            </CardContent>
          </Card>

          {byEngineEntries.length > 0 && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold">By Engine</CardTitle>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="text-[10px] uppercase">Engine</TableHead>
                      <TableHead className="text-[10px] uppercase text-right">Trades</TableHead>
                      <TableHead className="text-[10px] uppercase text-right">WR</TableHead>
                      <TableHead className="text-[10px] uppercase text-right">Avg R</TableHead>
                      <TableHead className="text-[10px] uppercase text-right">Total R</TableHead>
                      <TableHead className="text-[10px] uppercase text-right">SQN</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {byEngineEntries.map(([engine, stats]) => (
                      <TableRow key={engine}>
                        <TableCell className="text-xs font-mono capitalize">{engine}</TableCell>
                        <TableCell className="text-xs font-mono text-right">{num(stats.trades)}</TableCell>
                        <TableCell className="text-xs font-mono text-right">{fmtNum(stats.win_rate_pct, 1)}%</TableCell>
                        <TableCell className={`text-xs font-mono text-right ${num(stats.avg_r) >= 0 ? 'text-long' : 'text-short'}`}>{fmtNum(stats.avg_r, 2)}R</TableCell>
                        <TableCell className={`text-xs font-mono text-right ${num(stats.total_r) >= 0 ? 'text-long' : 'text-short'}`}>{fmtNum(stats.total_r, 2)}R</TableCell>
                        <TableCell className="text-xs font-mono text-right">
                          <SqnBadge sqn={num(stats.sqn)} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="autolog" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Auto-Trade Log</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[400px]">
                {autoLogList.length > 0 ? (
                  <div className="space-y-2">
                    {autoLogList.map((entry, i) => (
                      <div key={i} className="p-2 rounded-md bg-muted/30 text-xs font-mono break-all">
                        {JSON.stringify(entry)}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center text-muted-foreground py-12 text-sm">No auto-trade events</div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="failed" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-short" />
                Failed Executions
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[400px]">
                {failedList.length > 0 ? (
                  <div className="space-y-2">
                    {failedList.map((entry, i) => (
                      <div key={i} className="p-2 rounded-md bg-short/10 border border-short/20 text-xs font-mono break-all">
                        {JSON.stringify(entry)}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center text-muted-foreground py-12 text-sm">No failed executions</div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <AlertDialog open={!!closeTicket} onOpenChange={() => setCloseTicket(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Close Position</AlertDialogTitle>
            <AlertDialogDescription>
              Close {closeExchange.toUpperCase()} position {closeTicket}
              {closePair ? ` (${closePair}, ${closeDirection})` : ''}?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleClose} disabled={closing}>
              {closing ? 'Closing...' : 'Confirm Close'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function StatBox({ title, value, loading }: { title: string; value: string | number; loading?: boolean }) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
        {loading ? <Skeleton className="h-6 w-16 mt-1" /> : (
          <p className="text-xl font-mono font-bold mt-1">{value}</p>
        )}
      </CardContent>
    </Card>
  );
}
