# Instaloader WebUI

Instaloader WebUI is a personal, public-Instagram library POC. It provides a
single-administrator FastAPI and React application plus a persistent worker that
downloads public Instagram media through an exactly pinned Instaloader package
from PyPI.

This POC downloads public Instagram content only. Instagram can nevertheless
require an authenticated session to access public profiles or media. An
administrator can import one browser session through the WebUI; it is encrypted
at rest and used by new worker jobs without a container restart. This milestone
does not support Instagram password entry, two-factor authentication, private
profiles or media, Stories, or Tagged content.

## Docker Compose deployment

Requirements: Docker Engine and Docker Compose v2. Compose builds entirely from
the published `z21012101/instaloader-webui` image. The image contains an
exact Instaloader version from PyPI, so no sibling Instaloader source checkout
is required.

From `instaloader-webui/`:

1. Copy the environment template and set the bootstrap administrator.

   ```sh
   cp .env.example .env
   ```

   Set `IW_ADMIN_USERNAME` and an initial `IW_ADMIN_PASSWORD`. The password is
   used only to create the first administrator; after the account exists,
   changing these bootstrap values does not reset it.

2. Choose the persistent host directory in `IW_DATA_ROOT_HOST`. It is mounted
   at `/data` in both services. On Linux, create it and grant its ownership to
   the image's non-root runtime user (`10001:10001`):

   ```sh
   mkdir -p /your/chosen/path
   sudo chown 10001:10001 /your/chosen/path
   ```

3. Pull and start the two-service deployment.

   ```sh
   docker compose pull
   docker compose up -d
   ```

   `web` serves the UI at `http://host-address:8080` by default, while `worker`
   uses the exact same image to process downloads and scheduled profile syncs.
   The worker waits for the web health check before starting so the web service
   completes the initial migration first. Set `IW_HTTP_BIND=127.0.0.1` when a
   same-host reverse proxy is the only intended client, and use `IW_HTTP_PORT`
   to change the host port.

Both services are non-root, read-only outside `/data`, drop Linux capabilities,
and use a small writable `/tmp`. Do not run multiple worker replicas against
the POC database.

Set `IW_IMAGE` in `.env` to pin a release instead of following `latest`:

```text
IW_IMAGE=z21012101/instaloader-webui:0.1.0
```

### Building from source

For local development or validation, apply the build override explicitly:

```sh
docker compose \
  --file compose.yaml \
  --file compose.build.yaml \
  up -d --build
```

This builds `instaloader-webui:local` from `docker/Dockerfile`. Commands that
operate on this local deployment must include both Compose files.

## Using the public library and Instagram session

1. Sign in with the bootstrap administrator and complete the initial password
   confirmation.
2. Open **Add** and paste a direct public Instagram profile, post, reel, or TV
   URL. A post/reel/TV link queues one media download; a profile URL tracks that
   profile and queues its first sync.
3. Open **Activity** to follow queued and running work. It reports the current
   progress message and reaches **succeeded** or **failed** when the worker has
   finished. Activity refreshes automatically every ten seconds; select
   **Refresh** to poll immediately.
4. Open **Profiles** to browse a tracked account's saved posts and reels, or
   open an item to view its downloaded assets, caption, and original link.
5. In **Settings**, set the profile sync interval in minutes or queue an
   immediate sync of all active profiles. The default is 360 minutes.

### Managing profile sync

- **Stop Sync** excludes a profile from scheduled syncs, **Sync All**, and
  **Sync Now**. If its sync is already running, the worker completes the
  current post or reel before it stops and does not start another item.
- **Resume Sync** makes the profile eligible for future scheduled syncs again,
  but does not queue work immediately. Use **Sync Now** after resuming when an
  immediate sync is wanted.
- A profile first discovered through a single-media URL starts with sync
  stopped by default.
- Re-adding a complete post or reel shortcode skips its download. If any saved
  asset is missing, the worker repairs the item by downloading the missing
  files instead.

### Import an Instagram browser session

Import a session only through **Settings**. Do not paste Cookie values into a
terminal, configuration file, support request, or chat.

