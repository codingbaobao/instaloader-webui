# Profile Sync Control and Activity Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent per-profile Stop Sync and Resume Sync behavior, skip complete duplicate media downloads, and make Activity reliably render jobs with ten-second polling plus immediate refresh.

**Architecture:** Reuse `Profile.tracked` as the persisted synchronization switch and keep `Profile.status` dedicated to deletion lifecycle. Expose the switch through the existing profiles API, enforce it in service scheduling and worker media-boundary checks, and reuse shortcode plus verified local assets for duplicate detection. Stabilize Activity by giving it one fixed polling interval instead of rebuilding its polling controller from returned job state.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, Instaloader, React 19, TypeScript, Vite, Docker Compose, SQLite.

## Global Constraints

- Work directly in `C:\Users\z2101\instaloader\instaloader-webui` on `main`; the user explicitly approved direct-main development for this project.
- `profiles.tracked=true` means Sync active and `profiles.tracked=false` means Sync stopped; do not add a database column or migration.
- `profiles.status` remains limited to `active`, `deletion_pending`, and `deletion_failed`.
- Stop Sync must never interrupt `loader.download_post()`, asset finalization, database persistence, or asset commit/rollback.
- Stop Sync does not cancel a single-post or single-reel job.
- Resume Sync makes the profile eligible again but does not enqueue a job itself.
- Only a profile first created from a single-media download defaults to stopped; an existing profile keeps its current setting.
- A complete duplicate shortcode succeeds without an Instagram request; a record with any missing asset is repaired through the normal download path.
- Activity polls every `10_000` milliseconds and keeps a visible immediate Refresh action.
- API responses use the existing envelope and fixed user-safe error text; do not expose Instagram response bodies, Cookie contents, absolute paths, or tracebacks.
- Do not add, modify, or run unit, integration, smoke, or end-to-end tests.
- Allowed verification consists of Python compilation, frontend TypeScript/Vite production build, Docker Compose configuration/build checks, and Git diff checks.

---

## File Structure

- `backend/src/instaloader_webui/db/library_repositories.py`: persist the profile synchronization switch.
- `backend/src/instaloader_webui/services/library_service.py`: enforce stopped and deletion states at application-operation boundaries.
- `backend/src/instaloader_webui/api/routes/profiles.py`: expose the authenticated CSRF-protected sync-state PATCH endpoint and map fixed errors.
- `backend/src/instaloader_webui/instagram/public_adapter.py`: stop profile iteration at safe media boundaries and skip complete duplicate shortcodes before Instagram access.
- `backend/src/instaloader_webui/services/job_runner.py`: preserve meaningful successful completion messages from stopped and duplicate-skipped jobs.
- `frontend/src/library/api.ts`: call the sync-state PATCH endpoint.
- `frontend/src/library/ProfilePage.tsx`: render Stop Sync, Resume Sync, confirmation, and stopped manual-sync state.
- `frontend/src/library/ProfilesPage.tsx`: display user-facing synchronization badges.
- `frontend/src/library/ActivityPage.tsx`: use stable ten-second polling and immediate Refresh.
- `frontend/src/styles/global.css`: style the active and stopped synchronization states using existing visual language.
- `README.md`: explain Stop Sync, Resume Sync, duplicate skipping, and Activity refresh.

---

### Task 1: Persist and Expose the Profile Sync Switch

**Files:**
- Modify: `backend/src/instaloader_webui/db/library_repositories.py`
- Modify: `backend/src/instaloader_webui/services/library_service.py`
- Modify: `backend/src/instaloader_webui/api/routes/profiles.py`

**Interfaces:**
- Produces: `LibraryRepository.set_profile_sync_enabled(profile_id: str, enabled: bool, now: datetime) -> ProfileSnapshot | None`
- Produces: `LibraryService.set_profile_sync_enabled(profile_id: str, enabled: bool, now: datetime) -> ProfileSnapshot`
- Produces: `ProfileNotActiveError` and `ProfileSyncStoppedError`
- Produces: `PATCH /api/profiles/{profile_id}/sync` with body `{"enabled": boolean}` and `ProfileResponse`
- Preserves: `POST /api/profiles/{profile_id}/sync` as the immediate queue action

- [ ] **Step 1: Add the repository sync-state mutation**

Add a focused method beside the existing profile mutation methods:

```python
def set_profile_sync_enabled(
    self, *, profile_id: str, enabled: bool, now: datetime
) -> ProfileSnapshot | None:
    with self._session_factory.begin() as session:
        model = session.get(Profile, profile_id)
        if model is None:
            return None
        if model.status != "active":
            raise ValueError("Profile is not active.")
        model.tracked = enabled
        model.updated_at = _as_utc(now)
        session.flush()
        return _profile_snapshot(model)
```

