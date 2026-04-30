import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface SqnBadgeProps {
  sqn: number;
  className?: string;
}

export default function SqnBadge({ sqn, className }: SqnBadgeProps) {
  let variant: string;
  if (sqn >= 2.0) variant = 'bg-long/20 text-long border-long/40';
  else if (sqn >= 1.0) variant = 'bg-warning/20 text-warning border-warning/40';
  else variant = 'bg-short/20 text-short border-short/40';

  return (
    <Badge variant="outline" className={cn('text-[10px] font-mono', variant, className)}>
      SQN {sqn.toFixed(2)}
    </Badge>
  );
}
