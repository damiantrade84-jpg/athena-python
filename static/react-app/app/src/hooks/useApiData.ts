import { useState, useEffect, useCallback, useRef } from 'react';
import apiClient from '@/lib/apiClient';

export function useApiPoll<T>(url: string, interval = 0, enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const staleRef = useRef(false);
  const fetchedRef = useRef(false);
  const inFlightRef = useRef(false);
  const pendingRef = useRef(false);

  const fetch = useCallback(async () => {
    if (!enabled) return;
    if (inFlightRef.current) {
      pendingRef.current = true;
      return;
    }
    inFlightRef.current = true;
    const isSubsequent = fetchedRef.current;
    if (isSubsequent) setIsRefreshing(true);
    else setLoading(true);
    staleRef.current = false;
    try {
      const result = await apiClient.getJson(url) as T;
      if (!staleRef.current) {
        setData(result);
        setError(null);
        fetchedRef.current = true;
      }
    } catch (e: any) {
      if (!staleRef.current) {
        setError(e.message || 'Unknown error');
      }
    } finally {
      if (!staleRef.current) {
        setLoading(false);
        setIsRefreshing(false);
      }
      inFlightRef.current = false;
      if (pendingRef.current) {
        pendingRef.current = false;
        void fetch();
      }
    }
  }, [url, enabled]);

  useEffect(() => {
    staleRef.current = true;
    fetchedRef.current = false;
    pendingRef.current = false;
    setLoading(true);
    setIsRefreshing(false);
  }, [url]);

  useEffect(() => {
    fetch();
    if (interval > 0) {
      const id = setInterval(fetch, interval);
      return () => clearInterval(id);
    }
  }, [fetch, interval]);

  return { data, loading, isRefreshing, error, refresh: fetch };
}

export function useApiPost<T>() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const post = useCallback(async (url: string, payload?: Record<string, unknown>): Promise<T | null> => {
    setLoading(true);
    setError(null);
    try {
      const result = await apiClient.postJson(url, payload) as T;
      return result;
    } catch (e: any) {
      setError(e.message || 'Unknown error');
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return { post, loading, error };
}

// Legacy compatibility exports
export function useApiData<T>({
  endpoint,
  fallback,
  intervalMs = 0,
  enabled = true,
}: {
  endpoint: string;
  fallback: T;
  intervalMs?: number;
  enabled?: boolean;
}) {
  const { data, loading, error, refresh } = useApiPoll<T>(endpoint, intervalMs, enabled);
  return { data: data ?? fallback, loading, error, refetch: refresh };
}

export function usePostMutation<T>(endpoint: string) {
  const { post, loading, error } = useApiPost<T>();
  const mutate = useCallback(
    async (payload?: Record<string, unknown>): Promise<T | null> => {
      return post(endpoint, payload);
    },
    [post, endpoint]
  );
  return { mutate, loading, error };
}
