# Instaloader PyPI Dependency and Worker Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install an exactly pinned Instaloader release from PyPI and reuse process-local Instaloader clients across jobs in the persistent worker.

**Architecture:** A new worker-owned runtime caches one anonymous client and at most one authenticated client keyed by the encrypted session revision. Job-scoped adapters acquire and reconfigure a cached client, while Docker resolves `instaloader==4.15.3` from backend package metadata without requiring a sibling source checkout.

**Tech Stack:** Python 3.12, Instaloader 4.15.3, FastAPI worker, Docker BuildKit, Docker Compose

## Global Constraints

- Pin Instaloader exactly as `instaloader==4.15.3`.
- Reuse clients only within one worker process; do not persist rate-limit state across restarts.
- Keep the worker single-process and sequential.
- Keep Cookie import validation isolated from the cached worker clients.
- Do not add or run unit tests, smoke tests, or coverage for this POC.
- Run Python syntax compilation, Docker build/config validation, diff checks, and code review.

---

### Task 1: Make Instaloader an explicit PyPI dependency

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `docker/Dockerfile`
- Modify: `compose.yaml`
- Modify: `README.md`

**Interfaces:**
- Consumes: PyPI package `instaloader==4.15.3`
- Produces: a repository-local Docker build that installs the pinned wheel into both WebUI services

- [ ] **Step 1: Pin the dependency**

Add the following entry to `[project].dependencies` in
`backend/pyproject.toml`:

```toml
"instaloader==4.15.3",
```

- [ ] **Step 2: Remove the sibling-source wheel build**

Delete the `/build/instaloader` stage commands from `docker/Dockerfile`:

```dockerfile
WORKDIR /build/instaloader
COPY setup.py README.rst LICENSE ./
COPY instaloader/ ./instaloader/
RUN PIP_ROOT_USER_ACTION=ignore \
    python -m pip wheel --no-cache-dir --wheel-dir /wheels .
```

Keep the backend `pip wheel` command so dependency resolution downloads the
exact Instaloader wheel into `/wheels`.

- [ ] **Step 3: Make Docker paths repository-local**

Change frontend and backend copies to:

```dockerfile
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/ ./
COPY backend/ ./
```

Change the Compose build section to:

```yaml
build:
  context: .
  dockerfile: docker/Dockerfile
```

- [ ] **Step 4: Update deployment documentation**

Update `README.md` to state that the image installs pinned Instaloader from
PyPI, remove the required sibling checkout tree, and change the Operations
section from “outer repository context” to “WebUI repository context.”

- [ ] **Step 5: Inspect dependency and Compose metadata**

Run:

```powershell
docker compose config
Select-String -Path backend/pyproject.toml -Pattern '"instaloader==4.15.3"'
```

Expected: Compose resolves `dockerfile: docker/Dockerfile` under this repository
and the exact dependency appears once.

- [ ] **Step 6: Commit the packaging change**

```powershell
git add backend/pyproject.toml docker/Dockerfile compose.yaml README.md
git commit -m "build: install pinned Instaloader from PyPI"
```

---

### Task 2: Add the process-local worker runtime

**Files:**
- Create: `backend/src/instaloader_webui/instagram/worker_runtime.py`

**Interfaces:**
- Consumes: `InstagramSessionStore`, `InstagramSessionSnapshot`, `cookie_dict`
- Produces:
  - `WorkerInstaloaderRuntime.acquire(staging_directory: Path) -> tuple[Instaloader, bool]`
  - `WorkerInstaloaderRuntime.close() -> None`

- [ ] **Step 1: Define cached runtime state**

Create `WorkerInstaloaderRuntime` with these fields:

```python
self._sessions = sessions
self._anonymous_loader: Instaloader | None = None
self._authenticated_loader: Instaloader | None = None
self._authenticated_revision: tuple[str, datetime] | None = None
self._closed = False
```

- [ ] **Step 2: Centralize loader construction**

Create `_build_loader(staging_directory: Path) -> Instaloader` using the current
adapter configuration:

