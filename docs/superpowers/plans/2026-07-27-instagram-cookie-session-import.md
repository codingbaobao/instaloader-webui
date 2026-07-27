# Instagram Cookie Session Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one encrypted global Instagram browser session that the administrator can import from Netscape `cookies.txt` and that every subsequent Instaloader worker job uses without a container restart.

**Architecture:** A pure Netscape parser feeds an encrypted file-backed session store under `/data/secrets`. A validation service verifies candidate Cookies through an isolated Instaloader context before atomic replacement; FastAPI exposes safe status/import/delete endpoints, and the worker loads a fresh session snapshot for each job. React adds a dedicated Settings card with Chrome/Edge export guidance and never receives Cookie values.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, cryptography/Fernet, Instaloader 4.15.x local checkout, React 19, TypeScript, Vite, Docker Compose.

## Global Constraints

- Accept only Netscape-format UTF-8 `cookies.txt`; do not accept JSON or Instaloader pickle sessions.
- Limit the uploaded file to 256 KiB and retain only Instagram-domain Cookie records.
- Require non-empty `sessionid` and `csrftoken`; never log or return Cookie names, values, request bodies, or ciphertext.
- Validate a candidate session before it atomically replaces the current encrypted session.
- Store one global encrypted session at `/data/secrets/instagram_session.enc`.
- Derive the encryption key from the internally generated `/data/secrets/app_secret_key`; do not reintroduce `IW_APP_SECRET_KEY`.
- Web and worker share `/data`; each new Instagram job reads the current session without requiring a restart.
- Keep this milestone public-profile/public-media only; do not add password login, 2FA, private downloads, Stories, or Tagged.
- All delete operations use the existing confirmation dialog.
- Recommend **Get cookies.txt LOCALLY** for Chrome and Edge and link both its Chrome Web Store page and source repository.
- Per the existing POC instruction, do not add, modify, or run unit, integration, smoke, or E2E tests. Verification is limited to syntax/static checks, frontend production build, Compose/image build, review, and administrator-performed manual acceptance.

---

## File Map

### New backend files

- `backend/src/instaloader_webui/instagram/cookie_file.py` — immutable Cookie record and strict Netscape parser.
- `backend/src/instaloader_webui/instagram/session_store.py` — encrypted snapshot/status models and atomic file store.
- `backend/src/instaloader_webui/instagram/session_service.py` — Instaloader candidate validation and import/remove orchestration.
- `backend/src/instaloader_webui/instagram/errors.py` — safe classification of Instaloader access failures.
- `backend/src/instaloader_webui/api/routes/instagram_session.py` — safe GET/POST/DELETE API.

### New frontend file

- `frontend/src/library/InstagramSessionCard.tsx` — status, instructions, upload, replace, and confirmed removal UI.

### Existing files changed

- `backend/pyproject.toml` — add multipart form parser dependency.
- `backend/src/instaloader_webui/main.py` — construct the store/service and register the new router.
- `backend/src/instaloader_webui/worker.py` — load the internal key/store and pass it to the job runner.
- `backend/src/instaloader_webui/api/dependencies.py` — expose store/service dependencies.
- `backend/src/instaloader_webui/api/library_dtos.py` — safe Instagram session status DTO.
- `backend/src/instaloader_webui/api/middleware.py` — permit the bounded Cookie import body while retaining the 16 KiB limit elsewhere.
- `backend/src/instaloader_webui/services/job_runner.py` — pass the session store into adapters.
- `backend/src/instaloader_webui/instagram/public_adapter.py` — load current Cookies for each loader and use safe error classification.
- `frontend/src/app/api.ts` — send `FormData` without forcing JSON headers.
- `frontend/src/library/api.ts` — Instagram session API helpers.
- `frontend/src/library/types.ts` — safe status type.
- `frontend/src/library/SettingsPage.tsx` — render the new card.
- `frontend/src/styles/global.css` — responsive card/upload/status styling.
- `README.md` — operator and browser export instructions.

---

### Task 1: Strict Cookie Parser and Encrypted Session Store

**Files:**

- Create: `backend/src/instaloader_webui/instagram/cookie_file.py`
- Create: `backend/src/instaloader_webui/instagram/session_store.py`

**Interfaces:**

