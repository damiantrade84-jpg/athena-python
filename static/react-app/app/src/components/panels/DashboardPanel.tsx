import { useCallback, useMemo, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { useLivePrices } from '@/hooks/useLivePrices';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import {
  StatCard, ErrorBanner, EquityAreaChart,
  Panel, Empty, DirectionChip,
} from '@/components/shared';
import PrimeTimeCard from '@/components/athena/PrimeTimeCard';
import {
  TrendingUp, Activity, Target,
  AlertTriangle, BarChart3, Play, Square, X
} from 'lucide-react';
import { fmtNum, toNum } from '@/lib/utils';
import { fmtLiveQuoteMeta } from '@/lib/athenaFormat';
import type {
  HealthStatus, MT5Status, BybitStatus, GuardianApiStatus,
  OpenTrade, PerformanceMetrics
} from '@/types';
import type { EngineASignal } from '@/types/athena';
import { buildExecutionVolumePayload } from '@/lib/manualExecuteHelpers';

interface LastScanResponse {
  signals?: EngineASignal[];
  count?: number;
  scanTime?: string;
  generated_at?: string;
}

interface OpenTradesTimedResponse {
  positions?: OpenTrade[];
  audit_unresolved?: Record<string, unknown>[];
}

const OPEN_TRADES_POLL_MS = 2000;

const SESSIONS: { name: string; startUtc: number; endUtc: number; emoji?: string }[] = [
  { name: 'Sydney', startUtc: 22, endUtc: 7 },
  { name: 'Tokyo', startUtc: 0, endUtc: 9 },
  { name: 'London', startUtc: 7, endUtc: 16 },
  { name: 'New York', startUtc: 13, endUtc: 22 },
];

function isSessionActive(s: { startUtc: number; endUtc: number }): boolean {
  const h = new Date().getUTCHours();
  if (s.startUtc < s.endUtc) return h >= s.startUtc && h < s.endUtc;
  return h >= s.startUtc || h < s.endUtc;
}

function asArray<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === 'object' && Array.isArray((value as { positions?: unknown }).positions)) {
    return (value as { positions: T[] }).positions;
  }
  return [];
}

