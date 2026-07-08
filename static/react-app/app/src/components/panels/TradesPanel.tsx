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
import { ErrorBanner, SqnBadge, EquityAreaChart } from '@/components/shared';
import { X, AlertTriangle } from 'lucide-react';
import { fmtNum } from '@/lib/utils';
import { auditEngineTitle, formatAuditEngineLabel, resolveAuditEngine } from '@/lib/auditEngine';
import type { PerformanceMetrics, PerformanceEngineRow } from '@/types';

interface OpenTradesTimedResp {
  positions?: Record<string, unknown>[];
  audit_unresolved?: Record<string, unknown>[];
  audit_unresolved_count?: number;
  count?: number;
  error?: string;
}

// /api/score-decay — advisory-only re-scoring of live broker positions.
// Backend (athena.py _check_score_decay) already restricts this to pairs open
// at MT5/Bybit; keyed by pair display name.
interface RegimeShiftInfo {
  classification?: string;
  advisory?: string;
  confidence?: number;
}
interface ScoreDecayRow {
  engine?: string;
  decayPct?: number;
  directionFlip?: boolean;
  scoreNote?: string;
  currentDirection?: string;
  aiVerdict?: string;
  aiUrgency?: string;
  aiReasoning?: string;
  regimeShift?: RegimeShiftInfo;
}

type AnyPos = Record<string, unknown>;

const OPEN_TRADES_POLL_MS = 2000;
// Decay recomputes on a ~250s server cadence; 30s polling is ample.
const SCORE_DECAY_POLL_MS = 30000;

/** Normalize a pair label for cross-source matching (EUR/USD ↔ EURUSD). */
function normPair(v: unknown): string {
  return String(v ?? '').replace(/[/\s]/g, '').toUpperCase();
}

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

function str(v: unknown): string {
  if (v == null || v === '') return '—';
  return String(v);
}

function AutoTradeLogRow({ entry }: { entry: Record<string, unknown> }) {
  const providerUnavailable = entry.eventRiskProviderUnavailable === true;
  const effectiveAction = str(entry.eventRiskEffectiveAction);
  const showEventWarning =
    providerUnavailable || (effectiveAction !== '—' && effectiveAction.includes('provider_unavailable'));

  return (
    <div className="p-2 rounded-md bg-muted/30 text-xs space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono font-semibold">{str(entry.ts)}</span>
        <Badge variant="outline" className="text-[10px] h-5">{str(entry.grade)}</Badge>
        <span className="font-mono">{str(entry.pair)} {str(entry.direction)}</span>
        {entry.score != null ? (
          <span className="text-muted-foreground">score={str(entry.score)}</span>
        ) : null}
      </div>
      {(entry.failedStage || entry.blockReason) ? (
        <div className="font-mono text-short">
          {entry.failedStage ? `stage=${str(entry.failedStage)}` : null}
          {entry.failedStage && entry.blockReason ? ' · ' : null}
          {entry.blockReason ? `reason=${str(entry.blockReason)}` : null}
        </div>
      ) : null}
      {(entry.executionConvictionBase != null || entry.executionConvictionEffective != null) ? (
        <div className="text-muted-foreground font-mono">
          conviction base={str(entry.executionConvictionBase)} effective={str(entry.executionConvictionEffective)}
        </div>
      ) : null}
      {showEventWarning ? (
        <div className="font-mono text-amber-600 dark:text-amber-400">
          event risk: provider unavailable
          {effectiveAction !== '—' ? ` (${effectiveAction})` : ''}
        </div>
      ) : null}
      {entry.error_tag ? (
        <div className="font-mono text-muted-foreground break-all">{str(entry.error_tag)}</div>
      ) : null}
    </div>
  );
}

