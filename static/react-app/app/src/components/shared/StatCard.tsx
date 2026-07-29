import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import type { ReactNode } from 'react';

interface StatCardProps {
  title: string;
  value: string | number;
  icon: ReactNode;
  iconClass?: string;
  valueClass?: string;
  loading?: boolean;
  subtitle?: string;
}

/**
 * Headline KPI tile.
 *
 * Deliberately flat: the number is the only thing that should attract the
 * eye, so the tile has no gradient, no hover glow, no ambient corner light
 * and no icon ring. The icon sits at low contrast as a locator, not decor.
 */
export default function StatCard({
  title,
  value,
  icon,
  iconClass,
  valueClass,
  loading,
  subtitle,
}: StatCardProps) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3 transition-colors hover:border-muted-foreground/25">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="label truncate">{title}</p>
          {loading ? (
            <Skeleton className="mt-1.5 h-7 w-24" />
          ) : (
            <p
              className={cn(
                'readout mt-1 truncate text-[22px] font-semibold leading-tight tracking-tight',
                valueClass,
              )}
            >
              {value}
            </p>
          )}
          {subtitle && !loading && (
            <p className="mt-1 truncate text-[11px] text-muted-foreground">{subtitle}</p>
          )}
        </div>
        {/* Descendant selectors deliberately override any colour/size the
            caller set on the icon itself — the tile's value carries the
            status colour, so a second coloured glyph is noise. */}
        <span
          className={cn(
            'shrink-0 [&>svg]:h-4 [&>svg]:w-4 [&>svg]:text-muted-foreground/60',
            iconClass,
          )}
        >
          {icon}
        </span>
      </div>
    </div>
  );
}
