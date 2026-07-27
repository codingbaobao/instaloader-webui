# Instaloader WebUI

Instaloader WebUI is a personal, public-Instagram library POC. It provides a
single-administrator FastAPI and React application plus a persistent worker that
downloads public Instagram media through the unmodified local Instaloader
source checkout.

This POC is public-only: private accounts, private media, Stories, and content
that requires an Instagram login are not supported. A later milestone may add
an explicitly configured Instagram session; this deployment does not accept or
store Instagram credentials.

## Docker Compose deployment

Requirements: Docker Engine and Docker Compose v2. The checkout layout matters:
Compose builds from the parent repository so the image contains both the local
upstream `instaloader/` package and this WebUI repository.

```text
instaloader/
├── setup.py
├── instaloader/
└── instaloader-webui/
    ├── compose.yaml
    └── docker/Dockerfile
```

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

3. Build and start the two-service deployment.

   ```sh
   docker compose up -d --build
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

## Using the public library

1. Sign in with the bootstrap administrator and complete the initial password
   confirmation.
2. Open **Add** and paste a direct public Instagram profile, post, reel, or TV
   URL. A post/reel/TV link queues one media download; a profile URL tracks that
   profile and queues its first sync.
3. Open **Activity** to follow queued and running work. It reports the current
   progress message and reaches **succeeded** or **failed** when the worker has
   finished.
4. Open **Profiles** to browse a tracked account's saved posts and reels, or
   open an item to view its downloaded assets, caption, and original link.
5. In **Settings**, set the profile sync interval in minutes or queue an
   immediate sync of all active profiles. The default is 360 minutes.

Instagram can rate-limit, restrict, remove, or make public content unavailable;
those conditions appear as failed Activity jobs and can be retried after the
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

Stopping or recreating containers does not remove this directory. Back it up
while the services are stopped, and restore the whole directory together so the
database, downloaded media, and application secret remain consistent.

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

## Operations

```sh
docker compose ps
docker compose logs web
docker compose logs worker
docker compose down
```

The image is rebuilt from the outer repository context with:

```sh
docker compose build
```