```python
return Instaloader(
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

- [ ] **Step 3: Implement anonymous acquisition**

When `InstagramSessionStore.load()` returns `None`, lazily create the anonymous
loader, set `dirname_pattern` to the current staging directory, and return:

```python
return loader, False
```

- [ ] **Step 4: Implement revision-aware authenticated acquisition**

Use `(snapshot.username, snapshot.imported_at)` as the revision. When it changes:

1. Build a replacement loader.
2. Call `replacement.load_session(snapshot.username, cookie_dict(snapshot.cookies))`.
3. If loading fails, close only the replacement and preserve the cached client.
4. On success, install the replacement and close the stale authenticated client.

Set the selected loader's `dirname_pattern` before returning:

```python
return loader, True
```

- [ ] **Step 5: Implement deterministic shutdown**

Make `close()` idempotent. Mark the runtime closed, close the authenticated
loader, and use `finally` to ensure the anonymous loader is also closed. After
closure, `acquire()` must raise `RuntimeError("Instaloader runtime is closed.")`.

- [ ] **Step 6: Compile the runtime**

Run:

```powershell
python -m compileall -q backend/src/instaloader_webui/instagram/worker_runtime.py
```

Expected: exit code 0.

- [ ] **Step 7: Commit the runtime**

```powershell
git add backend/src/instaloader_webui/instagram/worker_runtime.py
git commit -m "feat: add reusable Instaloader worker runtime"
```

---

### Task 3: Inject the runtime through the worker path

**Files:**
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Modify: `backend/src/instaloader_webui/services/job_runner.py`
- Modify: `backend/src/instaloader_webui/worker.py`

**Interfaces:**
- Consumes: `WorkerInstaloaderRuntime.acquire()` and `.close()`
- Produces: all worker jobs use cached clients while retaining job-scoped adapters and progress callbacks

- [ ] **Step 1: Replace adapter-owned client construction**

Change `PublicInstaloaderAdapter.__init__` to accept
`loader_runtime: WorkerInstaloaderRuntime`, store it, and remove the
`InstagramSessionStore` field.

Rename `_new_loader()` to `_acquire_loader()` and implement it as:

```python
try:
    return self._loader_runtime.acquire(staging_directory)
except InstagramSessionStoreError:
    raise PublicInstagramAdapterError(
        "Instagram session storage is unreadable. "
        "An administrator must re-import the Cookie file."
    ) from None
```

Update the profile, shortcode, and sync entry points to call
`_acquire_loader()`. Remove the duplicated `Instaloader(...)`,
`cookie_dict(...)`, and session loading code from the adapter.

- [ ] **Step 2: Inject the runtime through JobRunner**

Replace `instagram_sessions: InstagramSessionStore` with
`loader_runtime: WorkerInstaloaderRuntime` in `JobRunner.__init__`. Store it and
pass it to every new `PublicInstaloaderAdapter`.

- [ ] **Step 3: Own the runtime in the persistent worker**

After constructing `InstagramSessionStore` in `worker.py`, create:

```python
loader_runtime = WorkerInstaloaderRuntime(instagram_sessions)
```

Pass it to `JobRunner`. In the worker's outer `finally`, close the runtime and
guarantee database disposal:

```python
finally:
    try:
        loader_runtime.close()
    finally:
        engine.dispose()
```

- [ ] **Step 4: Preserve validation isolation**

Confirm `InstagramSessionService._validate_candidate()` remains unchanged and
continues to create and close its own short-lived `Instaloader`.

- [ ] **Step 5: Compile the integrated backend**

Run:

```powershell
python -m compileall -q `
  backend/src/instaloader_webui/instagram/public_adapter.py `
  backend/src/instaloader_webui/instagram/worker_runtime.py `
  backend/src/instaloader_webui/services/job_runner.py `
  backend/src/instaloader_webui/worker.py
```

Expected: exit code 0.

- [ ] **Step 6: Commit the integration**

```powershell
git add backend/src/instaloader_webui/instagram/public_adapter.py `
  backend/src/instaloader_webui/services/job_runner.py `
  backend/src/instaloader_webui/worker.py
git commit -m "refactor: reuse Instaloader clients across worker jobs"
```

---

### Task 4: Verify packaging and lifecycle changes

**Files:**
- Review: all files modified in Tasks 1–3

**Interfaces:**
- Consumes: repository-local Docker context and reusable worker runtime
- Produces: build evidence and a clean reviewed branch

- [ ] **Step 1: Validate the final diff**

Run:

```powershell
git diff --check HEAD~3 HEAD
git status --short
```

Expected: no whitespace errors and a clean worktree.

- [ ] **Step 2: Build the Docker image**

Run:

```powershell
docker compose build
```

Expected: the backend wheel stage resolves `instaloader==4.15.3`, both services
reference `instaloader-webui:local`, and the build succeeds without reading a
sibling source checkout.

- [ ] **Step 3: Inspect the image configuration without starting services**

Run:

```powershell
docker image inspect instaloader-webui:local --format '{{json .Config.Cmd}}'
```

Expected: the configured command remains the Uvicorn WebUI entrypoint. Do not
start containers or run a smoke test.

- [ ] **Step 4: Request independent code review**

Review for:

- exact dependency pin and repository-local Docker paths;
- no remaining sibling-source dependency;
- cached anonymous and authenticated loader behavior;
- safe session revision replacement;
- explicit close behavior;
- unchanged isolated Cookie validation; and
- no new concurrent access assumptions.

- [ ] **Step 5: Address Important or Medium findings**

Apply only reviewer-requested fixes within the approved design, repeat syntax
compilation and `git diff --check`, and commit with a focused conventional
message.

