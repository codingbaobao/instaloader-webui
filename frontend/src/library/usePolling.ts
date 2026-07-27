import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../app/api";

type PollingState<T> = Readonly<{
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}>;

type PollController = {
  version: number;
  cancelled: boolean;
  timeoutId: number | null;
  abortController: AbortController | null;
  inFlight: boolean;
  reloadQueued: boolean;
  reloadWaiters: Array<() => void>;
  run: () => Promise<void>;
};

function resolveReloadWaiters(controller: PollController): void {
  const waiters = controller.reloadWaiters;
  controller.reloadWaiters = [];
  waiters.forEach((resolve) => resolve());
}

function wasAborted(cause: unknown, signal: AbortSignal): boolean {
  return (
    signal.aborted ||
    (cause instanceof Error && cause.name === "AbortError")
  );
}

/**
 * Loads data immediately and, when requested, only schedules the next poll
 * after the current request settles. Each effect owns a versioned controller,
 * so stale responses cannot update state after a route or load callback change.
 */
export function usePolling<T>(
  load: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  enabled: boolean,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<PollController | null>(null);
  const versionRef = useRef(0);

  const reload = useCallback((): Promise<void> => {
    const controller = controllerRef.current;
    if (controller === null || controller.cancelled) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      controller.reloadWaiters.push(resolve);
      controller.reloadQueued = true;
      if (controller.timeoutId !== null) {
        window.clearTimeout(controller.timeoutId);
        controller.timeoutId = null;
      }
      if (!controller.inFlight) {
        void controller.run();
      }
    });
  }, []);

  useEffect(() => {
    if (!enabled) {
      versionRef.current += 1;
      controllerRef.current = null;
      setLoading(false);
      return;
    }

    const version = versionRef.current + 1;
    versionRef.current = version;
    const controller: PollController = {
      version,
      cancelled: false,
      timeoutId: null,
      abortController: null,
      inFlight: false,
      reloadQueued: false,
      reloadWaiters: [],
      run: async () => {},
    };
    const isCurrent = () =>
      !controller.cancelled &&
      controllerRef.current === controller &&
      versionRef.current === controller.version;

    controller.run = async () => {
      if (!isCurrent() || controller.inFlight) {
        return;
      }
      const reloadRun = controller.reloadQueued;
      controller.reloadQueued = false;
      controller.inFlight = true;
      const requestController = new AbortController();
      controller.abortController = requestController;
      setLoading(true);
      setError(null);
      try {
        const result = await load(requestController.signal);
        if (isCurrent()) {
          setData(result);
        }
      } catch (cause) {
        if (!wasAborted(cause, requestController.signal) && isCurrent()) {
          setError(
            cause instanceof ApiError
              ? cause.message
              : "The library could not be loaded. Please try again.",
          );
        }
      } finally {
        if (controller.abortController === requestController) {
          controller.abortController = null;
        }
        controller.inFlight = false;
        if (controller.cancelled) {
          resolveReloadWaiters(controller);
          return;
        }
        if (controller.reloadQueued) {
          void controller.run();
          return;
        }
        if (reloadRun) {
          resolveReloadWaiters(controller);
        }
        if (intervalMs > 0 && isCurrent()) {
          controller.timeoutId = window.setTimeout(() => {
            controller.timeoutId = null;
            void controller.run();
          }, intervalMs);
        }
        if (isCurrent()) {
          setLoading(false);
        }
      }
    };

    controllerRef.current = controller;
    setData(null);
    setError(null);
    void controller.run();

    return () => {
      controller.cancelled = true;
      controller.abortController?.abort();
      if (controller.timeoutId !== null) {
        window.clearTimeout(controller.timeoutId);
      }
      resolveReloadWaiters(controller);
      if (controllerRef.current === controller) {
        controllerRef.current = null;
      }
      if (versionRef.current === controller.version) {
        versionRef.current += 1;
      }
    };
  }, [enabled, intervalMs, load]);

  return { data, loading, error, reload };
}
