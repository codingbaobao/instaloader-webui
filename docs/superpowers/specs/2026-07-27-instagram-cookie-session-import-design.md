# Instagram Cookie Session Import Design

Date: 2026-07-27

## Goal

Allow the single administrator to import an authenticated Instagram browser
session into the WebUI so the existing Instaloader worker can reliably access
public profiles and public post, reel, and TV URLs when Instagram rejects
anonymous GraphQL requests.

This milestone adds one global Instagram session. It does not add Instagram
username/password login, 2FA, multiple Instagram identities, private-profile
support, Stories, or Tagged downloads.

## Motivation

The public-only anonymous POC receives `403 Forbidden` from Instagram's
GraphQL endpoint and `429 Too Many Requests` from the legacy profile endpoint.
The bundled Instaloader version therefore cannot reliably resolve even a public
profile without an authenticated session. Public content visibility does not
guarantee anonymous access to Instagram's internal APIs.

The existing adapter also maps all Instaloader profile failures to
“unavailable or private,” which hides access denial, rate limiting, and expired
session failures. This design separates those outcomes.

## Chosen Approach

The WebUI accepts a Netscape-format `cookies.txt` exported from a browser
extension. It parses the upload in memory, retains only Instagram-domain
cookies, validates the candidate session with Instaloader, then encrypts and
atomically stores a normalized session document.

The original uploaded file is never persisted. Instaloader pickle session files
are not accepted because unpickling user-controlled data can execute arbitrary
code. Generic JSON cookie exports are deferred because extensions use
incompatible schemas.

### Recommended Browser Extension

The Settings page recommends **Get cookies.txt LOCALLY**:

- Chrome Web Store:
  `https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc`
- Source:
  `https://github.com/kairi003/Get-cookies.txt-LOCALLY`

Microsoft Edge users install the same extension from the Chrome Web Store,
which Edge supports. The UI tells the administrator to:

1. Sign in to `https://www.instagram.com/` in Chrome or Edge.
2. Keep an Instagram tab active.
3. Export only the current Instagram domain in Netscape format.
4. Upload the resulting `.txt` file to the WebUI.

The UI warns that the exported file is equivalent to an account credential and
should be deleted from the device after a successful import.

## Trust and Storage Boundary

The import API is available only to the authenticated administrator, requires
the existing CSRF token, and accepts `multipart/form-data` with one bounded text
file.

The parser:

- rejects uploads larger than 256 KiB;
- accepts UTF-8 text with an optional BOM;
- requires valid Netscape tab-separated cookie records;
- ignores comments and blank lines;
- accepts only `instagram.com`, `.instagram.com`, and their subdomains;
- rejects control characters, duplicate conflicting cookie names, malformed
  expiry values, and records with empty names;
- requires non-empty `sessionid` and `csrftoken` cookies;
- retains other valid Instagram cookies because Instaloader may need device,
  routing, and identity cookies;
- never includes Cookie values in validation errors, API responses, or logs.

The normalized session is stored at:

```text
/data/secrets/instagram_session.enc
```

The document contains the validated Instagram username, normalized Cookie
records, import time, and last validation time. It is encrypted with Fernet
using a key derived from the internally generated
`/data/secrets/app_secret_key`. The application secret remains internal; no
`IW_APP_SECRET_KEY` environment variable is reintroduced.

Writes use a same-directory temporary file, `fsync`, restrictive permissions,
and atomic replacement. A failed parse, validation, encryption, or write leaves
the previously stored session unchanged. The session status endpoint never
returns Cookie names or values.

## Backend Components

### Cookie parser

A focused parser converts Netscape records into immutable normalized Cookie
objects. It has no filesystem, database, FastAPI, or Instaloader dependencies.

### Encrypted session store

A session store owns encryption, decryption, atomic file replacement, status
metadata, and deletion. It reads the internal application secret at
application/worker startup and does not expose decrypted values outside its
loader-facing API.

Web and worker containers share `/data`, so both processes see the same
encrypted session file. Removing or replacing a session does not require a
container restart.

### Instaloader session factory

The public adapter receives a session loader rather than reading Cookie files
directly. Whenever it creates an Instaloader instance, it:

1. loads the current encrypted session snapshot if present;
2. applies the normalized Cookie dictionary through Instaloader's session API;
3. sets the validated Instagram username so Instaloader treats the context as
   authenticated;
4. otherwise leaves the loader anonymous.

Each job takes a fresh snapshot when it starts. A session replacement affects
the next job and never mutates a loader already executing.

