import { describe, expect, it, vi } from 'vitest';

import { confirmAutoToggle } from './display';


describe('Ghost Trade demo-auto confirmation', () => {
  it('requires explicit confirmation before enabling', () => {
    const confirm = vi.fn(() => true);

    expect(confirmAutoToggle(true, confirm)).toBe(true);
    expect(confirm).toHaveBeenCalledWith(
      'Enable Ghost Trade DEMO_AUTO? Orders can be sent only to currently verified demo/testnet accounts.',
    );
  });

  it('does not ask for confirmation when disabling', () => {
    const confirm = vi.fn(() => false);

    expect(confirmAutoToggle(false, confirm)).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
  });

  it('honours a cancelled enable confirmation', () => {
    expect(confirmAutoToggle(true, () => false)).toBe(false);
  });
});
