# Feed Content Sync, Activity Detail, and SQL Migration Design

## Goal

Make profile synchronization complete, resumable, and query-efficient while
showing useful per-run Activity detail. The product treats long-lived Instagram
Posts and Reels as one primary concept, **Feed content**. Stories remain separate
because they expire and must always be collected first.

The change removes the five-minute profile-sync time slice. A sync has no time or
item limit: it continues until both Feed manifests reach their ends, the user
stops synchronization, the worker is interrupted, or Instagram returns a
blocking outcome. Interrupted work resumes from SQL-backed iterator checkpoints.

## Product Model

- The primary library concepts are Stories and Feed content.
- Instagram's Post/Reel distinction is not required for download completeness,
  resume behavior, Activity progress, or the main library presentation.
- The worker must still read both the Posts timeline and Reels manifest because
  the Posts timeline does not guarantee a complete historical Reel listing.
- Entries from both manifests are merged newest-first and deduplicated by
  shortcode. One Instagram item is persisted and downloaded at most once.
- Existing `media_items.kind` values may retain `post` or `reel` as optional
  source metadata. They do not create separate primary progress or browsing
  concepts.
- Stories retain their existing identity, expiry, download, and presentation
  behavior.

## Approaches Considered

### Selected: SQL Checkpoints and Two Lightweight Manifests

Persist iterator checkpoints and per-job progress in SQLite. Read both source
manifests, but keep Reel entries lightweight until the media library proves that
an item is missing. This preserves complete coverage and removes the current
per-existing-Reel metadata request.

### Rejected: SQL Checkpoints with `Profile.get_reels()`

Instaloader 4.15.3 implements `Profile.get_reels()` by calling
`Post.from_shortcode()` for every yielded Reel. Checkpoints would prevent lost
position, but every existing Reel would still cause an avoidable metadata query.

### Rejected: Posts Timeline Only

This minimizes requests but cannot guarantee complete historical Reel coverage.
It also relies on unstable private response fields to distinguish content types.

## One-Way SQL Migration

The application introduces the first supported one-way migration from
`pre-1.0-fresh-schema-1` to `pre-1.0-feed-sync-2`. No old application image is
required to read the upgraded database.

The migration runs in one `BEGIN IMMEDIATE` transaction and performs these
changes:

1. Add nullable `target_label` and `target_url` columns to `jobs`.
2. Create `job_progress_segments` with:
   - `job_id`, referencing `jobs(id)` with `ON DELETE CASCADE`;
   - `segment`, constrained to `stories` or `feed`;
   - `state`, constrained to `pending`, `running`, `completed`, or `failed`;
   - non-negative `scanned`, `saved`, `existing`, and `warnings` counters;
   - nullable non-negative `total`;
   - `updated_at`;
   - composite primary key `(job_id, segment)`.
3. Create `profile_sync_checkpoints` with:
   - `profile_id`, referencing `profiles(id)` with `ON DELETE CASCADE`;
   - `source`, constrained to `posts` or `reels`;
   - `cursor_version`;
   - nullable bounded `cursor_json`;
   - `backfill_complete`;
   - `updated_at`;
   - composite primary key `(profile_id, source)`.
4. Backfill profile-sync job labels from the referenced current profile where it
   still exists.
5. Backfill single-media target URLs from `payload_text.original_url`.
6. Insert one incomplete Posts checkpoint and one incomplete Reels checkpoint
   for every active tracked profile. This makes the first post-migration sync
   verify the complete history rather than assuming the existing archive has no
   gaps.
7. Update the singleton schema marker only after every statement succeeds.

Migration code recognizes only an exact version-1 schema or an exact version-2
schema. Unknown, partial, or modified schemas fail closed. Fresh installations
are created directly at version 2.

Before migration on the NAS, the deployment workflow must stop the old worker,
create a timestamped SQLite backup including WAL state, open the backup
read-only, and verify its schema marker and integrity. The new image is then
started and performs the migration before the web or worker serves work. The old
image must not be restarted against the upgraded database.

## Synchronization Policy

### Stories

Stories are enumerated and processed before any Feed work. Their segment total
is known after enumeration. Every current Story candidate is checked against the
local media library; complete existing Stories are skipped without downloading,
and missing Stories are saved before they expire.

Stories do not use historical cursors because Instagram exposes only the current
ephemeral set.

