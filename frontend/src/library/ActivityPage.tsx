import { useCallback } from "react";
import { Link } from "react-router-dom";

import { listJobs } from "./api";
import { formatDate } from "./dateFormatters";
import { JobIssues } from "./JobIssues";
import type { JobProgressSegment, JobSummary } from "./types";
import { usePolling } from "./usePolling";

function jobTitle(job: JobSummary): string {
  return job.type.replaceAll("_", " ");
}

function profileId(job: JobSummary): string | null {
  const value = job.payload.profile_id;
  return typeof value === "string" && value.length > 0 ? value : null;
}

function canonicalInstagramUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    if (
      parsed.protocol !== "https:"
      || parsed.hostname !== "www.instagram.com"
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
    ) {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

function JobTarget({ job }: Readonly<{ job: JobSummary }>) {
  const label = job.target_label;
  if (!label) return null;
  const localProfileId = job.type === "profile_sync" ? profileId(job) : null;
  if (localProfileId) {
    return (
      <Link className="job-target" to={`/profiles/${encodeURIComponent(localProfileId)}`}>
        {label}
      </Link>
    );
  }
  const externalUrl = canonicalInstagramUrl(job.target_url);
  if (externalUrl) {
    return (
      <a className="job-target" href={externalUrl} target="_blank" rel="noreferrer">
        {label}
      </a>
    );
  }
  return <span className="job-target">{label}</span>;
}

function progress(job: JobSummary): number {
  if (job.progress_total === null || job.progress_total <= 0) {
    return job.state === "succeeded" || job.state === "completed_with_warnings"
      ? 100
      : 0;
  }
  return Math.min(100, Math.round((job.progress_current / job.progress_total) * 100));
}

function stateLabel(state: string): string {
  switch (state) {
    case "pending":
      return "Pending";
    case "running":
      return "Running";
    case "succeeded":
      return "Succeeded";
    case "completed_with_warnings":
      return "Completed with warnings";
    case "failed":
      return "Failed";
    default:
      return state.replaceAll("_", " ");
  }
}

function segmentPercent(segment: JobProgressSegment): number | undefined {
  if (segment.state === "completed") return 100;
  if (segment.total === null || segment.total <= 0) return undefined;
  return Math.min(100, Math.round((segment.scanned / segment.total) * 100));
}

function SegmentProgress({
  segment,
  target,
}: Readonly<{ segment: JobProgressSegment; target: string }>) {
  const percent = segmentPercent(segment);
  return (
    <section className={`job-segment job-segment-${segment.state}`}>
      <div className="job-segment-heading">
        <strong>{segment.label}</strong>
        <span>{stateLabel(segment.state)}</span>
      </div>
      <progress
        aria-label={`${target} ${segment.label} progress`}
        max="100"
        value={percent}
      />
      <div className="job-segment-counts">
        <span>Scanned {segment.scanned}</span>
        <span>Saved {segment.saved}</span>
        <span>Existing {segment.existing}</span>
        <span>Warnings {segment.warnings}</span>
      </div>
    </section>
  );
}

export function ActivityPage() {
  const loadJobs = useCallback((signal: AbortSignal) => listJobs(signal), []);
  const { data: jobs, error, loading, reload } = usePolling(
    loadJobs,
    10_000,
    true,
  );

  return (
    <section className="library-page" aria-labelledby="activity-title">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Library activity</p>
          <h1 id="activity-title">Downloads and syncs</h1>
          <p className="page-intro">
            Activity refreshes every ten seconds, or immediately when you choose Refresh.
          </p>
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
                  <h2>{jobTitle(job)} <JobTarget job={job} /></h2>
                  <p>{job.status_text || "Waiting for an update."}</p>
                </div>
                <span className={`status-badge status-badge-${job.state}`}>
                  {stateLabel(job.state)}
                </span>
              </div>
              {job.type === "profile_sync" && job.progress_segments?.length ? (
                <div className="job-segments">
                  {job.progress_segments.map((segment) => (
                    <SegmentProgress
                      key={segment.segment}
                      segment={segment}
                      target={job.target_label || jobTitle(job)}
                    />
                  ))}
                </div>
              ) : job.progress_total !== null && job.progress_total > 0 ? (
                <>
                  <div className="job-progress-copy">
                    <span>{job.progress_current} of {job.progress_total}</span>
                    <span>{progress(job)}%</span>
                  </div>
                  <progress
                    aria-label={`${jobTitle(job)} progress`}
                    max="100"
                    value={progress(job)}
                  />
                </>
              ) : null}
              {job.state === "failed" && job.error ? (
                <p className="job-error" role="alert">{job.error}</p>
              ) : null}
              {job.state === "completed_with_warnings" && job.issue_count > 0 ? (
                <JobIssues jobId={job.id} issueCount={job.issue_count} />
              ) : null}
              <p className="job-date">Created {formatDate(job.created_at)}</p>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
