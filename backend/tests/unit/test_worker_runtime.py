from pathlib import Path
from typing import cast

import pytest

from instaloader_webui.instagram.session_store import InstagramSessionStore
from instaloader_webui.instagram.worker_runtime import WorkerInstaloaderRuntime


class EmptySessionStore:
    def load(self) -> None:
        return None


def test_context_error_redacts_url_query_without_losing_safe_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Break caught: fail-closed secret handling must not erase an otherwise
    # safe warning merely because its Instagram URL has a query string.
    runtime = WorkerInstaloaderRuntime(
        cast(InstagramSessionStore, EmptySessionStore())
    )
    loader, configured = runtime.acquire(tmp_path / "staging")
    assert configured is False

    loader.context.error(
        "429 retrying "
        "https://www.instagram.com/graphql/query/"
        "?query_hash=query-marker-91&variables=secret-marker-37"
    )

    stderr = capsys.readouterr().err
    runtime.close()

    assert "429 retrying" in stderr
    for forbidden in (
        "query-marker-91",
        "secret-marker-37",
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

    assert stderr == "Instagram warning omitted because it contained sensitive details.\n"
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


@pytest.mark.parametrize(
    "message",
    [
        (
            "headers={'Cookie': {'sessionid': 'DUMMYSESSION', "
            "'csrftoken': 'DUMMYCSRF'}}"
        ),
        (
            "outer={layer:{HeAdErS:{cOoKiE:{IgSh:DUMMYIGSH,"
            "SeSsIoNiD:DUMMYDEEP}}}}"
        ),
        f"{'safe-prefix-' * 800} Cookie=DUMMYLATE",
    ],
)
def test_context_error_replaces_nested_sensitive_shapes_with_fixed_warning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    message: str,
) -> None:
    # Break caught: nested or mixed repr structures outgrow field-level regex
    # parsing and can retain raw secret values in stderr and error_log.
    runtime = WorkerInstaloaderRuntime(
        cast(InstagramSessionStore, EmptySessionStore())
    )
    loader, _configured = runtime.acquire(tmp_path / "staging")

    loader.context.error(message)

    stderr = capsys.readouterr().err
    runtime.close()

    assert stderr == "Instagram warning omitted because it contained sensitive details.\n"
    assert len(stderr.rstrip("\n")) <= 2048
    assert message.casefold() not in stderr.casefold()
    for forbidden in (
        "cookie",
        "sessionid",
        "csrftoken",
        "igsh",
        "dummysession",
        "dummycsrf",
        "dummyigsh",
        "dummydeep",
        "dummylate",
    ):
        assert forbidden.casefold() not in stderr.casefold()
    assert loader.context.error_log == [stderr.strip()]
