import apiClient from '@/lib/apiClient';
import type {
  GhostGroup, GhostHealth, GhostPerformance, GhostPosition, GhostScan, GhostSignal,
} from './types';

export async function getGhostHealth(): Promise<GhostHealth> {
  return apiClient.get<GhostHealth>('/api/ghost-trade/health');
}
export async function getGhostGroups(): Promise<GhostGroup[]> {
  const result = await apiClient.get<{ groups: GhostGroup[] }>('/api/ghost-trade/groups');
  return result.groups;
}
export async function getGhostSignals(): Promise<GhostSignal[]> {
  const result = await apiClient.get<{ signals: GhostSignal[] }>('/api/ghost-trade/signals?limit=1000');
  return result.signals;
}
export async function getGhostPositions(): Promise<GhostPosition[]> {
  const result = await apiClient.get<{ positions: GhostPosition[] }>('/api/ghost-trade/positions');
  return result.positions;
}
export async function getGhostPerformance(): Promise<GhostPerformance> {
  return apiClient.get<GhostPerformance>('/api/ghost-trade/performance');
}
export async function getCurrentGhostScan(): Promise<GhostScan | null> {
  try {
    return await apiClient.get<GhostScan>('/api/ghost-trade/scans/current');
  } catch (error) {
    if (error instanceof Error && error.message === 'scan_not_found') return null;
    throw error;
  }
}
export async function runGhostScan(): Promise<GhostScan> {
  return apiClient.post<GhostScan>('/api/ghost-trade/scan', { style: 'intraday' });
}
export async function executeGhostSignal(signalId: string): Promise<Record<string, unknown>> {
  return apiClient.post(`/api/ghost-trade/signals/${encodeURIComponent(signalId)}/execute-demo`, {});
}
export async function dismissGhostSignal(signalId: string): Promise<Record<string, unknown>> {
  return apiClient.post(`/api/ghost-trade/signals/${encodeURIComponent(signalId)}/dismiss`, {});
}
export async function closeGhostPosition(positionId: string): Promise<Record<string, unknown>> {
  return apiClient.post(`/api/ghost-trade/positions/${encodeURIComponent(positionId)}/close`, {});
}
export async function setGhostAutoEnabled(enabled: boolean): Promise<Record<string, unknown>> {
  const response = await fetch('/api/ghost-trade/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ demoAutoEnabled: enabled }),
  });
  const body = await response.json() as Record<string, unknown>;
  if (!response.ok) throw new Error(String(body.error || `HTTP ${response.status}`));
  return body;
}
