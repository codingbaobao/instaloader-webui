from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.container

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def reserve_host_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
    return subprocess.run(
        ["docker", "compose", "--project-name", project_name, *args],
        cwd=PROJECT_ROOT,
        check=check,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def test_compose_web_health_survives_restart(tmp_path: Path) -> None:
    project_name = f"iw-smoke-{uuid.uuid4().hex}"
    data_root = tmp_path / "data"
    data_root.mkdir()
    host_port = reserve_host_port()
    env = {
        **os.environ,
        "IW_DATA_ROOT_HOST": str(data_root),
        "IW_HTTP_PORT": str(host_port),
        "IW_APP_SECRET_KEY": "test-only-" + uuid.uuid4().hex,
        "IW_ADMIN_USERNAME": "smoke-owner",
        "IW_ADMIN_PASSWORD": "test-only-" + uuid.uuid4().hex,
        "IW_SESSION_COOKIE_SECURE": "false",
    }

    try:
        compose(project_name, env, "up", "-d", "--build")
        health_url = f"http://127.0.0.1:{host_port}/api/health"
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
                "import json, os, pathlib, shutil; "
                "print(json.dumps({"
                "'uid': os.getuid(), "
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
        assert runtime == {
            "uid": 100,
            "node": None,
            "npm": None,
            "database": True,
            "forbidden": [],
        }

        post_bootstrap_env = {**env, "IW_ADMIN_PASSWORD": ""}
        compose(
            project_name,
            post_bootstrap_env,
            "up",
            "-d",
            "--force-recreate",
        )
        assert wait_for_ok(health_url, timeout=60)["data"] == {"status": "ok"}

        compose(project_name, post_bootstrap_env, "restart", "web")
        assert wait_for_ok(health_url, timeout=60)["data"] == {"status": "ok"}
        assert (data_root / "database" / "app.sqlite3").is_file()
    finally:
        compose(
            project_name,
            env,
            "down",
            "--volumes",
            "--rmi",
            "local",
            check=False,
        )
