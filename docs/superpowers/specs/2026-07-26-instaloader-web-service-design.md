# Instaloader Personal Web Service Design

Date: 2026-07-26  
Status: Approved

> **Status — superseded for schema work (2026-08-01):** This is a historical
> design record. Do not execute its Alembic, migration,
> database-upgrade/downgrade, or migration-startup instructions. The current
> pre-1.0 runtime supports only the exact fresh schema and does not migrate
> older databases.

## 1. Purpose

Build a self-hosted, single-user web service on top of Instaloader without modifying the existing `instaloader/` source code.

The service provides:

- Downloading and retaining individual Instagram posts and reels by URL.
- Tracking selected profiles and periodically downloading new posts and reels.
- Optional global synchronization of tagged posts and stories.
- A mobile-friendly React UI inspired by Instagram for browsing downloaded profiles and media.
- Persistent background jobs for synchronization, downloads, retries, and deletion.
- A single administrator account for a service that may be reachable from the public internet.
- Docker Compose deployment with all persistent state beneath `/data`.

The service is an adapter and management layer. Instaloader remains responsible for Instagram communication and media download behavior.

## 2. Scope

### 2.1 Included

- One WebUI administrator.
- One active Instagram session.
- Administrator login and password change.
- Instagram username/password login with 2FA support.
- Instagram session import using validated Cookie JSON, Netscape `cookies.txt`, or a raw `Cookie` header.
- Profile tracking and incremental synchronization.
- Individual post and reel download.
- Posts and reels enabled by default.
- Tagged posts and stories disabled by default, with global settings to enable them.
- One global synchronization interval, defaulting to six hours and editable from the WebUI.
- Manual synchronization of all profiles or one profile.
- Browsing profiles, posts, reels, tagged posts, and archived stories.
- Media streaming with authenticated HTTP Range support.
- Persistent job progress across page navigation, logout, and container restart.
- Safe media and profile deletion workflows.
- Local filesystem storage mounted at `/data`.
- HTTP application endpoint suitable for placement behind a user-managed proxy or tunnel.

### 2.2 Excluded

- Multiple WebUI users, roles, or public registration.
- Multiple Instagram accounts.
- S3, remote object storage, or network storage support.
- Built-in HTTPS, TLS certificate management, Nginx, Caddy, Traefik, or tunnel services.
- Instagram posting, liking, following, messaging, or comment submission.
- A public unauthenticated media gallery.
- Loading untrusted Python pickle session files.
- Automatic cloud backup.

## 3. Upstream Isolation

No existing file under `instaloader/` is modified.

New service code lives in separate directories, such as:

```text
webapp/       FastAPI application, worker, domain logic, and adapter
webui/        React and TypeScript frontend
docker/       Runtime configuration and container entrypoints
tests/        Service-specific tests
```

The adapter uses public Instaloader APIs including:

- `Instaloader`
- `Post.from_shortcode()`
- `Profile.from_username()`
- `Instaloader.download_post()`
- Profile post, reel, tagged-post, and story iterators
- `Instaloader.login()`
- `Instaloader.two_factor_login()`
- `Instaloader.save_session()` and `Instaloader.load_session()`
- `Instaloader.test_login()`

The UI and API never expose Instaloader's raw internal response shape. The adapter normalizes upstream objects into service-owned domain models. Contract tests detect upstream compatibility changes.

Updating Instaloader therefore consists of merging or installing a newer upstream version, running the adapter contract tests, and changing only the adapter if upstream public behavior changed.

## 4. Deployment Architecture

The selected architecture uses one multi-stage image with two runtime services:

```text
                         shared /data volume
                        ┌───────────────────┐
Browser ──HTTP──> web ──┤ SQLite + settings │
                        │ media + sessions  │
                        └─────────┬─────────┘
                                  │
                               worker
                                  │
                           Instaloader APIs
                                  │
                         Instagram + media CDN
```

### 4.1 Web service

The `web` container:

- Runs FastAPI.
- Serves the compiled React application.
- Handles administrator authentication.
- Validates requests and creates persistent jobs.
- Reads library metadata from SQLite.
- Streams authenticated media from `/data/media`.
- Does not run scheduled downloads.

