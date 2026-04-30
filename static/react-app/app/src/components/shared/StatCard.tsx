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
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{title}</p>
            {loading ? (
              <Skeleton className="h-7 w-24 mt-1" />
            ) : (
              <p className={cn('text-2xl font-mono font-bold mt-1 truncate', valueClass)}>{value}</p>
            )}
            {subtitle && !loading && (
              <p className="text-[10px] text-muted-foreground mt-0.5">{subtitle}</p>
            )}
          </div>
          <div className={cn('p-2 rounded-lg shrink-0', iconClass || 'bg-primary/15')}>
            {icon}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
