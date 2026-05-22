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
      className={`inline-flex items-center rounded-sm border border-border/60 bg-background/90 px-1.5 py-0.5 text-[10px] font-mono text-foreground shadow-sm ${className}`}
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
    <div className={`flex min-w-0 flex-col gap-1 ${className}`}>
      <div className="flex flex-wrap items-center gap-1">
        {identityChips.map((chip) => (
          <FeedCaptureChip key={chip.key} title={chip.title}>
            {chip.label}
          </FeedCaptureChip>
        ))}
      </div>
      {feedChips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {feedChips.map((chip) => (
            <FeedCaptureChip key={chip.key} title={chip.title}>
              {chip.label}
            </FeedCaptureChip>
          ))}
        </div>
      )}
    </div>
  );
}
