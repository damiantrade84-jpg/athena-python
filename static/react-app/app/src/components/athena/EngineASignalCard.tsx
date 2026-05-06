import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
  const isLong  = signal.direction === 'LONG';
  const isShort = signal.direction === 'SHORT';
  const dirStyle = isLong
    ? { background: 'hsl(var(--long) / 0.18)', color: 'hsl(var(--long))' }
    : isShort
    ? { background: 'hsl(var(--short) / 0.18)', color: 'hsl(var(--short))' }
    : { background: 'hsl(var(--muted) / 0.40)', color: 'hsl(var(--muted-foreground))' };
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
  const intermarketEntries = intermarketConfirmationEntries(signal.intermarketConfirmation);
  void displayPrice;

  return (
    <Card
      className={cn(
        'transition-all duration-200',
        isLong  ? 'signal-long-border'  : '',
        isShort ? 'signal-short-border' : '',
      )}
      style={selected
        ? { background: 'hsl(var(--card) / 0.70)', border: '1px solid hsl(var(--gold) / 0.45)', boxShadow: '0 0 12px hsl(var(--gold) / 0.18)' }
        : { background: 'hsl(var(--card) / 0.50)', border: '1px solid hsl(var(--border) / 0.60)' }
      }
      onClick={() => onSelect?.(signal)}
    >
      <CardContent className={compact ? 'p-3 space-y-2' : 'p-4 space-y-3'}>
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-mono font-bold truncate">{pair}</span>
            {/* Direction badge — pill */}
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={dirStyle}>
              {signal.direction || '—'}
            </span>
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
          {/* Gold gradient bar */}
          <div className="w-full rounded-full h-1.5 overflow-hidden" style={{ background: 'hsl(var(--border) / 0.50)' }}>
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{
                width: `${conf ?? 0}%`,
                background: 'linear-gradient(90deg, hsl(var(--gold-dark)), hsl(var(--gold-light)))',
              }}
            />
          </div>
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

        {!compact && intermarketEntries.length > 0 && (
          <div className="rounded-md border border-border/50 bg-muted/20 p-2 space-y-1">
            <p className="text-[10px] uppercase text-muted-foreground">Intermarket confirmation</p>
            <div className="grid grid-cols-2 gap-1">
              {intermarketEntries.map(([label, value]) => (
                <div key={label} className="text-[10px] min-w-0">
                  <span className="text-muted-foreground">{label}: </span>
                  <span className="font-mono text-foreground break-words">{value}</span>
                </div>
              ))}
            </div>
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
            style={{
              background: 'linear-gradient(135deg, hsl(var(--gold-dark)), hsl(var(--gold)))',
              color: 'hsl(var(--primary-foreground))',
              border: 'none',
            }}
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
  const fg = accent === 'long' ? 'text-long' : accent === 'warning' ? 'text-warning' : 'text-primary';
  const bg = accent === 'long' ? 'hsl(var(--long) / 0.10)' : accent === 'warning' ? 'hsl(var(--warning) / 0.10)' : 'hsl(var(--gold) / 0.10)';
  return (
    <div className="p-2 rounded-md" style={{ background: bg }}>
      <p className="text-[10px] text-muted-foreground uppercase">{label}</p>
      <p className={cn('text-xs font-mono font-bold', fg)}>
        {fmtNum(value, 2)}
      </p>
    </div>
  );
}

function intermarketConfirmationEntries(value: unknown): Array<[string, string]> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v != null && v !== '')
    .slice(0, 6)
    .map(([k, v]) => [k, formatIntermarketValue(v)]);
}

function formatIntermarketValue(value: unknown): string {
  if (typeof value === 'number') return fmtNum(value, 2);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(formatIntermarketValue).join(', ');
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .slice(0, 3)
      .map(([k, v]) => `${k}:${formatIntermarketValue(v)}`)
      .join(' ');
  }
  return String(value ?? '');
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
  const bgStyle = accent === 'long'
    ? 'hsl(var(--long) / 0.10)'
    : accent === 'short'
    ? 'hsl(var(--short) / 0.10)'
    : accent === 'primary'
    ? 'hsl(var(--gold) / 0.10)'
    : 'hsl(var(--muted) / 0.30)';
  const fg = accent === 'long' ? 'text-long' : accent === 'short' ? 'text-short' : accent === 'primary' ? 'text-primary' : 'text-foreground';
  return (
    <div className="p-2 rounded-md" style={{ background: bgStyle }}>
      <p className={cn('text-[10px] uppercase', accent === 'muted' ? 'text-muted-foreground' : fg)}>{label}</p>
      <p className={cn('text-xs font-mono font-bold', fg)}>
        {decimals != null ? fmtNum(value, decimals) : fmtPrice(value, pair, type)}
      </p>
    </div>
  );
}