- Produces:
  - `InstagramCookie(domain: str, path: str, secure: bool, expires_at: int, name: str, value: str)`
  - `CookieFileError`
  - `parse_netscape_cookie_file(payload: bytes) -> tuple[InstagramCookie, ...]`
  - `cookie_dict(cookies: tuple[InstagramCookie, ...]) -> dict[str, str]`
  - `InstagramSessionSnapshot(username, cookies, imported_at, last_validated_at)`
  - `InstagramSessionStatus(configured, username, imported_at, last_validated_at)`
  - `InstagramSessionStore(data_root: Path, app_secret: AppSecret)`
  - `InstagramSessionStore.load() -> InstagramSessionSnapshot | None`
  - `InstagramSessionStore.status() -> InstagramSessionStatus`
  - `InstagramSessionStore.replace(snapshot) -> InstagramSessionStatus`
  - `InstagramSessionStore.delete() -> None`

- [ ] **Step 1: Implement immutable Netscape records and strict parsing**

Create a focused parser with the public shape:

```python
MAXIMUM_COOKIE_FILE_BYTES = 256 * 1024
REQUIRED_COOKIE_NAMES = frozenset({"sessionid", "csrftoken"})

@dataclass(frozen=True, slots=True)
class InstagramCookie:
    domain: str
    path: str
    secure: bool
    expires_at: int
    name: str
    value: str = field(repr=False)

def parse_netscape_cookie_file(payload: bytes) -> tuple[InstagramCookie, ...]:
    """Return validated Instagram Cookie records or raise CookieFileError."""

def cookie_dict(cookies: tuple[InstagramCookie, ...]) -> dict[str, str]:
    return {cookie.name: cookie.value for cookie in cookies}
```

Decode `utf-8-sig`, treat `#HttpOnly_` as a domain prefix rather than a
comment, split non-comment records into exactly seven tab-separated fields,
normalize the domain with `casefold()`, and accept only `instagram.com`,
`.instagram.com`, or a hostname ending in `.instagram.com`. Reject control
characters, invalid boolean/expiry fields, empty names/values for required
Cookies, and duplicate names with conflicting values. Sort accepted records by
`(name, domain, path)` before returning an immutable tuple.

- [ ] **Step 2: Implement encrypted snapshots and atomic file replacement**

Use a versioned JSON payload encrypted with `cryptography.fernet.Fernet`.
Derive the Fernet key with HKDF-SHA256 without persisting a second plaintext
secret:

```python
derived_key = HKDF(
    algorithm=hashes.SHA256(),
    length=32,
    salt=None,
    info=b"instaloader-webui:instagram-session:v1",
).derive(app_secret.value.encode("utf-8"))
fernet = Fernet(base64.urlsafe_b64encode(derived_key))
```

`InstagramSessionStore` must:

- use `<data_root>/secrets/instagram_session.enc`;
- decrypt and validate the versioned JSON schema on every `load()`;
- expose only safe metadata through `status()`;
- write to a same-directory temporary file with mode `0600`, flush and
  `os.fsync`, then call `os.replace`;
- `fsync` the parent directory on POSIX after replacement;
- keep the old file untouched until encryption and temporary-file persistence
  succeed;
- delete idempotently without following symlinks;
- never put Cookie values into `repr`, exceptions, or logs.

- [ ] **Step 3: Run allowed backend checks**

Run:

```powershell
python -m compileall -q backend/src/instaloader_webui/instagram
git diff --check
```

Expected: both commands exit `0`; do not run pytest or other automated tests.

- [ ] **Step 4: Review and commit Task 1**

Inspect the exact diff for plaintext persistence, unsafe deserialization,
domain matching, `#HttpOnly_` handling, and atomic replacement.

```powershell
git add backend/src/instaloader_webui/instagram/cookie_file.py backend/src/instaloader_webui/instagram/session_store.py
git commit -m "feat: store encrypted Instagram sessions"
```

---

### Task 2: Candidate Validation and Session API

**Files:**

- Create: `backend/src/instaloader_webui/instagram/session_service.py`
- Create: `backend/src/instaloader_webui/api/routes/instagram_session.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/src/instaloader_webui/main.py`
- Modify: `backend/src/instaloader_webui/api/dependencies.py`
- Modify: `backend/src/instaloader_webui/api/library_dtos.py`
- Modify: `backend/src/instaloader_webui/api/middleware.py`

**Interfaces:**

- Consumes Task 1 parser/store interfaces.
- Produces:
  - `InstagramSessionImportError(code: str, message: str, status_code: int)`
  - `InstagramSessionService.status()`
  - `InstagramSessionService.import_netscape(payload: bytes, now: datetime)`
  - `InstagramSessionService.remove()`
  - `GET|POST|DELETE /api/settings/instagram-session`
  - `InstagramSessionStatusResponse`

