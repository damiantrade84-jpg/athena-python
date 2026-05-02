import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ErrorBanner, RefreshButton } from '@/components/shared';
import {
  Radio,
  Activity,
  Zap,
  Layers,
  GitMerge,
  TrendingUp,
  Eye,
  Play,
  AlertTriangle,
  Check,
  X,
  Info,
} from 'lucide-react';
import { cn, fmtNum, toNum } from '@/lib/utils';
import { fmtPrice } from '@/lib/athenaFormat';
import type {
  LdSnapshot,
  LdSymbolRow,
  LdEngineARow,
  LdEngineBRow,
  LdEngineCRow,
  LdEngineDRow,
  LdAiReview,
} from '@/types/athena';

const DEFAULT_SYMBOLS = 'EUR/USD,GBP/USD,XAU/USD,BTCUSDT,ETHUSDT,NVDA,AAPL,MSFT';
const POLL_MS = 5000;

export default function LiveCockpitPanel() {
  const { showToast } = useStore();
  const [symbolsInput, setSymbolsInput] = useState(DEFAULT_SYMBOLS);
  const [activeSymbols, setActiveSymbols] = useState(DEFAULT_SYMBOLS);
  const [tf, setTf] = useState<'H1' | 'H4' | 'D1'>('H4');
  const [autoPoll, setAutoPoll] = useState(true);
  const [filter, setFilter] = useState<string>('all');
  const [selected, setSelected] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<LdSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { post: postPaperExec, loading: papering } = useApiPost<{ ok?: boolean; error?: string; ticket?: string }>();

  const fetchSnap = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      const syms = activeSymbols.trim();
      if (syms) params.set('symbols', syms);
      params.set('timeframe', tf);
      const res = await fetch(`/api/live-dashboard/snapshot?${params.toString()}`);
      const data = (await res.json()) as LdSnapshot;
      if (!res.ok || data?.error) {
        setError(data?.error || `HTTP ${res.status}`);
      } else {
        setSnapshot(data);
        if (!selected && data.symbols && data.symbols.length > 0) {
          setSelected(data.symbols[0].symbol);
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [activeSymbols, tf, selected]);

  useEffect(() => {
    fetchSnap();
  }, [fetchSnap]);

  useEffect(() => {
    if (pollRef.current) clearTimeout(pollRef.current);
    if (!autoPoll) return;
    const tick = () => {
      fetchSnap();
      pollRef.current = setTimeout(tick, POLL_MS);
    };
    pollRef.current = setTimeout(tick, POLL_MS);
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [autoPoll, fetchSnap]);

  const symbols = snapshot?.symbols || [];
  const events = snapshot?.events || [];
  const conn = snapshot?.connections || {};
  const paperMode = snapshot?.paperMode || {};
  const filtered = useMemo(() => {
    return symbols.filter((s) => {
      if (filter === 'aligned') return s.engineC?.decisionState === 'ALIGNED';
      if (filter === 'watchlist') return s.finalState === 'WATCHLIST';
      if (filter === 'paper') return s.finalState === 'PAPER CANDIDATE';
      if (filter === 'blocked') return s.finalState === 'BLOCKED';
      return true;
    });
  }, [symbols, filter]);

  const selectedRow = symbols.find((s) => s.symbol === selected) || null;

  const onPaperExecute = useCallback(
    async (row: LdSymbolRow) => {
      const direction = row.engineA?.direction || row.engineB?.direction;
      if (!direction) {
        showToast('No direction available — Engine A/B did not produce a signal.', 'error');
        return;
      }
      const r = await postPaperExec('/api/live-dashboard/paper-execute', {
        symbol: row.symbol,
        direction,
        entry: row.levels?.entry,
        sl: row.levels?.sl,
        tp: row.levels?.tp,
        rr: row.levels?.rr,
      });
      if (r?.ok) showToast(`Paper execute logged for ${row.symbol}`, 'success');
      else showToast(`Paper execute blocked: ${r?.error || 'unknown'}`, 'error');
    },
    [postPaperExec, showToast],
  );

  return (
    <div className="flex flex-col h-[calc(100vh-120px)] gap-3">
      {/* Status bar */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 flex items-center gap-2 flex-wrap">
          <div className="flex items-center gap-1">
            <Radio className="w-4 h-4 text-primary" />
            <span className="text-sm font-semibold">Live Cockpit</span>
            <Badge variant="outline" className="text-[10px] ml-1">{snapshot?.payloadVersion || 'v3'}</Badge>
          </div>
          <Badge className={connBg(conn.mt5)}>MT5: {(conn.mt5 || 'unknown').toUpperCase()}</Badge>
          <Badge className={connBg(conn.binanceWs === 'live' ? 'connected' : conn.binanceWs)}>BINANCE: {(conn.binanceWs || 'unknown').toUpperCase()}</Badge>
          <Badge className={snapshot?.freshnessAllOk === false ? 'bg-warning/20 text-warning' : 'bg-long/20 text-long'}>
            {snapshot?.freshnessAllOk === false ? '⚠ FRESHNESS ISSUE' : '✓ FRESHNESS OK'}
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            Paper: {paperMode.enabled ? 'ENABLED' : 'OFF'} · Real orders: {paperMode.realOrdersAllowed ? 'ALLOWED' : 'BLOCKED'}
          </Badge>

          <div className="flex items-center gap-2 ml-auto">
            <span className="text-[10px] text-muted-foreground">Auto</span>
            <Switch checked={autoPoll} onCheckedChange={setAutoPoll} />
            <RefreshButton onClick={fetchSnap} loading={loading} />
          </div>
        </CardContent>
      </Card>

      {!paperMode.realOrdersAllowed && (
        <Card className="border-border/60 bg-muted/20">
          <CardContent className="p-3 text-[11px] text-muted-foreground leading-relaxed">
            <strong className="text-foreground">Real orders: BLOCKED</strong> is expected when <code className="text-[10px]">REAL_ORDERS_ALLOWED</code> is false (paper soak safety).
            Symbol cards showing <span className="font-mono text-foreground">BLOCKED</span> are separate: freshness gates, Engine C, or no setup — open a card for details, or use filter &quot;All states&quot; instead of &quot;Blocked&quot;.
          </CardContent>
        </Card>
      )}

      {/* Symbol input + filters */}
      <Card className="border-border/60 bg-card/50">
        <CardContent className="p-3 flex items-center gap-2 flex-wrap">
          <Input
            value={symbolsInput}
            onChange={(e) => setSymbolsInput(e.target.value)}
            onBlur={() => setActiveSymbols(symbolsInput)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') setActiveSymbols(symbolsInput);
            }}
            placeholder="Comma-separated symbols (e.g. EUR/USD,XAU/USD,BTCUSDT)"
            className="flex-1 min-w-[280px] h-8 text-xs font-mono"
          />
          <Select value={tf} onValueChange={(v) => setTf(v as 'H1' | 'H4' | 'D1')}>
            <SelectTrigger className="w-[80px] h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="H1">H1</SelectItem>
              <SelectItem value="H4">H4</SelectItem>
              <SelectItem value="D1">D1</SelectItem>
            </SelectContent>
          </Select>
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="w-[140px] h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All states</SelectItem>
              <SelectItem value="paper">Paper candidate</SelectItem>
              <SelectItem value="aligned">Engine C aligned</SelectItem>
              <SelectItem value="watchlist">Watchlist</SelectItem>
              <SelectItem value="blocked">Blocked</SelectItem>
            </SelectContent>
          </Select>
          <Button size="sm" className="h-8 text-xs" onClick={() => setActiveSymbols(symbolsInput)}>
            Apply
          </Button>
        </CardContent>
      </Card>

      {error && <ErrorBanner message={error} onRetry={fetchSnap} />}

      {/* Two-column layout */}
      <div className="flex-1 grid grid-cols-7 gap-3 overflow-hidden">
        {/* Left: card grid */}
        <div className="col-span-3 flex flex-col gap-2 overflow-hidden">
          <ScrollArea className="flex-1 pr-2">
            <div className="grid grid-cols-1 gap-2">
              {filtered.length === 0 ? (
                <Card className="border-border/60 bg-card/50">
                  <CardContent className="p-6 text-center text-xs text-muted-foreground">
                    {loading ? 'Loading…' : 'No symbols match this filter.'}
                  </CardContent>
                </Card>
              ) : (
                filtered.map((row) => (
                  <CockpitCard key={row.symbol} row={row} active={selected === row.symbol} onClick={() => setSelected(row.symbol)} />
                ))
              )}
            </div>
          </ScrollArea>
          {/* Event feed */}
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Activity className="w-3.5 h-3.5" /> Event Feed
              </CardTitle>
            </CardHeader>
            <CardContent className="p-3 pt-0">
              <ScrollArea className="h-[160px]">
                {events.length === 0 ? (
                  <p className="text-[11px] text-muted-foreground">No events yet.</p>
                ) : (
                  <div className="space-y-1">
                    {events
                      .slice()
                      .reverse()
                      .slice(0, 30)
                      .map((e, i) => (
                        <div
                          key={`${e.timestamp}-${i}`}
                          className={cn(
                            'p-2 rounded-md text-[10px] font-mono flex items-start gap-2',
                            e.severity === 'pass'
                              ? 'bg-long/10 text-long'
                              : e.severity === 'block'
                                ? 'bg-short/10 text-short'
                                : 'bg-warning/10 text-warning',
                          )}
                        >
                          <span className="opacity-70">{new Date(e.timestamp).toLocaleTimeString()}</span>
                          <span className="font-bold">{e.symbol}</span>
                          <span className="flex-1">{e.message}</span>
                        </div>
                      ))}
                  </div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        {/* Right: detail tabs */}
        <Card className="col-span-4 border-border/60 bg-card/50 flex flex-col overflow-hidden">
          {selectedRow ? (
            <CockpitDetail row={selectedRow} onPaperExecute={onPaperExecute} executing={papering} />
          ) : (
            <CardContent className="p-12 text-center text-muted-foreground">
              Select a symbol from the left.
            </CardContent>
          )}
        </Card>
      </div>
    </div>
  );
}

function connBg(state?: string): string {
  if (state === 'connected' || state === 'live') return 'bg-long/20 text-long';
  if (state === 'error') return 'bg-short/20 text-short';
  return 'bg-muted/40 text-muted-foreground';
}

function CockpitCard({ row, active, onClick }: { row: LdSymbolRow; active: boolean; onClick: () => void }) {
  const finalBg =
    row.finalState === 'PAPER CANDIDATE'
      ? 'bg-long/15 text-long'
      : row.finalState === 'WATCHLIST'
        ? 'bg-warning/15 text-warning'
        : row.finalState === 'BLOCKED'
          ? 'bg-short/15 text-short'
          : 'bg-muted/40 text-muted-foreground';
  const dir = row.engineA?.direction || row.engineB?.direction;
  const dirBg = dir === 'LONG' ? 'bg-long/20 text-long' : dir === 'SHORT' ? 'bg-short/20 text-short' : 'bg-muted/40 text-muted-foreground';
  return (
    <Card
      className={cn(
        'border-border/60 bg-card/50 hover:border-primary/30 transition-colors cursor-pointer',
        active && 'border-primary/60 ring-1 ring-primary/30',
      )}
      onClick={onClick}
    >
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-mono font-bold truncate">{row.symbol}</span>
            {dir && <Badge className={cn('text-[10px]', dirBg)}>{dir}</Badge>}
            {row.asset_type && (
              <Badge variant="outline" className="text-[9px] uppercase">
                {row.asset_type}
              </Badge>
            )}
          </div>
          <Badge className={cn('text-[10px]', finalBg)}>{row.finalState}</Badge>
        </div>

        <div className="grid grid-cols-3 gap-2 text-xs">
          <Tile label="Price" value={fmtPrice(row.latest_price, row.symbol, row.asset_type || undefined)} />
          <Tile
            label="Engine A"
            value={
              row.engineA?.score != null
                ? `${fmtNum(row.engineA.score, 2)} / ${fmtNum(row.engineA.maxScore, 2)}`
                : '—'
            }
            accent={row.engineA?.passed ? 'long' : 'muted'}
          />
          <Tile
            label="Engine B"
            value={row.engineB?.confidencePassed ? 'PASS' : row.engineB?.structuralVerdict || '—'}
            accent={row.engineB?.confidencePassed ? 'long' : 'muted'}
          />
        </div>

        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>
            Engine C:{' '}
            <span className="text-foreground font-mono">{row.engineC?.decisionState || '—'}</span>
            {row.engineC?.tier && <span className="ml-1">[{row.engineC.tier}]</span>}
          </span>
          <span>
            Engine D: <span className="text-foreground font-mono">{row.engineD?.gateResult || '—'}</span>
            {row.engineD?.grade && <span className="ml-1">[{row.engineD.grade}]</span>}
          </span>
        </div>

        {row.freshness?.gateDecision !== 'ALLOW' && (
          <div className="flex items-center gap-1 text-[10px] text-warning">
            <AlertTriangle className="w-3 h-3" />
            Freshness: {row.freshness?.consistencyStatus || row.freshness?.blockReason || 'BLOCK'}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CockpitDetail({
  row,
  onPaperExecute,
  executing,
}: {
  row: LdSymbolRow;
  onPaperExecute: (row: LdSymbolRow) => void;
  executing: boolean;
}) {
  return (
    <Tabs defaultValue="overview" className="flex-1 flex flex-col overflow-hidden">
      <CardHeader className="pb-1 shrink-0">
        <div className="flex items-center justify-between">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
            <Radio className="w-4 h-4 text-primary" /> {row.symbol}
            <Badge variant="outline" className="text-[10px]">
              {row.asset_type || '—'}
            </Badge>
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono">{fmtPrice(row.latest_price, row.symbol, row.asset_type || undefined)}</span>
            {row.spread != null && (
              <span className="text-[10px] text-muted-foreground">spread {fmtNum(row.spread, 4)}</span>
            )}
          </div>
        </div>
        <TabsList className="w-full justify-start mt-2">
          <TabsTrigger value="overview" className="text-[11px]">Overview</TabsTrigger>
          <TabsTrigger value="levels" className="text-[11px]">Levels</TabsTrigger>
          <TabsTrigger value="engineA" className="text-[11px]">Engine A</TabsTrigger>
          <TabsTrigger value="engineB" className="text-[11px]">Engine B</TabsTrigger>
          <TabsTrigger value="engineC" className="text-[11px]">Engine C</TabsTrigger>
          <TabsTrigger value="engineD" className="text-[11px]">Engine D</TabsTrigger>
          <TabsTrigger value="ai" className="text-[11px]">AI</TabsTrigger>
          <TabsTrigger value="execute" className="text-[11px]">Execute</TabsTrigger>
          <TabsTrigger value="diagnostics" className="text-[11px]">Diagnostics</TabsTrigger>
        </TabsList>
      </CardHeader>

      <ScrollArea className="flex-1 px-4 pb-4">
        <TabsContent value="overview" className="m-0 mt-2 space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Tile label="Final state" value={row.finalState} />
            <Tile label="Main reason" value={row.mainReason || '—'} />
            <Tile label="Bid" value={fmtPrice(row.bid, row.symbol, row.asset_type || undefined)} />
            <Tile label="Ask" value={fmtPrice(row.ask, row.symbol, row.asset_type || undefined)} />
            <Tile label="Change %" value={row.change_pct != null ? `${fmtNum(row.change_pct, 2)}%` : '—'} />
            <Tile label="Source" value={row.source || '—'} />
            <Tile label="Timeframe" value={row.timeframe} />
            <Tile label="Engine C state" value={row.engineC?.decisionState || '—'} />
          </div>

          <FreshnessCard freshness={row.freshness} />
        </TabsContent>

        <TabsContent value="levels" className="m-0 mt-2 space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <Tile label="Entry" value={fmtPrice(row.levels?.entry, row.symbol, row.asset_type || undefined)} />
            <Tile label="SL" value={fmtPrice(row.levels?.sl, row.symbol, row.asset_type || undefined)} accent="short" />
            <Tile label="TP1" value={fmtPrice(row.levels?.tp1 ?? row.levels?.tp, row.symbol, row.asset_type || undefined)} accent="long" />
            <Tile label="TP2" value={fmtPrice(row.levels?.tp2, row.symbol, row.asset_type || undefined)} accent="long" />
            <Tile label="R:R" value={fmtNum(row.levels?.rr, 2)} />
            <Tile label="Source" value={row.levels?.source || '—'} />
          </div>
          {row.engineD?.vp && (
            <div className="grid grid-cols-3 gap-2">
              <Tile label="VP VAH" value={fmtPrice(row.engineD.vp.vah, row.symbol, row.asset_type || undefined)} />
              <Tile label="VP POC" value={fmtPrice(row.engineD.vp.poc, row.symbol, row.asset_type || undefined)} />
              <Tile label="VP VAL" value={fmtPrice(row.engineD.vp.val, row.symbol, row.asset_type || undefined)} />
            </div>
          )}
        </TabsContent>

        <TabsContent value="engineA" className="m-0 mt-2">
          <EngineARowCard row={row.engineA} pair={row.symbol} type={row.asset_type || undefined} />
        </TabsContent>

        <TabsContent value="engineB" className="m-0 mt-2">
          <EngineBRowCard row={row.engineB} pair={row.symbol} type={row.asset_type || undefined} />
        </TabsContent>

        <TabsContent value="engineC" className="m-0 mt-2">
          <EngineCRowCard row={row.engineC} />
        </TabsContent>

        <TabsContent value="engineD" className="m-0 mt-2">
          <EngineDRowCard row={row.engineD} pair={row.symbol} type={row.asset_type || undefined} />
        </TabsContent>

        <TabsContent value="ai" className="m-0 mt-2">
          <AiReviewCard ai={row.aiReview} />
        </TabsContent>

        <TabsContent value="execute" className="m-0 mt-2 space-y-3">
          <ExecuteCard row={row} onPaperExecute={onPaperExecute} executing={executing} />
        </TabsContent>

        <TabsContent value="diagnostics" className="m-0 mt-2 space-y-3">
          <Card className="border-border/60 bg-card/50">
            <CardContent className="p-3 space-y-2">
              <p className="text-[10px] uppercase text-muted-foreground">Executable State</p>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <Detail label="Can paper execute" value={row.executableState?.canPaperExecute ? 'YES' : 'NO'} />
                <Detail label="Can real execute" value={row.executableState?.canRealExecute ? 'YES' : 'NO'} />
                <Detail label="Disabled reason" value={row.executableState?.disabledReason || '—'} />
                <Detail label="Risk status" value={row.executableState?.riskStatus || '—'} />
                <Detail label="Freshness status" value={row.executableState?.freshnessStatus || '—'} />
                <Detail label="Paper mode" value={row.executableState?.paperMode ? 'ON' : 'OFF'} />
              </div>
            </CardContent>
          </Card>
          <Card className="border-border/60 bg-card/50">
            <CardContent className="p-3 space-y-2">
              <p className="text-[10px] uppercase text-muted-foreground">Final State</p>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <Detail label="Final" value={row.finalState} />
                <Detail label="Main reason" value={row.mainReason || '—'} />
                <Detail label="Block reason" value={row.blockReason || '—'} />
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </ScrollArea>
    </Tabs>
  );
}

function FreshnessCard({ freshness }: { freshness: LdSymbolRow['freshness'] }) {
  if (!freshness) return null;
  const ok = freshness.gateDecision === 'ALLOW';
  return (
    <Card className={cn('border', ok ? 'border-long/40 bg-long/5' : 'border-warning/40 bg-warning/5')}>
      <CardContent className="p-3 space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            {ok ? <Check className="w-3.5 h-3.5 text-long" /> : <AlertTriangle className="w-3.5 h-3.5 text-warning" />}
            Data freshness
          </span>
          <Badge variant="outline" className="text-[10px]">
            {freshness.gateDecision || '—'}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs mt-1">
          <Detail label="Consistency" value={freshness.consistencyStatus || '—'} />
          <Detail label="Policy" value={freshness.policyStatus || '—'} />
          {freshness.blockReason && <Detail label="Block reason" value={freshness.blockReason} />}
        </div>
      </CardContent>
    </Card>
  );
}

function EngineARowCard({ row, pair, type }: { row: LdEngineARow; pair?: string; type?: string }) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            <Zap className="w-3.5 h-3.5 text-primary" /> Engine A
          </span>
          <Badge className={row.passed ? 'badge-long' : 'badge-short'}>{row.passed ? 'PASS' : 'FAIL'}</Badge>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail label="Score" value={`${fmtNum(row.score, 2)} / ${fmtNum(row.maxScore, 2)}`} />
          <Detail label="Threshold" value={fmtNum(row.threshold, 2)} />
          <Detail label="Direction" value={row.direction || '—'} />
          <Detail label="Trend" value={fmtNum(row.trendScore, 2)} />
          <Detail label="Momentum" value={fmtNum(row.momentumScore, 2)} />
          <Detail label="Addon" value={fmtNum(row.addonScore, 2)} />
          <Detail label="ADX" value={fmtNum(row.adxValue, 1)} />
          <Detail label="ADX gate" value={row.adxGate || '—'} />
          <Detail label="Session ×" value={fmtNum(row.sessionMultiplier, 2)} />
          <Detail label="Conviction" value={fmtNum(row.conviction, 2)} />
          <Detail label="Entry" value={fmtPrice(row.entry, pair, type)} />
          <Detail label="SL" value={fmtPrice(row.sl, pair, type)} accent="short" />
          <Detail label="TP" value={fmtPrice(row.tp ?? row.tp1, pair, type)} accent="long" />
          <Detail label="R:R" value={fmtNum(row.rr, 2)} />
        </div>
        {row.failReasons && row.failReasons.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {row.failReasons.slice(0, 8).map((r) => (
              <Badge key={r} variant="outline" className="text-[9px] text-warning border-warning/40">
                {r}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function EngineBRowCard({ row, pair, type }: { row: LdEngineBRow; pair?: string; type?: string }) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            <Layers className="w-3.5 h-3.5 text-primary" /> Engine B
          </span>
          <Badge className={row.confidencePassed ? 'badge-long' : 'badge-short'}>
            {row.confidencePassed ? 'PASS' : 'FAIL'}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Detail label="Score" value={`${fmtNum(row.score, 2)} / ${fmtNum(row.maxScore, 2)}`} />
          <Detail label="Threshold" value={fmtNum(row.threshold, 2)} />
          <Detail label="Direction" value={row.direction || '—'} />
          <Detail label="Verdict" value={row.structuralVerdict || '—'} />
          <Detail label="Data valid" value={row.structuralDataValid ? 'YES' : 'NO'} />
          <Detail label="No-trigger class" value={row.noTriggerClassification || '—'} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Gate label="Structure" ok={row.structure_ok} />
          <Gate label="Location" ok={row.location_ok} />
          <Gate label="Entry" ok={row.entry_ok} />
          <Gate label="Room / RR" ok={row.room_rr_ok} />
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail label="Entry" value={fmtPrice(row.entry, pair, type)} />
          <Detail label="SL" value={fmtPrice(row.sl, pair, type)} accent="short" />
          <Detail label="TP" value={fmtPrice(row.tp, pair, type)} accent="long" />
        </div>
        {row.hardFailReasons.length > 0 && (
          <ReasonList items={row.hardFailReasons} className="text-short bg-short/10" label="Hard fail" icon={<X className="w-3 h-3" />} />
        )}
        {row.softWarnings.length > 0 && (
          <ReasonList items={row.softWarnings} className="text-warning bg-warning/10" label="Soft warning" icon={<AlertTriangle className="w-3 h-3" />} />
        )}
        {row.diagnosticNotes.length > 0 && (
          <ReasonList items={row.diagnosticNotes} className="text-muted-foreground bg-muted/30" label="Diagnostic" icon={<Info className="w-3 h-3" />} />
        )}
      </CardContent>
    </Card>
  );
}

function EngineCRowCard({ row }: { row: LdEngineCRow }) {
  const stateBg =
    row.decisionState === 'ALIGNED'
      ? 'bg-long/20 text-long'
      : row.decisionState === 'CONFLICT' || row.decisionState === 'BLOCKED'
        ? 'bg-short/20 text-short'
        : row.decisionState === 'A_ONLY' || row.decisionState === 'B_ONLY' || row.decisionState === 'WATCHLIST'
          ? 'bg-warning/20 text-warning'
          : 'bg-muted/40 text-muted-foreground';
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            <GitMerge className="w-3.5 h-3.5 text-primary" /> Engine C Consensus
          </span>
          <Badge className={cn('text-[10px]', stateBg)}>{row.decisionState}</Badge>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail label="Consensus" value={row.consensusType || '—'} />
          <Detail label="Tier" value={row.tier || '—'} />
          <Detail label="Conviction" value={fmtNum(row.conviction, 2)} />
          <Detail label="Engine A score" value={fmtNum(row.engineAContribution, 2)} />
          <Detail label="Engine B score" value={fmtNum(row.engineBContribution, 2)} />
          <Detail label="B passed" value={row.engineBChecklistPassed ? 'YES' : 'NO'} />
          <Detail label="Trade" value={row.trade ? 'YES' : 'NO'} />
          <Detail label="Watchlist reason" value={row.watchlistReason || '—'} />
          <Detail label="Block reason" value={row.blockReason || '—'} />
        </div>
        {row.reason && <p className="text-[11px] text-muted-foreground">{row.reason}</p>}
      </CardContent>
    </Card>
  );
}

function EngineDRowCard({ row, pair, type }: { row: LdEngineDRow; pair?: string; type?: string }) {
  const gateBg =
    row.gateResult === 'PASS'
      ? 'bg-long/20 text-long'
      : row.gateResult === 'WATCHLIST'
        ? 'bg-warning/20 text-warning'
        : row.gateResult === 'BLOCKED'
          ? 'bg-short/20 text-short'
          : 'bg-muted/40 text-muted-foreground';
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            <TrendingUp className="w-3.5 h-3.5 text-primary" /> Engine D (Scalp Lab)
          </span>
          <div className="flex items-center gap-1">
            <Badge className={cn('text-[10px]', gateBg)}>{row.gateResult}</Badge>
            {row.grade && <Badge variant="outline" className="text-[10px]">Grade {row.grade}</Badge>}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail label="Score" value={fmtNum(row.score, 0)} />
          <Detail label="Setup" value={row.setupType || '—'} />
          <Detail label="Direction" value={row.direction || '—'} />
          <Detail label="Spread" value={fmtNum(row.spread, 2)} />
          <Detail label="R:R" value={fmtNum(row.rr, 2)} />
          <Detail label="CVD bias" value={row.cvdBias || '—'} />
          <Detail label="Absorption" value={row.absorptionDetected ? 'YES' : 'NO'} />
          <Detail label="VP VAH" value={fmtPrice(row.vp.vah, pair, type)} />
          <Detail label="VP POC" value={fmtPrice(row.vp.poc, pair, type)} />
          <Detail label="VP VAL" value={fmtPrice(row.vp.val, pair, type)} />
        </div>
        {row.failReasons.length > 0 && (
          <ReasonList items={row.failReasons} className="text-short bg-short/10" label="Fail" icon={<X className="w-3 h-3" />} />
        )}
        {row.missingData.length > 0 && (
          <ReasonList items={row.missingData} className="text-muted-foreground bg-muted/30" label="Missing data" icon={<Info className="w-3 h-3" />} />
        )}
      </CardContent>
    </Card>
  );
}

function AiReviewCard({ ai }: { ai: LdAiReview | undefined }) {
  if (!ai) return null;
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1">
            <Eye className="w-3.5 h-3.5 text-primary" /> AI Review
          </span>
          <Badge variant="outline" className="text-[10px]">
            {ai.reviewState || 'NOT_VERIFIED'}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Detail label="Confidence" value={ai.confidence != null ? `${(toNum(ai.confidence) * 100).toFixed(0)}%` : '—'} />
          <Detail label="Downgrade only" value={ai.downgradeOnly ? 'YES' : 'NO'} />
          <Detail label="Marcus Reid" value={ai.marcusReid ? 'present' : '—'} />
          <Detail label="Engine B AI" value={ai.engineBAI ? 'present' : '—'} />
          <Detail label="Signal Debate" value={ai.signalDebate ? 'present' : '—'} />
          <Detail label="Chart Vision" value={ai.chartVision ? 'present' : '—'} />
        </div>
        {Array.isArray(ai.contradictions) && ai.contradictions.length > 0 && (
          <ReasonList items={ai.contradictions} className="text-warning bg-warning/10" label="Contradictions" icon={<AlertTriangle className="w-3 h-3" />} />
        )}
        {Array.isArray(ai.missingInformation) && ai.missingInformation.length > 0 && (
          <ReasonList items={ai.missingInformation} className="text-muted-foreground bg-muted/30" label="Missing info" icon={<Info className="w-3 h-3" />} />
        )}
      </CardContent>
    </Card>
  );
}

function ExecuteCard({
  row,
  onPaperExecute,
  executing,
}: {
  row: LdSymbolRow;
  onPaperExecute: (row: LdSymbolRow) => void;
  executing: boolean;
}) {
  const canPaper = !!row.executableState?.canPaperExecute;
  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold flex items-center gap-2">
            <Play className="w-4 h-4 text-primary" /> Paper Execute
          </span>
          <Badge variant="outline" className="text-[10px]">
            {row.finalState}
          </Badge>
        </div>
        <p className="text-[11px] text-muted-foreground">
          Paper-only logger. Calls <code className="font-mono">/api/live-dashboard/paper-execute</code> which re-validates
          freshness, kill-switch, and paper-mode policy on the backend. Real broker orders are blocked.
        </p>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <Detail label="Direction" value={row.engineA?.direction || row.engineB?.direction || '—'} />
          <Detail label="Entry" value={fmtPrice(row.levels?.entry, row.symbol, row.asset_type || undefined)} />
          <Detail label="SL" value={fmtPrice(row.levels?.sl, row.symbol, row.asset_type || undefined)} accent="short" />
          <Detail label="TP" value={fmtPrice(row.levels?.tp ?? row.levels?.tp1, row.symbol, row.asset_type || undefined)} accent="long" />
          <Detail label="R:R" value={fmtNum(row.levels?.rr, 2)} />
          <Detail label="Source" value={row.levels?.source || '—'} />
        </div>
        {!canPaper && (
          <div className="text-[11px] text-warning flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> Blocked: {row.executableState?.disabledReason || 'unknown'}
          </div>
        )}
        <Button size="sm" onClick={() => onPaperExecute(row)} disabled={!canPaper || executing}>
          {executing ? 'Logging…' : 'Paper Execute'}
        </Button>
      </CardContent>
    </Card>
  );
}

function ReasonList({
  items,
  className,
  label,
  icon,
}: {
  items: string[];
  className: string;
  label: string;
  icon: React.ReactNode;
}) {
  return (
    <div className={cn('p-2 rounded-md', className)}>
      <div className="flex items-center gap-1 text-[10px] uppercase font-semibold mb-1">
        {icon} {label}
      </div>
      <ul className="text-[10px] font-mono leading-relaxed list-disc pl-4">
        {items.slice(0, 6).map((it) => (
          <li key={it}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

function Tile({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: 'long' | 'short' | 'muted';
}) {
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : 'text-foreground';
  return (
    <div className="p-2 rounded-md bg-muted/30">
      <p className="text-[10px] uppercase text-muted-foreground">{label}</p>
      <p className={cn('text-xs font-mono font-bold truncate', fg)}>{value}</p>
    </div>
  );
}

function Detail({ label, value, accent }: { label: string; value: string; accent?: 'long' | 'short' }) {
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : 'text-foreground';
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[10px] text-muted-foreground capitalize truncate">{label}</span>
      <span className={cn('text-xs font-mono', fg)}>{value}</span>
    </div>
  );
}

function Gate({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className={cn('flex items-center gap-2 p-2 rounded-md', ok ? 'bg-long/10 text-long' : 'bg-short/10 text-short')}>
      {ok ? <Check className="w-3.5 h-3.5" /> : <X className="w-3.5 h-3.5" />}
      <span className="text-xs font-medium flex-1">{label}</span>
      <span className="text-[10px] font-mono">{ok ? 'OK' : 'FAIL'}</span>
    </div>
  );
}
