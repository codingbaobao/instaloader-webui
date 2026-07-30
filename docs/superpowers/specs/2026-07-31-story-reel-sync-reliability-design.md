# Story Support, Reel Classification, and Resilient Profile Sync

## Goal

Extend the existing Instagram library so it can save Stories, distinguish
Posts, Reels, and Stories correctly, and finish a profile synchronization when
individual media items fail. Upgrade Instaloader to the known-good `4.15.3`
release and make failures diagnosable without exposing credentials or sensitive
URLs.

This milestone uses one unified media model and one media-processing pipeline.
Story priority is a scheduling decision in the profile-sync coordinator, not a
second worker architecture.

## Scope

This change includes:

- direct Story URL input;
- automatic Story capture during scheduled and manual profile sync;
- Posts, Reels, and Story tabs on the Profile page;
- correction of existing Reels that were stored as Posts;
- video posters that are not counted as carousel content;
- a link from the Profile page to the original Instagram profile;
- per-item warning records and a `completed_with_warnings` job state;
- exact unified progress across Stories, Reels, and Posts;
- frontend and backend regression tests;
- an isolated NAS diagnostic run before any push, image release, or production
  deployment.

This change does not add a user-facing quality selector, a separate Story
schedule, Story deletion after expiry, or recursive download of media shared
inside a Story. Instaloader continues to request the best supported media URL
available to the authenticated session.

## Supported Inputs

The input parser returns a discriminated value:

- `ProfileInput(username)`
- `PostInput(shortcode)`
- `ReelInput(shortcode)`
- `StoryInput(username, story_media_id)`

It accepts canonical and query-bearing forms of:

```text
instagram.com/{username}/
instagram.com/p/{shortcode}/
instagram.com/reel/{shortcode}/
instagram.com/tv/{shortcode}/
instagram.com/stories/{username}/{story_media_id}/
```

`/tv/` remains a single-media input and initially uses Post classification.
Instagram metadata may provide a more specific classification where supported.
The parser discards fragments and query parameters such as `utm_source` and
`igsh`. Raw submitted URLs are not persisted.

The frontend uses one shared TypeScript helper to select the Profile or
single-media API. The backend independently parses and validates every input and
is the source of truth.

For a direct Story URL, the adapter verifies that the resolved Story owner
matches the URL username. It saves only the Story item. If the Story embeds or
shares a Reel, the source Reel is not followed or downloaded.

## Unified Media Identity

`media_items` supports:

- `kind`: `post`, `reel`, or `story`;
- `identity_type`: `shortcode` or `story_media_id`;
- `identity_value`: the corresponding Instagram identifier;
- nullable `shortcode`, populated for Posts and Reels;
- nullable `story_expires_at`, populated for Stories when available;
- `published_at`;
- the existing `downloaded_at`, used as the local capture timestamp.

`(identity_type, identity_value)` is unique. Classification is deliberately not
part of the unique key. The same shortcode discovered first as a Post and later
as a Reel remains one record whose `kind` can be corrected to `reel`.

Story identity uses the numeric Instagram media ID rather than a shortcode.
Downloaded Stories remain in the local library after Instagram expiry.
Canonical original URLs are constructed from validated identifiers and never
from an unfiltered submitted URL.

The SQLite migration preserves existing records:

- existing media receives `identity_type=shortcode`;
- its existing shortcode becomes `identity_value`;
- existing media kinds and timestamps are retained;
- existing assets initially receive `role=content` because the migration cannot
  safely infer historical poster relationships.

The next profile sync reconciles known media as well as new media. A Reel
manifest entry wins over a duplicate Post entry, updates an existing item's
kind, and repairs asset roles. If existing files and metadata are insufficient
to distinguish content from posters safely, that media item is staged and
downloaded again before replacing its asset records.

## Media Assets and Posters

`media_assets` keeps `kind=image|video` and adds:

- `role`: `content` or `poster`;
- `position`: the logical media position to which the asset belongs.

A single-video Reel is represented as:

| position | kind | role |
| ---: | --- | --- |
| 0 | video | content |
| 0 | image | poster |

The adapter returns explicit asset descriptors. Repository and UI code do not
infer role solely from file extension. A poster and its video share the same
logical position, which also supports future mixed carousels without changing
the response shape.

Only `content` assets participate in carousel navigation and media counts.
Grid cards prefer a poster, then the first renderable content asset. A video
uses the poster at its position through the HTML `poster` attribute. The same
rules apply to Reel and Story videos.

## Job States and Structured Issues

`jobs.state` adds the terminal value `completed_with_warnings`.
The database column is widened from its current 16-character limit so this
value cannot be truncated. Jobs also gain a nullable, application-controlled
`phase` value for the current coordinator phase. Historical jobs may leave it
null; new profile-sync jobs use `profile_preflight`, `saving_stories`,
`scanning_media`, `processing_reels`, or `processing_posts`. Terminal state
remains authoritative after completion.

