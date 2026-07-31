import {
  type KeyboardEvent,
  useCallback,
  useRef,
  useState,
} from "react";
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
import type { JobSummary, MediaKind } from "./types";
import { usePolling } from "./usePolling";

type ProfilePageProps = Readonly<{ session: SessionData }>;
type MediaTab = MediaKind;

const mediaTabs: readonly Readonly<{
  kind: MediaTab;
  id: string;
  label: string;
  emptyTitle: string;
  emptyDetail: string;
}>[] = [
  {
    kind: "post",
    id: "posts-tab",
    label: "Posts",
    emptyTitle: "No posts yet",
    emptyDetail: "No posts have been saved from this profile yet.",
  },
  {
    kind: "reel",
    id: "reels-tab",
    label: "Reels",
    emptyTitle: "No reels yet",
    emptyDetail: "No reels have been saved from this profile yet.",
  },
  {
    kind: "story",
    id: "story-tab",
    label: "Story",
    emptyTitle: "No stories yet",
    emptyDetail: "No stories have been saved from this profile yet.",
  },
];

export function ProfilePage({ session }: ProfilePageProps) {
  const { profileId = "" } = useParams();
  const navigate = useNavigate();
  const tabRefs = useRef<Record<MediaTab, HTMLButtonElement | null>>({
    post: null,
    reel: null,
    story: null,
  });
  const selectedTabRef = useRef<MediaTab>("post");
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
    const requestedTab = selectedTabRef.current;
    const [profile, media] = await Promise.all([
      getProfile(profileId, signal),
      listMedia({ profileId, kind: requestedTab, limit: 200 }, signal),
    ]);
    return { profile, media, mediaKind: requestedTab };
  }, [profileId]);
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

  function selectTab(nextTab: MediaTab) {
    if (nextTab === tab) {
      return;
    }
    selectedTabRef.current = nextTab;
    setTab(nextTab);
    void reload();
  }

  function handleTabKeyDown(
    event: KeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) {
    let nextIndex: number;
    switch (event.key) {
      case "ArrowLeft":
        nextIndex = (currentIndex - 1 + mediaTabs.length) % mediaTabs.length;
        break;
      case "ArrowRight":
        nextIndex = (currentIndex + 1) % mediaTabs.length;
        break;
      case "Home":
        nextIndex = 0;
        break;
      case "End":
        nextIndex = mediaTabs.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    const nextTab = mediaTabs[nextIndex];
    selectTab(nextTab.kind);
    tabRefs.current[nextTab.kind]?.focus();
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

  const { profile } = data;
  const media = data.mediaKind === tab ? data.media : [];
  const activeTab = mediaTabs.find((item) => item.kind === tab) ?? mediaTabs[0];
  return (
    <section className="library-page profile-page" aria-labelledby="profile-title">
      <Link className="back-link" to="/profiles">Back to profiles</Link>
      <header className="profile-header">
        <ProfileAvatar className="profile-avatar-large" profile={profile} />
        <div className="profile-header-main">
          <div className="profile-title-row">
            <h1 id="profile-title">@{profile.username}</h1>
            <a
              aria-label={`Open @${profile.username} on Instagram`}
              className="profile-instagram-link"
              href={`https://www.instagram.com/${encodeURIComponent(profile.username)}/`}
              rel="noopener noreferrer"
              target="_blank"
            >
              Instagram
            </a>
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
        {mediaTabs.map((item, index) => (
          <button
            aria-controls="profile-media-panel"
            aria-selected={tab === item.kind}
            className={
              tab === item.kind ? "media-tab media-tab-active" : "media-tab"
            }
            id={item.id}
            key={item.kind}
            ref={(element) => {
              tabRefs.current[item.kind] = element;
            }}
            role="tab"
            tabIndex={tab === item.kind ? 0 : -1}
            type="button"
            onClick={() => selectTab(item.kind)}
            onKeyDown={(event) => handleTabKeyDown(event, index)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {error ? (
        <div className="inline-error" role="alert">
          <span>{error}</span>
          <button className="text-button" type="button" onClick={() => void reload()}>Try again</button>
        </div>
      ) : null}
      <div
        aria-labelledby={activeTab.id}
        id="profile-media-panel"
        role="tabpanel"
        tabIndex={0}
      >
        <MediaGrid
          media={media}
          emptyDetail={activeTab.emptyDetail}
          emptyTitle={activeTab.emptyTitle}
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
