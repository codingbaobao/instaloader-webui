import { useCallback } from "react";
import { Link } from "react-router-dom";

import { listProfiles } from "./api";
import { formatDate } from "./MediaGrid";
import { usePolling } from "./usePolling";

export function ProfilesPage() {
  const loadProfiles = useCallback(() => listProfiles(), []);
  const { data: profiles, error, loading, reload } = usePolling(loadProfiles, 0, true);

  return (
    <section className="library-page" aria-labelledby="profiles-title">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Library</p>
          <h1 id="profiles-title">Profiles</h1>
          <p className="page-intro">Public accounts you have added for download and synchronization.</p>
        </div>
        <Link className="primary-button compact-button" to="/add">Add profile</Link>
      </header>

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
          <p>Save a public profile to keep its latest posts and reels together.</p>
          <Link className="primary-button compact-button" to="/add">Add a profile</Link>
        </section>
      ) : null}
      {profiles?.length ? (
        <div className="profiles-list">
          {profiles.map((profile) => (
            <Link className="profile-card" key={profile.id} to={`/profiles/${encodeURIComponent(profile.id)}`}>
              <span className="profile-avatar" aria-hidden="true">{profile.username.slice(0, 1).toUpperCase()}</span>
              <span className="profile-card-main">
                <span className="profile-card-title">@{profile.username}</span>
                <span className="profile-card-name">{profile.full_name || "Public Instagram profile"}</span>
                <span className="profile-card-meta">{profile.media_count} saved · Last attempt {formatDate(profile.last_sync_attempted_at)}</span>
              </span>
              <span className={`status-badge status-badge-${profile.status}`}>{profile.tracked ? profile.status : "untracked"}</span>
            </Link>
          ))}
        </div>
      ) : null}
    </section>
  );
}