### 4.2 Worker service

The `worker` container:

- Uses the same runtime image as `web`.
- Runs the persistent job loop and global scheduler.
- Owns all Instaloader network activity.
- Performs downloads, metadata normalization, retries, cleanup, and bulk deletion.
- Uses a single worker process to avoid duplicate Instagram requests and simplify rate limiting.

### 4.3 Persistence

Both services mount the same `/data` volume:

```text
/data/
├── database/app.sqlite3
├── media/profiles/<instagram-user-id>/<shortcode>/
├── media/stories/<instagram-user-id>/<story-id>/
├── sessions/instagram.enc
├── tmp/jobs/<job-id>/
└── logs/
```

SQLite runs in WAL mode. The supported deployment is a local Docker volume or local bind mount. Network filesystems with unreliable SQLite locking are outside scope.

### 4.4 Container build

The image uses multiple stages:

1. A Node.js stage builds the React and TypeScript frontend.
2. A Python builder stage builds application wheels and dependencies.
3. A minimal Python runtime stage contains only Python runtime dependencies, compiled frontend assets, service code, and Instaloader.

The final container runs as a non-root user. Docker Compose starts the same image with separate `web` and `worker` commands and includes health checks.

The application only provides HTTP, listening on a configurable address and port such as `0.0.0.0:8080`. TLS termination is entirely the deployer's responsibility.

## 5. Technology Choices

- Frontend: React, TypeScript, and Vite.
- Backend: FastAPI and Pydantic.
- Persistence: SQLite with SQLAlchemy and Alembic migrations.
- Password hashing: Argon2id.
- Session encryption: AES-GCM through a maintained cryptography library.
- Testing: pytest, Vitest or React Testing Library, and Playwright.

The implementation favors small, feature-focused modules and immutable domain values. Instaloader-specific behavior is confined to the adapter layer.

## 6. Data Model

### 6.1 Administrator and browser sessions

`admin_users`

- Contains exactly one active administrator.
- Stores username, Argon2id password hash, forced-password-change flag, and timestamps.

`web_sessions`

- Stores a hash of an opaque random browser session identifier.
- Tracks creation, last use, absolute expiration, revocation, and client metadata needed for security auditing.
- No JWT or administrator secret is stored in browser local storage.

### 6.2 Instagram session

`instagram_sessions`

- Tracks Instagram username, session status, last successful validation, and non-secret diagnostics.
- Encrypted cookie material is stored in `/data/sessions/instagram.enc`.
- Encryption uses a key supplied through `APP_SECRET_KEY` or a Docker secret.
- Instagram passwords and 2FA codes are never persisted.

### 6.3 Profiles and relationships

`profiles`

- Instagram numeric user ID as stable identity.
- Current username and previous-name metadata.
- Display name, biography, profile picture reference, privacy state, and normalized profile metadata.
- Tracking state: `tracked`, `archived`, `deletion_pending`, or `deletion_failed`.
- Last attempted and successful synchronization timestamps.

`media_items`

- Instagram media ID and shortcode with uniqueness constraints.
- Normalized type: `post`, `reel`, or `story`.
- Owner profile ID, caption, accessibility description, publication time, original URL, availability, and local lifecycle status.
- Lifecycle status includes `active`, `ignored`, `unavailable`, and `deletion_pending`.

`media_assets`

- One row per image, video, or thumbnail.
- Local path, MIME type, size, optional dimensions and duration, checksum, and carousel order.

`profile_media_relations`

- Connects a media item to a profile and records why it appears there.
- Relation types include `owned`, `tagged`, and `manual`.
- A globally unique media item can appear in several profile views without duplicate files.

`story_groups`

- Stores the profile association and grouping information needed for the Story-style circles in the UI.
- Story items retain their original expiry timestamp while remaining viewable as a local archive.

### 6.4 Jobs and settings

`jobs`

- Persistent type, state, priority, progress counters, attempt count, retry time, concise error code, and redacted error message.
- States include `pending`, `running`, `waiting_for_user`, `retry_scheduled`, `succeeded`, and `failed`.
- Job payloads use validated JSON and never contain plaintext passwords or cookies.

