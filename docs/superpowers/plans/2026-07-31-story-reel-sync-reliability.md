# Story, Reel Classification, and Resilient Sync Implementation Plan

> **Status — superseded for schema work (2026-08-01):** This is a historical
> plan. Do not execute its Alembic, migration, database-upgrade/downgrade, or
> migration-startup instructions. The current pre-1.0 runtime supports only
> the exact fresh schema and does not migrate older databases.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add direct and profile-synced Instagram Stories, repair Reel classification and poster handling, and make profile sync finish with structured warnings when individual media items fail.

**Architecture:** Keep `PublicInstaloaderAdapter` as the worker-facing facade, but extract focused media-domain, processing, safe-issue, and profile-sync coordinator modules from the current 675-line adapter. All media uses one identity/asset model and one staged processor; the coordinator merely schedules Story candidates before scanning and processing Reels and Posts.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite, Instaloader 4.15.3, pytest, React 18, TypeScript 5.8, Vitest, React Testing Library, MSW, Docker Compose.

## Global Constraints

- Pin Instaloader exactly to `4.15.3`; the repository has no Python lock file, so `backend/pyproject.toml` is the authoritative runtime pin.
- Accept only HTTPS `instagram.com` and `www.instagram.com` inputs; discard query strings and fragments before persistence.
- Story sync follows the existing Profile schedule and Sync Now action; do not add another scheduler or job type.
- During Profile sync, enumerate and process Stories before scanning Reels and Posts.
- Reels win shortcode deduplication against Posts.
- Every Story/Reel/Post outcome advances progress once after the exact total is known; while the total is unknown, show phase text without numeric progress.
- Item failures record safe issues and continue; profile metadata, unusable avatar, iterator, database, and filesystem failures remain fatal.
- A profile job with item issues ends as `completed_with_warnings`; a failed direct-media item ends as `failed`.
- Poster assets are retained, linked to a logical media position, and excluded from carousel content/counts.
- Failed items leave no successful media/assets record and remain retryable.
- Do not log Cookie values, session material, response bodies, tracebacks, or URLs with query strings.
- Do not add or enforce Ruff or coverage gates for this milestone.
- Do not run a local Docker build.
- Do not push commits, publish an image, create a GitHub Release, or replace the NAS production `0.1.1` deployment.

---

## File Structure

New backend modules:

- `backend/src/instaloader_webui/instagram/media_types.py`: media identity, candidate, outcome, and processor interfaces.
- `backend/src/instaloader_webui/instagram/media_processor.py`: common staged download, role mapping, asset finalization, and persistence.
- `backend/src/instaloader_webui/instagram/profile_sync.py`: Story-first manifests, Reel/Post deduplication, progress, stop checks, and warning continuation.
- `backend/src/instaloader_webui/instagram/safe_issues.py`: safe issue classification, exception class chains, redaction, and structured logging.

New frontend modules:

- `frontend/src/library/instagramInput.ts`: Add-page routing classification.
- `frontend/src/library/mediaPresentation.ts`: content/poster selection, labels, and safe display identifiers.
- `frontend/src/library/JobIssues.tsx`: structured warning display loaded from job detail.

The existing `public_adapter.py` remains the real Instaloader gateway and facade.
It creates normalized candidates and delegates processing/orchestration rather
than retaining all filesystem and sync-loop responsibilities.

---

### Task 1: Pin Instaloader and Parse Typed Story Inputs

**Files:**

- Modify: `backend/pyproject.toml`
- Modify: `backend/src/instaloader_webui/services/instagram_inputs.py`
- Modify: `backend/src/instaloader_webui/services/library_service.py`
- Create: `backend/tests/unit/test_instagram_inputs.py`
- Create: `backend/tests/unit/test_library_service_media_inputs.py`

**Interfaces:**

- Produces: `ProfileInput`, `PostInput`, `ReelInput`, `StoryInput`, and `InstagramInput`.
- Produces: `parse_instagram_input(raw: str) -> InstagramInput`.
- Produces: canonical payload keys `media_kind`, `identity_type`, `identity_value`, `shortcode`, `story_media_id`, `username`, and `original_url`.

- [ ] **Step 1: Write parser tests for profiles, Posts, Reels, TV, and Stories**

```python
@pytest.mark.parametrize(
    ("raw", "expected_type", "identifier"),
    [
        ("@natgeo", ProfileInput, "natgeo"),
        ("https://www.instagram.com/p/CmzV2H-rrlI/?utm_source=x", PostInput, "CmzV2H-rrlI"),
        ("https://www.instagram.com/reel/DOqEJyxCRGJ/", ReelInput, "DOqEJyxCRGJ"),
        ("https://www.instagram.com/tv/ABC_123/", PostInput, "ABC_123"),
        (
            "https://www.instagram.com/stories/katerina.soria/3952742051065980676"
            "?utm_source=ig_story_item_share&igsh=secret",
            StoryInput,
            "3952742051065980676",
        ),
    ],
)
def test_parse_instagram_input_returns_typed_canonical_values(
    raw: str,
    expected_type: type[object],
    identifier: str,
) -> None:
    parsed = parse_instagram_input(raw)
    assert isinstance(parsed, expected_type)
    assert parsed.identifier == identifier
    assert "?" not in (parsed.canonical_url or "")
```

Also assert Story username validation, numeric Story IDs, HTTPS-only hosts,
credential-bearing URL rejection, extra path rejection, and the safe error copy
`"Enter a profile, post, reel, TV, or Story URL."`.

- [ ] **Step 2: Run the new parser tests and verify they fail**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_instagram_inputs.py -q
```

Expected: collection or assertions fail because the typed input classes and
Story route do not exist.

- [ ] **Step 3: Replace the generic parsed value with typed dataclasses**

Implement these exact public shapes:

```python
@dataclass(frozen=True, slots=True)
class ProfileInput:
    username: str
    canonical_url: str | None
    kind: Literal["profile"] = field(default="profile", init=False)

    @property
    def identifier(self) -> str:
        return self.username


@dataclass(frozen=True, slots=True)
class PostInput:
    shortcode: str
    canonical_url: str
    kind: Literal["post"] = field(default="post", init=False)

    @property
    def identifier(self) -> str:
        return self.shortcode


@dataclass(frozen=True, slots=True)
class ReelInput:
    shortcode: str
    canonical_url: str
    kind: Literal["reel"] = field(default="reel", init=False)

    @property
    def identifier(self) -> str:
        return self.shortcode


@dataclass(frozen=True, slots=True)
class StoryInput:
    username: str
    story_media_id: str
    canonical_url: str
    kind: Literal["story"] = field(default="story", init=False)

    @property
    def identifier(self) -> str:
        return self.story_media_id


InstagramInput = ProfileInput | PostInput | ReelInput | StoryInput
```

Normalize `/tv/{shortcode}` to `PostInput` with canonical
`https://www.instagram.com/p/{shortcode}/`. Story IDs must match
`[0-9]{1,32}`. Never retain `raw` after parsing.

- [ ] **Step 4: Write service tests for canonical single-media job payloads**

```python
def test_add_story_enqueues_canonical_single_media_job() -> None:
    jobs = RecordingJobRepository()
    service = LibraryService(library=StubLibraryRepository(), jobs=jobs)

    service.add_media(
        "https://www.instagram.com/stories/katerina.soria/3952742051065980676"
        "?igsh=secret",
        NOW,
    )

    assert jobs.enqueued_payload == {
        "media_kind": "story",
        "identity_type": "story_media_id",
        "identity_value": "3952742051065980676",
        "story_media_id": "3952742051065980676",
        "username": "katerina.soria",
        "original_url": (
            "https://www.instagram.com/stories/"
            "katerina.soria/3952742051065980676/"
        ),
    }
```

