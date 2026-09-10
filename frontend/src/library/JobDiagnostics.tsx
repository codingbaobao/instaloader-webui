import { useEffect, useRef, useState } from "react";

import { getJob } from "./api";
import { APP_VERSION } from "./appVersion";
import { formatDateTime } from "./dateFormatters";
import { canonicalInstagramUrl } from "./instagramUrl";
import type { JobDetail, JobIssue, JobSummary } from "./types";

type JobDiagnosticsProps = Readonly<{
  job: JobSummary;
}>;

type AffectedTarget = Readonly<{
  kind: "content" | "profile" | null;
  url: string | null;
}>;

const usernamePattern = /^[a-z0-9._]{1,30}$/i;
const shortcodePattern = /^[a-z0-9_-]{1,64}$/i;
const storyMediaIdPattern = /^[0-9]{1,32}$/;

function targetUsername(job: JobSummary): string | null {
  const payloadUsername = job.payload.username;
  if (typeof payloadUsername === "string" && usernamePattern.test(payloadUsername)) {
    return payloadUsername;
  }
  const label = job.target_label?.startsWith("@")
    ? job.target_label.slice(1)
    : null;
  return label && usernamePattern.test(label) ? label : null;
}

function issueTarget(issue: JobIssue, job: JobSummary): AffectedTarget {
  const targetUrl = canonicalInstagramUrl(job.target_url);
  if (job.type === "single_media" && targetUrl) {
    return { kind: "content", url: targetUrl };
  }
  if (
    issue.identity_type === "shortcode"
    && shortcodePattern.test(issue.identity_value)
  ) {
    const route = issue.media_kind === "reel" ? "reel" : "p";
    return {
      kind: "content",
      url: canonicalInstagramUrl(
        `https://www.instagram.com/${route}/${issue.identity_value}/`,
      ),
    };
  }
  const username = targetUsername(job);
  if (
    issue.identity_type === "story_media_id"
    && storyMediaIdPattern.test(issue.identity_value)
    && username
  ) {
    return {
      kind: "content",
      url: canonicalInstagramUrl(
        `https://www.instagram.com/stories/${username}/${issue.identity_value}/`,
      ),
    };
  }
  return username
    ? { kind: "profile", url: `https://www.instagram.com/${username}/` }
    : { kind: null, url: null };
}

function affectedTarget(job: JobSummary, issue: JobIssue | null): AffectedTarget {
  if (issue) return issueTarget(issue, job);
  const targetUrl = canonicalInstagramUrl(job.target_url);
  if (targetUrl && job.type === "single_media") {
    return { kind: "content", url: targetUrl };
  }
  const username = targetUsername(job);
  if (username) {
    return { kind: "profile", url: `https://www.instagram.com/${username}/` };
  }
  return { kind: null, url: null };
}

function reportTarget(job: JobSummary): string | null {
  const username = targetUsername(job);
  if (job.target_label?.startsWith("@") && username) return `@${username}`;
  return canonicalInstagramUrl(job.target_label);
}

function terminalIssue(job: JobSummary): JobIssue | null {
  const issue = job.issues[job.issues.length - 1];
  if (!issue || !job.error) return null;
  return job.error === issue.safe_message
    || job.error.startsWith(`${issue.safe_message} `)
    ? issue
    : null;
}

function diagnosticReport(
  job: JobSummary,
  issue: JobIssue | null,
  url: string | null,
  itemIdentified: boolean,
): string {
  const segmentLines = (job.progress_segments ?? []).map((segment) => (
    `${segment.label}: ${segment.state}; scanned=${segment.scanned} `
    + `saved=${segment.saved} existing=${segment.existing} warnings=${segment.warnings}`
  ));
  const target = reportTarget(job);
  return [
    "Instaloader WebUI diagnostic report",
    `Job: ${job.id}`,
    target ? `Target: ${target}` : null,
    url ? `Affected ${itemIdentified ? "content" : "profile"}: ${url}` : null,
    `State: ${job.state}`,
    `Phase: ${job.phase ?? "not recorded"}`,
    `App version: ${APP_VERSION}`,
    `Error code: ${issue?.error_code ?? "not recorded"}`,
    `Exception classes: ${issue?.exception_class_chain.join(" → ") || "not recorded"}`,
    `Message: ${issue?.safe_message ?? job.status_text}`,
    `Occurred: ${issue?.occurred_at ?? job.completed_at ?? job.updated_at}`,
    ...segmentLines,
  ].filter((line): line is string => line !== null).join("\n");
}

