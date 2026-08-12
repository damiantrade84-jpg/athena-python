import { useApiPoll } from '@/hooks/useApiData';
import type { SuggestedTradeRunnerStatus } from '@/types/athena';

interface SuggestedTradeStatusResponse {
  ok?: boolean;
  counts?: {
    active?: number;
    ready?: number;
    expired?: number;
    cancelled?: number;
    invalidated?: number;
  };
  runner?: SuggestedTradeRunnerStatus;
  alert_only?: boolean;
}

const POLL_MS = 30_000;

export function useSuggestedTradeRunnerStatus(enabled = true) {
  const { data, loading, error, refresh } = useApiPoll<SuggestedTradeStatusResponse>(
    '/api/suggested-trades/status',
    POLL_MS,
    enabled,
  );
  return {
    runner: data?.runner,
    readyCount: data?.counts?.ready ?? 0,
    loading,
    error,
    refresh,
  };
}
