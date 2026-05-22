import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  BarChart2,
  Bell,
  Copy,
  ExternalLink,
  ListChecks,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useStore } from '@/hooks/useStore';
import { apiClient } from '@/lib/apiClient';
import { cn } from '@/lib/utils';
import { runnerBadgeClass, runnerBadgeLabel } from '@/lib/suggestedTradeRunnerDisplay';
import type {
  ScalpWorkbenchIntent,
  SuggestedTradesListResponse,
  SuggestedTradeWatch,
  TvChartIntent,
} from '@/types/athena';

const POLL_MS = 5000;
const FRONTEND_EVAL_MS = 8000;

function watchTriggerType(watch: SuggestedTradeWatch): string {
  return String(watch.trigger_type || watch.triggerType || '').toUpperCase();
}

function watchContextTf(watch: SuggestedTradeWatch): string | undefined {
  return watch.context_tf || watch.contextTf || watch.suggested_trade_plan?.contextTf;
}

function watchEntryTf(watch: SuggestedTradeWatch): string | undefined {
  return watch.entry_tf || watch.entryTf || watch.suggested_trade_plan?.entryTf;
}

function watchExecutionTf(watch: SuggestedTradeWatch): string | undefined {
  return watch.execution_tf || watch.executionTf || watch.suggested_trade_plan?.executionTf;
}

function watchCreatedAt(watch: SuggestedTradeWatch): string | undefined {
  return watch.created_at || watch.createdAt;
}

function watchExpiresAt(watch: SuggestedTradeWatch): string | null | undefined {
  return watch.expires_at ?? watch.expiresAt;
}

function watchReachedAt(watch: SuggestedTradeWatch): string | null | undefined {
  return watch.reached_at ?? watch.reachedAt;
}

function watchLastCheckedAt(watch: SuggestedTradeWatch): string | null | undefined {
  return watch.last_evaluated_at ?? watch.lastCheckedAt;
}

export function watchProgressText(watch: SuggestedTradeWatch): string {
  const status = String(watch.status || '').toUpperCase();
  const direction = String(watch.direction || '').toUpperCase();
  const trigger = watchTriggerType(watch);
  const level = watch.level;
  const zoneLow = watch.zone_low ?? watch.zoneLow;
  const zoneHigh = watch.zone_high ?? watch.zoneHigh;

  if (status === 'EXPIRED') return 'Expired';
  if (status === 'CANCELLED') return 'Cancelled';
  if (status === 'INVALIDATED') return 'Invalidated';
  if (status === 'READY_FOR_REVIEW' || status === 'LEVEL_REACHED') {
    return 'Level reached — review now';
  }
  if (trigger === 'ACCEPTANCE_ABOVE' && level != null) {
    return `Waiting for close above ${level}${direction ? ` (${direction})` : ''}`;
  }
  if (trigger === 'ACCEPTANCE_BELOW' && level != null) {
    return `Waiting for close below ${level}${direction ? ` (${direction})` : ''}`;
  }
  if (zoneLow != null && zoneHigh != null) {
    return `Waiting for pullback into zone ${zoneLow}-${zoneHigh}`;
  }
  if (watch.notes) return watch.notes;
  return 'Watching setup';
}

function sourceLabel(source: string | undefined): string {
  const key = String(source || '').toLowerCase();
  if (key.includes('scalp')) return 'Scalp Workbench';
  if (key.includes('ai_scalp')) return 'Scalp Review';
  if (key.includes('ai_chart')) return 'AI Chart Review';
  if (key.includes('tv')) return 'TV Chart';
  return source || 'Unknown';
}

function formatTs(value: string | null | undefined): string {
  if (!value) return '—';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
}