### Feed Content

The Feed segment consumes two reverse-chronological source streams:

- Posts timeline entries;
- lightweight Reels manifest entries.

Both entries expose only shortcode, publication-time hint, source metadata, and
a lazy resolver. The coordinator merges the streams newest-first and deduplicates
shortcodes before processing. A duplicate found in both sources is one scanned
Feed item and at most one local media item.

For a completed source's recent pass, the first existing complete shortcode is
its incremental library boundary. The source stops that recent pass without
resolving full Instagram metadata. An incomplete historical pass continues past
existing items until it reaches the stored cursor or source end. For a missing
shortcode:

- a Posts entry reuses the metadata already carried by its timeline page, with
  no second shortcode lookup;
- a Reels entry calls `Post.from_shortcode()` once, only because its lightweight
  manifest lacks the complete metadata required to download and persist assets.

There is no five-minute limit and no item-count limit. The worker continues until
both source streams are complete.

### Recent Pass and Historical Resume

Each source stores an independent checkpoint and `backfill_complete` flag.

- If a source is marked complete, a new sync starts at the newest entry and ends
  that source's recent pass at the first complete local item. Reverse chronology
  guarantees that all entries newer than that boundary were considered.
- If a source is incomplete with no cursor, it starts at the newest entry and
  continues to the source end.
- If a source has a cursor, the job first performs the bounded recent pass so
  content published after the interruption is not missed, then thaws and resumes
  the historical iterator.
- Repeated candidates around a frozen page boundary are safe because shortcode
  identity and complete-asset checks are idempotent.

The worker persists the frozen iterator at each fetched page boundary and before
an orderly stop or propagated failure. A process crash can repeat at most the
current page; it cannot skip content. Reaching the source end clears
`cursor_json` and marks that source complete.

Manual and scheduled syncs use the same policy. Requests for a profile that
already has a pending or running sync continue to coalesce into that active job.

## Checkpoint Encoding and Validation

The checkpoint stores a versioned JSON representation of Instaloader's
`FrozenNodeIterator`, including the remaining current page and pagination cursor.
The repository validates:

- an exact application cursor version;
- expected source and profile identity;
- bounded JSON byte size;
- required scalar and mapping shapes;
- timezone-aware timestamps;
- no unknown top-level fields.

A cursor that cannot be safely validated or thawed is discarded for that source
and the source restarts newest-first. Existing-media identity checks make this
recovery safe, although less efficient than a valid resume.

Checkpoint payloads contain manifest pagination data only. They must not include
Cookie values, request headers, session tokens, downloaded media bytes, or
short-lived CDN response bodies.

## Query Efficiency and Caching

The current expensive path is Instaloader 4.15.3 `Profile.get_reels()`: its node
wrapper calls `Post.from_shortcode()` once per Reel before application code can
check whether that Reel already exists. The new application-owned lightweight
Reels manifest yields raw identity and timestamp hints first. Existing Reels
therefore require only paginated manifest requests.

The query model becomes:

- one profile resolution per profile-sync job, with at most one native attempt
  before a required legacy fallback;
- current Story manifest requests;
- one request per Posts/Reels manifest page actually needed;
- one full metadata request only for each missing Reel;
- asset requests only for media that will be saved;
- no shortcode metadata requests for complete existing Feed items.

The persistent worker continues to reuse its authenticated Instaloader client and
RateController across sequential jobs. In fallback profile-lookup mode, a typed
native 429 opens a process-local native-path circuit for 30 minutes. During that
window, lookups use the already successful legacy path directly instead of
performing one predictable failed native request per profile. Expiry probes the
native path again. A legacy failure remains a real job failure and is never
hidden by cached profile metadata.

The application caches only durable local media identity/assets and resumable
manifest page/cursor state. It does not cache signed CDN URLs or resolved remote
media payloads across jobs because those values can expire or become stale.

If the stored profile-avatar URL is unchanged and a valid local avatar exists,
the worker reuses it. A changed URL still triggers the existing validated atomic
avatar replacement.

## Blocking Outcomes and Cooldown

Item-local unavailable, not-found, or asset-validation failures remain warnings
and increment the active segment warning count. Rate limit, challenge, session
rejection, and access denial remain blocking.

