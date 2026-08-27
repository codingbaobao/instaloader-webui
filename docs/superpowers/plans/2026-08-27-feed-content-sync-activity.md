# Feed Content Sync and Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace time-sliced Post/Reel synchronization with complete resumable Feed-content synchronization, expose detailed Activity targets and Stories/Feed progress, reduce redundant Instagram queries, and migrate the live NAS database safely.

**Architecture:** Upgrade the exact version-1 SQLite schema to version 2 with SQL-backed source checkpoints, per-job segment progress, and durable Activity targets. Keep Stories separate, merge lightweight Posts/Reels manifests into one shortcode-deduplicated Feed stream, and resolve full Reel metadata only for missing items. Run one-way migration and controlled NAS deployment only after all local verification passes.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, SQLite WAL, Instaloader 4.15.3, Pytest, React 18, TypeScript 5.8, Vitest, Testing Library, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-27-feed-content-sync-activity-design.md`

## Global Constraints

- Primary synchronization concepts are exactly `stories` and `feed`.
- Read both Posts and Reels manifests; deduplicate merged Feed entries by shortcode.
- Do not call `Post.from_shortcode()` for a complete existing Feed item.
- Do not impose a time limit or item-count limit on profile sync.
- Preserve and resume source cursors after stop, blocking failure, or worker interruption.
- Keep existing media `post`/`reel` values only as optional source metadata.
- Migration is one-way from exact `pre-1.0-fresh-schema-1` to `pre-1.0-feed-sync-2`.
- Never migrate the live NAS database without a verified SQLite backup and the new image ready.
- Never expose Cookie values, request headers, session tokens, signed URL query strings, or raw upstream errors.
- Every production behavior change follows RED → GREEN → REFACTOR.

---

### Task 1: Version-2 ORM Models and Transactional Migration

**Files:**
- Modify: `backend/src/instaloader_webui/db/models.py`
- Modify: `backend/src/instaloader_webui/db/schema.py`
- Test: `backend/tests/integration/test_schema_bootstrap.py`

**Interfaces:**
- Produces: ORM models `JobProgressSegment` and `ProfileSyncCheckpoint`.
- Produces: nullable `Job.target_label` and `Job.target_url` columns.
- Produces: `CURRENT_SCHEMA_VERSION = "pre-1.0-feed-sync-2"` and one-way initialization from version 1.
- Preserves: all existing version-1 rows and exact fresh-schema validation.

- [ ] **Step 1: Add a version-1 migration fixture test**

Create a database using a test-only copy of the version-1 DDL, seed one tracked profile, one profile-sync job, one single-media job, media/assets/issues/settings, and set marker `pre-1.0-fresh-schema-1`. Call `initialize_database()` and assert:

```python
assert marker == "pre-1.0-feed-sync-2"
assert job_columns >= {"target_label", "target_url"}
assert profile_job.target_label == "@mihi_727"
assert single_job.target_url == "https://www.instagram.com/p/DcdTMB3iXSB/"
assert checkpoint_rows == [
    (profile_id, "posts", 1, None, False),
    (profile_id, "reels", 1, None, False),
]
assert preserved_media_count == 1
```

- [ ] **Step 2: Run the migration test and verify RED**

Run:

```bash
.venv/bin/pytest backend/tests/integration/test_schema_bootstrap.py::test_exact_version_one_database_migrates_to_feed_sync_version_two -v
```

Expected: FAIL because version-2 columns/tables and migration do not exist.

- [ ] **Step 3: Add the version-2 ORM schema**

Add job columns and focused models with exact constraints:

```python
class JobProgressSegment(Base):
    __tablename__ = "job_progress_segments"
    __table_args__ = (
        CheckConstraint("segment IN ('stories', 'feed')", name="ck_job_progress_segment"),
        CheckConstraint("state IN ('pending', 'running', 'completed', 'failed')", name="ck_job_progress_state"),
        CheckConstraint("scanned >= 0 AND saved >= 0 AND existing >= 0 AND warnings >= 0", name="ck_job_progress_counts"),
        CheckConstraint("total IS NULL OR total >= 0", name="ck_job_progress_total"),
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    segment: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    scanned: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int | None] = mapped_column(Integer)
    saved: Mapped[int] = mapped_column(Integer, nullable=False)
    existing: Mapped[int] = mapped_column(Integer, nullable=False)
    warnings: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Add `ProfileSyncCheckpoint` with `(profile_id, source)` primary key, `cursor_version`, nullable `cursor_json`, `backfill_complete`, and `updated_at`.