A new `job_issues` table stores:

- `job_id`;
- `identity_type`;
- `identity_value`;
- `media_kind`;
- an application-controlled safe `error_code`;
- a safe user-facing `message`;
- an ordered exception class-name chain;
- `occurred_at`.

For a Post or Reel, `identity_value` is the shortcode. For a Story it is the
Story media ID. API serialization exposes these as `shortcode` or
`story_media_id` as appropriate, while retaining the generic identity fields
internally.

Exception chains contain class names only, for example:

```text
BadResponseException -> ConnectionException
```

They do not contain traceback frames or raw exception strings. The error
classifier produces bounded error codes and messages. Structured Docker logs
use the same job ID, identity, media kind, error code, safe message, class
chain, and timestamp. A final redaction boundary prevents logging Cookie
content, session material, query-bearing URLs, Instagram response bodies, or
other secrets.

Job list responses include `issue_count`; issue details are returned with the
job-detail response so routine polling does not grow with historical failures.

## Component Boundaries

### Instagram adapters

Adapters isolate Instaloader-specific behavior:

- session validation;
- Profile metadata and avatar retrieval;
- Story enumeration and resolution;
- Reel and Post enumeration;
- single-item download;
- conversion of downloaded output to explicit content/poster descriptors.

An avatar preflight succeeds when a usable avatar is obtained through the
supported high-quality value or a safe standard-profile-picture fallback. If no
valid avatar can be obtained, profile sync fails.

### Media processor

One media processor handles Stories, Reels, and Posts:

1. Check whether the media and all expected local assets are complete.
2. Reconcile classification and asset roles for known media.
3. Download into a per-item staging directory when work is required.
4. Validate all required content and poster relationships.
5. Move the complete staged result into the media library and commit its
   database transaction.
6. Remove staged output on failure without creating a successful media or asset
   record.

Known complete items are skipped but still produce one processed outcome.
Failed items remain eligible for the next sync. Successful items are skipped on
future syncs unless their files or role metadata are incomplete.

### Profile-sync coordinator

The coordinator owns only orchestration:

- execution order;
- manifests and deduplication;
- progress;
- safe issue recording;
- final job state.

Story priority does not create a separate job type, worker, persistence model,
or error system.

## Profile Sync Flow

Profile sync runs in this order:

1. Validate the authenticated Instagram session.
2. Fetch Profile metadata and a usable avatar.
3. Enumerate visible Stories into a manifest, deduplicated by Story media ID.
4. Immediately process every Story outcome.
5. Enumerate Reels into a manifest.
6. Enumerate Posts and discard shortcodes already present in the Reel manifest.
7. Set the exact unified total to unique Stories plus unique Reels and Posts.
8. Set current progress to the number of Story outcomes already processed.
9. Process Reels, then Posts.
10. Select the terminal state from the fatal error and issue rules.

During steps 3 and 4 the UI displays:

```text
Saving current Instagram stories before they expire…
```

During steps 5 and 6 it displays:

```text
Scanning Instagram posts and reels…
```

Before step 7, `progress_total` is null and the UI does not render a count,
percentage, `0 / 0`, or an estimated total. After step 7 it displays the exact
`progress_current / progress_total`. Every manifest item advances current
exactly once whether it downloads successfully, is already complete, or
produces an item warning.

If an iterator fails before its manifest is complete, the exact total remains
unknown and the job fails. Stories committed before a later Reel or Post scan
failure remain saved; filesystem and database work that already completed is
not rolled back.

Automatic Story capture follows the existing profile-sync interval and the
existing Sync Now action. It does not add a separate scheduler.
A successfully enumerated empty Story manifest is valid and contributes zero to
the unified total.

## Failure Semantics

The following are fatal for a profile-sync job:

- invalid or expired session;
- unreadable Profile metadata;
- inability to obtain a usable avatar;
- failure to create a Story, Reel, or Post iterator;
- interruption while enumerating any iterator, because the manifest is
  incomplete;
- database or filesystem infrastructure failure.

A fatal error sets the job to `failed`. The Activity detail identifies the
failed phase using a safe message.

The following are per-item warnings:

- an item is deleted or expires between manifest construction and download;
- one item's metadata cannot be resolved;
- one item's download fails;
- one item's required asset validation fails.

The coordinator records the issue and continues. When all manifests finish:

- no item issues results in `succeeded`;
- one or more item issues results in `completed_with_warnings`.

A direct single-media job has no remaining items to continue. Its item failure
creates the same structured issue but ends the job as `failed`.

## Instaloader Upgrade

Both the runtime dependency declaration and lock data are pinned to
`instaloader==4.15.3`. The build must not use an open version range that can
silently select another release.