A blocking result persists the latest Feed checkpoints before failing the job.
The existing global Instagram cooldown continues to pause Instagram job types.
The native-profile-lookup circuit is endpoint-specific request avoidance; it is
not a replacement for the global cooldown.

## Job Target and Activity API

Every newly enqueued job snapshots its Activity target:

- Profile sync: `target_label` is `@username`; `target_url` is null and the
  profile ID remains in the typed job payload.
- Single media: `target_label` is the canonical URL and `target_url` is that URL.
- Local deletion and followee jobs may use a concise target label when one is
  already available, but this change does not require new remote lookups solely
  for Activity decoration.

The job API adds:

- `target_label: string | null`;
- `target_url: string | null`;
- `progress_segments`, an ordered tuple containing Stories then Feed content for
  profile-sync jobs.

Each progress segment returns its key, display label, state, counters, nullable
total, and update timestamp. List responses include counters but continue to omit
detailed warning records; the job-detail endpoint retains the existing safe
structured issue behavior.

## Activity UI

Activity cards show their concrete target:

- Profile sync cards display `@username` and link to the local profile when the
  profile still exists.
- Single-media cards display the canonical Instagram URL as a safe external
  link.

Profile sync cards always render two ordered progress rows:

1. Stories;
2. Feed content.

While a segment is running with an unknown total, its progress element is
indeterminate. A known total uses determinate progress. A completed segment is
shown as complete even when its effective incremental boundary is smaller than
Instagram's total profile count. Each row displays this-run counters for scanned,
saved, existing, and warnings. No cross-job percentage is shown.

Non-profile jobs retain one progress bar when they have a meaningful known total.
Existing state badges, safe errors, issue expansion, polling, and manual refresh
remain unchanged.

## Testing

Backend tests must prove:

- an exact version-1 database migrates transactionally to version 2 without
  losing profiles, media, assets, jobs, issues, settings, or sessions;
- migration backfills job targets and creates incomplete source checkpoints;
- a fresh version-2 database matches the exact expected schema;
- an unknown or partially migrated schema fails closed;
- existing Reel manifest entries do not call `Post.from_shortcode()`;
- one missing Reel calls `Post.from_shortcode()` exactly once;
- Posts and Reels manifests merge newest-first and deduplicate shared shortcodes;
- no time or item cap ends a Feed scan early;
- interruption freezes both source iterators and a later job resumes without
  skipping content;
- the recent pass collects content published after a stored historical cursor;
- completed sources stop at their first complete local boundary;
- Stories are processed before Feed content;
- segment counters and states persist for success, warning, stop, and failure;
- native 429 circuit behavior avoids repeated native calls and probes after 30
  minutes;
- unchanged valid avatars cause no avatar HTTP request;
- API list/detail responses expose safe targets and ordered progress segments.

Frontend tests must prove:

- profile sync cards show `@username` and two progress rows;
- segment counters and determinate/indeterminate states render correctly;
- single-media cards show a safe clickable canonical URL;
- failed and completed-with-warning cards retain their existing behavior;
- jobs without the new optional target fields remain renderable during a rolling
  frontend test fixture transition, even though old production images are not
  supported after migration.

Full verification includes backend Pytest, Ruff, Mypy, frontend Vitest, ESLint,
the production frontend build, Compose smoke tests, migration against a copy of
the NAS database, and a read-only post-migration integrity check before the live
NAS database is upgraded.

## Deployment and Recovery

The live NAS migration is the final step after all tests pass and the new image is
available locally or from the configured registry.

1. Record the running image revision and container state.
2. Stop both old services so neither API requests nor worker jobs can write
   SQLite.
3. Create a timestamped SQLite backup using the SQLite backup API from a trusted
   container, not a raw copy of a potentially active WAL database.
4. Verify `PRAGMA integrity_check`, the version-1 schema marker, row counts, and
   that the backup opens read-only.
5. Deploy the new image and allow one service to perform the transactional
   migration.
6. Start the second service, verify health, verify the version-2 marker and new
   tables, and inspect recent logs without triggering a broad sync.
7. Trigger one controlled `mihi_727` sync and verify Activity targets, two
   progress segments, reduced query volume, and forward Feed progress.

If migration or startup validation fails, stop the new containers and restore
the verified version-1 backup before restarting the recorded old image. Once new
jobs have run successfully on version 2, rollback requires restoring that backup;
the old image must never be pointed at the version-2 database.
