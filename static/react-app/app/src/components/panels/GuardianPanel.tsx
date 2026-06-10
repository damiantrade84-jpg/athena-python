import { useState, useCallback } from 'react';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { ErrorBanner, RefreshButton } from '@/components/shared';
import { Shield, CheckCircle, XCircle, Clock, AlertTriangle, Activity, Wifi, RefreshCw } from 'lucide-react';

type GuardianOverall = 'healthy' | 'warning' | 'critical' | 'watch' | 'unknown';
type BootStatus = 'pass' | 'fail' | 'pending';

interface BootCheckRow {
  name: string;
  status: BootStatus;
  message: string;
  detail?: string;
}

interface BootCheckDetail {
  passed?: boolean | null;
  detail?: unknown;
  status?: string;
  message?: string;
}

interface BootCheckReport {
  passed?: boolean | null;
  failures?: string[];
  total_checks?: number;
  elapsed_ms?: number;
  checks?: Record<string, BootCheckDetail | string | boolean | null> | BootCheckRow[];
}

interface ShieldStatus {
  circuit_breaker_open?: boolean;
  failure_count?: number;
}

interface DivergenceEvent {
  name?: string;
  pair?: string;
  severity?: string;
  summary?: string;
  diverged?: boolean;
  divergence?: boolean;
  diff?: number;
  score_diff?: number;
  live_score?: number;
  bt_score?: number;
  live_direction?: string;
  bt_direction?: string;
  direction_match?: boolean | number;
  regime_match?: boolean | number;
}

interface DivergencePayload {
  divergence_count?: number;
  critical_count?: number;
  warning_count?: number;
  total_checks?: number;
  checks?: DivergenceEvent[];
  recent_events?: DivergenceEvent[];
}

interface FeedTimeframeMeta {
  source?: string;
  upstream?: string;
  lastBarIso?: string;
  lastBarTime?: string;
  last_update?: string;
  lastBarAgeSec?: number;
  freshness_seconds?: number;
  lastBarStale?: boolean;
  stalenessSeverity?: string;
  bucketLag?: number | null;
  hasCurrentBucket?: boolean;
  confirmedCount?: number;
  formingCount?: number;
}

interface FeedPair {
  pair?: string;
  symbol?: string;
  type?: string;
  source?: string;
  timeframes?: Record<string, FeedTimeframeMeta>;
}

interface FeedRow {
  label: string;
  source: string;
  lastUpdate: string;
  freshnessSeconds: number | null;
  status: string;
  detail?: string;
}

interface FeedHealth {
  feeds?: { source: string; last_update: string; freshness_seconds: number; status: string }[];
  activePairCount?: number;
  pairsWithCachedMeta?: number;
  mt5?: { available?: boolean; connected?: boolean; detail?: string };
  pairs?: FeedPair[];
}

interface ForensicView {
  name?: string;
  status?: string;
  score?: number;
  issues?: string[];
  metrics?: Record<string, unknown>;
}

interface ForensicsSummary {
  metrics?: Record<string, number>;
  views?: Record<string, ForensicView>;
  overall?: GuardianOverall;
  telegram_brief?: string[];
}

interface GuardianApiStatus {
  overall?: GuardianOverall;
  guardian?: BootCheckReport;
  shield?: ShieldStatus;
  divergence?: DivergencePayload;
  forensics?: ForensicsSummary;
}

const formatLabel = (value: string) =>
  value.replace(/[_-]/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());

const formatValue = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
};

const formatAge = (seconds: number | null | undefined): string => {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return 'n/a';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
};

const normalizeBootStatus = (status: unknown): BootStatus => {
  const raw = String(status ?? '').trim().toLowerCase();
  if (['pass', 'passed', 'ok', 'healthy', 'true'].includes(raw)) return 'pass';
  if (['fail', 'failed', 'error', 'critical', 'false'].includes(raw)) return 'fail';
  return 'pending';
};

