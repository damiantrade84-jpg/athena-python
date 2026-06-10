import { Card, CardContent } from '@/components/ui/card';
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

export default function StatCard({ title, value, icon, iconClass, valueClass, loading, subtitle }: StatCardProps) {
  return (
    <Card
      className="group border-border/70 relative overflow-hidden stat-card-top-line transition-all duration-300 hover:border-primary/35 hover:-translate-y-px hover:shadow-gold"
      style={{
        background: 'linear-gradient(180deg, hsl(var(--card) / 0.92), hsl(var(--background) / 0.55))',
        backdropFilter: 'blur(8px)',
      }}
    >
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <div className="min-w-0">
            <p
              className="text-[9px] uppercase tracking-[0.22em]"
              style={{ fontFamily: "'Cinzel', serif", color: 'hsl(var(--muted-foreground))' }}
            >
              {title}
            </p>
            {loading ? (
              <Skeleton className="h-7 w-24 mt-1" />
            ) : (
              <p className={cn('text-2xl font-mono font-bold mt-1 truncate tracking-tight', valueClass)}>
                {value}
              </p>
            )}
            {subtitle && !loading && (
              <p className="text-[10px] mt-0.5" style={{ color: 'hsl(var(--muted-foreground))' }}>
                {subtitle}
              </p>
            )}
          </div>
          {/* Icon chip — frosted ice ring */}
          <div
            className={cn('p-2 rounded-lg shrink-0 transition-shadow duration-300 group-hover:glow-gold-sm', iconClass)}
            style={{
              background: 'linear-gradient(160deg, hsl(var(--gold) / 0.12), hsl(var(--gold) / 0.03))',
              border: '1px solid hsl(var(--gold) / 0.25)',
            }}
          >
            {icon}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
