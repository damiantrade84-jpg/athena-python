export type AuditEngineKey =
  | 'engine_a'
  | 'engine_b'
  | 'engine_c'
  | 'scalp'
  | 'external'
  | 'unknown'
  | string;

function normalizeEngineKey(raw: unknown): AuditEngineKey {
  const key = String(raw ?? '').trim().toLowerCase();
  if (!key) return 'unknown';
  if (key in ENGINE_LABELS) return key;
  if (key === 'engine_d' || key === 'scalp_vp') return 'scalp';
  return key;
}

const ENGINE_LABELS: Record<string, string> = {
  engine_a: 'A',
  engine_b: 'B',
  engine_c: 'C',
  scalp: 'D',
  external: 'WH',
  unknown: '—',
};

const ENGINE_TITLES: Record<string, string> = {
  engine_a: 'Engine A',
  engine_b: 'Engine B',
  engine_c: 'Engine C',
  scalp: 'Engine D',
  external: 'Webhook / external',
  unknown: 'Unknown engine',
};

export function resolveAuditEngine(trade: Record<string, unknown>): AuditEngineKey {
  const resolved = trade.engine_resolved ?? trade.engine;
  return normalizeEngineKey(resolved);
}

export function formatAuditEngineLabel(key: unknown): string {
  const normalized = normalizeEngineKey(key);
  return ENGINE_LABELS[normalized] ?? '—';
}

export function auditEngineTitle(key: unknown): string {
  const normalized = normalizeEngineKey(key);
  return ENGINE_TITLES[normalized] ?? 'Unknown engine';
}
