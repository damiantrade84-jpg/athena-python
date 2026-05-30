import { useState } from 'react';
import type { ExitModeSelection } from '@/lib/manualExecuteHelpers';

// Per-trade exit-mode override. 'default' = no override (backend resolves
// per-group -> global). Mirrors useExecutionVolumeState's shape.
export function useExitModeState(defaultMode: ExitModeSelection = 'default') {
  const [exitMode, setExitMode] = useState<ExitModeSelection>(defaultMode);
  return { exitMode, setExitMode };
}