`settings`

- Global synchronization interval, default six hours.
- Posts enabled by default.
- Reels enabled by default.
- Tagged posts disabled by default.
- Stories disabled by default.
- Next scheduled synchronization timestamp and proxy-related application settings.

`audit_events`

- Records login failures, password changes, Instagram session changes, destructive actions, and important configuration changes.
- Never records passwords, 2FA codes, cookie values, or unredacted authorization headers.

### 6.5 Individual-media behavior

Downloading one post or reel automatically creates or updates its owner profile so the item has a natural Profile page. The profile is not marked `tracked` and is not included in scheduled profile synchronization unless the user explicitly chooses to track it.

Media files are globally deduplicated by Instagram media ID and shortcode. Physical assets are removed only when no remaining active library relationship references them. This prevents deleting a tagged or manually saved item that is still visible through another profile.

## 7. Download and Synchronization Flow

### 7.1 Smart input

The central add action accepts:

- `@username`
- A plain Instagram username
- An Instagram Profile URL
- An Instagram `/p/<shortcode>/` URL
- An Instagram `/reel/<shortcode>/` URL
- An Instagram `/tv/<shortcode>/` URL

The backend validates the hostname and extracts a username or shortcode. It does not fetch an arbitrary user-provided URL, preventing SSRF. The UI first displays a normalized preview and creates a job only after confirmation.

### 7.2 Individual media

```text
Validate input
→ create persistent job
→ worker loads encrypted Instagram session
→ Post.from_shortcode()
→ download_post() into /data/tmp/jobs/<job-id>
→ validate output and normalize metadata
→ atomically move files into /data/media
→ commit database records
```

The staging directory prevents partial downloads from appearing in the library. Stale staging directories are cleaned after recovery rules have classified the associated job.

### 7.3 Profile synchronization

The worker calculates the next run using one global interval. Changing the interval updates the next run without creating duplicate schedules.

Each synchronization:

1. Loads all profiles in `tracked` state.
2. Applies the global Posts, Reels, Tagged, and Stories switches.
3. Checks returned media IDs and shortcodes against SQLite.
4. Inspects possible pinned posts even when their timestamps are old.
5. Downloads only unknown or explicitly requested items.
6. Retains `ignored` items as tombstones and does not download them again.
7. Updates profile and synchronization metadata in bounded transactions.

Only one global synchronization job and one job per profile may run at a time. A manual synchronization request is coalesced with an equivalent pending or running scheduled request.

### 7.4 Scheduling and restart

The database is the source of truth for jobs and the next scheduled time. Leaving the UI has no effect on active work.

At worker startup:

- An interrupted idempotent job is returned to `pending`.
- A destructive job resumes from its persisted batch checkpoint.
- A future retry retains its retry timestamp.
- Invalid or unrecoverable state is marked `failed` with a user-visible action.

## 8. Deletion Semantics

All delete actions use a confirmation dialog. Profile deletion does not require retyping the username.

### 8.1 Media deletion

The confirmation dialog offers:

- **Release files and remember the deletion:** remove unreferenced local assets and retain an `ignored` tombstone. Future synchronization does not download it again.
- **Remove completely:** remove the library relationship and unreferenced assets, then remove the tombstone. A future synchronization may download it again.

### 8.2 Profile deletion

The confirmation dialog offers:

- **Stop tracking and keep content:** set the profile to `archived`; remove it from scheduled synchronization while retaining all library content.
- **Stop tracking and delete content:** create a persistent bulk-deletion job. Remove profile relationships and delete only assets that no other active relationship references.

Bulk deletion:

- Sets the profile to `deletion_pending`.
- Prevents new synchronization and duplicate deletion requests.
- Commits progress in batches and records total and completed counts.
- Continues if the user navigates away, logs out, or closes the browser.
- Resumes idempotently after worker or container restart.
- Treats an already absent target file as successfully deleted.
- Moves to `deletion_failed` when a non-recoverable filesystem error remains and supports retry from the Activity page.
- Cannot be canceled after permanent deletion has begun.

