import { Badge } from '@/components/ui/badge';
import { compactWatchStatusLabel, watchDetailLabel } from '@/lib/suggestedWatchDisplay';
import type { SuggestedTradeWatch } from '@/types/athena';

export interface CompactSuggestedWatchStatusProps {
  watches: SuggestedTradeWatch[];
}

export default function CompactSuggestedWatchStatus({ watches }: CompactSuggestedWatchStatusProps) {
  const status = compactWatchStatusLabel(watches);
  if (!status) return null;

  const detail = watches.length === 1 ? watchDetailLabel(watches[0]) : `${watches.length} active watches`;
  const tone =
    status === 'Ready for review'
      ? 'border-long/50 text-long'
      : status === 'Expired' || status === 'Cancelled'
        ? 'border-muted-foreground/40 text-muted-foreground'
        : '';

  return (
    <Badge variant="outline" className={`h-7 text-[10px] ${tone}`} title={detail}>
      {status}
    </Badge>
  );
}
