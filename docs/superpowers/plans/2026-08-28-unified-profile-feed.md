# Unified Profile Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove persisted Post/Reel classification and present all shortcode media as one Feed collection while keeping Stories separate.

**Architecture:** Derive each stored item's `feed|story` collection from `identity_type`, expose that computed value through repository and API boundaries, and transactionally rebuild SQLite `media_items` without `kind`. Retain `post|reel|story` only where the worker needs an operational download input or issue diagnostic. Update profile browsing and immersive navigation to use collection.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, SQLite, pytest, React 18, TypeScript 5.8, React Router 6, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-28-unified-profile-feed-design.md`

## Global Constraints

- Posts and Reels are one `feed` collection; Stories remain `story`.
- `media_items` has no `kind` column; `shortcode` identity means Feed and `story_media_id` means Story.
- Candidate, resolved-input, single-media-job, and job-issue operational kinds remain `post|reel|story`.
- Existing media, assets, jobs, issues, checkpoints, and profile metadata survive migration.
- API browsing uses `collection=feed|story` with no `kind` alias.
- Feed is the default profile tab and original Instagram URLs stay unchanged.
- Every production behavior change follows a witnessed red-green cycle.

---

### Task 1: Version-3 SQLite Schema and Migration

**Files:**
- Modify: `backend/src/instaloader_webui/db/models.py`
- Modify: `backend/src/instaloader_webui/db/schema.py`
- Modify: `backend/tests/integration/test_schema_bootstrap.py`

**Interfaces:**
- Produces: `CURRENT_SCHEMA_VERSION = "pre-1.0-unified-feed-3"`.
- Produces: exact version-2 validation and `_migrate_version_two_schema()`.
- Preserves: version-1 migration and every `media_assets` foreign key.

- [ ] **Step 1: Write failing version-2-to-version-3 migration tests**

Add an exact version-2 fixture helper with a fixed normalized schema digest.
Seed former Post, Reel, and Story rows with one asset each, run initialization,
and assert:

```python
assert marker == "pre-1.0-unified-feed-3"
assert "kind" not in media_item_columns
assert identities == [
    ("shortcode", "POST1"),
    ("shortcode", "REEL1"),
    ("story_media_id", "STORY1"),
]
assert asset_count == 3
assert foreign_key_errors == []
```

Update the exact version-1 test to expect version 3 in one call. Add tests that
reject a drifted version-2 schema and roll back after an injected rebuild
failure, preserving marker, digest, and row counts.

- [ ] **Step 2: Run schema tests and verify RED**

```bash
cd backend
python -m pytest tests/integration/test_schema_bootstrap.py --no-cov -v
```

Expected: FAIL because current schema is version 2 and still has `kind`.

- [ ] **Step 3: Implement the schema and chained migration**

Remove `ck_media_items_kind` and the mapped `MediaItem.kind`. Keep distinct
constants for version 1, version 2, and current. Validate each old marker against
its fixed digest. Version 1 migrates to version 2, then continues to version 3.

On one dedicated connection, set `PRAGMA foreign_keys = OFF` before
`BEGIN IMMEDIATE`; create `media_items_v3` with every existing column except
`kind`; copy all remaining columns; drop old table; rename the new table; and
recreate `ix_media_items_owner_profile_published_at`. Use the exact constraints:

```sql
CONSTRAINT uq_media_items_identity UNIQUE (identity_type, identity_value),
CONSTRAINT ck_media_items_identity_type
  CHECK (identity_type IN ('shortcode', 'story_media_id')),
UNIQUE (instagram_media_id),
FOREIGN KEY(owner_profile_id) REFERENCES profiles (id) ON DELETE CASCADE
```

Require an empty `PRAGMA foreign_key_check`, update the marker, and verify the
exact ORM signature before commit. Roll back on error and restore foreign keys
in `finally`.

- [ ] **Step 4: Run schema tests and verify GREEN**

