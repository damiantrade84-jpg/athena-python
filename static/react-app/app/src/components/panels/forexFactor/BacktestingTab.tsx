import { useCallback, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';
import { AlertTriangle, FlaskConical, Loader2 } from 'lucide-react';
import { fmtNum } from '@/lib/utils';
import {
  G8_PAIRS,
  runForexBacktest,
  validateForexBacktestRequest,
  parseSymbolList,
  type BacktestReport,
  ForexApiError,
} from '@/lib/forexFactorApi';
import { DiagnosticsList, FX_ACCENT, Stat, TabEmpty } from './shared';
import { ErrorBanner } from '@/components/shared';

type UniverseMode = 'g8' | 'custom';

export default function BacktestingTab() {
  const [start, setStart] = useState('2020-01-01');
  const [end, setEnd] = useState('2024-12-31');
  const [universeMode, setUniverseMode] = useState<UniverseMode>('g8');
  const [customSymbols, setCustomSymbols] = useState('EURUSD,GBPUSD');
  const [strictData, setStrictData] = useState(true);
  const [costModel, setCostModel] = useState(true);
  const [slippagePips, setSlippagePips] = useState('0.5');
  const [targetVol, setTargetVol] = useState('10');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<Array<Record<string, unknown>>>([]);
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  const symbols = useMemo(
    () => (universeMode === 'g8' ? [...G8_PAIRS] : parseSymbolList(customSymbols)),
    [universeMode, customSymbols],
  );

  const handleRun = useCallback(async () => {
    setError(null);
    setDiagnostics([]);
    setReport(null);
    setRunId(null);

    const validation = validateForexBacktestRequest({
      start,
      end,
      symbols,
      strictFactorData: strictData,
      costModelEnabled: costModel,
      slippagePips: slippagePips === '' ? '' : Number(slippagePips),
    });

    if (!validation.valid) {
      setError(validation.errors.join('; '));
      return;
    }

    setRunning(true);
    try {
      const envelope = await runForexBacktest(validation.payload!);
      setRunId(envelope.data?.run_id || null);
      setReport(envelope.data?.report || null);
      setDiagnostics(envelope.diagnostics || []);
    } catch (err) {
      const fxErr = err instanceof ForexApiError ? err : null;
      setError(err instanceof Error ? err.message : 'Backtest failed');
      setDiagnostics(fxErr?.envelope?.diagnostics || []);
      const partial = fxErr?.envelope?.data as { report?: BacktestReport } | undefined;
      if (partial?.report) setReport(partial.report);
    } finally {
      setRunning(false);
    }
  }, [start, end, symbols, strictData, costModel, slippagePips]);

  const summary = (report?.summary || {}) as Record<string, unknown>;
  const equityCurve = useMemo(() => {
    const raw = report?.equity_curve;
    if (!Array.isArray(raw)) return [];
    return raw.map((pt, i) => {
      if (typeof pt === 'number') return { idx: i + 1, equity: pt };
      return {
        date: pt.date || String(i + 1),
        equity: pt.equity ?? pt.value ?? 0,
      };
    });
  }, [report?.equity_curve]);

  const trades = report?.trades || [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <FlaskConical className="h-4 w-4" style={{ color: FX_ACCENT }} /> Backtest configuration
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <Label className="text-xs">Start</Label>
              <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-xs">End</Label>
              <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="h-8 text-xs" />
            </div>
            <div>
              <Label className="text-xs">Slippage (pips)</Label>
              <Input value={slippagePips} onChange={(e) => setSlippagePips(e.target.value)} className="h-8 text-xs font-mono" />
            </div>
            <div>
              <Label className="text-xs">Target vol (%)</Label>
              <Input value={targetVol} onChange={(e) => setTargetVol(e.target.value)} className="h-8 text-xs font-mono" disabled title="Display only — server uses fixed weekly cadence" />
            </div>
          </div>

          <div className="flex flex-wrap gap-4 items-center text-xs">
            <Button variant={universeMode === 'g8' ? 'default' : 'outline'} size="sm" onClick={() => setUniverseMode('g8')}>
              G8 preset ({G8_PAIRS.length} pairs)
            </Button>
            <Button variant={universeMode === 'custom' ? 'default' : 'outline'} size="sm" onClick={() => setUniverseMode('custom')}>
              Custom symbols
            </Button>
            <Badge variant="outline">Weekly cadence (fixed)</Badge>
          </div>

          {universeMode === 'custom' && (
            <Textarea
              value={customSymbols}
              onChange={(e) => setCustomSymbols(e.target.value)}
              placeholder="EURUSD, GBPUSD, ..."
              className="text-xs font-mono min-h-[60px]"
            />
          )}

          <div className="flex flex-wrap gap-6 items-start">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <Switch checked={strictData} onCheckedChange={setStrictData} id="strict-data" />
                <Label htmlFor="strict-data" className="text-xs">Strict factor data (default ON)</Label>
              </div>
              <p className="text-[10px] text-muted-foreground max-w-xs">
                Rejects decisions when carry/COT/REER inputs are missing or stale.
              </p>
              {!strictData && (
                <div className="flex items-center gap-1 text-amber-500 text-[10px]">
                  <AlertTriangle className="h-3 w-3" /> Relaxed mode may include lookahead-biased fills.
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={costModel} onCheckedChange={setCostModel} id="cost-model" />
              <Label htmlFor="cost-model" className="text-xs">Cost model (spread + slippage)</Label>
            </div>
          </div>

          <Button onClick={handleRun} disabled={running} style={{ background: FX_ACCENT, color: '#0a1020' }}>
            {running ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <FlaskConical className="h-4 w-4 mr-1" />}
            {running ? 'Running…' : 'Run backtest'}
          </Button>
        </CardContent>
      </Card>

      {error && <ErrorBanner message={error} />}
      <DiagnosticsList items={diagnostics} />

      {report && (
        <>
          {runId && <p className="text-[11px] text-muted-foreground font-mono">Run ID: {runId}</p>}
          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm">Summary</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Stat label="Trades" value={String(summary.trade_count ?? summary.n ?? '—')} />
              <Stat label="Win rate" value={`${fmtNum(summary.win_rate ?? summary.win_rate_pct, 1, '—')}%`} />
              <Stat label="Expectancy R" value={fmtNum(summary.expectancy_r, 2, '—')} />
              <Stat label="Sharpe" value={fmtNum(summary.sharpe, 2, '—')} />
              <Stat label="SQN" value={fmtNum(summary.sqn, 2, '—')} />
              <Stat label="Max DD %" value={fmtNum(summary.max_drawdown_pct, 1, '—')} />
              <Stat label="Turnover" value={fmtNum(summary.turnover, 2, '—')} />
              <Stat label="Cost drag" value={fmtNum(summary.cost_drag, 2, '—')} />
              <Stat label="Swap contrib." value={fmtNum(summary.swap_contribution, 2, '—')} />
            </CardContent>
          </Card>

          {equityCurve.length > 0 && (
            <div className="h-[260px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={equityCurve}>
                  <XAxis dataKey={equityCurve[0]?.date ? 'date' : 'idx'} tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="equity" stroke={FX_ACCENT} dot={false} strokeWidth={1.5} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {trades.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Dir</TableHead>
                  <TableHead>PnL</TableHead>
                  <TableHead>R</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.slice(0, 100).map((t, i) => (
                  <TableRow key={i}>
                    <TableCell className="text-xs">{String(t.date ?? t.entry_date ?? '—')}</TableCell>
                    <TableCell className="font-mono text-xs">{String(t.symbol ?? '—')}</TableCell>
                    <TableCell className="text-xs">{String(t.direction ?? '—')}</TableCell>
                    <TableCell className="font-mono text-xs">{fmtNum(t.pnl as number, 2, '—')}</TableCell>
                    <TableCell className="font-mono text-xs">{fmtNum(t.r_multiple as number, 2, '—')}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <TabEmpty message="No trades in report." />
          )}
        </>
      )}
    </div>
  );
}