Add equivalent Post and Reel assertions. Ensure profile input is rejected by
`add_media` and media input is rejected by `add_profile`.

- [ ] **Step 5: Update `LibraryService` to consume the typed inputs**

Use `isinstance(parsed, ProfileInput)` for profile creation. For single media,
serialize the canonical typed payload; do not infer media kind later from a raw
URL. Keep the existing `single_media` job type.

- [ ] **Step 6: Run the focused tests**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_instagram_inputs.py tests/unit/test_library_service_media_inputs.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Pin Instaloader and verify the declaration**

Change:

```toml
"instaloader==4.15.3",
```

Run:

```powershell
Set-Location backend
python -c "import pathlib; text=pathlib.Path('pyproject.toml').read_text(); assert 'instaloader==4.15.3' in text and 'instaloader==4.15.2' not in text"
```

Expected: exit code 0.

- [ ] **Step 8: Commit**

```powershell
git add backend/pyproject.toml backend/src/instaloader_webui/services/instagram_inputs.py backend/src/instaloader_webui/services/library_service.py backend/tests/unit/test_instagram_inputs.py backend/tests/unit/test_library_service_media_inputs.py
git commit -m "feat: parse Instagram story inputs"
```

---

### Task 2: Migrate the Unified Media, Asset, and Job-Issue Schema

**Files:**

- Create: `backend/migrations/versions/0008_story_media_and_job_issues.py`
- Modify: `backend/src/instaloader_webui/db/models.py`
- Create: `backend/tests/integration/test_story_media_migration.py`

**Interfaces:**

- Produces media kinds `post|reel|story`.
- Produces identity types `shortcode|story_media_id`.
- Produces asset roles `content|poster`.
- Produces job state `completed_with_warnings` and nullable phase.
- Produces `JobIssue` with cascading `job_id` foreign key.

- [ ] **Step 1: Write an upgrade-preservation migration test**

Build a database at revision `0007_followee_imports`, insert one Profile, Post,
image asset, and succeeded job, then upgrade to head:

```python
def test_story_schema_upgrade_preserves_existing_library_rows(test_settings) -> None:
    config = migration_config(test_settings.database_path)
    command.upgrade(config, "0007_followee_imports")
    insert_legacy_library_fixture(test_settings.database_path)

    command.upgrade(config, "head")

    with build_engine(test_settings.database_path).connect() as connection:
        media = connection.execute(
            text(
                "SELECT shortcode, identity_type, identity_value, kind "
                "FROM media_items"
            )
        ).one()
        role = connection.execute(text("SELECT role FROM media_assets")).scalar_one()
    assert media == ("CmzV2H-rrlI", "shortcode", "CmzV2H-rrlI", "post")
    assert role == "content"
```

Also inspect column nullability, unique constraints, check constraints,
`jobs.state` length, `jobs.phase`, and `job_issues` foreign-key cascade.

- [ ] **Step 2: Run the migration test and verify it fails**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/integration/test_story_media_migration.py -q
```

Expected: FAIL because revision `0008_story_media_and_job_issues` is absent.

- [ ] **Step 3: Add migration `0008_story_media_and_job_issues`**

The upgrade must:

1. Add nullable `identity_type`, `identity_value`, and `story_expires_at`.
2. Backfill identity fields from every existing non-null shortcode.
3. Recreate `media_items` in Alembic batch mode so shortcode becomes nullable,
   identity fields become non-null, `kind` accepts Story, and
   `(identity_type, identity_value)` is unique.
4. Preserve the existing unique `instagram_media_id`.
5. Add `media_assets.role` with temporary server default `content`, then remove
   the default and add the `content|poster` check.
6. Widen `jobs.state` to 32 characters, replace its state check, and add nullable
   `phase`.
7. Create `job_issues` with columns `id`, `job_id`, `identity_type`,
   `identity_value`, `media_kind`, `error_code`, `safe_message`,
   `exception_class_chain_text`, and `occurred_at`.
8. Add indexes on `(job_id, occurred_at)` and
   `(identity_type, identity_value)`.

Use explicit constraint names:

```python
sa.UniqueConstraint(
    "identity_type",
    "identity_value",
    name="uq_media_items_identity",
)
sa.CheckConstraint(
    "identity_type IN ('shortcode', 'story_media_id')",
    name="ck_media_items_identity_type",
)
sa.CheckConstraint(
    "kind IN ('post', 'reel', 'story')",
    name="ck_media_items_kind",
)
sa.CheckConstraint(
    "role IN ('content', 'poster')",
    name="ck_media_assets_role",
)
sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE")
```

The downgrade may remove Story and poster rows before restoring the older
constraints; document that behavior in the migration docstring.

- [ ] **Step 4: Mirror the schema in ORM models**

Add exact fields and constraints to `MediaItem`, `MediaAsset`, and `Job`. Add a
`JobIssue` model whose exception chain is stored as compact JSON text. Widen
`Job.state` to `String(32)`.

- [ ] **Step 5: Run migration tests**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/integration/test_story_media_migration.py tests/integration/test_database.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/migrations/versions/0008_story_media_and_job_issues.py backend/src/instaloader_webui/db/models.py backend/tests/integration/test_story_media_migration.py
git commit -m "feat: add Story media and job issue schema"
```

---

### Task 3: Add Identity-Aware Media and Job-Issue Repositories

**Files:**

- Modify: `backend/src/instaloader_webui/db/library_repositories.py`
- Create: `backend/tests/unit/test_library_repositories_media.py`
- Create: `backend/tests/unit/test_job_issue_repository.py`

**Interfaces:**

- Produces: `MediaIdentity(identity_type: str, value: str)`.
- Produces: `AssetSnapshot.role`.
- Produces: nullable `MediaSnapshot.shortcode`, `story_media_id`, and
  `story_expires_at`.
- Produces: `JobIssueInput`, `JobIssueSnapshot`, `JobSnapshot.phase`,
  `issue_count`, and `issues`.
- Produces: `LibraryRepository.find_media_by_identity(identity)`.
- Produces: `JobRepository.record_issue`, `complete_with_warnings`, and
  phase-aware `update_progress`.

- [ ] **Step 1: Write media repository tests**

```python
def test_upsert_story_uses_story_identity_and_poster_role(repository, profile) -> None:
    saved = repository.upsert_media(
        normalized=NormalizedMedia(
            identity=MediaIdentity("story_media_id", "3952742051065980676"),
            instagram_media_id="3952742051065980676",
            shortcode=None,
            kind="story",
            caption="",
            accessibility_caption="",
            published_at=NOW,
            story_expires_at=NOW + timedelta(hours=24),
            original_url=(
                "https://www.instagram.com/stories/"
                "katerina.soria/3952742051065980676/"
            ),
        ),
        profile_id=profile.id,
        assets=(
            NormalizedAsset("profiles/1/story/video.mp4", "video/mp4", "video", "content", 0, 10),
            NormalizedAsset("profiles/1/story/poster.jpg", "image/jpeg", "image", "poster", 0, 5),
        ),
        now=NOW,
    )
    assert saved.story_media_id == "3952742051065980676"
    assert [(asset.kind, asset.role, asset.position) for asset in saved.assets] == [
        ("video", "content", 0),
        ("image", "poster", 0),
    ]
```

Also test that a shortcode record can change from Post to Reel without
duplication, and that asset replacement removes prior role rows atomically.

- [ ] **Step 2: Write job issue repository tests**