This mutation must not change profile metadata, media, deletion status, or sync timestamps.

- [ ] **Step 2: Add service-level domain errors and operations**

Add the exact exception classes:

```python
class ProfileNotActiveError(RuntimeError):
    pass


class ProfileSyncStoppedError(RuntimeError):
    pass
```

Add `LibraryService.set_profile_sync_enabled()`. It must:

1. return `ProfileNotFoundError` when the profile does not exist;
2. return `ProfileNotActiveError` when `status != "active"`;
3. call the repository method with the requested boolean;
4. return the immutable updated snapshot.

Harden `LibraryService.sync_profile()` before enqueueing:

```python
if profile.status != "active":
    raise ProfileNotActiveError(profile_id)
if not profile.tracked:
    raise ProfileSyncStoppedError(profile_id)
```

Keep `add_profile()` using `tracked=True`. The existing
`model.tracked = model.tracked or tracked` behavior intentionally resumes a
stopped existing profile only when the user explicitly adds that profile.

- [ ] **Step 3: Add the PATCH request and route**

Add the frozen Pydantic request:

```python
class ProfileSyncStateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
```

Add `PATCH /{profile_id}/sync` next to the existing POST route. It calls the
service and serializes the returned profile with `_serialize_profile()`.

Map errors exactly:

```python
except ProfileNotFoundError as error:
    raise _profile_not_found(profile_id) from error
except ProfileNotActiveError as error:
    raise ApiError(
        409,
        "profile_not_active",
        "This profile cannot change synchronization state while deletion is pending.",
    ) from error
```

Update the POST sync route to additionally map:

```python
except ProfileSyncStoppedError as error:
    raise ApiError(
        409,
        "profile_sync_stopped",
        "Resume synchronization before requesting a profile sync.",
    ) from error
```

Map `ProfileNotActiveError` from POST to the same fixed
`409 profile_not_active` response.

- [ ] **Step 4: Perform bounded static verification**

Run:

```powershell
python -m compileall -q backend/src/instaloader_webui
git diff --check
```

Expected: both commands exit `0`. Do not run automated tests.

- [ ] **Step 5: Review and commit the backend API slice**

Confirm the diff contains no schema migration, no new mutable DTO, and no
upstream exception text. Commit:

```powershell
git add backend/src/instaloader_webui/db/library_repositories.py backend/src/instaloader_webui/services/library_service.py backend/src/instaloader_webui/api/routes/profiles.py
git commit -m "feat: control profile synchronization"
```

---

### Task 2: Stop at Safe Boundaries and Skip Duplicate Media

**Files:**
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Modify: `backend/src/instaloader_webui/services/job_runner.py`

**Interfaces:**
- Consumes: `ProfileSnapshot.tracked` persisted by Task 1
- Produces: duplicate shortcodes with complete assets return without constructing an Instaloader loader
- Produces: stopped profile iteration reports a successful stopped completion message
- Preserves: new single-media owner profiles use `tracked=False`; existing owner profiles retain their current value

- [ ] **Step 1: Add a pre-network duplicate guard to single-media download**

At the start of `download_shortcode()`, normalize the expected kind, then query
`find_media_by_shortcode(shortcode)` before creating or clearing a staging
directory and before calling `_new_loader()`.

Use this exact behavior:

```python
kind = self._normalize_kind(expected_kind)
existing = self._library.find_media_by_shortcode(shortcode)
if existing is not None and self._has_local_assets(existing):
    if kind == "reel" and existing.kind != "reel":
        updated = self._library.set_media_kind(
            shortcode=shortcode,
            kind="reel",
            now=datetime.now(UTC),
        )
        if updated is not None:
            existing = updated
    self._report(
        len(existing.assets),
        len(existing.assets),
        "Media is already saved; skipped duplicate download.",
    )
    return existing
```

If any asset is missing, continue through the existing staged download and
atomic replacement flow. Do not treat a database row alone as a complete
duplicate.

- [ ] **Step 2: Make profile stopping explicit at safe iterator boundaries**

Keep `_profile_is_syncable()` checks before constructing each posts/reels
iterator and at the top of each media loop. Replace the implicit `while
self._profile_is_syncable(profile_id)` condition with an explicit boundary:

```python
while True:
    if not self._profile_is_syncable(profile_id):
        self._report(
            inspected,
            inspected,
            "Profile synchronization stopped before the next media item.",
        )
        return inspected
    try:
        post = next(iterator)
    except StopIteration:
        break
    inspected += 1
    self._sync_post(
        loader=loader,
        post=post,
        job_id=job_id,
        kind=kind,
    )
    self._report(
        inspected,
        None,
        f"Inspected public Instagram {kind} {inspected}.",
    )
```

