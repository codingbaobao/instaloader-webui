from pathlib import Path
from typing import cast

import pytest

from instaloader_webui.instagram.session_store import InstagramSessionStore
from instaloader_webui.instagram.worker_runtime import WorkerInstaloaderRuntime


class EmptySessionStore:
    def load(self) -> None:
        return None


def test_context_error_redacts_sensitive_url_and_cookie_details_from_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Break caught: Instaloader's 429 warning prints the raw response URL before
    # the worker can classify the exception into a safe issue.
    runtime = WorkerInstaloaderRuntime(
        cast(InstagramSessionStore, EmptySessionStore())
    )
    loader, configured = runtime.acquire(tmp_path / "staging")
    assert configured is False

    loader.context.error(
        "429 retrying "
        "https://www.instagram.com/graphql/query/"
        "?query_hash=query-marker-91&variables=secret-marker-37 "
        "Cookie: cookie-secret-14; "
        "sessionid=session-secret-26; "
        "csrftoken=csrf-secret-58; "
        "igsh=igsh-secret-63"
    )

    stderr = capsys.readouterr().err
    runtime.close()

    assert "429 retrying" in stderr
    for forbidden in (
        "query-marker-91",
        "secret-marker-37",
        "cookie-secret-14",
        "session-secret-26",
        "csrf-secret-58",
        "igsh-secret-63",
        "cookie",
        "sessionid",
        "csrftoken",
        "igsh",
    ):
        assert forbidden.casefold() not in stderr.casefold()
    assert loader.context.error_log == [stderr.strip()]


def test_context_error_keeps_warning_output_bounded(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Break caught: forwarding an unexpectedly large upstream response can
    # flood stderr and the repeated error log.
    runtime = WorkerInstaloaderRuntime(
        cast(InstagramSessionStore, EmptySessionStore())
    )
    loader, _configured = runtime.acquire(tmp_path / "staging")

    loader.context.error(f"429 retrying {'x' * 10_000}")

    stderr = capsys.readouterr().err
    runtime.close()

    assert stderr.startswith("429 retrying ")
    assert len(stderr.rstrip("\n")) <= 2048
    assert loader.context.error_log == [stderr.strip()]


def test_context_error_redacts_repr_style_secret_mappings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Break caught: quoted mapping keys bypass simple key=value redaction and
    # disclose cookie values from exception repr strings.
    runtime = WorkerInstaloaderRuntime(
        cast(InstagramSessionStore, EmptySessionStore())
    )
    loader, _configured = runtime.acquire(tmp_path / "staging")

    loader.context.error(
        "429 request rejected "
        "cookies={'SeSsIoNiD': 'dummy-one-marker', "
        "'csrftoken': 'dummy-two-marker'} "
        'headers={"COOKIE": "dummy-three-marker"}'
    )

    stderr = capsys.readouterr().err
    runtime.close()

    assert "429 request rejected" in stderr
    assert len(stderr.rstrip("\n")) <= 2048
    for forbidden in (
        "sessionid",
        "csrftoken",
        "cookie",
        "dummy-one-marker",
        "dummy-two-marker",
        "dummy-three-marker",
    ):
        assert forbidden.casefold() not in stderr.casefold()
    assert loader.context.error_log == [stderr.strip()]