- [ ] **Step 4: Implement exact one-way migration**

In `schema.py`:

- retain `_schema_lock()` and `BEGIN IMMEDIATE`;
- compute a deterministic SHA-256 digest of the normalized schema signature;
- accept legacy digest `5edfc7cdd9d45fe1dea7ad4507417d5b1ea599793c49000cea26f90bab82e3b7` only with the exact version-1 marker;
- execute `ALTER TABLE jobs ADD COLUMN target_label TEXT` and `target_url TEXT`;
- create the two new tables from ORM table objects;
- backfill targets using `json_extract(payload_text, '$.profile_id')` and `json_extract(payload_text, '$.original_url')`;
- insert `posts` and `reels` checkpoints for active tracked profiles;
- update the marker last;
- validate the complete version-2 signature before returning.

- [ ] **Step 5: Add fail-closed and rollback tests**

Add tests where the legacy marker has an altered table and where a migration statement fails after `ALTER TABLE`. Assert `SchemaCompatibilityError` and no version-2 marker. For the injected failure, assert the original version-1 schema and seeded rows remain intact.

- [ ] **Step 6: Run schema integration tests and verify GREEN**

Run:

```bash
.venv/bin/pytest backend/tests/integration/test_schema_bootstrap.py backend/tests/integration/test_database.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add backend/src/instaloader_webui/db/models.py backend/src/instaloader_webui/db/schema.py backend/tests/integration/test_schema_bootstrap.py
git commit -m "feat(db): migrate feed sync state to schema v2"
```

---

### Task 2: Activity Targets and Segment Progress Repository

**Files:**
- Modify: `backend/src/instaloader_webui/db/library_repositories.py`
- Modify: `backend/src/instaloader_webui/services/library_service.py`
- Test: `backend/tests/unit/test_job_issue_repository.py`
- Test: `backend/tests/unit/test_library_service_media_inputs.py`

**Interfaces:**
- Produces: `JobProgressSegmentSnapshot` and target fields on `JobSnapshot`.
- Produces: `JobRepository.initialize_profile_sync_progress(job_id, now)`.
- Produces: `JobRepository.update_segment_progress()` and `JobRepository.fail_active_segment()`.
- Changes: job enqueue methods accept `target_label` and `target_url` without placing presentation data in typed payloads.

- [ ] **Step 1: Write failing repository tests for targets and ordered segments**

Assert a profile-sync job snapshots `@mihi_727`, initializes ordered Stories/Feed rows, updates only the selected segment, and cascades segment deletion with the job. Assert a single-media job preserves its canonical URL independently from payload decoding.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
.venv/bin/pytest backend/tests/unit/test_job_issue_repository.py backend/tests/unit/test_library_service_media_inputs.py -v
```

Expected: FAIL on missing target fields and segment APIs.

- [ ] **Step 3: Extend immutable job snapshots**

Add:

```python
@dataclass(frozen=True, slots=True)
class JobProgressSegmentSnapshot:
    segment: Literal["stories", "feed"]
    state: Literal["pending", "running", "completed", "failed"]
    scanned: int
    total: int | None
    saved: int
    existing: int
    warnings: int
    updated_at: datetime
