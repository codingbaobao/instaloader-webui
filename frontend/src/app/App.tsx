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
import { ActivityPage } from "../library/ActivityPage";
import { AddPage } from "../library/AddPage";
import { HomePage } from "../library/HomePage";
import { MediaViewerPage } from "../library/MediaViewerPage";
import { ProfilePage } from "../library/ProfilePage";
import { ProfilesPage } from "../library/ProfilesPage";
import { SettingsPage } from "../library/SettingsPage";

type AppProps = Readonly<{
  initialSession?: SessionData | null;
}>;

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
      <span className="nav-symbol" aria-hidden="true">{destination.symbol}</span>
      <span>{destination.label}</span>
    </NavLink>
  ));
}

function AuthenticatedShell() {
  const { session } = useSession();

  if (session === null) {
    return <Navigate replace to="/login" />;
  }

  return (
    <div className="app-frame">
      <aside className="desktop-sidebar">
        <NavLink className="wordmark" to="/" aria-label="Instaloader WebUI home">Instaloader</NavLink>
        <nav aria-label="Desktop" className="desktop-navigation"><NavigationItems /></nav>
        <div className="account-actions" aria-label="Desktop account controls">
          <span>@{session.username}</span>
          <LogoutButton />
        </div>
      </aside>
      <main className="content-area">
        <header className="mobile-header">
          <NavLink className="wordmark" to="/">Instaloader</NavLink>
          <span className="mobile-account-actions" aria-label="Mobile account controls">
            <span className="avatar" aria-label={`Signed in as ${session.username}`}>{session.username.slice(0, 1).toUpperCase()}</span>
            <LogoutButton className="mobile-logout-button" />
          </span>
        </header>
        <Routes>
          <Route path="/" element={<HomePage session={session} />} />
          <Route path="/profiles" element={<ProfilesPage />} />
          <Route path="/profiles/:profileId" element={<ProfilePage session={session} />} />
          <Route path="/media/:mediaId" element={<MediaViewerPage session={session} />} />
          <Route path="/add" element={<AddPage session={session} />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="/settings" element={<SettingsPage session={session} />} />
          <Route path="*" element={<Navigate replace to="/" />} />
        </Routes>
      </main>
      <nav aria-label="Mobile" className="mobile-navigation"><NavigationItems /></nav>
    </div>
  );
}

export function AppRoutes() {
  const { session, status, errorMessage, refreshSession } = useSession();

  if (status === "loading") {
    return <main className="loading-screen" aria-live="polite">Loading…</main>;
  }
  if (status === "error") {
    return (
      <main className="auth-layout">
        <section className="auth-card" aria-labelledby="session-error-title">
          <p className="eyebrow">Session unavailable</p>
          <h1 id="session-error-title">Unable to restore your session</h1>
          <p className="auth-intro" role="alert">{errorMessage ?? "The session could not be restored."}</p>
          <button className="primary-button" type="button" onClick={() => void refreshSession()}>Retry</button>
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
    <BrowserRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
      <SessionProvider initialSession={initialSession}><AppRoutes /></SessionProvider>
    </BrowserRouter>
  );
}
