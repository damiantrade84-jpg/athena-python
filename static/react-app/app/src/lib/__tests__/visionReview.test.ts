import { describe, expect, it } from 'vitest';

import { visionReviewImageTimeframes } from '../visionReview';

describe('visionReviewImageTimeframes', () => {
  it('sends GBPUSD major structure H4 and trigger M15, not setup H1', () => {
    expect(visionReviewImageTimeframes({
      structureTf: 'H4',
      setupTf: 'H1',
      triggerTf: 'M15',
      executionTf: 'M15',
    })).toEqual({ structureTf: 'H4', entryTf: 'M15' });
  });

  it('keeps Engine A structure and trigger roles separate', () => {
    expect(visionReviewImageTimeframes({
      structureTf: 'H4',
      setupTf: 'H1',
      triggerTf: 'M15',
      executionTf: 'M15',
    })).toEqual({ structureTf: 'H4', entryTf: 'M15' });
  });

  it('uses Engine B server-stamped zone and trigger roles', () => {
    expect(visionReviewImageTimeframes({
      timeframe: 'H4',
      engine_b: {
        zone_tf: 'H4',
        trigger_timeframe_actual: 'M30',
        entry_tf: 'M30',
      },
    })).toEqual({ structureTf: 'H4', entryTf: 'M30' });
  });

  it('reads Engine B snake_case structure_tf and nested triggerTf', () => {
    expect(visionReviewImageTimeframes({
      timeframe: 'H4',
      setupTf: 'M30',
      naked_data: {
        structure_tf: 'H1',
        triggerTf: 'M15',
        setup_tf: 'M30',
      },
    })).toEqual({ structureTf: 'H1', entryTf: 'M15' });
  });

  it('reads Engine B sourceTimeframes when top-level policy stamps are absent', () => {
    expect(visionReviewImageTimeframes({
      timeframe: 'H4',
      setupTf: 'M30',
      sourceTimeframes: { zone_tf: 'H1', trigger_tf: 'M15', entry_tf: 'M15' },
    })).toEqual({ structureTf: 'H1', entryTf: 'M15' });
  });

  it('does not invent structure or trigger roles from setupTf or chart timeframe', () => {
    expect(visionReviewImageTimeframes({
      timeframe: 'H4',
      setupTf: 'M30',
    })).toEqual({
      structureTf: '',
      entryTf: '',
    });
  });
});
