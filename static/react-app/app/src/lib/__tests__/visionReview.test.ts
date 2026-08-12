import { describe, expect, it } from 'vitest';

import { visionReviewImageTimeframes } from '../visionReview';

describe('visionReviewImageTimeframes', () => {
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

  it('does not invent a trigger timeframe when policy provenance is missing', () => {
    expect(visionReviewImageTimeframes({ timeframe: 'H4' })).toEqual({
      structureTf: 'H4',
      entryTf: '',
    });
  });
});
