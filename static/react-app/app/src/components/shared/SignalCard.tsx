import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn, fmtNum, toNum } from '@/lib/utils';
import { Play, Clock, ArrowUpRight } from 'lucide-react';
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

export default function SignalCard({ signal, onExecute, disabled, disabledLabel, compact }: SignalCardProps) {
  const isLong  = signal.direction === 'LONG';
  const isShort = signal.direction === 'SHORT';

  const dirStyle = isLong
    ? { background: 'hsl(var(--long) / 0.18)', color: 'hsl(var(--long))' }
    : { background: 'hsl(var(--short) / 0.18)', color: 'hsl(var(--short))' };

  const conf = confidenceDisplayPct(signal.conviction, signal.confidence);
  const CONF_HIGH = 85;
  const CONF_MED  = 70;
  const confColor = conf >= CONF_HIGH ? 'text-long' : conf >= CONF_MED ? 'text-warning' : 'text-muted-foreground';
  const confHigh = conf >= CONF_HIGH;
  const confBarColor = confHigh
    ? 'linear-gradient(90deg, hsl(var(--gold-dark)), hsl(var(--long)))'
    : 'linear-gradient(90deg, hsl(var(--gold-dark)), hsl(var(--gold-light)))';

  const tp2Num = Number(signal.tp2);
  const hasTp2 = signal.tp2 != null && Number.isFinite(tp2Num);

  /* ── Compact row ── */
  if (compact) {
    return (
      <div
        className={cn(
          'flex items-center justify-between p-2 rounded-md transition-all duration-150 hover:bg-muted/50',
          isLong  ? 'signal-long-border'  : '',
          isShort ? 'signal-short-border' : '',
        )}
        style={{ background: 'hsl(var(--muted) / 0.30)' }}
      >
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={dirStyle}>
            {signal.direction || '—'}
          </span>
          <span className="text-xs font-mono font-medium">{signal.pair || '—'}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground">{fmtNum(conf, 0)}%</span>
          <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => onExecute?.(signal)} disabled={disabled}>
            <Play className="w-3 h-3 text-primary" />
          </Button>
        </div>
      </div>
    );
  }

  /* ── Full card ── */
  return (
    <Card
      className={cn(
        'transition-all duration-200',
        isLong  ? 'signal-long-border'  : '',
        isShort ? 'signal-short-border' : '',
      )}
      style={{
        background: 'hsl(var(--card) / 0.60)',
        border: '1px solid hsl(var(--border) / 0.70)',
      }}
    >
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-sm font-mono font-bold">{signal.pair || '—'}</span>
            {/* Direction badge — pill shaped */}
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={dirStyle}>
              {signal.direction || '—'}
            </span>
            <Badge variant="outline" className="text-[10px]">{signal.engine || '—'}</Badge>
          </div>
          <span className="text-[10px] font-mono text-muted-foreground">
            <Clock className="w-3 h-3 inline mr-1" />
            {signal.timestamp ? new Date(signal.timestamp).toLocaleTimeString() : '—'}
          </span>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        {/* Entry / SL / TP grid */}
        <div className="grid grid-cols-3 gap-2">
          <div className="p-2 rounded-md" style={{ background: 'hsl(var(--muted) / 0.30)' }}>
            <p className="text-[10px] text-muted-foreground uppercase">Entry</p>
            <p className="text-xs font-mono font-bold">{fmtNum(signal.entry, 5)}</p>
          </div>
          <div className="p-2 rounded-md" style={{ background: 'hsl(var(--short) / 0.10)' }}>
            <p className="text-[10px] text-short uppercase">SL</p>
            <p className="text-xs font-mono font-bold text-short">{fmtNum(signal.sl, 5)}</p>
          </div>
          <div className="p-2 rounded-md" style={{ background: 'hsl(var(--long) / 0.10)' }}>
            <p className="text-[10px] text-long uppercase">TP</p>
            <p className="text-xs font-mono font-bold text-long">{fmtNum(signal.tp, 5)}</p>
          </div>
        </div>

        {hasTp2 && (
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <ArrowUpRight className="w-3 h-3 text-long" />
            TP2: <span className="font-mono text-foreground">{fmtNum(signal.tp2, 5)}</span>
          </div>
        )}

        {/* Confidence bar */}
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] text-muted-foreground">Confidence</span>
              <span className={cn('text-[10px] font-mono font-bold', confColor)}>
                {fmtNum(conf, 0)}%
              </span>
            </div>
            {/* Gradient progress bar — glows when high conviction */}
            <div
              className="relative w-full rounded-full h-2 overflow-hidden"
              style={{ background: 'hsl(var(--border) / 0.55)' }}
            >
              <div
                className={`h-full rounded-full transition-all duration-300 ${confHigh ? 'glow-gold-sm' : ''}`}
                style={{
                  width: `${Number.isFinite(conf) ? conf : 0}%`,
                  background: confBarColor,
                }}
              />
            </div>
          </div>
          <Badge variant="outline" className="text-[10px] font-mono">
            R:R {fmtNum(signal.rr ?? signal.rRatio, 2)}
          </Badge>
        </div>

        {/* Factor tags */}
        {signal.factors && signal.factors.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {signal.factors.map(f => (
              <Badge key={f} variant="secondary" className="text-[9px]">{f}</Badge>
            ))}
          </div>
        )}

        {onExecute && (
          <Button
            size="sm"
            className="w-full gap-2"
            onClick={() => onExecute(signal)}
            disabled={disabled}
            style={{
              background: 'linear-gradient(135deg, hsl(var(--gold-dark)), hsl(var(--gold)))',
              color: 'hsl(var(--primary-foreground))',
              border: 'none',
            }}
          >
            <Play className="w-3.5 h-3.5" />
            {disabledLabel || 'Execute'}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
