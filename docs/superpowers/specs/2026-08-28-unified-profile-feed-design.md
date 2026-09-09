# Unified Profile Feed Design

## Goal

Make the stored-media model and Web UI match profile synchronization: Posts and
Reels are one **Feed** collection, while Stories remain a separate collection.
The media library must not persist a redundant Post/Reel discriminator.

## Current State

Profile synchronization already reads the Posts and Reels Instagram sources,
merges them newest-first, and deduplicates them by shortcode. The persisted
`media_items.kind` column still stores `post`, `reel`, or `story`. That column is
then used by the media-list API, immersive-viewer cursors, frontend types, and
the three-tab Posts/Reels/Story profile view.

Repository-wide search shows that the `kind=post|reel` browsing contract is used
only by this repository's React client and tests. It is not documented as a
public API and no other client exists in the repository. Runtime download jobs
also carry a media kind, but that value selects an Instagram input/download
path and is not a library browsing category.

## Domain Model

Stored media has two collections derived from its existing stable identity:

- `identity_type == "shortcode"` means `feed`;
- `identity_type == "story_media_id"` means `story`.

`media_items.kind` is removed. `MediaItem`, `MediaSnapshot`, and
`NormalizedMedia` no longer contain a persisted Post/Reel/Story kind. The
backend exposes a computed `collection: "feed" | "story"` value in media API
responses so clients do not have to repeat the identity mapping.

The operational `MediaKind = "post" | "reel" | "story"` remains on
`MediaCandidate`, `ResolvedMedia`, queued single-media job payloads, and job
issues. Instaloader needs it to choose the correct URL and download behavior,
and Activity uses it to diagnose a failed input. It must not be used for media
library grouping, filtering, or navigation.

The existing reconciliation that updates a stored item from `post` to `reel`
is deleted. A complete locally stored shortcode is the same Feed item no matter
which Instagram source discovers it later.

## Database Schema and Migration

The schema marker advances from `pre-1.0-feed-sync-2` to
`pre-1.0-unified-feed-3`.

The version-3 `media_items` table keeps every existing column except `kind` and
removes `ck_media_items_kind`. Its identity constraint, unique identity,
profile foreign key, timestamps, metadata, original URL, and publication index
remain unchanged. `job_issues.media_kind` and JSON job payloads remain
unchanged because they contain operational diagnostics and queued work, not a
library category.

Initialization supports both existing versions:

1. An exact version-1 database first receives the existing version-2 migration.
2. An exact version-2 database is verified against a fixed normalized schema
   digest before any version-3 change.
3. The version-3 migration rebuilds `media_items` without `kind`, copies every
   remaining value, preserves all `media_assets` references, recreates the
   publication index, runs `PRAGMA foreign_key_check`, updates the marker, and
   validates the complete ORM schema before commit.
4. Any schema drift, copy failure, referential-integrity failure, or final
   signature mismatch rolls back and raises the existing fixed compatibility
   error.

SQLite foreign-key enforcement must be handled explicitly while rebuilding the
referenced parent table. The migration disables it only on the dedicated
migration connection before `BEGIN IMMEDIATE`, performs the rebuild in one
transaction, checks referential integrity before commit, and restores foreign
keys in `finally`. Tests prove media, assets, job issues, checkpoints, and jobs
survive. Fresh databases are created directly at version 3.

## Repository and API Contract

Library repository list, count, and viewer-window methods replace `kind` with
an optional `collection: Literal["feed", "story"]`. The filter maps `feed` to
`MediaItem.identity_type == "shortcode"` and `story` to
`MediaItem.identity_type == "story_media_id"`. Anchor validation uses the same
derived collection. Calls without a collection continue to include both.

`GET /api/media` and `GET /api/media/feed` accept `collection=feed|story`; they
no longer accept or emit a browsing `kind`. Serialized media includes the
computed `collection` field and retains `identity_type`, `identity_value`,
`shortcode`, `story_media_id`, and `original_url`.

Immersive viewer cursors advance to payload version 2 and bind to
`profile_id + collection`. A cursor cannot be replayed under another profile or
collection. Old version-1 cursors are rejected with the existing safe invalid
cursor response; cursors are transient browser state and are regenerated from
an anchor.

No compatibility alias maps `kind=post` or `kind=reel` to Feed. The only known
client ships in the same build, so retaining the obsolete vocabulary would
leave two competing contracts without a consumer.

## Web UI

The profile page has two accessible tabs:

- **Feed**: all shortcode media, regardless of whether Instagram exposed it
  through Posts or Reels;
- **Story**: all story identities.

Feed is the default. The profile URL uses `?tab=feed` or `?tab=story`; any
missing or unknown tab value resolves to Feed. Loading a tab sends one matching
`collection` filter. Empty states say “No feed media yet” and “No stories yet.”

Profile-origin viewer links carry `collection=feed|story` rather than a media
kind. The viewer uses that value for adjacent-media requests and its Back to
profile target. When opened without source context, it uses the media response's
computed collection.

Feed cards and viewer headings use the neutral label **Feed media**. Story
cards retain **Story**. Original Instagram links remain unchanged, so a saved
Reel still opens its canonical `/reel/<shortcode>/` URL even though it is not a
separate library category. Activity issue labels may still say Post, Reel, or
Story because they describe the failed download operation rather than browse
collections.

The frontend replaces browsing uses of `MediaKind` with
`MediaCollection = "feed" | "story"`. A separate
`OperationalMediaKind = "post" | "reel" | "story"` types job issues.

## Documentation

README browsing instructions and profile-sync wording describe Feed and Story
collections. Input instructions may continue to name post, Reel, Story, and TV
URLs because those are real accepted Instagram URL forms.

## Error Handling

- Unknown API collections fail FastAPI validation with HTTP 422.
- A viewer cursor whose version, profile, collection, position, or shape does
  not match the request returns `invalid_media_feed_cursor`.
- An anchor outside the requested profile or collection is treated as not
  found, matching current behavior.
- Database migration failures retain the existing generic schema compatibility
  message and do not partially update the schema marker or stored rows.

## Testing Strategy

Implementation follows red-green-refactor cycles.

Backend migration tests cover exact version-1-to-version-3 and
version-2-to-version-3 upgrades, preservation of both former Post and Reel rows
as Feed media, preservation of Story rows and asset foreign keys, fresh version
3 bootstrap, drift rejection, injected rollback, and foreign-key checking.

Repository and API tests prove Feed returns both former kinds in publication
order, Story remains isolated, media responses have `collection` and no `kind`,
counting uses collections, anchors cannot cross collections, and cursor context
uses collection.

Processor tests prove a complete shortcode found through either source is
returned unchanged without a kind-repair write. Existing download validation,
canonical original URLs, single-media jobs, and job-issue kinds remain covered.

Frontend tests prove the two-tab keyboard behavior, default and URL-selected
collections, combined Feed results, request query parameters, profile viewer
links, cursor paging, Back to profile links, and neutral Feed media labels.
The full backend suite, frontend test suite, frontend lint, and production build
must pass before completion is reported.

## Out of Scope

- Changing the two Instagram source iterators or their independent resume
  checkpoints. They remain implementation details required for complete sync.
- Removing operational media kinds from download jobs or issue diagnostics.
- Rewriting stored original Instagram URLs.
- Combining Stories into Feed.
