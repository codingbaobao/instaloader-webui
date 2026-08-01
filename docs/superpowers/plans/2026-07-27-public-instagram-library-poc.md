# Public Instagram Library POC Implementation Plan

> **Status — superseded for schema work (2026-08-01):** This is a historical
> plan. Do not execute its Alembic, migration, database-upgrade/downgrade, or
> migration-startup instructions. The current pre-1.0 runtime supports only
> the exact fresh schema and does not migrate older databases.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an end-to-end public Instagram archive that accepts profile and post/reel input, downloads through the unmodified sibling Instaloader package, and displays persisted media in the React WebUI.

**Architecture:** FastAPI creates and reads library records and persistent SQLite jobs. A separate single-process worker claims jobs, calls the upstream Instaloader Python API, stages files under `/data/tmp`, finalizes them below `/data/media`, and updates normalized database records. The React SPA polls the API for profiles, media, settings, and activity.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, SQLite WAL, Instaloader 4.15.2 sibling source, React 18, TypeScript, Vite, Docker Compose.

## Global Constraints

- Treat `C:\Users\z2101\instaloader\instaloader-webui` as the project root.
- Work directly on `main`; preserve all existing uncommitted authentication changes.
- Do not modify any file under the outer repository's `instaloader/` package.
- Support anonymous public Instagram profiles, posts, reels, and TV URLs only.
- Defer Cookie import, Instagram login, 2FA, private profiles, Stories, and Tagged.
- Keep all persistent database, media, staging, and internal application state under `/data`.
- Keep the existing single WebUI administrator and API envelope.
- Use one SQLite-backed worker process; web requests never wait for Instagram downloads.
- Prioritize a functioning POC over security hardening and architectural generalization.
- Do not add or execute unit, integration, smoke, or end-to-end tests.
- Build/type/compose validation and focused manual API/UI checks are allowed.
- All delete actions shown in the UI use a confirmation dialog.

---

### Task 1: Library schema, snapshots, and repositories

**Files:**

- Modify: `backend/src/instaloader_webui/config.py`
- Modify: `backend/src/instaloader_webui/db/models.py`
- Create: `backend/src/instaloader_webui/db/library_repositories.py`
- Create: `backend/migrations/versions/0006_public_library.py`
- Modify: `backend/src/instaloader_webui/main.py`

**Interfaces:**

- Produces `Settings.media_root`, `Settings.jobs_root`, and `Settings.profile_sync_interval_minutes`.
- Produces immutable `ProfileSnapshot`, `MediaSnapshot`, `AssetSnapshot`, `JobSnapshot`, and `AppSettingsSnapshot`.
- Produces `LibraryRepository`, `JobRepository`, and `SettingsRepository`.
- Later tasks consume the repositories through FastAPI `app.state` and the worker bootstrap.

- [ ] **Step 1: Extend runtime paths and defaults**

Add computed paths under `data_root`:

```python
@computed_field
@property
def media_root(self) -> Path:
    return self.data_root / "media"

@computed_field
@property
def jobs_root(self) -> Path:
    return self.data_root / "tmp" / "jobs"
```

Add `profile_sync_interval_minutes: int = 360` with a positive integer
constraint. Create both directories during web and worker startup.

- [ ] **Step 2: Add ORM models**

Add `Profile`, `MediaItem`, `MediaAsset`, `Job`, and `AppSetting` models using
string UUID primary keys and UTC timestamps. Use these exact uniqueness rules:

- `profiles.username` unique and non-null;
- `profiles.instagram_user_id` unique when non-null;
- `media_items.shortcode` unique and non-null;
- `media_items.instagram_media_id` unique when non-null;
- one `app_settings` row with ID `global`;
- indexes on `jobs(state, created_at)`, `media_items(owner_profile_id, published_at)`,
  and `profiles(tracked, status)`.

