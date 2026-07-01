// Prime execution-window map — liquidity/quality windows only, advisory display.
// These are NOT trade signals; setup quality, freshness, spread, RR and
// candle-close confirmation gates remain authoritative.
//
// Windows are anchored to GMT+2 (desk research timezone), DST-aligned for
// summer: London open 08:00 BST = 09:00 GMT+2, NYSE open 09:30 EDT = 15:30
// GMT+2. When US/EU revert to standard time these occur ~1h later in GMT+2.

export type WindowQuality = 'prime' | 'high' | 'medium' | 'low' | 'avoid';

export interface QualitySegment {
  /** minutes-of-day in the GMT+2 reference clock */
  startMin: number;
  endMin: number;
  quality: WindowQuality;
  label: string;
  markets: string;
}

export interface GroupWindow {
  startMin: number;
  endMin: number;
  tier: 'prime' | 'secondary';
}

export interface AssetGroup {
  id: string;
  label: string;
  windows: GroupWindow[];
}

export const REFERENCE_UTC_OFFSET_MIN = 120; // GMT+2
export const REFERENCE_UTC_OFFSET_HOURS = REFERENCE_UTC_OFFSET_MIN / 60;
/** Default when /api/market-hours is unavailable; matches config MT5_BROKER_UTC_OFFSET. */
export const DEFAULT_MT5_BROKER_UTC_OFFSET_HOURS = 3;

const MIN_PER_DAY = 1440;
const h = (hours: number, minutes = 0) => hours * 60 + minutes;

/** Universal quality map (GMT+2). Segments tile the full 24h. */
export const QUALITY_SEGMENTS: QualitySegment[] = [
  { startMin: h(0), endMin: h(2), quality: 'low', label: 'Late Asia Drift', markets: 'Crypto · AUD · NZD (strong setups only)' },
  { startMin: h(2), endMin: h(5), quality: 'medium', label: 'Asia Session', markets: 'JPY · AUD · NZD · Asia indices · BTC/ETH' },
  { startMin: h(5), endMin: h(8, 30), quality: 'avoid', label: 'Pre-London Dead Zone', markets: 'Avoid fresh entries' },
  { startMin: h(8, 30), endMin: h(9), quality: 'medium', label: 'London Pre-Open', markets: 'EUR · GBP · DAX preparation' },
  { startMin: h(9), endMin: h(11, 30), quality: 'high', label: 'London Prime', markets: 'EUR · GBP · DAX · FTSE · Gold · EU stocks' },
  { startMin: h(11, 30), endMin: h(13, 30), quality: 'low', label: 'Midday Lull', markets: 'Manage positions; avoid weak entries' },
  { startMin: h(13, 30), endMin: h(14), quality: 'medium', label: 'New York Ramp', markets: 'Pre-overlap positioning' },
  // Overlap runs to 17:00 UTC (19:00 GMT+2); the 16:00–17:00 UTC tail still carries
  // strong FX/gold flow, so the prime band extends to 19:00 (was clipped at 18:00).
  { startMin: h(14), endMin: h(19), quality: 'prime', label: 'London–NY Overlap', markets: 'FX majors · Gold · Oil · Crypto · US indices' },
  { startMin: h(19), endMin: h(20, 30), quality: 'low', label: 'US Lunch Chop', markets: 'Avoid chop unless strong trend day' },
  { startMin: h(20, 30), endMin: h(22), quality: 'high', label: 'US Power Hour', markets: 'US stocks · ETFs · index close setups' },
  { startMin: h(22), endMin: h(24), quality: 'avoid', label: 'Rollover Risk', markets: 'No new trades; spreads widen' },
];

/** Per-asset-group execution windows (GMT+2). Session quality is group-specific. */
export const ASSET_GROUPS: AssetGroup[] = [
  {
    id: 'fx_majors',
    label: 'FX Majors',
    windows: [
      { startMin: h(9), endMin: h(11, 30), tier: 'prime' },
      { startMin: h(14), endMin: h(19), tier: 'prime' },
    ],
  },
  {
    id: 'crypto',
    label: 'Crypto',
    windows: [
      { startMin: h(2), endMin: h(5), tier: 'secondary' },
      { startMin: h(9), endMin: h(11, 30), tier: 'secondary' },
      { startMin: h(14), endMin: h(18, 30), tier: 'prime' },
    ],
  },
  {
    id: 'us_stocks',
    label: 'US Stocks · ETFs',
    windows: [
      { startMin: h(15, 30), endMin: h(17, 30), tier: 'prime' },
      { startMin: h(20, 30), endMin: h(22), tier: 'secondary' },
    ],
  },
  {
    id: 'eu_stocks',
    label: 'EU Stocks · DAX/FTSE',
    windows: [
      { startMin: h(9), endMin: h(11, 30), tier: 'prime' },
      { startMin: h(15, 30), endMin: h(17, 30), tier: 'secondary' },
    ],
  },
  {
    id: 'us_indices',
    label: 'US Indices',
    windows: [
      { startMin: h(15, 30), endMin: h(17, 30), tier: 'prime' },
      { startMin: h(20, 30), endMin: h(22), tier: 'secondary' },
    ],
  },
  {
    id: 'commodities',
    label: 'Gold · Commodities',
    windows: [
      { startMin: h(9), endMin: h(12), tier: 'prime' },
      { startMin: h(14), endMin: h(19), tier: 'prime' },
    ],
  },
  {
    id: 'asia',
    label: 'Asia FX · Nikkei',
    windows: [
      { startMin: h(2), endMin: h(5), tier: 'prime' },
    ],
  },
];

