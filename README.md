# Instaloader WebUI

這是個人使用的 Instaloader WebUI。Phase 1 提供單一管理者登入介面、FastAPI
後端與 React 前端，並以一個 Docker image 執行。排程、Instagram 登入與下載
功能會在後續階段加入。

## Docker Compose 啟動

需求：Docker Engine 與 Docker Compose v2。

1. 複製環境設定：

   ```sh
   cp .env.example .env
   ```

2. 設定 `IW_ADMIN_USERNAME` 與初始的 `IW_ADMIN_PASSWORD`。密碼沒有長度或
   複雜度限制，也可以是空字串。首次登入後必須在 WebUI 確認管理員密碼。

3. 將 `IW_DATA_ROOT_HOST` 設為你要保存資料的主機目錄；該目錄會掛載到
   container 的 `/data`，SQLite 位於 `/data/database/app.sqlite3`。
   Linux 主機需事先建立目錄，並確保 container 的非 root 使用者
   （固定 UID/GID `10001:10001`）可讀寫：

   ```sh
   mkdir -p /your/chosen/path
   sudo chown 10001:10001 /your/chosen/path
   ```

4. 建置並啟動：

   ```sh
   docker compose up -d --build
   ```

   預設在所有主機介面提供 `http://主機位址:8080`；可用 `IW_HTTP_BIND`
   限制綁定介面，並以 `IW_HTTP_PORT` 修改主機 port。例如只允許同機反向
   代理連線時，可設 `IW_HTTP_BIND=127.0.0.1`。

## 安全注意事項

- 本專案不提供 HTTPS、Caddy 或 Nginx。直接將 HTTP 暴露於公網並不安全；
  請自行在外部反向代理或其他入口終止 TLS，並限制來源與登入嘗試。
- 應用程式只信任 `IW_FORWARDED_ALLOW_IPS` 列出的直接反向代理所提供的
  forwarded headers。值必須是已知 proxy 的 container IP 或 CIDR（多個值以
  逗號分隔）；不要設為 `*`，也不要加入不受信任的網段，否則用戶端可偽造
  來源 IP 並繞過依 IP 的登入限制。未使用反向代理時保留預設
  `127.0.0.1`。Compose 前方另有 proxy container 時，請明確改成該 proxy
  所在的受信任網段。
- 只有外部入口已完整使用 HTTPS 時，才將
  `IW_SESSION_COOKIE_SECURE=true`。若在純 HTTP 下設為 `true`，瀏覽器不會
  傳送 session cookie，登入流程將無法正常使用。
- 應用程式會送出 CSP、frame、MIME、referrer、permissions 與 no-store
  等安全標頭，但刻意不送 HSTS，因為它無法知道用戶端是否確實經由 HTTPS
  抵達。TLS 入口應覆寫/補上適合部署網域的 HSTS，並避免把內部純 HTTP
  連線誤宣告為 HTTPS。
- 管理者首次建立後，`IW_ADMIN_USERNAME` 與 `IW_ADMIN_PASSWORD` 的 bootstrap
  值會被忽略；之後修改環境變數不會重設既有帳號或密碼。確認已完成首次
  建立後，可清空 `.env` 的 `IW_ADMIN_PASSWORD` 並重新建立 container，避免
  長期保留初始密碼。
- `.env` 含有敏感資料，已由 Git 與 Docker build context 排除。請勿提交、
  分享或寫入 image。

## 常用操作

```sh
docker compose ps
docker compose logs web
docker compose down
```

停止或重建 container 不會刪除所選主機目錄中的資料。若要備份，請在服務
停止後複製整個 `IW_DATA_ROOT_HOST` 目錄。