`Profile.status` uses `active`, `deletion_pending`, or `deletion_failed`.
`MediaItem.kind` uses `post` or `reel`. `Job.type` uses `profile_sync`,
`single_media`, `delete_profile`, or `delete_media`. `Job.state` uses
`pending`, `running`, `succeeded`, or `failed`.

- [ ] **Step 3: Create migration `0006_public_library.py`**

Create all five tables, foreign keys, uniqueness constraints, and indexes.
Insert the singleton `app_settings` row with interval `360` and a due
`next_sync_at` timestamp. Downgrade removes only the new POC tables and indexes.

- [ ] **Step 4: Implement immutable repository boundaries**

`LibraryRepository` exposes:

```python
def list_profiles(self) -> tuple[ProfileSnapshot, ...]
def get_profile(self, profile_id: str) -> ProfileSnapshot | None
def find_profile_by_username(self, username: str) -> ProfileSnapshot | None
def upsert_profile_stub(self, *, username: str, tracked: bool, now: datetime) -> ProfileSnapshot
def update_profile_metadata(self, *, profile_id: str, instagram_user_id: str,
                            username: str, full_name: str, biography: str,
                            profile_pic_url: str | None, now: datetime) -> ProfileSnapshot
def set_profile_sync_result(self, *, profile_id: str, succeeded: bool,
                            now: datetime) -> ProfileSnapshot
def mark_profile_for_deletion(self, profile_id: str, now: datetime) -> ProfileSnapshot | None
def delete_profile_records(self, profile_id: str) -> tuple[str, ...]
def list_media(self, *, profile_id: str | None = None,
               kind: str | None = None, limit: int = 100) -> tuple[MediaSnapshot, ...]
def get_media(self, media_id: str) -> MediaSnapshot | None
def find_media_by_shortcode(self, shortcode: str) -> MediaSnapshot | None
def upsert_media(self, *, normalized: NormalizedMedia, profile_id: str,
                 assets: tuple[NormalizedAsset, ...], now: datetime) -> MediaSnapshot
def delete_media_records(self, media_id: str) -> tuple[str, ...]
```

Snapshots include nested asset tuples so API and worker code do not depend on
mutable ORM instances. `NormalizedMedia` and `NormalizedAsset` are frozen
dataclasses in the same module.

`JobRepository` exposes:

```python
def enqueue(self, *, job_type: str, payload: dict[str, object],
            status_text: str, now: datetime) -> JobSnapshot
def list(self, limit: int = 100) -> tuple[JobSnapshot, ...]
def get(self, job_id: str) -> JobSnapshot | None
def claim_next(self, now: datetime) -> JobSnapshot | None
def update_progress(self, *, job_id: str, current: int,
                    total: int | None, status_text: str, now: datetime) -> None
def succeed(self, *, job_id: str, status_text: str, now: datetime) -> None
def fail(self, *, job_id: str, error: str, now: datetime) -> None
def recover_interrupted(self, now: datetime) -> int
def has_active_profile_sync(self, profile_id: str) -> bool
```

Claiming uses `BEGIN IMMEDIATE` and changes exactly one oldest pending job to
running. Store job payloads as JSON text.

`SettingsRepository` exposes `get()`, `update_interval(minutes, now)`, and
`claim_due_sync(now)`. `claim_due_sync` moves `next_sync_at` forward atomically
and returns whether the caller owns this schedule tick.

- [ ] **Step 5: Wire repositories into FastAPI lifespan**

Construct the repositories from the existing session factory and assign:

```python
app.state.library_repository
app.state.job_repository
app.state.settings_repository
```

Do not change the existing authentication service behavior.

- [ ] **Step 6: Perform non-test structural checks**

Inspect the migration revision chain, confirm all imports resolve by inspection,
and run `git diff --check`. Do not invoke pytest.

- [ ] **Step 7: Commit the task**

```bash
git add backend/src/instaloader_webui/config.py \
  backend/src/instaloader_webui/db/models.py \
  backend/src/instaloader_webui/db/library_repositories.py \
  backend/src/instaloader_webui/main.py \
  backend/migrations/versions/0006_public_library.py
git commit -m "feat: add public media library persistence"
```