const normalizeBootChecks = (report?: BootCheckReport | null): BootCheckRow[] => {
  if (!report?.checks) return [];

  if (Array.isArray(report.checks)) {
    return report.checks.map((check, index) => ({
      name: check.name || `Check ${index + 1}`,
      status: normalizeBootStatus(check.status),
      message: check.message || formatLabel(normalizeBootStatus(check.status)),
      detail: check.detail,
    }));
  }

  return Object.entries(report.checks).map(([name, raw]) => {
    const detail =
      raw && typeof raw === 'object'
        ? (raw as BootCheckDetail)
        : ({ passed: typeof raw === 'boolean' ? raw : undefined, detail: raw } satisfies BootCheckDetail);
    const status = detail.status ? normalizeBootStatus(detail.status) : normalizeBootStatus(detail.passed);
    const message = detail.message || (status === 'pass' ? 'Passed' : status === 'fail' ? 'Failed' : 'Pending');
    const detailText = formatValue(detail.detail);
    return {
      name: formatLabel(name),
      status,
      message,
      detail: detailText || undefined,
    };
  });
};

const normalizeDivergenceRows = (payload?: DivergencePayload | null): DivergenceEvent[] => {
  if (!payload) return [];
  if (Array.isArray(payload.checks) && payload.checks.length > 0) return payload.checks;
  if (Array.isArray(payload.recent_events)) return payload.recent_events;
  return [];
};

const normalizeFeedStatus = (meta: FeedTimeframeMeta): string => {
  const severity = String(meta.stalenessSeverity || '').trim().toLowerCase();
  if (meta.lastBarStale === true || ['stale', 'missing', 'critical'].includes(severity)) return 'stale';
  if (meta.lastBarStale === false || meta.hasCurrentBucket === true || ['fresh', 'ok'].includes(severity)) return 'ok';
  if (severity) return severity;
  return 'unknown';
};

const normalizeFeedRows = (feed?: FeedHealth | null): FeedRow[] => {
  if (!feed) return [];

  if (Array.isArray(feed.feeds) && feed.feeds.length > 0) {
    return feed.feeds.map((item) => ({
      label: item.source,
      source: item.source,
      lastUpdate: item.last_update || 'n/a',
      freshnessSeconds: item.freshness_seconds,
      status: item.status || 'unknown',
    }));
  }

  const rows: FeedRow[] = [];
  for (const pair of feed.pairs || []) {
    for (const [timeframe, meta] of Object.entries(pair.timeframes || {})) {
      const severity = meta.stalenessSeverity ? `severity=${meta.stalenessSeverity}` : '';
      const bucketLag = typeof meta.bucketLag === 'number' ? `bucket lag=${meta.bucketLag}` : '';
      rows.push({
        label: `${pair.pair || pair.symbol || 'Unknown'} ${timeframe}`,
        source: meta.source || meta.upstream || pair.source || pair.type || 'unknown',
        lastUpdate: meta.lastBarIso || meta.lastBarTime || meta.last_update || 'n/a',
        freshnessSeconds:
          typeof meta.lastBarAgeSec === 'number'
            ? meta.lastBarAgeSec
            : typeof meta.freshness_seconds === 'number'
              ? meta.freshness_seconds
              : null,
        status: normalizeFeedStatus(meta),
        detail: [severity, bucketLag].filter(Boolean).join(', ') || undefined,
      });
    }
  }

  if (rows.length === 0 && feed.mt5) {
    rows.push({
      label: 'MT5 Terminal',
      source: 'mt5',
      lastUpdate: 'n/a',
      freshnessSeconds: null,
      status: feed.mt5.connected ? 'ok' : 'warning',
      detail: feed.mt5.detail,
    });
  }

  return rows;
};

const normalizeForensicViews = (forensics?: ForensicsSummary | null): ForensicView[] => {
  if (!forensics) return [];
  if (forensics.views) return Object.values(forensics.views);
  if (forensics.metrics) {
    return Object.entries(forensics.metrics).map(([key, value]) => ({
      name: key,
      status: 'healthy',
      score: value,
      metrics: { value },
    }));
  }
  return [];
};

const badgeVariantForStatus = (status: unknown): 'default' | 'destructive' | 'outline' => {
  const raw = String(status ?? '').trim().toLowerCase();
  if (['ok', 'healthy', 'fresh', 'closed', 'pass', 'passed', 'success'].includes(raw)) return 'default';
  if (['critical', 'open', 'fail', 'failed', 'stale', 'error'].includes(raw)) return 'destructive';
  return 'outline';
};

