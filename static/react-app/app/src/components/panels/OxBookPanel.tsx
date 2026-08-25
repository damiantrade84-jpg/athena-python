import { useCallback, useState } from 'react';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorBanner } from '@/components/shared';
import { BookOpenCheck, Play, RefreshCw, Search, Shield } from 'lucide-react';
import { fmtNum } from '@/lib/utils';

const OX_GOLD = 'hsl(42 90% 55%)';

interface ManualExecution {
  eligible?: boolean;
  action?: string;
  reason?: string;
}

interface OxMemberSnapshot {
  instrument?: string;
  display?: string;
  brokerSymbol?: string;
  hasData?: boolean;
  status?: string;
  bars?: number;
  lastBarTimeMs?: number | null;
  dataSource?: string;
  inPosition?: boolean;
  freshness?: { ok?: boolean; reason?: string };
  decision?: Record<string, any>;
  manualExecution?: ManualExecution;
  config?: Record<string, any>;
  error?: string;
}

interface OxBookStatus {
  success: boolean;
  enabled?: boolean;
  deployment?: string;
  executionMode?: string;
  autoExecute?: boolean;
  schedulerEnabled?: boolean;
  scanId?: string | null;
  scannedAt?: string | null;
  scannedCount?: number;
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

interface OxBookExecution {
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
  const { post: postScan, loading: scanning, error: scanError } = useApiPost<OxBookStatus>();
  const { post: postExecute, loading: executing, error: executeError } = useApiPost<OxBookExecution>();
  const [scanData, setScanData] = useState<OxBookStatus | null>(null);
  const [executionInstrument, setExecutionInstrument] = useState<string | null>(null);
  const [executionResult, setExecutionResult] = useState<OxBookExecution | null>(null);

  const handleScan = useCallback(async () => {
    const result = await postScan('/api/ox-book-scan');
    if (result) {
      setScanData(result);
      setExecutionResult(null);
      await refresh();
    }
  }, [postScan, refresh]);

  const current = data?.scanId && data.scanId === scanData?.scanId ? data : (scanData || data);

  const handleExecute = useCallback(async (instrument: string) => {
    const scanId = current?.scanId;
    const snapshot = current?.snapshots?.[instrument];
    const manual = snapshot?.manualExecution;
    if (!scanId || !manual?.eligible || !manual.action) return;

    const confirmed = window.confirm(
      `Execute ${manual.action} for ${snapshot?.display || instrument} on the DEMO account? `
      + 'The server will revalidate this exact daily scan before routing it.',
    );
    if (!confirmed) return;

    setExecutionInstrument(instrument);
    setExecutionResult(null);
    const result = await postExecute('/api/ox-book-execute', { instrument, scanId });
    if (result) setExecutionResult(result);
    await refresh();
  }, [current, postExecute, refresh]);

  const cert = current?.certification || {};
  const pooled = cert.bookPooled || {};
  const evidence = cert.members || {};
  const members = current?.members || [];
  const snapshots = current?.snapshots || {};
  const scanned = Boolean(current?.scanId);

