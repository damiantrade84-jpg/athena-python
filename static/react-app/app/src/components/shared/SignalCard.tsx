import { Button } from '@/components/ui/button';
import { Chip, DirectionChip, Meter, Metric } from '@/components/shared/primitives';
import { cn, fmtNum, toNum } from '@/lib/utils';
import { Play } from 'lucide-react';
import type { Signal } from '@/types';

interface SignalCardProps {
  signal: Signal;
  onExecute?: (signal: Signal) => void;
  disabled?: boolean;
  disabledLabel?: string;
  compact?: boolean;
}

/** Map backend conviction (0–1) or legacy confidence (0–100) to 0–100 display. */
function confidenceDisplayPct(conviction: unknown, confidence: unknown): number {
  const raw = conviction ?? confidence;
  const n = toNum(raw, NaN);
  if (!Number.isFinite(n)) return 0;
  if (n >= 0 && n <= 1) return n * 100;
  return n;
}

const CONF_HIGH = 85;

export default function SignalCard({
  signal,
  onExecute,
  disabled,
  disabledLabel,
  compact,
}: SignalCardProps) {
  const isLong = signal.direction === 'LONG';
  const isShort = signal.direction === 'SHORT';
  const conf = confidenceDisplayPct(signal.conviction, signal.confidence);
  const confHigh = conf >= CONF_HIGH;

  const tp2Num = Number(signal.tp2);
  const hasTp2 = signal.tp2 != null && Number.isFinite(tp2Num);

  /* ── Compact row ── */
  if (compact) {
    return (
      <div
        className={cn(
          'flex items-center justify-between gap-2 rounded border border-border bg-card px-2.5 py-2 transition-colors hover:border-muted-foreground/25',
          isLong && 'signal-long-border',
          isShort && 'signal-short-border',
        )}
      >
        <div className="flex min-w-0 items-center gap-2">
          <DirectionChip direction={signal.direction} />
          <span className="readout truncate text-xs">{signal.pair || '—'}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="readout text-[11px] text-muted-foreground">{fmtNum(conf, 0)}%</span>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 w-6 p-0"
            onClick={() => onExecute?.(signal)}
            disabled={disabled}
          >
            <Play className="h-3 w-3" />
          </Button>
        </div>
      </div>
    );
  }

  /* ── Full card ── */
  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card transition-colors hover:border-muted-foreground/25',
        isLong && 'signal-long-border',
        isShort && 'signal-short-border',
      )}
    >
      {/* Header: identity only */}
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="readout truncate text-sm font-semibold">{signal.pair || '—'}</span>
          <DirectionChip direction={signal.direction} />
          {signal.engine && <Chip>{signal.engine}</Chip>}
        </div>
        <span className="readout shrink-0 text-[11px] text-muted-foreground">
          {signal.timestamp ? new Date(signal.timestamp).toLocaleTimeString() : '—'}
        </span>
      </div>

      <div className="space-y-3 p-3">
        {/* Conviction — caption, number, flat meter */}
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between">
            <span className="label">Conviction</span>
            <span
              className={cn(
                'readout text-sm',
                confHigh ? 'text-long' : 'text-foreground',
              )}
            >
              {fmtNum(conf, 0)}%
            </span>
          </div>
          <Meter pct={conf} passed={confHigh} />
        </div>

        {/* Levels */}
        <div className="grid grid-cols-4 gap-3">
          <Metric label="Entry" value={fmtNum(signal.entry, 5)} />
          <Metric label="SL" value={fmtNum(signal.sl, 5)} tone="short" />
          <Metric label="TP" value={fmtNum(signal.tp, 5)} tone="long" />
          <Metric label="R:R" value={fmtNum(signal.rr ?? signal.rRatio, 2)} />
        </div>

        {(hasTp2 || (signal.factors && signal.factors.length > 0)) && (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-border pt-2.5">
            {hasTp2 && <Chip title="Second take-profit">TP2 {fmtNum(signal.tp2, 5)}</Chip>}
            {signal.factors?.map(f => (
              <Chip key={f}>{f}</Chip>
            ))}
          </div>
        )}

        {onExecute && (
          <Button
            size="sm"
            className="w-full gap-2"
            onClick={() => onExecute(signal)}
            disabled={disabled}
          >
            <Play className="h-3.5 w-3.5" />
            {disabledLabel || 'Execute'}
          </Button>
        )}
      </div>
    </div>
  );
}
