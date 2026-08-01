import { useEffect, useRef, useState } from "react";

import { getJob } from "./api";
import { formatDateTime } from "./dateFormatters";
import type { JobDetail, JobIssue, MediaKind } from "./types";

type JobIssuesProps = Readonly<{
  jobId: string;
  issueCount: number;
}>;

function warningLabel(count: number): string {
  return `${count} ${count === 1 ? "warning" : "warnings"}`;
}

function mediaKindLabel(kind: MediaKind): string {
  switch (kind) {
    case "post":
      return "Post";
    case "reel":
      return "Reel";
    case "story":
      return "Story";
  }
}

function identityLabel(issue: JobIssue): string {
  return issue.identity_type === "shortcode" ? "Shortcode" : "Story media ID";
}

function IssueDetail({ issue }: Readonly<{ issue: JobIssue }>) {
  return (
    <li className="job-issue">
      <div className="job-issue-heading">
        <span className="job-issue-kind">{mediaKindLabel(issue.media_kind)}</span>
        <time dateTime={issue.occurred_at}>{formatDateTime(issue.occurred_at)}</time>
      </div>
      <dl className="job-issue-fields">
        <div>
          <dt>{identityLabel(issue)}</dt>
          <dd>{issue.identity_value}</dd>
        </div>
        <div>
          <dt>Error code</dt>
          <dd><code>{issue.error_code}</code></dd>
        </div>
        <div className="job-issue-message">
          <dt>Message</dt>
          <dd>{issue.safe_message}</dd>
        </div>
        <div className="job-issue-chain">
          <dt>Exception classes</dt>
          <dd>{issue.exception_class_chain.join(" → ")}</dd>
        </div>
      </dl>
    </li>
  );
}

export function JobIssues({ jobId, issueCount }: JobIssuesProps) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestController = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => requestController.current?.abort();
  }, []);

  function loadDetail(): void {
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    setError(null);
    void getJob(jobId, controller.signal)
      .then((job) => {
        if (!controller.signal.aborted) {
          setDetail(job);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError("Warning details could not be loaded. Please try again.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
        if (requestController.current === controller) {
          requestController.current = null;
        }
      });
  }

  function toggleExpanded(): void {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (detail === null && !loading) {
      loadDetail();
    }
  }

  const label = warningLabel(issueCount);

  return (
    <section className="job-issues" aria-label={`Job ${label}`}>
      <button
        aria-expanded={expanded}
        className="text-button job-issues-toggle"
        type="button"
        onClick={toggleExpanded}
      >
        {expanded ? `Hide ${label}` : `View ${label}`}
      </button>
      {expanded ? (
        <div className="job-issues-content">
          {loading ? <p className="loading-copy">Loading warning details…</p> : null}
          {error ? <p className="job-issues-error" role="alert">{error}</p> : null}
          {detail !== null && detail.issues.length === 0 ? (
            <p className="job-issues-empty">No warning details were returned.</p>
          ) : null}
          {detail !== null && detail.issues.length > 0 ? (
            <ul className="job-issue-list">
              {detail.issues.map((issue, index) => (
                <IssueDetail
                  issue={issue}
                  key={`${issue.identity_type}-${issue.identity_value}-${issue.occurred_at}-${index}`}
                />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