  return (
    <div className="p-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <BookOpenCheck className="h-5 w-5" style={{ color: OX_GOLD }} />
          <h2 className="text-lg font-semibold">OX Book — Daily TSMOM Trend Book</h2>
          <Badge className="badge-neutral">DEMO ONLY</Badge>
          <Badge className="badge-neutral">MANUAL EXECUTION</Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={refresh} disabled={loading || scanning}>
            <RefreshCw className="h-4 w-4 mr-1" /> Refresh status
          </Button>
          <Button size="sm" onClick={handleScan} disabled={scanning || executing}
            style={{ background: OX_GOLD, color: '#241a04' }}>
            <Search className="h-4 w-4 mr-1" /> {scanning ? 'Scanning…' : 'Scan Book'}
          </Button>
        </div>
      </div>

      {(error || scanError || executeError) && (
        <ErrorBanner message={error || scanError || executeError || ''} />
      )}
      {current && current.success === false && (
        <ErrorBanner message={current.error || 'OX Book error'} />
      )}

      <div className="rounded border border-border/60 bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        Scan is read-only. OX Book has no scheduler and never auto-executes. A demo order is
        possible only when a fresh scan exposes an OPEN_LONG or CLOSE action and you click its
        separate manual button.
        {scanned
          ? ` Latest scan: ${current?.scannedAt || 'time unavailable'} (${current?.scannedCount ?? 0} markets).`
          : ' No scan has been run in this server session.'}
      </div>

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

      {members.length === 0 ? (
        <div className="text-sm text-muted-foreground">
          No certified members are authorized in OX_BOOK.LIVE_MEMBERS.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {members.map((name) => {
            const snap = snapshots[name] || {};
            const dec = snap.decision || {};
            const manual = snap.manualExecution || {};
            const ev = evidence[name] || {};
            const fresh = snap.freshness || {};
            const cfg = snap.config || {};
            const isExecuting = executing && executionInstrument === name;
            return (
              <Card key={name}>
                <CardHeader className="pb-2">
                  <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span className="flex flex-wrap items-center gap-2">
                      {snap.display || name}
                      <Badge className={snap.inPosition ? 'badge-long' : 'badge-neutral'}>
                        {!scanned ? 'NOT SCANNED' : snap.inPosition ? 'IN POSITION' : 'FLAT'}
                      </Badge>
                      {scanned && (
                        <Badge className={fresh.ok ? 'badge-long' : 'badge-short'}>
                          {fresh.ok ? 'fresh' : `stale${fresh.reason ? `: ${fresh.reason}` : ''}`}
                        </Badge>
                      )}
                    </span>
                    <Button
                      size="sm"
                      onClick={() => handleExecute(name)}
                      disabled={!manual.eligible || executing || scanning || !current?.scanId}
                      title={manual.reason || 'Run Scan Book first'}
                    >
                      <Play className="mr-1 h-3.5 w-3.5" />
                      {isExecuting
                        ? 'Executing…'
                        : manual.eligible
                          ? 'Execute Demo Manually'
                          : 'No manual action'}
                    </Button>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {!scanned ? (
                    <div className="text-xs text-muted-foreground">Click Scan Book to read confirmed D1 MT5 bars.</div>
                  ) : !snap.hasData ? (
                    <div className="text-xs text-muted-foreground">
                      No authoritative daily bars{snap.status ? ` (${snap.status})` : ''}.
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
                        <Stat label="Data source" value={(snap.dataSource || 'unknown').toUpperCase()} />
                        <Stat label="EMA fast/slow" value={`${cfg.fast ?? 15}/${cfg.slow ?? 60}`} />
                      </div>
                      <p className="text-[11px] font-mono text-muted-foreground">
                        Manual: {manual.reason || 'not evaluated'}
                      </p>
                    </>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {executionResult && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Last manual demo execution — {executionInstrument}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <Badge className={executionResult.executed ? 'badge-long' : 'badge-short'}>
                {executionResult.result?.status || (executionResult.success ? 'executed' : 'rejected')}
              </Badge>
              <span className="text-xs font-mono text-muted-foreground">
                {executionResult.error || executionResult.result?.decision?.reason || ''}
              </span>
            </div>
            <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted/40 p-2 text-[11px]">
              {JSON.stringify(executionResult.result ?? executionResult, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      {(current?.excludedInstruments?.length ?? 0) > 0 && (
        <p className="text-[11px] text-muted-foreground">
          Excluded (not certified): {(current?.excludedInstruments || []).join(', ') || '—'}.
          Entries revalidate the scanned bar and route through demo, freshness, guardian,
          risk, and broker gates; exits use the demo-gated owned-position close path.
        </p>
      )}
    </div>
  );
}
