# Phase 1 Foundation and Administrator Authentication Implementation Plan

> **Status — superseded for schema work (2026-08-01):** This is a historical
> plan. Do not execute its Alembic, migration, database-upgrade/downgrade, or
> migration-startup instructions. The current pre-1.0 runtime supports only
> the exact fresh schema and does not migrate older databases.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Docker-runnable FastAPI and React foundation in which one bootstrapped administrator can log in, change the initial password, maintain an opaque server-side session, and log out.

**Architecture:** The backend uses a Python `src` layout, FastAPI application factory, SQLAlchemy repositories, Alembic migrations, and SQLite WAL. The frontend is a Vite-built React SPA served by FastAPI; a multi-stage Docker build leaves Node.js outside the final runtime image.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy 2, Alembic, SQLite, Argon2id, React, TypeScript, Vite, Vitest, React Testing Library, pytest, Playwright, Docker Compose.

## Global Constraints

- Project root is `C:\Users\z2101\instaloader\instaloader-webui`.
- Do not modify the outer repository's `instaloader/` package.
- Keep persistent runtime state beneath `/data`.
- Serve HTTP only; do not add TLS or a reverse proxy.
- Support exactly one WebUI administrator.
- Write and observe a failing test before every production behavior.
- Maintain at least 80% backend and frontend coverage.
- Use immutable request/domain values and Pydantic validation at boundaries.
- Never log or return plaintext passwords, session tokens, or CSRF secrets.
- Do not commit generated `.superpowers/`, local databases, environment files, build output, or coverage output.

## File Map

```text
instaloader-webui/
├── .gitignore
├── .env.example
├── compose.yaml
├── docker/
│   └── Dockerfile
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── migrations/
│   │   ├── env.py
│   │   └── versions/
│   │       ├── 0001_admin_and_sessions.py
│   │       └── 0002_login_failures.py
│   ├── src/instaloader_webui/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── api/
│   │   │   ├── envelope.py
│   │   │   ├── dependencies.py
│   │   │   └── routes/
│   │   │       ├── auth.py
│   │   │       └── health.py
│   │   ├── auth/
│   │   │   ├── passwords.py
│   │   │   ├── session_tokens.py
│   │   │   └── throttle.py
│   │   ├── db/
│   │   │   ├── base.py
│   │   │   ├── engine.py
│   │   │   ├── migrations.py
│   │   │   ├── models.py
│   │   │   └── repositories.py
│   │   ├── services/
│   │   │   ├── admin_bootstrap.py
│   │   │   └── auth_service.py
│   │   └── web/
│   │       └── spa.py
│   └── tests/
│       ├── conftest.py
│       ├── unit/
│       └── integration/
├── frontend/
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── vitest.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── app/
│       ├── auth/
│       └── styles/
└── tests/
    └── container/
        └── test_compose_smoke.py
```

---

### Task 1: Backend Package, Configuration, and API Envelope

**Files:**

- Create: `.gitignore`
- Create: `.env.example`
- Create: `backend/pyproject.toml`
- Create: `backend/src/instaloader_webui/__init__.py`
- Create: `backend/src/instaloader_webui/config.py`
- Create: `backend/src/instaloader_webui/api/envelope.py`
- Create: `backend/src/instaloader_webui/api/routes/health.py`
- Create: `backend/src/instaloader_webui/main.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/unit/test_config.py`
- Create: `backend/tests/integration/test_health.py`

**Interfaces:**

- Produces: `Settings`, `ApiEnvelope[T]`, and `create_app(settings: Settings | None = None) -> FastAPI`.
- Consumes: Environment variables only; no database dependency in this task.

- [ ] **Step 1: Create backend packaging and test configuration**

Create `backend/pyproject.toml` with Python `>=3.12,<3.13`, runtime dependencies for FastAPI, Uvicorn, Pydantic Settings, SQLAlchemy, Alembic, Argon2, and Cryptography, plus test dependencies for pytest, pytest-cov, HTTPX, Ruff, and mypy. Configure pytest with:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers --cov=instaloader_webui --cov-report=term-missing --cov-fail-under=80"
```

Create `.gitignore` with:

```gitignore
.env
.superpowers/
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
node_modules/
dist/
playwright-report/
test-results/
data/
*.sqlite3
```

- [ ] **Step 2: Write failing configuration and health tests**

```python
# backend/tests/unit/test_config.py
from pathlib import Path

from instaloader_webui.config import Settings


