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
      className="group relative overflow-hidden stat-card-top-line transition-all duration-300 hover:-translate-y-0.5 hover:shadow-gold hover:border-gold/40"
      style={{
        background: 'linear-gradient(168deg, hsl(var(--card) / 0.94), hsl(var(--background) / 0.6))',
        backdropFilter: 'blur(10px)',
        boxShadow: 'inset 0 1px 0 hsl(var(--gold) / 0.08), 0 6px 18px hsl(250 60% 3% / 0.5)',
        border: '1px solid hsl(var(--border) / 0.7)',
      }}
    >
      {/* ambient corner glow */}
      <div
        className="pointer-events-none absolute -top-8 -right-8 w-28 h-28 rounded-full opacity-0 blur-2xl transition-opacity duration-300 group-hover:opacity-100"
        style={{ background: 'radial-gradient(circle, hsl(var(--gold) / 0.22), transparent 70%)' }}
      />
      <CardContent className="p-4 relative">
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
          {/* Icon chip — luminous violet ring */}
          <div
            className={cn('p-2 rounded-lg shrink-0 transition-all duration-300 group-hover:glow-gold-sm', iconClass)}
            style={{
              background: 'linear-gradient(160deg, hsl(var(--gold) / 0.16), hsl(var(--gold) / 0.04))',
              border: '1px solid hsl(var(--gold) / 0.28)',
            }}
          >
            {icon}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