The next explicit boundary check happens only after `_sync_post()` and its
progress report return, so the current media transaction is complete. Do not
add a tracked check inside `_download_post()`, `_finalize_assets()`, repository
persistence, or commit/rollback.

The initial stopped check remains before Instagram profile access and reports:

```python
"Profile synchronization is stopped."
```

Do not alter `download_shortcode()` based on owner `tracked` state.

- [ ] **Step 3: Preserve successful worker status text**

Currently `JobRunner.run()` overwrites the adapter's final progress message
with `Worker job completed.`. After `_dispatch(job)` succeeds, load the current
job snapshot and preserve its non-empty status:

```python
completed = self._jobs.get(job.id)
status_text = (
    completed.status_text
    if completed is not None and completed.status_text
    else "Worker job completed."
)
self._jobs.succeed(
    job_id=job.id,
    status_text=status_text,
    now=datetime.now(UTC),
)
```

This keeps duplicate-skipped and stopped-at-boundary messages visible without
changing failure handling.

- [ ] **Step 4: Confirm single-media owner state remains non-destructive**

Inspect `_upsert_owner()` and retain:

```python
stored = self._library.upsert_profile_stub(
    username=data.username,
    tracked=False,
    now=datetime.now(UTC),
)
```

Only execute it when neither Instagram user ID nor username resolves an
existing profile. Do not write `tracked=False` onto an existing profile.

- [ ] **Step 5: Perform bounded static verification**

Run:

```powershell
python -m compileall -q backend/src/instaloader_webui
git diff --check
```

Expected: both commands exit `0`. Do not access Instagram and do not run
automated tests.

- [ ] **Step 6: Review and commit worker behavior**

Confirm duplicate lookup occurs before `_new_loader()`, the tracked check occurs
only between media items, and failure recording is unchanged. Commit:

```powershell
git add backend/src/instaloader_webui/instagram/public_adapter.py backend/src/instaloader_webui/services/job_runner.py
git commit -m "feat: stop profile sync safely"
```

---

### Task 3: Add Profile Controls and Repair Activity Polling

**Files:**
- Modify: `frontend/src/library/api.ts`
- Modify: `frontend/src/library/ProfilePage.tsx`
- Modify: `frontend/src/library/ProfilesPage.tsx`
- Modify: `frontend/src/library/ActivityPage.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Consumes: `PATCH /api/profiles/{profile_id}/sync` from Task 1
- Produces: `setProfileSyncEnabled(profileId: string, enabled: boolean, csrfToken: string) -> Promise<ProfileDetail>`
- Produces: Stop Sync confirmation and immediate Resume Sync
- Produces: one stable `10_000` millisecond Activity polling controller with immediate Refresh

- [ ] **Step 1: Add the frontend API helper**

Add:

```typescript
export function setProfileSyncEnabled(
  profileId: string,
  enabled: boolean,
  csrfToken: string,
): Promise<ProfileDetail> {
  return apiRequest<ProfileDetail>(
    `/api/profiles/${pathSegment(profileId)}/sync`,
    {
      method: "PATCH",
      body: { enabled },
      csrfToken,
    },
  );
}
```

Do not change the existing `syncProfile()` POST helper.

- [ ] **Step 2: Add Stop Sync and Resume Sync to the Profile page**

Import `setProfileSyncEnabled`. Add:

```typescript
const [syncSetting, setSyncSetting] = useState(false);
const [stopSyncOpen, setStopSyncOpen] = useState(false);
```

Create one operation:

```typescript
async function updateSyncSetting(enabled: boolean) {
  if (!profileId) {
    return;
  }
  setSyncSetting(true);
  setActionError(null);
  try {
    await setProfileSyncEnabled(profileId, enabled, session.csrf_token);
    setStopSyncOpen(false);
    await reload();
  } catch (cause) {
    setActionError(
      cause instanceof ApiError
        ? cause.message
        : "The profile synchronization setting could not be changed.",
    );
  } finally {
    setSyncSetting(false);
  }
}
```

For active profiles, render `Sync active` and a `Stop sync` button that opens a
confirmation dialog. For stopped profiles, render `Sync stopped` and a
`Resume sync` button that directly calls `updateSyncSetting(true)`.

Disable Sync Now when `!profile.tracked`, `syncing`, or `syncSetting`. Disable
Stop/Resume while the setting request is active.

Add the confirmation:

```tsx
<ConfirmDialog
  confirmLabel="Stop sync"
  description={`Stop future downloads for @${profile.username}. If a post or reel is currently downloading, it will finish safely before synchronization stops.`}
  open={stopSyncOpen}
  title="Stop profile synchronization?"
  onClose={() => setStopSyncOpen(false)}
  onConfirm={() => updateSyncSetting(false)}