def test_settings_keep_runtime_paths_under_data_root(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        admin_username="owner",
        admin_password="correct-horse-battery-staple",
    )

    assert settings.database_path == tmp_path / "database" / "app.sqlite3"
    assert settings.static_root == Path("/app/static")
```

```python
# backend/tests/conftest.py
from pathlib import Path
import pytest
from instaloader_webui.config import Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path,
        admin_username="owner",
        admin_password="correct-horse-battery-staple",
    )
```

```python
# backend/tests/integration/test_health.py
from fastapi.testclient import TestClient

from instaloader_webui.main import create_app


def test_health_uses_consistent_envelope(test_settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"status": "ok"},
        "error": None,
        "meta": {},
    }
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
cd backend
python -m pip install -e ".[test]"
pytest tests/unit/test_config.py tests/integration/test_health.py -v
```

Expected: collection fails because `instaloader_webui.config` and `instaloader_webui.main` do not exist.

- [ ] **Step 4: Implement minimal settings, envelope, and app factory**

Use immutable Pydantic settings:

```python
# backend/src/instaloader_webui/config.py
from pathlib import Path
from pydantic import SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IW_",
        env_file=".env",
        frozen=True,
        extra="ignore",
    )

    data_root: Path = Path("/data")
    static_root: Path = Path("/app/static")
    admin_username: str
    admin_password: SecretStr | None = None
    admin_password_file: Path | None = None
    session_cookie_secure: bool = False

    @computed_field
    @property
    def database_path(self) -> Path:
        return self.data_root / "database" / "app.sqlite3"
```

```python
# backend/src/instaloader_webui/api/envelope.py
from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiEnvelope(BaseModel, Generic[T]):
    model_config = ConfigDict(frozen=True)

    success: bool
    data: T | None
    error: dict[str, str] | None = None
    meta: dict[str, object] = Field(default_factory=dict)
```

```python
# backend/src/instaloader_webui/api/routes/health.py
from fastapi import APIRouter
from instaloader_webui.api.envelope import ApiEnvelope

router = APIRouter(prefix="/api")


@router.get("/health", response_model=ApiEnvelope[dict[str, str]])
def health() -> ApiEnvelope[dict[str, str]]:
    return ApiEnvelope(success=True, data={"status": "ok"})
```

```python
# backend/src/instaloader_webui/main.py
from fastapi import FastAPI
from instaloader_webui.api.routes.health import router as health_router
from instaloader_webui.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()
    app = FastAPI(title="Instaloader WebUI")
    app.state.settings = resolved
    app.include_router(health_router)
    return app
```

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```powershell
pytest tests/unit/test_config.py tests/integration/test_health.py -v
ruff check src tests
mypy src
```

Expected: both tests pass with no lint or type errors.

- [ ] **Step 6: Commit the independently testable foundation**

```powershell
git add .gitignore .env.example backend
git commit -m "feat: scaffold FastAPI service foundation"
```

---

### Task 2: SQLite WAL, Models, Repositories, and Alembic

**Files:**

- Create: `backend/src/instaloader_webui/db/base.py`
- Create: `backend/src/instaloader_webui/db/engine.py`
- Create: `backend/src/instaloader_webui/db/models.py`
- Create: `backend/src/instaloader_webui/db/repositories.py`
- Create: `backend/src/instaloader_webui/db/migrations.py`
- Create: `backend/alembic.ini`
- Create: `backend/migrations/env.py`
- Create: `backend/migrations/versions/0001_admin_and_sessions.py`
- Create: `backend/tests/integration/test_database.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**

- Produces: `build_engine(database_path: Path) -> Engine`, `build_session_factory(engine) -> sessionmaker[Session]`, `run_migrations(settings: Settings) -> None`, `AdminRepository`, and `WebSessionRepository`.
- Consumes: `Settings.database_path` from Task 1.

- [ ] **Step 1: Write failing SQLite and migration tests**

```python
# backend/tests/integration/test_database.py
from sqlalchemy import text

from instaloader_webui.db.engine import build_engine
from instaloader_webui.db.migrations import run_migrations


def test_sqlite_enables_wal_and_foreign_keys(test_settings) -> None:
    engine = build_engine(test_settings.database_path)

    with engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()

    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1


def test_initial_migration_creates_auth_tables(test_settings) -> None:
    run_migrations(test_settings)
    engine = build_engine(test_settings.database_path)

    with engine.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }

    assert {"admin_users", "web_sessions", "alembic_version"} <= names
```

