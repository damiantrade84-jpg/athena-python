import { Badge } from '@/components/ui/badge';

export interface ThesisBadgeProps {
  grade?: string | null;
  candidatePhase?: string | null;
  setupModel?: string | null;
  marketState?: string | null;
  location?: string | null;
  aggressionClassification?: string | null;
  sourceQuality?: string | null;
}

export function ScalpThesisBadge(props: ThesisBadgeProps) {
  const items = [
    props.grade ? `Grade ${props.grade}` : null,
    props.candidatePhase,
    props.setupModel,
    props.marketState ? `State ${props.marketState}` : null,
    props.location ? `Loc ${props.location}` : null,
    props.aggressionClassification,
    props.sourceQuality ? `Src ${props.sourceQuality}` : null,
  ].filter(Boolean);

  return (
    <div className="pointer-events-none absolute left-2 top-2 z-10 max-w-[95%] rounded-md border border-border/60 bg-background/90 px-2 py-1.5 shadow-md">
      <div className="flex flex-wrap gap-1">
        {items.map((item) => (
          <Badge key={item} variant="outline" className="text-[9px] font-medium">
            {item}
          </Badge>
        ))}
      </div>
    </div>
  );
}