```python
def test_complete_with_warnings_returns_structured_issues(jobs) -> None:
    job = jobs.enqueue(
        job_type="profile_sync",
        payload={"profile_id": "profile-1"},
        status_text="Queued.",
        now=NOW,
    )
    claimed = jobs.claim_next(NOW)
    assert claimed is not None
    jobs.record_issue(
        job_id=job.id,
        issue=JobIssueInput(
            identity_type="shortcode",
            identity_value="DOqEJyxCRGJ",
            media_kind="reel",
            error_code="instagram_unavailable",
            safe_message="Instagram could not be reached. Try again later.",
            exception_class_chain=("BadResponseException",),
        ),
        now=NOW,
    )
    jobs.complete_with_warnings(
        job_id=job.id,
        status_text="Completed with 1 warning.",
        now=NOW,
    )
    detail = jobs.get(job.id, include_issues=True)
    assert detail is not None
    assert detail.state == "completed_with_warnings"
    assert detail.issue_count == 1
    assert detail.issues[0].identity_value == "DOqEJyxCRGJ"
```

Also verify `list()` returns `issue_count` but an empty `issues` tuple, deletion
of a job cascades issues, and progress stores `phase`.

- [ ] **Step 3: Run the repository tests and verify they fail**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_library_repositories_media.py tests/unit/test_job_issue_repository.py -q
```

Expected: FAIL because the snapshots and repository methods are absent.

- [ ] **Step 4: Implement identity-aware media snapshots and persistence**

Use:

```python
@dataclass(frozen=True, slots=True)
class MediaIdentity:
    identity_type: Literal["shortcode", "story_media_id"]
    value: str


@dataclass(frozen=True, slots=True)
class NormalizedAsset:
    relative_path: str
    mime_type: str
    kind: Literal["image", "video"]
    role: Literal["content", "poster"]
    position: int
    file_size: int
```

Query and upsert media by both identity columns. Keep
`find_media_by_shortcode()` as a compatibility wrapper around
`find_media_by_identity(MediaIdentity("shortcode", shortcode))`.
Return assets deterministically by logical position, with `content` before
`poster` at the same position.

- [ ] **Step 5: Implement job phases, issue snapshots, and warning completion**

Store exception class chains with:

```python
json.dumps(issue.exception_class_chain, separators=(",", ":"))
```

Decode only a JSON list of bounded strings; reject malformed persisted data.
`complete_with_warnings()` may update only a running job and clears fatal
`error`, just like `succeed()`.

- [ ] **Step 6: Run focused repository and migration tests**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_library_repositories_media.py tests/unit/test_job_issue_repository.py tests/integration/test_story_media_migration.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/instaloader_webui/db/library_repositories.py backend/tests/unit/test_library_repositories_media.py backend/tests/unit/test_job_issue_repository.py
git commit -m "feat: persist media identities and job warnings"
```

---

### Task 4: Produce Safe Structured Instagram Issues and Logs

**Files:**

- Modify: `backend/src/instaloader_webui/instagram/errors.py`
- Create: `backend/src/instaloader_webui/instagram/safe_issues.py`
- Create: `backend/tests/unit/test_safe_issues.py`

**Interfaces:**

- Produces: `SafeMediaIssue`.
- Produces: `MediaItemFailure(issue: SafeMediaIssue)`.
- Produces: `classify_media_issue(error, *, session_configured, target, identity, kind)`.
- Produces: `log_media_issue(logger, *, job_id, issue)`.

- [ ] **Step 1: Write classification and redaction tests**

```python
def test_issue_keeps_class_names_but_not_exception_message() -> None:
    cause = ConnectionException(
        "https://instagram.com/p/x/?igsh=secret Cookie: sessionid=secret"
    )
    error = BadResponseException("wrapper")
    error.__cause__ = cause

    issue = classify_media_issue(
        error,
        session_configured=True,
        target="media",
        identity=MediaIdentity("shortcode", "DOqEJyxCRGJ"),
        kind="reel",
    )

    assert issue.error_code == "instagram_unavailable"
    assert issue.safe_message == "Instagram could not be reached. Try again later."
    assert issue.exception_class_chain == (
        "BadResponseException",
        "ConnectionException",
    )
    assert "secret" not in repr(issue)
```

Use `caplog` to assert the emitted warning includes job ID, shortcode, kind,
safe code, and class names but contains none of `Cookie`, `sessionid`,
`igsh=`, `?`, or the secret values.

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_safe_issues.py -q
```

Expected: FAIL because `safe_issues` does not exist.

- [ ] **Step 3: Implement controlled issue codes and exception chains**

Use these exact stable codes:

```python
IssueCode = Literal[
    "challenge_required",
    "instagram_rate_limited",
    "instagram_session_rejected",
    "instagram_access_denied",
    "instagram_not_found",
    "instagram_unavailable",
    "asset_validation_failed",
]
```

`SafeMediaIssue` contains only identity, media kind, code, fixed safe message,
and a class-name chain. Traverse `__cause__` before `__context__`, deduplicate
cycles, allow at most eight class names, and bound each name to 128 characters.
Keep `classify_instaloader_error()` as a compatibility wrapper returning only
the safe message.

- [ ] **Step 4: Implement the final log redaction boundary**

`log_media_issue()` must log one controlled, single-line message. Sanitize all
fields again by removing URL query/fragment components and replacing
case-insensitive `cookie`, `sessionid`, and `csrftoken` assignments with
`[redacted]`. Never call `logger.exception()` and never pass the raw exception
object to logging.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_safe_issues.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/src/instaloader_webui/instagram/errors.py backend/src/instaloader_webui/instagram/safe_issues.py backend/tests/unit/test_safe_issues.py
git commit -m "feat: record safe Instagram media issues"
```

---

### Task 5: Extract the Unified Staged Media Processor

**Files:**

- Create: `backend/src/instaloader_webui/instagram/media_types.py`
- Create: `backend/src/instaloader_webui/instagram/media_processor.py`
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Modify: `backend/src/instaloader_webui/instagram/__init__.py`
- Create: `backend/tests/unit/test_media_processor.py`

**Interfaces:**

- Produces: `MediaCandidate`, `ResolvedMedia`, `MediaProcessResult`, and
  `MediaProcessor`.
- Consumes: `MediaIdentity`, `NormalizedMedia`, `NormalizedAsset`,
  `MediaItemFailure`, and `LibraryRepository`.
- `MediaProcessor.process(candidate, *, job_id) -> MediaProcessResult`.

- [ ] **Step 1: Write processor tests for image, video-poster, skip, and rollback**

```python
def test_video_candidate_maps_jpg_to_poster_not_content(processor, fake_download) -> None:
    candidate = make_candidate(
        identity=MediaIdentity("shortcode", "DOqEJyxCRGJ"),
        kind="reel",
        content_kinds=("video",),
        download=fake_download.files(
            ("DOqEJyxCRGJ.jpg", b"jpeg"),
            ("DOqEJyxCRGJ.mp4", b"video"),
        ),
    )

    result = processor.process(candidate, job_id="job-1")

    assert result.status == "saved"
    assert [(a.kind, a.role, a.position) for a in result.media.assets] == [
        ("video", "content", 0),
        ("image", "poster", 0),
    ]
```

Also test:

- one image becomes `content` position 0;
- sidecar node kinds map files to their expected logical positions;
- a complete media item with valid files and roles returns `existing`;
- a legacy Reel with JPG and MP4 both marked `content` is reprocessed;
- no content output raises `MediaItemFailure` with
  `asset_validation_failed`;
- database failure restores the previous final directory;
- item failure removes staging and creates no media record.

