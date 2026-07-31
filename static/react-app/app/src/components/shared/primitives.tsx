import { useId, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

/* ══════════════════════════════════════════════════════════════════════════
   Aurora Terminal primitives — the vocabulary every rebuilt surface is
   written in.

   The point of this file is that a price, a label, a score gauge and a
   collapsed diagnostic block look identical everywhere they appear.
   ══════════════════════════════════════════════════════════════════════════ */

/* ── Panel ────────────────────────────────────────────────────────────────
   Raised glass surface with a hairline border and soft elevation. */
export function Panel({
  title,
  icon,
  action,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      className={cn('surface-raised', className)}
    >
      {(title || action) && (
        <header className="flex h-10 items-center gap-2 border-b border-border/70 px-3">
          {icon && <span className="shrink-0 text-primary/80">{icon}</span>}
          <h2 className="panel-title truncate">{title}</h2>
          {action && <div className="ml-auto flex shrink-0 items-center gap-1.5">{action}</div>}
        </header>
      )}
      <div className={cn('p-3', bodyClassName)}>{children}</div>
    </section>
  );
}

/* ── Metric ───────────────────────────────────────────────────────────────
   Label above value. The single approved way to show one number.
   `tone` is semantic only: long/short for direction and P&L. */
export type Tone = 'default' | 'long' | 'short' | 'accent' | 'warning' | 'muted';

const TONE_TEXT: Record<Tone, string> = {
  default: 'text-foreground',
  long: 'text-long',
  short: 'text-short',
  accent: 'text-primary',
  warning: 'text-warning',
  muted: 'text-muted-foreground',
};

export function Metric({
  label,
  value,
  tone = 'default',
  meta,
  size = 'sm',
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  tone?: Tone;
  meta?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}) {
  const valueSize =
    size === 'lg' ? 'text-xl' : size === 'md' ? 'text-sm' : 'text-[13px]';
  return (
    <div className={cn('min-w-0', className)}>
      <div className="label truncate">{label}</div>
      <div className={cn('readout mt-0.5 truncate', valueSize, TONE_TEXT[tone])}>
        {value}
      </div>
      {meta && (
        <div className="mt-0.5 truncate text-[10px] text-muted-foreground">{meta}</div>
      )}
    </div>
  );
}

/* ── KeyValue ─────────────────────────────────────────────────────────────
   Label left, value right, on one baseline. For dense detail lists where a
   stacked Metric would waste vertical space. */
export function KeyValue({
  label,
  value,
  tone = 'default',
  meta,
}: {
  label: ReactNode;
  value: ReactNode;
  tone?: Tone;
  meta?: ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="label shrink-0 normal-case tracking-normal">{label}</span>
      <span className="min-w-0 text-right">
        <span className={cn('readout text-xs', TONE_TEXT[tone])}>{value}</span>
        {meta && <span className="block text-[10px] text-muted-foreground">{meta}</span>}
      </span>
    </div>
  );
}

/* ── Meter ────────────────────────────────────────────────────────────────
   The score / quality bar. Gradient fill on an inset track, pass threshold
   drawn as a tick rather than by rescaling the axis — rescaling is what
   made scores incomparable between groups in the old build. */
export function Meter({
  pct,
  thresholdPct,
  passed,
  thresholdTitle,
  className,
}: {
  pct: number | null | undefined;
  thresholdPct?: number | null;
  passed?: boolean;
  thresholdTitle?: string;
  className?: string;
}) {
  const width = Math.max(0, Math.min(100, Number.isFinite(Number(pct)) ? Number(pct) : 0));
  return (
    <div
      className={cn('meter', className)}
      role="meter"
      aria-valuenow={Math.round(width)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn('meter-fill', passed ? 'meter-fill-pass' : 'meter-fill-accent')}
        style={{ width: `${width}%` }}
      />
      {thresholdPct != null && Number.isFinite(thresholdPct) && (
        <div
          className="meter-tick"
          style={{ left: `${Math.max(0, Math.min(100, thresholdPct))}%` }}
          title={thresholdTitle}
        />
      )}
    </div>
  );
}

/* ── ScoreRing ────────────────────────────────────────────────────────────
   Radial gauge for headline scores. The arc is a gradient (azure→cyan while
   earning, green sweep once the score clears its threshold) so the state is
   readable from across the room; the exact number sits in the middle. */
export function ScoreRing({
  pct,
  passed,
  size = 56,
  stroke = 5,
  className,
}: {
  pct: number | null | undefined;
  passed?: boolean;
  size?: number;
  stroke?: number;
  className?: string;
}) {
  const rawId = useId();
  const gradId = `score-ring-${rawId.replace(/[^a-zA-Z0-9]/g, '')}`;
  const valid = pct != null && Number.isFinite(Number(pct));
  const clamped = Math.max(0, Math.min(100, valid ? Number(pct) : 0));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - clamped / 100);
  return (
    <div
      className={cn('relative shrink-0', className)}
      style={{ width: size, height: size }}
      role="img"
      aria-label={valid ? `Score ${Math.round(clamped)} percent` : 'Score unavailable'}
    >
      <svg
        width={size}
        height={size}
        className="-rotate-90"
        style={{
          filter: passed
            ? 'drop-shadow(0 0 6px hsl(var(--long) / 0.45))'
            : 'drop-shadow(0 0 6px hsl(var(--primary) / 0.35))',
        }}
      >
        <defs>
          <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
            {passed ? (
              <>
                <stop offset="0%" stopColor="hsl(152 70% 42%)" />
                <stop offset="100%" stopColor="hsl(160 78% 56%)" />
              </>
            ) : (
              <>
                <stop offset="0%" stopColor="hsl(var(--primary))" />
                <stop offset="100%" stopColor="hsl(var(--primary-2))" />
              </>
            )}
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="hsl(222 18% 15%)"
          strokeWidth={stroke}
        />
        {valid && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={`url(#${gradId})`}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={offset}
            style={{ transition: 'stroke-dashoffset 400ms cubic-bezier(0.22, 1, 0.36, 1)' }}
          />
        )}
      </svg>
      <span
        className={cn(
          'readout absolute inset-0 flex items-center justify-center font-semibold tracking-tight',
          size >= 56 ? 'text-[13px]' : 'text-[11px]',
          valid ? (passed ? 'text-long' : 'text-foreground') : 'text-muted-foreground',
        )}
      >
        {valid ? `${Math.round(clamped)}%` : '—'}
      </span>
    </div>
  );
}