/** Advisory badge for a decaying open position. Advisory only — no execution. */
function DecayAdvisoryBadge({ info }: { info?: ScoreDecayRow }) {
  if (!info) return <span className="text-[10px] text-muted-foreground">—</span>;
  const decayPct = num(info.decayPct);
  const flip = info.directionFlip === true;
  const verdict = String(info.aiVerdict || '').toUpperCase();
  const regime = String(info.regimeShift?.classification || '').toUpperCase();

  const danger = flip || decayPct >= 40 || verdict === 'EXIT' || regime === 'FAKE';
  const warn = !danger && (decayPct >= 25 || verdict === 'WATCH');

  // Evaluated but nothing to flag — position looks healthy.
  if (!danger && !warn) {
    return <span className="text-[10px] text-muted-foreground">ok</span>;
  }

  const tone = danger ? 'text-short border-short/50' : 'text-warning border-warning/50';
  const label = flip ? 'FLIP' : decayPct >= 25 ? `▼${decayPct.toFixed(0)}%` : (verdict || 'WATCH');

  const tip = [
    info.scoreNote ? `Score: ${info.scoreNote}` : '',
    decayPct ? `Decay: ${decayPct.toFixed(0)}% from entry` : '',
    flip ? `Direction flip vs entry (now ${str(info.currentDirection)})` : '',
    verdict ? `AI verdict: ${verdict}${info.aiUrgency ? ` · ${info.aiUrgency}` : ''}` : '',
    info.aiReasoning ? String(info.aiReasoning) : '',
    regime ? `Regime: ${regime}${info.regimeShift?.advisory ? ` — ${info.regimeShift.advisory}` : ''}` : '',
  ].filter(Boolean).join('\n');

  return (
    <div className="flex flex-col items-end gap-0.5" title={tip}>
      <Badge variant="outline" className={`text-[10px] h-5 font-mono ${tone}`}>{label}</Badge>
      {verdict ? (
        <span className={`text-[9px] font-mono ${verdict === 'EXIT' ? 'text-short' : verdict === 'HOLD' ? 'text-long' : 'text-warning'}`}>
          AI {verdict}
        </span>
      ) : null}
    </div>
  );
}

