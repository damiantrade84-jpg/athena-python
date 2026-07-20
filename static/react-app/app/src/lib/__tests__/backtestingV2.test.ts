import { describe, expect, it } from 'vitest';

import { assuranceTone, hasExplicitUniverse, statusTone } from '../backtestingV2';

describe('backtesting v2 workstation contracts', () => {
  it('never treats a missing or blank universe as run everything', () => {
    expect(hasExplicitUniverse([])).toBe(false);
    expect(hasExplicitUniverse(['', '   '])).toBe(false);
    expect(hasExplicitUniverse(['EURUSD'])).toBe(true);
  });

  it('keeps certified, partial, and exploratory assurance visually distinct', () => {
    expect(assuranceTone('CERTIFIED')).toContain('emerald');
    expect(assuranceTone('PARTIAL_CERTIFIED')).toContain('amber');
    expect(assuranceTone('EXPLORATORY')).toContain('fuchsia');
  });

  it('distinguishes completed, validation, and failed job states', () => {
    expect(statusTone('COMPLETED')).toContain('emerald');
    expect(statusTone('VALIDATING')).toContain('sky');
    expect(statusTone('FAILED')).toContain('rose');
  });
});
