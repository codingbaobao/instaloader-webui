# Deploy Instaloader WebUI to NAS

## NAS details

- IP: `192.168.0.103`
- SSH alias: `nas`
- Compose path: `/vol3/1000/docker-configs/instaloader-webui`
- Image: `z21012101/instaloader-webui:nightly`
- Web URL: `http://192.168.0.103:8082`

## Steps

1. Confirm the new nightly image has been published from `main`.

2. Connect to the NAS and enter the Compose directory:

   ```bash
   ssh nas
   cd /vol3/1000/docker-configs/instaloader-webui
   ```

3. Pull the new image and recreate the services:

   ```bash
   docker compose pull
   docker compose up -d --remove-orphans
   ```

4. Verify that web and worker are running:

   ```bash
   docker compose ps
   docker inspect --format '{{.Name}} revision={{ index .Config.Labels "org.opencontainers.image.revision" }} image={{.Image}}' \
     instaloader-webui-web-1 instaloader-webui-worker-1
   ```

5. Verify the health endpoint and recent logs:

   ```bash
   curl -fsS http://192.168.0.103:8082/api/health
   docker compose logs --since=5m --no-color web worker | tail -80
   ```

Expected health response:

```json
{"success":true,"data":{"status":"ok"},"error":null,"meta":{}}
```

## Safety notes

- Pull the published image; do not build Docker images on the NAS.
- Do not run `docker compose down -v` or delete the mounted `/data` directory.
- Recreating the containers preserves the database, downloaded media, settings,
  and encrypted Instagram Cookie session in the NAS data mount.

## Schema v2 migration (`pre-1.0-feed-sync-2`)

This migration is intentionally one-way. Never start an older image against a
database that already reports `pre-1.0-feed-sync-2`.

Before changing the live database:

1. Build or pull the exact new image and record its immutable image ID and Git
   revision.
2. Run the image against a temporary data root and verify `/api/health`,
   `PRAGMA integrity_check`, and the fresh schema marker.
3. Use SQLite's backup API from the currently trusted image to make a timestamped
   v1 backup while the application is still running.
4. Migrate a separate copy of that backup with the new image. Verify integrity,
   the v2 marker, row counts, and Posts/Reels checkpoint counts before touching
   the live database.

For the live cutover, first confirm there are no pending or running jobs, then:

```bash
cd /vol3/1000/docker-configs/instaloader-webui
docker compose stop worker web
```

Create a second, final backup after both services are stopped. Use Python's
`sqlite3.Connection.backup()` API; do not copy an active WAL database directly.
Read the backup in SQLite read-only mode and require:

```sql
PRAGMA integrity_check; -- must return ok
SELECT version FROM schema_marker WHERE id = 'global';
SELECT COUNT(*) FROM profiles;
SELECT COUNT(*) FROM media_items;
SELECT COUNT(*) FROM jobs;
SELECT COUNT(*) FROM job_issues;
```

Update `IW_IMAGE` in `.env` to the verified tag. Start only the web service so
exactly one process performs or validates the migration:

```bash
docker compose up -d --no-deps web
curl -fsS http://192.168.0.103:8082/api/health
docker compose exec -T web python -c \
  'import sqlite3; c=sqlite3.connect("file:/data/database/app.sqlite3?mode=ro", uri=True); print(c.execute("PRAGMA integrity_check").fetchone()[0]); print(c.execute("SELECT version FROM schema_marker WHERE id=\"global\"").fetchone()[0])'
```

Only after health, integrity, schema, and preserved row counts are confirmed:

```bash
docker compose up -d worker
docker compose ps
```

Validate one controlled profile sync rather than Sync All. Activity must show
the profile target plus Stories and Feed content rows. Confirm Feed progresses
beyond the former repeated prefix and the job either completes or retains
source checkpoints after a blocking outcome.

### 2026-08-28 migration record

- Git revision: `c6485655a2cb428af23986f3586e7a881f8a8c45`
- Image tag: `instaloader-webui:feed-sync-v2-c648565`
- Image ID: `sha256:dcb8def9cd92c26304eda0c07dd4fcb84e55bc68da84004078357829152ca475`
- Online v1 backup:
  `/vol3/1000/docker-configs/instaloader-webui/data/database/backups/app-v1-feed-sync-20260827T164057Z.sqlite3`
- Final stopped-services v1 backup:
  `/vol3/1000/docker-configs/instaloader-webui/data/database/backups/app-v1-feed-sync-final-20260827T164152Z.sqlite3`
- Preserved rows: 8 profiles, 3,407 media items, 299 jobs, and 2 job issues.
- Migrated checkpoints: 7 Posts rows and 7 Reels rows.
- Controlled `mihi_727` job:
  `13392bc7-8459-48d3-858c-c7b1027afdcf`; 402 Feed items scanned,
  400 existing, 2 saved, and 0 warnings.

The image was built in an isolated NAS build directory only because the local
Docker daemon was unavailable. Normal releases should continue to use a
published immutable image and `docker compose pull`.

### Rollback

Rollback requires restoring the final v1 backup; the old image cannot read the
v2 database. Stop both services, preserve the failed v2 database separately,
restore the verified backup to `/data/database/app.sqlite3` with SQLite's backup
API, restore the saved pre-migration `.env`, and start the recorded old image
revision. Re-run integrity and row-count checks before starting the worker.