- [ ] **Step 2: Run the processor test and verify it fails**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_media_processor.py -q
```

Expected: FAIL because the processor modules do not exist.

- [ ] **Step 3: Define the media-domain candidate**

```python
DownloadAction = Callable[[Instaloader, str], None]
ResolveAction = Callable[[], "ResolvedMedia"]


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    identity: MediaIdentity
    kind: Literal["post", "reel", "story"]
    session_configured: bool
    resolve: ResolveAction = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    identity: MediaIdentity
    kind: Literal["post", "reel", "story"]
    instagram_media_id: str
    shortcode: str | None
    profile_id: str
    instagram_user_id: str
    owner_username: str
    caption: str
    accessibility_caption: str
    published_at: datetime
    story_expires_at: datetime | None
    original_url: str
    content_kinds: tuple[Literal["image", "video"], ...]
    download: DownloadAction = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class MediaProcessResult:
    status: Literal["saved", "existing"]
    media: MediaSnapshot
```

Manifest construction reads only `MediaCandidate.identity` and `kind`.
Potentially fragile caption, accessibility, sidecar, URL, and content-kind
metadata is deferred to `resolve()` inside the per-item processor boundary.
This makes one item's metadata error a warning while an iterator that cannot
produce complete identities remains fatal. Each download runs in a clean,
per-item staging directory, so Story filenames do not need to contain a
shortcode.

- [ ] **Step 4: Move staging, finalization, and persistence into `MediaProcessor`**

Move the relevant path containment, replacement directory, backup, rollback,
commit, and local-asset checks from `PublicInstaloaderAdapter`. Final media
directories use the safe `identity.value`, not a required shortcode.

Call `candidate.resolve()` inside `process()`. Translate an Instaloader
exception from resolution or download into `MediaItemFailure` using the
candidate identity, kind, and `session_configured`. Allow database and
filesystem exceptions to escape as fatal infrastructure errors.

Map files using both `resolved.content_kinds` and Instaloader sequence naming:

- expected image position: JPG is `content`;
- expected video position: MP4 is `content`, JPG at that position is `poster`;
- unmatched supported files cause `asset_validation_failed`;
- at least one `content` asset is required.

The processor may use extensions to identify MIME type, but role comes from the
expected logical content kind.

- [ ] **Step 5: Delegate existing Post/Reel downloads through the processor**

Keep `PublicInstaloaderAdapter.download_shortcode()` temporarily for caller
compatibility. Create a lightweight `MediaCandidate` from the Post shortcode
and expected kind, then call `MediaProcessor.process()`. Its lazy resolver
builds `ResolvedMedia.content_kinds` from `Post.typename`, `Post.is_video`, and
`Post.get_sidecar_nodes()` inside the per-item boundary.

- [ ] **Step 6: Run processor and existing focused tests**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_media_processor.py tests/unit/test_library_repositories_media.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/instaloader_webui/instagram/media_types.py backend/src/instaloader_webui/instagram/media_processor.py backend/src/instaloader_webui/instagram/public_adapter.py backend/src/instaloader_webui/instagram/__init__.py backend/tests/unit/test_media_processor.py
git commit -m "refactor: unify staged Instagram media processing"
```

---

### Task 6: Resolve and Download Stories Through the Real Adapter

**Files:**

- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Modify: `backend/src/instaloader_webui/instagram/worker_runtime.py`
- Create: `backend/tests/unit/test_public_adapter_stories.py`

**Interfaces:**

- Produces: `PublicInstaloaderAdapter.download_input(parsed, job_id)`.
- Produces: `PublicInstaloaderAdapter.iter_story_candidates(profile, profile_id)`.
- Produces: `WorkerInstaloaderRuntime.acquire_required_session(staging_directory)`.
- Consumes: `StoryInput`, `StoryItem`, `MediaCandidate`, and `MediaProcessor`.

- [ ] **Step 1: Write direct Story adapter tests**

Use fake loader/runtime/StoryItem objects; do not call Instagram:

```python
def test_direct_story_validates_owner_and_downloads_only_story(adapter, story_item) -> None:
    story_item.owner_profile.username = "katerina.soria"

    saved = adapter.download_input(
        StoryInput(
            username="katerina.soria",
            story_media_id="3952742051065980676",
            canonical_url=(
                "https://www.instagram.com/stories/"
                "katerina.soria/3952742051065980676/"
            ),
        ),
        "job-1",
    )

    assert saved.kind == "story"
    assert saved.story_media_id == "3952742051065980676"
    assert story_item.download_calls == 1
    assert story_item.shared_reel_download_calls == 0
```

Also test owner mismatch, missing authenticated session, expired/not-found
Story, image Story, video Story poster, and canonical URL persistence.

- [ ] **Step 2: Run the Story adapter tests and verify they fail**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_public_adapter_stories.py -q
```

Expected: FAIL because Story adapter methods do not exist.

- [ ] **Step 3: Require an authenticated loader for Story operations**

Add:

```python
def acquire_required_session(self, staging_directory: Path) -> Instaloader:
    loader, configured = self.acquire(staging_directory)
    if not configured or not loader.context.is_logged_in:
        raise InstagramSessionRevisionError(
            "An imported Instagram Cookie is required for Stories."
        )
    return loader
```

Translate this into the existing safe session-rejected/access-denied boundary;
do not expose the stored username or Cookie details.

- [ ] **Step 4: Build Story candidates**

Resolve direct Story input with:

```python
StoryItem.from_mediaid(loader.context, int(parsed.story_media_id))
```

For Profile sync, enumerate:

```python
for story in loader.get_stories(userids=[profile.userid]):
    for item in story.get_items():
        yield build_story_candidate(item, stored_profile)
```

Use `item.mediaid` for the lightweight identity. Defer owner metadata,
`item.date_utc`, `item.expiring_utc`, and `item.is_video` to the lazy resolver,
which also supplies
`loader.download_storyitem(item, target=username)`. Do not inspect or follow
reshare/source-Reel fields.

- [ ] **Step 5: Route all typed direct inputs through `download_input`**

Post and Reel inputs use `Post.from_shortcode`; Story uses
`StoryItem.from_mediaid`. Verify/update the owner Profile and avatar before
building the candidate. Direct single-media avatar failure remains non-fatal,
matching existing single-media behavior; Profile-sync preflight remains strict.

- [ ] **Step 6: Run Story and processor tests**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_public_adapter_stories.py tests/unit/test_media_processor.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/instaloader_webui/instagram/public_adapter.py backend/src/instaloader_webui/instagram/worker_runtime.py backend/tests/unit/test_public_adapter_stories.py
git commit -m "feat: download Instagram Stories"
```

---

### Task 7: Implement the Story-First Profile Sync Coordinator

**Files:**

- Create: `backend/src/instaloader_webui/instagram/profile_sync.py`
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Create: `backend/tests/unit/test_profile_sync_coordinator.py`

**Interfaces:**

- Produces: `ProfileSyncCoordinator`.
- Produces: `ProfileSyncResult(processed, total, issue_count, stopped)`.
- Consumes: source iterator callables, `MediaProcessor`, `MediaItemFailure`,
  progress callback, issue callback, and syncable callback.

- [ ] **Step 1: Write ordering, deduplication, and progress tests**

