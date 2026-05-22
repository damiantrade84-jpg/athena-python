import { useApiPoll } from '@/hooks/useApiData';
import type { SuggestedTradeRunnerStatus } from '@/types/athena';

interface SuggestedTradeStatusResponse {
  ok?: boolean;
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
    loading,
    error,
    refresh,
  };
}
