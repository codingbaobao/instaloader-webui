import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { ApiError, apiRequest } from "../app/api";
import {
  parseSessionData,
  requestSession,
  type SessionData,
} from "./sessionData";

export type { SessionData } from "./sessionData";

export type SessionStatus =
  | "loading"
  | "authenticated"
  | "unauthenticated"
  | "error";

type SessionState = {
  status: SessionStatus;
  session: SessionData | null;
  errorMessage: string | null;
};

type SessionContextValue = SessionState & {
  logoutPending: boolean;
  setSession: (session: SessionData) => void;
  clearSession: () => void;
  refreshSession: () => Promise<void>;
  applySessionOperation: (
    operation: () => Promise<SessionData>,
  ) => Promise<SessionData | null>;
  logout: () => Promise<boolean>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

type SessionProviderProps = {
  children: ReactNode;
  initialSession?: SessionData | null;
};

const unauthenticatedState: SessionState = {
  status: "unauthenticated",
  session: null,
  errorMessage: null,
};

function errorState(error: unknown): SessionState {
  if (error instanceof ApiError && error.code === "authentication_required") {
    return unauthenticatedState;
  }
  return {
    status: "error",
    session: null,
    errorMessage:
      error instanceof ApiError
        ? error.message
        : "The session could not be restored.",
  };
}

async function loadRemoteSessionState(): Promise<SessionState> {
  try {
    return {
      status: "authenticated",
      session: await requestSession("/api/auth/session"),
      errorMessage: null,
    };
  } catch (error) {
    return errorState(error);
  }
}

function initialState(
  initialSession: SessionData | null | undefined,
): SessionState {
  if (initialSession === undefined) {
    return { status: "loading", session: null, errorMessage: null };
  }
  if (initialSession === null) {
    return unauthenticatedState;
  }
  try {
    return {
      status: "authenticated",
      session: parseSessionData(initialSession),
      errorMessage: null,
    };
  } catch (error) {
    return errorState(error);
  }
}

export function SessionProvider({
  children,
  initialSession,
}: SessionProviderProps) {
  const [state, setState] = useState<SessionState>(() =>
    initialState(initialSession),
  );
  const [logoutPending, setLogoutPending] = useState(false);
  const generation = useRef(0);
  const logoutLock = useRef(false);
  const logoutOperation = useRef<Promise<boolean> | null>(null);
  const sessionRef = useRef<SessionData | null>(state.session);

  useEffect(() => {
    sessionRef.current = state.session;
  }, [state.session]);

  const beginSessionOperation = useCallback((): number => {
    generation.current += 1;
    return generation.current;
  }, []);

  const commitSessionState = useCallback(
    (operationGeneration: number, nextState: SessionState): boolean => {
      if (generation.current !== operationGeneration) {
        return false;
      }
      sessionRef.current = nextState.session;
      setState(nextState);
      return true;
    },
    [],
  );

  useEffect(() => {
    if (initialSession !== undefined) {
      return;
    }
    const operationGeneration = beginSessionOperation();
    void loadRemoteSessionState().then((loadedState) => {
      commitSessionState(operationGeneration, loadedState);
    });
    return () => {
      if (generation.current === operationGeneration) {
        generation.current += 1;
      }
    };
  }, [beginSessionOperation, commitSessionState, initialSession]);

  const refreshSession = useCallback(async () => {
    if (logoutLock.current) {
      return;
    }
    const operationGeneration = beginSessionOperation();
    commitSessionState(operationGeneration, {
      status: "loading",
      session: null,
      errorMessage: null,
    });
    commitSessionState(
      operationGeneration,
      await loadRemoteSessionState(),
    );
  }, [beginSessionOperation, commitSessionState]);

  const clearSession = useCallback(() => {
    const operationGeneration = beginSessionOperation();
    commitSessionState(operationGeneration, unauthenticatedState);
  }, [beginSessionOperation, commitSessionState]);

  const setSession = useCallback((session: SessionData) => {
    if (logoutLock.current) {
      return;
    }
    const operationGeneration = beginSessionOperation();
    try {
      commitSessionState(operationGeneration, {
        status: "authenticated",
        session: parseSessionData(session),
        errorMessage: null,
      });
    } catch (error) {
      commitSessionState(operationGeneration, errorState(error));
    }
  }, [beginSessionOperation, commitSessionState]);

  const applySessionOperation = useCallback(
    async (
      operation: () => Promise<SessionData>,
    ): Promise<SessionData | null> => {
      if (logoutLock.current) {
        return null;
      }
      const operationGeneration = beginSessionOperation();
      const session = parseSessionData(await operation());
      if (
        !commitSessionState(operationGeneration, {
          status: "authenticated",
          session,
          errorMessage: null,
        })
      ) {
        return null;
      }
      return session;
    },
    [beginSessionOperation, commitSessionState],
  );

  const logout = useCallback((): Promise<boolean> => {
    if (logoutOperation.current !== null) {
      return logoutOperation.current;
    }
    const currentSession = sessionRef.current;
    if (currentSession === null) {
      clearSession();
      return Promise.resolve(true);
    }
    logoutLock.current = true;
    const operationGeneration = beginSessionOperation();
    const operation = (async () => {
      setLogoutPending(true);
      try {
        await apiRequest<{ logged_out: boolean }>("/api/auth/logout", {
          method: "POST",
          csrfToken: currentSession.csrf_token,
        });
        return commitSessionState(
          operationGeneration,
          unauthenticatedState,
        );
      } catch (error) {
        if (
          error instanceof ApiError &&
          error.code === "authentication_required"
        ) {
          return commitSessionState(
            operationGeneration,
            unauthenticatedState,
          );
        }
        throw error;
      } finally {
        logoutLock.current = false;
        setLogoutPending(false);
        logoutOperation.current = null;
      }
    })();
    logoutOperation.current = operation;
    return operation;
  }, [
    beginSessionOperation,
    clearSession,
    commitSessionState,
  ]);

  const value = useMemo<SessionContextValue>(
    () => ({
      ...state,
      logoutPending,
      setSession,
      clearSession,
      refreshSession,
      applySessionOperation,
      logout,
    }),
    [
      applySessionOperation,
      clearSession,
      logout,
      logoutPending,
      refreshSession,
      setSession,
      state,
    ],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (context === null) {
    throw new Error("useSession must be used within SessionProvider");
  }
  return context;
}
