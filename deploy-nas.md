# Deploy Nightly Image to NAS

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
