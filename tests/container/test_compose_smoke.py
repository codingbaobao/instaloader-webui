from __future__ import annotations

import json
import os
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.container

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "compose.yaml"
COMPOSE_ENV_FILE = PROJECT_ROOT / ".env.example"
COMPOSE_CONTROL_PREFIX = "COMPOSE_"


def prepare_data_root(data_root: Path, *, posix: bool = os.name == "posix") -> None:
    data_root.mkdir()
    if posix:
        data_root.chmod(0o777)


def test_prepare_data_root_is_writable_for_a_posix_bind_mount(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"

    prepare_data_root(data_root, posix=True)

    assert stat.S_IMODE(data_root.stat().st_mode) == 0o777


def test_compose_ignores_conflicting_control_environment(tmp_path: Path) -> None:
    unintended_compose = tmp_path / "unintended.yaml"
    unintended_compose.write_text(
        "services:\n  unintended:\n    image: busybox\n    profiles: [unintended]\n",
        encoding="utf-8",
    )
    unintended_env = tmp_path / "unintended.env"
    unintended_env.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "COMPOSE_FILE": str(unintended_compose),
        "COMPOSE_PROFILES": "unintended",
        "COMPOSE_ENV_FILES": str(unintended_env),
        "COMPOSE_PATH_SEPARATOR": ";",
        "COMPOSE_PROJECT_NAME": "unintended-project",
        "IW_APP_SECRET_KEY": "a" * 32,
        "IW_ADMIN_USERNAME": "smoke-owner",
    }

    configured_services = compose(
        f"iw-config-{uuid.uuid4().hex}",
        env,
        "config",
        "--services",
    )

    assert configured_services.stdout.splitlines() == ["web"]


def wait_for_ok(url: str, timeout: float) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return json.load(response)
        except (OSError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(0.5)
    raise AssertionError(f"{url} did not become healthy: {last_error}")


def compose(
    project_name: str,
    env: dict[str, str],
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    sanitized_env = {
        key: value
        for key, value in env.items()
        if not key.upper().startswith(COMPOSE_CONTROL_PREFIX)
    }
    return subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            str(COMPOSE_FILE),
            "--env-file",
            str(COMPOSE_ENV_FILE),
            "--project-name",
            project_name,
            *args,
        ],
        cwd=PROJECT_ROOT,
        check=check,
        env=sanitized_env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def published_health_url(project_name: str, env: dict[str, str]) -> str:
    mapping = compose(project_name, env, "port", "web", "8080").stdout.strip()
    if not mapping.startswith("127.0.0.1:"):
        raise AssertionError(f"unexpected test-only port mapping: {mapping}")
    host_port = int(mapping.rsplit(":", 1)[1])
    return f"http://127.0.0.1:{host_port}/api/health"


def cleanup_project(project_name: str, env: dict[str, str]) -> None:
    compose(
        project_name,
        env,
        "down",
        "--volumes",
        "--rmi",
        "local",
    )


def test_container_rejects_one_character_app_secret(tmp_path: Path) -> None:
    project_name = f"iw-short-secret-{uuid.uuid4().hex}"
    data_root = tmp_path / "short-secret-data"
    prepare_data_root(data_root)
    env = {
        **os.environ,
        "IW_DATA_ROOT_HOST": str(data_root),
        "IW_HTTP_BIND": "127.0.0.1",
        "IW_HTTP_PORT": "0",
        "IW_APP_SECRET_KEY": "Z",
        "IW_ADMIN_USERNAME": "smoke-owner",
        "IW_ADMIN_PASSWORD": "test-only-" + uuid.uuid4().hex,
        "IW_SESSION_COOKIE_SECURE": "false",
    }

    try:
        startup = compose(
            project_name,
            env,
            "up",
            "-d",
            "--build",
            "--wait",
            "--wait-timeout",
            "15",
            check=False,
        )
        assert startup.returncode != 0

        logs = compose(
            project_name,
            env,
            "logs",
            "--no-color",
            "web",
            check=False,
        )
        assert "app_secret_key" in logs.stdout
        assert "input_value" not in logs.stdout
    finally:
        cleanup_project(project_name, env)


def test_compose_web_health_survives_restart(tmp_path: Path) -> None:
    project_name = f"iw-smoke-{uuid.uuid4().hex}"
    data_root = tmp_path / "data"
    prepare_data_root(data_root)
    env = {
        **os.environ,
        "IW_DATA_ROOT_HOST": str(data_root),
        "IW_HTTP_BIND": "127.0.0.1",
        "IW_HTTP_PORT": "0",
        "IW_APP_SECRET_KEY": "test-only-" + uuid.uuid4().hex,
        "IW_ADMIN_USERNAME": "smoke-owner",
        "IW_ADMIN_PASSWORD": "test-only-" + uuid.uuid4().hex,
        "IW_SESSION_COOKIE_SECURE": "false",
    }

    try:
        compose(project_name, env, "up", "-d", "--build")
        health_url = published_health_url(project_name, env)
        assert wait_for_ok(health_url, timeout=120) == {
            "success": True,
            "data": {"status": "ok"},
            "error": None,
            "meta": {},
        }

        inspect = compose(
            project_name,
            env,
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            (
                "import importlib.metadata, json, os, pathlib, shutil; "
                "print(json.dumps({"
                "'uid': os.getuid(), "
                "'gid': os.getgid(), "
                "'pip': importlib.metadata.version('pip'), "
                "'node': shutil.which('node'), "
                "'npm': shutil.which('npm'), "
                "'database': pathlib.Path('/data/database/app.sqlite3').is_file(), "
                "'forbidden': [str(path) for path in ("
                "pathlib.Path('/app/frontend'), pathlib.Path('/app/backend'), "
                "pathlib.Path('/app/.env'), pathlib.Path('/app/.git'), "
                "pathlib.Path('/app/.superpowers')) if path.exists()]"
                "}))"
            ),
        )
        runtime = json.loads(inspect.stdout)
        assert runtime | {"pip": None} == {
            "uid": 10001,
            "gid": 10001,
            "pip": None,
            "node": None,
            "npm": None,
            "database": True,
            "forbidden": [],
        }
        pip_version = tuple(int(part) for part in runtime["pip"].split("."))
        assert (26, 1, 2) <= pip_version < (27,)

        post_bootstrap_env = {**env, "IW_ADMIN_PASSWORD": ""}
        compose(
            project_name,
            post_bootstrap_env,
            "up",
            "-d",
            "--force-recreate",
        )
        health_url = published_health_url(project_name, post_bootstrap_env)
        assert wait_for_ok(health_url, timeout=60)["data"] == {"status": "ok"}

        compose(project_name, post_bootstrap_env, "restart", "web")
        health_url = published_health_url(project_name, post_bootstrap_env)
        assert wait_for_ok(health_url, timeout=60)["data"] == {"status": "ok"}
        assert (data_root / "database" / "app.sqlite3").is_file()
    finally:
        cleanup_project(project_name, env)