```

Extend `JobSnapshot` with `target_label`, `target_url`, and `progress_segments`. Make repository `list()` and `get()` bulk-load segment rows without one query per job.

- [ ] **Step 4: Implement segment update methods**

Use one upsert/update transaction per callback:

```python
def update_segment_progress(
    self,
    *,
    job_id: str,
    segment: Literal["stories", "feed"],
    state: Literal["pending", "running", "completed", "failed"],
    scanned: int,
    total: int | None,
    saved: int,
    existing: int,
    warnings: int,
    status_text: str,
    now: datetime,
) -> None:
    with self._session_factory.begin() as session:
        segment_row = session.get(JobProgressSegment, (job_id, segment))
        if segment_row is None:
            raise ValueError("Profile sync progress segment is missing.")
        segment_row.state = state
        segment_row.scanned = scanned
        segment_row.total = total
        segment_row.saved = saved
        segment_row.existing = existing
        segment_row.warnings = warnings
        segment_row.updated_at = _as_utc(now)
        session.execute(
            update(Job)
            .where(Job.id == job_id, Job.state == "running")
            .values(status_text=status_text, updated_at=_as_utc(now))
        )
```

Keep legacy `progress_current`, `progress_total`, and `phase` updated for non-profile jobs and compatibility with existing card behavior.

- [ ] **Step 5: Snapshot targets at enqueue boundaries**

- Profile jobs use `target_label=f"@{profile.username}"`.
- Single-media jobs use the parser's canonical `original_url` for both label and URL.
- Coalescing remains keyed by `profile_id`, not the display label, so username changes cannot create duplicate active sync jobs.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run the Task 2 tests and `backend/tests/unit/test_job_runner_media.py`; expect PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add backend/src/instaloader_webui/db/library_repositories.py backend/src/instaloader_webui/services/library_service.py backend/tests/unit/test_job_issue_repository.py backend/tests/unit/test_library_service_media_inputs.py backend/tests/unit/test_job_runner_media.py
git commit -m "feat: persist activity targets and sync segments"
```

---

### Task 3: SQL Checkpoint Codec and Repository

**Files:**
- Create: `backend/src/instaloader_webui/instagram/profile_sync_checkpoints.py`
- Modify: `backend/src/instaloader_webui/db/library_repositories.py`
- Create: `backend/tests/unit/test_profile_sync_checkpoints.py`

**Interfaces:**
- Produces: `ProfileSyncCheckpointSnapshot`.
- Produces: `ProfileSyncCheckpointRepository.get(profile_id, source)`.
- Produces: `save_frozen(profile_id, source, frozen, now)`, `mark_complete(profile_id, source, now)`, and `reset(profile_id, source, now)`.
- Produces: `encode_frozen_iterator()` and `decode_frozen_iterator()` with a 2 MiB upper bound and cursor version 1.

- [ ] **Step 1: Write codec RED tests**

Round-trip a real `FrozenNodeIterator` containing query variables, remaining edges, first node, and timezone-independent timestamp. Reject oversized JSON, unknown keys, wrong cursor version, wrong scalar types, non-mapping `remaining_data`, and non-finite timestamps.

- [ ] **Step 2: Write repository RED tests**

Assert independent Posts/Reels rows, atomic replacement, completion clearing `cursor_json`, reset returning to incomplete/no-cursor, and profile deletion cascade.

- [ ] **Step 3: Run tests and verify RED**

```bash
.venv/bin/pytest backend/tests/unit/test_profile_sync_checkpoints.py -v
```

Expected: import or attribute failures for the missing codec/repository.

- [ ] **Step 4: Implement strict bounded codec**

Encode a top-level document with exact keys:

```python
{
    "version": 1,
    "query_hash": frozen.query_hash,
    "query_variables": frozen.query_variables,
    "query_referer": frozen.query_referer,
    "context_username": frozen.context_username,
    "total_index": frozen.total_index,
    "best_before": frozen.best_before,
    "remaining_data": frozen.remaining_data,
    "first_node": frozen.first_node,
    "doc_id": frozen.doc_id,
}
```

