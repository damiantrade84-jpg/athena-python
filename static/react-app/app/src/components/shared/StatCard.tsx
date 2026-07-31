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
 * Raised glass tile: the icon sits in a tinted locator well, the number
 * carries the status colour, and the whole tile lifts slightly on hover.
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
    <div className="surface-raised px-4 py-3.5 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/25 hover:shadow-elev-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="label truncate">{title}</p>
          {loading ? (
            <Skeleton className="mt-1.5 h-7 w-24" />
          ) : (
            <p
              className={cn(
                'readout mt-1 truncate text-[24px] font-semibold leading-tight tracking-tight',
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
        {/* Icon well: a tinted locator, not decor. Callers can still override
            colour via iconClass. */}
        <span
          className={cn(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20 [&>svg]:h-4 [&>svg]:w-4 [&>svg]:text-primary',
            iconClass,
          )}
        >
          {icon}
        </span>
      </div>
    </div>
  );
}
