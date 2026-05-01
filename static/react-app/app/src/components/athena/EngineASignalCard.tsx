import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { ArrowUpRight, Play } from 'lucide-react';
import { cn, fmtNum, toNum } from '@/lib/utils';
import {
  fmtPrice,
  priceDecimals,
  confluencePct,
  convictionTier,
  regimeLabel,
  sessionLabel,
} from '@/lib/athenaFormat';
import type { EngineASignal } from '@/types/athena';

interface Props {
  signal: EngineASignal;
  onExecute?: (sig: EngineASignal) => void;
  onSelect?: (sig: EngineASignal) => void;
  selected?: boolean;
  executeDisabled?: boolean;
  executeLabel?: string;
  compact?: boolean;
}

export default function EngineASignalCard({
  signal,
  onExecute,
  onSelect,
  selected,
  executeDisabled,
  executeLabel,
  compact,
}: Props) {
  const dirBg =
    signal.direction === 'LONG' ? 'bg-long/20 text-long' : signal.direction === 'SHORT' ? 'bg-short/20 text-short' : 'bg-muted/40 text-muted-foreground';
  const conf = confluencePct(signal);
  const conv = toNum(signal.conviction, NaN);
  const convT = convictionTier(Number.isFinite(conv) ? conv : null);
  const score = toNum(signal.confluenceScore ?? signal.score, NaN);
  const max = toNum(signal.maxScore, NaN);
  const threshold = toNum(signal.threshold, NaN);
  const passed = Number.isFinite(score) && Number.isFinite(threshold) && score >= threshold;
  const fs = signal.factorScores || {};
  const pair = signal.display || signal.pair || signal.symbol || '—';
  const type = signal.type;
  const livePrice = toNum(signal.livePrice, NaN);
  const displayPrice = Number.isFinite(livePrice) ? livePrice : signal.entry ?? signal.price;
  const decimals = priceDecimals(pair, type);

  return (
    <Card
      className={cn(
        'border-border/60 bg-card/50 hover:border-primary/30 transition-colors',
        selected && 'border-primary/60 ring-1 ring-primary/30',
      )}
      onClick={() => onSelect?.(signal)}
    >
      <CardContent className={compact ? 'p-3 space-y-2' : 'p-4 space-y-3'}>
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-mono font-bold truncate">{pair}</span>
            <Badge className={cn('text-[10px]', dirBg)}>{signal.direction || '—'}</Badge>
            {signal.signalClass && (
              <Badge variant="outline" className="text-[9px] uppercase">
                {String(signal.signalClass)}
              </Badge>
            )}
            {signal.style && (
              <Badge variant="outline" className="text-[9px]">
                {signal.style}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <Badge variant="outline" className={cn('text-[10px] font-mono', convT.color)}>
              {convT.tier}
            </Badge>
            {Number.isFinite(conv) && (
              <span className="text-[10px] text-muted-foreground font-mono">{(conv * 100).toFixed(0)}%</span>
            )}
          </div>
        </div>

        {/* Score + threshold + bar */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-[10px] text-muted-foreground">
            <span>
              Confluence{' '}
              <span className={cn('font-mono', passed ? 'text-long' : 'text-muted-foreground')}>
                {fmtNum(score, 2)}/{fmtNum(max, 2)}
              </span>
              {Number.isFinite(threshold) && <span className="ml-1">≥ {fmtNum(threshold, 2)}</span>}
            </span>
            <span className="font-mono">{conf != null ? `${conf.toFixed(0)}%` : '—'}</span>
          </div>
          <Progress value={conf ?? 0} className="h-1.5" />
          <p className="text-[9px] text-muted-foreground leading-snug">
            Final confluence blends trend, momentum quality, ADX/session gates and addon — it is{' '}
            <span className="font-medium text-foreground/80">not</span> the sum of the factor boxes below.
          </p>
        </div>

        {/* Factor breakdown */}
        {(fs.trend != null || fs.momentum != null || fs.addon != null) && (
          <div className="grid grid-cols-3 gap-2 text-center">
            <FactorBox label="Trend" value={fs.trend} accent="long" />
            <FactorBox label="Momentum" value={fs.momentum} accent="primary" />
            <FactorBox label="Addon" value={fs.addon} accent="warning" />
          </div>
        )}

        {/* Levels */}
        <div className="grid grid-cols-4 gap-2">
          <Level
            label="Live"
            value={Number.isFinite(livePrice) ? livePrice : undefined}
            pair={pair}
            type={type}
            accent="primary"
            decimals={decimals}
          />
          <Level label="Entry" value={signal.entry ?? signal.price} pair={pair} type={type} accent="muted" decimals={decimals} />
          <Level label="SL" value={signal.sl} pair={pair} type={type} accent="short" decimals={decimals} />
          <Level label="TP" value={signal.tp ?? signal.tp1} pair={pair} type={type} accent="long" decimals={decimals} />
        </div>

        {(signal.tp2 != null && Number.isFinite(Number(signal.tp2))) && (
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <ArrowUpRight className="w-3 h-3 text-long" />
            TP2: <span className="font-mono text-foreground">{fmtPrice(signal.tp2, pair, type)}</span>
            {signal.rr != null && <span className="ml-2">R:R {fmtNum(signal.rr ?? signal.rr1, 2)}</span>}
          </div>
        )}

        {/* Context row */}
        {!compact && (
          <div className="flex items-center justify-between text-[10px] text-muted-foreground gap-2 flex-wrap">
            <span>Regime: <span className="text-foreground font-mono">{regimeLabel(signal.regime)}</span></span>
            <span>Session: <span className="text-foreground font-mono">{sessionLabel(signal.session)}</span></span>
            {signal.factorDiagnostics?.adxValue != null && (
              <span>ADX: <span className="text-foreground font-mono">{fmtNum(signal.factorDiagnostics.adxValue, 1)}</span></span>
            )}
            {signal.atr != null && (
              <span>ATR: <span className="text-foreground font-mono">{fmtPrice(signal.atr, pair, type)}</span></span>
            )}
          </div>
        )}

        {/* Warnings */}
        {!compact && signal.warnings && signal.warnings.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {signal.warnings.slice(0, 6).map((w) => (
              <Badge key={w} variant="outline" className="text-[9px] text-warning border-warning/40">
                {w}
              </Badge>
            ))}
          </div>
        )}

        {onExecute && (
          <Button
            size="sm"
            className="w-full gap-2"
            onClick={(e) => {
              e.stopPropagation();
              onExecute(signal);
            }}
            disabled={executeDisabled}
          >
            <Play className="w-3.5 h-3.5" />
            {executeLabel || 'Execute'}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function FactorBox({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | undefined;
  accent: 'long' | 'primary' | 'warning';
}) {
  const accentClass = accent === 'long' ? 'bg-long/10 text-long' : accent === 'warning' ? 'bg-warning/10 text-warning' : 'bg-primary/10 text-primary';
  return (
    <div className="p-2 rounded-md bg-muted/30">
      <p className="text-[10px] text-muted-foreground uppercase">{label}</p>
      <p className={cn('text-xs font-mono font-bold', accentClass.split(' ')[1])}>
        {fmtNum(value, 2)}
      </p>
    </div>
  );
}

function Level({
  label,
  value,
  pair,
  type,
  accent,
  decimals,
}: {
  label: string;
  value: unknown;
  pair?: string;
  type?: string;
  accent: 'muted' | 'long' | 'short' | 'primary';
  decimals?: number;
}) {
  const bg = accent === 'long' ? 'bg-long/10' : accent === 'short' ? 'bg-short/10' : accent === 'primary' ? 'bg-primary/10' : 'bg-muted/30';
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : accent === 'primary' ? 'text-primary' : 'text-foreground';
  return (
    <div className={cn('p-2 rounded-md', bg)}>
      <p className={cn('text-[10px] uppercase', accent === 'muted' ? 'text-muted-foreground' : fg)}>{label}</p>
      <p className={cn('text-xs font-mono font-bold', fg)}>
        {decimals != null ? fmtNum(value, decimals) : fmtPrice(value, pair, type)}
      </p>
    </div>
  );
}
