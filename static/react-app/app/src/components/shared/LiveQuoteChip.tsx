import { useLivePrices } from '@/hooks/useLivePrices';
import { liveQuoteView } from '@/lib/athenaFormat';
import { cn } from '@/lib/utils';

type LiveQuoteChipProps = {
  pair?: string;
  symbol?: string;
  type?: string;
  className?: string;
  compact?: boolean;
  showBook?: boolean;
};

export default function LiveQuoteChip({
  pair,
  symbol,
  type,
  className,
  compact = false,
  showBook = false,
}: LiveQuoteChipProps) {
  const { priceEntryFor } = useLivePrices();
  const view = liveQuoteView(priceEntryFor({ display: pair, pair, symbol }), pair, type);
  const book = showBook && view.bid != null && view.ask != null
    ? `bid ${view.bidLabel} / ask ${view.askLabel} · ${view.meta}`
    : view.meta;

  return (
    <div
      className={cn('min-w-0', className)}
      title={book}
      data-live-quote={view.available ? 'yes' : 'no'}
      data-live-stale={view.stale ? 'yes' : 'no'}
      data-live-source={view.source || ''}
    >
      <div className={cn(
        'flex flex-wrap items-baseline gap-x-1.5',
        compact ? 'text-[10px]' : 'text-sm',
      )}>
        <span className="text-[9px] font-medium uppercase tracking-wide text-muted-foreground">Live</span>
        <span className={cn(
          'readout font-semibold',
          view.available && !view.stale ? 'text-foreground' : 'text-warning',
        )}>
          {view.priceLabel}
        </span>
        <span className="truncate text-[9px] text-muted-foreground">{book}</span>
      </div>
    </div>
  );
}