Serialize with compact sorted JSON, reject payloads over `2 * 1024 * 1024` bytes, and never log rejected payload content.

- [ ] **Step 5: Implement checkpoint repository**

Put checkpoint persistence in a focused repository that maps SQL rows to immutable snapshots and translates codec failures into `ProfileSyncCheckpointError` with a fixed safe message.

- [ ] **Step 6: Run tests and verify GREEN**

Run checkpoint tests plus database integration tests; expect PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add backend/src/instaloader_webui/instagram/profile_sync_checkpoints.py backend/src/instaloader_webui/db/library_repositories.py backend/tests/unit/test_profile_sync_checkpoints.py
git commit -m "feat: persist profile feed iterator checkpoints"
```

---

### Task 4: Lightweight Posts/Reels Manifest Sources

**Files:**
- Create: `backend/src/instaloader_webui/instagram/feed_manifest.py`
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Modify: `backend/src/instaloader_webui/instagram/media_types.py`
- Test: `backend/tests/unit/test_profile_sync_coordinator.py`

**Interfaces:**
- Produces: `FeedManifestEntry(shortcode, published_at_hint, source, resolve)`.
- Produces: resumable `FeedManifestIterator` protocol with `__next__()`, `freeze()`, `thaw()`, and nullable `count`.
- Produces: Posts and Reels manifest factories bound to one resolved profile and loader.
- Preserves: `MediaCandidate.kind` as optional source metadata for existing persistence paths.

- [ ] **Step 1: Write query-count RED tests**

Build a fake Reels manifest page with two shortcodes already complete locally and one missing shortcode. Assert:

```python
assert shortcode_lookup_calls == ["missing-code"]
assert [candidate.identity.value for candidate in candidates] == [
    "existing-one", "existing-two", "missing-code"
]
```

Also assert a shortcode present in both Posts and Reels manifests is yielded once by the merged Feed coordinator.

- [ ] **Step 2: Run focused tests and verify RED**

Run the new tests; expected failure is that `Profile.get_reels()` calls `Post.from_shortcode()` for every Reel.

- [ ] **Step 3: Implement application-owned manifest entries**

Wrap Posts timeline items without a second request. Recreate Instaloader 4.15.3's Reels `NodeIterator` query parameters, but set `node_wrapper` to return raw nodes. Extract and validate:

- `media.code` as shortcode;
- `media.taken_at` as an optional UTC publication hint.

The Reel candidate closure calls `Post.from_shortcode()` only when `MediaProcessor` invokes `resolve()` after the local complete-item check.

- [ ] **Step 4: Implement freeze/thaw adapters**

Expose only the small application protocol. Hide Instaloader query hashes/doc IDs and raw node shapes inside `feed_manifest.py`. Raise a fixed `FeedManifestError` on schema drift without retaining raw response data.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run coordinator, media processor, and Story adapter tests; expect PASS and exact query counts.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/src/instaloader_webui/instagram/feed_manifest.py backend/src/instaloader_webui/instagram/public_adapter.py backend/src/instaloader_webui/instagram/media_types.py backend/tests/unit/test_profile_sync_coordinator.py
git commit -m "feat: scan lightweight feed manifests"
```

---

### Task 5: Unlimited Resumable Stories/Feed Coordinator

**Files:**
- Modify: `backend/src/instaloader_webui/instagram/profile_sync.py`
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Modify: `backend/src/instaloader_webui/services/job_runner.py`
- Modify: `backend/src/instaloader_webui/worker.py`
- Test: `backend/tests/unit/test_profile_sync_coordinator.py`
- Test: `backend/tests/unit/test_job_runner_media.py`
- Test: `backend/tests/unit/test_worker_composition.py`

**Interfaces:**
- Replaces: time-slice callbacks with segment progress and checkpoint callbacks.
- Produces: `ProfileSyncResult` with Stories/Feed counters and no `backfill_pending`.
- Consumes: independent Posts/Reels checkpoints and manifest iterators.

