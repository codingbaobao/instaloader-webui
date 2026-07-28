import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import type { SessionData } from "../auth/useSession";
import { commitFolloweeImport, getFolloweeImport } from "./api";
import type {
  FolloweeImportCandidate,
  FolloweeImportCommitResult,
  JobSummary,
} from "./types";
import { usePolling } from "./usePolling";

type FolloweeImportPageProps = Readonly<{ session: SessionData }>;

function progressPercent(job: JobSummary): number {
  if (job.progress_total === null || job.progress_total <= 0) {
    return job.state === "succeeded" ? 100 : 0;
  }
  return Math.min(
    100,
    Math.round((job.progress_current / job.progress_total) * 100),
  );
}

function candidateSort(
  first: FolloweeImportCandidate,
  second: FolloweeImportCandidate,
): number {
  return (
    first.username.localeCompare(second.username, undefined, {
      sensitivity: "base",
    }) || first.username.localeCompare(second.username)
  );
}

type CandidateRowProps = Readonly<{
  candidate: FolloweeImportCandidate;
  selected: boolean;
  onToggle: (candidateId: string) => void;
}>;

function CandidateRow({
  candidate,
  selected,
  onToggle,
}: CandidateRowProps) {
  const content = (
    <>
      <span className="followee-candidate-copy">
        <span className="followee-candidate-username">@{candidate.username}</span>
        <span className="followee-candidate-name">
          {candidate.full_name || "Instagram profile"}
        </span>
      </span>
      <span className="followee-candidate-badges">
        {candidate.is_private ? (
          <span className="status-badge status-badge-private">Private</span>
        ) : null}
        {candidate.already_exists ? (
          <span className="status-badge status-badge-active">Already exists</span>
        ) : null}
      </span>
    </>
  );

  if (candidate.already_exists) {
    return (
      <div className="followee-candidate followee-candidate-existing">
        {content}
      </div>
    );
  }

  return (
    <label className="followee-candidate">
      <input
        checked={selected}
        type="checkbox"
        onChange={() => onToggle(candidate.id)}
      />
      {content}
    </label>
  );
}