### Validation service

The import route validates a candidate in memory before storage:

1. parse and normalize the uploaded Netscape file;
2. build an isolated Instaloader context with bounded connection attempts and
   timeout;
3. apply the candidate Cookie set;
4. call Instaloader's login check;
5. require a returned Instagram username;
6. encrypt and atomically replace the stored session;
7. return status metadata only.

Invalid, expired, challenged, rate-limited, or rejected candidates do not
replace the existing session. Validation does not enqueue downloads
automatically.

## API

All endpoints use the existing API envelope and administrator authentication.

### `GET /api/settings/instagram-session`

Returns:

- `configured`;
- validated Instagram `username` when configured;
- `imported_at`;
- `last_validated_at`.

It never returns Cookie names, values, paths, domains, or ciphertext.

### `POST /api/settings/instagram-session`

Requires CSRF and a multipart `.txt` upload. It parses, validates, and atomically
stores the candidate. Success returns the same safe status DTO.

Expected failure categories:

- invalid or unsupported cookie file;
- required Instagram cookies missing;
- Instagram session expired or not logged in;
- Instagram challenge/checkpoint required;
- Instagram temporarily rate-limited;
- Instagram network request failed;
- encrypted session storage failed.

### `DELETE /api/settings/instagram-session`

Requires CSRF and removes the encrypted session file. Missing state is
idempotent. The frontend always presents a confirmation dialog before calling
this endpoint.

## Error Classification

The public adapter stops describing every Instaloader exception as private or
unavailable. User-facing job errors distinguish:

- no configured session while Instagram rejects anonymous access;
- configured session expired or logged out;
- Instagram challenge/checkpoint required;
- Instagram rate limited the server;
- public target not found;
- target is private or inaccessible to the imported account;
- transient Instagram/network failure.

Errors remain concise and do not include request URLs containing parameters,
Cookie data, response bodies, local paths, or tracebacks.

## Frontend

The existing Settings page gains a separate **Instagram session** card below
the synchronization controls.

When no session is configured, it shows:

- why a session is needed even for public profiles;
- the recommended extension name;
- links to the Chrome Web Store and open-source repository;
- Chrome/Edge export instructions;
- a warning to export only Instagram cookies and delete the local file after
  import;
- a `.txt` file picker and **Validate and import** button.

When configured, it additionally shows:

- `Connected as @username`;
- import and validation timestamps;
- **Replace Cookie file**;
- **Remove session**, guarded by the shared confirmation dialog.

Upload progress is a local request state. The UI shows separate parsing,
validation, rate-limit, and expired-session errors using the API's safe
messages. After a successful import it advises the administrator to use the
existing **Sync all profiles now** action. It does not automatically enqueue
jobs.

## Deployment and Documentation

No new environment variable or Compose service is added. The existing web and
worker services already share `/data`, which contains both the encryption key
and encrypted Instagram session.

The README documents:

- why public profiles may still require an authenticated session;
- supported Netscape format and the recommended extension;
- Chrome and Edge import steps;
- `/data/secrets/instagram_session.enc`;
- backup implications;
- session replacement/removal;
- the need to re-export after Instagram logout or expiry.

The session ciphertext and application secret must be backed up together.
Restoring only one makes the session unusable.

## Verification

Following the existing POC constraint, this milestone does not add or run unit,
integration, smoke, or E2E tests.

Implementation verification consists of:

- backend static/type-oriented checks already available without executing the
  automated test suite;
- frontend TypeScript production build;
- `docker compose config`;
- Docker image rebuild when the local engine is available;
- manual acceptance by importing an administrator-provided Instagram
  `cookies.txt`, confirming the detected username, then retrying the known
  public profile `https://www.instagram.com/oioo712/`.

No Cookie fixture containing a real session is committed to the repository.

## Acceptance Criteria

The milestone is complete when:

- the administrator can import a Netscape Instagram Cookie file from Settings;
- invalid or unverified candidates cannot replace a working stored session;
- the API and logs never expose Cookie values;
- web and worker use one encrypted global session from shared `/data`;
- a worker restart is not required after import or removal;
- authenticated Instaloader requests are used for subsequent jobs;
- session status and removal are available in the UI;
- deletion requires confirmation;
- the UI recommends Get cookies.txt LOCALLY for both Chrome and Edge;
- the known public profile can be retried manually after a valid Cookie import;
- anonymous denial, expiry, rate limiting, not-found, and private access errors
  are no longer collapsed into one misleading message.