The UI keeps the profile visible but disabled while deletion is active and shows persisted progress. After successful content deletion, the profile leaves the normal profile list; a redacted audit event remains.

## 9. Authentication and Security

### 9.1 Administrator bootstrap

- Initial administrator credentials come from Docker environment variables or secrets.
- Bootstrap credentials are used only if no administrator exists.
- The first login requires a password change.
- There is no public registration route.
- A host-side management command resets a forgotten password.
- Login attempts are rate-limited by both normalized username and client IP.

### 9.2 Browser authentication

- Server-side opaque sessions are stored as hashes.
- The browser receives an `HttpOnly`, `SameSite=Lax` cookie.
- `SESSION_COOKIE_SECURE` is configurable for deployments behind HTTPS.
- State-changing requests require CSRF validation.
- Password changes revoke other browser sessions.
- Security headers include a restrictive Content Security Policy, frame protection, MIME sniffing protection, and a safe referrer policy.

### 9.3 Instagram login and import

The WebUI presents the following equal choices:

- Username/password followed by 2FA when requested.
- Cookie JSON upload.
- Netscape `cookies.txt` upload.
- Raw `Cookie` header paste.

Cookie import:

- Enforces a small input size limit.
- Parses data-only formats and rejects pickle.
- Accepts only appropriate Instagram domains and valid cookie syntax.
- Rejects control characters and conflicting duplicates.
- Validates the resulting session with `Instaloader.test_login()`.
- Encrypts and stores cookies only after validation succeeds.
- Clears plaintext import material after use.

Instagram passwords and 2FA codes exist only for the request flow and never enter job payloads, logs, database fields, frontend storage, or API responses.

### 9.4 Filesystem and media access

- `/data/media` is never exposed as a public static directory.
- Authenticated API endpoints resolve a media ID through the database.
- Resolved paths must remain inside the configured media root.
- User-provided filesystem paths are never accepted.
- Media responses support safe byte ranges for video playback.
- Destructive filesystem targets are resolved and validated before deletion.

### 9.5 HTTP-only boundary

The project does not implement HTTPS or ship a reverse proxy. It supports deployment behind user-managed infrastructure through configurable allowed hosts, public URL, trusted proxy count, and secure-cookie settings.

Documentation prominently warns that directly exposing the service over public HTTP can disclose the administrator password, Instagram credentials, 2FA codes, and session cookies in transit.

## 10. UI Design

### 10.1 Navigation

The selected layout is a hybrid Instagram-style home:

- Profile shortcut row at the top.
- Recently downloaded content below.
- Mobile bottom navigation: Home, Profiles, Add, Activity, Settings.
- Desktop navigation becomes a persistent left sidebar.

### 10.2 Profile page

The selected Profile page follows Instagram's familiar layout:

- Profile image, username, biography, media counts, and last synchronization state.
- Story archive circles.
- Posts, Reels, and Tagged icon tabs.
- A synchronization action and profile management menu.
- Responsive three-column media grid.

Disabled global categories remain visible with an explanatory empty state and a link to synchronization settings rather than silently disappearing.

### 10.3 Media viewer

The viewer supports:

- Image and video display.
- Carousel navigation.
- Caption, publication time, owner, source type, and original Instagram URL.
- Local availability and download state.
- Delete and retry actions.

### 10.4 Activity and settings

The Activity page shows persistent running, pending, retrying, completed, and failed jobs. Reloading the page reconstructs progress from the API.

Settings cover:

- Global synchronization interval.
- Posts, Reels, Tagged, and Stories switches.
- Instagram session setup and status.
- Administrator password and browser-session revocation.
- Storage usage and non-sensitive service diagnostics.

Destructive dialogs focus the non-destructive action by default, do not preselect a destructive option, and disable duplicate submission while the API processes the request.

## 11. API Design

API groups:

```text
/api/auth/*               Administrator authentication and browser sessions
/api/instagram-session/*  Instagram login, 2FA, imports, and validation
/api/profiles/*            Profile listing, detail, tracking, and synchronization
/api/media/*               Media listing, detail, streaming, retry, and deletion
/api/jobs/*                Job listing, status, retry, and allowed cancellation
/api/settings/*            Global synchronization and service settings
```

