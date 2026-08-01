# Profile Sync Control, Duplicate Skipping, and Activity Repair

> **Status — superseded for schema work (2026-08-01):** This is a historical
> design record. Do not execute any migration or schema-startup instruction in
> this document. The current pre-1.0 runtime supports only the exact fresh
> schema and does not migrate older databases.

## Scope

This change adds persistent per-profile Stop Sync and Resume Sync controls,
prevents duplicate single-media downloads, and repairs the Activity page so
successful job responses replace its initial loading state.

The service remains a single-user, local-storage POC. This change does not add
Instagram private-profile support, remote storage, multi-user authorization, or
job-level arbitrary cancellation.

## Existing State Reused

The existing `profiles.tracked` boolean is the source of truth for profile
synchronization:

- `tracked=true`: profile synchronization is active.
- `tracked=false`: profile synchronization is stopped.

The existing `profiles.status` field continues to describe deletion lifecycle
state only. No database migration or new profile state column is required.

## Profile Sync API

Add:

```http
PATCH /api/profiles/{profile_id}/sync
Content-Type: application/json
X-CSRF-Token: ...

{"enabled": false}
```

The request accepts one required boolean `enabled` field and returns the updated
`ProfileResponse` in the existing API envelope.

- `enabled=false` stops profile synchronization.
- `enabled=true` resumes profile synchronization.
- A missing profile returns the existing `404 profile_not_found` response.
- A profile in a deletion lifecycle state cannot change its sync setting and
  returns a fixed, user-safe `409 profile_not_active` response.
- The endpoint requires the existing authenticated session and CSRF protection.

The existing `POST /api/profiles/{profile_id}/sync` remains the manual
immediate-sync operation. It returns `409 profile_sync_stopped` when the profile
is stopped rather than silently re-enabling it.

Adding a profile explicitly through the Add Profile form continues to set
`tracked=true`. If the profile already exists in stopped state, adding that
profile explicitly resumes it and queues its normal initial synchronization.

## Stop and Resume Semantics

Stop Sync prevents every profile-wide download path:

- periodic scheduled synchronization ignores the profile;
- Sync All ignores the profile;
- manual Sync Now is rejected while stopped;
- a pending profile-sync job that starts after Stop Sync performs no Instagram
  download;
- a running profile-sync job completes its current post or reel before stopping.

The running worker checks `tracked` only at safe media boundaries. It never
interrupts `loader.download_post()`, file finalization, database persistence, or
asset commit/rollback. Once the current media item has either completed or
failed as one unit, the iterator checks the profile state before requesting the
next target.

Stop Sync does not cancel an explicitly requested single-post or single-reel
job. That job finishes its one target even if the owner profile is stopped.

Resume Sync sets `tracked=true` and makes the profile eligible for the next
scheduled run, Sync All, and Sync Now. Resume itself does not immediately queue
a job.

## Profiles Created by Single-Media Downloads

When a single post or reel reveals an owner profile that does not yet exist in
the library, the service creates that profile with `tracked=false`. The media
and public owner metadata are still stored and remain viewable.

If the owner profile already exists, the single-media job preserves its current
`tracked` value. It never stops or resumes a profile as a side effect.

## Duplicate Media Handling

Instagram shortcode is the duplicate identity for posts and reels.

Before making an Instagram request for a single-media job, the adapter looks up
the shortcode:

- If the media record exists and every recorded local asset exists inside the
  media library, the job reports that the media is already saved and succeeds
  without accessing Instagram or rewriting files.
- If the record exists but any recorded asset is missing, the job proceeds
  through the existing staged download and atomic replacement flow to repair
  the media.
- If a reel URL identifies an existing complete item that is still classified
  as a post, the stored kind is updated to `reel` without downloading it again.

Profile synchronization keeps the same behavior: complete known media is
skipped, while incomplete local media is repaired.

The single worker already serializes jobs, so this milestone does not add a
cross-worker duplicate-download lock.

## WebUI

The Profile page displays a synchronization state separate from deletion
status:

- active profile: `Sync active` and a `Stop sync` action;
- stopped profile: `Sync stopped` and a `Resume sync` action.

Stop Sync uses the existing confirmation dialog. Its copy states that a running
sync finishes the current post or reel and then stops. It does not require
re-entering the username.

Resume Sync is immediate and does not show a confirmation dialog. After either
operation, the page reloads the profile response and updates the buttons and
badge. Sync Now is disabled for stopped profiles.

The Profiles list displays `Sync active` or `Sync stopped` instead of
`active`/`untracked`.

## Activity Repair

Observed runtime evidence shows `/api/jobs` returning `200` repeatedly
while the Activity page remains at its initial loading presentation. The
backend job list and stored job records are available, so the repair is scoped
to frontend polling state.

Activity will use one fixed ten-second polling controller while the page is
mounted. It will no longer switch the polling interval based on the returned job
array, which currently tears down the controller and clears its data during
state transitions.

The first request owns the full-page `Loading activity...` state. Later polling
requests keep rendering the last successful job list while refreshing. Leaving
the Activity route aborts the request and removes the timer through the existing
polling cleanup.

The page keeps a visible `Refresh` button that requests an immediate poll
without waiting for the next ten-second interval. It does not start a duplicate
request while another poll is in flight, and the next background interval is
scheduled after the immediate request settles.

## Error and Job Presentation

- API errors use the existing envelope and fixed user-safe messages.
- A duplicate single-media job is a successful terminal job, not a failure.
- A profile-sync job that observes Stop Sync completes successfully with status
  text indicating that synchronization stopped before the next media item.
- Stop/Resume request errors remain visible on the Profile page.
- No Instagram response bodies, Cookie contents, local absolute paths, or
  tracebacks are returned to the browser.

## Verification Constraints

Per the POC instruction, implementation will not add or run unit, integration,
smoke, or end-to-end tests. Verification is limited to relevant Python
compilation, frontend TypeScript/Vite production build, Docker Compose
configuration/build checks, and Git diff checks.

Manual acceptance will cover:

1. Activity replaces its initial loading state with persisted jobs, refreshes
   every ten seconds, and supports an immediate Refresh action.
2. Stop Sync prevents the next profile media item while allowing the current
   media item to finish safely.
3. Stopped profiles are ignored by scheduled sync, Sync All, and Sync Now.
4. Resume makes the profile eligible again without immediately queueing work.
5. A newly discovered owner from single-media download is stopped by default.
6. Re-submitting a complete shortcode succeeds without downloading it again.