- [ ] **Step 1: Add multipart support and a path-specific body limit**

Add:

```toml
"python-multipart>=0.0.20,<1",
```

to backend runtime dependencies.

Keep `MAXIMUM_REQUEST_BODY_BYTES = 16 * 1024` for every existing route. Add
`MAXIMUM_INSTAGRAM_SESSION_IMPORT_BYTES = 272 * 1024` for
`POST /api/settings/instagram-session`, allowing multipart framing around the
256 KiB file while still bounding the full request. Make
`RequestBodyLimitMiddleware` choose its limit from `scope["path"]` and
`scope["method"]`; no other route receives the larger limit.

- [ ] **Step 2: Implement isolated validation and import orchestration**

Create immutable
`InstagramSessionImportError(status_code: int, code: str, message: str)` and
`InstagramSessionService`. Its exact public methods are
`status() -> InstagramSessionStatus`,
`import_netscape(payload: bytes, now: datetime) -> InstagramSessionStatus`, and
`remove() -> None`.

Validation must instantiate an isolated loader with `sleep=False`,
`quiet=True`, `max_connection_attempts=3`, and `request_timeout=20`. Apply the
candidate through the public `Instaloader.load_session("cookie-import",
cookie_dict(cookies))` API. Do not call `Instaloader.test_login()`: the bundled
implementation catches `ConnectionException` and returns `None`, which would
collapse rate limiting and network failures into “expired session.” Instead,
run its underlying login-check query directly so exceptions remain
classifiable:

```python
response = loader.context.graphql_query(
    "d6f4427fbe92d846298cf93df0b937d3",
    {},
)
user = response.get("data", {}).get("user")
username = user.get("username") if isinstance(user, dict) else None
```

Require a non-empty username and close the loader in `finally`.

Map parser failures to `400 invalid_cookie_file`; a missing login identity to
`422 instagram_session_invalid`; challenge/checkpoint to
`422 instagram_challenge_required`; `429` to
`429 instagram_rate_limited`; and other connection failures to
`502 instagram_unavailable`. Only construct and store an
`InstagramSessionSnapshot` after validation, setting `username` to the detected
username, `cookies` to the parsed immutable tuple, and both timestamps to the
supplied `now`.

- [ ] **Step 3: Add safe DTO, dependencies, and routes**

Add the immutable DTO:

```python
class InstagramSessionStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    configured: bool
    username: str | None
    imported_at: datetime | None
    last_validated_at: datetime | None
```

During FastAPI lifespan, construct one `InstagramSessionStore` from
`resolved.data_root` and `app_secret`, then one `InstagramSessionService`;
store both on `app.state`. Add typed dependency getters.

The new router must:

- require `require_password_change_complete` for GET;
- require `require_csrf` for POST and DELETE;
- read the uploaded `UploadFile` in bounded chunks and reject more than
  256 KiB before calling the service;
- convert `InstagramSessionImportError` to the existing `ApiError`;
- return only `InstagramSessionStatusResponse`;
- make DELETE idempotent.

Register the router in `main.py`.

- [ ] **Step 4: Run allowed backend/API checks**

Run:

```powershell
python -m compileall -q backend/src/instaloader_webui
git diff --check
```

Expected: exit `0`; do not issue a live Instagram request and do not run
automated tests.

- [ ] **Step 5: Review and commit Task 2**

Review authentication/CSRF dependencies, upload bounds, safe error payloads,
old-session preservation, and absence of Cookie values from DTOs.

```powershell
git add backend/pyproject.toml backend/src/instaloader_webui/api backend/src/instaloader_webui/instagram/session_service.py backend/src/instaloader_webui/main.py
git commit -m "feat: expose Instagram session import API"
```

---

### Task 3: Worker Session Loading and Accurate Instagram Errors

**Files:**

- Create: `backend/src/instaloader_webui/instagram/errors.py`
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Modify: `backend/src/instaloader_webui/services/job_runner.py`
- Modify: `backend/src/instaloader_webui/worker.py`

**Interfaces:**

- Consumes `InstagramSessionStore.load()` from Task 1.
- Produces:
  - `classify_instaloader_error(error, *, session_configured, target) -> str`
  - a required `instagram_sessions: InstagramSessionStore` keyword argument on
    `PublicInstaloaderAdapter`
  - a required `instagram_sessions: InstagramSessionStore` keyword argument on
    `JobRunner`

- [ ] **Step 1: Implement concise exception-chain classification**

