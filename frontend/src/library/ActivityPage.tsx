import { useCallback, useEffect, useState } from "react";

import { listJobs } from "./api";
import { formatDate } from "./MediaGrid";
import type { JobSummary } from "./types";
import { usePolling } from "./usePolling";

function isActive(job: JobSummary): boolean {
  return job.state === "pending" || job.state === "running";
}

function jobTitle(job: JobSummary): string {
  return job.type.replaceAll("_", " ");
}

function progress(job: JobSummary): number {
  if (job.progress_total === null || job.progress_total <= 0) {
    return job.state === "succeeded" ? 100 : 0;
  }
  return Math.min(100, Math.round((job.progress_current / job.progress_total) * 100));
}

export function ActivityPage() {
  const loadJobs = useCallback((signal: AbortSignal) => listJobs(signal), []);
  const [pollingActive, setPollingActive] = useState(false);
  const { data: jobs, error, loading, reload } = usePolling(
    loadJobs,
    pollingActive ? 2_000 : 0,
    true,
  );

  useEffect(() => {
    setPollingActive(jobsContainActive(jobs));
  }, [jobs]);

  return (
    <section className="library-page" aria-labelledby="activity-title">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Library activity</p>
          <h1 id="activity-title">Downloads and syncs</h1>
          <p className="page-intro">Active jobs refresh automatically every two seconds.</p>
        </div>
        <button className="secondary-button compact-button" type="button" disabled={loading} onClick={() => void reload()}>Refresh</button>
      </header>

      {error ? (
        <div className="inline-error" role="alert">
          <span>{error}</span>
          <button className="text-button" type="button" onClick={() => void reload()}>Try again</button>
        </div>
      ) : null}
      {jobs === null && loading ? <p className="loading-copy">Loading activity…</p> : null}
      {jobs?.length === 0 ? (
        <section className="empty-state">
          <span className="empty-state-mark" aria-hidden="true">✓</span>
          <h2>Nothing is running</h2>
          <p>Queued public downloads and syncs will show their progress here.</p>
        </section>
      ) : null}
      {jobs?.length ? (
        <div className="job-list">
          {jobs.map((job) => (
            <article className="job-card" key={job.id}>
              <div className="job-card-heading">
                <div>
                  <h2>{jobTitle(job)}</h2>
                  <p>{job.status_text || "Waiting for an update."}</p>
                </div>
                <span className={`status-badge status-badge-${job.state}`}>{job.state}</span>
              </div>
              <div className="job-progress-copy">
                <span>{job.progress_total === null ? "Progress pending" : `${job.progress_current} of ${job.progress_total}`}</span>
                <span>{progress(job)}%</span>
              </div>
              <progress aria-label={`${jobTitle(job)} progress`} max="100" value={progress(job)} />
              {job.error ? <p className="job-error" role="alert">{job.error}</p> : null}
              <p className="job-date">Created {formatDate(job.created_at)}</p>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function jobsContainActive(jobs: readonly JobSummary[] | null): boolean {
  return jobs?.some(isActive) ?? false;
}
