import { useCallback, useState } from 'react';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ErrorBanner } from '@/components/shared';
import { Activity, Shield, TrendingUp, RefreshCw, Play } from 'lucide-react';
import { fmtNum } from '@/lib/utils';

const CYAN = 'hsl(185 85% 45%)';

interface TsmomStatus {
  success: boolean;
  deployment?: string;
  scorecard?: Record<string, any>;
  state?: Record<string, any>;
  error?: string;
}
interface TsmomRun {
  success: boolean;
  executed?: boolean;
  result?: Record<string, any>;
  error?: string;
}

function actionBadgeClass(action?: string): string {
  switch ((action || '').toUpperCase()) {
    case 'OPEN_LONG': return 'badge-long';
    case 'CLOSE': return 'badge-short';
    case 'HOLD': return 'badge-neutral';
    default: return 'badge-neutral';
  }
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-sm font-mono">{value}</span>
    </div>
  );
}

export default function TSMOMPanel() {
  const { data, loading, error, refresh } = useApiPoll<TsmomStatus>('/api/tsmom-status', 30000);
  const { post, loading: running, error: runError } = useApiPost<TsmomRun>();
  const [runResult, setRunResult] = useState<TsmomRun | null>(null);

  const handleRun = useCallback(async () => {
    const res = await post('/api/tsmom-run', { instrument: 'gold' });
    setRunResult(res);
    refresh();
  }, [post, refresh]);

  const st = data?.state || {};
  const dec = (st.decision || {}) as Record<string, any>;
  const fresh = (st.freshness || {}) as Record<string, any>;
  const card = data?.scorecard || {};
  const book = (card.book || {}) as Record<string, any>;
  const goldAlone = (card.goldAlone || {}) as Record<string, any>;
  const cfg = (st.config || {}) as Record<string, any>;

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-5 w-5" style={{ color: CYAN }} />
          <h2 className="text-lg font-semibold">TSMOM — Trend Specialist</h2>
          <Badge className="badge-neutral">DEMO ONLY</Badge>
          <Badge className="badge-neutral">GOLD (XAU/USD)</Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
            <RefreshCw className="h-4 w-4 mr-1" /> Refresh
          </Button>
          <Button size="sm" onClick={handleRun} disabled={running}
            style={{ background: CYAN, color: '#04181c' }}>
            <Play className="h-4 w-4 mr-1" /> {running ? 'Running…' : 'Run daily cycle'}
          </Button>
        </div>
      </div>

      {(error || runError) && <ErrorBanner message={error || runError || ''} />}
      {data && data.success === false && <ErrorBanner message={data.error || 'status error'} />}

      {/* Validated evidence */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Activity className="h-4 w-4" style={{ color: CYAN }} /> Validated edge (25-yr futures)
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat label="Strategy" value={<span className="text-xs">{card.validated || 'gold long-only EMA15/60×3ATR'}</span>} />
          <Stat label="Gold SQN100 (full / OOS)" value={`${fmtNum(goldAlone.sqn100Full, 2)} / ${fmtNum(goldAlone.sqn100Oos, 2)}`} />
          <Stat label="Book SQN100 (full / OOS)" value={`${fmtNum(book.sqn100Full, 2)} / ${fmtNum(book.sqn100Oos, 2)}`} />
          <Stat label="Book members" value={<span className="text-xs">{(book.members || []).join(' + ') || 'gold+brent+nasdaq'}</span>} />
        </CardContent>
      </Card>

      {/* Live decision */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Shield className="h-4 w-4" style={{ color: CYAN }} /> Live signal (gated demo)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {!st.hasData ? (
            <div className="text-sm text-muted-foreground">
              No daily bars available yet — needs the candle builder feeding D1 XAU/USD.
              {st.status ? <span className="font-mono"> ({st.status})</span> : null}
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2 flex-wrap">
                <Badge className={actionBadgeClass(dec.action)}>{dec.action || 'NONE'}</Badge>
                <span className="text-xs text-muted-foreground font-mono">{dec.reason}</span>
                <Badge className={st.inPosition ? 'badge-long' : 'badge-neutral'}>
                  {st.inPosition ? 'IN POSITION' : 'FLAT'}
                </Badge>
                <Badge className={fresh.ok ? 'badge-long' : 'badge-short'}>
                  freshness: {fresh.ok ? 'OK' : 'BLOCKED'}{fresh.reason ? ` (${fresh.reason})` : ''}
                </Badge>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Stat label="Direction" value={dec.direction || 'NONE'} />
                <Stat label="Entry ref" value={fmtNum(dec.entryRef, 2)} />
                <Stat label="Stop / trail" value={fmtNum(dec.trailStop ?? dec.sl, 2)} />
                <Stat label="ATR(14)" value={fmtNum(dec.atr, 2)} />
                <Stat label="EMA fast / slow" value={`${cfg.fast ?? 15} / ${cfg.slow ?? 60}`} />
                <Stat label="ATR mult" value={fmtNum(cfg.atrMult ?? 3, 1)} />
                <Stat label="Bars" value={st.bars ?? '—'} />
                <Stat label="Broker symbol" value={st.brokerSymbol || 'XAUUSD'} />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Last run outcome */}
      {runResult && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Last manual run</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <Badge className={runResult.executed ? 'badge-long' : 'badge-neutral'}>
                {runResult.result?.status || (runResult.success ? 'ran' : 'error')}
              </Badge>
              <span className="text-xs font-mono text-muted-foreground">
                {runResult.error || runResult.result?.decision?.reason || ''}
              </span>
            </div>
            <pre className="mt-2 text-[11px] bg-muted/40 rounded p-2 overflow-auto max-h-48">
              {JSON.stringify(runResult.result ?? runResult, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      <p className="text-[11px] text-muted-foreground">
        {card.note || 'Demo deployment is gold-only; brent/nasdaq pending broker-symbol verification.'}
        {' '}Orders route through demo gate → guardian → risk_check → broker, and fail closed without a fresh confirmed D1 bar.
      </p>
    </div>
  );
}
