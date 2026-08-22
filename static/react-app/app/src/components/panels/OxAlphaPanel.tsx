import { useCallback, useMemo, useState } from 'react';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ErrorBanner } from '@/components/shared';
import {
  Chip,
  Details,
  DirectionChip,
  Empty,
  KeyValue,
  Meter,
  Panel,
  ScoreRing,
} from '@/components/shared/primitives';
import { Activity, Gem, RefreshCw, Zap } from 'lucide-react';
import { fmtNum } from '@/lib/utils';

const OX_VIOLET = 'hsl(268 85% 70%)';

interface OxComponent {
  [axis: string]: number | null;
}
interface OxGate {
  name: string;
  passed: boolean;
  detail?: string;
  hard?: boolean;
}
interface OxSignal {
  display?: string;
  symbol?: string;
  type?: string;
  source?: string;
  profile?: string;
  scoreGroup?: string;
  decision: string;
  direction: string | null;
  playType?: string | null;
  score: number;
  convictionPct?: number;
  opportunityPct?: number;
  components?: OxComponent;
  entryRef?: number;
  sl?: number;
  tp1?: number;
  tp2?: number;
  rr1?: number;
  atr?: number;
  atrPct?: number;
  entryReadiness?: string;
  entryReadinessReason?: string;
  executable?: boolean;
  priceSource?: string;
  volumeAxisUsed?: boolean;
  triggerConfirmed?: boolean;
  gates?: OxGate[];
  magnets?: Record<string, unknown>;
  requiredTimeframes?: string[];
}
interface OxEnvelope {
  success: boolean;
  signals?: OxSignal[];
  skipped?: { pair: string; reason: string }[];
  counts?: Record<string, number>;
  totalPairs?: number;
  elapsedSec?: number;
  scannedAt?: number;
  error?: string;
}

const AXES: { key: string; label: string; signed: boolean }[] = [
  { key: 'kinetic', label: 'Kinetic cascade', signed: true },
  { key: 'spring', label: 'Compression spring', signed: false },
  { key: 'flow', label: 'Effort–result flow', signed: true },
  { key: 'velocity', label: 'Session velocity', signed: false },
  { key: 'magnet', label: 'Magnet location', signed: true },
  { key: 'structure', label: 'Structure pulse', signed: true },
];

function decisionTone(d?: string): 'long' | 'warning' | 'default' {
  if (d === 'TRADE') return 'long';
  if (d === 'WATCH') return 'warning';
  return 'default';
}

function readinessTone(r?: string): 'long' | 'warning' | 'default' {
  const v = String(r || '').toUpperCase();
  if (v === 'READY') return 'long';
  if (!v || v === 'UNAVAILABLE') return 'default';
  return 'warning';
}

function AxisRow({ axis, value }: { axis: (typeof AXES)[number]; value: number | null }) {
  const v = typeof value === 'number' && Number.isFinite(value) ? value : null;
  const magnitude = v == null ? 0 : Math.min(100, Math.abs(v) * 100);
  const positive = (v ?? 0) >= 0;
  return (
    <div className="flex items-center gap-2">
      <span className="label w-36 shrink-0 truncate">{axis.label}</span>
      <Meter pct={magnitude} passed={magnitude >= 55} className="flex-1" />
      <span className="readout w-12 shrink-0 text-right text-[11px]">
        {v == null ? '—' : `${positive ? '+' : '−'}${Math.round(magnitude)}`}
      </span>
    </div>
  );
}