export function FolloweeImportPage({ session }: FolloweeImportPageProps) {
  const { batchId = "" } = useParams();
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [commitPending, setCommitPending] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [commitResult, setCommitResult] =
    useState<FolloweeImportCommitResult | null>(null);
  const initializedBatchId = useRef<string | null>(null);
  const loadBatch = useCallback(
    async (signal: AbortSignal) => {
      const result = await getFolloweeImport(batchId, signal);
      if (
        result.state === "ready" &&
        initializedBatchId.current !== result.id
      ) {
        initializedBatchId.current = result.id;
        setSelectedIds(
          new Set(
            result.candidates
              .filter((candidate) => !candidate.already_exists)
              .map((candidate) => candidate.id),
          ),
        );
      }
      return result;
    },
    [batchId],
  );
  const {
    data: batch,
    error,
    loading,
    reload,
  } = usePolling(loadBatch, 2_000, batchId.length > 0);
  const candidates = useMemo(
    () => [...(batch?.candidates ?? [])].sort(candidateSort),
    [batch?.candidates],
  );
  const importableIds = useMemo(
    () =>
      candidates
        .filter((candidate) => !candidate.already_exists)
        .map((candidate) => candidate.id),
    [candidates],
  );

  function selectAll(): void {
    setSelectedIds(new Set(importableIds));
  }

  function selectNone(): void {
    setSelectedIds(new Set());
  }

  function toggleCandidate(candidateId: string): void {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(candidateId)) {
        next.delete(candidateId);
      } else {
        next.add(candidateId);
      }
      return next;
    });
  }

  async function handleCommit(): Promise<void> {
    if (selectedIds.size === 0 || commitPending) {
      return;
    }
    setCommitPending(true);
    setCommitError(null);
    try {
      setCommitResult(
        await commitFolloweeImport(
          batchId,
          [...selectedIds],
          session.csrf_token,
        ),
      );
    } catch (cause) {
      setCommitError(
        cause instanceof Error
          ? cause.message
          : "The selected profiles could not be imported.",
      );
      setCommitPending(false);
    }
  }

  if (batchId.length === 0) {
    return (
      <section className="library-page narrow-page" role="alert">
        <h1>Import not found</h1>
        <p>This followings import link is invalid.</p>
        <Link className="primary-button compact-button" to="/profiles">
          Back to Profiles
        </Link>
      </section>
    );
  }

  if (error) {
    return (
      <section className="library-page narrow-page" role="alert">
        <p className="eyebrow">Import unavailable</p>
        <h1>Followings import could not be loaded</h1>
        <p>{error}</p>
        <div className="followee-selection-actions">
          <button className="secondary-button compact-button" type="button" onClick={() => void reload()}>
            Try again
          </button>
          <Link className="primary-button compact-button" to="/profiles">
            Back to Profiles
          </Link>
        </div>
      </section>
    );
  }

  return (
    <section className="library-page followee-import-page" aria-labelledby="followee-import-title">
      <Link className="back-link" to="/profiles">← Back to Profiles</Link>
      <header className="page-heading">
        <div>
          <p className="eyebrow">Profiles</p>
          <h1 id="followee-import-title">Import Instagram followings</h1>
          <p className="page-intro">
            {batch?.source_username
              ? `Choose profiles followed by @${batch.source_username}.`
              : "Loading the accounts followed by your connected Instagram session."}
          </p>
        </div>
      </header>

      {commitResult ? (
        <section className="followee-import-result" aria-labelledby="followee-import-result-title" role="status">
          <p className="eyebrow">Import complete</p>
          <h2 id="followee-import-result-title">Profiles are ready</h2>
          <p>
            Imported {commitResult.imported_count} profiles and queued{" "}
            {commitResult.jobs.length} initial sync jobs.{" "}
            {commitResult.existing_count} existing profiles were unchanged.
          </p>
          <Link className="primary-button compact-button" to="/profiles">
            Back to Profiles
          </Link>
        </section>
      ) : batch?.imported_at !== null && batch?.imported_at !== undefined ? (
        <section className="followee-import-result" aria-labelledby="followee-import-result-title" role="status">
          <p className="eyebrow">Import complete</p>
          <h2 id="followee-import-result-title">This batch was already imported</h2>
          <p>The selected profiles and their initial sync jobs have already been created.</p>
          <Link className="primary-button compact-button" to="/profiles">
            Back to Profiles
          </Link>
        </section>
      ) : batch?.state === "failed" ? (
        <section className="followee-import-failed" role="alert">
          <p className="eyebrow">Import failed</p>
          <h2>Followings could not be loaded</h2>
          <p>
            {batch.error ||
              "Instagram did not return this account's followings. Check the connected Cookie in Settings, then start a new import."}
          </p>
          <Link className="primary-button compact-button" to="/profiles">
            Back to Profiles
          </Link>
        </section>
      ) : batch?.state === "ready" ? (
        <>
          <dl className="followee-import-summary" aria-label="Import summary">
            <div>
              <dt>Total followings</dt>
              <dd>{batch.total_count}</dd>
            </div>
            <div>
              <dt>Selected</dt>
              <dd>{selectedIds.size}</dd>
            </div>
            <div>
              <dt>Already exist</dt>
              <dd>{batch.existing_count}</dd>
            </div>
          </dl>

          {commitError ? <div className="inline-error" role="alert">{commitError}</div> : null}

          <div className="followee-import-toolbar">
            <div className="followee-selection-actions" aria-label="Selection controls">
              <button className="secondary-button compact-button" type="button" onClick={selectAll}>
                Select all
              </button>
              <button className="secondary-button compact-button" type="button" onClick={selectNone}>
                Select none
              </button>
            </div>
            <button
              className="primary-button compact-button"
              disabled={selectedIds.size === 0 || commitPending}
              type="button"
              onClick={() => void handleCommit()}
            >
              {commitPending ? "Importing…" : `Import selected (${selectedIds.size})`}
            </button>
          </div>

          <div className="followee-candidate-list" aria-label="Instagram followings">
            {candidates.map((candidate) => (
              <CandidateRow
                candidate={candidate}
                key={candidate.id}
                selected={selectedIds.has(candidate.id)}
                onToggle={toggleCandidate}
              />
            ))}
          </div>
        </>
      ) : (
        <section className="followee-import-progress" aria-live="polite" role="status">
          <div className="job-card-heading">
            <div>
              <p className="eyebrow">Import in progress</p>
              <h2>Loading Instagram followings</h2>
              <p>{batch?.job.status_text || "Waiting for the import job to start."}</p>
            </div>
            {batch?.job.state ? (
              <span className={`status-badge status-badge-${batch.job.state}`}>
                {batch.job.state}
              </span>
            ) : null}
          </div>
          {batch?.job ? (
            <>
              <div className="job-progress-copy">
                <span>
                  {batch.job.progress_total === null
                    ? `${batch.job.progress_current} processed`
                    : `${batch.job.progress_current} of ${batch.job.progress_total}`}
                </span>
                <span>{progressPercent(batch.job)}%</span>
              </div>
              <progress
                aria-label="Followings import progress"
                max="100"
                value={progressPercent(batch.job)}
              />
            </>
          ) : (
            <p className="loading-copy">
              {loading ? "Starting import…" : "Waiting for an update…"}
            </p>
          )}
        </section>
      )}
    </section>
  );
}
