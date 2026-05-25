import { useEffect, useState } from 'react';
import apiClient from '@/lib/apiClient';
import type { ExecutionVolumeMode, RiskPreviewResponse } from '@/lib/manualExecuteHelpers';
import { formatRiskPreviewLine } from '@/lib/manualExecuteHelpers';

export function useExecutionVolumeState(defaultMode: ExecutionVolumeMode = 'min_lot') {
  const [volumeMode, setVolumeMode] = useState<ExecutionVolumeMode>(defaultMode);
  const [sizingOverride, setSizingOverride] = useState(1.0);

  useEffect(() => {
    const win = window as unknown as Record<string, unknown>;
    win.__execVolumeMode = volumeMode;
    win.__execRiskSizing = sizingOverride;
  }, [volumeMode, sizingOverride]);

  return {
    volumeMode,
    setVolumeMode,
    sizingOverride,
    setSizingOverride,
  };
}

export async function fetchRiskPreview(
  body: Record<string, unknown>,
): Promise<RiskPreviewResponse | null> {
  try {
    return await apiClient.postJson('/api/risk-preview', body) as RiskPreviewResponse;
  } catch {
    return null;
  }
}

export { formatRiskPreviewLine };