- [ ] **Step 1: Write RED tests for no cap and two segments**

Delete expectations for `_PROFILE_SYNC_TIME_SLICE_SECONDS`. Feed 500 existing candidates and 30 saved candidates through the coordinator and assert all 530 are scanned in one run. Assert progress callbacks transition Stories then Feed through `pending → running → completed` with correct counters.

- [ ] **Step 2: Write RED tests for recent boundary and resume**

Cover these exact cases:

- complete source: two new candidates followed by first existing candidate stops that source;
- incomplete source: existing candidates do not stop historical scan;
- stored cursor: newest recent candidate is saved, then historical cursor resumes;
- process interruption: both iterators freeze and the next run repeats at most a page without skipping identity;
- Posts/Reels duplicate shortcode counts once in Feed statistics;
- blocking media failure persists checkpoints before escaping.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
.venv/bin/pytest backend/tests/unit/test_profile_sync_coordinator.py backend/tests/unit/test_job_runner_media.py backend/tests/unit/test_worker_composition.py -v
```

Expected: failures from time-slice behavior and absent checkpoint/segment APIs.

- [ ] **Step 4: Refactor coordinator around Stories and Feed**

Use immutable counters:

```python
@dataclass(frozen=True, slots=True)
class SegmentCounts:
    scanned: int = 0
    saved: int = 0
    existing: int = 0
    warnings: int = 0
```

The coordinator reports one `stories` segment and one deduplicated `feed` segment. It never sleeps merely for existing candidates. Preserve the current 1–3 second pause only between newly saved Feed items when another network-resolved item is about to run.

- [ ] **Step 5: Wire checkpoint lifecycle**

The adapter loads checkpoints after profile metadata refresh, creates fresh recent iterators, thaws historical iterators when required, saves frozen cursors at page boundaries and failure/stop boundaries, and marks each source complete only at source exhaustion.

- [ ] **Step 6: Wire JobRunner segment persistence**

Initialize both rows when a profile job starts. Segment callbacks update `job_progress_segments`; failure marks the active segment failed. Remove `backfill_pending` completion copy. Keep global cooldown behavior for propagated blocking outcomes.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run all Task 5 tests; expect PASS.

- [ ] **Step 8: Commit Task 5**

```bash
git add backend/src/instaloader_webui/instagram/profile_sync.py backend/src/instaloader_webui/instagram/public_adapter.py backend/src/instaloader_webui/services/job_runner.py backend/src/instaloader_webui/worker.py backend/tests/unit/test_profile_sync_coordinator.py backend/tests/unit/test_job_runner_media.py backend/tests/unit/test_worker_composition.py
git commit -m "fix: resume complete profile feed syncs"
```

---

### Task 6: Native Lookup Circuit and Avatar Reuse

**Files:**
- Modify: `backend/src/instaloader_webui/instagram/profile_lookup.py`
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Test: `backend/tests/unit/test_profile_lookup.py`
- Test: `backend/tests/unit/test_profile_sync_coordinator.py`

**Interfaces:**
- Changes: `ProfileLookupResolver` accepts injected monotonic time and a 1,800-second native circuit duration.
- Changes: profile sync skips avatar HTTP when stored URL is unchanged and a valid local JPEG/WebP avatar exists.

- [ ] **Step 1: Write native-circuit RED tests**

At time 0, make native return typed 429 and legacy succeed. At time 60, assert the second resolve calls only legacy. At time 1,801, assert native is probed again. Assert native success closes the circuit and non-429 failures never open it.

- [ ] **Step 2: Write avatar-cache RED tests**

Given unchanged `profile_pic_url` and a valid stored avatar, assert `loader.context.get_raw` is not called. Given a changed URL or missing/invalid local avatar, assert the validated fetch/replace path still runs.

- [ ] **Step 3: Run tests and verify RED**

Run the two focused modules; expect repeated native calls and avatar network access.

- [ ] **Step 4: Implement the circuit and avatar reuse**

Keep the circuit process-local on the shared resolver. Store only `native_blocked_until: float`; do not cache Profile objects or upstream payloads. Reuse `stored_profile_avatar()` for avatar validation and compare the normalized stored URL exactly before skipping.

- [ ] **Step 5: Run tests and verify GREEN**

Run profile lookup, lookup boundary, profile sync, and avatar tests; expect PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add backend/src/instaloader_webui/instagram/profile_lookup.py backend/src/instaloader_webui/instagram/public_adapter.py backend/tests/unit/test_profile_lookup.py backend/tests/unit/test_profile_sync_coordinator.py
git commit -m "fix: avoid repeated profile metadata requests"
```

