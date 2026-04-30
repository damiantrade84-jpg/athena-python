import { useState, useCallback } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import { ErrorBanner, SqnBadge } from '@/components/shared';
import { GitMerge, Play, BarChart3, TrendingUp } from 'lucide-react';
import type { Signal } from '@/types';

interface ConsensusResult {
  signal?: Signal;
  engine_a_weight: number;
  engine_b_weight: number;
  gating_weight: number;
  regime: string;
}

interface BacktestConsensusResult {
  sqn: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  equity_curve: { date: string; equity: number }[];
}

interface StabilityResult {
  pair: string;
  stability_score: number;
  engine_a_agree: number;
  engine_b_agree: number;
}

export default function EngineCPanel() {
  const { showToast } = useStore();
  const [pair, setPair] = useState('EURUSD');
  const [days, setDays] = useState(30);
  const [consensusResult, setConsensusResult] = useState<ConsensusResult | null>(null);
  const [backtestResult, setBacktestResult] = useState<BacktestConsensusResult | null>(null);

  const { post: postConsensus, loading: consensusLoading } = useApiPost<{ signals: Signal[]; scan_time: number; pairs_scanned: number }>();
  const { post: postBacktest, loading: backtestLoading } = useApiPost<BacktestConsensusResult>();
  const { data: stability, loading: stabilityLoading, error: stabilityError } = useApiPoll<StabilityResult[]>('/api/signal-stability', 0);

  const runConsensus = useCallback(async () => {
    const result = await postConsensus('/api/scan', { asset_class: 'forex', style: 'consensus' });
    if (result?.signals?.[0]) {
      setConsensusResult({
        signal: result.signals[0],
        engine_a_weight: 0.5,
        engine_b_weight: 0.4,
        gating_weight: 0.1,
        regime: 'trending',
      });
    }
  }, [postConsensus]);

  const runBacktest = useCallback(async () => {
    const result = await postBacktest('/api/backtest-consensus', { pair, days });
    if (result) setBacktestResult(result);
  }, [pair, days, postBacktest]);

  return (
    <div className="space-y-5">
      {/* Description */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-4">
          <div className="flex items-center gap-2 mb-2">
            <GitMerge className="w-4 h-4 text-primary" />
            <h3 className="text-sm font-semibold">Engine C — Consensus Engine</h3>
          </div>
          <p className="text-xs text-muted-foreground">
            Combines Engine A (factor scoring) and Engine B (market structure) with regime-conditional weighting.
            Signals only pass when both engines agree and gating conditions are met.
          </p>
        </CardContent>
      </Card>

      {/* Run Consensus */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <Play className="w-4 h-4 text-primary" />
            Run Consensus Scan
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Input
              value={pair}
              onChange={e => setPair(e.target.value.toUpperCase())}
              className="w-32 h-8 text-xs font-mono"
              placeholder="Pair"
            />
            <Button size="sm" className="h-8 text-xs" onClick={runConsensus} disabled={consensusLoading}>
              {consensusLoading ? 'Scanning...' : 'Run Consensus'}
            </Button>
          </div>

          {consensusResult?.signal && (
            <div className="p-4 rounded-md bg-muted/30 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-mono font-bold">{consensusResult.signal.pair}</span>
                  <Badge className={consensusResult.signal.direction === 'LONG' ? 'badge-long' : 'badge-short'}>
                    {consensusResult.signal.direction}
                  </Badge>
                </div>
                <Badge variant="outline" className="text-[10px]">Confidence: {consensusResult.signal.confidence}%</Badge>
              </div>
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div className="p-2 rounded bg-blue-500/10">
                  <p className="text-[10px] text-muted-foreground">Engine A</p>
                  <p className="font-mono font-bold">{(consensusResult.engine_a_weight * 100).toFixed(0)}%</p>
                </div>
                <div className="p-2 rounded bg-purple-500/10">
                  <p className="text-[10px] text-muted-foreground">Engine B</p>
                  <p className="font-mono font-bold">{(consensusResult.engine_b_weight * 100).toFixed(0)}%</p>
                </div>
                <div className="p-2 rounded bg-amber-500/10">
                  <p className="text-[10px] text-muted-foreground">Gating</p>
                  <p className="font-mono font-bold">{(consensusResult.gating_weight * 100).toFixed(0)}%</p>
                </div>
              </div>
              <div className="text-[10px] text-muted-foreground">Regime: <span className="text-foreground font-mono">{consensusResult.regime}</span></div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Backtest */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-primary" />
            Consensus Backtest
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Input value={pair} onChange={e => setPair(e.target.value.toUpperCase())} className="w-32 h-8 text-xs font-mono" placeholder="Pair" />
            <Input type="number" value={days} onChange={e => setDays(parseInt(e.target.value))} className="w-24 h-8 text-xs font-mono" placeholder="Days" />
            <Button size="sm" className="h-8 text-xs" onClick={runBacktest} disabled={backtestLoading}>
              {backtestLoading ? 'Running...' : 'Run Backtest'}
            </Button>
          </div>

          {backtestResult && (
            <div className="grid grid-cols-6 gap-3">
              <StatBox title="SQN" value={backtestResult.sqn.toFixed(2)} sqn />
              <StatBox title="Win Rate" value={`${(backtestResult.win_rate * 100).toFixed(1)}%`} />
              <StatBox title="Profit Factor" value={backtestResult.profit_factor.toFixed(2)} />
              <StatBox title="Trades" value={backtestResult.total_trades} />
              <StatBox title="Sharpe" value={backtestResult.sharpe.toFixed(2)} />
              <StatBox title="Max DD" value={`${(backtestResult.max_drawdown * 100).toFixed(1)}%`} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Signal Stability */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-primary" />
            Signal Stability
          </CardTitle>
        </CardHeader>
        <CardContent>
          {stabilityLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : stabilityError ? (
            <ErrorBanner message={stabilityError} />
          ) : stability && stability.length > 0 ? (
            <ScrollArea className="h-[300px]">
              <div className="space-y-2">
                {stability.map(s => (
                  <div key={s.pair} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                    <span className="text-xs font-mono font-bold">{s.pair}</span>
                    <div className="flex items-center gap-4 flex-1 mx-4">
                      <Progress value={s.stability_score} className="h-1.5 flex-1" />
                      <span className="text-xs font-mono">{s.stability_score.toFixed(0)}%</span>
                    </div>
                    <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                      <span>A:{s.engine_a_agree}</span>
                      <span>B:{s.engine_b_agree}</span>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          ) : (
            <div className="text-xs text-muted-foreground">No stability data</div>
          )}
        </CardContent>
      </Card>
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
    <div className="p-3 rounded-md bg-muted/30">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
      <p className={`text-lg font-mono font-bold mt-1 ${colorClass}`}>{value}</p>
    </div>
  );
}
