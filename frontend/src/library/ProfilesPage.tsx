import { useCallback, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { SessionData } from "../auth/useSession";
import { getInstagramSession, listProfiles, startFolloweeImport } from "./api";
import { formatDate } from "./MediaGrid";
import { ProfileAvatar } from "./ProfileAvatar";
import { usePolling } from "./usePolling";

function deletionStatusBadge(status: string): Readonly<{
  className: string;
  label: string;
}> {
  return status === "deletion_failed"
    ? {
        className: "status-badge status-badge-failed",
        label: "Deletion failed",
      }
    : {
        className: "status-badge status-badge-pending",
        label: "Deletion pending",
      };
}

type ProfilesPageProps = Readonly<{ session: SessionData }>;

export function ProfilesPage({ session }: ProfilesPageProps) {
  const navigate = useNavigate();
  const [startPending, setStartPending] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const loadProfiles = useCallback(
    (signal: AbortSignal) => listProfiles(signal),
    [],
  );
  const { data: profiles, error, loading, reload } = usePolling(loadProfiles, 0, true);
  const loadInstagramSession = useCallback(
    (signal: AbortSignal) => getInstagramSession(signal),
    [],
  );
  const {
    data: instagramSession,
    error: instagramSessionError,
    loading: instagramSessionLoading,
  } = usePolling(loadInstagramSession, 0, true);
  const sessionReady = instagramSession?.configured === true;
  const importDisabled = !sessionReady || instagramSessionLoading || startPending;
  const importHint = instagramSessionError
    ? "Instagram session status is unavailable. Check Settings before importing."
    : instagramSession === null || instagramSessionLoading
      ? "Checking the Instagram session."
      : sessionReady
        ? "Uses the connected Instagram session."
        : "Import an Instagram Cookie file in Settings first.";

  async function handleStartImport(): Promise<void> {
    if (importDisabled) {
      return;
    }
    setStartPending(true);
    setStartError(null);
    try {
      const batch = await startFolloweeImport(session.csrf_token);
      navigate(`/profiles/import-followings/${encodeURIComponent(batch.id)}`);
    } catch (cause) {
      setStartError(
        cause instanceof Error
          ? cause.message
          : "The followings import could not be started.",
      );
      setStartPending(false);
    }
  }

  return (
    <section className="library-page" aria-labelledby="profiles-title">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Library</p>
          <h1 id="profiles-title">Profiles</h1>
          <p className="page-intro">Instagram accounts you have added for download and synchronization.</p>
        </div>
        <div className="profile-heading-actions">
          <div className="profile-import-action">
            <button
              aria-describedby="import-followings-hint"
              className="secondary-button compact-button"
              disabled={importDisabled}
              title={!sessionReady ? importHint : undefined}
              type="button"
              onClick={() => void handleStartImport()}
            >
              {startPending ? "Starting…" : "Import followings"}
            </button>
            <p className="profile-import-hint" id="import-followings-hint">
              {importHint}{" "}
              {!sessionReady && !instagramSessionLoading ? (
                <Link className="text-link" to="/settings">Settings</Link>
              ) : null}
            </p>
          </div>
          <Link className="primary-button compact-button" to="/add">Add profile</Link>
        </div>
      </header>

      {startError ? <div className="inline-error" role="alert">{startError}</div> : null}
      {error ? (
        <div className="inline-error" role="alert">
          <span>{error}</span>
          <button className="text-button" type="button" onClick={() => void reload()}>Try again</button>
        </div>
      ) : null}
      {profiles === null && loading ? <p className="loading-copy">Loading profiles…</p> : null}
      {profiles?.length === 0 ? (
        <section className="empty-state">
          <span className="empty-state-mark" aria-hidden="true">@</span>
          <h2>No profiles added</h2>
          <p>Save an Instagram profile to keep its latest posts and reels together.</p>
          <Link className="primary-button compact-button" to="/add">Add a profile</Link>
        </section>
      ) : null}
      {profiles?.length ? (
        <div className="profiles-list">
          {profiles.map((profile) => (
            <Link className="profile-card" key={profile.id} to={`/profiles/${encodeURIComponent(profile.id)}`}>
              <ProfileAvatar profile={profile} />
              <span className="profile-card-main">
                <span className="profile-card-title">@{profile.username}</span>
                <span className="profile-card-name">{profile.full_name || "Public Instagram profile"}</span>
                <span className="profile-card-meta">{profile.media_count} saved · Last attempt {formatDate(profile.last_sync_attempted_at)}</span>
              </span>
              <span className="profile-card-badges">
                <span
                  className={
                    profile.tracked
                      ? "status-badge status-badge-sync-active"
                      : "status-badge status-badge-sync-stopped"
                  }
                >
                  {profile.tracked ? "Sync active" : "Sync stopped"}
                </span>
                {profile.status !== "active" ? (
                  <span className={deletionStatusBadge(profile.status).className}>
                    {deletionStatusBadge(profile.status).label}
                  </span>
                ) : null}
              </span>
            </Link>
          ))}
        </div>
      ) : null}
    </section>
  );
}