### Task 2: Input parser and complete library API

**Files:**

- Create: `backend/src/instaloader_webui/services/instagram_inputs.py`
- Create: `backend/src/instaloader_webui/services/library_service.py`
- Modify: `backend/src/instaloader_webui/api/dependencies.py`
- Create: `backend/src/instaloader_webui/api/routes/profiles.py`
- Create: `backend/src/instaloader_webui/api/routes/media.py`
- Create: `backend/src/instaloader_webui/api/routes/jobs.py`
- Create: `backend/src/instaloader_webui/api/routes/settings.py`
- Modify: `backend/src/instaloader_webui/main.py`

**Interfaces:**

- Consumes Task 1 snapshots and repositories.
- Produces `ParsedInstagramInput(kind: Literal["profile", "media"], value: str,
  original_url: str | None)` and `parse_instagram_input(raw: str)`.
- Produces the `/api/profiles`, `/api/media`, `/api/jobs`, and `/api/settings`
  endpoints consumed by Task 4.

- [ ] **Step 1: Implement smart input parsing**

Normalize whitespace and leading `@`. Accept plain usernames matching
`[A-Za-z0-9._]{1,30}` and only `instagram.com` or `www.instagram.com` HTTPS
URLs. Recognize profile roots and `/p/`, `/reel/`, `/tv/` shortcodes. Return a
frozen parsed value and raise a domain-specific `InvalidInstagramInput` for
everything else.

- [ ] **Step 2: Implement `LibraryService`**

Expose:

```python
def add_profile(self, raw_input: str, now: datetime) -> tuple[ProfileSnapshot, JobSnapshot]
def add_media(self, raw_input: str, now: datetime) -> JobSnapshot
def sync_profile(self, profile_id: str, now: datetime) -> JobSnapshot
def sync_all(self, now: datetime) -> tuple[JobSnapshot, ...]
def delete_profile(self, profile_id: str, now: datetime) -> JobSnapshot
def delete_media(self, media_id: str, now: datetime) -> JobSnapshot
```

Profile input creates or reuses a tracked stub and coalesces an existing active
profile-sync job. Single-media input enqueues only a shortcode and original URL;
it does not create a profile until the worker resolves the owner.

- [ ] **Step 3: Add dependency getters**

Add typed getters for `LibraryRepository`, `JobRepository`,
`SettingsRepository`, and `LibraryService` from `request.app.state`.

- [ ] **Step 4: Add profile routes**

Implement:

- `GET /api/profiles`
- `POST /api/profiles` with `{ "input": "..." }`
- `GET /api/profiles/{profile_id}`
- `POST /api/profiles/{profile_id}/sync`
- `DELETE /api/profiles/{profile_id}`

Read routes require `require_password_change_complete`. Mutations require
`require_csrf`. Serialize profile snapshots and include profile media counts.

- [ ] **Step 5: Add media routes**

Implement:

- `GET /api/media?profile_id=&kind=&limit=`
- `POST /api/media` with `{ "input": "..." }`
- `GET /api/media/{media_id}`
- `GET /api/media/{media_id}/assets/{asset_id}`
- `DELETE /api/media/{media_id}`

The asset route resolves the database relative path below `Settings.media_root`
and returns `FileResponse` with the stored MIME type. Reject missing database
records or files with stable `media_not_found` and `asset_not_found` errors.

- [ ] **Step 6: Add job and settings routes**

Implement:

- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/settings`
- `PATCH /api/settings` with `{ "profile_sync_interval_minutes": integer }`
- `POST /api/settings/sync-all`

Use the existing envelope and return ISO-8601 timestamps.

- [ ] **Step 7: Register routes and service**

Build `LibraryService` in the FastAPI lifespan, store it in `app.state`, and
include all four routers before installing the SPA fallback.

- [ ] **Step 8: Perform non-test API review**

Compare every route against the approved spec, inspect CSRF/session dependency
usage, and run `git diff --check`. Do not invoke pytest or API smoke tests.

- [ ] **Step 9: Commit the task**

```bash
git add backend/src/instaloader_webui/api \
  backend/src/instaloader_webui/services/instagram_inputs.py \
  backend/src/instaloader_webui/services/library_service.py \
  backend/src/instaloader_webui/main.py
git commit -m "feat: expose public library APIs"
```

### Task 3: Instaloader adapter, worker, scheduling, and deletion

**Files:**

- Modify: `backend/pyproject.toml`
- Create: `backend/src/instaloader_webui/instagram/__init__.py`
- Create: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Create: `backend/src/instaloader_webui/services/job_runner.py`
- Create: `backend/src/instaloader_webui/worker.py`

**Interfaces:**

- Consumes Task 1 repositories and Task 2 job payload formats.
- Produces `PublicInstaloaderAdapter.fetch_profile()`,
  `download_shortcode()`, and `sync_profile()`.
- Produces console entry point `instaloader-webui-worker`.

- [ ] **Step 1: Configure deterministic Instaloader output**

Construct a fresh anonymous `Instaloader` per job with:

```python
Instaloader(
    dirname_pattern=str(staging_directory),
    filename_pattern="{shortcode}",
    download_pictures=True,
    download_videos=True,
    download_video_thumbnails=True,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    post_metadata_txt_pattern="",
    quiet=True,
)
```

The adapter receives `data_root`, `media_root`, `jobs_root`,
`LibraryRepository`, and a progress callback. It never mutates upstream source.

- [ ] **Step 2: Normalize profiles and posts**

`fetch_profile(username)` resolves `Profile.from_username()` and returns numeric
ID, username, full name, biography, and profile picture URL.

`download_shortcode(shortcode, job_id, expected_kind=None)`:

1. clears and recreates `/data/tmp/jobs/<job_id>`;
2. loads `Post.from_shortcode()`;
3. upserts the owner profile as untracked;
4. calls `download_post()`;
5. discovers `<shortcode>*.jpg` and `<shortcode>*.mp4`;
6. moves them to `/data/media/profiles/<profile-id>/<shortcode>/`;
7. builds ordered `NormalizedAsset` values;
8. persists caption, accessibility caption, publication time, original URL,
   media ID, and kind;
9. removes the staging directory.

Treat posts coming from the reels iterator as `reel`; direct GraphVideo input
uses the URL path hint when available and otherwise remains `post`.

- [ ] **Step 3: Implement profile synchronization**

`sync_profile(profile_id, job_id)` refreshes profile metadata, then iterates
`profile.get_posts()` and `profile.get_reels()`. Skip shortcodes already having
local assets, but update an existing post to kind `reel` when it appears in the
reels iterator. Report progress after every inspected item.

Use the repository's tracked state before each iterator and stop when the
profile enters deletion state. Anonymous private/inaccessible profiles raise a
concise adapter error that the runner stores on the job.

- [ ] **Step 4: Implement `JobRunner`**

Dispatch exact job types:

- `profile_sync` → adapter `sync_profile`;
- `single_media` → adapter `download_shortcode`;
- `delete_media` → delete recorded files, then database records;
- `delete_profile` → remove all profile files with progress, then database
  records.

Always mark jobs succeeded or failed. An absent file counts as deleted.
Filesystem and Instaloader exception messages are shortened before persistence.

- [ ] **Step 5: Implement worker process and scheduler**

`worker.main()` loads settings, runs migrations, builds engine/repositories,
creates runtime directories, recovers interrupted jobs, and loops:

```python
while True:
    enqueue_due_profile_syncs(...)
    job = jobs.claim_next(datetime.now(UTC))
    if job is None:
        time.sleep(2)
        continue
    runner.run(job)