---

### Task 7: Activity API Contracts

**Files:**
- Modify: `backend/src/instaloader_webui/api/library_dtos.py`
- Modify: `backend/src/instaloader_webui/api/routes/jobs.py`
- Test: `backend/tests/integration/test_job_issues_api.py`

**Interfaces:**
- Produces: `JobProgressSegmentResponse`.
- Adds: `target_label`, `target_url`, and ordered `progress_segments` to `JobResponse`.
- Preserves: list endpoint omits issue detail; detail endpoint includes safe issues.

- [ ] **Step 1: Write API RED tests**

Seed a profile job with both segments and a single-media job with canonical URL. Assert list/detail JSON contains:

```json
{
  "target_label": "@mihi_727",
  "target_url": null,
  "progress_segments": [
    {"segment": "stories", "state": "completed"},
    {"segment": "feed", "state": "running"}
  ]
}
```

Assert URLs are canonical and no Cookie/query secret appears.

- [ ] **Step 2: Run integration test and verify RED**

Run `test_job_issues_api.py`; expected response-field assertion failures.

- [ ] **Step 3: Implement frozen DTO serialization**

Map immutable snapshots directly. Keep segment ordering in the repository/serializer contract, never by client-side sorting. Do not infer targets by issuing Instagram requests.

- [ ] **Step 4: Run integration tests and verify GREEN**

Run job API, profile API, media API, and auth API integration modules; expect PASS.

- [ ] **Step 5: Commit Task 7**

```bash
git add backend/src/instaloader_webui/api/library_dtos.py backend/src/instaloader_webui/api/routes/jobs.py backend/tests/integration/test_job_issues_api.py
git commit -m "feat(api): expose detailed activity progress"
```

---

### Task 8: Activity UI with Concrete Targets and Two Progress Rows

