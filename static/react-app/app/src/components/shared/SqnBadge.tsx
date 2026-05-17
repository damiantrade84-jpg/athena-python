import { Badge } from '@/components/ui/badge';
import { cn, fmtNum, toNum } from '@/lib/utils';

interface SqnBadgeProps {
  sqn: number | null | undefined;
  className?: string;
}

export default function SqnBadge({ sqn, className }: SqnBadgeProps) {
  const n = toNum(sqn, NaN);
  const valid = Number.isFinite(n);
  let variant: string;
  if (!valid) variant = 'bg-muted/20 text-muted-foreground border-muted/40';
  else if (n >= 3.0) variant = 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40';
  else if (n >= 2.0) variant = 'bg-long/20 text-long border-long/40';
  else if (n >= 1.0) variant = 'bg-warning/20 text-warning border-warning/40';
  else variant = 'bg-short/20 text-short border-short/40';

  return (
    <Badge variant="outline" className={cn('text-[10px] font-mono', variant, className)}>
      SQN {valid ? fmtNum(n, 2) : '—'}
    </Badge>
  );
}