function SignalCard({
  signal,
  onExecuted,
}: {
  signal: OxSignal;
  onExecuted: () => void;
}) {
  const { post, loading: executing, error: execError } = useApiPost<Record<string, any>>();
  const [execResult, setExecResult] = useState<Record<string, any> | null>(null);
  const canExecute = signal.executable === true;

  const handleExecute = useCallback(async () => {
    const res = await post('/api/ox-alpha-execute', {
      symbol: signal.display || signal.symbol,
      direction: signal.direction,
    });
    setExecResult(res);
    onExecuted();
  }, [post, signal.display, signal.symbol, signal.direction, onExecuted]);

  const dp = signal.type === 'crypto' ? 1 : signal.entryRef != null && signal.entryRef > 500 ? 2 : 4;

  return (
    <Card className="surface-raised">
      <CardContent className="space-y-3 p-3">
        <div className="flex items-start gap-3">
          <ScoreRing pct={signal.score} passed={signal.decision === 'TRADE'} />
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="readout text-sm font-semibold">{signal.display}</span>
              <DirectionChip direction={signal.direction} />
              <Chip tone={decisionTone(signal.decision)} className="font-semibold">
                {signal.decision}
              </Chip>
              {signal.playType && <Chip tone="accent">{signal.playType}</Chip>}
              <Chip title={`score group profile`}>{signal.profile || signal.scoreGroup || '—'}</Chip>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Chip tone={readinessTone(signal.entryReadiness)}>
                readiness: {signal.entryReadiness || '—'}
              </Chip>
              {signal.triggerConfirmed != null && (
                <Chip tone={signal.triggerConfirmed ? 'long' : 'default'}>
                  trigger {signal.triggerConfirmed ? 'confirmed' : 'pending'}
                </Chip>
              )}
            </div>
          </div>
          <Button
            size="sm"
            disabled={!canExecute || executing}
            onClick={handleExecute}
            style={canExecute ? { background: OX_VIOLET, color: '#160b26' } : undefined}
            title={
              canExecute
                ? 'Fresh re-scan + demo-gated execution'
                : 'Executable only when TRADE + readiness READY'
            }
          >
            <Zap className="mr-1 h-3.5 w-3.5" />
            {executing ? 'Executing…' : 'Execute'}
          </Button>
        </div>

        {(execError || execResult) && (
          <div className="space-y-1">
            {execError && <ErrorBanner message={execError} />}
            {execResult && (
              <div className="flex items-center gap-2">
                <Badge className={execResult.executed ? 'badge-long' : 'badge-neutral'}>
                  {execResult.executed ? 'FILLED' : (execResult.reason || 'rejected')}
                </Badge>
                {execResult.venue && (
                  <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    venue: {String(execResult.venue)}
                  </span>
                )}
              </div>
            )}
          </div>
        )}

        <div className="grid grid-cols-3 gap-x-4 gap-y-1.5 md:grid-cols-6">
          <KeyValue label="Entry ref" value={fmtNum(signal.entryRef, dp)} />
          <KeyValue
            label="Stop"
            value={fmtNum(signal.sl, dp)}
            tone={signal.direction === 'LONG' ? 'short' : 'short'}
          />
          <KeyValue label="TP1" value={fmtNum(signal.tp1, dp)} tone="long" />
          <KeyValue label="TP2" value={fmtNum(signal.tp2, dp)} tone="long" />
          <KeyValue
            label="RR1"
            value={fmtNum(signal.rr1, 2)}
            tone={(signal.rr1 ?? 0) >= 1.5 ? 'long' : 'warning'}
          />
          <KeyValue label="ATR (M15)" value={fmtNum(signal.atr, dp)} meta={`${fmtNum((signal.atrPct ?? 0) * 100, 3)}%`} />
        </div>

        <div className="grid grid-cols-1 gap-x-6 gap-y-1 md:grid-cols-2">
          {AXES.map((axis) => (
            <AxisRow key={axis.key} axis={axis} value={signal.components?.[axis.key] ?? null} />
          ))}
        </div>

        <div className="flex items-center justify-between gap-2">
          <div className="flex gap-3">
            <span className="label">
              conviction{' '}
              <span className="readout text-xs">{fmtNum(signal.convictionPct, 0)}%</span>
            </span>
            <span className="label">
              opportunity{' '}
              <span className="readout text-xs">{fmtNum(signal.opportunityPct, 0)}%</span>
            </span>
          </div>
          <span className="note">{signal.priceSource || ''}</span>
        </div>

        <Details summary="Gates & diagnostics">
          {(signal.gates || []).map((g) => (
            <KeyValue
              key={g.name}
              label={`${g.hard === false ? 'soft' : 'hard'} · ${g.name}`}
              value={g.passed ? 'PASS' : 'FAIL'}
              tone={g.passed ? 'long' : 'short'}
              meta={g.detail}
            />
          ))}
          <KeyValue
            label="required timeframes"
            value={(signal.requiredTimeframes || []).join(' ') || '—'}
          />
          <pre className="max-h-40 overflow-auto rounded bg-muted/40 p-2 text-[10px]">
            {JSON.stringify(signal.magnets ?? {}, null, 2)}
          </pre>
        </Details>
      </CardContent>
    </Card>
  );
}