export default function TradesPanel() {
  const { showToast } = useStore();
  const [activeTab, setActiveTab] = useState('open');
  const [closeTicket, setCloseTicket] = useState<string | null>(null);
  const [closePair, setClosePair] = useState<string>('');
  const [closeVolume, setCloseVolume] = useState<number>(0);
  const [closeExchange, setCloseExchange] = useState<string>('mt5');
  const [closeDirection, setCloseDirection] = useState<string>('LONG');
  const [auditOrphanTicket, setAuditOrphanTicket] = useState<string | null>(null);
  const [auditOrphanBulk, setAuditOrphanBulk] = useState(false);

  const { data: openTradesResp, loading: openLoading, error: openError, refresh: refreshOpen } = useApiPoll<OpenTradesTimedResp>('/api/open-trades-timed', OPEN_TRADES_POLL_MS);
  const { data: scoreDecayResp } = useApiPoll<Record<string, ScoreDecayRow>>('/api/score-decay', SCORE_DECAY_POLL_MS);
  const { data: performance, loading: perfLoading, error: perfError, refresh: refreshPerf } = useApiPoll<PerformanceMetrics>('/api/performance', 0);
  const { data: autoLog } = useApiPoll<unknown>('/api/auto-trade/log', 0);
  const { data: failedExecs } = useApiPoll<unknown>('/api/failed-executions', 0);

  const { post: postClose, loading: closing } = useApiPost<{ success: boolean; error?: string }>();
  const { post: postReconcileOrphan, loading: reconcilingOrphan } = useApiPost<{
    success?: boolean;
    closed?: number;
    error?: string;
  }>();

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
  // Decay/advisory keyed by normalized pair for row lookup.
  const decayByPair = useMemo<Record<string, ScoreDecayRow>>(() => {
    const out: Record<string, ScoreDecayRow> = {};
    if (scoreDecayResp && typeof scoreDecayResp === 'object') {
      for (const [k, v] of Object.entries(scoreDecayResp)) {
        if (v && typeof v === 'object') out[normPair(k)] = v as ScoreDecayRow;
      }
    }
    return out;
  }, [scoreDecayResp]);
  const unresolvedAudit = useMemo<AnyPos[]>(() => {
    if (!openTradesResp || Array.isArray(openTradesResp)) return [];
    return Array.isArray(openTradesResp.audit_unresolved) ? openTradesResp.audit_unresolved : [];
  }, [openTradesResp]);
  const auditOnlyRows = useMemo(
    () => unresolvedAudit.filter((row) => !row.broker_live && row.close_action_enabled !== false),
    [unresolvedAudit],
  );

  const handleReconcileAuditOrphan = useCallback(async () => {
    const payload = auditOrphanBulk
      ? { reconcile_all_audit_only: true }
      : auditOrphanTicket
        ? { ticket: auditOrphanTicket }
        : null;
    if (!payload) return;

    const result = await postReconcileOrphan('/api/audit/reconcile-orphan', payload);
    if (result?.success) {
      const closed = Number(result.closed ?? (auditOrphanBulk ? auditOnlyRows.length : 1));
      showToast(`Closed ${closed} audit row${closed === 1 ? '' : 's'} (scratch)`, 'success');
      refreshOpen();
      refreshPerf();
    } else {
      showToast(`Audit close failed: ${result?.error || 'Unknown'}`, 'error');
    }
    setAuditOrphanTicket(null);
    setAuditOrphanBulk(false);
  }, [
    auditOrphanBulk,
    auditOrphanTicket,
    auditOnlyRows.length,
    postReconcileOrphan,
    refreshOpen,
    refreshPerf,
    showToast,
  ]);

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
              <CardTitle className="text-xs font-semibold flex items-center justify-between uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
                <span>Open Positions</span>
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className="text-[10px]">{openTrades.length} live</Badge>
                  {unresolvedAudit.length > 0 && (
                    <Badge variant="outline" className="text-[10px] text-warning border-warning/50">
                      {unresolvedAudit.length} audit
                    </Badge>
                  )}
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[500px]">
                {openLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                  </div>
                ) : openTrades.length === 0 && unresolvedAudit.length === 0 ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">No open positions</div>
                ) : (
                  <div className="space-y-4">
                  {openTrades.length > 0 && (
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="text-[10px] uppercase">Symbol</TableHead>
                        <TableHead className="text-[10px] uppercase">Dir</TableHead>
                        <TableHead className="text-[10px] uppercase">Exch</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Volume</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Open</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">P&amp;L</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">P&amp;L %</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">SL / Close</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">TP</TableHead>
                        <TableHead className="text-[10px] uppercase">Style</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Advisory</TableHead>
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
                        // Prefer broker sub-cent precision; show more decimals when the
                        // 2-dp dollar value would collapse to $0.00 on tiny positions.
                        const pnlRaw = num(p.profit_raw ?? p.profit ?? p.pnl);
                        const pnlDecimals = pnlRaw !== 0 && Math.abs(pnlRaw) < 0.01 ? 4 : 2;
                        const pnlPctRaw = p.pnl_pct;
                        const pnlPct = pnlPctRaw == null ? null : num(pnlPctRaw);
                        const sl = num(p.sl);
                        const tp = num(p.tp);
                        const style = String(p.style || p.audit_engine || '—');
                        const slCloseLine = String(
                          (p as { sl_close_line?: string }).sl_close_line
                          || (sl > 0 ? `Closes ${fmtNum(sl, 5)}` : '—'),
                        );
                        const pctToSl = (p as { pct_to_sl?: number }).pct_to_sl;
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
                            <TableCell className={`text-xs font-mono font-bold text-right ${pnlRaw >= 0 ? 'text-long' : 'text-short'}`}>
                              {pnlRaw >= 0 ? '+' : ''}${fmtNum(pnlRaw, pnlDecimals)}
                            </TableCell>
                            <TableCell className={`text-xs font-mono text-right ${pnlPct == null ? 'text-muted-foreground' : pnlPct >= 0 ? 'text-long' : 'text-short'}`}>
                              {pnlPct == null ? '—' : `${pnlPct >= 0 ? '+' : ''}${fmtNum(pnlPct, 2)}%`}
                            </TableCell>
                            <TableCell className="text-xs font-mono text-right text-short">
                              <div>{fmtNum(sl, 5)}</div>
                              <div className="text-[9px] text-muted-foreground">
                                {pctToSl != null ? `${fmtNum(pctToSl, 2)}% to SL` : slCloseLine}
                              </div>
                            </TableCell>
                            <TableCell className="text-xs font-mono text-right text-long">{fmtNum(tp, 5)}</TableCell>
                            <TableCell className="text-[10px] font-mono text-muted-foreground">{style}</TableCell>
                            <TableCell className="text-right">
                              <DecayAdvisoryBadge info={decayByPair[normPair(pairLabel)]} />
                            </TableCell>
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
                  {unresolvedAudit.length > 0 && (
                    <div className="rounded-md border border-warning/30 bg-warning/5">
                      <div className="flex items-center gap-2 px-3 py-2 text-[10px] uppercase tracking-wider text-warning">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        Audit rows without exit price
                        {auditOnlyRows.length > 0 && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="ml-auto h-6 text-[10px] border-warning/40 text-warning hover:bg-warning/10"
                            onClick={() => setAuditOrphanBulk(true)}
                          >
                            Close all audit-only
                          </Button>
                        )}
                      </div>
                      <Table>
                        <TableHeader>
                          <TableRow className="hover:bg-transparent">
                            <TableHead className="text-[10px] uppercase">Symbol</TableHead>
                            <TableHead className="text-[10px] uppercase">Dir</TableHead>
                            <TableHead className="text-[10px] uppercase">Ticket</TableHead>
                            <TableHead className="text-[10px] uppercase">State</TableHead>
                            <TableHead className="text-[10px] uppercase text-right">Entry</TableHead>
                            <TableHead className="text-[10px] uppercase text-right">SL</TableHead>
                            <TableHead className="text-[10px] uppercase text-right">TP</TableHead>
                            <TableHead className="text-[10px] uppercase">Style</TableHead>
                            <TableHead className="text-[10px] uppercase text-right">Action</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {unresolvedAudit.map((row, idx) => {
                            const direction = String(row.direction || '');
                            const ticket = String(row.ticket || row.id || idx);
                            const canCloseAudit = !row.broker_live && row.close_action_enabled !== false;
                            return (
                              <TableRow key={`audit-${ticket}`}>
                                <TableCell className="text-xs font-mono">{String(row.pair || '-')}</TableCell>
                                <TableCell>
                                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                                    {direction || '-'}
                                  </span>
                                </TableCell>
                                <TableCell className="text-[10px] font-mono text-muted-foreground">{ticket}</TableCell>
                                <TableCell className="text-[10px] uppercase text-warning">
                                  {row.broker_live ? 'broker live' : 'audit only'}
                                </TableCell>
                                <TableCell className="text-xs font-mono text-right">{fmtNum(num(row.entry_price), 5)}</TableCell>
                                <TableCell className="text-xs font-mono text-right text-short">{fmtNum(num(row.sl), 5)}</TableCell>
                                <TableCell className="text-xs font-mono text-right text-long">{fmtNum(num(row.tp), 5)}</TableCell>
                                <TableCell className="text-[10px] font-mono text-muted-foreground">{String(row.style || row.engine || '-')}</TableCell>
                                <TableCell className="text-right">
                                  {canCloseAudit ? (
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      className="h-6 w-6 p-0"
                                      onClick={() => setAuditOrphanTicket(ticket)}
                                    >
                                      <X className="w-3 h-3 text-warning" />
                                    </Button>
                                  ) : (
                                    <span className="text-[10px] text-muted-foreground">—</span>
                                  )}
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                  </div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="history" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Last 20 Trade History</CardTitle>
            </CardHeader>
            <CardContent>
              {Array.isArray(performance?.last_20_trades) && performance!.last_20_trades.length > 0 ? (
                <ScrollArea className="h-[400px]">
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="text-[10px] uppercase">Pair</TableHead>
                        <TableHead className="text-[10px] uppercase">Dir</TableHead>
                        <TableHead className="text-[10px] uppercase">Engine</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Entry</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">Exit</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">P&amp;L</TableHead>
                        <TableHead className="text-[10px] uppercase text-right">R</TableHead>
                        <TableHead className="text-[10px] uppercase">Reason</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {performance!.last_20_trades.map((t, i) => {
                        const engineKey = resolveAuditEngine(t as Record<string, unknown>);
                        const direction = String(t.direction || '—');
                        return (
                        <TableRow key={i}>
                          <TableCell className="text-xs font-mono">{String(t.pair || '—')}</TableCell>
                          <TableCell>
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${direction === 'LONG' ? 'bg-long/20 text-long' : direction === 'SHORT' ? 'bg-short/20 text-short' : ''}`}>
                              {direction}
                            </span>
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant="outline"
                              className="text-[10px] h-5 font-mono"
                              title={auditEngineTitle(engineKey)}
                            >
                              {formatAuditEngineLabel(engineKey)}
                            </Badge>
                          </TableCell>
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
                        );
                      })}
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
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Equity Curve (Cumulative R)</CardTitle>
            </CardHeader>
            <CardContent>
              {perfLoading ? (
                <Skeleton className="h-[260px] w-full" />
              ) : equityData.length > 0 ? (
                <EquityAreaChart
                  data={equityData}
                  height={260}
                  valueLabel="Cumulative R"
                  valueFormatter={(v) => `${fmtNum(v, 2)}R`}
                  labelFormatter={(label) => `Trade #${label}`}
                  idSuffix="Trades"
                />
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No equity data — waiting for closed trades</div>
              )}
            </CardContent>
          </Card>

          {byEngineEntries.length > 0 && (
            <Card className="border-border/60 bg-card/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>By Engine</CardTitle>
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
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Auto-Trade Log</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[400px]">
                {autoLogList.length > 0 ? (
                  <div className="space-y-2">
                    {autoLogList.map((entry, i) => (
                      <AutoTradeLogRow key={i} entry={entry} />
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
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
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

      <AlertDialog
        open={!!auditOrphanTicket || auditOrphanBulk}
        onOpenChange={() => {
          setAuditOrphanTicket(null);
          setAuditOrphanBulk(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Close audit row</AlertDialogTitle>
            <AlertDialogDescription>
              {auditOrphanBulk
                ? `Close ${auditOnlyRows.length} audit-only row${auditOnlyRows.length === 1 ? '' : 's'} as scratch (exit=entry, pnl=0)? No broker positions will be touched.`
                : `Close audit row ${auditOrphanTicket} as scratch (exit=entry, pnl=0)? No broker position will be touched.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleReconcileAuditOrphan} disabled={reconcilingOrphan}>
              {reconcilingOrphan ? 'Closing...' : 'Confirm Close'}
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
