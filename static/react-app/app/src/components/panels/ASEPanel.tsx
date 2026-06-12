import { useCallback, useMemo, useState } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Input } from '@/components/ui/input';
import { ErrorBanner } from '@/components/shared';
import { Cpu, Radar, Activity, Shield, AlertTriangle } from 'lucide-react';
import { fmtNum, toNum } from '@/lib/utils';
import type { ASEScanResponse, ASEShadowSummary, ASESignalRow } from '@/types/athena';

const CYAN = 'hsl(185 85% 45%)';
const CYAN_DIM = 'hsl(185 55% 28%)';

const FAMILY_OPTIONS = [
  { value: 'all', label: 'All families' },
  { value: 'forex', label: 'Forex' },
  { value: 'crypto', label: 'Crypto' },
  { value: 'commodity', label: 'Commodity' },
  { value: 'equity', label: 'Equity' },
  { value: 'index_etf', label: 'Index / ETF' },
];

function statusBadgeClass(status?: string): string {
  switch ((status || '').toUpperCase()) {
    case 'TRADE': return 'badge-long';
    case 'WATCH': return 'badge-neutral';
    case 'ERROR': return 'badge-short';
    default: return 'badge-neutral';
  }
}

function deploymentBadge(state?: string): string {
  return (state || 'SHADOW').toUpperCase() === 'DEMO' ? 'badge-long' : 'badge-neutral';
}