export default function OxAlphaPanel() {
  const [filter, setFilter] = useState<'ALL' | 'TRADE' | 'WATCH'>('ALL');
  const { data, loading, error, refresh } = useApiPoll<OxEnvelope>(
    '/api/ox-alpha-status',
    0,
  );

  const signals = useMemo(() => {
    const all = data?.signals || [];
    if (filter === 'ALL') return all;
    return all.filter((s) => s.decision === filter);
  }, [data, filter]);

  const counts = data?.counts || {};

  return (
    <div className="space-y-4">
      <Panel
        title="OX Alpha — intraday kinetic-liquidity engine"
        icon={<Gem className="h-4 w-4" style={{ color: OX_VIOLET }} />}
        action={
          <>
            <Badge className="badge-neutral">DEMO ONLY</Badge>
            <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
              <RefreshCw className="mr-1 h-3.5 w-3.5" /> {loading ? 'Scanning…' : 'Rescan universe'}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {error && <ErrorBanner message={error} />}
          {data?.success === false && <ErrorBanner message={data.error || 'status error'} />}

          <div className="flex flex-wrap items-center gap-2">
            <Chip tone="long">TRADE {counts.TRADE ?? 0}</Chip>
            <Chip tone="warning">WATCH {counts.WATCH ?? 0}</Chip>
            <Chip>NO_SIGNAL {counts.NO_SIGNAL ?? 0}</Chip>
            <Chip>{data?.totalPairs ?? 0} pairs</Chip>
            {data?.elapsedSec != null && <Chip>{fmtNum(data.elapsedSec, 1)}s scan</Chip>}
            <div className="ml-auto flex items-center gap-1">
              {(['ALL', 'TRADE', 'WATCH'] as const).map((f) => (
                <Button
                  key={f}
                  size="sm"
                  variant={filter === f ? 'default' : 'outline'}
                  onClick={() => setFilter(f)}
                  className="h-7 px-2 text-[11px]"
                >
                  {f}
                </Button>
              ))}
            </div>
          </div>

          <p className="note flex items-center gap-1.5">
            <Activity className="h-3 w-3" style={{ color: OX_VIOLET }} />
            Six axes per pair — kinetic cascade, compression spring, effort–result flow,
            session velocity, magnet map, structure pulse — weighted by score-group
            profile. Execute routes through demo gate → guardian → risk check → broker.
          </p>
        </div>
      </Panel>

      {signals.length === 0 ? (
        <Empty>
          {loading ? 'Scanning the universe…' : 'No signals match this filter.'}
        </Empty>
      ) : (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {signals.map((s) => (
            <SignalCard
              key={`${s.display}-${s.symbol}`}
              signal={s}
              onExecuted={refresh}
            />
          ))}
        </div>
      )}

      {(data?.skipped?.length ?? 0) > 0 && (
        <Details summary={`Skipped pairs (${data?.skipped?.length})`}>
          {(data?.skipped || []).map((sk) => (
            <KeyValue key={sk.pair} label={sk.pair} value={sk.reason} tone="muted" />
          ))}
        </Details>
      )}
    </div>
  );
}
