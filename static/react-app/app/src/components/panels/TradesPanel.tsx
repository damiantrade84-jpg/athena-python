import { useState, useCallback } from 'react';
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
import { X, TrendingUp, Clock, AlertTriangle } from 'lucide-react';
import type { Position, PerformanceMetrics } from '@/types';

interface MT5Positions {
  positions: Position[];
}

interface OpenTradeTimed {
  ticket: string;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  volume: number;
  open_price: number;
  sl: number;
  tp: number;
  profit: number;
  open_time: string;
  duration: string;
  swap?: number;
}

export default function TradesPanel() {
  const { showToast } = useStore();
  const [activeTab, setActiveTab] = useState('open');
  const [closeTicket, setCloseTicket] = useState<string | null>(null);
  const [closeSymbol, setCloseSymbol] = useState<string>('');
  const [closeVolume, setCloseVolume] = useState<number>(0);

  const { data: mt5Positions, loading: posLoading, error: posError, refresh: refreshPos } = useApiPoll<MT5Positions>('/api/mt5-positions', 15000);
  const { data: openTrades, loading: openLoading, error: openError, refresh: refreshOpen } = useApiPoll<OpenTradeTimed[]>('/api/open-trades-timed', 15000);
  const { data: performance, loading: perfLoading, error: perfError, refresh: refreshPerf } = useApiPoll<PerformanceMetrics>('/api/performance', 0);
  const { data: autoLog } = useApiPoll<Record<string, unknown>[]>('/api/auto-trade/log', 0);
  const { data: failedExecs } = useApiPoll<Record<string, unknown>[]>('/api/failed-executions', 0);

  const { post: postClose, loading: closing } = useApiPost<{ success: boolean; error?: string }>();

  const handleClose = useCallback(async () => {
    if (!closeTicket) return;
    const result = await postClose('/api/close-position', { ticket: closeTicket, symbol: closeSymbol, volume: closeVolume });
    if (result?.success) {
      showToast(`Closed position ${closeTicket}`, 'success');
      refreshPos();
      refreshOpen();
    } else {
      showToast(`Close failed: ${result?.error || 'Unknown'}`, 'error');
    }
    setCloseTicket(null);
  }, [closeTicket, closeSymbol, closeVolume, postClose, showToast, refreshPos, refreshOpen]);

  const openPositions = mt5Positions?.positions || [];
  const equityData = performance?.equity_curve || [];

  return (
    <div className="space-y-4">
      {(posError || openError || perfError) && (
        <ErrorBanner
          message={[posError, openError, perfError].filter(Boolean).join(' | ')}
          onRetry={() => { refreshPos(); refreshOpen(); refreshPerf(); }}
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
                <Badge variant="outline" className="text-[10px]">{openPositions.length} open</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[500px]">
                {posLoading || openLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                  </div>
                ) : openPositions.length === 0 && (!openTrades || openTrades.length === 0) ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">No open positions</div>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="text-[10px] uppercase">Symbol</TableHead>
                        <TableHead className="text-[10px] uppercase">Dir</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Volume</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Open</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">P&L</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">SL</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">TP</TableHead>
                        <TableHead className="text-[10px] uppercase">Duration</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Action</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(openTrades || openPositions).map((p: any) => (
                        <TableRow key={p.ticket || p.id}>
                          <TableCell className="text-xs font-mono">{p.symbol || p.pair}</TableCell>
                          <TableCell>
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${p.direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                              {p.direction}
                            </span>
                          </TableCell>
                          <TableCell className="text-xs font-mono text-right">{p.volume || p.size}</TableCell>
                          <TableCell className="text-xs font-mono text-right">{(p.open_price || p.entry).toFixed(5)}</TableCell>
                          <TableCell className={`text-xs font-mono font-bold text-right ${(p.profit || p.pnl) >= 0 ? 'text-long' : 'text-short'}`}>
                            {(p.profit || p.pnl) >= 0 ? '+' : ''}${(p.profit || p.pnl).toFixed(2)}
                          </TableCell>
                          <TableCell className="text-xs font-mono text-right text-short">{p.sl.toFixed(5)}</TableCell>
                          <TableCell className="text-xs font-mono text-right text-long">{p.tp.toFixed(5)}</TableCell>
                          <TableCell className="text-[10px] font-mono text-muted-foreground">{p.duration || '-'}</TableCell>
                          <TableCell className="text-right">
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-6 w-6 p-0"
                              onClick={() => { setCloseTicket(p.ticket || p.id); setCloseSymbol(p.symbol || p.pair); setCloseVolume(p.volume || p.size); }}
                            >
                              <X className="w-3 h-3 text-short" />
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))}
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
              <CardTitle className="text-sm font-semibold">Trade History</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-center text-muted-foreground py-12 text-sm">Trade history available via API /api/performance</div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="performance" className="mt-2 space-y-4">
          <div className="grid grid-cols-6 gap-3">
            <StatBox title="Total Trades" value={performance?.total_trades ?? 0} loading={perfLoading} />
            <StatBox title="Win Rate" value={`${((performance?.win_rate ?? 0) * 100).toFixed(1)}%`} loading={perfLoading} />
            <StatBox title="Profit Factor" value={(performance?.profit_factor ?? 0).toFixed(2)} loading={perfLoading} />
            <StatBox title="SQN" value={(performance?.sqn ?? 0).toFixed(2)} loading={perfLoading} sqn />
            <StatBox title="Sharpe" value={(performance?.sharpe ?? 0).toFixed(2)} loading={perfLoading} />
            <StatBox title="Max DD" value={`${((performance?.max_drawdown ?? 0) * 100).toFixed(1)}%`} loading={perfLoading} />
          </div>

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Equity Curve</CardTitle>
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
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} width={50} />
                      <Tooltip
                        contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '6px', fontSize: '11px' }}
                        formatter={(value: number) => [`$${value.toFixed(2)}`, 'Equity']}
                      />
                      <Area type="monotone" dataKey="equity" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#equityGrad2)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No equity data</div>
              )}
            </CardContent>
          </Card>

          {/* By Engine Breakdown */}
          {performance?.by_engine && (
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
                      <TableHead className="text-[10px] uppercase text-right">PF</TableHead>
                      <TableHead className="text-[10px] uppercase text-right">SQN</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries(performance.by_engine).map(([engine, stats]) => (
                      <TableRow key={engine}>
                        <TableCell className="text-xs font-mono capitalize">{engine}</TableCell>
                        <TableCell className="text-xs font-mono text-right">{stats.trades || 0}</TableCell>
                        <TableCell className="text-xs font-mono text-right">{((stats.win_rate || 0) * 100).toFixed(1)}%</TableCell>
                        <TableCell className="text-xs font-mono text-right">{(stats.profit_factor || 0).toFixed(2)}</TableCell>
                        <TableCell className="text-xs font-mono text-right">
                          <SqnBadge sqn={stats.sqn || 0} />
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
                {autoLog && autoLog.length > 0 ? (
                  <div className="space-y-2">
                    {autoLog.map((entry, i) => (
                      <div key={i} className="p-2 rounded-md bg-muted/30 text-xs font-mono">
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
                {failedExecs && failedExecs.length > 0 ? (
                  <div className="space-y-2">
                    {failedExecs.map((entry, i) => (
                      <div key={i} className="p-2 rounded-md bg-short/10 border border-short/20 text-xs font-mono">
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

      {/* Close Confirm Dialog */}
      <AlertDialog open={!!closeTicket} onOpenChange={() => setCloseTicket(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Close Position</AlertDialogTitle>
            <AlertDialogDescription>
              Close position {closeTicket} for {closeSymbol}?
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

function StatBox({ title, value, loading, sqn }: { title: string; value: string | number; loading?: boolean; sqn?: boolean }) {
  let colorClass = '';
  if (sqn && typeof value === 'string') {
    const n = parseFloat(value);
    colorClass = n >= 2 ? 'text-long' : n >= 1 ? 'text-warning' : 'text-short';
  }
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
        {loading ? <Skeleton className="h-6 w-16 mt-1" /> : (
          <p className={`text-xl font-mono font-bold mt-1 ${colorClass}`}>{value}</p>
        )}
      </CardContent>
    </Card>
  );
}
