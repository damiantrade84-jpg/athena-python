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
import { Switch } from '@/components/ui/switch';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { ErrorBanner } from '@/components/shared';
import { Activity, Play, BarChart3, Save, AlertTriangle } from 'lucide-react';
import type { ScalpSignal } from '@/types';

interface ScalpPair {
  symbol: string;
  group: string;
  session: string;
}

interface ScalpPairsData {
  pairs: ScalpPair[];
}

interface BacktestScalpResult {
  sqn: number;
  win_rate: number;
  total_trades: number;
  profit_factor: number;
  max_drawdown: number;
}

interface GroupRR {
  [group: string]: number;
}

export default function ScalpLabPanel() {
  const { showToast } = useStore();
  const [selectedPair, setSelectedPair] = useState('EURUSD');
  const [sessionMode, setSessionMode] = useState(false);
  const [scalpResult, setScalpResult] = useState<ScalpSignal | null>(null);
  const [confirmExecute, setConfirmExecute] = useState(false);
  const [backtestDays, setBacktestDays] = useState(30);
  const [backtestResult, setBacktestResult] = useState<BacktestScalpResult | null>(null);
  const [localGroupRR, setLocalGroupRR] = useState<GroupRR>({});

  const { data: scalpPairs, loading: pairsLoading, error: pairsError } = useApiPoll<ScalpPairsData>('/api/scalp-pairs', 0);
  const { post: postScan, loading: scanning } = useApiPost<ScalpSignal>();
  const { post: postExecute, loading: executing } = useApiPost<{ success: boolean; ticket?: string; error?: string }>();
  const { post: postBacktest, loading: backtestLoading } = useApiPost<BacktestScalpResult>();
  const { data: groupRR, loading: rrLoading, error: rrError, refresh: refreshRR } = useApiPoll<GroupRR>('/api/scalp-group-rr', 0);
  const { post: postGroupRR } = useApiPost<void>();

  const handleScan = useCallback(async () => {
    const result = await postScan('/api/scalp-scan', { symbol: selectedPair, session_mode: sessionMode });
    if (result) setScalpResult(result);
  }, [selectedPair, sessionMode, postScan]);

  const handleExecute = useCallback(async () => {
    if (!scalpResult) return;
    const payload = {
      symbol: selectedPair,
      direction: scalpResult.entry > scalpResult.sl ? 'LONG' : 'SHORT',
      entry: scalpResult.entry,
      sl: scalpResult.sl,
      tp: scalpResult.tp,
      tp2: scalpResult.tp2,
      grade: scalpResult.grade,
      score: scalpResult.score,
      setup_type: scalpResult.setup_type,
    };
    const result = await postExecute('/api/scalp-execute', payload);
    if (result?.success) {
      showToast(`Scalp executed — Ticket ${result.ticket}`, 'success');
    } else {
      showToast(`Execution failed: ${result?.error || 'Unknown'}`, 'error');
    }
    setConfirmExecute(false);
  }, [scalpResult, selectedPair, postExecute, showToast]);

  const handleBacktest = useCallback(async () => {
    const result = await postBacktest('/api/backtest-scalp', { pair: selectedPair, days: backtestDays });
    if (result) setBacktestResult(result);
  }, [selectedPair, backtestDays, postBacktest]);

  const handleSaveRR = useCallback(async () => {
    await postGroupRR('/api/scalp-group-rr', localGroupRR);
    showToast('Group RR config saved', 'success');
    refreshRR();
  }, [localGroupRR, postGroupRR, showToast, refreshRR]);

  const gradeColor = (grade: string) => {
    switch (grade) {
      case 'A': return 'bg-long/20 text-long border-long/40';
      case 'B': return 'bg-blue-500/20 text-blue-400 border-blue-500/40';
      case 'C': return 'bg-warning/20 text-warning border-warning/40';
      case 'D': return 'bg-short/20 text-short border-short/40';
      default: return 'bg-muted text-muted-foreground';
    }
  };

  const canExecute = scalpResult && scalpResult.grade !== 'D' && scalpResult.executable;

  return (
    <div className="space-y-5">
      {(pairsError || rrError) && <ErrorBanner message={[pairsError, rrError].filter(Boolean).join(' | ')} />}

      {/* Pair Selector & Scan */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            Scalp Scan
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-3 flex-wrap">
            <select
              className="h-8 text-xs bg-card border border-border/60 rounded px-2 min-w-[120px]"
              value={selectedPair}
              onChange={e => setSelectedPair(e.target.value)}
            >
              {pairsLoading ? <option>Loading...</option> : scalpPairs?.pairs.map(p => (
                <option key={p.symbol} value={p.symbol}>{p.symbol} ({p.group})</option>
              ))}
            </select>
            <div className="flex items-center gap-2">
              <Switch checked={sessionMode} onCheckedChange={setSessionMode} />
              <span className="text-xs text-muted-foreground">Session Mode</span>
            </div>
            <Button size="sm" className="h-8 text-xs" onClick={handleScan} disabled={scanning}>
              {scanning ? 'Scanning...' : 'Scan'}
            </Button>
          </div>

          {scalpResult && (
            <div className="p-4 rounded-md bg-muted/30 space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-mono font-bold">{selectedPair}</span>
                  <Badge variant="outline" className={`text-[10px] ${gradeColor(scalpResult.grade)}`}>
                    Grade {scalpResult.grade}
                  </Badge>
                  <Badge variant="outline" className="text-[10px]">{scalpResult.setup_type}</Badge>
                </div>
                <span className="text-xs font-mono">Score: {scalpResult.score}/100</span>
              </div>

              {/* Quality Components */}
              {scalpResult.components && (
                <div className="grid grid-cols-4 gap-2">
                  {Object.entries(scalpResult.components).map(([key, val]) => (
                    <div key={key} className="p-2 rounded bg-card/50">
                      <p className="text-[9px] uppercase text-muted-foreground">{key}</p>
                      <Progress value={val as number} className="h-1 mt-1" />
                      <p className="text-[10px] font-mono text-right mt-0.5">{val as number}</p>
                    </div>
                  ))}
                </div>
              )}

              <div className="grid grid-cols-4 gap-2 text-xs">
                <div className="p-2 rounded bg-short/10">
                  <p className="text-[10px] text-short">SL</p>
                  <p className="font-mono font-bold">{scalpResult.sl.toFixed(5)}</p>
                </div>
                <div className="p-2 rounded bg-long/10">
                  <p className="text-[10px] text-long">TP</p>
                  <p className="font-mono font-bold">{scalpResult.tp.toFixed(5)}</p>
                </div>
                {scalpResult.tp2 && (
                  <div className="p-2 rounded bg-long/10">
                    <p className="text-[10px] text-long">TP2</p>
                    <p className="font-mono font-bold">{scalpResult.tp2.toFixed(5)}</p>
                  </div>
                )}
                <div className="p-2 rounded bg-muted/30">
                  <p className="text-[10px] text-muted-foreground">R:R</p>
                  <p className="font-mono font-bold">{scalpResult.rr_actual.toFixed(2)}</p>
                </div>
                <div className="p-2 rounded bg-muted/30">
                  <p className="text-[10px] text-muted-foreground">Confluence</p>
                  <p className="font-mono font-bold">{scalpResult.confluenceScore}/100</p>
                </div>
              </div>

              {scalpResult.candidate_fail_reasons && scalpResult.candidate_fail_reasons.length > 0 && (
                <div className="p-2 rounded bg-short/10 border border-short/20">
                  <p className="text-[10px] text-short uppercase mb-1">Fail Reasons</p>
                  <div className="flex flex-wrap gap-1">
                    {scalpResult.candidate_fail_reasons.map(r => (
                      <Badge key={r} variant="outline" className="text-[9px] text-short border-short/40">{r}</Badge>
                    ))}
                  </div>
                </div>
              )}

              <Button
                size="sm"
                className="w-full gap-2"
                onClick={() => setConfirmExecute(true)}
                disabled={!canExecute}
              >
                <Play className="w-3.5 h-3.5" />
                {canExecute ? 'Execute Scalp' : 'Not Executable'}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Backtest */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-primary" />
            Scalp Backtest
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-3">
            <Input value={selectedPair} onChange={e => setSelectedPair(e.target.value.toUpperCase())} className="w-32 h-8 text-xs font-mono" />
            <Input type="number" value={backtestDays} onChange={e => setBacktestDays(parseInt(e.target.value))} className="w-24 h-8 text-xs font-mono" placeholder="Days" />
            <Button size="sm" className="h-8 text-xs" onClick={handleBacktest} disabled={backtestLoading}>
              {backtestLoading ? 'Running...' : 'Run'}
            </Button>
          </div>
          {backtestResult && (
            <div className="grid grid-cols-5 gap-3">
              <StatBox title="SQN" value={backtestResult.sqn.toFixed(2)} sqn />
              <StatBox title="Win Rate" value={`${(backtestResult.win_rate * 100).toFixed(1)}%`} />
              <StatBox title="Trades" value={backtestResult.total_trades} />
              <StatBox title="Profit Factor" value={backtestResult.profit_factor.toFixed(2)} />
              <StatBox title="Max DD" value={`${(backtestResult.max_drawdown * 100).toFixed(1)}%`} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Group RR Config */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <Save className="w-4 h-4 text-primary" />
            Group Min RR Config
          </CardTitle>
        </CardHeader>
        <CardContent>
          {rrLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : groupRR ? (
            <div className="space-y-3">
              {Object.entries(groupRR).map(([group, rr]) => (
                <div key={group} className="flex items-center justify-between">
                  <span className="text-xs capitalize">{group}</span>
                  <Input
                    type="number"
                    step="0.1"
                    className="w-24 h-8 text-xs font-mono"
                    defaultValue={rr}
                    onChange={e => setLocalGroupRR(prev => ({ ...prev, [group]: parseFloat(e.target.value) }))}
                  />
                </div>
              ))}
              <Button size="sm" className="gap-1 text-xs" onClick={handleSaveRR}>
                <Save className="w-3 h-3" /> Save
              </Button>
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">No group RR config</div>
          )}
        </CardContent>
      </Card>

      {/* Execute Confirm */}
      <AlertDialog open={confirmExecute} onOpenChange={setConfirmExecute}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Scalp Execution</AlertDialogTitle>
            <AlertDialogDescription>
              Execute {selectedPair} scalp at {scalpResult?.entry.toFixed(5)}?
              <br />SL: {scalpResult?.sl.toFixed(5)} | TP: {scalpResult?.tp.toFixed(5)}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleExecute} disabled={executing}>
              {executing ? 'Executing...' : 'Confirm'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
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
