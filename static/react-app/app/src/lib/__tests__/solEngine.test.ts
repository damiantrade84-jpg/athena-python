import { describe, expect, it } from 'vitest';

import {
  solComponentLabel,
  solPrice,
  solScanProgress,
  solSetupLabel,
  type SolScanState,
} from '../solEngine';


function scan(overrides: Partial<SolScanState> = {}): SolScanState {
  return {
    scanId: 'scan-1',
    status: 'RUNNING',
    startedAt: '2026-08-18T10:00:00Z',
    totalPairs: 40,
    processedPairs: 10,
    readyCount: 1,
    watchCount: 2,
    blockedCount: 7,
    errorCount: 0,
    ...overrides,
  };
}


describe('SOL UI helpers', () => {
  it('computes and bounds scan progress', () => {
    expect(solScanProgress(scan())).toBe(25);
    expect(solScanProgress(scan({ processedPairs: 50 }))).toBe(100);
    expect(solScanProgress(scan({ processedPairs: -2 }))).toBe(0);
    expect(solScanProgress(scan({ processedPairs: Number.NaN }))).toBe(0);
    expect(solScanProgress(null)).toBe(0);
  });

  it('formats instrument prices without inventing missing values', () => {
    expect(solPrice(null)).toBe('—');
    expect(solPrice(Number.NaN)).toBe('—');
    expect(solPrice(1.23456789)).toMatch(/1[.,]23457/);
  });

  it('uses operational labels for native setup and score fields', () => {
    expect(solSetupLabel('SWEEP_RECLAIM')).toBe('Sweep → reclaim');
    expect(solSetupLabel('COMPRESSION_RELEASE')).toBe('Compression → release');
    expect(solComponentLabel('execution_geometry')).toBe('Execution geometry');
    expect(solComponentLabel('custom_field')).toBe('custom field');
  });
});
