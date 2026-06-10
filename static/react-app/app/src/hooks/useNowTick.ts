import { useEffect, useState } from 'react';

/** Re-renders on a fixed interval and returns the current time. */
export function useNowTick(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
