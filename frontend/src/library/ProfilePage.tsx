import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../app/api";
import type { SessionData } from "../auth/useSession";
import {
  deleteProfile,
  getProfile,
  listMedia,
  setProfileSyncEnabled,
  syncProfile,
} from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { formatDate } from "./dateFormatters";
import { MediaGrid } from "./MediaGrid";
import { ProfileAvatar } from "./ProfileAvatar";
import type { JobSummary } from "./types";
import { usePolling } from "./usePolling";

type ProfilePageProps = Readonly<{ session: SessionData }>;
type MediaTab = "post" | "reel";

export function ProfilePage({ session }: ProfilePageProps) {
  const { profileId = "" } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState<MediaTab>("post");
  const [actionJob, setActionJob] = useState<JobSummary | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncSetting, setSyncSetting] = useState(false);
  const [stopSyncOpen, setStopSyncOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const loadProfile = useCallback(async (signal: AbortSignal) => {
    if (!profileId) {
      throw new Error("The profile was not found.");
    }
    const [profile, media] = await Promise.all([
      getProfile(profileId, signal),
      listMedia({ profileId, kind: tab, limit: 200 }, signal),
    ]);
    return { profile, media };
  }, [profileId, tab]);
  const { data, error, loading, reload } = usePolling(loadProfile, 0, true);

  async function requestSync() {
    if (!profileId) {
      return;
    }
    setSyncing(true);
    setActionError(null);
    try {
      setActionJob(await syncProfile(profileId, session.csrf_token));
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "The profile could not be synchronized.");
    } finally {
      setSyncing(false);
    }
  }

  async function updateSyncSetting(enabled: boolean) {
    if (!profileId) {
      return;
    }
    setSyncSetting(true);
    setActionError(null);
    try {
      await setProfileSyncEnabled(profileId, enabled, session.csrf_token);
      setStopSyncOpen(false);
      await reload();
    } catch (cause) {
      setActionError(
        cause instanceof ApiError
          ? cause.message
          : "The profile synchronization setting could not be changed.",
      );
    } finally {
      setSyncSetting(false);
    }
  }

  async function confirmDeletion() {
    if (!profileId) {
      return;
    }
    setActionError(null);
    try {
      await deleteProfile(profileId, session.csrf_token);
      setDeleteOpen(false);
      navigate("/activity");
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "The profile could not be deleted.");
    }
  }

  if (data === null) {
    return (
      <section className="library-page narrow-page" aria-live="polite">
        {loading ? <p className="loading-copy">Loading profile…</p> : null}
        {error ? (
          <div className="inline-error" role="alert">
            <span>{error}</span>
            <button className="text-button" type="button" onClick={() => void reload()}>Try again</button>
          </div>
        ) : null}
      </section>
    );
  }

  const { profile, media } = data;
  return (
    <section className="library-page profile-page" aria-labelledby="profile-title">
      <Link className="back-link" to="/profiles">Back to profiles</Link>
      <header className="profile-header">
        <ProfileAvatar className="profile-avatar-large" profile={profile} />
        <div className="profile-header-main">
          <div className="profile-title-row">
            <h1 id="profile-title">@{profile.username}</h1>
            <span className={`status-badge status-badge-${profile.status}`}>{profile.status}</span>
            <span
              className={
                profile.tracked
                  ? "status-badge status-badge-sync-active"
                  : "status-badge status-badge-sync-stopped"
              }
            >
              {profile.tracked ? "Sync active" : "Sync stopped"}
            </span>
          </div>
          {profile.full_name ? <p className="profile-full-name">{profile.full_name}</p> : null}
          {profile.biography ? <p className="profile-biography">{profile.biography}</p> : <p className="profile-biography muted-copy">No public biography saved yet.</p>}
          <div className="profile-stats">
            <span><strong>{profile.media_count}</strong> saved</span>
            <span>Last attempt {formatDate(profile.last_sync_attempted_at)}</span>
          </div>
          <div className="profile-actions">
            <button className="secondary-button" type="button" disabled={!profile.tracked || syncing || syncSetting} onClick={() => void requestSync()}>
              {syncing ? "Queueing…" : "Sync now"}
            </button>
            {profile.tracked ? (
              <button className="secondary-button" type="button" disabled={syncSetting} onClick={() => setStopSyncOpen(true)}>
                Stop sync
              </button>
            ) : (
              <button className="secondary-button" type="button" disabled={syncSetting} onClick={() => void updateSyncSetting(true)}>
                Resume sync
              </button>
            )}
            <button className="danger-button danger-button-outline" type="button" onClick={() => setDeleteOpen(true)}>
              Delete profile
            </button>
          </div>
          {actionJob ? <p className="action-note" aria-live="polite">{actionJob.status_text}</p> : null}
          {actionError ? <p className="form-error" role="alert">{actionError}</p> : null}
        </div>
      </header>

      <div className="media-tabs" role="tablist" aria-label="Profile media">
        <button className={tab === "post" ? "media-tab media-tab-active" : "media-tab"} id="posts-tab" role="tab" type="button" aria-selected={tab === "post"} onClick={() => setTab("post")}>Posts</button>
        <button className={tab === "reel" ? "media-tab media-tab-active" : "media-tab"} id="reels-tab" role="tab" type="button" aria-selected={tab === "reel"} onClick={() => setTab("reel")}>Reels</button>
      </div>
      {error ? (
        <div className="inline-error" role="alert">
          <span>{error}</span>
          <button className="text-button" type="button" onClick={() => void reload()}>Try again</button>
        </div>
      ) : null}
      <div aria-labelledby={tab === "post" ? "posts-tab" : "reels-tab"} role="tabpanel">
        <MediaGrid
          media={media}
          emptyDetail={`No ${tab === "post" ? "posts" : "reels"} have been saved from this profile yet.`}
          emptyTitle={`No ${tab === "post" ? "posts" : "reels"} yet`}
        />
      </div>
      <ConfirmDialog
        confirmLabel="Stop sync"
        description={`Stop future downloads for @${profile.username}. If a post or reel is currently downloading, it will finish safely before synchronization stops.`}
        open={stopSyncOpen}
        title="Stop profile synchronization?"
        onClose={() => setStopSyncOpen(false)}
        onConfirm={() => updateSyncSetting(false)}
      />
      <ConfirmDialog
        confirmLabel="Delete profile"
        description={`This queues deletion of @${profile.username} and all downloaded media from this library. This cannot be undone.`}
        open={deleteOpen}
        title="Delete this profile?"
        onClose={() => setDeleteOpen(false)}
        onConfirm={confirmDeletion}
      />
    </section>
  );
}