```

Scheduled ticks enqueue one sync job per tracked profile unless an active
profile-sync job already exists.

- [ ] **Step 6: Add worker console entry point**

Add:

```toml
[project.scripts]
instaloader-webui-worker = "instaloader_webui.worker:main"
```

The upstream Instaloader dependency is supplied as a separately built sibling
wheel in Task 5, not as a PyPI version pin.

- [ ] **Step 7: Perform non-test worker review**

Inspect staging/final path construction, job state transitions, scheduler
coalescing, and cleanup paths. Run `git diff --check`; do not contact Instagram
or invoke pytest.

- [ ] **Step 8: Commit the task**

```bash
git add backend/pyproject.toml \
  backend/src/instaloader_webui/instagram \
  backend/src/instaloader_webui/services/job_runner.py \
  backend/src/instaloader_webui/worker.py
git commit -m "feat: add public Instaloader download worker"
```

### Task 4: Instagram-style React library UI

**Files:**

- Modify: `frontend/src/app/api.ts`
- Modify: `frontend/src/app/App.tsx`
- Create: `frontend/src/library/types.ts`
- Create: `frontend/src/library/api.ts`
- Create: `frontend/src/library/usePolling.ts`
- Create: `frontend/src/library/HomePage.tsx`
- Create: `frontend/src/library/AddPage.tsx`
- Create: `frontend/src/library/ProfilesPage.tsx`
- Create: `frontend/src/library/ProfilePage.tsx`
- Create: `frontend/src/library/MediaGrid.tsx`
- Create: `frontend/src/library/MediaViewerPage.tsx`
- Create: `frontend/src/library/ActivityPage.tsx`
- Create: `frontend/src/library/SettingsPage.tsx`
- Create: `frontend/src/library/ConfirmDialog.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**

- Consumes Task 2 JSON DTOs and existing `apiRequest`.
- Produces routes `/`, `/add`, `/profiles`, `/profiles/:profileId`,
  `/media/:mediaId`, `/activity`, and `/settings`.

- [ ] **Step 1: Add immutable DTO types and API functions**

Define `ProfileSummary`, `ProfileDetail`, `MediaSummary`, `MediaDetail`,
`MediaAsset`, `JobSummary`, and `LibrarySettings` matching backend field names.
Implement typed functions for every Task 2 endpoint. Mutations read the current
CSRF token from the session passed by each page.

- [ ] **Step 2: Add reusable polling and confirmation**

`usePolling(load, intervalMs, enabled)` reloads jobs/library data while mounted
and returns data, loading, and error state. `ConfirmDialog` uses the native
`dialog` element with Cancel focused by default and invokes the destructive
callback only after explicit confirmation.

- [ ] **Step 3: Build Home and Add pages**

Home shows profile shortcut circles, recent media grid, loading/error states,
and a useful empty state.

Add provides one smart input with examples. It classifies `/p/`, `/reel/`, and
`/tv/` as media; every other accepted value goes to profiles. On success show
the job state and actions to open Activity or the created profile.

- [ ] **Step 4: Build Profiles and Profile pages**

Profiles displays avatar, username, name, counts, tracking state, and last sync.
Profile detail displays biography, sync/delete controls, Posts and Reels tabs,
and a responsive three-column `MediaGrid`.

- [ ] **Step 5: Build routed media viewer**

Load one media item and display:

- image assets with carousel previous/next controls;
- video assets with native `controls`;
- caption, owner, publication time, type, and original Instagram link;
- confirmed delete action.

Use authenticated asset API URLs directly in `img` and `video` elements.

- [ ] **Step 6: Build Activity and Settings pages**

Activity polls every two seconds while pending/running jobs exist and renders
progress bars plus failure text. Settings edits the interval, triggers sync-all,
and clearly states that only public Instagram content is supported.

- [ ] **Step 7: Replace placeholder routing**

Keep the existing authenticated shell and responsive navigation. Replace all
`PlaceholderPage` elements and add profile/media parameterized routes.