Run the focused command again. Expected: all bootstrap, drift, rollback,
concurrency, and foreign-key cases PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/instaloader_webui/db/models.py \
  backend/src/instaloader_webui/db/schema.py \
  backend/tests/integration/test_schema_bootstrap.py
git commit -m "feat: migrate media library to unified feed schema"
```

---

### Task 2: Collection-Based Repository Domain

**Files:**
- Modify: `backend/src/instaloader_webui/db/library_repositories.py`
- Modify: `backend/tests/unit/test_library_repositories_media.py`
- Modify fixtures in: `backend/tests/integration/test_library_media_api.py`
- Modify fixtures in: `backend/tests/unit/test_media_processor.py`
- Modify fixtures in: `backend/tests/unit/test_job_runner_media.py`
- Modify fixtures in: `backend/tests/unit/test_profile_sync_coordinator.py`

**Interfaces:**
- Produces: `MediaCollection = Literal["feed", "story"]` and `MediaSnapshot.collection`.
- Produces: collection filters for list, count, and viewer windows.
- Removes: snapshot/normalized `kind` and `set_media_kind()`.

- [ ] **Step 1: Write failing repository tests**

```python
feed = library.list_media(profile_id=profile.id, collection="feed")
stories = library.list_media(profile_id=profile.id, collection="story")
assert {item.identity_value for item in feed} == {"POST1", "REEL1"}
assert all(item.collection == "feed" for item in feed)
assert [item.identity_value for item in stories] == ["STORY1"]
assert library.count_media(profile_id=profile.id, collection="feed") == 2
```

Assert Story anchors requested under Feed and Feed anchors requested under Story
return `None`.

- [ ] **Step 2: Run repository tests and verify RED**

```bash
cd backend
python -m pytest tests/unit/test_library_repositories_media.py --no-cov -v
```

Expected: FAIL because methods and snapshots still use `kind`.

- [ ] **Step 3: Implement one canonical mapping**

```python
MediaCollection = Literal["feed", "story"]

def media_collection(identity_type: str) -> MediaCollection:
    if identity_type == "shortcode":
        return "feed"
    if identity_type == "story_media_id":
        return "story"
    raise ValueError("Unsupported media identity type.")
```

Use the corresponding identity equality as `_collection_filter()`. Replace
repository `kind` parameters with typed `collection`, including anchor checks.
Remove `kind` from `MediaSnapshot`, `NormalizedMedia`, inserts, updates, and
fixtures; delete `set_media_kind`. Do not remove operational candidate, resolved
media, issue, asset, or job kinds.

- [ ] **Step 4: Run affected backend tests**

```bash
cd backend
python -m pytest tests/unit/test_library_repositories_media.py \
  tests/unit/test_media_processor.py tests/unit/test_job_runner_media.py \
  tests/unit/test_profile_sync_coordinator.py --no-cov -v
```

Expected: PASS; worker kind behavior remains covered.

- [ ] **Step 5: Commit**

```bash
git add backend/src/instaloader_webui/db/library_repositories.py backend/tests
git commit -m "refactor: derive media collections from identity"
```

---

### Task 3: Media API and Cursor Contract

**Files:**
- Modify: `backend/src/instaloader_webui/api/library_dtos.py`
- Modify: `backend/src/instaloader_webui/api/routes/media.py`
- Modify: `backend/tests/integration/test_library_media_api.py`

**Interfaces:**
- Produces: media JSON `collection` with no browsing `kind`.
- Produces: `collection` query parameter and version-2 collection-bound cursors.

- [ ] **Step 1: Write failing API tests**

```python
response = await authenticated_client.get(
    "/api/media",
    params={"profile_id": profile.id, "collection": "feed"},
)
items = response.json()["data"]
assert {item["shortcode"] for item in items} == {"POST1", "REEL1"}
assert {item["collection"] for item in items} == {"feed"}
assert all("kind" not in item for item in items)
```

Assert Stories serialize as `collection=story`, `collection=reel` returns 422,
cursor JSON has `version=2` and `collection`, and replay under another
collection returns `invalid_media_feed_cursor`.

- [ ] **Step 2: Run API tests and verify RED**

```bash
cd backend
python -m pytest tests/integration/test_library_media_api.py --no-cov -v
```

Expected: FAIL on old DTO, query, and cursor fields.

- [ ] **Step 3: Implement API collection contract**

Replace DTO `kind` with `collection: Literal["feed", "story"]`. Route params are:

```python
collection: Literal["feed", "story"] | None = None
```

Cursor payload is exactly:

```python
{
    "collection": collection,
    "direction": direction,
    "id": media.id,
    "profile_id": profile_id,
    "published_at": media.published_at.isoformat(),
    "version": 2,
}
```

Decode only exact version/profile/collection matches and preserve the existing
safe cursor error response.

- [ ] **Step 4: Run API tests and verify GREEN**

Run the focused command again. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/instaloader_webui/api \
  backend/tests/integration/test_library_media_api.py
git commit -m "feat: expose unified media collections"
```

