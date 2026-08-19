import { describe, expect, it } from 'vitest';

import {
  grokClockLabel,
  grokComponentLabel,
  grokPrice,
  grokScanProgress,
  grokSetupLabel,
  type GrokScanState,
} from '../grokEngine';

function scan(overrides: Partial<GrokScanState> = {}): GrokScanState {
  return {
    scanId: 'scan-1',
    status: 'RUNNING',
    startedAt: '2026-08-19T10:00:00Z',
    totalPairs: 40,
    processedPairs: 10,
    readyCount: 1,
    watchCount: 2,
    blockedCount: 7,
    errorCount: 0,
    ...overrides,
  };
}

describe('GROK UI helpers', () => {
  it('computes and bounds scan progress', () => {
    expect(grokScanProgress(scan())).toBe(25);
    expect(grokScanProgress(scan({ processedPairs: 50 }))).toBe(100);
    expect(grokScanProgress(scan({ processedPairs: -2 }))).toBe(0);
    expect(grokScanProgress(scan({ processedPairs: Number.NaN }))).toBe(0);
    expect(grokScanProgress(null)).toBe(0);
  });

  it('formats instrument prices without inventing missing values', () => {
    expect(grokPrice(null)).toBe('—');
    expect(grokPrice(Number.NaN)).toBe('—');
    expect(grokPrice(1.23456789)).toMatch(/1[.,]23457/);
  });

  it('uses operational labels for native setup and score fields', () => {
    expect(grokSetupLabel('SILVER_BULLET')).toBe('Silver Bullet');
    expect(grokSetupLabel('JUDAS_RECLAIM')).toBe('Judas reclaim');
    expect(grokComponentLabel('killzone_clock')).toBe('Killzone clock');
    expect(grokComponentLabel('custom_field')).toBe('custom field');
    expect(grokClockLabel({ primaryWindow: 'ny_am_silver_bullet', primaryKind: 'silver_bullet' })).toBe(
      'ny am silver bullet · silver bullet',
    );
  });
});