1. In Chrome or Edge, install [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc). It is open source at [kairi003/Get-cookies.txt-LOCALLY](https://github.com/kairi003/Get-cookies.txt-LOCALLY); Edge can install this Chrome Web Store extension.
2. Sign in to [instagram.com](https://www.instagram.com/) in that browser and
   keep an Instagram tab active.
3. Use the extension to export only the current Instagram domain in **Netscape**
   format, producing a `cookies.txt`/`.txt` file.
4. In **Settings**, choose the exported `.txt` file and select **Validate and
   import**. The server validates it before storing it; a successful import
   displays `Connected as @<username>`.
5. Delete the exported local file after a successful import. It is equivalent
   to an account credential.

The WebUI stores one encrypted session at
`/data/secrets/instagram_session.enc`. Each new worker job reads the current
stored session, so a successful import or replacement applies to subsequent
jobs without restarting containers. Importing another valid file replaces the
existing session only after validation. **Remove session** deletes the stored
session; future jobs then use anonymous access and may be denied by Instagram.

If Instagram logout, session expiry, a challenge/checkpoint, or rejection is
reported, resolve the issue in the browser, re-export a fresh Netscape Cookie
file, and replace the imported session in **Settings**. Instagram can also
rate-limit, restrict, remove, or make public content unavailable; those
conditions appear as failed Activity jobs and can be retried after the
underlying content is available again.

## Persistent storage and backup

Keep the complete `IW_DATA_ROOT_HOST` directory. It contains shared state for
both services:

```text
/data/
├── database/app.sqlite3     # administrators, sessions, library, and jobs
├── media/                   # downloaded images and videos
├── secrets/app_secret_key   # generated application-session secret
└── tmp/jobs/                # worker staging files
```

The encrypted imported session, when configured, is stored at
`/data/secrets/instagram_session.enc`. Stopping or recreating containers does
not remove this directory. Back it up while the services are stopped, and
restore the whole directory together so the database, downloaded media, and
application secret remain consistent. In particular,
`secrets/instagram_session.enc` and `secrets/app_secret_key` must be backed up
and restored together: the session is encrypted from the application secret and
cannot be recovered with a different one.

## Administrator manual acceptance

Live Instagram access must be accepted by an administrator with their own valid
browser session; do not share Cookie contents. After deployment:

1. Open **Settings** and import the extension-produced Instagram `cookies.txt`.
2. Confirm that Settings displays `Connected as @<username>`, then delete the
   exported local file.
3. If `https://www.instagram.com/oioo712/` is not already tracked, add it
   through **Add** first.
4. Choose **Sync all profiles now**.
5. Verify that the `oioo712` profile advances through **Activity** and
   produces saved media.
6. If shown, report any challenge, rate-limit, or expired-session message
   exactly as displayed, without including Cookie contents.
7. Open **Activity** and confirm persisted jobs replace Loading activity.
8. Confirm automatic refresh occurs after ten seconds and **Refresh** updates
   immediately.
9. Start a profile sync, choose **Stop Sync** during one media download, and
   confirm the current media finishes but no next item starts.
10. Confirm **Sync Now** and **Sync All** ignore the stopped profile.
11. Resume the profile, then explicitly choose **Sync Now** and confirm work
    queues.
12. Submit a new single-media URL and confirm its newly created owner profile
    shows sync stopped.
13. Submit the same URL again and confirm Activity reports a successful
    duplicate skip without rewriting its saved files.

## Security notes

- This project does not provide HTTPS, Caddy, or Nginx. Do not expose plain HTTP
  directly to the public internet; terminate TLS and restrict access at a
  trusted reverse proxy or other external boundary.
- Set `IW_SESSION_COOKIE_SECURE=true` only when the browser-facing endpoint is
  HTTPS. On plain HTTP, browsers will not return a secure session cookie.
- `IW_FORWARDED_ALLOW_IPS` must contain only trusted direct reverse-proxy IPs or
  CIDRs (comma-separated). Do not set it to `*`, or clients could forge
  forwarded source addresses and evade IP-based login throttling.
- `.env` contains bootstrap credentials and is excluded from Git and the Docker
  build context. Do not commit it or copy it into an image.

## CI and release automation

Pull requests and pushes to `main` run four independent GitHub Actions checks:

- Gitleaks over the complete Git history;
- backend Pytest validation (Ruff, Mypy, and coverage gates are deferred);
- frontend Vitest (without a coverage gate), ESLint, and production build validation;
- Docker Compose configuration validation and a local image build.

The release workflow uses
[Release Please](https://github.com/googleapis/release-please) and Conventional
Commits. Merge pull requests with a squash commit whose title describes the
released change:

```text
fix: handle an expired Instagram session
feat: add scheduled profile sync
feat!: replace the persisted library format
```

`fix:` proposes a patch release, `feat:` proposes a minor release, and `!` or a
`BREAKING CHANGE:` footer proposes a major release. Release Please creates one
release pull request and keeps it updated as more qualifying commits reach
`main`. Merging that release pull request creates the SemVer tag and GitHub
Release. Publishing the GitHub Release builds and pushes `linux/amd64` and
`linux/arm64` images to Docker Hub.

### Repository configuration

Create a public Docker Hub repository named
`z21012101/instaloader-webui`, then configure these values under
**GitHub repository Settings > Secrets and variables > Actions**:

| Kind | Name | Value or permissions |
| --- | --- | --- |
| Variable | `DOCKERHUB_USERNAME` | Docker Hub account name, `z21012101` |
| Secret | `DOCKERHUB_TOKEN` | Expiring Docker Hub personal access token with Read/Write, not Delete |
| Secret | `RELEASE_PLEASE_TOKEN` | Expiring fine-grained GitHub PAT scoped only to this repository |

Give `RELEASE_PLEASE_TOKEN` repository permissions for **Contents**,
**Issues**, and **Pull requests**, each set to Read and write. A separate PAT is
needed because resources created by the built-in `GITHUB_TOKEN` do not trigger
the CI workflow for the generated release pull request.

Recommended GitHub settings:

1. Under **Actions > General**, allow GitHub Actions to create pull requests.
2. Use a `main` branch ruleset that requires the **Secret scan**, **Backend**,
   **Frontend**, and **Container** checks.
3. Enable squash merging and use Conventional Commit squash titles.
4. Enable GitHub secret scanning and push protection.

The existing source is the `0.1.0` release baseline. After the first push,
publish a one-time GitHub Release from `main` with tag `v0.1.0` to create the
initial Docker image. Later releases are produced by merging the Release Please
pull request.

## Operations

```sh
docker compose ps
docker compose logs web
docker compose logs worker
docker compose pull
docker compose up -d
docker compose down
```

The local image is rebuilt from the WebUI repository context with:

```sh
docker compose \
  --file compose.yaml \
  --file compose.build.yaml \
  build
```

## License

Instaloader WebUI is available under the [MIT License](LICENSE).
