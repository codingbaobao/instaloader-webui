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
LOCAL_BUILD_COMPOSE_FILE = PROJECT_ROOT / "compose.build.yaml"
COMPOSE_ENV_FILE = PROJECT_ROOT / ".env.example"
COMPOSE_CONTROL_PREFIX = "COMPOSE_"


def test_compose_declares_runtime_security_boundaries() -> None:
    compose_source = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "read_only: true" in compose_source
    assert "cap_drop:" in compose_source
    assert "- ALL" in compose_source
    assert "no-new-privileges:true" in compose_source
    assert "tmpfs:" in compose_source
    assert 'FORWARDED_ALLOW_IPS: "${IW_FORWARDED_ALLOW_IPS:-127.0.0.1}"' in (
        compose_source
    )


def resolved_compose_config(*compose_files: Path) -> dict[str, object]:
    command = ["docker", "compose"]
    for compose_file in compose_files:
        command.extend(["--file", str(compose_file)])
    command.extend(
        [
            "--env-file",
            str(COMPOSE_ENV_FILE),
            "config",
            "--format",
            "json",
        ]
    )
    return json.loads(
        subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        ).stdout
    )


def test_deployment_compose_uses_only_the_published_image() -> None:
    config = resolved_compose_config(COMPOSE_FILE)
    services = config["services"]

    assert services["web"]["image"] == "z21012101/instaloader-webui:latest"
    assert services["worker"]["image"] == "z21012101/instaloader-webui:latest"
    assert "build" not in services["web"]
    assert "build" not in services["worker"]


def test_local_build_override_builds_one_shared_local_image() -> None:
    config = resolved_compose_config(COMPOSE_FILE, LOCAL_BUILD_COMPOSE_FILE)
    services = config["services"]

    assert services["web"]["image"] == "instaloader-webui:local"
    assert services["worker"]["image"] == "instaloader-webui:local"
    assert services["web"]["build"]["dockerfile"].endswith("docker/Dockerfile")
    assert "build" not in services["worker"]


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
            "--file",
            str(LOCAL_BUILD_COMPOSE_FILE),
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


def test_compose_web_health_survives_restart(tmp_path: Path) -> None:
    project_name = f"iw-smoke-{uuid.uuid4().hex}"
    data_root = tmp_path / "data"
    prepare_data_root(data_root)
    env = {
        **os.environ,
        "IW_DATA_ROOT_HOST": str(data_root),
        "IW_HTTP_BIND": "127.0.0.1",
        "IW_HTTP_PORT": "0",
        "IW_ADMIN_USERNAME": "smoke-owner",
        "IW_ADMIN_PASSWORD": "",
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
                "import hashlib, importlib.metadata, json, os, pathlib, shutil; "
                "app_process = next(path for path in pathlib.Path('/proc').iterdir() "
                "if path.name.isdigit() and (path / 'cmdline').is_file() "
                "and any(token.endswith(b'/uvicorn') for token in "
                "(path / 'cmdline').read_bytes().split(b'\\0'))); "
                "status = dict(line.split(':', 1) for line in "
                "(app_process / 'status').read_text().splitlines() "
                "if ':' in line); "
                "print(json.dumps({"
                "'uid': os.getuid(), "
                "'gid': os.getgid(), "
                "'pip': importlib.metadata.version('pip'), "
                "'node': shutil.which('node'), "
                "'npm': shutil.which('npm'), "
                "'database': pathlib.Path('/data/database/app.sqlite3').is_file(), "
                "'database_mode': oct(pathlib.Path('/data/database/app.sqlite3')"
                ".stat().st_mode & 0o777), "
                "'database_directory_mode': oct(pathlib.Path('/data/database')"
                ".stat().st_mode & 0o777), "
                "'app_secret': pathlib.Path('/data/secrets/app_secret_key')"
                ".is_file(), "
                "'app_secret_mode': oct(pathlib.Path('/data/secrets/app_secret_key')"
                ".stat().st_mode & 0o777), "
                "'app_secret_digest': hashlib.sha256("
                "pathlib.Path('/data/secrets/app_secret_key').read_bytes()).hexdigest(), "
                "'umask': status['Umask'].strip(), "
                "'no_new_privileges': status['NoNewPrivs'].strip(), "
                "'effective_capabilities': status['CapEff'].strip(), "
                "'forbidden': [str(path) for path in ("
                "pathlib.Path('/app/frontend'), pathlib.Path('/app/backend'), "
                "pathlib.Path('/app/.env'), pathlib.Path('/app/.git'), "
                "pathlib.Path('/app/.superpowers')) if path.exists()]"
                "}))"
            ),
        )
        runtime = json.loads(inspect.stdout)
        app_secret_digest = runtime.pop("app_secret_digest")
        assert runtime | {"pip": None} == {
            "uid": 10001,
            "gid": 10001,
            "pip": None,
            "node": None,
            "npm": None,
            "database": True,
            "database_mode": "0o600",
            "database_directory_mode": "0o700",
            "app_secret": True,
            "app_secret_mode": "0o600",
            "umask": "0077",
            "no_new_privileges": "1",
            "effective_capabilities": "0000000000000000",
            "forbidden": [],
        }
        pip_version = tuple(int(part) for part in runtime["pip"].split("."))
        assert (26, 1, 2) <= pip_version < (27,)
        container_id = compose(project_name, env, "ps", "-q", "web").stdout.strip()
        container_inspect = json.loads(
            subprocess.run(
                ["docker", "inspect", container_id],
                check=True,
                text=True,
                encoding="utf-8",
                capture_output=True,
            ).stdout
        )[0]["HostConfig"]
        assert container_inspect["ReadonlyRootfs"] is True
        assert container_inspect["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in container_inspect["SecurityOpt"]
        assert "/tmp" in container_inspect["Tmpfs"]

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
        restarted_secret_digest = compose(
            project_name,
            post_bootstrap_env,
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            (
                "import hashlib, pathlib; "
                "print(hashlib.sha256(pathlib.Path("
                "'/data/secrets/app_secret_key').read_bytes()).hexdigest())"
            ),
        ).stdout.strip()
        assert restarted_secret_digest == app_secret_digest
        assert (data_root / "database" / "app.sqlite3").is_file()
    finally:
        cleanup_project(project_name, env)
