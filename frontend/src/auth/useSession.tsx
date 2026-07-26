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
  logout: () => Promise<void>;
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

function initialState(initialSession: SessionData | null | undefined): SessionState {
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
  const logoutOperation = useRef<Promise<void> | null>(null);
  const sessionRef = useRef<SessionData | null>(state.session);

  useEffect(() => {
    sessionRef.current = state.session;
  }, [state.session]);

  const loadSession = useCallback(async (): Promise<SessionState> => {
    try {
      return {
        status: "authenticated",
        session: await requestSession("/api/auth/session"),
        errorMessage: null,
      };
    } catch (error) {
      return errorState(error);
    }
  }, []);

  const refreshSession = useCallback(async () => {
    setState({ status: "loading", session: null, errorMessage: null });
    setState(await loadSession());
  }, [loadSession]);

  useEffect(() => {
    if (initialSession !== undefined) {
      return;
    }
    let active = true;
    void loadSession().then((loadedState) => {
      if (active) {
        setState(loadedState);
      }
    });
    return () => {
      active = false;
    };
  }, [initialSession, loadSession]);

  const clearSession = useCallback(() => {
    sessionRef.current = null;
    setState(unauthenticatedState);
  }, []);

  const setSession = useCallback((session: SessionData) => {
    try {
      setState({
        status: "authenticated",
        session: parseSessionData(session),
        errorMessage: null,
      });
    } catch (error) {
      setState(errorState(error));
    }
  }, []);

  const logout = useCallback((): Promise<void> => {
    if (logoutOperation.current !== null) {
      return logoutOperation.current;
    }
    const currentSession = sessionRef.current;
    if (currentSession === null) {
      clearSession();
      return Promise.resolve();
    }
    const operation = (async () => {
      setLogoutPending(true);
      try {
        await apiRequest<{ logged_out: boolean }>("/api/auth/logout", {
          method: "POST",
          csrfToken: currentSession.csrf_token,
        });
        clearSession();
      } catch (error) {
        if (
          error instanceof ApiError &&
          error.code === "authentication_required"
        ) {
          clearSession();
          return;
        }
        throw error;
      } finally {
        setLogoutPending(false);
        logoutOperation.current = null;
      }
    })();
    logoutOperation.current = operation;
    return operation;
  }, [clearSession]);

  const value = useMemo<SessionContextValue>(
    () => ({
      ...state,
      logoutPending,
      setSession,
      clearSession,
      refreshSession,
      logout,
    }),
    [
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
