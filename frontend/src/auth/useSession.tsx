import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { apiRequest } from "../app/api";

export type SessionData = {
  username: string;
  must_change_password: boolean;
  expires_at: string;
  csrf_token: string;
};

type SessionContextValue = {
  session: SessionData | null;
  loading: boolean;
  setSession: (session: SessionData) => void;
  clearSession: () => void;
  refreshSession: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

type SessionProviderProps = {
  children: ReactNode;
  initialSession?: SessionData | null;
};

export function SessionProvider({
  children,
  initialSession,
}: SessionProviderProps) {
  const [session, updateSession] = useState<SessionData | null>(
    initialSession ?? null,
  );
  const [loading, setLoading] = useState(initialSession === undefined);

  const fetchSession = useCallback(async (): Promise<SessionData | null> => {
    try {
      return await apiRequest<SessionData>("/api/auth/session");
    } catch {
      return null;
    }
  }, []);

  const refreshSession = useCallback(async () => {
    setLoading(true);
    updateSession(await fetchSession());
    setLoading(false);
  }, [fetchSession]);

  useEffect(() => {
    if (initialSession !== undefined) {
      return;
    }
    let active = true;
    void fetchSession().then((loadedSession) => {
      if (active) {
        updateSession(loadedSession);
        setLoading(false);
      }
    });
    return () => {
      active = false;
    };
  }, [fetchSession, initialSession]);

  const value = useMemo<SessionContextValue>(
    () => ({
      session,
      loading,
      setSession: updateSession,
      clearSession: () => updateSession(null),
      refreshSession,
    }),
    [loading, refreshSession, session],
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