export const QUALITY_META: Record<WindowQuality, { label: string; color: string; bg: string; border: string }> = {
  prime: { label: 'PRIME', color: 'hsl(var(--gold-light))', bg: 'hsl(var(--gold) / 0.14)', border: 'hsl(var(--gold) / 0.45)' },
  high: { label: 'HIGH', color: 'hsl(var(--long))', bg: 'hsl(var(--long) / 0.10)', border: 'hsl(var(--long) / 0.35)' },
  medium: { label: 'MEDIUM', color: 'hsl(var(--warning))', bg: 'hsl(var(--warning) / 0.08)', border: 'hsl(var(--warning) / 0.30)' },
  low: { label: 'LOW', color: 'hsl(var(--muted-foreground))', bg: 'hsl(var(--muted) / 0.30)', border: 'hsl(var(--border) / 0.60)' },
  avoid: { label: 'AVOID', color: 'hsl(var(--short))', bg: 'hsl(var(--short) / 0.08)', border: 'hsl(var(--short) / 0.30)' },
};

/** Current minutes-of-day in the GMT+2 reference clock. */
export function refMinutesOfDay(now: Date): number {
  return (now.getUTCHours() * 60 + now.getUTCMinutes() + REFERENCE_UTC_OFFSET_MIN) % MIN_PER_DAY;
}

export function currentSegment(now: Date): QualitySegment {
  const m = refMinutesOfDay(now);
  return QUALITY_SEGMENTS.find(s => m >= s.startMin && m < s.endMin) ?? QUALITY_SEGMENTS[0];
}

export function nextSegment(now: Date): { segment: QualitySegment; minutesUntil: number } {
  const m = refMinutesOfDay(now);
  const cur = currentSegment(now);
  const idx = QUALITY_SEGMENTS.indexOf(cur);
  const next = QUALITY_SEGMENTS[(idx + 1) % QUALITY_SEGMENTS.length];
  return { segment: next, minutesUntil: (cur.endMin - m + MIN_PER_DAY) % MIN_PER_DAY || MIN_PER_DAY };
}

export interface GroupStatus {
  group: AssetGroup;
  /** window containing now, if any */
  active: GroupWindow | null;
  /** minutes until active window ends, or until next window starts */
  minutes: number;
  next: GroupWindow;
}

export function groupStatuses(now: Date): GroupStatus[] {
  const m = refMinutesOfDay(now);
  return ASSET_GROUPS.map(group => {
    const active = group.windows.find(w => m >= w.startMin && m < w.endMin) ?? null;
    if (active) {
      return { group, active, minutes: active.endMin - m, next: active };
    }
    let next = group.windows[0];
    let best = Infinity;
    for (const w of group.windows) {
      const delta = (w.startMin - m + MIN_PER_DAY) % MIN_PER_DAY;
      if (delta < best) {
        best = delta;
        next = w;
      }
    }
    return { group, active: null, minutes: best, next };
  });
}

/** Convert a GMT+2 reference minute-of-day to a local HH:mm label. */
export function refMinToLocalLabel(refMin: number): string {
  const d = new Date();
  d.setUTCHours(0, refMin - REFERENCE_UTC_OFFSET_MIN, 0, 0);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/** Format a GMT+2 anchor minute-of-day as HH:mm GMT+2 (window reference clock). */
export function refMinToAnchorLabel(refMin: number, withSuffix = true): string {
  const normalized = ((refMin % MIN_PER_DAY) + MIN_PER_DAY) % MIN_PER_DAY;
  const hh = Math.floor(normalized / 60);
  const mm = normalized % 60;
  const clock = `${hh.toString().padStart(2, '0')}:${mm.toString().padStart(2, '0')}`;
  return withSuffix ? `${clock} GMT+2` : clock;
}

/** Hours between MT5 broker clock and the GMT+2 window anchor (e.g. 3 - 2 = +1). */
export function brokerOffsetDeltaHours(brokerUtcOffsetHours: number): number {
  return brokerUtcOffsetHours - REFERENCE_UTC_OFFSET_HOURS;
}

/** MT5 broker HH:mm for a GMT+2 window boundary. */
export function refMinToBrokerLabel(refMin: number, brokerUtcOffsetHours: number): string {
  const deltaMin = brokerOffsetDeltaHours(brokerUtcOffsetHours) * 60;
  const brokerMin = ((refMin + deltaMin) % MIN_PER_DAY + MIN_PER_DAY) % MIN_PER_DAY;
  const hh = Math.floor(brokerMin / 60);
  const mm = brokerMin % 60;
  return `${hh.toString().padStart(2, '0')}:${mm.toString().padStart(2, '0')}`;
}

/** Footer note comparing MT5 broker clock to the GMT+2 window map. */
export function fmtBrokerOffsetNote(brokerUtcOffsetHours: number): string | null {
  const delta = brokerOffsetDeltaHours(brokerUtcOffsetHours);
  if (delta === 0) {
    return `MT5 broker GMT+${brokerUtcOffsetHours} — same as window clock.`;
  }
  const sign = delta > 0 ? '+' : '';
  return `MT5 charts GMT+${brokerUtcOffsetHours} (${sign}${delta}h vs window GMT+2).`;
}

export function fmtCountdown(minutes: number): string {
  const mins = Math.max(0, Math.round(minutes));
  const hh = Math.floor(mins / 60);
  const mm = mins % 60;
  if (hh <= 0) return `${mm}m`;
  return `${hh}h ${mm.toString().padStart(2, '0')}m`;
}