- [ ] **Step 2: Run database tests and verify RED**

Run:

```powershell
pytest tests/integration/test_database.py -v
```

Expected: collection fails because the database modules do not exist.

- [ ] **Step 3: Implement engine and immutable model boundaries**

```python
# backend/src/instaloader_webui/db/engine.py
from pathlib import Path
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_path: Path) -> Engine:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database_path}", future=True)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
```

Extend `backend/tests/conftest.py` with:

```python
@pytest.fixture
def engine(test_settings):
    return build_engine(test_settings.database_path)


@pytest.fixture
def session_factory(engine):
    return build_session_factory(engine)
```

Define SQLAlchemy models with UUID string primary keys and UTC timestamps:

```python
# backend/src/instaloader_webui/db/models.py
class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class WebSession(Base):
    __tablename__ = "web_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    admin_user_id: Mapped[str] = mapped_column(ForeignKey("admin_users.id"))
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
```

Implement repositories so services receive value objects and return new immutable snapshots rather than mutating caller-owned objects.

- [ ] **Step 4: Add Alembic configuration and initial migration**

Create an Alembic environment that reads `IW_DATA_ROOT`, imports `Base.metadata`, and builds the SQLite URL from `Settings`. The `0001_admin_and_sessions.py` migration must create the exact columns and uniqueness constraints represented by the two models.

Implement:

```python
# backend/src/instaloader_webui/db/migrations.py
from alembic import command
from alembic.config import Config
from instaloader_webui.config import Settings


def run_migrations(settings: Settings) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.database_path}")
    command.upgrade(config, "head")
```

- [ ] **Step 5: Run database tests and verify GREEN**

Run:

```powershell
pytest tests/integration/test_database.py -v
alembic upgrade head
alembic downgrade base
alembic upgrade head
```

Expected: tests pass and migrations round-trip without warnings.

- [ ] **Step 6: Run full backend checks**

Run:

```powershell
pytest -v
ruff check src tests migrations
mypy src
```

Expected: all checks pass and coverage remains at least 80%.

- [ ] **Step 7: Commit the persistence layer**

```powershell
git add backend
git commit -m "feat: add SQLite persistence foundation"
```

---

### Task 3: Argon2 Passwords and Idempotent Administrator Bootstrap

**Files:**

- Create: `backend/src/instaloader_webui/auth/passwords.py`
- Create: `backend/src/instaloader_webui/services/admin_bootstrap.py`
- Create: `backend/tests/unit/test_passwords.py`
- Create: `backend/tests/integration/test_admin_bootstrap.py`
- Modify: `backend/src/instaloader_webui/config.py`
- Modify: `backend/src/instaloader_webui/db/repositories.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**

- Produces: `PasswordService.hash(password: str) -> str`, `PasswordService.verify(hash_value: str, password: str) -> bool`, `resolve_bootstrap_password(settings: Settings) -> str`, and `bootstrap_admin(session_factory: sessionmaker[Session], settings: Settings, passwords: PasswordService | None = None) -> AdminSnapshot`.
- Consumes: `AdminRepository` and `Settings` from Tasks 1 and 2.

- [ ] **Step 1: Write failing password and bootstrap tests**

```python
# backend/tests/unit/test_passwords.py
from instaloader_webui.auth.passwords import PasswordService


def test_argon2_hash_does_not_reveal_password() -> None:
    service = PasswordService()
    encoded = service.hash("correct-horse-battery-staple")

    assert "correct-horse-battery-staple" not in encoded
    assert encoded.startswith("$argon2id$")
    assert service.verify(encoded, "correct-horse-battery-staple")
    assert not service.verify(encoded, "wrong-password")
```

```python
# backend/tests/integration/test_admin_bootstrap.py
def test_bootstrap_creates_exactly_one_forced_change_admin(
    session_factory, test_settings
) -> None:
    first = bootstrap_admin(session_factory, test_settings)
    second = bootstrap_admin(session_factory, test_settings)

    assert first.id == second.id
    assert first.username == "owner"
    assert first.must_change_password is True
    assert count_admins(session_factory) == 1
```

Add tests that reject:

- Missing password and password file.
- Both inline password and password file.
- Password shorter than 16 characters.
- A password file larger than 4 KiB.
- A username outside `^[A-Za-z0-9._-]{3,64}$`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/unit/test_passwords.py tests/integration/test_admin_bootstrap.py -v
```