---

### Task 4: Remove Stored-Kind Reconciliation

**Files:**
- Modify: `backend/src/instaloader_webui/instagram/media_processor.py`
- Modify: `backend/tests/unit/test_media_processor.py`
- Modify: `backend/tests/unit/test_library_repositories_media.py`

**Interfaces:**
- Preserves: operational candidate/resolved kind validation.
- Removes: Post-to-Reel updates for an already complete shortcode.

- [ ] **Step 1: Write failing no-reconciliation test**

Process a Reel candidate for a complete stored shortcode and assert:

```python
result = processor.process(reel_candidate, job_id="job-1")
assert result.status == "existing"
assert result.media.collection == "feed"
assert download_call_count == 0
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
python -m pytest tests/unit/test_media_processor.py \
  tests/unit/test_library_repositories_media.py --no-cov -v
```

Expected: FAIL until old reconciliation behavior is removed.

- [ ] **Step 3: Implement the direct existing-item return**

```python
existing = self._library.find_media_by_identity(candidate.identity)
if existing is not None and self._has_complete_local_assets(existing):
    return MediaProcessResult(status="existing", media=existing)
```

Delete `_reconcile_existing_kind`; build `NormalizedMedia` without `kind`.
Keep `resolved.kind != candidate.kind` validation and safe issue kinds.

- [ ] **Step 4: Run processing and worker tests**

```bash
cd backend
python -m pytest tests/unit/test_media_processor.py \
  tests/unit/test_job_runner_media.py tests/unit/test_profile_sync_coordinator.py \
  tests/unit/test_public_adapter_stories.py \
  tests/integration/test_job_issues_api.py --no-cov -v
```

Expected: PASS, proving operational kinds still work.

- [ ] **Step 5: Commit**

```bash
git add backend/src/instaloader_webui/instagram/media_processor.py backend/tests
git commit -m "refactor: stop persisting feed source kinds"
```

---

### Task 5: Frontend Collection Types and API Client

**Files:**
- Modify: `frontend/src/library/types.ts`
- Modify: `frontend/src/library/api.ts`
- Modify: `frontend/src/library/mediaPresentation.ts`
- Modify: `frontend/src/library/JobIssues.tsx`
- Modify fixtures in: `frontend/src/library/ActivityPage.test.tsx`
- Modify fixtures in: `frontend/src/library/MediaGrid.test.tsx`
- Modify fixtures in: `frontend/src/library/MediaViewerPage.test.tsx`
- Modify fixtures in: `frontend/src/library/ProfilePage.test.tsx`

**Interfaces:**
- Produces: `MediaCollection = "feed" | "story"` for browsing.
- Produces: `OperationalMediaKind = "post" | "reel" | "story"` for issues.
- Produces: API queries using `collection` and neutral labels.

- [ ] **Step 1: Write failing fixture and presentation expectations**

Replace media-fixture `kind` with `collection`. Assert:

