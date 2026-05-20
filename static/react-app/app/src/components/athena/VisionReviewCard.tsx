import { Eye } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { chartImageSrc } from '@/lib/visionReview';
import type { ChartAnalysisResponse } from '@/types/athena';

function labelText(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value).replace(/_/g, ' ');
}

function rightEdgeBadgeClass(value: unknown): string {
  if (value === 'CONFIRMS') return 'badge-long';
  if (value === 'POTENTIAL_REVERSAL') return 'badge-short';
  return 'badge-neutral';
}

function imageRows(vision: ChartAnalysisResponse): Array<{ label: string; src: string }> {
  const d1 = chartImageSrc(vision.chart_image_d1);
  const h4 = chartImageSrc(vision.chart_image);
  const h1 = chartImageSrc(vision.chart_image_h1);
  return [
    ...(d1 ? [{ label: 'D1', src: d1 }] : []),
    ...(h4 ? [{ label: 'H4', src: h4 }] : []),
    ...(h1 ? [{ label: 'H1', src: h1 }] : []),
  ];
}

export default function VisionReviewCard({ vision }: { vision: ChartAnalysisResponse }) {
  const structured = vision.structured || {};
  const tradeRead = (vision.structured_trade_read || structured.structured_trade_read || {}) as Record<string, unknown>;
  const rightEdge = tradeRead.right_edge_status || structured.right_edge_status || vision.right_edge;
  const tfAlignment = tradeRead.tf_alignment || vision.tf_alignment;
  const freshness = tradeRead.freshness_status;
  const styleRatings = (tradeRead.style_ratings || structured.style_ratings || {}) as Record<string, unknown>;
  const warnings = (Array.isArray(tradeRead.warnings) ? tradeRead.warnings : vision.chart_timestamp_warnings || []) as unknown[];
  const memo = typeof tradeRead.memo === 'string' ? tradeRead.memo : '';
  const images = imageRows(vision);

  return (
    <Card className="border-border/60 bg-card/50">
      <CardContent className="p-3 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Eye className="w-4 h-4 text-primary" />
          <span className="text-sm font-semibold">AI Vision Review</span>
          {rightEdge && (
            <Badge className={`${rightEdgeBadgeClass(rightEdge)} text-[10px]`}>
              RIGHT EDGE: {labelText(rightEdge)}
            </Badge>
          )}
          {tfAlignment && <Badge variant="outline" className="text-[10px]">TF: {labelText(tfAlignment)}</Badge>}
          {structured.rating && <Badge variant="outline" className="text-[10px]">{structured.rating}</Badge>}
          {vision.model && (
            <span className="text-[10px] text-muted-foreground font-mono ml-auto">
              {vision.model} - {vision.tf || 'H4'}
            </span>
          )}
        </div>

        {images.length > 0 ? (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-2">
            {images.map((img) => (
              <div key={img.label} className="border border-border/50 rounded-md overflow-hidden bg-background/50">
                <div className="px-2 py-1 text-[10px] text-muted-foreground border-b border-border/40">{img.label}</div>
                <img
                  src={img.src}
                  alt={`${vision.symbol || 'Chart'} ${img.label}`}
                  className="w-full h-auto block"
                  style={{ maxHeight: '280px', objectFit: 'contain' }}
                />
              </div>
            ))}
          </div>
        ) : (
          <div className="text-[11px] text-warning border border-border/40 rounded-md p-2">
            No chart image returned with the Vision response.
          </div>
        )}

        <div className="grid grid-cols-3 gap-2 text-[11px]">
          <div>
            <div className="text-muted-foreground text-[10px]">Scalp</div>
            <div className="font-mono">{labelText(styleRatings.scalp)}</div>
          </div>
          <div>
            <div className="text-muted-foreground text-[10px]">Intraday</div>
            <div className="font-mono">{labelText(styleRatings.intraday)}</div>
          </div>
          <div>
            <div className="text-muted-foreground text-[10px]">Swing</div>
            <div className="font-mono">{labelText(styleRatings.swing)}</div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 text-[11px]">
          <div>
            <span className="text-muted-foreground">Freshness: </span>
            <span className="font-mono">{labelText(freshness, 'unknown')}</span>
          </div>
          <div>
            <span className="text-muted-foreground">Execution context: </span>
            <span className="font-mono">
              {tradeRead.allowed_for_execution_context === true ? 'allowed' : 'advisory only'}
            </span>
          </div>
        </div>

        {memo && <p className="text-[11px] leading-relaxed text-foreground/90">{memo}</p>}

        {warnings.length > 0 && (
          <div className="text-[11px] text-warning space-y-1">
            {warnings.slice(0, 3).map((warning, idx) => (
              <div key={`${warning}-${idx}`}>{String(warning)}</div>
            ))}
          </div>
        )}

        {vision.analysis && (
          <details className="text-[11px]">
            <summary className="cursor-pointer text-muted-foreground">Model notes</summary>
            <pre className="mt-2 font-mono whitespace-pre-wrap text-foreground/90 max-h-72 overflow-y-auto p-2 bg-muted/20 rounded border border-border/40">
              {vision.analysis}
            </pre>
          </details>
        )}
      </CardContent>
    </Card>
  );
}