export function JobDiagnostics({ job }: JobDiagnosticsProps) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState("");
  const requestController = useRef<AbortController | null>(null);

  useEffect(() => () => requestController.current?.abort(), []);

  function loadDetail(): void {
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    setLoadError(null);
    void getJob(job.id, controller.signal)
      .then((loaded) => {
        if (!controller.signal.aborted) setDetail(loaded);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setLoadError("Diagnostic details could not be loaded. Please try again.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
        if (requestController.current === controller) requestController.current = null;
      });
  }

  function toggle(): void {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (job.issue_count > 0 && detail === null && !loading) loadDetail();
  }

  const source = detail ?? job;
  const issue = terminalIssue(source);
  const affected = affectedTarget(source, issue);
  const url = affected.url;
  const itemIdentified = affected.kind === "content";
  const occurredAt = issue?.occurred_at ?? source.completed_at ?? source.updated_at;

  async function copyReport(): Promise<void> {
    try {
      await navigator.clipboard.writeText(
        diagnosticReport(source, issue, url, itemIdentified),
      );
      setCopyStatus("Safe debug report copied.");
    } catch {
      setCopyStatus("Copy was unavailable. Please try again.");
    }
  }

  return (
    <section className="job-diagnostics" aria-label="Job diagnostic details">
      <button
        aria-expanded={expanded}
        className="text-button job-diagnostics-toggle"
        type="button"
        onClick={toggle}
      >
        {expanded ? "Hide diagnostic details" : "View diagnostic details"}
      </button>
      {expanded ? (
        <div className="job-diagnostics-content">
          <h3>What happened</h3>
          <p>{source.error ?? source.status_text}</p>
          {!itemIdentified ? (
            <p className="job-diagnostics-note">
              No individual media URL was available when the request failed.
            </p>
          ) : null}
          {url ? (
            <div className="job-diagnostics-affected">
              <span>{itemIdentified ? "Affected content" : "Affected profile"}</span>
              <a href={url} target="_blank" rel="noreferrer">
                {url}<span aria-hidden="true"> ↗</span>
              </a>
            </div>
          ) : null}
          {loading ? <p className="loading-copy">Loading diagnostic details…</p> : null}
          {loadError ? <p className="job-issues-error" role="alert">{loadError}</p> : null}
          <dl className="job-diagnostics-fields">
            <div>
              <dt>Error code</dt>
              <dd><code>{issue?.error_code ?? "Not recorded"}</code></dd>
            </div>
            <div><dt>Phase</dt><dd><code>{source.phase ?? "Not recorded"}</code></dd></div>
            <div className="job-diagnostics-wide">
              <dt>Exception classes</dt>
              <dd>{issue?.exception_class_chain.join(" → ") || "Not recorded"}</dd>
            </div>
            <div><dt>Occurred</dt><dd>{formatDateTime(occurredAt)}</dd></div>
            <div><dt>App version</dt><dd>{APP_VERSION}</dd></div>
            <div><dt>Job ID</dt><dd><code>{source.id}</code></dd></div>
          </dl>
          <div className="job-diagnostics-footer">
            <p>Sensitive request data is excluded.</p>
            <button className="secondary-button compact-button" type="button" onClick={() => void copyReport()}>
              Copy debug report
            </button>
          </div>
          <p className="job-diagnostics-copy-status" aria-live="polite">{copyStatus}</p>
        </div>
      ) : null}
    </section>
  );
}