Walk `__cause__` and `__context__` without cycles. Detect exception classes and
case-folded messages, but never return the original message. Return fixed text:

```python
RATE_LIMITED = "Instagram rate limited this server. Try again later."
CHALLENGE = "Instagram requires account verification. Refresh the imported Cookie after resolving the challenge in a browser."
SESSION_REJECTED = "The imported Instagram session expired or was rejected. Import a fresh Cookie file."
ANONYMOUS_REJECTED = "Instagram denied anonymous access. Import an Instagram Cookie file in Settings."
PROFILE_NOT_FOUND = "This Instagram profile was not found or is inaccessible."
MEDIA_NOT_FOUND = "This Instagram media item was not found or is inaccessible."
TRANSIENT = "Instagram could not be reached. Try again later."
```

Prioritize challenge/checkpoint, then `429`, then login/`401`/`403`, then
not-found/private types, then transient connection failures. Select
`SESSION_REJECTED` versus `ANONYMOUS_REJECTED` from `session_configured`.

- [ ] **Step 2: Load a fresh encrypted snapshot for every adapter loader**

Add `instagram_sessions` to `PublicInstaloaderAdapter.__init__`. In
`_new_loader`, create the existing configured Instaloader object, call
`instagram_sessions.load()`, and when present call:

```python
loader.load_session(snapshot.username, cookie_dict(snapshot.cookies))
```

If decryption/schema validation fails, raise a concise
`PublicInstagramAdapterError` that tells the administrator to re-import the
Cookie. Do not silently fall back to anonymous access when an encrypted file is
present but unreadable.

Replace the two generic “unavailable or private” handlers and the generic media
handler with `classify_instaloader_error`. Keep explicit rejection of
`profile.is_private`, with text stating that private profiles are not supported
by this POC even if the imported account can view them.

- [ ] **Step 3: Wire the shared store into the worker**

In `worker.py`, call `load_or_create_app_secret(settings.data_root)`, construct
`InstagramSessionStore`, and pass it to `JobRunner`. Add the corresponding
constructor field and adapter argument in `job_runner.py`.

The web and worker must derive the same key from the shared
`/data/secrets/app_secret_key`; do not cache a decrypted snapshot across jobs.

- [ ] **Step 4: Run allowed backend checks**

Run:

```powershell
python -m compileall -q backend/src/instaloader_webui
git diff --check
```

Expected: exit `0`; do not run a live profile/media download yet.

- [ ] **Step 5: Review and commit Task 3**

Review constructor call sites, session refresh timing, corrupted-store
behavior, private-profile guard, and error-message non-disclosure.

```powershell
git add backend/src/instaloader_webui/instagram/errors.py backend/src/instaloader_webui/instagram/public_adapter.py backend/src/instaloader_webui/services/job_runner.py backend/src/instaloader_webui/worker.py
git commit -m "feat: authenticate Instaloader worker jobs"
```

---

### Task 4: Settings UI and Browser Export Guidance

**Files:**

- Create: `frontend/src/library/InstagramSessionCard.tsx`
- Modify: `frontend/src/app/api.ts`
- Modify: `frontend/src/library/api.ts`
- Modify: `frontend/src/library/types.ts`
- Modify: `frontend/src/library/SettingsPage.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**

- Consumes Task 2's safe status DTO and endpoints.
- Produces:
  - `InstagramSessionStatus` TypeScript type.
  - `getInstagramSession(signal?)`
  - `importInstagramSession(file, csrfToken)`
  - `removeInstagramSession(csrfToken)`
  - `<InstagramSessionCard session={session} />`

- [ ] **Step 1: Teach the shared API client to send FormData**

Keep JSON behavior unchanged. When `options.body instanceof FormData`, pass it
directly to `fetch` and do not set `Content-Type`; the browser must generate
the multipart boundary. Continue sending `X-CSRF-Token` and credentials.

```typescript
const formData = options.body instanceof FormData;
if (options.body !== undefined && !formData) {
  headers.set("Content-Type", "application/json");
}
const body = formData
  ? options.body
  : options.body === undefined
    ? undefined
    : JSON.stringify(options.body);
```

- [ ] **Step 2: Add immutable status type and API helpers**

```typescript
export type InstagramSessionStatus = Readonly<{
  configured: boolean;
  username: string | null;
  imported_at: string | null;
  last_validated_at: string | null;
}>;
```

POST helper appends the file under field name `cookie_file`; GET accepts an
`AbortSignal`; DELETE supplies CSRF and returns the safe status DTO.

- [ ] **Step 3: Build the Instagram session card**

The card must fetch current status on mount and render:

- why a login session may be required for public content;
- exact extension name **Get cookies.txt LOCALLY**;
- Chrome Web Store link:
  `https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc`;
- source link:
  `https://github.com/kairi003/Get-cookies.txt-LOCALLY`;
