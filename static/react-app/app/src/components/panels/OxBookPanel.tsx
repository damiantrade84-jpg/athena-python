import { useCallback, useState } from 'react';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorBanner } from '@/components/shared';
import { BookOpenCheck, Play, RefreshCw, Shield } from 'lucide-react';
import { fmtNum } from '@/lib/utils';

const OX_GOLD = 'hsl(42 90% 55%)';

interface OxMemberSnapshot {
  instrument?: string;
  display?: string;
  brokerSymbol?: string;
  hasData?: boolean;
  status?: string;
  bars?: number;
  inPosition?: boolean;
  freshness?: { ok?: boolean; reason?: string };
  decision?: Record<string, any>;
  config?: Record<string, any>;
  error?: string;
}

interface OxBookStatus {
  success: boolean;
  deployment?: string;
  certification?: {
    certifiedOn?: string;
    universe?: number;
    trialsLogged?: number;
    members?: Record<string, Record<string, any>>;
    bookPooled?: Record<string, any>;
  };
  members?: string[];
  excludedInstruments?: string[];
  snapshots?: Record<string, OxMemberSnapshot>;
  error?: string;
}

interface OxBookRun {
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

export default function OxBookPanel() {
  const { data, loading, error, refresh } = useApiPoll<OxBookStatus>('/api/ox-book-status', 30000);
  const { post, loading: running, error: runError } = useApiPost<OxBookRun>();
  const [runInstrument, setRunInstrument] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<OxBookRun | null>(null);

  const handleRun = useCallback(async (instrument: string) => {
    const res = await post('/api/ox-book-run', { instrument });
    setRunInstrument(instrument);
    setRunResult(res);
    refresh();
  }, [post, refresh]);

  const cert = data?.certification || {};
  const pooled = cert.bookPooled || {};
  const evidence = cert.members || {};
  const members = data?.members || [];
  const snapshots = data?.snapshots || {};

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BookOpenCheck className="h-5 w-5" style={{ color: OX_GOLD }} />
          <h2 className="text-lg font-semibold">OX Book — Evidence-Certified Trend Book</h2>
          <Badge className="badge-neutral">DEMO ONLY</Badge>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
          <RefreshCw className="h-4 w-4 mr-1" /> Refresh
        </Button>
      </div>

      {(error || runError) && <ErrorBanner message={error || runError || ''} />}
      {data && data.success === false && <ErrorBanner message={data.error || 'status error'} />}

      {/* Certification evidence */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Shield className="h-4 w-4" style={{ color: OX_GOLD }} />
            Certified edge ({cert.certifiedOn || '2026-08-25'} — {cert.universe ?? 38}-market universe,
            {' '}{cert.trialsLogged ?? 419} logged trials)
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat label="Book SQN100 full" value={fmtNum(pooled.sqn100Full, 2)} />
          <Stat label="Book SQN100 OOS" value={fmtNum(pooled.sqn100Oos, 2)} />
          <Stat label="t-stat (bar: 3.0)" value={fmtNum(pooled.tStat, 2)} />
          <Stat label="Expectancy / trade" value={`${fmtNum((pooled.expR ?? 0) * 100, 1)} mR`} />
        </CardContent>
      </Card>

      {/* Members */}
      {members.length === 0 ? (
        <div className="text-sm text-muted-foreground">
          No certified members authorized. Add instruments to OX_BOOK.LIVE_MEMBERS in config.yaml
          after they pass the ox_book evidence gates.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {members.map((name) => {
            const snap = snapshots[name] || {};
            const dec = snap.decision || {};
            const ev = evidence[name] || {};
            const fresh = snap.freshness || {};
            const cfg = snap.config || {};
            const isRunning = running && runInstrument === name;
            return (
              <Card key={name}>
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center justify-between gap-2 text-sm">
                    <span className="flex items-center gap-2">
                      {snap.display || name}
                      <Badge className={snap.inPosition ? 'badge-long' : 'badge-neutral'}>
                        {snap.inPosition ? 'IN POSITION' : 'FLAT'}
                      </Badge>
                      <Badge className={fresh.ok ? 'badge-long' : 'badge-short'}>
                        {fresh.ok ? 'fresh' : `stale${fresh.reason ? `: ${fresh.reason}` : ''}`}
                      </Badge>
                    </span>
                    <Button size="sm" onClick={() => handleRun(name)} disabled={running}
                      style={{ background: OX_GOLD, color: '#241a04' }}>
                      <Play className="mr-1 h-3.5 w-3.5" /> {isRunning ? 'Running…' : 'Daily cycle'}
                    </Button>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {!snap.hasData ? (
                    <div className="text-xs text-muted-foreground">
                      No daily bars yet{snap.status ? ` (${snap.status})` : ''}.
                      {snap.error ? ` ${snap.error}` : ''}
                    </div>
                  ) : (
                    <>
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge className={actionBadgeClass(dec.action)}>{dec.action || 'NONE'}</Badge>
                        <span className="text-xs font-mono text-muted-foreground">{dec.reason}</span>
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <Stat label="Entry ref" value={fmtNum(dec.entryRef, 2)} />
                        <Stat label="Trail stop" value={fmtNum(dec.trailStop ?? dec.sl, 2)} />
                        <Stat label="ATR(14)" value={fmtNum(dec.atr, 2)} />
                        <Stat label="Bars" value={snap.bars ?? '—'} />
                        <Stat label="Edge quality" value={fmtNum(ev.edgeQuality, 3)} />
                        <Stat label="SQN100" value={fmtNum(ev.sqn100Full, 2)} />
                        <Stat label="Plateau pass" value={`${Math.round((ev.plateauPassFrac ?? 0) * 100)}%`} />
                        <Stat label="EMA fast/slow" value={`${cfg.fast ?? 15}/${cfg.slow ?? 60}`} />
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Last manual run */}
      {runResult && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Last manual run — {runInstrument}</CardTitle>
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
            <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted/40 p-2 text-[11px]">
              {JSON.stringify(runResult.result ?? runResult, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      {(data?.excludedInstruments?.length ?? 0) > 0 && (
        <p className="text-[11px] text-muted-foreground">
          Excluded (not certified): {(data?.excludedInstruments || []).join(', ') || '—'}.
          Orders route through the TSMOM demo gate → guardian → risk_check → broker and fail
          closed without a fresh confirmed D1 bar.
        </p>
      )}
    </div>
  );
}
