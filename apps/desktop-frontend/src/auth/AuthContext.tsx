import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { DesktopApiClient } from "../api/client";
import type { LocalSession } from "../api/types";

interface AuthContextValue {
  session: LocalSession | null;
  api: DesktopApiClient;
  isAuthenticated: boolean;
  authMessage: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  clearSession: (message?: string) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<LocalSession | null>(null);
  const [authMessage, setAuthMessage] = useState<string | null>(null);
  const sessionRef = useRef<LocalSession | null>(null);

  const clearSession = useCallback((message?: string) => {
    sessionRef.current = null;
    setSession(null);
    setAuthMessage(message ?? null);
  }, []);

  const api = useMemo(
    () =>
      new DesktopApiClient({
        getSession: () =>
          sessionRef.current
            ? {
                accessToken: sessionRef.current.access_token,
                sessionToken: sessionRef.current.session_token
              }
            : null,
        onUnauthorized: () => clearSession("Your local session expired. Sign in again.")
      }),
    [clearSession]
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const nextSession = await api.login({ email, password });
      sessionRef.current = nextSession;
      setSession(nextSession);
      setAuthMessage(null);
    },
    [api]
  );

  const logout = useCallback(async () => {
    try {
      if (sessionRef.current) {
        await api.logout();
      }
    } finally {
      clearSession();
    }
  }, [api, clearSession]);

  const value = useMemo(
    () => ({
      session,
      api,
      isAuthenticated: Boolean(session),
      authMessage,
      login,
      logout,
      clearSession
    }),
    [api, authMessage, clearSession, login, logout, session]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
