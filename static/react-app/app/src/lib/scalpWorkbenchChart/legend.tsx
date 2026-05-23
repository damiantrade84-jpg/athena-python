export interface ScalpChartLegendProps {
  sourceLabel?: string | null;
  grade?: string | null;
  aiDecision?: string | null;
}

export function scalpChartLegendItems(props: ScalpChartLegendProps): string[] {
  const items = ['Candles', 'POC/VAH/VAL', 'Liquidity', 'Candidate', 'Engine B', 'Order Flow'];
  if (props.sourceLabel) items.push(`Source: ${props.sourceLabel}`);
  if (props.grade) items.push(`Grade ${props.grade}`);
  if (props.aiDecision) items.push(`AI: ${props.aiDecision}`);
  return items;
}

export function ScalpChartLegend(props: ScalpChartLegendProps) {
  const items = scalpChartLegendItems(props);
  return (
    <div className="pointer-events-none absolute bottom-2 left-2 z-10 max-w-[95%] rounded-md border border-border/50 bg-background/85 px-2 py-1 text-[9px] text-muted-foreground">
      {items.join(' · ')}
    </div>
  );
}
