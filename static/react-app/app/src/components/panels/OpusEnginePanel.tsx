// OPUS Engine — intraday liquidity/auction engine.
//
// The panel is built around the one thing that decides an OPUS trade: net
// expected R after costs. Conviction, probability and RR are all shown as
// inputs to that number rather than as headline scores, because a high score
// with a 0.4R cost is not a trade and the UI should not imply otherwise.

import { useCallback, useMemo, useState } from 'react';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ErrorBanner, Details } from '@/components/shared';
import {
  Activity, AlertTriangle, CheckCircle2, ChevronRight, CircleSlash,
  Gauge, Layers, Play, RefreshCw, ShieldCheck, Target, Waves, XCircle,
} from 'lucide-react';

const OPUS = 'hsl(199 89% 52%)';
const WARN = 'hsl(38 92% 50%)';
const GOOD = 'hsl(152 60% 45%)';
const BAD = 'hsl(0 72% 55%)';

interface Reading {
  name: string;
  value: number;
  confidence: number;
  detail: Record<string, any>;
}
interface Gate {
  name: string;
  passed: boolean;
  hard: boolean;
  detail: string;
  observed: any;
  limit: any;
}
interface Levels {
  entry: number;
  stop: number;
  targets: number[];
  entryKind: string;
  stopBasis: string;
  targetBasis: string[];
  riskPerUnit: number;
}
interface OpusSignal {
  signalId: string;
  symbol: string;
  display: string;
  assetClass: string;
  direction: 'LONG' | 'SHORT';
  archetype: string;
  regime: string;
  decision: 'TRADE' | 'WATCH' | 'STAND_DOWN';
  readiness: 'READY' | 'ARMED' | 'BLOCKED';
  conviction: number;
  coherence: number;
  probability: number;
  expectancyR: number;
  costR: number;
  rr: number;
  levels: Levels;
  readings: Reading[];
  gates: Gate[];
  blockingGates: string[];
  sizeUnits: number;
  riskPct: number;
  quote: { bid: number; ask: number; spreadPct: number; source: string } | null;
  timeframes: Record<string, string>;
  provenance: Record<string, any>;
  notes: string[];
}
interface ScanResponse {
  ok: boolean;
  signals: OpusSignal[];
  errors: { symbol: string; error: string }[];
  scanned: number;
  elapsedSec: number;
  engineVersion?: string;
  error?: string;
}
interface StatusResponse {
  ok: boolean;
  engineVersion: string;
  scoreModelVersion: string;
  mode: string;
  liveArmed: boolean;
  liveReason: string;
  universe: { symbol: string; display?: string; venue: string; class: string }[];
  store: Record<string, any>;
}
interface AccountRow {
  broker: string;
  venue?: string;
  available: boolean;
  live?: boolean;
  accountKind?: string;
  equity?: number;
  currency?: string;
  login?: number;
  server?: string;
  error?: string;
}
interface AccountResponse {
  ok: boolean;
  liveArmed: boolean;
  liveReason: string;
  requireDemoAccount: boolean;
  accounts: AccountRow[];
}

const INDICATOR_LABEL: Record<string, string> = {
  LDI: 'Liquidity Displacement',
  ARC: 'Absorption / Rejection',
  VFI: 'Void Field Inefficiency',
  SAV: 'Session Anchored Value',
  KDI: 'Kinetic Drift',
  OFP: 'Order Flow Pressure',
};

const ARCHETYPE_LABEL: Record<string, string> = {
  SWEEP_RECLAIM: 'Sweep & Reclaim',
  DISPLACEMENT_CONTINUATION: 'Displacement Continuation',
  VALUE_FADE: 'Value Fade',
  VACUUM_BREAK: 'Vacuum Break',
};

function num(v: any, digits = 2, fallback = '—'): string {
  const f = Number(v);
  return Number.isFinite(f) ? f.toFixed(digits) : fallback;
}

function price(v: any): string {
  const f = Number(v);
  if (!Number.isFinite(f)) return '—';
  const abs = Math.abs(f);
  const digits = abs >= 1000 ? 2 : abs >= 10 ? 3 : 5;
  return f.toFixed(digits);
}

