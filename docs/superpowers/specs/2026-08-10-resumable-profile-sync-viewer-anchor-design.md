# Resumable Profile Sync and Direct Viewer Anchor Design

## Goal

Make large first-time profile synchronizations preserve current Stories, collect
recent Posts and Reels fairly, resume historical backfill across scheduled runs,
and stop safely when Instagram blocks the session. Opening media from a grid must
paint the selected media immediately without scrolling through earlier items.

## Constraints

- Preserve the exact `pre-1.0-fresh-schema-1` SQLite schema used by existing NAS
  data. No new tables or columns are allowed.
- Never commit the root `cookie.txt` credential or its Windows metadata stream.
- Keep Stories first because they expire.
- Browsing remains unbounded; the sync work limit applies only to new downloads
  in one scheduled worker run.
- Do not continue issuing media requests after a rate limit, session rejection,
  challenge, or checkpoint.

## Profile Sync

Stories are enumerated and processed first. Reels and Posts are then consumed as
two reverse-chronological streams and merged by their publication timestamps.
Reels win shortcode deduplication, while unique Posts and Reels are processed in
newest-first order. This prevents a long Reel history from starving every Post.

One run saves at most 25 new long-lived media items. Existing complete items do
not consume the budget, so the media library itself is the durable checkpoint.
When the budget is reached, the job succeeds with a backfill-pending status; the
next normal profile-sync schedule resumes at the first missing item. This creates
a six-hour cooldown under the current default schedule without changing the
database schema or immediately requeueing Instagram traffic.

Item-local unavailable/not-found/asset-validation failures remain warnings.
Rate-limited, challenge-required, session-rejected, and access-denied failures
are blocking: record one safe issue and fail the job immediately. Instaloader's
`AbortDownloadException` is explicitly included in safe classification so it can
never degrade into `Instagram operation failed.` again.

## Viewer Anchor

The anchored feed response remains windowed around the requested media. During
the initial layout pass, the viewer temporarily forces `scroll-behavior: auto`
and jumps directly to `anchorIndex * viewportHeight` before first paint. It then
restores the stylesheet-controlled smooth behavior for user navigation. Route
reuse performs the same direct jump for the new media.

## Cookie Handling

Root cookie filenames are ignored explicitly. The provided Cookie is streamed
directly to the NAS session import service, validated against Instagram, stored
only in the existing encrypted session store, and never printed or copied into a
tracked path. The plaintext local file remains under the user's control.

## Verification

- Unit tests prove newest-first Reel/Post interleaving, Reel deduplication,
  25-new-item backfill pausing, existing-item checkpoint behavior, blocking
  issue propagation, and `AbortDownloadException` classification.
- Frontend tests prove the first anchor jump occurs with smooth scrolling
  disabled and later navigation remains smooth.
- Full backend tests, Ruff, Mypy, frontend tests, ESLint, and production build
  must pass.
- Git status and staged diffs must contain neither Cookie filename as an
  untracked file nor Cookie content.