```typescript
expect(mediaLabel(feedFixture)).toBe("Feed media");
expect(mediaLabel(storyFixture)).toBe("Story");
```

API-observing tests require `collection=feed` and reject `kind=`.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
cd frontend
npm exec -- vitest run src/library/MediaGrid.test.tsx \
  src/library/ProfilePage.test.tsx src/library/MediaViewerPage.test.tsx
```

Expected: type/runtime failures on old media kinds.

- [ ] **Step 3: Implement separate collection and operational types**

```typescript
export type MediaCollection = "feed" | "story";
export type OperationalMediaKind = "post" | "reel" | "story";
```

Use `collection` on `MediaSummary`, `OperationalMediaKind` on `JobIssue`, and
`collection` in list/feed option query builders. Implement:

```typescript
export function mediaLabel(media: MediaSummary): "Feed media" | "Story" {
  return media.collection === "story" ? "Story" : "Feed media";
}
```

Keep JobIssues' Post/Reel/Story diagnostic switch.

- [ ] **Step 4: Re-run focused tests and build**

```bash
cd frontend
npm exec -- vitest run src/library/MediaGrid.test.tsx \
  src/library/ProfilePage.test.tsx src/library/MediaViewerPage.test.tsx
npm run build
```

Expected: focused tests pass; any build errors are limited to call sites that
Tasks 6 and 7 immediately replace.

- [ ] **Step 5: Commit when the boundary type-checks**

If Task 6 is required for a clean build, include these files in its commit.
Otherwise:

```bash
git add frontend/src/library/types.ts frontend/src/library/api.ts \
  frontend/src/library/mediaPresentation.ts frontend/src/library/JobIssues.tsx \
  frontend/src/library/ActivityPage.test.tsx
git commit -m "refactor: model frontend media collections"
```

---

### Task 6: Two-Tab Profile Feed UI

**Files:**
- Modify: `frontend/src/library/ProfilePage.tsx`
- Modify: `frontend/src/library/ProfilePage.test.tsx`
- Modify: `frontend/src/library/MediaGrid.tsx`
- Modify: `frontend/src/library/MediaGrid.test.tsx`

**Interfaces:**
- Produces: accessible Feed and Story tabs only.
- Produces: `?tab=feed|story`, unknown values defaulting to Feed.
- Produces: profile viewer links carrying collection.

- [ ] **Step 1: Write failing two-tab tests**

```tsx
const feedTab = await screen.findByRole("tab", { name: "Feed" });
const storyTab = screen.getByRole("tab", { name: "Story" });
expect(screen.queryByRole("tab", { name: "Posts" })).not.toBeInTheDocument();
expect(screen.queryByRole("tab", { name: "Reels" })).not.toBeInTheDocument();
feedTab.focus();
await user.keyboard("{ArrowRight}");
expect(storyTab).toHaveFocus();
```

Assert initial query contains `collection=feed`; `?tab=reel` also resolves to
Feed without requesting `collection=reel`.

- [ ] **Step 2: Run profile/grid tests and verify RED**

```bash
cd frontend
npm exec -- vitest run src/library/ProfilePage.test.tsx \
  src/library/MediaGrid.test.tsx
```

Expected: FAIL on the current Posts/Reels/Story UI.

- [ ] **Step 3: Implement Feed and Story tabs**

```typescript
const mediaTabs = [
  {
    collection: "feed",
    id: "feed-tab",
    label: "Feed",
    emptyTitle: "No feed media yet",
    emptyDetail: "No feed media has been saved from this profile yet.",
  },
  {
    collection: "story",
    id: "story-tab",
    label: "Story",
    emptyTitle: "No stories yet",
    emptyDetail: "No stories have been saved from this profile yet.",
  },
] as const;
```

Only `tab=story` selects Story. Load and link by collection. MediaGrid's profile
source shape is `{ type: "profile", profileId, collection }` and viewer URLs use
`collection=<feed|story>`.

- [ ] **Step 4: Re-run tests and verify GREEN**

Run the focused command again. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/library/ProfilePage.tsx \
  frontend/src/library/ProfilePage.test.tsx frontend/src/library/MediaGrid.tsx \
  frontend/src/library/MediaGrid.test.tsx frontend/src/library/types.ts \
  frontend/src/library/api.ts frontend/src/library/mediaPresentation.ts \
  frontend/src/library/JobIssues.tsx
git commit -m "feat: combine profile posts and reels into feed"
```