function decisionClass(d: string): string {
  if (d === 'TRADE') return 'badge-long';
  if (d === 'WATCH') return 'badge-neutral';
  return 'badge-short';
}

/** Signed bar for an indicator reading, opacity encoding confidence. */
function ReadingBar({ reading }: { reading: Reading }) {
  const v = Math.max(-1, Math.min(1, Number(reading.value) || 0));
  const pct = Math.abs(v) * 50;
  const conf = Math.max(0.15, Math.min(1, Number(reading.confidence) || 0));
  return (
    <div className="flex items-center gap-2">
      <span className="w-9 shrink-0 font-mono text-[10px] text-muted-foreground">
        {reading.name}
      </span>
      <div className="relative h-2 flex-1 rounded-sm bg-muted/40">
        <div className="absolute left-1/2 top-0 h-full w-px bg-border" />
        <div
          className="absolute top-0 h-full rounded-sm"
          style={{
            width: `${pct}%`,
            left: v >= 0 ? '50%' : `${50 - pct}%`,
            backgroundColor: v >= 0 ? GOOD : BAD,
            opacity: conf,
          }}
        />
      </div>
      <span className="w-12 shrink-0 text-right font-mono text-[10px]">
        {v >= 0 ? '+' : ''}{v.toFixed(2)}
      </span>
    </div>
  );
}

/** The expectancy equation, shown as the decisive quantity it is. */
function ExpectancyStrip({ s }: { s: OpusSignal }) {
  const positive = s.expectancyR > 0;
  return (
    <div className="rounded-md border p-2" style={{ borderColor: positive ? GOOD : BAD }}>
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Net expectancy after costs
        </span>
        <span
          className="font-mono text-base font-semibold"
          style={{ color: positive ? GOOD : BAD }}
        >
          {s.expectancyR >= 0 ? '+' : ''}{num(s.expectancyR, 3)}R
        </span>
      </div>
      <div className="mt-1 font-mono text-[10px] text-muted-foreground">
        {num(s.probability, 2)} × {num(s.rr, 2)}R − {num(1 - s.probability, 2)} − {num(s.costR, 3)}R cost
      </div>
    </div>
  );
}