```python
def test_sync_saves_stories_before_scanning_reels_and_posts() -> None:
    events: list[str] = []
    source = RecordingSource(
        stories=(story("story-1"), story("story-2")),
        reels=(reel("shared"), reel("reel-only")),
        posts=(post("shared"), post("post-only")),
        events=events,
    )
    coordinator = make_coordinator(source=source, events=events)

    result = coordinator.run(profile=PROFILE, job_id="job-1")

    assert events == [
        "scan:stories",
        "process:story-1",
        "process:story-2",
        "scan:reels",
        "scan:posts",
        "process:shared",
        "process:reel-only",
        "process:post-only",
    ]
    assert result.total == 5
```

Assert phase callbacks are:

```python
[
    (0, None, "saving_stories", "Saving current Instagram stories before they expire…"),
    (2, None, "scanning_media", "Scanning Instagram posts and reels…"),
    (2, 5, "processing_reels", "Processing Instagram reels."),
]
```

Add tests for empty Stories, already-existing outcomes, one warning per failed
item, progress increment after failure, `completed_with_warnings` result,
iterator interruption as an uncaught fatal error, and Stop Sync checks only at
item boundaries. Add adapter-level tests proving a valid standard
`profile_pic_url` satisfies avatar preflight and an unreadable/non-image avatar
is fatal before Story enumeration begins.

- [ ] **Step 2: Run the coordinator tests and verify they fail**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_profile_sync_coordinator.py -q
```

Expected: FAIL because the coordinator does not exist.

- [ ] **Step 3: Implement focused coordinator protocols**

```python
ProgressCallback = Callable[[int, int | None, str, str], None]
IssueCallback = Callable[[SafeMediaIssue], None]


class ProfileMediaSource(Protocol):
    def iter_stories(self, profile: object) -> Iterable[MediaCandidate]: ...
    def iter_reels(self, profile: object) -> Iterable[MediaCandidate]: ...
    def iter_posts(self, profile: object) -> Iterable[MediaCandidate]: ...


@dataclass(frozen=True, slots=True)
class ProfileSyncResult:
    processed: int
    total: int | None
    issue_count: int
    stopped: bool
```

Convert each iterator to a tuple before processing that manifest. Iterator
construction or iteration failure must escape as fatal. Catch only
`MediaItemFailure` around `processor.process(candidate)`, record/log its safe
issue, continue, and increment progress once in all item-outcome paths.

- [ ] **Step 4: Implement Reel-first deduplication**

Create an insertion-ordered dictionary keyed by `candidate.identity.value`.
Insert Reels first. Add Posts only when the shortcode key is absent. Never use
`kind` as part of the dedupe key.

- [ ] **Step 5: Wire strict Profile preflight in the facade**

Before calling the coordinator:

1. Load stored Profile and validate tracked/active state.
2. Acquire the loader.
3. resolve Profile metadata;
4. obtain and validate a usable avatar;
5. update Profile metadata;
6. call the coordinator.

Failure in these steps or any iterator sets the Profile sync result to failed.
A returned result, including one with warnings, sets
`last_sync_succeeded_at`.

- [ ] **Step 6: Run coordinator and adapter tests**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_profile_sync_coordinator.py tests/unit/test_public_adapter_stories.py tests/unit/test_media_processor.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/instaloader_webui/instagram/profile_sync.py backend/src/instaloader_webui/instagram/public_adapter.py backend/tests/unit/test_profile_sync_coordinator.py
git commit -m "feat: make profile sync Story-first and resilient"
```

---

### Task 8: Wire Worker Terminal States, Issues, and Phases

**Files:**

- Modify: `backend/src/instaloader_webui/services/job_runner.py`
- Create: `backend/tests/unit/test_job_runner_media.py`

**Interfaces:**

- Consumes: typed single-media payloads and `PublicInstaloaderAdapter.download_input`.
- Consumes: `ProfileSyncResult.issue_count`.
- Produces: runner-controlled `succeeded`, `completed_with_warnings`, and
  `failed` transitions.
- Produces: phase-aware progress and safe issue persistence/logging.

- [ ] **Step 1: Write job runner terminal-state tests**

```python
def test_profile_sync_with_item_issues_completes_with_warnings(
    runner, jobs, monkeypatch
) -> None:
    job = claimed_profile_sync(jobs)
    monkeypatch.setattr(
        runner,
        "_adapter",
        lambda _job: FakeAdapter(
            sync_result=ProfileSyncResult(
                processed=3,
                total=3,
                issue_count=1,
                stopped=False,
            )
        )
    )

    runner.run(job)

    completed = jobs.get(job.id, include_issues=True)
    assert completed is not None
    assert completed.state == "completed_with_warnings"
```

Also test:

- zero issues ends `succeeded`;
- direct Story payload reaches `StoryInput`;
- a direct `MediaItemFailure` records one issue then ends `failed`;
- fatal Profile iterator error ends `failed` and updates
  `last_sync_attempted_at` without `last_sync_succeeded_at`;
- phase and nullable totals reach `JobRepository.update_progress`;
- a Story saved before a later fatal scan remains in the library.

- [ ] **Step 2: Run the runner tests and verify they fail**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_job_runner_media.py -q
```

Expected: FAIL because the runner still infers kind from raw URLs and always
calls `succeed()`.

- [ ] **Step 3: Deserialize typed payloads without raw-URL inference**

Replace `_expected_kind(original_url)` with one private decoder that validates
the payload keys created in Task 1 and returns `PostInput`, `ReelInput`, or
`StoryInput`. Reject inconsistent kind/identity combinations before calling the
adapter.

- [ ] **Step 4: Add phase and issue callbacks to adapter construction**

Pass:

```python
progress=lambda current, total, phase, status: self._progress(
    job, current, total, phase, status
)
issue=lambda issue: self._record_media_issue(job, issue)
```

`_record_media_issue()` calls `JobRepository.record_issue()` and
`log_media_issue()` exactly once.

- [ ] **Step 5: Select terminal state from the dispatch result**

Make `_dispatch()` return the warning count. In `run()`:

```python
if warning_count:
    self._jobs.complete_with_warnings(
        job_id=job.id,
        status_text=f"Completed with {warning_count} warning(s).",
        now=now,
    )
else:
    self._jobs.succeed(job_id=job.id, status_text=status_text, now=now)
```

Catch direct `MediaItemFailure` separately, persist/log its issue, and fail with
only `issue.safe_message`.

- [ ] **Step 6: Run runner and coordinator tests**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/unit/test_job_runner_media.py tests/unit/test_profile_sync_coordinator.py tests/unit/test_safe_issues.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/src/instaloader_webui/services/job_runner.py backend/tests/unit/test_job_runner_media.py
git commit -m "feat: complete media jobs with structured warnings"
```

---

### Task 9: Expose Story Assets and Job Issues Through the API

**Files:**

- Modify: `backend/src/instaloader_webui/api/library_dtos.py`
- Modify: `backend/src/instaloader_webui/api/routes/media.py`
- Modify: `backend/src/instaloader_webui/api/routes/jobs.py`
- Create: `backend/tests/integration/test_library_media_api.py`
- Create: `backend/tests/integration/test_job_issues_api.py`

**Interfaces:**

- Produces media filters `post|reel|story`.
- Produces `AssetResponse.role`.
- Produces nullable `MediaResponse.shortcode`, `story_media_id`,
  `identity_type`, `identity_value`, and `story_expires_at`.
- Produces `JobIssueResponse`.
- Produces `JobResponse.phase`, `issue_count`, and `issues`.

- [ ] **Step 1: Write API serialization tests**

Seed repository snapshots through `authenticated_client.app.state` and assert:

```python
response = await authenticated_client.get(
    "/api/media",
    params={"profile_id": profile.id, "kind": "story"},
)
assert response.status_code == 200
story = response.json()["data"][0]
assert story["kind"] == "story"
assert story["shortcode"] is None
assert story["story_media_id"] == "3952742051065980676"
assert story["assets"][1]["role"] == "poster"
```