Expected: collection fails because password and bootstrap services do not exist.

- [ ] **Step 3: Implement password hashing and safe bootstrap resolution**

```python
# backend/src/instaloader_webui/auth/passwords.py
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


class PasswordService:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, hash_value: str, password: str) -> bool:
        try:
            return self._hasher.verify(hash_value, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
```

`resolve_bootstrap_password()` must read at most 4097 bytes, reject conflicting sources, strip one trailing line ending from a secret file, and return an immutable string value. Do not log it.

`bootstrap_admin()` must:

1. Return the existing administrator without examining bootstrap password fields.
2. Validate bootstrap username and password when no administrator exists.
3. Hash the password before creating a database row.
4. Set `must_change_password=True`.
5. Handle a uniqueness race by re-reading the single administrator.

Define the repository return type explicitly:

```python
@dataclass(frozen=True, slots=True)
class AdminSnapshot:
    id: str
    username: str
    password_hash: str
    must_change_password: bool
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
pytest tests/unit/test_passwords.py tests/integration/test_admin_bootstrap.py -v
```

Expected: all password and bootstrap tests pass.

- [ ] **Step 5: Run full backend checks**

Run:

```powershell
pytest -v
ruff check src tests
mypy src
```

Expected: all checks pass at 80% or higher coverage.

- [ ] **Step 6: Commit administrator bootstrap**

```powershell
git add backend
git commit -m "feat: bootstrap single administrator securely"
```

---

### Task 4: Persistent Login Throttling

**Files:**

- Create: `backend/src/instaloader_webui/auth/throttle.py`
- Create: `backend/migrations/versions/0002_login_failures.py`
- Create: `backend/tests/unit/test_login_throttle.py`
- Modify: `backend/src/instaloader_webui/db/models.py`
- Modify: `backend/src/instaloader_webui/db/repositories.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**

- Produces: `LoginThrottle.check(key: LoginAttemptKey, now: datetime) -> ThrottleDecision`, `LoginThrottle.record_failure(key: LoginAttemptKey, now: datetime) -> None`, and `LoginThrottle.record_success(key: LoginAttemptKey) -> None`.
- Consumes: normalized administrator username and a trusted client-IP string supplied by the API layer.

- [ ] **Step 1: Write failing throttle policy tests**

```python
# backend/tests/unit/test_login_throttle.py
from datetime import UTC, datetime, timedelta