export default function SuggestedTradesPanel() {
  const {
    setActivePanel,
    setTvChartIntent,
    setScalpWorkbenchIntent,
    showToast,
  } = useStore();

  const [payload, setPayload] = useState<SuggestedTradesListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionWatchId, setActionWatchId] = useState<string | null>(null);

  const runner = payload?.runner;
  const watches = useMemo(
    () => (Array.isArray(payload?.watches) ? payload!.watches! : []),
    [payload],
  );
  const counts = payload?.counts || {};
  const needsFrontendEval = !runner?.active || runner?.mode === 'frontend_poll' || runner?.mode === 'manual_only';

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiClient.getJson('/api/suggested-trades') as SuggestedTradesListResponse;
      setPayload(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load suggested trades');
    } finally {
      setLoading(false);
    }
  }, []);

  const evaluateNow = useCallback(async () => {
    setEvaluating(true);
    setError(null);
    try {
      const res = await apiClient.postJson('/api/suggested-trades/evaluate-now', {}) as SuggestedTradesListResponse & {
        success?: boolean;
        error?: string;
      };
      if (res?.error && !res.ok) {
        setError(res.error);
      }
      setPayload(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Evaluate failed');
    } finally {
      setEvaluating(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!needsFrontendEval) return undefined;
    void evaluateNow();
    const timer = window.setInterval(() => {
      void evaluateNow();
    }, FRONTEND_EVAL_MS);
    return () => window.clearInterval(timer);
  }, [needsFrontendEval, evaluateNow]);

  const openInTvChart = useCallback((watch: SuggestedTradeWatch) => {
    const symbol = String(watch.symbol || '').toUpperCase();
    if (!symbol) {
      showToast('Watch has no symbol', 'error');
      return;
    }
    const intent: TvChartIntent = {
      id: `tv-watch-${watch.watch_id || symbol}-${Date.now()}`,
      source: 'manual',
      symbol,
      display: watch.display || symbol,
      signal: watch.signal,
      preferredTf: watchEntryTf(watch) || watchContextTf(watch) || 'H4',
      autoReview: false,
      createdAt: new Date().toISOString(),
    };
    setTvChartIntent(intent);
    setActivePanel('tvChart');
  }, [setActivePanel, setTvChartIntent, showToast]);

  const openInScalpWorkbench = useCallback((watch: SuggestedTradeWatch) => {
    const symbol = String(watch.symbol || '').toUpperCase();
    if (!symbol) {
      showToast('Watch has no symbol', 'error');
      return;
    }
    const intent: ScalpWorkbenchIntent = {
      id: `scalp-watch-${watch.watch_id || symbol}-${Date.now()}`,
      source: 'manual',
      symbol,
      signal: watch.signal,
      preferredTf: 'M5',
      autoReview: false,
      createdAt: new Date().toISOString(),
    };
    setScalpWorkbenchIntent(intent);
    setActivePanel('scalpWorkbench');
  }, [setActivePanel, setScalpWorkbenchIntent, showToast]);

  const cancelWatch = useCallback(async (watch: SuggestedTradeWatch) => {
    const watchId = watch.watch_id;
    if (!watchId) return;
    setActionWatchId(watchId);
    try {
      await apiClient.postJson(`/api/suggested-trades/${encodeURIComponent(watchId)}/cancel`, {});
      showToast('Watch cancelled', 'info');
      await refresh();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Cancel failed', 'error');
    } finally {
      setActionWatchId(null);
    }
  }, [refresh, showToast]);

  const copyWatchJson = useCallback(async (watch: SuggestedTradeWatch) => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(watch, null, 2));
      showToast('Watch JSON copied', 'success');
    } catch {
      showToast('Copy failed', 'error');
    }
  }, [showToast]);

  const isScalpSource = (watch: SuggestedTradeWatch) => {
    const src = String(watch.source || '').toLowerCase();
    return src.includes('scalp');
  };

  return (
    <Card className="h-full">
      <CardHeader className="space-y-3 pb-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <ListChecks className="h-5 w-5 text-primary" />
            Suggested Trades
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => void refresh()} disabled={loading}>
              <RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', loading && 'animate-spin')} />
              Refresh
            </Button>
            <Button size="sm" variant="secondary" className="h-8 text-xs" onClick={() => void evaluateNow()} disabled={evaluating}>
              <Activity className={cn('mr-1.5 h-3.5 w-3.5', evaluating && 'animate-pulse')} />
              Evaluate Now
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="text-[10px]">Active {counts.active ?? 0}</Badge>
          <Badge variant="outline" className="text-[10px] border-long/40 text-long">Ready {counts.ready ?? 0}</Badge>
          <Badge variant="outline" className="text-[10px]">Expired {counts.expired ?? 0}</Badge>
          <Badge variant="outline" className={cn('text-[10px]', runnerBadgeClass(runner))}>
            Runner: {runnerBadgeLabel(runner)}
          </Badge>
          <Badge variant="outline" className="text-[10px] border-primary/30 text-primary">
            Alert-only
          </Badge>
          {needsFrontendEval && (
            <Badge variant="outline" className="text-[10px] border-warning/40 text-warning">
              Runner inactive — using frontend evaluation
            </Badge>
          )}
        </div>

        <div className="grid gap-2 text-[11px] text-muted-foreground sm:grid-cols-2 xl:grid-cols-4">
          <div>Last tick: {formatTs(runner?.lastTickAt)}</div>
          <div>Poll interval: {runner?.pollSeconds ?? '—'}s</div>
          <div>Tick age: {runner?.lastTickAgeSeconds != null ? `${Math.round(runner.lastTickAgeSeconds)}s` : '—'}</div>
          <div>Mode: {runner?.mode || '—'}</div>
        </div>
        {runner?.error && (
          <div className="rounded-md border border-short/30 bg-short/10 px-3 py-2 text-xs text-short">
            Runner error: {runner.error}
          </div>
        )}
        {error && (
          <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            {error}
          </div>
        )}
      </CardHeader>

      <CardContent className="space-y-3">
        {watches.length === 0 && !loading && (
          <div className="rounded-md border border-dashed border-border/60 px-4 py-8 text-center text-sm text-muted-foreground">
            <Bell className="mx-auto mb-2 h-5 w-5 opacity-60" />
            No flagged setups yet. Use Flag / Watch Setup on TV Chart or Scalp Workbench after AI review.
          </div>
        )}

        {watches.map((watch) => {
          const status = String(watch.status || '').toUpperCase();
          const ready = status === 'READY_FOR_REVIEW' || status === 'LEVEL_REACHED';
          const cancelled = status === 'CANCELLED' || status === 'EXPIRED' || status === 'INVALIDATED';
          return (
            <div
              key={watch.watch_id || `${watch.symbol}-${watch.created_at}`}
              className={cn(
                'rounded-md border border-border/60 bg-card/50 p-3 space-y-3',
                ready && 'border-long/30 bg-long/5',
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-sm">{watch.display || watch.symbol}</span>
                    <Badge variant="outline" className="text-[10px]">{status || 'UNKNOWN'}</Badge>
                    <Badge variant="outline" className="text-[10px]">{sourceLabel(watch.source)}</Badge>
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground">{watchProgressText(watch)}</div>
                </div>
                <div className="text-[10px] text-muted-foreground text-right">
                  <div>{watch.direction} · {watch.action}</div>
                  <div>{watchTriggerType(watch)}</div>
                </div>
              </div>

              <div className="grid gap-1 text-[10px] text-muted-foreground sm:grid-cols-2 xl:grid-cols-4">
                <div>Context TF: {watchContextTf(watch) || '—'}</div>
                <div>Entry TF: {watchEntryTf(watch) || '—'}</div>
                <div>Execution TF: {watchExecutionTf(watch) || '—'}</div>
                <div>Level/Zone: {watch.level ?? `${watch.zone_low ?? watch.zoneLow ?? '—'}-${watch.zone_high ?? watch.zoneHigh ?? '—'}`}</div>
                <div>Created: {formatTs(watchCreatedAt(watch))}</div>
                <div>Expires: {formatTs(watchExpiresAt(watch))}</div>
                <div>Reached: {formatTs(watchReachedAt(watch))}</div>
                <div>Last checked: {formatTs(watchLastCheckedAt(watch))}</div>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" className="h-7 text-[10px]" onClick={() => openInTvChart(watch)}>
                  <ExternalLink className="mr-1 h-3 w-3" />
                  Open in TV Chart
                </Button>
                {isScalpSource(watch) && (
                  <Button size="sm" variant="outline" className="h-7 text-[10px]" onClick={() => openInScalpWorkbench(watch)}>
                    <BarChart2 className="mr-1 h-3 w-3" />
                    Open in Scalp Workbench
                  </Button>
                )}
                {!cancelled && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-[10px]"
                    disabled={actionWatchId === watch.watch_id}
                    onClick={() => void cancelWatch(watch)}
                  >
                    <XCircle className="mr-1 h-3 w-3" />
                    Cancel Watch
                  </Button>
                )}
                <Button size="sm" variant="ghost" className="h-7 text-[10px]" onClick={() => void copyWatchJson(watch)}>
                  <Copy className="mr-1 h-3 w-3" />
                  Copy watch JSON
                </Button>
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