/* ── ScoreLine ────────────────────────────────────────────────────────────
   Headline score presentation: radial gauge + caption + fraction + meter.
   This is the "score / quality score" block, unified so Engine A confluence
   and Engine B quality read the same way at a glance. */
export function ScoreLine({
  caption,
  fraction,
  pct,
  thresholdPct,
  passed,
  thresholdTitle,
  pctSuffix,
}: {
  caption: ReactNode;
  fraction?: ReactNode;
  pct: number | null | undefined;
  thresholdPct?: number | null;
  passed?: boolean;
  thresholdTitle?: string;
  pctSuffix?: ReactNode;
}) {
  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-3">
        <ScoreRing pct={pct} passed={passed} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <span className="label truncate">{caption}</span>
            {pctSuffix && <span className="label shrink-0">{pctSuffix}</span>}
          </div>
          <div className="mt-1 flex items-center gap-2">
            {fraction != null && (
              <span className={cn('readout text-sm font-semibold', passed ? 'text-long' : 'text-muted-foreground')}>
                {fraction}
              </span>
            )}
            {passed === true && (
              <span className="chip chip-long">PASS</span>
            )}
          </div>
        </div>
      </div>
      <Meter pct={pct} thresholdPct={thresholdPct} passed={passed} thresholdTitle={thresholdTitle} />
    </div>
  );
}

/* ── Chip ─────────────────────────────────────────────────────────────────
   One shape, one size, colour only where it means something. */
export function Chip({
  children,
  tone = 'default',
  title,
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  title?: string;
  className?: string;
}) {
  const toneClass =
    tone === 'long' ? 'chip-long'
    : tone === 'short' ? 'chip-short'
    : tone === 'accent' ? 'chip-accent'
    : tone === 'warning' ? 'chip-warning'
    : '';
  return (
    <span className={cn('chip', toneClass, className)} title={title}>
      {children}
    </span>
  );
}

/* ── DirectionChip ────────────────────────────────────────────────────────
   LONG/SHORT. The one place green and red are always correct. */
export function DirectionChip({ direction }: { direction?: string | null }) {
  const dir = String(direction || '').toUpperCase();
  const isLong = dir === 'LONG' || dir === 'BUY';
  const isShort = dir === 'SHORT' || dir === 'SELL';
  return (
    <Chip tone={isLong ? 'long' : isShort ? 'short' : 'default'} className="font-semibold">
      {dir || '—'}
    </Chip>
  );
}

/* ── Details ──────────────────────────────────────────────────────────────
   Progressive disclosure. Diagnostics are never deleted — they start
   collapsed and are one click away on the same card. */
export function Details({
  summary,
  children,
  defaultOpen,
  className,
}: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  return (
    <details className={cn('disclosure', className)} open={defaultOpen}>
      <summary>{summary}</summary>
      <div className="space-y-2 pt-1.5">{children}</div>
    </details>
  );
}

/* ── Empty ────────────────────────────────────────────────────────────── */
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="py-10 text-center text-xs text-muted-foreground">{children}</div>
  );
}
