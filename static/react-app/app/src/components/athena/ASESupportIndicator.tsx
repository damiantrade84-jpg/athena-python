import { Chip } from '@/components/shared/primitives';
import { cn } from '@/lib/utils';
import { resolveASESupportDisplay } from '@/lib/aseSupport';
import type { ASEShadowContext } from '@/types/athena';

export default function ASESupportIndicator({
  shadow,
  compact = false,
}: {
  shadow: ASEShadowContext | null | undefined;
  compact?: boolean;
}) {
  const display = resolveASESupportDisplay(shadow);
  if (!display) return null;

  return (
    <div
      className={cn(
        'rounded border border-border/70 bg-muted/10 px-2 py-1.5',
        compact ? 'space-y-1' : 'space-y-1.5',
      )}
      title={display.title}
      data-ase-support={display.supported ? 'supported' : 'advisory'}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="label shrink-0">ASE shadow</span>
        <Chip
          tone={display.tone}
          className={display.tone === 'muted' ? 'text-muted-foreground' : undefined}
        >
          {display.label}
        </Chip>
        {display.detail && (
          <span className="readout min-w-0 text-[10px] text-muted-foreground">
            {display.detail}
          </span>
        )}
      </div>
      {!compact && (
        <p className="text-[10px] leading-snug text-muted-foreground">
          {display.reason}. Advisory only — no Engine B score or gate change.
        </p>
      )}
    </div>
  );
}