For jobs, assert `/api/jobs` returns `issue_count=1` with `issues=[]`, while
`/api/jobs/{id}` returns the full issue including shortcode/type/code/message,
class-name chain, and timestamp. Assert no raw exception or query parameter is
present in the JSON body.

- [ ] **Step 2: Run API tests and verify they fail**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/integration/test_library_media_api.py tests/integration/test_job_issues_api.py -q
```

Expected: FAIL because current DTOs require shortcode and omit role/issues.

- [ ] **Step 3: Extend immutable DTOs and serializers**

Define:

```python
class JobIssueResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    identity_type: str
    identity_value: str
    shortcode: str | None
    story_media_id: str | None
    media_kind: str
    error_code: str
    safe_message: str
    exception_class_chain: tuple[str, ...]
    occurred_at: datetime
```

`serialize_job()` includes all issues present on the supplied snapshot. List
snapshots have none; detail snapshots include them.

- [ ] **Step 4: Extend filters and detail loading**

Update `kind` in `routes/media.py` to
`Literal["post", "reel", "story"] | None`. Change job detail to:

```python
job = jobs.get(job_id, include_issues=True)
```

Keep authentication and API envelopes unchanged.

- [ ] **Step 5: Run API and repository tests**

Run:

```powershell
Set-Location backend
python -m pytest --no-cov tests/integration/test_library_media_api.py tests/integration/test_job_issues_api.py tests/unit/test_job_issue_repository.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/src/instaloader_webui/api/library_dtos.py backend/src/instaloader_webui/api/routes/media.py backend/src/instaloader_webui/api/routes/jobs.py backend/tests/integration/test_library_media_api.py backend/tests/integration/test_job_issues_api.py
git commit -m "feat: expose Stories and job issues in the API"
```

---

### Task 10: Route Story URLs Correctly From the Add Page

**Files:**

- Create: `frontend/src/library/instagramInput.ts`
- Modify: `frontend/src/library/AddPage.tsx`
- Create: `frontend/src/library/AddPage.test.tsx`

**Interfaces:**

- Produces: `classifyAddInput(value: string) -> "profile" | "media"`.
- Consumes existing `addMedia()` and `addProfile()` APIs.

- [ ] **Step 1: Write frontend routing tests with MSW**

```tsx
it("queues a query-bearing Story URL as media", async () => {
  let mediaInput = "";
  let profileCalls = 0;
  server.use(
    http.post("/api/media", async ({ request }) => {
      mediaInput = ((await request.json()) as { input: string }).input;
      return HttpResponse.json(successEnvelope(jobFixture));
    }),
    http.post("/api/profiles", () => {
      profileCalls += 1;
      return HttpResponse.json(successEnvelope(profileCreateFixture));
    }),
  );
  render(
    <TestRouter initialPath="/add" initialSession={authenticatedSession} />,
  );

  await userEvent.type(
    screen.getByLabelText("Instagram link or profile"),
    "https://www.instagram.com/stories/katerina.soria/3952742051065980676"
      + "?utm_source=ig_story_item_share&igsh=secret",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add to library" }));

  expect(await screen.findByRole("heading", { name: "Download queued" })).toBeVisible();
  expect(mediaInput).toContain("/stories/");
  expect(profileCalls).toBe(0);
});
```

Add parameterized cases for Profile, `/p/`, `/reel/`, and `/tv/`.

- [ ] **Step 2: Run the Add-page tests and verify the Story case fails**

Run:

```powershell
Set-Location frontend
npm exec -- vitest run src/library/AddPage.test.tsx
```

Expected: Story request reaches `/api/profiles` and the test fails.

- [ ] **Step 3: Implement one focused classifier**

`classifyAddInput()` trims input, parses valid Instagram HTTPS URLs with
`URL`, and returns `media` only for path shapes:

```text
/p/{shortcode}
/reel/{shortcode}
/tv/{shortcode}
/stories/{username}/{numeric_story_id}
```

Unrecognized values return `profile` so the backend remains the validation
authority. Replace `isMediaInput()` in `AddPage`.

- [ ] **Step 4: Update Add-page copy**

Mention Story links in the intro, placeholder/hint, and safe validation copy.
Do not claim shared source Reels will also be downloaded.

- [ ] **Step 5: Run Add-page tests**

Run:

```powershell
Set-Location frontend
npm exec -- vitest run src/library/AddPage.test.tsx
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/library/instagramInput.ts frontend/src/library/AddPage.tsx frontend/src/library/AddPage.test.tsx
git commit -m "feat: route Instagram Story inputs"
```

---

### Task 11: Add Profile Story Tab, Original Link, and Poster-Aware Media UI

**Files:**

- Modify: `frontend/src/library/types.ts`
- Modify: `frontend/src/library/api.ts`
- Create: `frontend/src/library/mediaPresentation.ts`
- Modify: `frontend/src/library/ProfilePage.tsx`
- Modify: `frontend/src/library/MediaGrid.tsx`
- Modify: `frontend/src/library/MediaViewerPage.tsx`
- Modify: `frontend/src/styles/global.css`
- Create: `frontend/src/library/ProfilePage.test.tsx`
- Create: `frontend/src/library/MediaGrid.test.tsx`
- Create: `frontend/src/library/MediaViewerPage.test.tsx`

**Interfaces:**

- Produces frontend media kinds `post|reel|story` and asset roles
  `content|poster`.
- Produces: `contentAssets(media)`, `posterFor(media, position)`,
  `thumbnailAsset(media)`, `mediaLabel(media)`, and
  `mediaDisplayIdentifier(media)`.
- Consumes the API shapes from Task 9.

- [ ] **Step 1: Write Profile page tests**

MSW returns one profile and records `/api/media` queries. Assert:

```tsx
expect(await screen.findByRole("tab", { name: "Posts" })).toBeVisible();
expect(screen.getByRole("tab", { name: "Reels" })).toBeVisible();
expect(screen.getByRole("tab", { name: "Story" })).toBeVisible();
expect(
  screen.getByRole("link", { name: "Open @katerina.soria on Instagram" }),
).toHaveAttribute("href", "https://www.instagram.com/katerina.soria/");
expect(
  screen.getByRole("link", { name: "Open @katerina.soria on Instagram" }),
).toHaveAttribute("rel", "noopener noreferrer");
```

Click Story and assert the next request contains `kind=story`.

- [ ] **Step 2: Write poster and carousel tests**

For a Reel with a video `content` asset and image `poster` at position 0:

- MediaGrid renders the poster image and says `Reel`;
- the overlay does not say `2 items`;
- MediaViewer renders one video with its poster URL;
- carousel controls are absent because there is one content item.

For a Story fixture, assert `Story` heading/label, nullable shortcode handling,
and the canonical Story original link.

- [ ] **Step 3: Run the new UI tests and verify they fail**

Run:

```powershell
Set-Location frontend
npm exec -- vitest run src/library/ProfilePage.test.tsx src/library/MediaGrid.test.tsx src/library/MediaViewerPage.test.tsx
```

Expected: failures for the missing Story tab/link and poster exclusion.

- [ ] **Step 4: Align TypeScript API types**

Use literal unions:

```typescript
export type MediaKind = "post" | "reel" | "story";
export type AssetKind = "image" | "video";
export type AssetRole = "content" | "poster";
```

Make `shortcode` nullable and add `identity_type`, `identity_value`,
`story_media_id`, `story_expires_at`, and asset `role`. Extend
`MediaListOptions.kind` with Story.

- [ ] **Step 5: Implement media presentation helpers**

```typescript
export function contentAssets(media: MediaSummary): readonly MediaAsset[] {
  return media.assets.filter((asset) => asset.role === "content");
}

export function posterFor(
  media: MediaSummary,
  position: number,
): MediaAsset | null {
  return (
    media.assets.find(
      (asset) => asset.role === "poster" && asset.position === position,
    ) ?? null
  );
}
```

`thumbnailAsset()` prefers the lowest-position poster, then content.
`mediaDisplayIdentifier()` returns shortcode, then Story media ID, then
identity value. `mediaLabel()` returns exactly Post, Reel, or Story.

- [ ] **Step 6: Update Profile, grid, and viewer components**

Add `story` to `MediaTab`, render the requested singular `Story` tab, and map
tab IDs/empty copy explicitly instead of nested conditionals. Add the safe
external Profile link.

In MediaGrid, count only `contentAssets()`. In MediaViewer, store selection
against the filtered content array and set video `poster` from the matching
poster asset.

- [ ] **Step 7: Add minimal Story/warning styles**

Add only selectors required by the new tab, external link, and poster-aware
viewer. Preserve existing responsive breakpoints.

- [ ] **Step 8: Run UI tests**

Run:

```powershell
Set-Location frontend
npm exec -- vitest run src/library/ProfilePage.test.tsx src/library/MediaGrid.test.tsx src/library/MediaViewerPage.test.tsx
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```powershell
git add frontend/src/library/types.ts frontend/src/library/api.ts frontend/src/library/mediaPresentation.ts frontend/src/library/ProfilePage.tsx frontend/src/library/MediaGrid.tsx frontend/src/library/MediaViewerPage.tsx frontend/src/styles/global.css frontend/src/library/ProfilePage.test.tsx frontend/src/library/MediaGrid.test.tsx frontend/src/library/MediaViewerPage.test.tsx
git commit -m "feat: present Posts Reels and Stories correctly"
```

---

### Task 12: Show Warning Jobs and Safe Issue Details in Activity

**Files:**

- Modify: `frontend/src/library/types.ts`
- Modify: `frontend/src/library/ActivityPage.tsx`
- Create: `frontend/src/library/JobIssues.tsx`
- Modify: `frontend/src/styles/global.css`
- Create: `frontend/src/library/ActivityPage.test.tsx`

**Interfaces:**

- Produces frontend `JobIssue` and `JobDetail`.
- Consumes `listJobs()` for summaries and `getJob()` for expanded details.
- Produces distinct `Completed with warnings` presentation.

- [ ] **Step 1: Write Activity tests for unknown totals and issues**

```tsx
it("shows a scanning phase without a false count or percentage", async () => {
  server.use(
    http.get("/api/jobs", () =>
      HttpResponse.json(successEnvelope([
        jobFixture({
          state: "running",
          phase: "scanning_media",
          progress_current: 2,
          progress_total: null,
          status_text: "Scanning Instagram posts and reels…",
        }),
      ])),
    ),
  );
  render(<TestRouter initialPath="/activity" initialSession={authenticatedSession} />);

  expect(await screen.findByText("Scanning Instagram posts and reels…")).toBeVisible();
  expect(screen.queryByText(/2 of/)).not.toBeInTheDocument();
  expect(screen.queryByText(/%/)).not.toBeInTheDocument();
});
```

Add a `completed_with_warnings` summary with `issue_count=1`. Click
`View 1 warning`, have MSW return job detail, and assert shortcode,
`Reel`, error code, safe message, class chain, and occurrence time are visible.
Assert query-bearing/raw exception text is absent.

- [ ] **Step 2: Run Activity tests and verify they fail**

Run:

```powershell
Set-Location frontend
npm exec -- vitest run src/library/ActivityPage.test.tsx
```

Expected: failures because Activity renders `Progress pending`, `0%`, and no
issue details.

- [ ] **Step 3: Add job issue/detail types**

```typescript
export type JobIssue = Readonly<{
  identity_type: "shortcode" | "story_media_id";
  identity_value: string;
  shortcode: string | null;
  story_media_id: string | null;
  media_kind: MediaKind;
  error_code: string;
  safe_message: string;
  exception_class_chain: readonly string[];
  occurred_at: string;
}>;
```

Add nullable `phase`, `issue_count`, and `issues` to `JobSummary`; alias
`JobDetail = JobSummary`.

- [ ] **Step 4: Implement expansion and warning presentation**

For unknown totals, render only `status_text`; omit numeric copy and the
`<progress>` element. For known totals, retain exact count and percentage.
Map state labels explicitly so `completed_with_warnings` becomes
`Completed with warnings`.

`JobIssues` fetches detail only after the user expands a warning job, displays
the safe fields, and handles a detail-fetch error without removing the summary.

- [ ] **Step 5: Add status and issue styles**

Add a visible warning color distinct from success/failure and a compact,
responsive issue list. Do not render exception data as HTML.

- [ ] **Step 6: Run Activity and related tests**

Run:

```powershell
Set-Location frontend
npm exec -- vitest run src/library/ActivityPage.test.tsx src/library/AddPage.test.tsx src/library/ProfilePage.test.tsx src/library/MediaGrid.test.tsx src/library/MediaViewerPage.test.tsx
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/library/types.ts frontend/src/library/ActivityPage.tsx frontend/src/library/JobIssues.tsx frontend/src/styles/global.css frontend/src/library/ActivityPage.test.tsx
git commit -m "feat: show completed jobs with warnings"
```

---

### Task 13: Run Automated Regression Gates

**Files:**

- Modify only if a targeted test or production build exposes a defect in files
  already listed in Tasks 1–12.

**Interfaces:**

- Validates all prior task interfaces together.
- Produces no Docker image and no remote state change.

- [ ] **Step 1: Run the complete backend suite without coverage**

Run:

```powershell
Set-Location backend
python -m pip install --disable-pip-version-check -e ".[test]"
python -m pytest --no-cov
```

Expected: all backend tests pass; no coverage threshold is evaluated.

- [ ] **Step 2: Verify the installed Instaloader version**

Run:

```powershell
Set-Location backend
python -c "from importlib.metadata import version; actual=version('instaloader'); print(actual); assert actual == '4.15.3'"
```

Expected: output `4.15.3`.

- [ ] **Step 3: Run the complete frontend suite**

Run:

```powershell
Set-Location frontend
npm ci
npm exec -- vitest run
```

Expected: all frontend tests pass without a coverage threshold.

- [ ] **Step 4: Run the frontend production type/build check**

Run:

```powershell
Set-Location frontend
npm run build
```

Expected: TypeScript and Vite build succeed. Do not run a local Docker build.

- [ ] **Step 5: Check the Git worktree**

Run:

```powershell
Set-Location ..
git diff --check
git status --short
```

Expected: no whitespace errors and no uncommitted implementation files. If a
test-driven correction was required, commit only that correction with a
Conventional Commit message before proceeding.

---

### Task 14: Validate on the NAS in an Isolated Diagnostic Deployment

**Files:**

- Create remotely:
  `/vol3/1000/docker-configs/instaloader-webui-diagnostics/releases/${diagnosticCommit}/`
- Create remotely:
  `/vol3/1000/docker-configs/instaloader-webui-diagnostics/data/${diagnosticCommit}/`
- Create remotely:
  `/vol3/1000/docker-configs/instaloader-webui-diagnostics/.env.${diagnosticCommit}`
- Do not modify:
  `/vol3/1000/docker-configs/instaloader-webui/`

**Interfaces:**

- Consumes the committed working tree after Task 13.
- Produces an isolated NAS-only image and a result matrix.
- Does not push, release, or alter production.

- [ ] **Step 1: Resolve production mounts and unused ports read-only**

Run:

```powershell
ssh nas "docker ps --format '{{.Names}} {{.Image}} {{.Ports}}'; ss -ltn"
ssh nas "docker inspect instaloader-webui-web-1 --format '{{range .Mounts}}{{println .Source .Destination}}{{end}}'"
```

If the actual production container name differs, select the running
`z21012101/instaloader-webui:0.1.1` web container from the first command and
inspect that exact name. Confirm its `/data` source resolves under
`/vol3/1000/docker-configs/instaloader-webui/`. These commands must not print
file contents.

- [ ] **Step 2: Archive the committed implementation without `.git` or local data**

Run:

```powershell
$diagnosticCommit = (git rev-parse HEAD).Trim()
$diagnosticArchive = Join-Path ([System.IO.Path]::GetTempPath()) "$diagnosticCommit-instaloader-webui.tar"
git archive --format=tar --output="$diagnosticArchive" HEAD
```

Expected: one local source archive named by the exact commit.

- [ ] **Step 3: Create an isolated release and data tree**

On NAS, validate the root prefix before creating directories. Create the
diagnostics root, upload the archive, and extract it into:

```text
/vol3/1000/docker-configs/instaloader-webui-diagnostics/releases/${diagnosticCommit}/
```

Upload with:

```powershell
ssh nas "set -eu; diagnostic_root='/vol3/1000/docker-configs/instaloader-webui-diagnostics'; case \"`$diagnostic_root\" in /vol3/1000/docker-configs/instaloader-webui-diagnostics) ;; *) exit 1 ;; esac; mkdir -p \"`$diagnostic_root\""
scp "$diagnosticArchive" "nas:/vol3/1000/docker-configs/instaloader-webui-diagnostics/"
ssh nas "set -eu; test ! -e '/vol3/1000/docker-configs/instaloader-webui-diagnostics/releases/$diagnosticCommit'; mkdir -p '/vol3/1000/docker-configs/instaloader-webui-diagnostics/releases/$diagnosticCommit' '/vol3/1000/docker-configs/instaloader-webui-diagnostics/data/$diagnosticCommit/secrets'; tar -xf '/vol3/1000/docker-configs/instaloader-webui-diagnostics/$diagnosticCommit-instaloader-webui.tar' -C '/vol3/1000/docker-configs/instaloader-webui-diagnostics/releases/$diagnosticCommit'"
```

Create:

```text
/vol3/1000/docker-configs/instaloader-webui-diagnostics/data/${diagnosticCommit}/secrets/
```

Copy only `secrets/app_secret_key` and
`secrets/instagram_session.enc` from the resolved production `/data` source,
preserving owner and mode. Do not copy the production database, media, jobs, or
temporary directories. Do not print or hash either secret file.

- [ ] **Step 4: Generate the isolated `.env` without exposing its password**

On NAS, select the first unused port in the inclusive range `18081..18120` by
checking `ss -ltn`. Bind diagnostics to `127.0.0.1`. Generate a 48-character
random administrator password directly into the private `.env` and set:

```dotenv
IW_ADMIN_USERNAME=diagnostic-owner
IW_ADMIN_PASSWORD=${diagnostic_password}
IW_SESSION_COOKIE_SECURE=false
IW_FORWARDED_ALLOW_IPS=127.0.0.1
IW_HTTP_BIND=127.0.0.1
IW_HTTP_PORT=${diagnostic_port}
IW_DATA_ROOT_HOST=/vol3/1000/docker-configs/instaloader-webui-diagnostics/data/${diagnosticCommit}
```

Assign `diagnostic_password` from a 48-character cryptographic random
generator and `diagnostic_port` from the port scan inside the same remote shell.
Use `umask 077` and mode `0600`. The shell command and its output must not
contain either value.

- [ ] **Step 5: Build and start only the diagnostic Compose project**

From the extracted release directory on NAS, run:

```sh
diagnosticCommit="$(basename "$PWD")"
docker compose \
  --project-name instaloader-webui-diagnostics \
  --env-file /vol3/1000/docker-configs/instaloader-webui-diagnostics/.env.${diagnosticCommit} \
  --file compose.yaml \
  --file compose.build.yaml \
  build web
docker compose \
  --project-name instaloader-webui-diagnostics \
  --env-file /vol3/1000/docker-configs/instaloader-webui-diagnostics/.env.${diagnosticCommit} \
  --file compose.yaml \
  --file compose.build.yaml \
  up --detach
```

Expected: only diagnostic web/worker containers and
`instaloader-webui:local` are created. Confirm production container IDs and
start times are unchanged.

- [ ] **Step 6: Queue the diagnostic matrix through the local API**

Run requests from NAS against `127.0.0.1:${diagnostic_port}`. Read the diagnostic
password from the private `.env` inside the process, log in, retain the session
cookie and CSRF token in memory, and never echo either.

Queue:

```text
Profile: katerina.soria
Post: https://www.instagram.com/p/CmzV2H-rrlI/
Regression target: https://www.instagram.com/p/DOqEJyxCRGJ/
```

For the Reel case, first resolve `DOqEJyxCRGJ` metadata. If its typename is a
Reel, queue its canonical `/reel/DOqEJyxCRGJ/` URL. Otherwise select one Reel
shortcode returned by the diagnostic Profile scan and queue its canonical
`/reel/{shortcode}/` URL.

Poll `/api/jobs/{id}` until each job reaches `succeeded`,
`completed_with_warnings`, or `failed`. Do not include Cookie or CSRF headers in
captured output.

- [ ] **Step 7: Verify Story, classification, posters, progress, and logs**

Use authenticated API responses and database-safe fields to record:

| Target | Pass condition |
| --- | --- |
| Profile metadata | username/full name metadata returned |
| Avatar | `/api/profiles/{id}/avatar` returns `image/jpeg` |
| Story | current Story appears as `kind=story`; video poster is excluded from content count |
| Reel | `kind=reel`; video content and poster share position |
| Post | `CmzV2H-rrlI` appears as `kind=post` |
| `CmzV2H-rrlI` | metadata/download succeeds under Instaloader 4.15.3 |
| `DOqEJyxCRGJ` | succeeds or returns complete safe issue fields |
| Progress | exact total after scan; each outcome advances once |
| Logs | no `Cookie`, `sessionid`, `csrftoken`, `igsh=`, or query-bearing Instagram URL |

If `katerina.soria` has no currently visible Stories, report the real-network
Story row as `not observable: no active Story`; do not fabricate a pass. The
automated Story adapter/coordinator tests remain the deterministic evidence.

- [ ] **Step 8: Stop diagnostics and prove production was untouched**

Run:

```sh
diagnosticCommit="$(basename "$PWD")"
docker compose \
  --project-name instaloader-webui-diagnostics \
  --env-file /vol3/1000/docker-configs/instaloader-webui-diagnostics/.env.${diagnosticCommit} \
  --file compose.yaml \
  --file compose.build.yaml \
  stop
```

Do not run `down -v`, delete files, or prune images. Confirm the production
`0.1.1` web and worker containers are still running with their original IDs.

- [ ] **Step 9: Report the release gate**

Report:

- backend test count/result;
- frontend test count/result;
- frontend production build result;
- exact installed Instaloader version;
- every NAS matrix row with evidence;
- all warning/error codes observed;
- whether push, Docker Hub release, and production replacement are recommended.

Stop after the report. Await explicit approval before any push, release, or
production change.
