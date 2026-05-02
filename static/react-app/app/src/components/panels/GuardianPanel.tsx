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

interface GuardianApiStatus {
  overall: 'healthy' | 'warning' | 'critical';
  guardian: { passed: boolean; checks: { name: string; status: string; message: string; detail?: string }[] };
  shield: { circuit_breaker_open: boolean; failure_count: number };
  divergence: { divergence_count: number; critical_count: number; checks: { name: string; divergence: boolean; diff: number }[] };
  forensics: Record<string, unknown>;
}

interface FeedHealth {
  feeds: { source: string; last_update: string; freshness_seconds: number; status: string }[];
}

interface ForensicsSummary {
  metrics: Record<string, number>;
}

export default function GuardianPanel() {
  const [confirmReset, setConfirmReset] = useState(false);

  const { data: status, loading: statusLoading, error: statusError, refresh: refreshStatus } = useApiPoll<GuardianApiStatus>('/api/guardian/status', 30000);
  const { data: bootCheck, loading: bootLoading, error: bootError, refresh: refreshBoot } = useApiPoll<GuardianApiStatus>('/api/guardian/boot-check', 0);
  const { data: divergence } = useApiPoll<{ divergence_count: number; critical_count: number; checks: any[] }>('/api/divergence', 30000);
  const { data: shieldStatus } = useApiPoll<{ circuit_breaker_open: boolean; failure_count: number }>('/api/shield/status', 30000);
  const { data: feedHealth, loading: feedLoading, error: feedError } = useApiPoll<FeedHealth>('/api/feed-health', 30000);
  const { data: forensics, loading: forensicsLoading } = useApiPoll<ForensicsSummary>('/api/forensics/summary', 0);
  const { data: diagnostics } = useApiPoll<Record<string, unknown>>('/api/live-feed-diagnostics', 0);

  const { post: postReset, loading: resetting } = useApiPost<{ success: boolean }>();

  const handleReset = useCallback(async () => {
    const res = await postReset('/api/shield/reset');
    if (res?.success) {
      refreshStatus();
    }
    setConfirmReset(false);
  }, [postReset, refreshStatus]);

  const overall = status?.overall || 'unknown';
  const bannerColor = overall === 'healthy' ? 'bg-long/20 border-long/40 text-long' :
    overall === 'warning' ? 'bg-warning/20 border-warning/40 text-warning' :
    'bg-short/20 border-short/40 text-short';

  const checks = bootCheck?.guardian?.checks || status?.guardian?.checks || [];

  return (
    <div className="space-y-5">
      {(statusError || bootError || feedError) && (
        <ErrorBanner
          message={[statusError, bootError, feedError].filter(Boolean).join(' | ')}
          onRetry={() => { refreshStatus(); refreshBoot(); }}
        />
      )}

      {/* Overall Health Banner */}
      <div className={`p-4 rounded-md border flex items-center justify-between ${bannerColor}`}>
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5" />
          <span className="text-sm font-bold uppercase">Guardian Overall: {overall}</span>
        </div>
        <RefreshButton onClick={() => { refreshStatus(); refreshBoot(); }} loading={statusLoading || bootLoading} />
      </div>

      <div className="grid grid-cols-2 gap-5">
        {/* Boot Checks */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
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
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
              <AlertTriangle className="w-4 h-4 text-primary" />
              Circuit Breaker
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between p-3 rounded-md bg-muted/30">
              <span className="text-xs">Status</span>
              <Badge variant={shieldStatus?.circuit_breaker_open ? 'destructive' : 'default'} className="text-[10px]">
                {shieldStatus?.circuit_breaker_open ? 'OPEN' : 'CLOSED'}
              </Badge>
            </div>
            <div className="flex items-center justify-between p-3 rounded-md bg-muted/30">
              <span className="text-xs">Failure Count</span>
              <span className="text-xs font-mono font-bold">{shieldStatus?.failure_count || 0}</span>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full gap-1 text-xs"
              onClick={() => setConfirmReset(true)}
              disabled={!shieldStatus?.circuit_breaker_open}
            >
              <RefreshCw className="w-3 h-3" />
              Reset Circuit Breaker
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Divergence Monitor */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
            <Activity className="w-4 h-4 text-primary" />
            Divergence Monitor
          </CardTitle>
        </CardHeader>
        <CardContent>
          {divergence ? (
            <div className="space-y-3">
              <div className="flex items-center gap-4">
                <Badge variant="outline" className="text-[10px]">Divergences: {divergence.divergence_count}</Badge>
                <Badge variant={divergence.critical_count > 0 ? 'destructive' : 'outline'} className="text-[10px]">Critical: {divergence.critical_count}</Badge>
              </div>
              {divergence.checks && divergence.checks.length > 0 && (
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border/40">
                      <th className="text-[10px] uppercase py-2 text-muted-foreground">Check</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground">Divergence</th>
                      <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Diff</th>
                    </tr>
                  </thead>
                  <tbody>
                    {divergence.checks.map((c, i) => (
                      <tr key={i} className="border-b border-border/20">
                        <td className="py-2 text-xs">{c.name}</td>
                        <td className="py-2">
                          {c.divergence ? <XCircle className="w-3.5 h-3.5 text-short" /> : <CheckCircle className="w-3.5 h-3.5 text-long" />}
                        </td>
                        <td className="py-2 text-xs font-mono text-right">{c.diff?.toFixed(3) || '0.000'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
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
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>
            <Wifi className="w-4 h-4 text-primary" />
            Feed Health
          </CardTitle>
        </CardHeader>
        <CardContent>
          {feedLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : feedHealth?.feeds && feedHealth.feeds.length > 0 ? (
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border/40">
                  <th className="text-[10px] uppercase py-2 text-muted-foreground">Source</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground">Last Update</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Freshness</th>
                  <th className="text-[10px] uppercase py-2 text-muted-foreground">Status</th>
                </tr>
              </thead>
              <tbody>
                {feedHealth.feeds.map((f, i) => (
                  <tr key={i} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
                    <td className="py-2 text-xs font-mono">{f.source}</td>
                    <td className="py-2 text-[10px] font-mono text-muted-foreground">{f.last_update}</td>
                    <td className="py-2 text-xs font-mono text-right">{f.freshness_seconds}s</td>
                    <td className="py-2">
                      <Badge variant={f.status === 'ok' ? 'default' : 'destructive'} className="text-[10px]">{f.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="text-center text-muted-foreground py-8 text-sm">No feed health data</div>
          )}
        </CardContent>
      </Card>

      {/* Forensics */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Rajdhani', sans-serif", letterSpacing: '0.12em' }}>Forensics Summary</CardTitle>
        </CardHeader>
        <CardContent>
          {forensicsLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : forensics?.metrics ? (
            <div className="grid grid-cols-4 gap-3">
              {Object.entries(forensics.metrics).map(([key, val]) => (
                <div key={key} className="p-3 rounded-md bg-muted/30">
                  <p className="text-[10px] uppercase text-muted-foreground">{key}</p>
                  <p className="text-lg font-mono font-bold">{typeof val === 'number' ? val.toFixed(2) : String(val)}</p>
                </div>
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