- [ ] **Step 8: Implement responsive visual styling**

Extend `global.css` with:

- centered content width and Instagram-like white surfaces;
- profile shortcut row;
- three-column square media grids;
- object-fit images and video;
- routed viewer two-column desktop layout and stacked mobile layout;
- job cards/progress;
- forms, empty states, tabs, badges, and confirmation dialog;
- existing mobile bottom navigation and desktop sidebar behavior.

- [ ] **Step 9: Run build-only verification**

Run:

```bash
npm run build
```

This is a TypeScript/Vite build, not a test. Fix compilation failures without
adding or running frontend tests.

- [ ] **Step 10: Commit the task**

```bash
git add frontend/src/app frontend/src/library frontend/src/styles/global.css
git commit -m "feat: add public Instagram library UI"
```

### Task 5: Docker packaging, Compose worker, and POC handoff

**Files:**

- Modify: `docker/Dockerfile`
- Create: `docker/Dockerfile.dockerignore`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**

- Consumes the worker console entry point and compiled frontend.
- Produces one image containing the local upstream Instaloader and WebUI.
- Produces `web` and `worker` services sharing `/data`.

- [ ] **Step 1: Build upstream and WebUI wheels**

Change Compose build context to the outer repository and Dockerfile to
`instaloader-webui/docker/Dockerfile`. Add
`docker/Dockerfile.dockerignore` so the parent build context excludes Git
metadata, data directories, caches, tests, and Node dependencies. In the Python
builder:

1. copy outer `setup.py`, `README.rst`, `LICENSE`, and `instaloader/`;
2. build the local Instaloader wheel into `/wheels`;
3. copy `instaloader-webui/backend/`;
4. build the WebUI wheel into `/wheels`.

Update frontend COPY paths for the outer build context. Install both wheels in
the runtime image using `--no-index --find-links=/wheels`.

- [ ] **Step 2: Add worker service**

Keep the existing `web` service and add `worker` using the same image:

```yaml
command: ["/bin/sh", "-c", "umask 077 && exec instaloader-webui-worker"]
```

Mount the same `/data`, use the same `IW_DATA_ROOT`, apply the existing runtime
restrictions, and make worker depend on web health only to avoid simultaneous
migrations during the POC startup.

- [ ] **Step 3: Update environment and documentation**

Document public-only limitations, add/profile/link usage, Activity progress,
sync interval, storage layout, two-service Compose deployment, and the later
Instagram-session milestone. Do not reintroduce `IW_APP_SECRET_KEY`.

- [ ] **Step 4: Run non-test packaging checks**

Run:

```bash
docker compose config
docker compose build
```

Do not run the container smoke test or any test suite. If Docker is unavailable,
record the exact build blocker and still complete static Compose review.

- [ ] **Step 5: Perform focused manual acceptance**

When Docker and Instagram network access are available:

1. start `docker compose up -d`;
2. sign in through the existing administrator UI;
3. add one user-selected public post/reel URL;
4. confirm Activity reaches succeeded;
5. open the downloaded image/video and caption;
6. add one user-selected public profile;
7. confirm profile sync and media grid population;
8. recreate containers with the same `/data` and confirm records remain.

This is manual POC verification, not an automated smoke test. If no public
target is supplied, stop after build/start verification and ask the user for a
target instead of selecting a large profile.

- [ ] **Step 6: Commit the task**

```bash
git add docker/Dockerfile docker/Dockerfile.dockerignore \
  compose.yaml .env.example README.md
git commit -m "feat: package public Instagram library POC"
```

## Final Review

- [ ] Confirm every POC completion criterion in the approved design maps to a
  completed task.
- [ ] Confirm no Instagram authentication, Cookie, 2FA, Story, or Tagged code
  was added.
- [ ] Confirm no outer `instaloader/` source file changed.
- [ ] Review the full branch diff for obvious runtime blockers.
- [ ] Do not run unit, integration, smoke, or end-to-end tests.
