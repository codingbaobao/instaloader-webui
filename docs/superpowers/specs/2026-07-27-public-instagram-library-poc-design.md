# Public Instagram Library POC Design

Date: 2026-07-27
Status: Awaiting final review

## 1. Goal

Deliver a usable end-to-end proof of concept that proves three things:

1. The WebUI can accept a public Instagram profile, post URL, or reel URL.
2. The service can use the unmodified sibling Instaloader source package to
   download that public content into `/data`.
3. The WebUI can browse the downloaded profiles, images, videos, captions, and
   job status on desktop and mobile.

The POC prioritizes working ingestion and viewing over production hardening.

## 2. Included Scope

- Anonymous access to public Instagram content only.
- Add input accepts:
  - `username`
  - `@username`
  - `https://www.instagram.com/<username>/`
  - `https://www.instagram.com/p/<shortcode>/`
  - `https://www.instagram.com/reel/<shortcode>/`
  - `https://www.instagram.com/tv/<shortcode>/`
- Track a public profile and download its posts and reels.
- Download one public post or reel without tracking the complete owner profile.
- Automatically create an untracked owner profile for individually saved media.
- Persistent SQLite-backed jobs and a separate worker container.
- Periodic synchronization of tracked profiles using one global interval.
- Manual synchronization for one profile and all tracked profiles.
- Persist normalized profiles, media records, assets, captions, and job progress.
- Store media below `/data/media` and temporary job output below `/data/tmp`.
- Home page with recent media and profile shortcuts.
- Profile list and Instagram-style profile detail page.
- Posts and Reels tabs with responsive media grids.
- Image, video, and carousel viewer with caption and original Instagram link.
- Activity page for pending, running, successful, and failed jobs.
- Settings page for the global synchronization interval.
- Basic deletion of a saved media item or tracked profile, with a confirmation
  dialog and worker-backed profile content deletion.
- Existing single WebUI administrator authentication remains in place.
- Docker Compose runs `web` and `worker` from the same multi-stage image.

## 3. Explicitly Deferred

- Instagram Cookie import.
- Instagram username/password login.
- Instagram 2FA and challenge handling.
- Private profile access.
- Stories and tagged-post synchronization.
- Encrypted Instagram session storage.
- Production security hardening.
- Unit, integration, smoke, and end-to-end test implementation or execution.
- Remote object storage and built-in HTTPS.

The deferred Instagram authentication work will be added only after the UI and
anonymous Instaloader integration are accepted.

## 4. Architecture

The existing FastAPI and React application remains the web process. A second
container runs a small Python worker loop. Both share the same SQLite database
and `/data` mount.

```text
Browser
  |
  v
FastAPI web ---- SQLite jobs/library ---- worker ---- Instaloader ---- Instagram
  |                    |                   |
  |                    +-------------------+
  v
React SPA        /data/media and /data/tmp
```

The API never waits for an Instagram download. Mutating endpoints create a job
and return it immediately. The frontend polls job and library endpoints to show
progress and newly available content.

The worker is deliberately single-process for the POC. It claims one pending
job at a time, runs Instaloader, updates progress, and records a concise error
when a public item cannot be accessed.

## 5. Upstream Instaloader Integration

No file in the outer repository's `instaloader/` package is modified.

The Docker build context is moved to the outer repository so the image can build
and install both:

- the sibling upstream Instaloader package;
- `instaloader-webui/backend`.

The adapter uses public Python APIs:

- `Instaloader`
- `Profile.from_username()`
- `Profile.get_posts()`
- `Profile.get_reels()`
- `Post.from_shortcode()`
- `Instaloader.download_post()`

The adapter configures deterministic output names and disables upstream caption
and metadata side files that the WebUI does not consume directly. Caption and
normalized metadata are written to SQLite from the `Post` and `Profile`
objects. Downloaded `.jpg` and `.mp4` files are discovered in a per-job staging
directory and moved into their final media directory only after the download
succeeds.

## 6. Persistence Model

The POC adds these tables:

### `profiles`

- Internal UUID.
- Instagram numeric user ID when available.
- Current username, display name, biography, profile-picture URL.
- `tracked` flag.
- Lifecycle status.
- Last attempted and successful synchronization timestamps.
- Created and updated timestamps.

### `media_items`

- Internal UUID.
- Instagram media ID and shortcode.
- Owner profile ID.
- Kind: `post` or `reel`.
- Caption, accessibility description, publication time, original URL.
- Download status and timestamps.

### `media_assets`

