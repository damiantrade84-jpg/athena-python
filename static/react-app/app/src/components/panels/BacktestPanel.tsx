import { useState, useCallback } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';
import { ErrorBanner, SqnBadge } from '@/components/shared';
import { FlaskConical, Play, AlertTriangle, Trophy } from 'lucide-react';
import type { BacktestResult } from '@/types';

interface BacktestApiResult {
  sqn: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  equity_curve: { date: string; equity: number }[];
}

interface BacktestHistoryItem {
  id: string;
  pair: string;
  engine: string;
  sqn: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  timestamp: string;
}

export default function BacktestPanel() {
  const { showToast } = useStore();
  const [pair, setPair] = useState('EURUSD');
  const [engine, setEngine] = useState('A');
  const [timeframe, setTimeframe] = useState('H4');
  const [days, setDays] = useState(90);
  const [result, setResult] = useState<BacktestApiResult | null>(null);
  const [activeTab, setActiveTab] = useState('run');

  const { post: postBacktest, loading: running } = useApiPost<BacktestApiResult>();
  const { post: postNakedBacktest } = useApiPost<BacktestApiResult>();
  const { data: history, loading: historyLoading, error: historyError } = useApiPoll<BacktestHistoryItem[]>('/api/backtest-history', 0);
  const { data: bestPairs, loading: bestLoading, error: bestError } = useApiPoll<{ pair: string; sqn: number; trades: number }[]>('/api/backtest-best', 0);

  const handleRun = useCallback(async () => {
    let res: BacktestApiResult | null = null;
    if (engine === 'B') {
      res = await postNakedBacktest('/api/backtest-naked', { pair, days, style: timeframe });
    } else {
      res = await postBacktest('/api/backtest', { pair, asset_class: 'forex', days, engine: `engine_${engine.toLowerCase()}` });
    }
    if (res) {
      setResult(res);
      showToast(`Backtest complete — ${res.total_trades} trades`, 'success');
    } else {
      showToast('Backtest failed', 'error');
    }
  }, [pair, engine, timeframe, days, postBacktest, postNakedBacktest, showToast]);

  const insufficient = result && result.total_trades < 30;

  return (
    <div className="space-y-5">
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="run" className="text-xs">Run Backtest</TabsTrigger>
          <TabsTrigger value="history" className="text-xs">History</TabsTrigger>
          <TabsTrigger value="best" className="text-xs">Best Pairs</TabsTrigger>
        </TabsList>

        <TabsContent value="run" className="mt-2 space-y-4">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <FlaskConical className="w-4 h-4 text-primary" />
                Backtest Config
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-3 flex-wrap">
                <Input value={pair} onChange={e => setPair(e.target.value.toUpperCase())} className="w-32 h-8 text-xs font-mono" placeholder="Pair" />
                <select value={engine} onChange={e => setEngine(e.target.value)} className="h-8 text-xs bg-card border border-border/60 rounded px-2">
                  <option value="A">Engine A</option>
                  <option value="B">Engine B</option>
                  <option value="C">Engine C</option>
                  <option value="D">Engine D</option>
                </select>
                <select value={timeframe} onChange={e => setTimeframe(e.target.value)} className="h-8 text-xs bg-card border border-border/60 rounded px-2">
                  <option value="M15">M15</option>
                  <option value="H1">H1</option>
                  <option value="H4">H4</option>
                  <option value="D1">D1</option>
                </select>
                <Input type="number" value={days} onChange={e => setDays(parseInt(e.target.value))} className="w-24 h-8 text-xs font-mono" placeholder="Days" />
                <Button size="sm" className="h-8 text-xs gap-1" onClick={handleRun} disabled={running}>
                  <Play className="w-3 h-3" />
                  {running ? 'Running...' : 'Run'}
                </Button>
              </div>
            </CardContent>
          </Card>

          {insufficient && (
            <div className="flex items-center gap-2 p-3 rounded-md bg-warning/20 border border-warning/40">
              <AlertTriangle className="w-4 h-4 text-warning" />
              <span className="text-xs text-warning">
                Only {result?.total_trades} trades — results are not statistically valid (minimum 30 required)
              </span>
            </div>
          )}

          {result && (
            <>
              <div className="grid grid-cols-6 gap-3">
                <StatBox title="SQN" value={result.sqn.toFixed(2)} sqn />
                <StatBox title="Win Rate" value={`${(result.win_rate * 100).toFixed(1)}%`} />
                <StatBox title="Profit Factor" value={result.profit_factor.toFixed(2)} />
                <StatBox title="Trades" value={result.total_trades} />
                <StatBox title="Sharpe" value={result.sharpe.toFixed(2)} />
                <StatBox title="Max DD" value={`${(result.max_drawdown * 100).toFixed(1)}%`} />
              </div>

              <Card className="border-border/60 bg-card/50">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-semibold">Equity Curve</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-[300px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={result.equity_curve}>
                        <defs>
                          <linearGradient id="btEquityGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                            <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} width={50} />
                        <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: '6px', fontSize: '11px' }} formatter={(value: number) => [`$${value.toFixed(2)}`, 'Equity']} />
                        <Area type="monotone" dataKey="equity" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#btEquityGrad)" />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>
            </>
          )}
        </TabsContent>

        <TabsContent value="history" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Backtest History</CardTitle>
            </CardHeader>
            <CardContent>
              {historyError && <ErrorBanner message={historyError} />}
              <ScrollArea className="h-[500px]">
                {historyLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                  </div>
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
                      </tr>
                    </thead>
                    <tbody>
                      {history.map(h => (
                        <tr key={h.id} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                          <td className="py-2 text-xs font-mono">{h.pair}</td>
                          <td className="py-2 text-[10px] text-muted-foreground">{h.engine}</td>
                          <td className="py-2 text-xs font-mono text-right">{h.total_trades}</td>
                          <td className="py-2 text-xs font-mono text-right">{(h.win_rate * 100).toFixed(1)}%</td>
                          <td className="py-2 text-xs font-mono text-right">{h.profit_factor.toFixed(2)}</td>
                          <td className="py-2 text-right">
                            <SqnBadge sqn={h.sqn} />
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

        <TabsContent value="best" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Trophy className="w-4 h-4 text-primary" />
                Best Performing Pairs
              </CardTitle>
            </CardHeader>
            <CardContent>
              {bestError && <ErrorBanner message={bestError} />}
              {bestLoading ? (
                <Skeleton className="h-20 w-full" />
              ) : bestPairs && bestPairs.length > 0 ? (
                <div className="space-y-2">
                  {bestPairs.map((bp, i) => (
                    <div key={i} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-mono text-muted-foreground w-4">#{i + 1}</span>
                        <span className="text-xs font-mono font-bold">{bp.pair}</span>
                      </div>
                      <div className="flex items-center gap-4">
                        <span className="text-[10px] text-muted-foreground">{bp.trades} trades</span>
                        <SqnBadge sqn={bp.sqn} />
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No best pairs data</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function StatBox({ title, value, sqn }: { title: string; value: string | number; sqn?: boolean }) {
  let colorClass = '';
  if (sqn && typeof value === 'string') {
    const n = parseFloat(value);
    colorClass = n >= 2 ? 'text-long' : n >= 1 ? 'text-warning' : 'text-short';
  }
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
        <p className={`text-xl font-mono font-bold mt-1 ${colorClass}`}>{value}</p>
      </CardContent>
    </Card>
  );
}
