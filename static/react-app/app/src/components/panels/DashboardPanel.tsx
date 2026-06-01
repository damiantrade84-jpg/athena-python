import { useCallback, useMemo, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { useLivePrices } from '@/hooks/useLivePrices';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { StatCard, ErrorBanner } from '@/components/shared';
import {
  TrendingUp, TrendingDown, Activity, Zap, Target,
  Clock, Globe, AlertTriangle, BarChart3, Play, Square, X
} from 'lucide-react';
import { AreaChart, Area, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
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

  const guardianColor =
    guardianStatus?.overall === 'healthy' ? 'text-long' :
    guardianStatus?.overall === 'warning' ? 'text-warning' : 'text-short';

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
    <div className="space-y-5">
      {health?.paper_mode && (
        <div className="bg-warning/20 border border-warning/40 rounded-md px-4 py-2 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-warning" />
          <span className="text-xs font-medium text-warning">PAPER MODE — No real orders will be executed</span>
        </div>
      )}

      {(healthError || mt5Error || bybitError || guardianError || tradesError || lastScanError || perfError) && (
        <ErrorBanner
          message={[healthError, mt5Error, bybitError, guardianError, tradesError, lastScanError, perfError].filter(Boolean).join(' | ')}
          onRetry={() => { refreshHealth(); refreshMt5(); refreshBybit(); refreshGuardian(); refreshTrades(); refreshScan(); }}
        />
      )}

      {/* Top stats row */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard
          title="System Status"
          value={health?.status?.toUpperCase() || 'UNKNOWN'}
          icon={<Zap className="w-5 h-5 text-primary" />}
          valueClass={health?.status === 'ok' ? 'text-long' : 'text-warning'}
          loading={healthLoading}
          subtitle={
            health?.uptime_seconds != null
              ? `Uptime ${Math.floor((health.uptime_seconds || 0) / 60)}m · Scans ${health.scan_count ?? 0}`
              : `Pairs ${(health as { activePairs?: number; pairs?: number } | null)?.activePairs ?? (health as { pairs?: number } | null)?.pairs ?? 0}`
          }
        />
        <StatCard
          title="MT5 Equity"
          value={Number.isFinite(Number(mt5Equity)) ? `$${fmtNum(mt5Equity, 2)}` : 'N/A'}
          icon={mt5?.connected ? <TrendingUp className="w-5 h-5 text-long" /> : <TrendingDown className="w-5 h-5 text-short" />}
          valueClass={mt5?.connected ? 'text-long' : 'text-short'}
          loading={mt5Loading}
          subtitle={mt5?.connected
            ? (Number.isFinite(Number(mt5MarginLevel)) ? `Margin ${fmtNum(mt5MarginLevel, 1)}%` : 'Connected')
            : 'Disconnected'}
        />
        <StatCard
          title="Bybit USDT"
          value={Number.isFinite(Number(bybitBalance)) ? `$${fmtNum(bybitBalance, 2)}` : 'N/A'}
          icon={bybit?.connected ? <TrendingUp className="w-5 h-5 text-long" /> : <TrendingDown className="w-5 h-5 text-short" />}
          valueClass={bybit?.connected ? 'text-long' : 'text-short'}
          loading={bybitLoading}
          subtitle={bybit?.connected ? 'Connected' : 'Disconnected'}
        />
        <StatCard
          title="Guardian"
          value={guardianStatus?.overall?.toUpperCase() || 'UNKNOWN'}
          icon={<Activity className={`w-5 h-5 ${guardianColor}`} />}
          valueClass={guardianColor}
          loading={guardianLoading}
          subtitle={`Divergences ${guardianStatus?.divergence?.divergence_count ?? 0}`}
        />
      </div>

      {/* Performance summary row */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard
          title="Total P&L"
          value={`${totalRealized >= 0 ? '+' : ''}$${fmtNum(totalRealized, 2)}`}
          icon={<BarChart3 className="w-5 h-5 text-primary" />}
          valueClass={totalRealized >= 0 ? 'text-long' : 'text-short'}
          subtitle={`Trades ${performance?.total_trades ?? 0}`}
        />
        <StatCard
          title="Win Rate"
          value={winRatePct != null ? `${fmtNum(winRatePct, 1)}%` : 'N/A'}
          icon={<Target className="w-5 h-5 text-primary" />}
          valueClass="text-foreground"
          subtitle={`Wins ${performance?.win_count ?? 0} / Losses ${performance?.loss_count ?? 0}`}
        />
        <StatCard
          title="Profit Factor"
          value={profitFactor == null ? '—' : fmtNum(profitFactor, 2)}
          icon={<Activity className="w-5 h-5 text-primary" />}
          valueClass={profitFactor != null && profitFactor >= 1.2 ? 'text-long' : 'text-warning'}
          subtitle={performance?.sharpe == null ? '' : `Sharpe ${fmtNum(performance.sharpe, 2)}`}
        />
        <StatCard
          title="Live Open P&L"
          value={`${totalLiveProfit >= 0 ? '+' : ''}$${fmtNum(totalLiveProfit, 2)}`}
          icon={<TrendingUp className="w-5 h-5 text-primary" />}
          valueClass={totalLiveProfit >= 0 ? 'text-long' : 'text-short'}
          subtitle={`Live ${openTrades.length} / Audit ${unresolvedAudit.length}`}
        />
      </div>

      {/* Equity + sessions / controls */}
      <div className="grid grid-cols-3 gap-5">
        <Card className="col-span-2 border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <TrendingUp className="w-4 h-4 text-primary" /> Equity Curve
              </CardTitle>
              <Badge variant="outline" className="text-[10px]">{equityData.length} points</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {equityData.length === 0 ? (
              <div className="h-[260px] flex items-center justify-center text-muted-foreground text-xs">
                No closed trades yet
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={equityData} margin={{ left: 0, right: 0, top: 4, bottom: 0 }}>
                  <defs>
                    <linearGradient id="dashEquityFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.5} />
                      <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="idx" hide />
                  <YAxis domain={['auto', 'auto']} tick={{ fontSize: 10 }} width={50} />
                  <Tooltip
                    contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', fontSize: 11 }}
                    formatter={(v: number) => [`$${fmtNum(v, 2)}`, 'Equity']}
                  />
                  <Area type="monotone" dataKey="equity" stroke="hsl(var(--primary))" fill="url(#dashEquityFill)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Clock className="w-4 h-4 text-primary" /> Market Sessions (UTC)
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {SESSIONS.map(session => {
                const active = isSessionActive(session);
                return (
                  <div
                    key={session.name}
                    className={`flex items-center justify-between p-2 rounded-md ${active ? 'bg-long/15 border border-long/30' : 'bg-muted/30'}`}
                  >
                    <div className="flex items-center gap-2">
                      <Globe className={`w-3.5 h-3.5 ${active ? 'text-long' : 'text-muted-foreground'}`} />
                      <span className="text-xs font-medium">{session.name}</span>
                    </div>
                    <Badge variant={active ? 'default' : 'outline'} className="text-[9px]">
                      {active ? 'OPEN' : 'CLOSED'}
                    </Badge>
                  </div>
                );
              })}
            </CardContent>
          </Card>

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
                <Activity className="w-4 h-4 text-primary" /> Quick Controls
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Button
                className="w-full gap-2"
                variant={serverAutoTradeEnabled ? 'default' : 'outline'}
                size="sm"
                onClick={handleAutoTradeToggle}
                disabled={togglingAutoTrade}
              >
                {serverAutoTradeEnabled ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                {serverAutoTradeEnabled ? 'Stop Auto-Trade' : 'Start Auto-Trade'}
              </Button>
              {autoTrade && (
                <div className="space-y-1">
                  <div className="flex items-center justify-between text-[10px] text-muted-foreground px-1">
                    <span>Server Auto-Trade</span>
                    <Badge variant={autoTrade.enabled ? 'default' : 'outline'} className="text-[9px] h-4">
                      {autoTrade.enabled ? 'ON' : 'OFF'}
                    </Badge>
                  </div>
                  <p className="px-1 text-[9px] text-muted-foreground">
                    Autotrade volume: Minimum (broker min)
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Bottom row: signals + open positions + per-engine summary */}
      <div className="grid grid-cols-3 gap-5">
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
              <Target className="w-4 h-4 text-primary" /> Latest Signals
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[220px]">
              <div className="space-y-2">
                {recentSignals.length === 0 ? (
                  <div className="text-xs text-muted-foreground text-center py-8">
                    No recent signals — run a scan
                  </div>
                ) : (
                  recentSignals.map((sig, i) => {
                    const dir = String(sig.direction || '').toUpperCase();
                    const long = dir === 'LONG' || dir === 'BUY';
                    const score = sig.confluenceScore ?? sig.score;
                    const max = sig.maxScore ?? 3.0;
                    const label = String(sig.display || sig.pair || sig.symbol || `signal-${i}`);
                    const entryPrice = sig.price ?? sig.entry;
                    const livePrice = priceFor(sig);
                    return (
                      <div
                        key={`${label}-${i}`}
                        className="flex items-center justify-between p-2 rounded-md bg-muted/30"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${long ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                            {dir}
                          </span>
                          <span className="text-xs font-mono truncate">{label}</span>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="text-[10px] text-primary font-mono text-right">
                            L {livePrice ? fmtNum(livePrice, livePrice > 100 ? 2 : 5) : '—'}
                            <span className="block text-[9px] text-muted-foreground">
                              {fmtLiveQuoteMeta(ageSecFor(sig), sourceFor(sig))}
                            </span>
                          </span>
                          <span className="text-[10px] text-muted-foreground">
                            {fmtNum(score, 2)}/{fmtNum(max, 1)}
                          </span>
                          <Button
                            size="sm" variant="outline" className="h-6 text-[10px]"
                            onClick={() => runSignal(sig)}
                            disabled={executingSignal || !entryPrice}
                          >
                            Run
                          </Button>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
              <BarChart3 className="w-4 h-4 text-primary" /> Open Positions
              {unresolvedAudit.length > 0 && (
                <Badge variant="outline" className="ml-auto text-[10px] text-warning border-warning/50">
                  {unresolvedAudit.length} audit
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[220px]">
              <div className="space-y-2">
                {tradesLoading ? (
                  <Skeleton className="h-8 w-full" />
                ) : openTrades.length > 0 || unresolvedAudit.length > 0 ? (
                  <>
                  {openTrades.length > 0 && (
                  openTrades.map((trade, i) => {
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
                      <div key={ticketKey} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                        <div className="flex flex-col gap-0.5 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${t.direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                              {t.direction || '—'}
                            </span>
                            <span className="text-xs font-mono truncate">{label}</span>
                          </div>
                          {slLine && (
                            <span className="text-[9px] font-mono text-muted-foreground truncate" title={slLine}>
                              {slLine}
                            </span>
                          )}
                        </div>
                        <span className={`text-xs font-mono font-bold shrink-0 ${profit >= 0 ? 'text-long' : 'text-short'}`}>
                          {profit >= 0 ? '+' : ''}${fmtNum(profit, 2)}
                        </span>
                      </div>
                    );
                  })
                  )}
                  {unresolvedAudit.map((row, i) => {
                    const ticket = String(row.ticket || row.id || i);
                    const direction = String(row.direction || '');
                    const canCloseAudit = !row.broker_live && row.close_action_enabled !== false;
                    return (
                      <div key={`audit-${ticket}`} className="flex items-center justify-between p-2 rounded-md border border-warning/30 bg-warning/5">
                        <div className="flex items-center gap-2">
                          <AlertTriangle className="h-3.5 w-3.5 text-warning" />
                          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                            {direction || '-'}
                          </span>
                          <span className="text-xs font-mono">{String(row.pair || '-')}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] uppercase text-warning">
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
                              <X className="w-3 h-3 text-warning" />
                            </Button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                  </>
                ) : (
                  <div className="text-xs text-muted-foreground text-center py-8">No open positions</div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
              <BarChart3 className="w-4 h-4 text-primary" /> Performance by Engine
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[220px]">
              <div className="space-y-2">
                {(() => {
                  const byEngine = performance?.performance_by_engine ?? performance?.by_engine ?? {};
                  const rows = Object.entries(byEngine);
                  if (rows.length === 0) {
                    return <div className="text-xs text-muted-foreground text-center py-8">No closed trades yet</div>;
                  }
                  return rows.map(([engine, row]) => {
                    const r = row as Record<string, number | undefined>;
                    const wr = r.win_rate ?? r.wr;
                    const trades = r.trades ?? r.count;
                    const pnl = r.total_pnl ?? r.pnl;
                    return (
                      <div key={engine} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                        <span className="text-xs font-medium uppercase">{engine}</span>
                        <div className="flex items-center gap-2 text-[10px]">
                          <Badge variant="outline">{trades ?? 0} tr</Badge>
                          <Badge variant="outline">WR {fmtNum(wr, 1)}%</Badge>
                          <span className={`font-mono font-bold ${(pnl ?? 0) >= 0 ? 'text-long' : 'text-short'}`}>
                            ${fmtNum(pnl, 2)}
                          </span>
                        </div>
                      </div>
                    );
                  });
                })()}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