---

### Task 7: Collection-Aware Immersive Viewer

**Files:**
- Modify: `frontend/src/library/MediaViewerPage.tsx`
- Modify: `frontend/src/library/MediaViewerPage.test.tsx`

**Interfaces:**
- Consumes: `MediaDetail.collection` and collection-aware feed API.
- Produces: collection-bound paging and Back to profile targets.

- [ ] **Step 1: Write failing viewer tests**

Use `source=profile&profileId=profile-1&collection=feed`. Assert initial and
cursor requests include collection and no kind, and Back to profile links to
`/profiles/profile-1?tab=feed`. Add a context-free Story anchor test that infers
`story` from the detail response.

- [ ] **Step 2: Run viewer tests and verify RED**

```bash
cd frontend
npm exec -- vitest run src/library/MediaViewerPage.test.tsx
```

Expected: FAIL because viewer source and fallback still use kind.

- [ ] **Step 3: Implement collection-aware source**

```typescript
type FeedSource =
  | Readonly<{ type: "recent" }>
  | Readonly<{
      type: "profile";
      profileId: string;
      collection: MediaCollection;
    }>;
```

Parse only `feed|story`, pass collection through paging, build the return query
from `source.collection`, use `media.collection` for fallback, and link each
owner profile with `?tab=${media.collection}`.

- [ ] **Step 4: Run viewer and all frontend checks**

```bash
cd frontend
npm exec -- vitest run
npm run lint
npm run build
```

Expected: all tests PASS; lint and build exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/library/MediaViewerPage.tsx \
  frontend/src/library/MediaViewerPage.test.tsx \
  frontend/src/library/ActivityPage.test.tsx
git commit -m "feat: navigate profile viewer by media collection"
```

---

### Task 8: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify if schema wording requires it: `deploy-nas.md`

**Interfaces:**
- Produces: Feed/Story browsing documentation.
- Verifies: all migration, backend, frontend, and packaging requirements.

- [ ] **Step 1: Update documentation**

Describe profile browsing as saved Feed media and Stories. Keep accepted input
wording for post, Reel, Story, and TV URLs. Update deployment text only where it
names the schema marker or exact migration target.

- [ ] **Step 2: Search every stale contract**

```bash
rg -n "MediaItem\.kind|media\.kind|kind=post|kind=reel|kind=story|posts-tab|reels-tab" \
  backend/src frontend/src README.md
```

Expected: no persistence or browsing matches. Only inspected operational
candidate/job/issue kinds and Instagram input wording may remain.

- [ ] **Step 3: Run complete backend checks**

```bash
cd backend
python -m pytest --no-cov
python -m ruff check src tests
python -m mypy src
```

Expected: all tests PASS and static checks exit 0.

- [ ] **Step 4: Run complete frontend checks**

```bash
cd frontend
npm exec -- vitest run
npm run lint
npm run build
```

Expected: all tests PASS; lint and build exit 0.

- [ ] **Step 5: Validate packaging and diff**

```bash
docker compose --file compose.yaml --env-file .env.example config --quiet
docker compose --file compose.yaml --file compose.build.yaml \
  --env-file .env.example build web
git diff --check
git status --short
```

Expected: Compose and image build exit 0; diff check is clean; only intended
files remain.

- [ ] **Step 6: Commit remaining documentation**

```bash
git add README.md deploy-nas.md backend frontend
git commit -m "docs: describe unified profile feed"
```

Skip the commit if there are no remaining changes; never amend unrelated work.
