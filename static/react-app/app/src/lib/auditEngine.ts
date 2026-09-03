export type AuditEngineKey =
  | 'engine_a'
  | 'engine_b'
  | 'engine_c'
  | 'ase'
  | 'scalp'
  | 'grok'
  | 'sol'
  | 'opus'
  | 'kimi'
  | 'ox_alpha'
  | 'fable'
  | 'external'
  | 'unknown'
  | string;

const ENGINE_ALIASES: Record<string, AuditEngineKey> = {
  engine_a: 'engine_a',
  engine_a_v3: 'engine_a',
  'engine a': 'engine_a',
  a: 'engine_a',
  engine_b: 'engine_b',
  'engine b': 'engine_b',
  b: 'engine_b',
  naked: 'engine_b',
  engine_c: 'engine_c',
  'engine c': 'engine_c',
  c: 'engine_c',
  consensus: 'engine_c',
  ase: 'ase',
  scalp: 'scalp',
  scalp_vp: 'scalp',
  engine_d: 'scalp',
  'engine d': 'scalp',
  d: 'scalp',
  grok: 'grok',
  grok_engine: 'grok',
  sol: 'sol',
  sol_engine: 'sol',
  opus: 'opus',
  opus_engine: 'opus',
  kimi: 'kimi',
  kimi_engine: 'kimi',
  ox_alpha: 'ox_alpha',
  oxalpha: 'ox_alpha',
  'ox alpha': 'ox_alpha',
  ox: 'ox_alpha',
  fable: 'fable',
  fable_engine: 'fable',
  external: 'external',
  webhook: 'external',
};

const ENGINE_LABELS: Record<string, string> = {
  engine_a: 'Engine A',
  engine_b: 'Engine B',
  engine_c: 'Engine C',
  ase: 'ASE',
  scalp: 'Engine D',
  grok: 'GROK',
  sol: 'SOL',
  opus: 'OPUS',
  kimi: 'KIMI',
  ox_alpha: 'OX ALPHA',
  fable: 'FABLE',
  external: 'Webhook',
  unknown: 'Unknown',
};

const ENGINE_TITLES: Record<string, string> = {
  engine_a: 'Engine A (confluence / V3)',
  engine_b: 'Engine B (naked structure)',
  engine_c: 'Engine C (consensus)',
  ase: 'ASE',
  scalp: 'Engine D (scalp)',
  grok: 'GROK engine',
  sol: 'SOL engine',
  opus: 'OPUS engine',
  kimi: 'KIMI engine',
  ox_alpha: 'OX Alpha engine',
  fable: 'FABLE engine (narrative liquidity)',
  external: 'Webhook / external',
  unknown: 'Unknown engine',
};

function normalizeEngineKey(raw: unknown): AuditEngineKey {
  const key = String(raw ?? '').trim().toLowerCase();
  if (!key) return 'unknown';
  return ENGINE_ALIASES[key] ?? key;
}

export function resolveAuditEngine(trade: Record<string, unknown> | null | undefined): AuditEngineKey {
  const row = trade || {};
  for (const raw of [row.engine_resolved, row.audit_engine, row.engine]) {
    const key = normalizeEngineKey(raw);
    if (key !== 'unknown') return key;
  }
  if (row.is_engine_d === true) return 'scalp';
  const style = String(row.style || '').trim().toLowerCase();
  const grade = String(row.grade || '').trim().toUpperCase();
  if (grade === 'ASE') return 'ase';
  if (style === 'scalp' || grade === 'SCALP') return 'scalp';
  if (grade === 'WEBHOOK') return 'external';
  return 'unknown';
}

export function formatAuditEngineLabel(key: unknown): string {
  const normalized = normalizeEngineKey(key);
  return ENGINE_LABELS[normalized] ?? (normalized === 'unknown' ? 'Unknown' : String(key || 'Unknown'));
}

export function auditEngineTitle(key: unknown): string {
  const normalized = normalizeEngineKey(key);
  return ENGINE_TITLES[normalized] ?? formatAuditEngineLabel(normalized);
}
