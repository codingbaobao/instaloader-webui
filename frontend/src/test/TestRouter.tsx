import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { AppRoutes } from "../app/App";
import {
  SessionProvider,
  type SessionData,
} from "../auth/useSession";

type TestRouterProps = {
  children?: ReactNode;
  initialPath?: string;
  initialSession?: SessionData | null;
};

export function TestRouter({
  children,
  initialPath = "/",
  initialSession = null,
}: TestRouterProps) {
  return (
    <MemoryRouter
      initialEntries={[initialPath]}
      future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
    >
      <SessionProvider initialSession={initialSession}>
        {children ?? <AppRoutes />}
      </SessionProvider>
    </MemoryRouter>
  );
}
