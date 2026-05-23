import { Badge } from '@/components/ui/badge';

export interface AdvisoryBadgeProps {
  warnings: string[];
}

export function ScalpAdvisoryBadge({ warnings }: AdvisoryBadgeProps) {
  if (!warnings.length) return null;
  const shown = warnings.slice(0, 6);
  return (
    <div className="pointer-events-none absolute right-2 top-2 z-10 max-w-[45%] rounded-md border border-warning/40 bg-warning/10 px-2 py-1.5 shadow-md">
      <div className="mb-1 text-[9px] font-semibold uppercase tracking-wide text-warning">Advisory</div>
      <div className="flex flex-wrap gap-1">
        {shown.map((w) => (
          <Badge key={w} variant="outline" className="border-warning/50 text-[8px] text-warning">
            {w}
          </Badge>
        ))}
      </div>
    </div>
  );
}