/>
```

Keep the existing Delete Profile confirmation independent.

- [ ] **Step 3: Update the Profiles list badge**

Replace the tracked display with:

```tsx
<span
  className={
    profile.tracked
      ? "status-badge status-badge-sync-active"
      : "status-badge status-badge-sync-stopped"
  }
>
  {profile.tracked ? "Sync active" : "Sync stopped"}
</span>
```

Deletion lifecycle presentation on the Profile detail page remains separate
from this synchronization badge.

- [ ] **Step 4: Stabilize Activity at ten-second polling**

Remove `useEffect`, `useState`, `isActive()`, and `jobsContainActive()` from
`ActivityPage.tsx`. Keep `loadJobs` memoized and call:

```typescript
const { data: jobs, error, loading, reload } = usePolling(
  loadJobs,
  10_000,
  true,
);
```

Keep the existing Refresh button wired to `reload()`. The polling hook already:

- queues an immediate run when idle;
- prevents a second in-flight request;
- clears the existing timeout;
- schedules the next interval after the request settles;
- preserves non-null data during later runs;
- aborts and clears timers when the route unmounts.

Change the explanatory copy to:

```tsx
<p className="page-intro">
  Activity refreshes every ten seconds, or immediately when you choose Refresh.
</p>
```

- [ ] **Step 5: Add synchronization badge styling**

Extend the existing badge selectors without introducing a new color system:

```css
.status-badge-sync-active {
  color: #067647;
  background: #ecfdf3;
}

.status-badge-sync-stopped {
  color: #6941c6;
  background: #f4f3ff;
}
```

Keep existing mobile breakpoints and button layout behavior.

- [ ] **Step 6: Build the frontend**

Run:

```powershell
npm run build
git diff --check
```

Run `npm run build` from `frontend`. Expected: TypeScript and Vite exit `0`.
Do not run frontend tests.

- [ ] **Step 7: Review and commit the UI slice**

Confirm stopped state disables Sync Now, Stop uses confirmation, Resume is
immediate, and Activity uses exactly `10_000`. Commit:

```powershell
git add frontend/src/library/api.ts frontend/src/library/ProfilePage.tsx frontend/src/library/ProfilesPage.tsx frontend/src/library/ActivityPage.tsx frontend/src/styles/global.css
git commit -m "feat: add profile sync controls"
```

---

### Task 4: Document and Package the Completed Flow

**Files:**
- Modify: `README.md`
- Verify: `compose.yaml`
- Verify: `docker/Dockerfile`

**Interfaces:**
- Documents: Stop Sync, Resume Sync, safe current-media completion, duplicate skipping, ten-second Activity polling, and immediate Refresh
- Preserves: one Docker image for web and worker with shared `/data`

- [ ] **Step 1: Update operator-facing behavior**

In README's usage section, state:

- Stop Sync excludes a profile from scheduled sync, Sync All, and Sync Now.
- A running profile sync finishes the current post or reel before stopping.
- Resume Sync re-enables future scheduling but does not immediately queue work.
- Profiles first discovered through a single-media URL are stopped by default.
- Re-adding a complete shortcode skips its download; missing files trigger
  repair.
- Activity refreshes every ten seconds and its Refresh button polls
  immediately.

Do not claim live Instagram acceptance.

- [ ] **Step 2: Run the complete allowed verification set**

Run:

```powershell
python -m compileall -q backend/src/instaloader_webui
```

From `frontend`:

```powershell
npm run build
```

From the project root:

```powershell
docker compose config --quiet
docker compose build
git diff --check
git status --short --branch
```

Expected: compilation, build, Compose, Docker build, and diff check exit `0`.
The status may show only the intended README modification before its commit.
Do not run tests or make a live Instagram request.

- [ ] **Step 3: Review security and data-integrity boundaries**

Confirm:

- no Cookie value, response body, absolute media path, or traceback was added to
  a response or log;
- Stop Sync is authenticated and CSRF protected;
- no check can interrupt media finalization or persistence;
- duplicate skipping requires every recorded asset to exist;
- no database migration was introduced.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md
git commit -m "docs: explain profile sync controls"
```

- [ ] **Step 5: Record manual acceptance steps without executing them**

Handoff these exact steps to the administrator:

1. Rebuild and recreate the Compose services.
2. Open Activity and confirm persisted jobs replace Loading activity.
3. Confirm automatic refresh occurs after ten seconds and Refresh updates
   immediately.
4. Start a profile sync, choose Stop Sync during one media download, and confirm
   the current media finishes but no next item starts.
5. Confirm Sync Now and Sync All ignore the stopped profile.
6. Resume the profile, then explicitly choose Sync Now and confirm work queues.
7. Submit a new single-media URL and confirm its newly created owner profile
   shows Sync stopped.
8. Submit the same URL again and confirm Activity reports a successful duplicate
   skip without rewriting its saved files.
