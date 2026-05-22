export interface RightEdgeLabel {
  id: string;
  text: string;
  y: number;
  priority: number;
  color: string;
  side: 'right';
}

export type LayoutRightEdgeLabel = RightEdgeLabel & { layoutY: number };

export const RIGHT_EDGE_LABEL_HEIGHT_PX = 14;
export const RIGHT_EDGE_LABEL_MIN_GAP_PX = 16;

/** Priority: lower number = higher placement precedence when resolving collisions. */
export const RIGHT_EDGE_LABEL_PRIORITY = {
  currentPrice: 0,
  bosChoch: 1,
  entryContra: 2,
  zoneEdge: 3,
  fvgObProfile: 4,
} as const;

export function labelPriorityForText(text: string): number {
  const key = text.toUpperCase();
  if (key === 'PRICE' || key === 'LAST') return RIGHT_EDGE_LABEL_PRIORITY.currentPrice;
  if (key === 'BOS' || key === 'CHOCH' || key === 'BREAKER') return RIGHT_EDGE_LABEL_PRIORITY.bosChoch;
  if (key === 'ENTRY' || key === 'SL' || key === 'TP1' || key === 'TP2' || key === 'CONTRA') {
    return RIGHT_EDGE_LABEL_PRIORITY.entryContra;
  }
  if (key === 'SUP' || key === 'RES' || key.includes('SUPPORT') || key.includes('RESISTANCE')) {
    return RIGHT_EDGE_LABEL_PRIORITY.zoneEdge;
  }
  return RIGHT_EDGE_LABEL_PRIORITY.fvgObProfile;
}

export interface LayoutRightEdgeLabelsOptions {
  minGapPx?: number;
  labelHeightPx?: number;
  paneHeightPx?: number;
  panePaddingPx?: number;
}

/**
 * Deterministic vertical stacking for right-edge chart labels.
 * Adjusts layoutY only; underlying price levels are unchanged.
 */
export function layoutRightEdgeLabels(
  labels: RightEdgeLabel[],
  options: LayoutRightEdgeLabelsOptions = {},
): LayoutRightEdgeLabel[] {
  const minGapPx = options.minGapPx ?? RIGHT_EDGE_LABEL_MIN_GAP_PX;
  const labelHeightPx = options.labelHeightPx ?? RIGHT_EDGE_LABEL_HEIGHT_PX;
  const panePaddingPx = options.panePaddingPx ?? 4;
  const paneHeightPx = options.paneHeightPx ?? Number.POSITIVE_INFINITY;

  if (labels.length === 0) return [];

  const sorted = [...labels].sort((a, b) => {
    if (a.priority !== b.priority) return a.priority - b.priority;
    return a.y - b.y;
  });

  const maxY = Number.isFinite(paneHeightPx)
    ? paneHeightPx - panePaddingPx - labelHeightPx / 2
    : Number.POSITIVE_INFINITY;
  const minY = panePaddingPx + labelHeightPx / 2;

  const placed: LayoutRightEdgeLabel[] = [];
  let lastBottom = -Infinity;

  for (const label of sorted) {
    let layoutY = label.y;

    if (layoutY - labelHeightPx / 2 < lastBottom + minGapPx) {
      layoutY = lastBottom + minGapPx + labelHeightPx / 2;
    }

    if (layoutY > maxY) {
      layoutY = maxY;
      if (placed.length > 0) {
        const prev = placed[placed.length - 1];
        if (layoutY - labelHeightPx / 2 < prev.layoutY + labelHeightPx / 2 + minGapPx) {
          layoutY = Math.max(minY, prev.layoutY - minGapPx - labelHeightPx);
        }
      }
    }

    layoutY = Math.max(minY, Math.min(maxY, layoutY));
    placed.push({ ...label, layoutY });
    lastBottom = layoutY + labelHeightPx / 2;
  }

  const priceLabel = placed.find((item) => item.priority === RIGHT_EDGE_LABEL_PRIORITY.currentPrice);
  if (!priceLabel) return placed;

  const priceTop = priceLabel.layoutY - labelHeightPx / 2;
  const priceBottom = priceLabel.layoutY + labelHeightPx / 2;

  return placed.map((item) => {
    if (item.id === priceLabel.id) return item;
    let layoutY = item.layoutY;
    if (layoutY >= priceTop - minGapPx && layoutY <= priceBottom + minGapPx) {
      layoutY = priceBottom + minGapPx + labelHeightPx / 2;
      if (layoutY > maxY) {
        layoutY = priceTop - minGapPx - labelHeightPx / 2;
      }
    }
    return { ...item, layoutY: Math.max(minY, Math.min(maxY, layoutY)) };
  });
}