**Files:**
- Modify: `frontend/src/library/types.ts`
- Modify: `frontend/src/library/ActivityPage.tsx`
- Modify: `frontend/src/library/ActivityPage.test.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Consumes: target fields and ordered Stories/Feed segments from Task 7.
- Produces: local profile target links, safe external single-media links, and segment progress rows.

- [ ] **Step 1: Write UI RED tests**

Add fixtures and assert:

- profile card heading includes `profile sync` and `@mihi_727`;
- profile target links to `/profiles/profile-1` using payload `profile_id`;
- single media URL renders as an `<a>` with `target="_blank"` and `rel="noreferrer"`;
- exactly `Stories` and `Feed content` progress rows render in server order;
- running unknown-total segment renders `<progress>` without `value`;
- known totals use determinate percentage;
- each row shows scanned/saved/existing/warnings;
- failed and warning issue behavior remains unchanged.

- [ ] **Step 2: Run Activity tests and verify RED**

```bash
cd frontend && npm test -- src/library/ActivityPage.test.tsx
```

Expected: missing target and segment UI assertions fail.

- [ ] **Step 3: Extend TypeScript contracts**

Add exact unions:

```typescript
type JobProgressSegment = Readonly<{
  segment: "stories" | "feed";
  label: string;
  state: "pending" | "running" | "completed" | "failed";
  scanned: number;
  total: number | null;
  saved: number;
  existing: number;
  warnings: number;
  updated_at: string;
}>;
```

Add nullable target fields and `readonly JobProgressSegment[]` to `JobSummary`.

- [ ] **Step 4: Implement accessible target and segment components**

Extract small pure helpers/components inside `ActivityPage.tsx`:

- `jobTitle(job)`;
- `JobTarget`;
- `SegmentProgress`.

Use visible labels and unique `aria-label` values containing the target and segment. Do not render raw payload JSON.

- [ ] **Step 5: Style two compact rows responsively**

Add Activity-scoped classes for segment header, counters, and progress. Preserve mobile one-column cards and existing status colors.

- [ ] **Step 6: Run frontend tests and verify GREEN**

Run Activity tests, then all frontend tests; expect PASS.

- [ ] **Step 7: Commit Task 8**

```bash
git add frontend/src/library/types.ts frontend/src/library/ActivityPage.tsx frontend/src/library/ActivityPage.test.tsx frontend/src/styles/global.css
git commit -m "feat(ui): detail activity targets and feed progress"
```

---

### Task 9: Full Verification, Review, and Controlled NAS Migration

**Files:**
- Modify if needed: `deploy-nas.md`
- Test: full repository and live migration checks.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified local image, reviewed diff, timestamped NAS backup, version-2 NAS database, and one controlled `mihi_727` validation sync.

- [ ] **Step 1: Run backend quality gates**

```bash
.venv/bin/pytest
.venv/bin/ruff check backend/src backend/tests tests
.venv/bin/mypy backend/src
```

Expected: all PASS with coverage at least 80%.

- [ ] **Step 2: Run frontend quality gates**

```bash
cd frontend
npm test
npm run lint
npm run build
```

Expected: all PASS.

- [ ] **Step 3: Run Compose/static verification**

```bash
.venv/bin/pytest tests/container/test_compose_smoke.py -v
git diff --check
git status --short
```

Run Docker-dependent smoke tests only when Docker is available; a missing Docker daemon must be reported rather than hidden.

- [ ] **Step 4: Review requirements and query counts**

Inspect the branch diff against the spec. Confirm tests prove no five-minute cap, no existing-Reel shortcode lookup, two Activity segments, target details, and resumable cursors. Use the `superpowers:requesting-code-review` skill and resolve every correctness finding before deployment.

- [ ] **Step 5: Build the deployable image before touching NAS**

Build `instaloader-webui:feed-sync-v2` locally with `compose.build.yaml`, run health smoke tests against a temporary data root, then transfer/load that exact image on the NAS or publish an immutable registry tag. Record its image ID.

- [ ] **Step 6: Dry-run migration on a NAS database copy**

Use the SQLite backup API inside the current trusted web image to create a timestamped copy. Mount the copy into the new image, run initialization, and verify:

```sql
PRAGMA integrity_check;
SELECT version FROM schema_marker WHERE id='global';
SELECT COUNT(*) FROM job_progress_segments;
SELECT source, COUNT(*) FROM profile_sync_checkpoints GROUP BY source;
```

Expected marker: `pre-1.0-feed-sync-2`; integrity: `ok`.

- [ ] **Step 7: Migrate live NAS safely**

Stop both services. Create and read-verify a fresh backup using SQLite's backup API. Start only the new web service to migrate, verify health/schema/integrity, then start the new worker. Never start the old image against version 2.

- [ ] **Step 8: Validate one controlled profile sync**

Trigger only `mihi_727`. Verify Activity shows `@mihi_727`, Stories and Feed content rows, Feed scan advances beyond the old 28–31-item repeated prefix, existing Reels do not emit per-shortcode metadata queries, and the job reaches completion or a safely persisted blocking outcome.

- [ ] **Step 9: Update runbook and commit final deployment notes**

Document the version-2 backup, migration, integrity, and rollback commands in `deploy-nas.md` without embedding credentials or host secrets beyond the existing checked-in NAS alias/path.

```bash
git add deploy-nas.md
git commit -m "docs: add schema v2 NAS migration runbook"
```
