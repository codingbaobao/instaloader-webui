import {
  BrowserRouter,
  Navigate,
  NavLink,
  Route,
  Routes,
} from "react-router-dom";

import { ChangePasswordPage } from "../auth/ChangePasswordPage";
import { LoginPage } from "../auth/LoginPage";
import { LogoutButton } from "../auth/LogoutButton";
import {
  SessionProvider,
  type SessionData,
  useSession,
} from "../auth/useSession";

type AppProps = {
  initialSession?: SessionData | null;
};

const destinations = [
  { path: "/", label: "Home", symbol: "⌂", end: true },
  { path: "/profiles", label: "Profiles", symbol: "◎" },
  { path: "/add", label: "Add", symbol: "+" },
  { path: "/activity", label: "Activity", symbol: "◷" },
  { path: "/settings", label: "Settings", symbol: "⚙" },
] as const;

function NavigationItems() {
  return destinations.map((destination) => (
    <NavLink
      className={({ isActive }) =>
        isActive ? "nav-link nav-link-active" : "nav-link"
      }
      end={"end" in destination ? destination.end : false}
      key={destination.path}
      to={destination.path}
    >
      <span className="nav-symbol" aria-hidden="true">
        {destination.symbol}
      </span>
      <span>{destination.label}</span>
    </NavLink>
  ));
}

function PlaceholderPage({
  title,
  detail,
}: {
  title: string;
  detail: string;
}) {
  return (
    <section className="page-panel" aria-labelledby={`${title}-title`}>
      <p className="eyebrow">Library</p>
      <h1 id={`${title}-title`}>{title}</h1>
      <p>{detail}</p>
    </section>
  );
}

function AuthenticatedShell() {
  const { session } = useSession();

  if (session === null) {
    return <Navigate replace to="/login" />;
  }

  return (
    <div className="app-frame">
      <aside className="desktop-sidebar">
        <a className="wordmark" href="/" aria-label="Instaloader WebUI home">
          Instaloader
        </a>
        <nav aria-label="Desktop" className="desktop-navigation">
          <NavigationItems />
        </nav>
        <div className="account-actions" aria-label="Desktop account controls">
          <span>@{session.username}</span>
          <LogoutButton />
        </div>
      </aside>
      <main className="content-area">
        <header className="mobile-header">
          <span className="wordmark">Instaloader</span>
          <span
            className="mobile-account-actions"
            aria-label="Mobile account controls"
          >
            <span
              className="avatar"
              aria-label={`Signed in as ${session.username}`}
            >
              {session.username.slice(0, 1).toUpperCase()}
            </span>
            <LogoutButton className="mobile-logout-button" />
          </span>
        </header>
        <Routes>
          <Route
            path="/"
            element={
              <PlaceholderPage
                title={`Welcome back, ${session.username}`}
                detail="Recently downloaded media will appear here."
              />
            }
          />
          <Route
            path="/profiles"
            element={
              <PlaceholderPage
                title="Profiles"
                detail="Downloaded profiles will be available in a later phase."
              />
            }
          />
          <Route
            path="/add"
            element={
              <PlaceholderPage
                title="Add"
                detail="Profile and post capture controls are coming next."
              />
            }
          />
          <Route
            path="/activity"
            element={
              <PlaceholderPage
                title="Activity"
                detail="Persistent download jobs will be shown here."
              />
            }
          />
          <Route
            path="/settings"
            element={
              <PlaceholderPage
                title="Settings"
                detail="Synchronization and account controls will live here."
              />
            }
          />
          <Route path="*" element={<Navigate replace to="/" />} />
        </Routes>
      </main>
      <nav aria-label="Mobile" className="mobile-navigation">
        <NavigationItems />
      </nav>
    </div>
  );
}

export function AppRoutes() {
  const { session, status, errorMessage, refreshSession } = useSession();

  if (status === "loading") {
    return (
      <main className="loading-screen" aria-live="polite">
        Loading…
      </main>
    );
  }
  if (status === "error") {
    return (
      <main className="auth-layout">
        <section className="auth-card" aria-labelledby="session-error-title">
          <p className="eyebrow">Session unavailable</p>
          <h1 id="session-error-title">Unable to restore your session</h1>
          <p className="auth-intro" role="alert">
            {errorMessage ?? "The session could not be restored."}
          </p>
          <button
            className="primary-button"
            type="button"
            onClick={() => void refreshSession()}
          >
            Retry
          </button>
        </section>
      </main>
    );
  }
  if (status === "unauthenticated" || session === null) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate replace to="/login" />} />
      </Routes>
    );
  }
  if (session.must_change_password) {
    return (
      <Routes>
        <Route path="/change-password" element={<ChangePasswordPage />} />
        <Route path="*" element={<Navigate replace to="/change-password" />} />
      </Routes>
    );
  }
  return <AuthenticatedShell />;
}

export function App({ initialSession }: AppProps) {
  return (
    <BrowserRouter
      future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
    >
      <SessionProvider initialSession={initialSession}>
        <AppRoutes />
      </SessionProvider>
    </BrowserRouter>
  );
}