This exact release is required because the production `4.15.2` runtime
reproduced `BadResponseException: Fetching Post metadata failed`, while the same
NAS session and shortcode `CmzV2H-rrlI` resolved successfully under `4.15.3`.
The adapter boundary and regression cases make a future Instaloader upgrade a
deliberate, testable change.

## API and WebUI

### Add page

The Add page recognizes Story URLs, including query-bearing shared URLs, and
submits them as single-media jobs. It no longer routes `/stories/` inputs to
profile creation.

### Profile page

The Profile page has exactly these tabs:

- Posts
- Reels
- Story

Each tab filters on `media_items.kind`; poster assets do not affect tab counts.
The profile heading links to
`https://www.instagram.com/{username}/` in a new tab with
`rel="noopener noreferrer"`.

### Media grid and viewer

Cards show Post, Reel, or Story labels and use the poster selection rules above.
The viewer excludes posters from carousel navigation and assigns them to the
matching videos. Original-media links are canonical:

- Post: `https://www.instagram.com/p/{shortcode}/`
- Reel: `https://www.instagram.com/reel/{shortcode}/`
- Story: `https://www.instagram.com/stories/{username}/{story_media_id}/`

An expired Story's original link may no longer resolve, but its saved local
content remains available.

### Activity

The Activity page renders `Completed with warnings` as a distinct terminal
state. Job details show:

- Post, Reel, or Story;
- shortcode or Story media ID;
- safe error code and message;
- exception class chain;
- occurrence time.

The job API accepts `post|reel|story` media filters, exposes asset `kind`,
`role`, and `position`, allows null progress while scanning, exposes the current
phase and message, and returns structured issues on job detail. Existing
response fields remain where practical to keep the frontend and backend
upgrade compatible within one release.

## Automated Verification

Instagram network behavior is mocked in automated tests. Real-network
verification is reserved for the isolated NAS diagnostic.

Frontend Vitest, React Testing Library, and MSW cases cover:

- Profile username and URL routing;
- `/p/`, `/reel/`, and `/tv/` single-media routing;
- Story URL routing with and without query parameters;
- the three Profile tabs and their media-kind filters;
- the safe original-profile link;
- poster selection, video poster assignment, and carousel exclusion;
- Activity warning state and issue presentation;
- scanning phases with no false numeric progress.

Backend tests cover:

- all accepted input forms and URL sanitization;
- Story owner validation;
- SQLite migration with preserved existing records;
- Reel-over-Post deduplication and next-sync classification repair;
- Story-first coordinator ordering;
- unified exact totals and one increment per outcome;
- already-complete, successful, and failed item behavior;
- `completed_with_warnings` versus fatal `failed` boundaries;
- atomic per-item persistence and retry eligibility;
- asset role mapping and repair;
- structured issue serialization and log redaction;
- the exact Instaloader dependency version;
- regression fixtures or diagnostic cases for `CmzV2H-rrlI` and
  `DOqEJyxCRGJ`.

This milestone does not add a coverage threshold and does not use Ruff or full
coverage as an acceptance gate. Targeted backend tests run without the
repository-wide coverage threshold. Frontend tests plus the existing production
type/build check are required. No local Docker image build is required.

## Isolated NAS Diagnostic

Only after automated checks pass, copy the working tree needed for diagnostics
to an isolated directory such as:

```text
/vol3/1000/docker-configs/instaloader-webui-diagnostics/
```

The diagnostic deployment uses:

- a distinct Compose project and container names;
- an isolated application database, download directory, and config directory;
- a high port confirmed unused at diagnostic time;
- a diagnostic-only image built on the NAS, never pushed to Docker Hub;
- only the minimum required copied Instagram session file, with its contents
  never printed or logged.

The production data directory is never mounted read-write. The production
`0.1.1` containers are not stopped, recreated, or modified.

The diagnostic matrix covers:

| Target | Required evidence |
| --- | --- |
| Profile metadata | Valid username and public metadata |
| Avatar | Valid saved avatar through supported quality/fallback handling |
| Story | Story classification; video poster excluded from content count |
| Reel | Reel classification; video and poster roles |
| Post | Post classification |
| `CmzV2H-rrlI` | Metadata and download regression under 4.15.3 |
| `DOqEJyxCRGJ` | Successful result or complete safe structured issue |
| Progress | Unified total and one increment per Story/Reel/Post outcome |
| Logs | No Cookie, session material, or query-bearing URLs |

After diagnostics, stop the diagnostic containers and retain the isolated files
for inspection. Do not delete them without approval.

## Release Gate

The implementation phase ends by reporting:

- automated test and frontend build results;
- the NAS diagnostic matrix;
- observed warnings and limitations;
- whether the result is ready for push, release, and production upgrade.

Before explicit follow-up approval, do not push commits, publish a Docker image,
create a GitHub Release, or replace the NAS production `0.1.1` deployment.
