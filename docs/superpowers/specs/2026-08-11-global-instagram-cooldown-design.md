# Global Instagram Cooldown Design

## Goal

Replace the per-profile 25-new-media limit with fair time slicing and protect
the shared Instagram account/IP from repeated requests after a 429 response.

## Current Problem

The 25-item limit is counted independently for every profile sync job. It limits
new downloads, but does not bound Instagram requests because feed pagination
still occurs while traversing existing media. All due profiles are queued at the
same time, and after one job receives a 429 the worker immediately claims the
next profile job. The current limit therefore neither matches Instagram's
account/IP-wide rate-limit scope nor prevents a cascade of 429 responses.

## Profile Time Slice and Pacing

Stories remain first and are not subject to the long-lived-media time slice.
After Stories, each profile receives a five-minute time slice for its merged
Posts/Reels backfill. The coordinator checks elapsed monotonic time between
media outcomes. When the slice expires, the job succeeds with a backfill-pending
status and the next scheduled sync resumes from complete media already stored in
the library.

The fixed 25-new-media limit is removed. Instaloader's inherited query-level
`RateController.wait_before_query()` remains the primary normal-request pacing.
Additionally, after a newly saved Post or Reel, the coordinator waits a random
one-to-three seconds before resolving/downloading the next long-lived item.
Existing items do not trigger this media jitter.

## Persistent Global Cooldown

A schema-free cooldown store persists safe state at
`/data/state/instagram_cooldown.json`. It records only an expiry timestamp and
consecutive rate-limit count in an atomically replaced JSON file. The state
directory uses mode `0700` and the file uses mode `0600`; it never stores Cookie
values or Instagram responses.

The first 429 starts a 30-minute cooldown. Consecutive 429 outcomes double the
duration to 60 minutes, 2 hours, then 4 hours, capped at 6 hours. A successful
Instagram job resets the consecutive count. The persisted timestamp survives a
worker/container restart.

Rate-limit detection covers safe `MediaItemFailure` codes and safe adapter
errors from profile sync, direct media, and followee discovery. The current job
stops immediately; it does not sleep and retry inside the failing request. Its
terminal error includes the UTC time when Instagram work may resume.

## Cooldown-Aware Scheduling

During an active cooldown, the worker leaves these Instagram jobs pending:

- `profile_sync`
- `single_media`
- `followee_discovery`

The job repository claims the oldest eligible pending job while excluding those
types. Local `delete_media` and `delete_profile` jobs therefore continue during
the cooldown. Due profile jobs remain coalesced and resume in original queue
order when the cooldown expires. No database table or column changes are made.

## User-Visible Status

A time-sliced profile job reports that its time slice ended and more history
will continue on the next scheduled sync. A rate-limited job reports the normal
safe 429 explanation plus the global cooldown expiry in UTC. Pending jobs remain
queued; they are not incorrectly failed merely because another job caused the
cooldown.

## Verification

Tests must prove:

- the five-minute slice replaces the 25-item count and uses monotonic time;
- Stories remain exempt and newly saved long-lived items receive one-to-three
  second jitter while existing items do not;
- cooldown backoff is 30 minutes, 1 hour, 2 hours, 4 hours, then 6 hours;
- cooldown state survives reconstructing the store and resets after success;
- a 429 stops the current job and records a safe pause-until message;
- Instagram job types remain pending during cooldown while local deletion jobs
  can be claimed;
- the existing SQLite schema remains unchanged;
- the full backend and frontend verification suites still pass.