function GateList({ gates }: { gates: Gate[] }) {
  return (
    <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
      {gates.map((g) => (
        <div key={g.name} className="flex items-start gap-1.5 text-[11px]">
          {g.passed ? (
            <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" style={{ color: GOOD }} />
          ) : g.hard ? (
            <XCircle className="mt-0.5 h-3 w-3 shrink-0" style={{ color: BAD }} />
          ) : (
            <CircleSlash className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
          )}
          <div className="min-w-0">
            <span className="font-mono">{g.name}</span>
            <span className="text-muted-foreground">
              {' '}{String(g.observed ?? '—')}
              {g.limit !== null && g.limit !== undefined ? ` / ${String(g.limit)}` : ''}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function SignalCardOpus({
  s, onExecute, executing, routeLive, routeLabel,
}: {
  s: OpusSignal;
  onExecute: (s: OpusSignal) => void;
  executing: boolean;
  routeLive: boolean;
  routeLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const canExecute = s.decision === 'TRADE' && s.readiness === 'READY';

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-center gap-2">
          <button
            className="flex items-center gap-1 text-left"
            onClick={() => setOpen((v) => !v)}
          >
            <ChevronRight
              className="h-4 w-4 transition-transform"
              style={{ transform: open ? 'rotate(90deg)' : 'none' }}
            />
            <CardTitle className="text-base">{s.display}</CardTitle>
          </button>
          <Badge className={s.direction === 'LONG' ? 'badge-long' : 'badge-short'}>
            {s.direction}
          </Badge>
          <Badge className="badge-neutral">
            {ARCHETYPE_LABEL[s.archetype] || s.archetype}
          </Badge>
          <Badge className="badge-neutral">{s.regime}</Badge>
          <div className="ml-auto flex items-center gap-2">
            <Badge className={decisionClass(s.decision)}>{s.decision}</Badge>
            <Badge className={s.readiness === 'READY' ? 'badge-long' : 'badge-neutral'}>
              {s.readiness}
            </Badge>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Conviction</div>
            <div className="font-mono text-sm">{num(s.conviction * 100, 1)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Coherence</div>
            <div className="font-mono text-sm">{num(s.coherence, 2)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">P(target)</div>
            <div className="font-mono text-sm">{num(s.probability * 100, 1)}%</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Cost / risk</div>
            <div
              className="font-mono text-sm"
              style={{ color: s.costR > 0.25 ? WARN : undefined }}
            >
              {num(s.costR * 100, 1)}%
            </div>
          </div>
        </div>

        <ExpectancyStrip s={s} />

        <div className="grid grid-cols-3 gap-3 text-sm">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Entry</div>
            <div className="font-mono">{price(s.levels.entry)}</div>
            <div className="text-[10px] text-muted-foreground">{s.levels.entryKind}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Stop</div>
            <div className="font-mono" style={{ color: BAD }}>{price(s.levels.stop)}</div>
            <div className="truncate text-[10px] text-muted-foreground" title={s.levels.stopBasis}>
              {s.levels.stopBasis}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Target ({num(s.rr, 2)}R)
            </div>
            <div className="font-mono" style={{ color: GOOD }}>
              {price(s.levels.targets?.[0])}
            </div>
            <div className="truncate text-[10px] text-muted-foreground">
              {s.levels.targetBasis?.[0]}
            </div>
          </div>
        </div>

        <div className="space-y-1">
          {s.readings.map((r) => (
            <ReadingBar key={r.name} reading={r} />
          ))}
        </div>

        {s.notes?.length > 0 && (
          <div className="space-y-1">
            {s.notes.map((n, i) => (
              <div key={i} className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" style={{ color: WARN }} />
                <span>{n}</span>
              </div>
            ))}
          </div>
        )}

        {s.blockingGates?.length > 0 && (
          <div className="rounded-md border border-dashed p-2 text-[11px]">
            <span className="text-muted-foreground">Blocked by: </span>
            <span className="font-mono">{s.blockingGates.join(', ')}</span>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant={routeLive ? 'default' : 'outline'}
            disabled={!canExecute || executing}
            onClick={() => onExecute(s)}
            title={
              canExecute
                ? `Submit to ${routeLabel}`
                : 'Only a TRADE signal that is READY can be executed'
            }
          >
            <Play className="mr-1 h-3 w-3" />
            {executing ? 'Submitting…' : `Execute (${routeLabel})`}
          </Button>
          {canExecute && (
            <span className="font-mono text-[11px] text-muted-foreground">
              {num(s.sizeUnits, 2)} units · risk {num(s.riskPct, 2)}%
            </span>
          )}
          {s.quote && (
            <span className="ml-auto font-mono text-[11px] text-muted-foreground">
              {price(s.quote.bid)} / {price(s.quote.ask)} · spread {num(s.quote.spreadPct, 3)}% · {s.quote.source}
            </span>
          )}
        </div>

        {open && (
          <div className="space-y-3 border-t pt-3">
            <Details summary="Gates">
              <GateList gates={s.gates} />
            </Details>
            <Details summary="Indicator detail">
              <div className="space-y-2">
                {s.readings.map((r) => (
                  <div key={r.name} className="text-[11px]">
                    <div className="font-medium">
                      {INDICATOR_LABEL[r.name] || r.name}{' '}
                      <span className="font-mono text-muted-foreground">
                        {num(r.value, 3)} (conf {num(r.confidence, 2)})
                      </span>
                    </div>
                    <pre className="mt-0.5 overflow-x-auto rounded bg-muted/40 p-1.5 font-mono text-[10px]">
                      {JSON.stringify(r.detail, null, 1)}
                    </pre>
                  </div>
                ))}
              </div>
            </Details>
            <Details summary="Provenance">
              <pre className="overflow-x-auto rounded bg-muted/40 p-1.5 font-mono text-[10px]">
                {JSON.stringify(s.provenance, null, 1)}
              </pre>
            </Details>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function OpusEnginePanel() {
  const { data: status, refresh: refreshStatus } =
    useApiPoll<StatusResponse>('/api/opus/status', 30000);
  const { data: accountData, refresh: refreshAccounts } =
    useApiPoll<AccountResponse>('/api/opus/account', 60000);
  const { post, loading: posting } = useApiPost<any>();

  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [lastOrder, setLastOrder] = useState<any>(null);
  const [onlyTradeable, setOnlyTradeable] = useState(false);
  const [routeToBroker, setRouteToBroker] = useState(false);

  // The broker account that live routing would actually reach.
  const brokerAccount = useMemo(
    () => (accountData?.accounts || []).find((a) => a.live && a.available),
    [accountData],
  );
  const liveArmed = Boolean(accountData?.liveArmed ?? status?.liveArmed);
  const accountKind = brokerAccount?.accountKind || 'unknown';
  const demoConfirmed = accountKind === 'demo';
  // Routing is only offered when the engine is armed AND the connected account
  // is a confirmed demo, matching the server-side require_demo_account guard.
  const canRouteLive = liveArmed && Boolean(brokerAccount) &&
    (!accountData?.requireDemoAccount || demoConfirmed);
  const routeLive = routeToBroker && canRouteLive;
  const routeLabel = routeLive
    ? `${brokerAccount?.broker ?? 'broker'} ${accountKind}`
    : 'paper';

  const runScan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      const res = (await post('/api/opus/scan', { includeBlocked: true })) as ScanResponse;
      if (!res || res.ok === false) setError(res?.error || 'Scan failed');
      else setScan(res);
    } catch (e: any) {
      setError(e?.message || 'Scan failed');
    } finally {
      setScanning(false);
      refreshStatus();
    }
  }, [post, refreshStatus]);

  const execute = useCallback(
    async (s: OpusSignal) => {
      setExecutingId(s.signalId);
      setError(null);
      try {
        const res = await post('/api/opus/execute', {
          signalId: s.signalId,
          symbol: s.symbol,
          live: routeLive,
        });
        setLastOrder(res?.order || null);
        if (res?.ok === false) {
          setError(res?.order?.message || res?.error || 'Execution rejected');
        }
        refreshAccounts();
      } catch (e: any) {
        setError(e?.message || 'Execution failed');
      } finally {
        setExecutingId(null);
      }
    },
    [post, routeLive, refreshAccounts],
  );

  const signals = useMemo(() => {
    const list = scan?.signals || [];
    return onlyTradeable ? list.filter((s) => s.decision === 'TRADE') : list;
  }, [scan, onlyTradeable]);

  const tradeCount = (scan?.signals || []).filter((s) => s.decision === 'TRADE').length;

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Waves className="h-5 w-5" style={{ color: OPUS }} />
        <h2 className="text-lg font-semibold">OPUS Engine</h2>
        <Badge className="badge-neutral">INTRADAY</Badge>
        <Badge className={status?.liveArmed ? 'badge-short' : 'badge-neutral'}>
          {status?.mode ? status.mode.toUpperCase() : 'PAPER'}
        </Badge>
        {status?.engineVersion && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {status.engineVersion}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setOnlyTradeable((v) => !v)}>
            {onlyTradeable ? 'Showing tradeable' : 'Showing all'}
          </Button>
          <Button size="sm" onClick={runScan} disabled={scanning}>
            <RefreshCw className={`mr-1 h-3 w-3 ${scanning ? 'animate-spin' : ''}`} />
            {scanning ? 'Scanning…' : 'Run scan'}
          </Button>
        </div>
      </div>

      <p className="text-[11px] text-muted-foreground">
        Liquidity displacement, absorption, void-field inefficiency, session value, kinetic
        drift and order-flow pressure — combined by coherence, conditioned on regime and
        time of day, then gated on <span className="font-medium">net expected R after costs</span>.
      </p>

      {error && <ErrorBanner message={error} />}

      {lastOrder && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 py-3 text-sm">
            <ShieldCheck
              className="h-4 w-4"
              style={{ color: lastOrder.ok ? GOOD : BAD }}
            />
            <span className="font-medium">
              {lastOrder.status?.toUpperCase()} · {lastOrder.mode}
            </span>
            <span className="font-mono text-[11px] text-muted-foreground">
              {lastOrder.symbol} {lastOrder.direction} {num(lastOrder.units, 2)} @ {price(lastOrder.entry)}
            </span>
            <span className="text-[11px] text-muted-foreground">{lastOrder.message}</span>
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto"
              onClick={() => setLastOrder(null)}
            >
              Dismiss
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          { label: 'Universe', value: status?.universe?.length ?? '—', icon: Layers },
          { label: 'Scanned', value: scan?.scanned ?? '—', icon: Activity },
          { label: 'Signals', value: scan?.signals?.length ?? '—', icon: Gauge },
          { label: 'Tradeable', value: scan ? tradeCount : '—', icon: Target },
          {
            label: 'Scan time',
            value: scan ? `${num(scan.elapsedSec, 2)}s` : '—',
            icon: RefreshCw,
          },
        ].map((s) => (
          <Card key={s.label}>
            <CardContent className="flex items-center gap-2 py-3">
              <s.icon className="h-4 w-4" style={{ color: OPUS }} />
              <div>
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  {s.label}
                </div>
                <div className="font-mono text-sm">{s.value}</div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 py-3">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Execution route
          </span>
          <Button
            size="sm"
            variant={routeLive ? 'default' : 'outline'}
            disabled={!canRouteLive}
            onClick={() => setRouteToBroker((v) => !v)}
            title={
              canRouteLive
                ? 'Toggle between the internal paper simulator and the connected broker'
                : 'Broker routing needs OPUS armed and a confirmed demo account'
            }
          >
            {routeLive ? `Broker — ${accountKind.toUpperCase()}` : 'Paper simulator'}
          </Button>

          {brokerAccount ? (
            <span className="font-mono text-[11px] text-muted-foreground">
              {brokerAccount.broker.toUpperCase()} · {brokerAccount.login ?? '—'} ·{' '}
              {brokerAccount.server ?? '—'} ·{' '}
              {num(brokerAccount.equity, 2)} {brokerAccount.currency ?? ''}
            </span>
          ) : (
            <span className="text-[11px] text-muted-foreground">
              No live broker connected
            </span>
          )}

          <Badge
            className={
              demoConfirmed ? 'badge-long' : accountKind === 'real' ? 'badge-short' : 'badge-neutral'
            }
            title="Read from the terminal's own trade_mode, not the server name"
          >
            {accountKind.toUpperCase()} ACCOUNT
          </Badge>

          {accountData?.requireDemoAccount && (
            <Badge className="badge-neutral" title="Real-money accounts are refused">
              DEMO-ONLY GUARD ON
            </Badge>
          )}
        </CardContent>
      </Card>

      {!liveArmed && (
        <div className="rounded-md border border-dashed p-2 text-[11px] text-muted-foreground">
          Broker routing is disarmed — {accountData?.liveReason || status?.liveReason}.
          Execution submits to the paper simulator, which fills against the live quote
          with slippage applied. To route to the broker, set <code>MODE: live</code> in
          opus.local.yaml and start the server with <code>OPUS_LIVE_CONFIRM=1</code>.
        </div>
      )}

      {liveArmed && !demoConfirmed && accountData?.requireDemoAccount && (
        <div
          className="rounded-md border p-2 text-[11px]"
          style={{ borderColor: WARN }}
        >
          OPUS is armed but the connected account reports{' '}
          <span className="font-mono">{accountKind}</span>, not <span className="font-mono">demo</span>.
          Broker routing is refused by the demo-only guard.
        </div>
      )}

      {scan?.errors && scan.errors.length > 0 && (
        <Details summary={`Feed errors (${scan.errors.length})`}>
          <div className="space-y-1">
            {scan.errors.map((e, i) => (
              <div key={i} className="font-mono text-[11px]">
                <span className="text-muted-foreground">{e.symbol}: </span>
                {e.error}
              </div>
            ))}
          </div>
        </Details>
      )}

      {!scan && !scanning && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            Run a scan to evaluate the OPUS universe.
          </CardContent>
        </Card>
      )}

      {scan && signals.length === 0 && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No signals cleared setup geometry on this pass. That is a normal result —
            OPUS emits nothing when structure and cost do not line up.
          </CardContent>
        </Card>
      )}

      <div className="space-y-3">
        {signals.map((s) => (
          <SignalCardOpus
            key={s.signalId}
            s={s}
            onExecute={execute}
            executing={executingId === s.signalId || posting}
            routeLive={routeLive}
            routeLabel={routeLabel}
          />
        ))}
      </div>
    </div>
  );
}
