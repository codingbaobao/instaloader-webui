import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../app/api";

type PollingState<T> = Readonly<{
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}>;

/** Reload data immediately, then on an interval while the consumer is mounted. */
export function usePolling<T>(
  load: () => Promise<T>,
  intervalMs: number,
  enabled: boolean,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    if (!enabled) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await load();
      if (mounted.current) {
        setData(result);
      }
    } catch (cause) {
      if (mounted.current) {
        setError(
          cause instanceof ApiError
            ? cause.message
            : "The library could not be loaded. Please try again.",
        );
      }
    } finally {
      if (mounted.current) {
        setLoading(false);
      }
    }
  }, [enabled, load]);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    void reload();
    if (intervalMs <= 0) {
      return;
    }
    const timer = window.setInterval(() => void reload(), intervalMs);
    return () => window.clearInterval(timer);
  }, [enabled, intervalMs, reload]);

  return { data, loading, error, reload };
}
