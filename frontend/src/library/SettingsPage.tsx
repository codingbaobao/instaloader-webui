import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../app/api";
import type { SessionData } from "../auth/useSession";
import { getLibrarySettings, syncAllProfiles, updateLibrarySettings } from "./api";
import { formatDate } from "./MediaGrid";
import type { JobSummary } from "./types";
import { usePolling } from "./usePolling";

type SettingsPageProps = Readonly<{ session: SessionData }>;

export function SettingsPage({ session }: SettingsPageProps) {
  const loadSettings = useCallback(() => getLibrarySettings(), []);
  const { data: settings, error, loading, reload } = usePolling(loadSettings, 0, true);
  const [interval, setInterval] = useState("15");
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (settings !== null) {
      setInterval(String(settings.profile_sync_interval_minutes));
    }
  }, [settings]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const minutes = Number(interval);
    if (!Number.isInteger(minutes) || minutes <= 0) {
      setActionError("Enter a whole number of minutes greater than zero.");
      return;
    }
    setSaving(true);
    setActionError(null);
    setNotice(null);
    try {
      const updated = await updateLibrarySettings(minutes, session.csrf_token);
      setInterval(String(updated.profile_sync_interval_minutes));
      setNotice("Sync interval saved.");
      await reload();
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "The settings could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function syncAll() {
    setSyncing(true);
    setActionError(null);
    setNotice(null);
    try {
      const result = await syncAllProfiles(session.csrf_token);
      setNotice(syncNotice(result.jobs));
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "Profiles could not be synchronized.");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <section className="library-page narrow-page" aria-labelledby="settings-title">
      <p className="eyebrow">Library settings</p>
      <h1 id="settings-title">Synchronization</h1>
      <p className="page-intro">Instaloader only downloads content that is public on Instagram. Private accounts and content are not supported.</p>

      {error ? (
        <div className="inline-error" role="alert">
          <span>{error}</span>
          <button className="text-button" type="button" onClick={() => void reload()}>Try again</button>
        </div>
      ) : null}
      {settings === null && loading ? <p className="loading-copy">Loading settings…</p> : null}
      {settings ? (
        <>
          <form className="settings-form" onSubmit={(event) => void save(event)}>
            <label htmlFor="sync-interval">Profile sync interval (minutes)</label>
            <input
              id="sync-interval"
              inputMode="numeric"
              min="1"
              step="1"
              type="number"
              value={interval}
              onChange={(event) => setInterval(event.target.value)}
            />
            <p className="field-hint">Next scheduled sync: {formatDate(settings.next_sync_at)}.</p>
            <button className="primary-button" disabled={saving} type="submit">{saving ? "Saving…" : "Save interval"}</button>
          </form>
          <section className="settings-sync-now" aria-labelledby="sync-now-title">
            <div>
              <h2 id="sync-now-title">Sync all profiles now</h2>
              <p>Queue a fresh synchronization for every active tracked profile.</p>
            </div>
            <button className="secondary-button" type="button" disabled={syncing} onClick={() => void syncAll()}>{syncing ? "Queueing…" : "Sync all"}</button>
          </section>
        </>
      ) : null}
      {notice ? (
        <p className="success-note" aria-live="polite">
          {notice} <Link to="/activity">Open activity</Link>
        </p>
      ) : null}
      {actionError ? <p className="form-error" role="alert">{actionError}</p> : null}
    </section>
  );
}

function syncNotice(jobs: readonly JobSummary[]): string {
  return jobs.length === 0 ? "No active tracked profiles need a sync." : `${jobs.length} profile ${jobs.length === 1 ? "sync was" : "syncs were"} queued.`;
}