def test_fifth_failure_blocks_for_fifteen_minutes(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")

    for offset in range(5):
        throttle.record_failure(key, now + timedelta(seconds=offset))

    decision = throttle.check(key, now + timedelta(seconds=5))

    assert decision.allowed is False
    assert decision.retry_after_seconds == 15 * 60 - 1


def test_success_clears_previous_failures(throttle) -> None:
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    key = LoginAttemptKey(username="owner", client_ip="203.0.113.7")
    throttle.record_failure(key, now)
    throttle.record_success(key)

    assert throttle.check(key, now).allowed is True
```

Also test normalization, separate username/IP buckets, expiry after the window, and non-negative `Retry-After`.

- [ ] **Step 2: Run throttle tests and verify RED**

Run:

```powershell
pytest tests/unit/test_login_throttle.py -v
```

Expected: collection fails because `LoginThrottle` does not exist.

- [ ] **Step 3: Implement persistent failure buckets**

Create a `login_failures` table keyed by an HMAC digest of normalized username plus client IP. Store count, first failure, last failure, and blocked-until timestamps. Never store attempted passwords.

Use immutable results:

```python
@dataclass(frozen=True, slots=True)
class LoginAttemptKey:
    username: str
    client_ip: str


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    allowed: bool
    retry_after_seconds: int
```

The default policy is five failures in fifteen minutes followed by a fifteen-minute block. A successful login deletes the matching bucket.

- [ ] **Step 4: Run migration and throttle tests**

Run:

```powershell
alembic upgrade head
pytest tests/unit/test_login_throttle.py -v
```

Expected: migration and all throttle tests pass.

- [ ] **Step 5: Run full backend checks**

Run:

```powershell
pytest -v
ruff check src tests migrations
mypy src
```

Expected: all checks pass.

- [ ] **Step 6: Commit throttling**

```powershell
git add backend
git commit -m "feat: persist administrator login throttling"
```

---

### Task 5: Opaque Browser Sessions, CSRF, and Authentication API

**Files:**

- Create: `backend/src/instaloader_webui/auth/session_tokens.py`
- Create: `backend/src/instaloader_webui/services/auth_service.py`
- Create: `backend/src/instaloader_webui/api/dependencies.py`
- Create: `backend/src/instaloader_webui/api/routes/auth.py`
- Create: `backend/tests/unit/test_session_tokens.py`
- Create: `backend/tests/integration/test_auth_api.py`
- Modify: `backend/src/instaloader_webui/main.py`
- Modify: `backend/src/instaloader_webui/db/repositories.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**

- Produces: `AuthService.login(username: str, password: str, client_ip: str, now: datetime) -> LoginResult`, `AuthService.authenticate_session(raw_token: str, now: datetime) -> AuthenticatedSession | None`, `AuthService.change_password(raw_token: str, current_password: str, new_password: str, now: datetime) -> AuthenticatedSession`, `AuthService.logout(raw_token: str, now: datetime) -> None`, `issue_session_token() -> IssuedSession`, `digest_session_token(raw: str) -> str`, and `derive_csrf_token(raw_session: str, app_secret_key: str) -> str`.
- Consumes: administrator, web-session, and login-throttle repositories.

- [ ] **Step 1: Write failing token tests**

```python
# backend/tests/unit/test_session_tokens.py
def test_session_token_is_opaque_and_only_digest_is_persisted() -> None:
    issued = issue_session_token()

    assert len(issued.raw) >= 43
    assert issued.raw not in issued.digest
    assert issued.digest == digest_session_token(issued.raw)


def test_csrf_token_is_stable_for_session_and_secret() -> None:
    first = derive_csrf_token("raw-session", "s" * 32)
    second = derive_csrf_token("raw-session", "s" * 32)

    assert first == second
    assert first != derive_csrf_token("another-session", "s" * 32)
```

- [ ] **Step 2: Write failing authentication API tests**

Cover these exact flows in `backend/tests/integration/test_auth_api.py`:

```python
def test_login_sets_http_only_cookie_and_requires_password_change(client) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correct-horse-battery-staple"},
    )

    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.json()["data"]["must_change_password"] is True
    assert response.json()["data"]["csrf_token"]


def test_state_change_rejects_missing_csrf(authenticated_client) -> None:
    response = authenticated_client.post(
        "/api/auth/change-password",
        json={
            "current_password": "correct-horse-battery-staple",
            "new_password": "different-long-owner-password",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


def test_password_change_revokes_other_sessions(
    authenticated_client, second_authenticated_client
) -> None:
    csrf = authenticated_client.get("/api/auth/session").json()["data"]["csrf_token"]
    changed = authenticated_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "correct-horse-battery-staple",
            "new_password": "different-long-owner-password",
        },
    )

    assert changed.status_code == 200
    assert second_authenticated_client.get("/api/auth/session").status_code == 401
```

Also test invalid credentials, `Retry-After`, expired session, revoked session, logout, secure-cookie configuration, safe error envelopes, and absence of passwords in captured logs.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
pytest tests/unit/test_session_tokens.py tests/integration/test_auth_api.py -v
```

Expected: tests fail because session-token functions and auth routes do not exist.

- [ ] **Step 4: Implement tokens and authentication service**

```python
# backend/src/instaloader_webui/auth/session_tokens.py
from dataclasses import dataclass
import hashlib
import hmac
import secrets


@dataclass(frozen=True, slots=True)
class IssuedSession:
    raw: str
    digest: str


def digest_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_session_token() -> IssuedSession:
    raw = secrets.token_urlsafe(32)
    return IssuedSession(raw=raw, digest=digest_session_token(raw))


def derive_csrf_token(raw_session: str, app_secret_key: str) -> str:
    return hmac.new(
        app_secret_key.encode("utf-8"),
        raw_session.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
```

`AuthService` must use constant-time verification where applicable, create seven-day sessions, refresh `last_seen_at` at a bounded cadence, and revoke every other session after password change.

Define `LoginResult` and `AuthenticatedSession` as frozen dataclasses containing administrator ID, username, `must_change_password`, expiration, and the newly issued raw token only when the caller must set a cookie. Repository methods receive token digests, never raw session tokens.

- [ ] **Step 5: Implement API dependencies and routes**

Use request models with `ConfigDict(frozen=True)`. Set the cookie as:

```python
response.set_cookie(
    key="iw_session",
    value=issued.raw,
    httponly=True,
    secure=settings.session_cookie_secure,
    samesite="lax",
    max_age=7 * 24 * 60 * 60,
    path="/",
)
```

For every state-changing authenticated endpoint, compare `X-CSRF-Token` against a token derived from the persisted internal application secret using `hmac.compare_digest`.

Return stable errors such as:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "invalid_credentials",
    "message": "The username or password is incorrect."
  },
  "meta": {}
}
```

Use the FastAPI lifespan to run migrations, build repositories, and invoke `bootstrap_admin()` before accepting requests.

Extend `backend/tests/conftest.py` with:

- `client`: a `TestClient(create_app(test_settings))` context that runs migrations and bootstrap.
- `authenticated_client`: a fresh client that logs in using the bootstrap credentials.
- `second_authenticated_client`: another independently authenticated client against the same temporary database.

Each test receives a fresh `tmp_path` database so password changes and throttling do not leak between tests.

- [ ] **Step 6: Run targeted and full backend tests**

Run:

```powershell
pytest tests/unit/test_session_tokens.py tests/integration/test_auth_api.py -v
pytest -v
ruff check src tests migrations
mypy src
```

Expected: every authentication test passes with at least 80% total coverage.

- [ ] **Step 7: Commit the authentication API**

```powershell
git add backend
git commit -m "feat: add secure administrator sessions"
```

---

### Task 6: React Login, Forced Password Change, and Responsive Shell

**Files:**

- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/App.test.tsx`
- Create: `frontend/src/app/api.ts`
- Create: `frontend/src/auth/LoginPage.tsx`
- Create: `frontend/src/auth/LoginPage.test.tsx`
- Create: `frontend/src/auth/ChangePasswordPage.tsx`
- Create: `frontend/src/auth/ChangePasswordPage.test.tsx`
- Create: `frontend/src/auth/useSession.ts`
- Create: `frontend/src/test/server.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/test/TestRouter.tsx`
- Create: `frontend/src/styles/global.css`
- Create: `backend/src/instaloader_webui/web/spa.py`
- Create: `backend/tests/integration/test_spa.py`
- Modify: `backend/src/instaloader_webui/main.py`

**Interfaces:**

- Produces: `apiRequest<T>()`, `useSession()`, `<LoginPage>`, `<ChangePasswordPage>`, and the authenticated responsive shell.
- Consumes: Task 5's `/api/auth/login`, `/api/auth/session`, `/api/auth/change-password`, and `/api/auth/logout`.

- [ ] **Step 1: Create frontend test tooling**

Define npm scripts:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run --coverage",
    "lint": "eslint ."
  }
}
```

Use React, React DOM, React Router, TypeScript, Vite, Vitest, jsdom, Testing Library, user-event, MSW, ESLint, and the V8 coverage provider. Commit the generated `package-lock.json`; do not use floating installs in Docker.

Configure `frontend/src/test/server.ts` with `setupServer()`, register `beforeAll`, `afterEach`, and `afterAll` lifecycle hooks in `frontend/src/test/setup.ts`, and define `TestRouter` as a `MemoryRouter` wrapper that renders the same application routes used by production.

- [ ] **Step 2: Write failing login and forced-change component tests**

```tsx
// frontend/src/auth/LoginPage.test.tsx
it("submits credentials and routes a bootstrap admin to password change", async () => {
  server.use(
    http.post("/api/auth/login", () =>
      HttpResponse.json({
        success: true,
        data: { username: "owner", must_change_password: true, csrf_token: "csrf" },
        error: null,
        meta: {},
      }),
    ),
  );
  render(<TestRouter initialPath="/login"><LoginPage /></TestRouter>);

  await userEvent.type(screen.getByLabelText("Username"), "owner");
  await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery-staple");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByRole("heading", { name: "Change your password" })).toBeVisible();
});
```

```tsx
// frontend/src/auth/ChangePasswordPage.test.tsx
it("sends the csrf token and enters the authenticated shell", async () => {
  let csrfHeader = "";
  server.use(
    http.post("/api/auth/change-password", ({ request }) => {
      csrfHeader = request.headers.get("X-CSRF-Token") ?? "";
      return HttpResponse.json({
        success: true,
        data: { must_change_password: false },
        error: null,
        meta: {},
      });
    }),
  );

  render(<ChangePasswordPage csrfToken="csrf-value" />);
  await userEvent.type(screen.getByLabelText("Current password"), "initial-password-value");
  await userEvent.type(screen.getByLabelText("New password"), "different-long-owner-password");
  await userEvent.click(screen.getByRole("button", { name: "Change password" }));

  expect(csrfHeader).toBe("csrf-value");
  expect(await screen.findByText("Profiles")).toBeVisible();
});
```

- [ ] **Step 3: Run frontend tests and verify RED**

Run:

```powershell
cd frontend
npm ci
npm test -- LoginPage.test.tsx ChangePasswordPage.test.tsx
```

Expected: tests fail because the components and API client do not exist.

- [ ] **Step 4: Implement the API client and authentication screens**

`apiRequest<T>()` must:

- Always send `credentials: "include"`.
- Add `Content-Type: application/json` for JSON bodies.
- Add `X-CSRF-Token` only when supplied.
- Parse the consistent envelope.
- Throw a typed `ApiError` containing only the stable code and safe message.

Implement accessible labeled inputs, disabled duplicate submission, non-sensitive inline errors, and no password persistence. Store the CSRF token only in React memory and reacquire it from `/api/auth/session` after reload.

- [ ] **Step 5: Write failing application-shell tests**

```tsx
// frontend/src/app/App.test.tsx
it("renders mobile bottom navigation and desktop navigation landmarks", async () => {
  render(<App initialSession={authenticatedSession} />);

  expect(screen.getByRole("navigation", { name: "Mobile" })).toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "Desktop" })).toBeInTheDocument();
  expect(screen.getAllByText("Profiles").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Activity").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Settings").length).toBeGreaterThan(0);
});
```

Run:

```powershell
npm test -- App.test.tsx
```

Expected: FAIL because the authenticated shell is absent.

- [ ] **Step 6: Implement the responsive shell and approved visual foundation**

Use the approved hybrid layout direction:

- White and neutral surfaces with restrained Instagram-like spacing.
- Mobile bottom navigation for Home, Profiles, Add, Activity, Settings.
- Desktop sidebar using the same destinations.
- A temporary screen for each destination, without implementing Phase 3 library behavior.
- CSS media query at `768px` to switch navigation modes.
- Visible keyboard focus, reduced-motion support, and semantic landmarks.

- [ ] **Step 7: Write failing SPA fallback test**

```python
# backend/tests/integration/test_spa.py
def test_non_api_route_returns_react_index(client_with_static_build) -> None:
    response = client_with_static_build.get("/profiles/example")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
```

Run:

```powershell
cd backend
pytest tests/integration/test_spa.py -v
```

Expected: FAIL with 404 because the SPA fallback is not mounted.

- [ ] **Step 8: Implement authenticated-safe static SPA serving**

Mount hashed build assets under `/assets` and add a final non-API fallback route that returns `index.html`. Never intercept `/api/*`, and never serve files from `/data`.

For `client_with_static_build`, extend the backend test fixtures to create a temporary directory containing:

```html
<!doctype html><html><body><div id="root"></div></body></html>
```

Then pass `test_settings.model_copy(update={"static_root": temporary_static_root})` to `create_app()`.

- [ ] **Step 9: Run frontend, backend, and build checks**

Run:

```powershell
cd frontend
npm test
npm run lint
npm run build
cd ../backend
pytest -v
ruff check src tests
mypy src
```

Expected: all checks pass and both coverage gates are at least 80%.

- [ ] **Step 10: Commit the authenticated React shell**

```powershell
git add frontend backend
git commit -m "feat: add administrator login experience"
```

---

### Task 7: Multi-stage Runtime Image and Compose Smoke Test

**Files:**

- Create: `docker/Dockerfile`
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `tests/container/test_compose_smoke.py`
- Modify: `.env.example`
- Modify: `backend/src/instaloader_webui/main.py`
- Create: `README.md`

**Interfaces:**

- Produces: One `instaloader-webui` runtime image and a Phase 1 `web` service.
- Consumes: backend package and `frontend/dist` from Tasks 1 through 6.

- [ ] **Step 1: Write failing container smoke test**

```python
# tests/container/test_compose_smoke.py
import os
import subprocess
import time
import urllib.request


def test_compose_web_health_survives_restart(tmp_path) -> None:
    env = {
        **os.environ,
        "IW_DATA_ROOT_HOST": str(tmp_path / "data"),
        "IW_ADMIN_USERNAME": "owner",
        "IW_ADMIN_PASSWORD": "correct-horse-battery-staple",
    }
    subprocess.run(["docker", "compose", "up", "-d", "--build"], check=True, env=env)
    try:
        wait_for_ok("http://127.0.0.1:8080/api/health", timeout=60)
        subprocess.run(["docker", "compose", "restart", "web"], check=True, env=env)
        wait_for_ok("http://127.0.0.1:8080/api/health", timeout=30)
        assert (tmp_path / "data" / "database" / "app.sqlite3").is_file()
    finally:
        subprocess.run(["docker", "compose", "down"], check=True, env=env)
```

Mark the test `container` so normal unit runs can exclude it when Docker is unavailable.

- [ ] **Step 2: Run smoke test and verify RED**

Run:

```powershell
pytest tests/container/test_compose_smoke.py -m container -v
```

Expected: FAIL because `compose.yaml` and `docker/Dockerfile` do not exist.

- [ ] **Step 3: Implement the multi-stage image**

The Dockerfile must contain:

```dockerfile
FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS python-build
WORKDIR /build/backend
COPY backend/ ./
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN addgroup --system app && adduser --system --ingroup app app
WORKDIR /app
COPY --from=python-build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY --from=frontend-build /build/frontend/dist /app/static
COPY backend/alembic.ini /app/alembic.ini
COPY backend/migrations /app/migrations
RUN mkdir -p /data && chown -R app:app /app /data
USER app
EXPOSE 8080
CMD ["uvicorn", "instaloader_webui.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
```

The final image must not copy frontend source, Node.js, npm cache, backend tests, `.env`, `.git`, `.superpowers`, or local `/data`.

- [ ] **Step 4: Implement Compose and health checks**

Create one Phase 1 service:

```yaml
services:
  web:
    build:
      context: .
      dockerfile: docker/Dockerfile
    ports:
      - "${IW_HTTP_PORT:-8080}:8080"
    environment:
      IW_DATA_ROOT: /data
      IW_ADMIN_USERNAME: ${IW_ADMIN_USERNAME}
      IW_ADMIN_PASSWORD: ${IW_ADMIN_PASSWORD}
      IW_SESSION_COOKIE_SECURE: ${IW_SESSION_COOKIE_SECURE:-false}
    volumes:
      - "${IW_DATA_ROOT_HOST:-./data}:/data"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 6
    restart: unless-stopped
```

Do not add HTTPS or a proxy service. Phase 2 adds the `worker` service using the same image.

- [ ] **Step 5: Document safe Phase 1 startup**

`README.md` must include:

- Copy `.env.example` to `.env`.
- Do not expose the internally generated application secret as user-facing configuration.
- Set the initial administrator password, which may be empty.
- Mount a chosen host directory to `/data`.
- Run `docker compose up -d --build`.
- Explain that direct public HTTP is unsafe and TLS is the user's responsibility.
- Explain that bootstrap environment values are ignored after the administrator exists.

- [ ] **Step 6: Run smoke test and verify GREEN**

Run:

```powershell
pytest tests/container/test_compose_smoke.py -m container -v
docker compose config
docker image inspect instaloader-webui-web --format "{{.Config.User}}"
```

Expected:

- Smoke test passes before and after restart.
- Compose configuration is valid.
- Runtime image user is non-root.
- SQLite remains beneath the mounted data directory.

- [ ] **Step 7: Run the complete Phase 1 verification**

Run:

```powershell
cd backend
pytest -v
ruff check src tests migrations
mypy src
cd ../frontend
npm test
npm run lint
npm run build
cd ..
pytest tests/container/test_compose_smoke.py -m container -v
docker compose config
```

Expected: every command exits zero, backend and frontend coverage are at least 80%, and output contains no leaked bootstrap password or session token.

- [ ] **Step 8: Commit the Phase 1 runnable increment**

```powershell
git add .dockerignore .env.example compose.yaml docker README.md tests
git commit -m "feat: package authenticated web service"
```

## Phase 1 Review Gate

Before Phase 2:

1. Review the full diff for plaintext secrets, unsafe cookie flags, missing CSRF checks, arbitrary file serving, and unvalidated environment input.
2. Run dependency vulnerability checks for Python and npm.
3. Confirm the outer Instaloader source tree has no modifications.
4. Confirm all tests pass and coverage is at least 80%.
5. Confirm first login requires a password change and a second browser session is revoked after that change.
6. Confirm `.env`, `/data`, build output, and visual brainstorming artifacts are ignored.