function SignalCard({ row }: { row: ASESignalRow }) {
  const dq = row.dataQuality || {};
  const health = row.modelHealth || {};
  const monitor = (health.monitor || {}) as Record<string, unknown>;
  const drift = toNum(health.driftScore, 0);
  const deployment = String(dq.deployment || 'SHADOW').toUpperCase();
  const pCal = toNum(row.probabilityPositive, 0);
  const retQ = row.returnQ || {};
  const q10 = toNum(retQ.q10 ?? retQ['0.1'], NaN);
  const q90 = toNum(retQ.q90 ?? retQ['0.9'], NaN);

  return (
    <div
      className="rounded-lg border p-3 relative overflow-hidden"
      style={{ borderColor: CYAN_DIM, background: 'hsl(200 30% 6%)' }}
    >
      {deployment === 'SHADOW' && (
        <div
          className="absolute inset-0 pointer-events-none flex items-center justify-center opacity-[0.07] text-4xl font-black tracking-[0.3em] rotate-[-12deg]"
          style={{ color: CYAN }}
        >
          SHADOW
        </div>
      )}
      <div className="flex items-start justify-between gap-2 relative">
        <div>
          <div className="font-mono font-bold text-sm" style={{ color: CYAN }}>
            {row.instrument || '—'}
          </div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground mt-0.5">
            {row.modelFamily} · {row.horizon}
          </div>
        </div>
        <div className="flex flex-wrap gap-1 justify-end">
          <Badge className={statusBadgeClass(row.decisionStatus)}>{row.decisionStatus}</Badge>
          <Badge className={deploymentBadge(deployment)}>{deployment}</Badge>
          {row.direction && row.direction !== 'NONE' && (
            <Badge variant="outline">{row.direction}</Badge>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 mt-3 text-xs font-mono">
        <div>
          <div className="text-muted-foreground text-[10px]">E[net R]</div>
          <div className={toNum(row.expectedNetR) >= 0 ? 'text-long' : 'text-short'}>
            {fmtNum(row.expectedNetR, 3)}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground text-[10px]">P(cal)</div>
          <div>{fmtNum(pCal * 100, 1)}%</div>
          {!Number.isNaN(q10) && !Number.isNaN(q90) && (
            <div className="text-[10px] text-muted-foreground">
              q10–q90: {fmtNum(q10, 2)} / {fmtNum(q90, 2)} R
            </div>
          )}
        </div>
        <div>
          <div className="text-muted-foreground text-[10px]">Strength</div>
          <div>{row.signalStrength ?? 0}/100</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-1 mt-2">
        {(row.primarySignals || []).map((s) => (
          <Badge key={`${s.name}-${s.direction}`} variant="secondary" className="text-[10px]">
            {s.name}:{s.direction > 0 ? '+' : s.direction < 0 ? '−' : '0'}
          </Badge>
        ))}
      </div>

      <div className="flex flex-wrap gap-2 mt-2 text-[10px] text-muted-foreground">
        <span>route: {String(dq.route || 'core')}</span>
        <span>v: {row.modelVersion || '—'}</span>
        {drift > 0.15 && (
          <span className="text-amber-400 inline-flex items-center gap-0.5">
            <AlertTriangle className="h-3 w-3" /> drift {fmtNum(drift, 2)}
          </span>
        )}
        {Boolean(monitor.watchMax) && <span className="text-amber-400">WATCH-max</span>}
      </div>
    </div>
  );
}

export default function ASEPanel() {
  const { showToast } = useStore();
  const [family, setFamily] = useState('all');
  const [horizon, setHorizon] = useState<'intraday' | 'swing'>('intraday');
  const [pairFilter, setPairFilter] = useState('');
  const [scanResult, setScanResult] = useState<ASEScanResponse | null>(null);

  const { data: summary, refresh: refreshSummary } = useApiPoll<ASEShadowSummary>(
    '/api/ase-shadow-summary',
    30000,
  );
  const { post: postScan, loading: scanning, error: scanError } = useApiPost<ASEScanResponse>();

  const runScan = useCallback(async () => {
    const tokens = pairFilter.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
    const body: Record<string, unknown> = {
      horizon,
      writeJournal: true,
    };
    if (family !== 'all') body.family = family;
    if (tokens.length) body.symbols = tokens;

    const result = await postScan('/api/ase-scan', body);
    if (!result || result.error) {
      showToast(`ASE scan failed: ${result?.error || 'unknown'}`, 'error');
      return;
    }
    setScanResult(result);
    await refreshSummary();
    const tradeN = (result.signals || []).filter((s) => s.decisionStatus === 'TRADE').length;
    const watchN = (result.signals || []).filter((s) => s.decisionStatus === 'WATCH').length;
    showToast(`ASE shadow: ${tradeN} TRADE · ${watchN} WATCH · ${result.candidateCount ?? 0} candidates`, 'success');
  }, [family, horizon, pairFilter, postScan, refreshSummary, showToast]);

  const signals = useMemo(() => {
    const rows = scanResult?.signals || [];
    return [...rows].sort((a, b) => toNum(b.expectedNetR) - toNum(a.expectedNetR));
  }, [scanResult]);

  const activeSignals = signals.filter((s) => s.decisionStatus === 'TRADE' || s.decisionStatus === 'WATCH');

  return (
    <div className="h-full min-h-0 flex flex-col gap-3 p-3" style={{ background: 'hsl(200 35% 4%)' }}>
      <div
        className="shrink-0 rounded-lg border px-4 py-3 flex items-center justify-between"
        style={{ borderColor: CYAN_DIM, background: 'linear-gradient(135deg, hsl(195 40% 8%), hsl(200 35% 5%))' }}
      >
        <div className="flex items-center gap-3">
          <Cpu className="h-6 w-6" style={{ color: CYAN }} />
          <div>
            <h1 className="text-lg font-semibold tracking-wide" style={{ color: CYAN }}>
              ASE Command Centre
            </h1>
            <p className="text-[11px] text-muted-foreground">
              Adaptive Specialist Engine · shadow mode · no Engine C routing
            </p>
          </div>
        </div>
        <Badge variant="outline" className="uppercase tracking-widest text-[10px]" style={{ borderColor: CYAN, color: CYAN }}>
          SHADOW
        </Badge>
      </div>

      {scanError && <ErrorBanner message={scanError} />}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-3 shrink-0">
        <Card className="lg:col-span-3 border-0" style={{ background: 'hsl(200 30% 7%)', border: `1px solid ${CYAN_DIM}` }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2" style={{ color: CYAN }}>
              <Radar className="h-4 w-4" /> Shadow Scan
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2 items-end">
            <Select value={family} onValueChange={setFamily}>
              <SelectTrigger className="w-[140px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {FAMILY_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={horizon} onValueChange={(v) => setHorizon(v as 'intraday' | 'swing')}>
              <SelectTrigger className="w-[120px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="intraday">Intraday H1</SelectItem>
                <SelectItem value="swing">Swing D1</SelectItem>
              </SelectContent>
            </Select>
            <Input
              className="h-8 text-xs flex-1 min-w-[160px]"
              placeholder="Optional symbols: EURUSD, BTCUSDT"
              value={pairFilter}
              onChange={(e) => setPairFilter(e.target.value)}
            />
            <Button
              size="sm"
              onClick={runScan}
              disabled={scanning}
              className="h-8"
              style={{ background: CYAN, color: 'hsl(200 40% 6%)' }}
            >
              {scanning ? 'Scanning…' : 'Run ASE Scan'}
            </Button>
          </CardContent>
        </Card>

        <Card className="border-0" style={{ background: 'hsl(200 30% 7%)', border: `1px solid ${CYAN_DIM}` }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2" style={{ color: CYAN }}>
              <Shield className="h-4 w-4" /> Journal
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs font-mono space-y-1">
            <div>rows: {summary?.journal?.totalRows ?? '—'}</div>
            <div>reconciled: {summary?.journal?.reconciled ?? '—'}</div>
            <div className="text-[10px] text-muted-foreground truncate" title={summary?.journal?.journalPath}>
              {summary?.journal?.journalPath || '—'}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="flex-1 min-h-0 border-0 flex flex-col" style={{ background: 'hsl(200 30% 6%)', border: `1px solid ${CYAN_DIM}` }}>
        <CardHeader className="pb-2 shrink-0">
          <CardTitle className="text-sm flex items-center justify-between" style={{ color: CYAN }}>
            <span className="flex items-center gap-2">
              <Activity className="h-4 w-4" />
              Signals ({activeSignals.length} active / {signals.length} total)
            </span>
            {scanResult && (
              <span className="text-[10px] font-normal text-muted-foreground">
                candidates={scanResult.candidateCount}
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex-1 min-h-0 p-0">
          <ScrollArea className="h-[calc(100vh-320px)] px-4 pb-4">
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {signals.length === 0 && (
                <p className="text-sm text-muted-foreground col-span-full py-8 text-center">
                  Run an ASE scan to populate shadow signals. All families default to SHADOW until manually promoted.
                </p>
              )}
              {signals.map((row) => (
                <SignalCard key={`${row.instrument}-${row.decisionTimeMs}-${row.horizon}`} row={row} />
              ))}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