- Internal UUID.
- Media item ID.
- Relative local path.
- MIME type.
- Asset kind: image or video.
- Carousel order and file size.

### `jobs`

- Internal UUID.
- Type: profile sync, single media download, delete profile, or delete media.
- State: pending, running, succeeded, or failed.
- JSON payload.
- Progress current and total.
- User-facing status text and concise error.
- Created, started, completed, and updated timestamps.

### `app_settings`

- Singleton row.
- Global profile synchronization interval in minutes.
- Next scheduled synchronization time.

Database records refer only to relative paths under the configured data root.

## 7. API Surface

All routes reuse the existing administrator session and CSRF dependencies.

### Profiles

- `GET /api/profiles`
- `POST /api/profiles`
- `GET /api/profiles/{profile_id}`
- `POST /api/profiles/{profile_id}/sync`
- `DELETE /api/profiles/{profile_id}`

Creating a profile accepts a username or supported profile URL, creates or
updates the profile, marks it tracked, and enqueues its first synchronization.

### Media

- `GET /api/media`
- `POST /api/media`
- `GET /api/media/{media_id}`
- `GET /api/media/{media_id}/assets/{asset_id}`
- `DELETE /api/media/{media_id}`

Creating media accepts a supported post, reel, or TV URL and enqueues one
download job. Media listing supports optional profile and kind filters.

Asset responses stream the local image or video. Video responses use FastAPI's
file response behavior for the POC; complete custom range handling is deferred
unless browser playback requires it.

### Jobs

- `GET /api/jobs`
- `GET /api/jobs/{job_id}`

### Settings

- `GET /api/settings`
- `PATCH /api/settings`
- `POST /api/settings/sync-all`

Responses retain the existing envelope:

```json
{
  "success": true,
  "data": {},
  "error": null,
  "meta": {}
}
```

## 8. Worker Behavior

At startup the worker changes interrupted `running` jobs back to `pending`.

The worker loop:

1. Creates scheduled profile-sync jobs when the global interval is due.
2. Atomically claims the oldest pending job.
3. Updates state to `running`.
4. Executes the matching adapter operation.
5. Commits normalized records and final media paths.
6. Marks the job `succeeded` or `failed`.

Duplicate profile synchronization jobs are coalesced. Media is deduplicated by
shortcode. A failed job remains visible in Activity and can be superseded by
submitting the same profile or link again.

Profile deletion runs in the worker so the request returns immediately. The
worker reports progress while removing that profile's media records and files.

## 9. Frontend Experience

The existing authenticated shell is retained and its placeholders are replaced.

### Home

- Profile shortcut row.
- Recent media grid.
- Empty state linking to Add.

### Add

- One prominent smart-input field.
- The frontend sends profile-like input to the profile endpoint and post/reel
  URLs to the media endpoint.
- Submission immediately shows the created job and links to Activity.

### Profiles

- Responsive cards for all tracked and automatically created owner profiles.
- Profile detail header with avatar, biography, tracking and sync state.
- Posts and Reels tabs.
- Three-column media grid on profile pages.

### Viewer

- Modal-style routed page.
- Image, video, or carousel asset navigation.
- Caption, publication time, owner link, and original Instagram link.
- Delete action with confirmation.

### Activity

- Polls while pending or running jobs exist.
- Displays progress, status text, completion, and failures.
- Refreshing or leaving the UI does not affect worker activity.

### Settings

- Global synchronization interval.
- Sync-all action.
- Clear explanation that this POC supports public content only.

## 10. Error Behavior

- Invalid input is rejected before creating a job.
- Private, missing, or inaccessible profiles and posts become failed jobs with a
  readable message.
- A failed profile remains visible when it already has downloaded content.
- Partial staging directories are removed or replaced on the next attempt.
- The frontend shows API and job errors inline and keeps navigation usable.

Raw Instagram responses and Python tracebacks are not returned to the browser,
but extensive production-grade redaction and retry classification are deferred.

## 11. POC Completion Criteria

- `docker compose up -d --build` starts both web and worker services.
- A user can add a public profile from the WebUI.
- The worker downloads that profile's public posts and reels.
- A user can add one public post or reel URL without downloading the complete
  profile.
- Downloaded images and videos are viewable from the WebUI.
- Captions and basic media metadata are visible in the viewer.
- Profile and job state remain after browser refresh and container recreation
  with the same `/data` mount.
- Tracked profiles receive scheduled update jobs.
- Basic profile and media deletion work through confirmation dialogs.
- Existing upstream Instaloader source files remain unchanged.
- No automated tests are added or run for this POC.
