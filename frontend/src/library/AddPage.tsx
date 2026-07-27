import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../app/api";
import type { SessionData } from "../auth/useSession";
import { addMedia, addProfile } from "./api";
import type { JobSummary, ProfileDetail } from "./types";

type AddPageProps = Readonly<{ session: SessionData }>;

function isMediaInput(value: string): boolean {
  return /\/(?:p|reel|tv)\//i.test(value.trim());
}

function jobLabel(job: JobSummary): string {
  return `${job.type.replaceAll("_", " ")} · ${job.state}`;
}

export function AddPage({ session }: AddPageProps) {
  const [input, setInput] = useState("");
  const [job, setJob] = useState<JobSummary | null>(null);
  const [createdProfile, setCreatedProfile] = useState<ProfileDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = input.trim();
    if (!value) {
      setError("Enter an Instagram profile or media URL.");
      return;
    }
    setSubmitting(true);
    setError(null);
    setJob(null);
    setCreatedProfile(null);
    try {
      if (isMediaInput(value)) {
        setJob(await addMedia(value, session.csrf_token));
      } else {
        const created = await addProfile(value, session.csrf_token);
        setCreatedProfile(created.profile);
        setJob(created.job);
      }
      setInput("");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The download could not be queued.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="library-page narrow-page" aria-labelledby="add-title">
      <p className="eyebrow">Add to library</p>
      <h1 id="add-title">Save public Instagram media</h1>
      <p className="page-intro">Paste a public profile, post, reel, or TV link. Nothing private is supported.</p>

      <form className="add-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="instagram-input">Instagram link or profile</label>
        <input
          autoComplete="off"
          id="instagram-input"
          maxLength={2048}
          placeholder="@instagram or https://www.instagram.com/reel/..."
          value={input}
          onChange={(event) => setInput(event.target.value)}
        />
        <p className="field-hint">Examples: @instagram, instagram.com/natgeo, /p/, /reel/, or /tv/ links.</p>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <button className="primary-button" disabled={submitting} type="submit">
          {submitting ? "Adding…" : "Add to library"}
        </button>
      </form>

      {job ? (
        <section className="job-result" aria-live="polite">
          <span className="status-badge status-badge-pending">Queued</span>
          <div>
            <h2>Download queued</h2>
            <p>{job.status_text || jobLabel(job)}</p>
          </div>
          <div className="result-actions">
            <Link className="secondary-button" to="/activity">Open activity</Link>
            {createdProfile ? (
              <Link className="text-link" to={`/profiles/${encodeURIComponent(createdProfile.id)}`}>
                Open @{createdProfile.username}
              </Link>
            ) : null}
          </div>
        </section>
      ) : null}
    </section>
  );
}
