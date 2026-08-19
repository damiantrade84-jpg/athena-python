import { describe, expect, it } from 'vitest';
import {
  auditEngineTitle,
  formatAuditEngineLabel,
  resolveAuditEngine,
} from '../auditEngine';

describe('resolveAuditEngine', () => {
  it('prefers engine_resolved then audit_engine over style', () => {
    expect(
      resolveAuditEngine({
        style: 'intraday',
        engine: 'engine_b',
        audit_engine: 'engine_a',
        engine_resolved: 'engine_c',
      }),
    ).toBe('engine_c');
    expect(
      resolveAuditEngine({
        style: 'intraday',
        audit_engine: 'engine_a',
      }),
    ).toBe('engine_a');
  });

  it('maps scalp and sidecar engines', () => {
    expect(resolveAuditEngine({ audit_engine: 'scalp_vp' })).toBe('scalp');
    expect(resolveAuditEngine({ is_engine_d: true, style: 'intraday' })).toBe('scalp');
    expect(resolveAuditEngine({ engine: 'grok_engine' })).toBe('grok');
    expect(resolveAuditEngine({ engine: 'sol' })).toBe('sol');
  });

  it('does not treat a missing engine as style', () => {
    expect(resolveAuditEngine({ style: 'intraday' })).toBe('unknown');
  });
});

describe('formatAuditEngineLabel', () => {
  it('shows a readable executed-engine name', () => {
    expect(formatAuditEngineLabel('engine_a')).toBe('Engine A');
    expect(formatAuditEngineLabel('scalp')).toBe('Engine D');
    expect(formatAuditEngineLabel(resolveAuditEngine({ audit_engine: 'engine_b' }))).toBe('Engine B');
    expect(auditEngineTitle('engine_a')).toContain('Engine A');
  });
});
