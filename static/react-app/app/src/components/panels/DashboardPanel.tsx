import { useEffect, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { StatCard, ErrorBanner, SignalCard } from '@/components/shared';
import {
  TrendingUp, TrendingDown, Activity, Zap, Target,
  Clock, Globe, AlertTriangle, BarChart3, Play, Square
} from 'lucide-react';
import { format } from 'date-fns';
import type { HealthStatus, MT5Status, BybitStatus, GuardianApiStatus, OpenTrade } from '@/types';

export default function DashboardPanel() {
  const { signals, positions, isAutoTrade, toggleAutoTrade, executeSignal, showToast } = useStore();

  const { data: health, loading: healthLoading, error: healthError, refresh: refreshHealth } = useApiPoll<HealthStatus>('/api/health', 10000);
  const { data: mt5, loading: mt5Loading, error: mt5Error, refresh: refreshMt5 } = useApiPoll<MT5Status>('/api/mt5-status', 10000);
  const { data: bybit, loading: bybitLoading, error: bybitError, refresh: refreshBybit } = useApiPoll<BybitStatus>('/api/bybit-status', 10000);
  const { data: guardianStatus, loading: guardianLoading, error: guardianError, refresh: refreshGuardian } = useApiPoll<GuardianApiStatus>('/api/guardian/status', 10000);
  const { data: openTrades, loading: tradesLoading, error: tradesError, refresh: refreshTrades } = useApiPoll<OpenTrade[]>('/api/open-trades-timed', 10000);
  const { data: autoTrade, loading: autoLoading } = useApiPoll<{ enabled: boolean }>('/api/auto-trade', 10000);

  const activeSignals = signals.filter(s => s.status === 'active');
  const openPositions = positions.filter(p => p.status === 'open');
  const totalPnl = positions.reduce((sum, p) => sum + p.pnl, 0);

  const guardianColor =
    guardianStatus?.overall === 'healthy' ? 'text-long' :
    guardianStatus?.overall === 'warning' ? 'text-warning' : 'text-short';
  const guardianBg =
    guardianStatus?.overall === 'healthy' ? 'bg-long/15' :
    guardianStatus?.overall === 'warning' ? 'bg-warning/15' : 'bg-short/15';

  const pnlByPair = positions.slice(0, 6).map(p => ({
    pair: p.pair,
    pnl: p.pnl,
  }));

  return (
    <div className="space-y-5">
      {/* Paper Mode Banner */}
      {health?.paper_mode && (
        <div className="bg-warning/20 border border-warning/40 rounded-md px-4 py-2 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-warning" />
          <span className="text-xs font-medium text-warning">PAPER MODE — No real orders will be executed</span>
        </div>
      )}

      {/* Error Banner */}
      {(healthError || mt5Error || bybitError || guardianError || tradesError) && (
        <ErrorBanner
          message={[healthError, mt5Error, bybitError, guardianError, tradesError].filter(Boolean).join(' | ')}
          onRetry={() => { refreshHealth(); refreshMt5(); refreshBybit(); refreshGuardian(); refreshTrades(); }}
        />
      )}

      {/* Top Stats Row */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard
          title="System Status"
          value={health?.status?.toUpperCase() || 'UNKNOWN'}
          icon={<Zap className="w-5 h-5 text-primary" />}
          valueClass={health?.status === 'ok' ? 'text-long' : 'text-warning'}
          loading={healthLoading}
          subtitle={`Uptime: ${health ? Math.floor(health.uptime_seconds / 60) : 0}m | Scans: ${health?.scan_count || 0}`}
        />
        <StatCard
          title="MT5 Balance"
          value={mt5 ? `$${mt5.account_equity.toFixed(2)}` : 'N/A'}
          icon={mt5?.connected ? <TrendingUp className="w-5 h-5 text-long" /> : <TrendingDown className="w-5 h-5 text-short" />}
          valueClass={mt5?.connected ? 'text-long' : 'text-short'}
          loading={mt5Loading}
          subtitle={mt5?.connected ? `Margin: ${mt5.margin_level.toFixed(1)}%` : 'Disconnected'}
        />
        <StatCard
          title="Bybit Balance"
          value={bybit ? `$${bybit.balance_usdt.toFixed(2)}` : 'N/A'}
          icon={bybit?.connected ? <TrendingUp className="w-5 h-5 text-long" /> : <TrendingDown className="w-5 h-5 text-short" />}
          valueClass={bybit?.connected ? 'text-long' : 'text-short'}
          loading={bybitLoading}
          subtitle={bybit?.connected ? 'Connected' : 'Disconnected'}
        />
        <StatCard
          title="Guardian Overall"
          value={guardianStatus?.overall?.toUpperCase() || 'UNKNOWN'}
          icon={<Activity className={`w-5 h-5 ${guardianColor}`} />}
          valueClass={guardianColor}
          loading={guardianLoading}
          subtitle={`Divergences: ${guardianStatus?.divergence?.divergence_count || 0}`}
        />
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-3 gap-5">
        {/* Equity Curve */}
        <Card className="col-span-2 border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-primary" />
                Equity Curve
              </CardTitle>
              <Badge variant="outline" className="text-[10px]">Live</Badge>
            </div>
          </CardHeader>
          <CardContent>
            <div className="h-[260px] flex items-center justify-center text-muted-foreground text-xs">
              Equity curve data available in Performance panel
            </div>
          </CardContent>
        </Card>

        {/* Session & Controls */}
        <div className="space-y-4">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Clock className="w-4 h-4 text-primary" />
                Market Sessions
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {['London', 'New York', 'Tokyo', 'Sydney'].map(session => (
                <div key={session} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                  <div className="flex items-center gap-2">
                    <Globe className="w-3.5 h-3.5 text-muted-foreground" />
                    <span className="text-xs font-medium">{session}</span>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Activity className="w-4 h-4 text-primary" />
                Quick Controls
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Button
                className="w-full gap-2"
                variant={isAutoTrade ? 'default' : 'outline'}
                size="sm"
                onClick={toggleAutoTrade}
              >
                {isAutoTrade ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                {isAutoTrade ? 'Stop Auto-Trade' : 'Start Auto-Trade'}
              </Button>
              {autoTrade && (
                <div className="flex items-center justify-between text-[10px] text-muted-foreground px-1">
                  <span>Server Auto-Trade</span>
                  <Badge variant={autoTrade.enabled ? 'default' : 'outline'} className="text-[9px] h-4">
                    {autoTrade.enabled ? 'ON' : 'OFF'}
                  </Badge>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Bottom Row */}
      <div className="grid grid-cols-3 gap-5">
        {/* Latest Signals */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Target className="w-4 h-4 text-primary" />
              Latest Signals
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[200px]">
              <div className="space-y-2">
                {signals.slice(0, 5).map(signal => (
                  <SignalCard
                    key={signal.id}
                    signal={signal}
                    onExecute={(s) => executeSignal(s.id)}
                    compact
                  />
                ))}
                {signals.length === 0 && (
                  <div className="text-xs text-muted-foreground text-center py-8">No signals available</div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        {/* Open Positions Mini Table */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-primary" />
              Open Positions
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[200px]">
              <div className="space-y-2">
                {tradesLoading ? (
                  <Skeleton className="h-8 w-full" />
                ) : openTrades && openTrades.length > 0 ? (
                  openTrades.map(trade => (
                    <div key={trade.ticket} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                      <div className="flex items-center gap-2">
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${trade.direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                          {trade.direction}
                        </span>
                        <span className="text-xs font-mono">{trade.symbol}</span>
                      </div>
                      <span className={`text-xs font-mono font-bold ${trade.profit >= 0 ? 'text-long' : 'text-short'}`}>
                        {trade.profit >= 0 ? '+' : ''}${trade.profit.toFixed(2)}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-muted-foreground text-center py-8">No open positions</div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        {/* P&L by Pair */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-primary" />
              P&L by Pair
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {pnlByPair.map(entry => (
                <div key={entry.pair} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                  <span className="text-xs font-mono">{entry.pair}</span>
                  <span className={`text-xs font-mono font-bold ${entry.pnl >= 0 ? 'text-long' : 'text-short'}`}>
                    {entry.pnl >= 0 ? '+' : ''}${entry.pnl.toFixed(2)}
                  </span>
                </div>
              ))}
              {pnlByPair.length === 0 && (
                <div className="text-xs text-muted-foreground text-center py-8">No position data</div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