- text that Edge can install the Chrome Web Store extension;
- four export steps from the design;
- warning that the file is account-equivalent and should be deleted after
  import;
- `.txt` file input and `Validate and import`/`Replace Cookie file` action;
- `Connected as @username` and safe timestamps when configured;
- API error and success notices;
- `Remove session` opening `ConfirmDialog`, with deletion only from
  `onConfirm`.

Disable upload while validating, reject a missing or non-`.txt` selection
client-side without reading its contents, clear the file input after success,
and suggest **Sync all profiles now** rather than enqueueing automatically.

- [ ] **Step 4: Integrate and style the card**

Render the card from `SettingsPage` beneath the existing synchronization
controls. Add responsive styles scoped under `.instagram-session-card`,
`.instagram-session-status`, `.cookie-import-form`, and
`.cookie-import-instructions`. Preserve the current narrow/mobile layout and
visible focus treatment.

- [ ] **Step 5: Run the frontend production build**

Run:

```powershell
npm run build
git diff --check
```

from `frontend/` for the first command and repository root for the second.
Expected: TypeScript and Vite exit `0`; do not run frontend unit or E2E tests.

- [ ] **Step 6: Review and commit Task 4**

Review multipart behavior, external link labels, no Cookie disclosure, button
disabled states, confirmed deletion, and mobile layout.

```powershell
git add frontend/src/app/api.ts frontend/src/library frontend/src/styles/global.css
git commit -m "feat: add Instagram Cookie import UI"
```

---

### Task 5: Packaging, Documentation, and Manual Acceptance Handoff

**Files:**

- Modify: `README.md`
- Verify: `compose.yaml`
- Verify: `docker/Dockerfile`

**Interfaces:**

- Consumes all earlier tasks.
- Produces a rebuilt two-service image and operator instructions; no new
  environment variables or Compose services.

- [ ] **Step 1: Document session import and lifecycle**

Update README to replace the anonymous-only limitation with:

- public content may still require an authenticated Instagram session;
- exact Chrome/Edge extension and links;
- Netscape export/import steps;
- `/data/secrets/instagram_session.enc` storage;
- encrypted session and `app_secret_key` must be backed up/restored together;
- replacement/removal behavior;
- re-export guidance after Instagram logout, expiry, challenge, or rejection;
- no password/2FA/private/Story/Tagged support in this milestone.

- [ ] **Step 2: Validate Compose and build the production image**

Run:

```powershell
docker compose config --quiet
docker compose build
```

Expected: both exit `0`; the local backend wheel includes
`python-multipart`, and web/worker still use the same image and `/data` mount.
These are packaging checks, not smoke tests.

- [ ] **Step 3: Prepare administrator-performed manual acceptance**

Do not request or persist the Cookie outside the WebUI. Ask the administrator
to:

1. open Settings;
2. import the extension-produced Instagram `cookies.txt`;
3. confirm `Connected as @<username>`;
4. delete the exported local file;
5. choose **Sync all profiles now**;
6. verify `https://www.instagram.com/oioo712/` advances through Activity and
   produces saved media;
7. report any challenge, rate-limit, or expired-session message exactly as
   displayed, without sharing Cookie contents.

If manual acceptance cannot be performed in the coding environment, report it
as an explicit residual verification gap rather than claiming live download
success.

- [ ] **Step 4: Run final allowed static checks**

Run:

```powershell
git diff --check
git status --short --branch
```

Expected: no whitespace errors and only intended README changes before commit.
Do not run automated test suites.

- [ ] **Step 5: Review and commit Task 5**

Review documentation against the implemented UI/API and confirm Compose exposes
no Cookie or new secret environment variable.

```powershell
git add README.md
git commit -m "docs: explain Instagram session import"
```

---

## Final Review Gate

After Task 5:

1. Request a whole-range code review from the pre-feature commit through HEAD.
2. Fix all Critical and Important findings in one bounded follow-up task.
3. Re-review only the fix diff.
4. Run fresh allowed verification: backend compile, frontend production build,
   `docker compose config --quiet`, `docker compose build`, `git diff --check`,
   and clean status.
5. Report manual live Instagram acceptance separately; never claim it passed
   unless the administrator actually imported a valid Cookie and observed a
   successful download.