export default function GuardianPanel() {
  const [confirmReset, setConfirmReset] = useState(false);

  const {
    data: status,
    loading: statusLoading,
    isRefreshing: statusRefreshing,
    error: statusError,
    refresh: refreshStatus,
  } = useApiPoll<GuardianApiStatus>('/api/guardian/status', 30000);
  const {
    data: bootCheck,
    loading: bootLoading,
    isRefreshing: bootRefreshing,
    error: bootError,
    refresh: refreshBoot,
  } = useApiPoll<BootCheckReport>('/api/guardian/boot-check', 0);
  const {
    data: divergence,
    isRefreshing: divergenceRefreshing,
    refresh: refreshDivergence,
  } = useApiPoll<DivergencePayload>('/api/divergence', 30000);
  const {
    data: shieldStatus,
    isRefreshing: shieldRefreshing,
    refresh: refreshShield,
  } = useApiPoll<ShieldStatus>('/api/shield/status', 30000);
  const {
    data: feedHealth,
    loading: feedLoading,
    isRefreshing: feedRefreshing,
    error: feedError,
    refresh: refreshFeed,
  } = useApiPoll<FeedHealth>('/api/feed-health', 30000);
  const {
    data: forensics,
    loading: forensicsLoading,
    isRefreshing: forensicsRefreshing,
    refresh: refreshForensics,
  } = useApiPoll<ForensicsSummary>('/api/forensics/summary', 0);
  const { data: diagnostics } = useApiPoll<Record<string, unknown>>('/api/live-feed-diagnostics', 0);

  const { post: postReset, loading: resetting } = useApiPost<{ success: boolean }>();

  const refreshAll = useCallback(() => {
    refreshStatus();
    refreshBoot();
    refreshDivergence();
    refreshShield();
    refreshFeed();
    refreshForensics();
  }, [refreshStatus, refreshBoot, refreshDivergence, refreshShield, refreshFeed, refreshForensics]);

  const panelRefreshing =
    statusRefreshing
    || bootRefreshing
    || divergenceRefreshing
    || shieldRefreshing
    || feedRefreshing
    || forensicsRefreshing;

  const handleReset = useCallback(async () => {
    const res = await postReset('/api/shield/reset');
    if (res?.success) {
      refreshAll();
    }
    setConfirmReset(false);
  }, [postReset, refreshAll]);

  const overall = status?.overall || forensics?.overall || 'unknown';
  const bannerColor = overall === 'healthy' ? 'bg-long/20 border-long/40 text-long' :
    overall === 'critical' ? 'bg-short/20 border-short/40 text-short' :
    overall === 'warning' || overall === 'watch' ? 'bg-warning/20 border-warning/40 text-warning' :
    'bg-muted/20 border-border/60 text-muted-foreground';

  const shield = shieldStatus || status?.shield || {};
  const directBootChecks = normalizeBootChecks(bootCheck);
  const checks = directBootChecks.length > 0 ? directBootChecks : normalizeBootChecks(status?.guardian);
  const directDivergenceRows = normalizeDivergenceRows(divergence);
  const divergencePayload =
    divergence && ((divergence.total_checks ?? divergence.divergence_count ?? directDivergenceRows.length) !== undefined)
      ? divergence
      : status?.divergence;
  const divergenceRows = normalizeDivergenceRows(divergencePayload);
  const feedRows = normalizeFeedRows(feedHealth);
  const directForensicViews = normalizeForensicViews(forensics);
  const forensicsPayload =
    directForensicViews.length > 0 || (forensics?.telegram_brief?.length || 0) > 0
      ? forensics
      : status?.forensics;
  const forensicViews = normalizeForensicViews(forensicsPayload);

  return (
    <div className="space-y-5">
      {(statusError || bootError || feedError) && (
        <ErrorBanner
          message={[statusError, bootError, feedError].filter(Boolean).join(' | ')}
          onRetry={refreshAll}
        />
      )}

      {/* Overall Health Banner */}
      <div className={`p-4 rounded-md border flex items-center justify-between ${bannerColor}`}>
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5" />
          <span className="text-sm font-bold uppercase">Guardian Overall: {overall}</span>
        </div>
        <RefreshButton
          onClick={refreshAll}
          loading={panelRefreshing || statusLoading || bootLoading}
        />
      </div>

      <div className="grid grid-cols-2 gap-5">
        {/* Boot Checks */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
              <CheckCircle className="w-4 h-4 text-primary" />
              Boot Checks
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[300px]">
              {bootLoading || statusLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                </div>
              ) : checks.length > 0 ? (
                <div className="space-y-2">
                  {checks.map((check, i) => (
                    <div key={i} className="flex items-start gap-2 p-2 rounded-md bg-muted/30">
                      {check.status === 'pass' ? <CheckCircle className="w-4 h-4 text-long shrink-0 mt-0.5" /> :
                       check.status === 'fail' ? <XCircle className="w-4 h-4 text-short shrink-0 mt-0.5" /> :
                       <Clock className="w-4 h-4 text-warning shrink-0 mt-0.5" />}
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-medium">{check.name}</p>
                        <p className="text-[10px] text-muted-foreground">{check.message}</p>
                        {check.detail && <p className="text-[10px] text-muted-foreground mt-0.5">{check.detail}</p>}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No boot check data</div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>

        {/* Circuit Breaker */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
              <AlertTriangle className="w-4 h-4 text-primary" />
              Circuit Breaker
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between p-3 rounded-md bg-muted/30">
              <span className="text-xs">Status</span>
              <Badge variant={shield.circuit_breaker_open ? 'destructive' : 'default'} className="text-[10px]">
                {shield.circuit_breaker_open ? 'OPEN' : 'CLOSED'}
              </Badge>
            </div>
            <div className="flex items-center justify-between p-3 rounded-md bg-muted/30">
              <span className="text-xs">Failure Count</span>
              <span className="text-xs font-mono font-bold">{shield.failure_count || 0}</span>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full gap-1 text-xs"
              onClick={() => setConfirmReset(true)}
              disabled={!shield.circuit_breaker_open}
            >
              <RefreshCw className="w-3 h-3" />
              Reset Circuit Breaker
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Divergence Monitor */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
            <Activity className="w-4 h-4 text-primary" />
            Divergence Monitor
          </CardTitle>
          <RefreshButton onClick={refreshAll} loading={panelRefreshing} />
        </CardHeader>
        <CardContent>
          {divergencePayload ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <Badge variant="outline" className="text-[10px]">Checks: {divergencePayload.total_checks || 0}</Badge>
                <Badge variant="outline" className="text-[10px]">Divergences: {divergencePayload.divergence_count || 0}</Badge>
                <Badge variant={(divergencePayload.critical_count || 0) > 0 ? 'destructive' : 'outline'} className="text-[10px]">Critical: {divergencePayload.critical_count || 0}</Badge>
                <Badge variant={(divergencePayload.warning_count || 0) > 0 ? 'outline' : 'default'} className="text-[10px]">Warnings: {divergencePayload.warning_count || 0}</Badge>
              </div>
              {divergenceRows.length > 0 ? (
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border/40">
                      <th className="text-[10px] uppercase py-2 text-muted-foreground">Pair</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground">Severity</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Diff</th>
                    </tr>
                  </thead>
                  <tbody>
                    {divergenceRows.map((c, i) => (
                      <tr key={i} className="border-b border-border/20">
                        <td className="py-2">
                          <div className="text-xs font-medium">{c.pair || c.name || 'Unknown'}</div>
                          {c.summary && <div className="text-[10px] text-muted-foreground">{c.summary}</div>}
                        </td>
                        <td className="py-2">
                          <Badge variant={badgeVariantForStatus(c.severity || (c.diverged || c.divergence ? 'critical' : 'ok'))} className="text-[10px]">
                            {c.severity || (c.diverged || c.divergence ? 'diverged' : 'ok')}
                          </Badge>
                        </td>
                        <td className="py-2 text-xs font-mono text-right">
                          {typeof c.score_diff === 'number'
                            ? c.score_diff.toFixed(3)
                            : typeof c.diff === 'number'
                              ? c.diff.toFixed(3)
                              : '0.000'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="text-center text-muted-foreground py-8 text-sm">No recent divergence events</div>
              )}
            </div>
          ) : (
            <div className="text-center text-muted-foreground py-8 text-sm">No divergence data</div>
          )}
        </CardContent>
      </Card>

      {/* Feed Health */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
            <Wifi className="w-4 h-4 text-primary" />
            Feed Health
          </CardTitle>
        </CardHeader>
        <CardContent>
          {feedLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : feedRows.length > 0 ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <Badge variant="outline" className="text-[10px]">Active Pairs: {feedHealth?.activePairCount ?? feedRows.length}</Badge>
                <Badge variant="outline" className="text-[10px]">Cached Meta: {feedHealth?.pairsWithCachedMeta ?? 'n/a'}</Badge>
                {feedHealth?.mt5 && (
                  <Badge variant={feedHealth.mt5.connected ? 'default' : 'outline'} className="text-[10px]">
                    MT5: {feedHealth.mt5.detail || (feedHealth.mt5.connected ? 'connected' : 'not connected')}
                  </Badge>
                )}
              </div>
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-border/40">
                    <th className="text-[10px] uppercase py-2 text-muted-foreground">Feed</th>
                    <th className="text-[10px] uppercase py-2 text-muted-foreground">Source</th>
                    <th className="text-[10px] uppercase py-2 text-muted-foreground">Last Update</th>
                    <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Freshness</th>
                    <th className="text-[10px] uppercase py-2 text-muted-foreground">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {feedRows.map((f, i) => (
                    <tr key={i} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                      <td className="py-2">
                        <div className="text-xs font-mono">{f.label}</div>
                        {f.detail && <div className="text-[10px] text-muted-foreground">{f.detail}</div>}
                      </td>
                      <td className="py-2 text-xs font-mono">{f.source}</td>
                      <td className="py-2 text-[10px] font-mono text-muted-foreground">{f.lastUpdate}</td>
                      <td className="py-2 text-xs font-mono text-right">{formatAge(f.freshnessSeconds)}</td>
                      <td className="py-2">
                        <Badge variant={badgeVariantForStatus(f.status)} className="text-[10px]">{f.status}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center text-muted-foreground py-8 text-sm">No feed health data</div>
          )}
        </CardContent>
      </Card>

      {/* Forensics */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Forensics Summary</CardTitle>
        </CardHeader>
        <CardContent>
          {forensicsLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : forensicViews.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {forensicViews.map((view, index) => (
                <div key={view.name || index} className="p-3 rounded-md bg-muted/30 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-[10px] uppercase text-muted-foreground">{formatLabel(view.name || `View ${index + 1}`)}</p>
                    <Badge variant={badgeVariantForStatus(view.status)} className="text-[10px]">{view.status || 'unknown'}</Badge>
                  </div>
                  {typeof view.score === 'number' && (
                    <p className="text-lg font-mono font-bold">{view.score.toFixed(1)}</p>
                  )}
                  {view.issues && view.issues.length > 0 && (
                    <ul className="space-y-1">
                      {view.issues.slice(0, 3).map((issue, i) => (
                        <li key={i} className="text-[10px] text-muted-foreground">{issue}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          ) : forensicsPayload?.telegram_brief && forensicsPayload.telegram_brief.length > 0 ? (
            <div className="space-y-2">
              {forensicsPayload.telegram_brief.map((line, index) => (
                <div key={index} className="p-2 rounded-md bg-muted/30 text-xs text-muted-foreground">{line}</div>
              ))}
            </div>
          ) : (
            <div className="text-center text-muted-foreground py-8 text-sm">No forensics data</div>
          )}
        </CardContent>
      </Card>

      {/* Reset Confirm Dialog */}
      <AlertDialog open={confirmReset} onOpenChange={setConfirmReset}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset Circuit Breaker?</AlertDialogTitle>
            <AlertDialogDescription>
              This will reset the circuit breaker and allow trading to resume. Only do this if the underlying issue has been resolved.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleReset} disabled={resetting}>
              {resetting ? 'Resetting...' : 'Confirm Reset'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
