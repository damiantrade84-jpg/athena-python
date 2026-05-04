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
      className="border-border bg-card relative overflow-hidden stat-card-top-line transition-all duration-200 hover:border-primary/30"
      style={{
        background: 'hsl(var(--card))',
      }}
    >
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <div className="min-w-0">
            <p className="text-[9px] uppercase tracking-[0.15em] font-mono" style={{ color: 'hsl(var(--muted-foreground))' }}>
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
          {/* Icon container — gold ring */}
          <div
            className={cn('p-2 rounded-md shrink-0', iconClass)}
            style={{
              background: 'hsl(var(--gold) / 0.08)',
              border: '1px solid hsl(var(--gold) / 0.22)',
            }}
          >
            {icon}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