export default function DashboardPanel() {
  const { showToast } = useStore();

  const { data: health, loading: healthLoading, error: healthError, refresh: refreshHealth }
    = useApiPoll<HealthStatus>('/api/health', 15000);
  const { data: mt5, loading: mt5Loading, error: mt5Error, refresh: refreshMt5 }
    = useApiPoll<MT5Status>('/api/mt5-status', 15000);
  const { data: bybit, loading: bybitLoading, error: bybitError, refresh: refreshBybit }
    = useApiPoll<BybitStatus>('/api/bybit-status', 15000);
  const { data: guardianStatus, loading: guardianLoading, error: guardianError, refresh: refreshGuardian }
    = useApiPoll<GuardianApiStatus>('/api/guardian/status', 15000);
  const { data: openTradesRaw, loading: tradesLoading, error: tradesError, refresh: refreshTrades }
    = useApiPoll<OpenTrade[] | OpenTradesTimedResponse>('/api/open-trades-timed', OPEN_TRADES_POLL_MS);
  const { data: lastScan, error: lastScanError, refresh: refreshScan }
    = useApiPoll<LastScanResponse>('/api/last-scan?limit=6', 60000);
  const { data: performance, error: perfError }
    = useApiPoll<PerformanceMetrics>('/api/performance', 60000);
  const { data: autoTrade, refresh: refreshAutoTrade } = useApiPoll<{ enabled: boolean }>('/api/auto-trade', 15000);
  const { post: postQuickExecute, loading: executingSignal } = useApiPost<{ success?: boolean; ticket?: string; error?: string; approval?: { approved: boolean; reason: string } }>();
  const { post: postAutoTrade, loading: togglingAutoTrade } = useApiPost<{ enabled: boolean; error?: string }>();
  const { post: postReconcileOrphan } = useApiPost<{ success?: boolean; error?: string }>();
  const [reconcilingAuditTicket, setReconcilingAuditTicket] = useState<string | null>(null);
  const { priceFor, ageSecFor, sourceFor } = useLivePrices();

  const openTrades = asArray<OpenTrade>(openTradesRaw);
  const unresolvedAudit = openTradesRaw && !Array.isArray(openTradesRaw) && Array.isArray(openTradesRaw.audit_unresolved)
    ? openTradesRaw.audit_unresolved
    : [];
  const recentSignals = (lastScan?.signals || []).slice(0, 6);
  const serverAutoTradeEnabled = Boolean(autoTrade?.enabled);

  const handleDismissAuditOrphan = useCallback(async (ticket: string) => {
    if (!window.confirm(`Close audit row as scratch (exit=entry, pnl=0)? No broker position will be touched.`)) {
      return;
    }
    setReconcilingAuditTicket(ticket);
    const result = await postReconcileOrphan('/api/audit/reconcile-orphan', { ticket });
    setReconcilingAuditTicket(null);
    if (result?.success) {
      showToast('Audit row closed (scratch)', 'success');
      await refreshTrades();
    } else {
      showToast(`Audit close failed: ${result?.error || 'Unknown'}`, 'error');
    }
  }, [postReconcileOrphan, refreshTrades, showToast]);

  const handleAutoTradeToggle = useCallback(async () => {
    const action = serverAutoTradeEnabled ? 'off' : 'on';
    const result = await postAutoTrade('/api/auto-trade', { action });
    if (!result) {
      showToast('Auto-trade toggle failed: server did not confirm state', 'error');
      return;
    }
    await refreshAutoTrade();
    showToast(result.enabled ? 'Auto-trade enabled on server' : 'Auto-trade disabled on server', 'info');
  }, [postAutoTrade, refreshAutoTrade, serverAutoTradeEnabled, showToast]);

  const equityData = useMemo(() => {
    const ec = performance?.equity_curve;
    if (!Array.isArray(ec) || ec.length === 0) return [] as { idx: number; equity: number }[];
    if (typeof ec[0] === 'number') {
      return (ec as number[]).map((equity, idx) => ({ idx, equity }));
    }
    return (ec as { date?: string; equity: number }[]).map((p, idx) => ({
      idx,
      equity: typeof p === 'object' ? Number(p.equity) || 0 : Number(p) || 0,
    }));
  }, [performance?.equity_curve]);

  const mt5Equity = mt5?.account?.equity ?? mt5?.account_equity;
  const mt5Margin = mt5?.account?.margin;
  const mt5MarginLevel = mt5?.margin_level
    ?? (mt5Equity != null && mt5Margin != null && mt5Margin > 0 ? (mt5Equity / mt5Margin) * 100 : undefined);
  const bybitBalance = bybit?.account?.balance ?? bybit?.balance_usdt;

  const totalRealized = performance?.total_pnl ?? 0;
  const totalLiveProfit = openTrades.reduce((sum, t) => sum + toNum((t as OpenTrade & { profit?: number }).profit), 0);
  const winRatePct = performance?.win_rate;
  const profitFactor = performance?.profit_factor;

  const runSignal = useCallback(async (sig: EngineASignal) => {
    const pair = String(sig.pair || sig.display || sig.symbol || '').trim();
    if (!pair) {
      showToast('Execution failed: missing pair on signal', 'error');
      return;
    }
    const entryPrice = sig.price ?? sig.entry;
    if (!entryPrice) {
      showToast(`Execution failed: ${pair} has no live entry price`, 'error');
      return;
    }

    const style = String(sig.style || 'swing');
    const volumePayload = buildExecutionVolumePayload({ volumeMode: 'min_lot' });
    const payload = {
      signal: {
        ...sig,
        pair,
        display: sig.display || pair,
        price: entryPrice,
        style,
      },
      engine_b: sig.naked_data ?? sig.engine_b ?? {},
      pip_mode: style,
      ...volumePayload,
    };
    const result = await postQuickExecute('/api/quick-execute', payload as unknown as Record<string, unknown>);
    if (!result) {
      showToast(`Execution failed: no response for ${pair}`, 'error');
      return;
    }
    if (result.approval && !result.approval.approved) {
      showToast(`Rejected: ${result.approval.reason}`, 'error');
    } else if (result.success || result.ticket) {
      showToast(`Executed ${sig.direction || ''} ${pair}${result.ticket ? ` — ${result.ticket}` : ''}`, 'success');
      refreshTrades();
    } else {
      showToast(`Execution failed: ${result.error || 'unknown'}`, 'error');
    }
  }, [postQuickExecute, refreshTrades, showToast]);

  return (
    <div className="space-y-4">
      {health?.paper_mode && (
        <div className="flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />
          <span className="text-xs text-warning">
            Paper mode — no real orders will be executed
          </span>
        </div>
      )}

      {(healthError || mt5Error || bybitError || guardianError || tradesError || lastScanError || perfError) && (
        <ErrorBanner
          message={[healthError, mt5Error, bybitError, guardianError, tradesError, lastScanError, perfError].filter(Boolean).join(' | ')}
          onRetry={() => { refreshHealth(); refreshMt5(); refreshBybit(); refreshGuardian(); refreshTrades(); refreshScan(); }}
        />
      )}

      {/* ── Money first: the four numbers that decide whether to act ── */}
      <div className="grid grid-cols-4 gap-3">
        <StatCard
          title="Total P&L"
          value={`${totalRealized >= 0 ? '+' : ''}$${fmtNum(totalRealized, 2)}`}
          icon={<BarChart3 />}
          valueClass={totalRealized >= 0 ? 'text-long' : 'text-short'}
          subtitle={`${performance?.total_trades ?? 0} closed trades`}
        />
        <StatCard
          title="Live Open P&L"
          value={`${totalLiveProfit >= 0 ? '+' : ''}$${fmtNum(totalLiveProfit, 2)}`}
          icon={<TrendingUp />}
          valueClass={totalLiveProfit >= 0 ? 'text-long' : 'text-short'}
          subtitle={`${openTrades.length} open${unresolvedAudit.length ? ` · ${unresolvedAudit.length} audit` : ''}`}
        />
        <StatCard
          title="Win Rate"
          value={winRatePct != null ? `${fmtNum(winRatePct, 1)}%` : '—'}
          icon={<Target />}
          subtitle={`${performance?.win_count ?? 0}W / ${performance?.loss_count ?? 0}L`}
        />
        <StatCard
          title="Profit Factor"
          value={profitFactor == null ? '—' : fmtNum(profitFactor, 2)}
          icon={<Activity />}
          valueClass={profitFactor != null && profitFactor >= 1.2 ? 'text-long' : undefined}
          subtitle={performance?.sharpe == null ? undefined : `Sharpe ${fmtNum(performance.sharpe, 2)}`}
        />
      </div>

      {/* ── Infrastructure: a single status strip, not four KPI tiles.
             These are binary health facts; they do not deserve headline weight. ── */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-border bg-card px-4 py-2.5">
        <SystemStat
          label="System"
          value={health?.status?.toUpperCase() || 'UNKNOWN'}
          ok={health?.status === 'ok'}
          loading={healthLoading}
          meta={
            health?.uptime_seconds != null
              ? `up ${Math.floor((health.uptime_seconds || 0) / 60)}m · ${health.scan_count ?? 0} scans`
              : undefined
          }
        />
        <SystemStat
          label="MT5"
          value={Number.isFinite(Number(mt5Equity)) ? `$${fmtNum(mt5Equity, 2)}` : 'N/A'}
          ok={Boolean(mt5?.connected)}
          loading={mt5Loading}
          meta={
            mt5?.connected && Number.isFinite(Number(mt5MarginLevel))
              ? `margin ${fmtNum(mt5MarginLevel, 1)}%`
              : mt5?.connected ? 'connected' : 'disconnected'
          }
        />
        <SystemStat
          label="Bybit"
          value={Number.isFinite(Number(bybitBalance)) ? `$${fmtNum(bybitBalance, 2)}` : 'N/A'}
          ok={Boolean(bybit?.connected)}
          loading={bybitLoading}
          meta={bybit?.connected ? 'connected' : 'disconnected'}
        />
        <SystemStat
          label="Guardian"
          value={guardianStatus?.overall?.toUpperCase() || 'UNKNOWN'}
          ok={guardianStatus?.overall === 'healthy'}
          warn={guardianStatus?.overall === 'warning'}
          loading={guardianLoading}
          meta={`${guardianStatus?.divergence?.divergence_count ?? 0} divergences`}
        />
        <div className="ml-auto flex items-center gap-2">
          <span className="label">Auto-Trade</span>
          <Button
            size="sm"
            variant={serverAutoTradeEnabled ? 'default' : 'outline'}
            className="h-7 gap-1.5 text-[11px]"
            onClick={handleAutoTradeToggle}
            disabled={togglingAutoTrade}
          >
            {serverAutoTradeEnabled ? <Square className="h-3 w-3" /> : <Play className="h-3 w-3" />}
            {serverAutoTradeEnabled ? 'Stop' : 'Start'}
          </Button>
        </div>
      </div>

      {/* Prime execution windows (advisory liquidity map) */}
      <PrimeTimeCard />

      {/* ── Equity + sessions ── */}
      <div className="grid grid-cols-3 gap-4">
        <Panel
          className="col-span-2"
          title="Equity Curve"
          action={<span className="label">{equityData.length} points</span>}
        >
          {equityData.length === 0 ? (
            <Empty>No closed trades yet</Empty>
          ) : (
            <EquityAreaChart
              data={equityData}
              height={248}
              idSuffix="Dash"
              valueFormatter={(v) => `$${fmtNum(v, 2)}`}
              valueLabel="Equity"
            />
          )}
        </Panel>

        <Panel title="Market Sessions" action={<span className="label">UTC</span>} bodyClassName="p-0">
          <ul>
            {SESSIONS.map(session => {
              const active = isSessionActive(session);
              return (
                <li
                  key={session.name}
                  className="flex items-center justify-between border-b border-border px-3 py-2.5 last:border-b-0"
                >
                  <span className="flex items-center gap-2">
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${active ? 'bg-long' : 'bg-muted-foreground/30'}`}
                    />
                    <span className="text-xs text-foreground">{session.name}</span>
                  </span>
                  <span className={`label ${active ? 'text-long' : ''}`}>
                    {active ? 'Open' : 'Closed'}
                  </span>
                </li>
              );
            })}
          </ul>
        </Panel>
      </div>

      {/* ── Bottom row: signals · positions · per-engine ── */}
      <div className="grid grid-cols-3 gap-4">
        <Panel title="Latest Signals" bodyClassName="p-0">
          <ScrollArea className="h-[228px]">
            {recentSignals.length === 0 ? (
              <Empty>No recent signals — run a scan</Empty>
            ) : (
              <ul>
                {recentSignals.map((sig, i) => {
                  const score = sig.confluenceScore ?? sig.score;
                  const max = sig.maxScore ?? 3.0;
                  const label = String(sig.display || sig.pair || sig.symbol || `signal-${i}`);
                  const entryPrice = sig.price ?? sig.entry;
                  const livePrice = priceFor(sig);
                  return (
                    <li
                      key={`${label}-${i}`}
                      className="flex items-center justify-between gap-2 border-b border-border px-3 py-2 last:border-b-0"
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <DirectionChip direction={sig.direction} />
                        <span className="readout truncate text-xs">{label}</span>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        <span
                          className="readout text-[11px] text-muted-foreground"
                          title={fmtLiveQuoteMeta(ageSecFor(sig), sourceFor(sig))}
                        >
                          {livePrice ? fmtNum(livePrice, livePrice > 100 ? 2 : 5) : '—'}
                        </span>
                        <span className="readout text-[11px] text-muted-foreground">
                          {fmtNum(score, 2)}/{fmtNum(max, 1)}
                        </span>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-6 px-2 text-[10px]"
                          onClick={() => runSignal(sig)}
                          disabled={executingSignal || !entryPrice}
                        >
                          Run
                        </Button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </ScrollArea>
        </Panel>

        <Panel
          title="Open Positions"
          action={
            unresolvedAudit.length > 0
              ? <span className="label text-warning">{unresolvedAudit.length} audit</span>
              : undefined
          }
          bodyClassName="p-0"
        >
          <ScrollArea className="h-[228px]">
            {tradesLoading ? (
              <div className="p-3"><Skeleton className="h-8 w-full" /></div>
            ) : openTrades.length > 0 || unresolvedAudit.length > 0 ? (
              <ul>
                {openTrades.map((trade, i) => {
                  const t = trade as OpenTrade & {
                    pair?: string;
                    profit?: number;
                    sl_close_line?: string;
                    closes_at_sl?: number;
                    pct_to_sl?: number;
                    current_r?: number;
                  };
                  const label = t.symbol || t.pair || '—';
                  const profit = toNum(t.profit);
                  const ticketKey = t.ticket || `${label}-${i}`;
                  const slLine = t.sl_close_line
                    || (t.closes_at_sl
                      ? `SL ${fmtNum(t.closes_at_sl, 5)}${t.pct_to_sl != null ? ` · ${fmtNum(t.pct_to_sl, 2)}% to SL` : ''}`
                      : null);
                  return (
                    <li
                      key={ticketKey}
                      className="flex items-center justify-between gap-2 border-b border-border px-3 py-2 last:border-b-0"
                    >
                      <div className="flex min-w-0 flex-col gap-0.5">
                        <div className="flex items-center gap-2">
                          <DirectionChip direction={t.direction} />
                          <span className="readout truncate text-xs">{label}</span>
                        </div>
                        {slLine && (
                          <span className="truncate text-[10px] text-muted-foreground" title={slLine}>
                            {slLine}
                          </span>
                        )}
                      </div>
                      <span
                        className={`readout shrink-0 text-xs ${profit >= 0 ? 'text-long' : 'text-short'}`}
                      >
                        {profit >= 0 ? '+' : ''}${fmtNum(profit, 2)}
                      </span>
                    </li>
                  );
                })}
                {unresolvedAudit.map((row, i) => {
                  const ticket = String(row.ticket || row.id || i);
                  const direction = String(row.direction || '');
                  const canCloseAudit = !row.broker_live && row.close_action_enabled !== false;
                  return (
                    <li
                      key={`audit-${ticket}`}
                      className="flex items-center justify-between gap-2 border-b border-border bg-warning/5 px-3 py-2 last:border-b-0"
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />
                        <DirectionChip direction={direction} />
                        <span className="readout truncate text-xs">{String(row.pair || '-')}</span>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="label text-warning">
                          {row.broker_live ? 'broker live' : 'audit only'}
                        </span>
                        {canCloseAudit && (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 w-6 p-0"
                            disabled={reconcilingAuditTicket === ticket}
                            onClick={() => handleDismissAuditOrphan(ticket)}
                          >
                            <X className="h-3 w-3 text-warning" />
                          </Button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <Empty>No open positions</Empty>
            )}
          </ScrollArea>
        </Panel>

        <Panel title="Performance by Engine" bodyClassName="p-0">
          <ScrollArea className="h-[228px]">
            {(() => {
              const byEngine = performance?.performance_by_engine ?? performance?.by_engine ?? {};
              const rows = Object.entries(byEngine);
              if (rows.length === 0) return <Empty>No closed trades yet</Empty>;
              return (
                <ul>
                  {rows.map(([engine, row]) => {
                    const r = row as Record<string, number | undefined>;
                    const wr = r.win_rate ?? r.wr;
                    const trades = r.trades ?? r.count;
                    const pnl = r.total_pnl ?? r.pnl;
                    return (
                      <li
                        key={engine}
                        className="flex items-center justify-between gap-2 border-b border-border px-3 py-2 last:border-b-0"
                      >
                        <span className="truncate text-xs font-medium uppercase text-foreground">
                          {engine}
                        </span>
                        <div className="flex shrink-0 items-center gap-3">
                          <span className="readout text-[11px] text-muted-foreground">
                            {trades ?? 0} tr
                          </span>
                          <span className="readout text-[11px] text-muted-foreground">
                            WR {fmtNum(wr, 1)}%
                          </span>
                          <span
                            className={`readout text-xs ${(pnl ?? 0) >= 0 ? 'text-long' : 'text-short'}`}
                          >
                            ${fmtNum(pnl, 2)}
                          </span>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              );
            })()}
          </ScrollArea>
        </Panel>
      </div>
    </div>
  );
}

/**
 * One entry in the infrastructure status strip.
 *
 * A dot carries health so the value itself can stay neutral ink — four
 * coloured headline numbers for four binary connection facts was most of
 * what made the old dashboard feel loud.
 */
function SystemStat({
  label,
  value,
  ok,
  warn,
  meta,
  loading,
}: {
  label: string;
  value: string;
  ok?: boolean;
  warn?: boolean;
  meta?: string;
  loading?: boolean;
}) {
  const dot = ok ? 'bg-long' : warn ? 'bg-warning' : 'bg-short';
  return (
    <div className="flex items-center gap-2">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      <div className="min-w-0">
        <div className="label truncate">{label}</div>
        {loading ? (
          <Skeleton className="mt-0.5 h-4 w-16" />
        ) : (
          <div className="readout truncate text-xs text-foreground">
            {value}
            {meta && <span className="ml-1.5 text-[10px] text-muted-foreground">{meta}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