Responses use one envelope:

```json
{
  "success": true,
  "data": {},
  "error": null,
  "meta": {}
}
```

Errors use stable machine-readable codes and safe user-facing messages. Internal exceptions and Instagram response bodies are not returned directly.

Lists use cursor pagination with deterministic ordering. All request boundaries use Pydantic validation. Mutating endpoints are idempotent where practical and use database constraints to prevent duplicate profiles, media, or active jobs.

## 12. Error Handling

Errors are classified into actionable categories:

- **Instagram session expired:** pause Instagram-dependent scheduled work and request reauthentication.
- **2FA or checkpoint required:** move the interactive login flow to `waiting_for_user`.
- **Private profile not followed:** report a permission error without automatic retries.
- **Rate limit or transient network failure:** retry a bounded number of times using exponential backoff and jitter.
- **Post unavailable or removed:** mark the remote item unavailable while retaining existing local content.
- **Disk full:** stop new file writes, preserve existing data, and surface a storage error.
- **Invalid or malicious import:** reject without storing the payload.
- **Filesystem deletion failure:** preserve progress and allow an idempotent retry.

Logs contain structured job and error context but redact passwords, 2FA codes, cookies, authorization headers, and sensitive URL parameters.

## 13. Testing Strategy

Development follows test-driven development with at least 80% measured coverage.

### 13.1 Unit tests

- Username and shortcode parsing.
- Supported Instagram URL validation and SSRF rejection.
- Cookie JSON, Netscape file, and raw header parsing.
- Scheduler calculations and job coalescing.
- Path containment and deletion target validation.
- Lifecycle and tombstone transitions.
- Error classification and redaction.

### 13.2 Integration tests

- FastAPI authentication, CSRF, rate limiting, and API envelopes.
- SQLite migrations, WAL behavior, uniqueness constraints, and job recovery.
- Web and worker coordination.
- Session encryption and key failures.
- Authenticated media streaming and byte ranges.
- Bulk deletion persistence and idempotent resume.

### 13.3 Adapter contract tests

Versioned response fixtures exercise Instaloader normalization without contacting Instagram during normal tests. Tests cover single images, videos, reels, carousels, captions, profile changes, tagged relations, stories, and expected upstream exceptions.

### 13.4 End-to-end tests

Playwright covers:

- Initial administrator login and forced password change.
- Instagram login state UI and safe cookie import using test fixtures.
- Smart-input preview for profiles, posts, and reels.
- Profile and media browsing on mobile and desktop viewports.
- Global schedule updates.
- Manual synchronization.
- Media and profile deletion.
- Leaving and revisiting the UI while a bulk deletion continues.

### 13.5 Container tests

- Build the multi-stage image.
- Start `web` and `worker` with a temporary `/data` volume.
- Validate health checks.
- Confirm persistence and job recovery across container restart.
- Verify the final image does not contain Node.js build caches, development dependencies, test secrets, or source-map artifacts not intended for production.

Normal CI never requires a real Instagram account or session. Any live Instagram smoke test is opt-in and excluded from the default suite.

## 14. Acceptance Criteria

- Existing Instaloader source files remain unchanged.
- Docker Compose starts separate `web` and `worker` services from the same image.
- Recreating containers with the same `/data` mount preserves accounts, settings, jobs, sessions, metadata, and media.
- The service downloads a single post or reel without downloading the owner's full profile.
- Tracking a profile incrementally retrieves new posts and reels.
- Global synchronization interval and type switches are editable in the WebUI.
- Posts and Reels default to enabled; Tagged and Stories default to disabled.
- Duplicate Instagram media does not create duplicate physical files.
- Ignored media remains ignored during future synchronization.
- Background and deletion work survives UI navigation and container restart.
- Mobile and desktop interfaces implement the approved hybrid home, Instagram-style Profile page, and smart add flow.
- Every management and media endpoint requires authentication.
- Passwords, 2FA codes, and cookie values do not appear in logs, API responses, job payloads, or browser storage.
- All automated tests pass with at least 80% coverage.
- Documentation clearly states that HTTPS and reverse proxy configuration are the user's responsibility.
