import type { CSSProperties, ReactNode } from 'react';

export function FeedCaptureChip({
  children,
  title,
  className = '',
  style,
}: {
  children: ReactNode;
  title?: string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      data-chart-capture-label
      title={title}
      className={`inline-flex items-center rounded border border-border/50 bg-muted/80 px-1.5 py-0.5 text-[10px] font-mono leading-none text-foreground ${className}`}
      style={style}
    >
      {children}
    </span>
  );
}

export interface ChartFeedHeaderChipSpec {
  key: string;
  label: string;
  title?: string;
}

export interface ChartFeedHeaderChipsProps {
  identityChips: ChartFeedHeaderChipSpec[];
  feedChips: ChartFeedHeaderChipSpec[];
  className?: string;
}

export default function ChartFeedHeaderChips({
  identityChips,
  feedChips,
  className = '',
}: ChartFeedHeaderChipsProps) {
  return (
    <div className={`flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 ${className}`}>
      {identityChips.map((chip) => (
        <FeedCaptureChip key={chip.key} title={chip.title}>
          {chip.label}
        </FeedCaptureChip>
      ))}
      {feedChips.length > 0 && (
        <>
          <span className="mx-0.5 hidden h-3 w-px shrink-0 bg-border/70 sm:inline" aria-hidden />
          {feedChips.map((chip) => (
            <FeedCaptureChip key={chip.key} title={chip.title}>
              {chip.label}
            </FeedCaptureChip>
          ))}
        </>
      )}
    </div>
  );
}
