from __future__ import annotations

import logging
from collections.abc import Mapping
from itertools import pairwise
from typing import Any, cast

import pytest
from instaloader import (
    BadResponseException,
    InstaloaderContext,
    InstaloaderException,
    Profile,
    ProfileNotExistsException,
    TooManyRequestsException,
)
from requests import Response
from requests.exceptions import ConnectionError, HTTPError, RequestException

from instaloader_webui.instagram.profile_lookup import (
    ProfileLookupFailure,
    ProfileLookupResolver,
)

_EVENT_FIELDS = {"mode", "path", "outcome", "status_class"}
_ALLOWED_MODES = {"native", "fallback", "legacy"}
_ALLOWED_PATHS = {"native", "legacy"}
_ALLOWED_OUTCOMES = {"success", "fallback", "failure"}
_ALLOWED_STATUS_CLASSES = {
    "success",
    "rate_limited",
    "bad_request",
    "unauthorized",
    "forbidden",
    "not_found",
    "other_4xx",
    "server_error",
    "transport_error",
    "schema_drift",
    "unexpected_error",
}
_STANDARD_LOG_RECORD_FIELDS = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message"}


class _QueryContext:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def doc_id_graphql_query(
        self,
        doc_id: str,
        variables: Mapping[str, object],
    ) -> object:
        self.calls.append((doc_id, variables))
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


def _context(payload: object) -> InstaloaderContext:
    return cast(InstaloaderContext, _QueryContext(payload))


def _query_calls(context: InstaloaderContext) -> list[tuple[str, Mapping[str, object]]]:
    return cast(_QueryContext, cast(object, context)).calls


def _users(*nodes: object) -> dict[str, Any]:
    return {
        "data": {
            "xdt_api__v1__fbsearch__non_profiled_serp": {
                "users": list(nodes),
            }
        }
    }


def _node(username: str, profile_id: int = 123) -> dict[str, object]:
    return {"id": profile_id, "username": username}


def _http_error(status_code: object) -> HTTPError:
    response = Response()
    response.status_code = cast(int, status_code)
    return HTTPError("raw-http-secret", response=response)


def _install_native(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: Profile | BaseException,
) -> list[tuple[InstaloaderContext, str]]:
    calls: list[tuple[InstaloaderContext, str]] = []

    def from_username(context: InstaloaderContext, username: str) -> Profile:
        calls.append((context, username))
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(Profile, "from_username", staticmethod(from_username))
    return calls


def _records(
    caplog: pytest.LogCaptureFixture,
    *,
    logger_name: str,
) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == logger_name]


def _event_values(record: logging.LogRecord) -> tuple[object, object, object, object]:
    return (
        record.__dict__["mode"],
        record.__dict__["path"],
        record.__dict__["outcome"],
        record.__dict__["status_class"],
    )


def _assert_terminal_failure(
    raised: BaseException,
    terminal_error: BaseException,
) -> None:
    if isinstance(terminal_error, RequestException):
        assert isinstance(raised, ProfileLookupFailure)
        assert raised.terminal_error is terminal_error
        assert raised.__cause__ is terminal_error
        assert "secret" not in str(raised)
    else:
        assert raised is terminal_error


def test_fallback_native_success_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Calling legacy after a successful native lookup must fail this test."""
    context = _context(_users(_node("Target")))
    native_profile = Profile(context, _node("Target"))
    native_calls = _install_native(monkeypatch, result=native_profile)
    logger_name = "test.profile_lookup.native_success"
    resolver = ProfileLookupResolver("fallback", logging.getLogger(logger_name))

    with caplog.at_level(logging.INFO, logger=logger_name):
        result = resolver.resolve(context, "Target")

    assert result is native_profile
    assert native_calls == [(context, "Target")]
    assert _query_calls(context) == []
    [record] = _records(caplog, logger_name=logger_name)
    assert _event_values(record) == (
        "fallback",
        "native",
        "success",
        "success",
    )


def test_native_mode_calls_only_native_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling legacy or repeating native in native mode must fail this test."""
    context = _context(_users(_node("Target")))
    profile = Profile(context, _node("Target"))
    native_calls = _install_native(monkeypatch, result=profile)

    assert ProfileLookupResolver("native", logging.getLogger(__name__)).resolve(
        context, "Target"
    ) is profile
    assert native_calls == [(context, "Target")]
    assert _query_calls(context) == []


def test_legacy_mode_calls_only_exact_legacy_query_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native call or changed doc-id query in legacy mode must fail this test."""
    context = _context(_users(_node("Target")))
    native_calls = _install_native(
        monkeypatch,
        result=AssertionError("native must not run"),
    )

    result = ProfileLookupResolver("legacy", logging.getLogger(__name__)).resolve(
        context, "Target"
    )

    assert isinstance(result, Profile)
    assert native_calls == []
    assert _query_calls(context) == [
        (
            "26347858941511777",
            {"hasQuery": True, "query": "Target"},
        )
    ]


def test_resolver_rejects_a_mode_outside_the_closed_set() -> None:
    """Accepting an unconfigured fourth branch must fail this test."""
    with pytest.raises(ValueError):
        ProfileLookupResolver(cast(Any, "automatic"), logging.getLogger(__name__))


def test_fallback_uses_legacy_once_for_native_typed_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing or repeated fallback after a typed rate limit must fail this test."""
    context = _context(_users(_node("Target")))
    rate_limit = TooManyRequestsException("private-native-detail")
    native_calls = _install_native(monkeypatch, result=rate_limit)

    result = ProfileLookupResolver("fallback", logging.getLogger(__name__)).resolve(
        context, "Target"
    )

    assert isinstance(result, Profile)
    assert native_calls == [(context, "Target")]
    assert len(_query_calls(context)) == 1


@pytest.mark.parametrize("chain_attribute", ["__cause__", "__context__"])
def test_fallback_recognizes_typed_http_429_in_preserved_chain(
    monkeypatch: pytest.MonkeyPatch,
    chain_attribute: str,
) -> None:
    """Ignoring a typed Requests 429 cause or context must fail this test."""
    context = _context(_users(_node("Target")))
    wrapper = BadResponseException("wrapper without a status")
    setattr(wrapper, chain_attribute, _http_error(429))
    native_calls = _install_native(monkeypatch, result=wrapper)

    result = ProfileLookupResolver("fallback", logging.getLogger(__name__)).resolve(
        context, "Target"
    )

    assert isinstance(result, Profile)
    assert native_calls == [(context, "Target")]
    assert len(_query_calls(context)) == 1


def test_fallback_accepts_typed_rate_limit_at_eighth_unique_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping before the eighth unique exception node must fail this test."""
    context = _context(_users(_node("Target")))
    chain: list[BaseException] = [RuntimeError(f"wrapper-{index}") for index in range(7)]
    chain.append(TooManyRequestsException("private-native-detail"))
    for current, cause in pairwise(chain):
        current.__cause__ = cause
    _install_native(monkeypatch, result=chain[0])

    assert isinstance(
        ProfileLookupResolver("fallback", logging.getLogger(__name__)).resolve(
            context, "Target"
        ),
        Profile,
    )
    assert len(_query_calls(context)) == 1


def test_fallback_fails_closed_when_exception_graph_exceeds_eight_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back from an overlong graph, even with an early typed 429, must fail."""
    context = _context(_users(_node("Target")))
    chain: list[BaseException] = [RuntimeError(f"wrapper-{index}") for index in range(9)]
    chain[3] = TooManyRequestsException("private-native-detail")
    for current, cause in pairwise(chain):
        current.__cause__ = cause
    _install_native(monkeypatch, result=chain[0])

    with pytest.raises(RuntimeError) as raised:
        ProfileLookupResolver("fallback", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    assert raised.value is chain[0]
    assert _query_calls(context) == []


def test_fallback_fails_closed_for_a_cyclic_exception_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back from a cyclic graph containing a typed 429 must fail this test."""
    context = _context(_users(_node("Target")))
    wrapper = RuntimeError("wrapper")
    rate_limit = TooManyRequestsException("private-native-detail")
    wrapper.__cause__ = rate_limit
    rate_limit.__context__ = wrapper
    _install_native(monkeypatch, result=wrapper)

    with pytest.raises(RuntimeError) as raised:
        ProfileLookupResolver("fallback", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    assert raised.value is wrapper
    assert _query_calls(context) == []


@pytest.mark.parametrize(
    "native_error",
    [
        BadResponseException("429 is only untrusted text"),
        type("TooManyRequestsException", (RuntimeError,), {})(),
        _http_error("429"),
        _http_error(400),
        _http_error(401),
        _http_error(403),
        _http_error(404),
        ProfileNotExistsException("private target does not exist"),
        ConnectionError("private transport detail"),
        OSError("private filesystem detail"),
    ],
)
def test_fallback_does_not_use_legacy_for_untyped_or_non_429_failures(
    monkeypatch: pytest.MonkeyPatch,
    native_error: BaseException,
) -> None:
    """Text matching or broad failure fallback must fail this test."""
    context = _context(_users(_node("Target")))
    native_calls = _install_native(monkeypatch, result=native_error)

    expected_type = (
        ProfileLookupFailure
        if isinstance(native_error, RequestException)
        else type(native_error)
    )
    with pytest.raises(expected_type) as raised:
        ProfileLookupResolver("fallback", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    _assert_terminal_failure(raised.value, native_error)
    assert native_calls == [(context, "Target")]
    assert _query_calls(context) == []


def test_fallback_rejects_inaccessible_http_status_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treating inaccessible status metadata as a 429 must fail this test."""

    class ExplodingResponse:
        @property
        def status_code(self) -> int:
            raise RuntimeError("status-secret")

    context = _context(_users(_node("Target")))
    native_error = HTTPError("private transport detail", response=ExplodingResponse())
    _install_native(monkeypatch, result=native_error)

    with pytest.raises(ProfileLookupFailure) as raised:
        ProfileLookupResolver("fallback", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    _assert_terminal_failure(raised.value, native_error)
    assert _query_calls(context) == []


def test_fallback_rejects_boolean_http_429_status_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treating bool, an int subclass, as an integer HTTP status must fail."""
    context = _context(_users(_node("Target")))
    native_error = _http_error(True)
    native_calls = _install_native(monkeypatch, result=native_error)

    with pytest.raises(ProfileLookupFailure) as raised:
        ProfileLookupResolver("fallback", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    _assert_terminal_failure(raised.value, native_error)
    assert native_calls == [(context, "Target")]
    assert _query_calls(context) == []


def test_legacy_casefold_exact_match_returns_profile_bound_to_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case-sensitive matching or replacing the supplied context must fail this test."""
    context = _context(_users(_node("STRASSE")))
    _install_native(monkeypatch, result=AssertionError("native must not run"))

    profile = ProfileLookupResolver("legacy", logging.getLogger(__name__)).resolve(
        context, "Straße"
    )

    assert type(profile) is Profile
    assert profile.username == "strasse"
    assert profile._context is context
    assert profile._has_full_metadata is False


@pytest.mark.parametrize(
    "nodes",
    [
        (),
        (_node("TargetSuffix"),),
        (_node("PrefixTarget"),),
        (_node(" Target "),),
        (_node("Targat"),),
    ],
)
def test_legacy_complete_list_without_exact_match_is_profile_not_found(
    monkeypatch: pytest.MonkeyPatch,
    nodes: tuple[object, ...],
) -> None:
    """Fuzzy, trimmed, substring, or empty-list matching must fail this test."""
    context = _context(_users(*nodes))
    _install_native(monkeypatch, result=AssertionError("native must not run"))

    with pytest.raises(ProfileNotExistsException):
        ProfileLookupResolver("legacy", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    assert len(_query_calls(context)) == 1


@pytest.mark.parametrize(
    "payload",
    [
        _users(
            {"id": object(), "username": "Broken"},
            _node("Target"),
        ),
        _users(
            _node("Target"),
            {"pk": True, "username": "Broken"},
        ),
    ],
)
def test_legacy_validates_every_node_identifier_before_exact_match(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    """Trusting or skipping any unusable user node around a match must fail."""
    context = _context(payload)
    _install_native(monkeypatch, result=AssertionError("native must not run"))

    with pytest.raises(InstaloaderException) as raised:
        ProfileLookupResolver("legacy", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    assert not isinstance(raised.value, ProfileNotExistsException)
    assert str(raised.value) == "Instagram profile lookup response was unavailable."
    assert len(_query_calls(context)) == 1


@pytest.mark.parametrize(
    "node",
    [
        {"id": 0, "pk": 1, "username": "Target"},
        {"id": False, "pk": 1, "username": "Target"},
        {"id": "", "pk": 1, "username": "Target"},
        {"pk": 0, "username": "Target"},
        {"id": 1, "pk": False, "username": "Target"},
    ],
)
def test_legacy_rejects_each_present_invalid_identifier(
    monkeypatch: pytest.MonkeyPatch,
    node: Mapping[str, object],
) -> None:
    """Hiding an invalid id or pk behind the other identifier must fail."""
    context = _context(_users(node))
    _install_native(monkeypatch, result=AssertionError("native must not run"))

    with pytest.raises(InstaloaderException) as raised:
        ProfileLookupResolver("legacy", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    assert not isinstance(raised.value, ProfileNotExistsException)
    assert str(raised.value) == "Instagram profile lookup response was unavailable."
    assert len(_query_calls(context)) == 1


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"data": None},
        {"data": {}},
        {"data": {"xdt_api__v1__fbsearch__non_profiled_serp": None}},
        {"data": {"xdt_api__v1__fbsearch__non_profiled_serp": {}}},
        {
            "data": {
                "xdt_api__v1__fbsearch__non_profiled_serp": {"users": None}
            }
        },
        _users("not-a-mapping"),
        _users({"id": 123}),
        _users({"username": "Broken"}),
        _users(_node("Target"), {"username": "Broken"}),
        _users({"id": 123, "username": ""}),
        _users({"id": 123, "username": 456}),
    ],
)
def test_legacy_malformed_payload_fails_as_fixed_schema_error(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    """Failing open or reporting malformed payloads as not-found must fail this test."""
    context = _context(payload)
    _install_native(monkeypatch, result=AssertionError("native must not run"))

    with pytest.raises(InstaloaderException) as raised:
        ProfileLookupResolver("legacy", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    assert not isinstance(raised.value, ProfileNotExistsException)
    assert str(raised.value) == "Instagram profile lookup response was unavailable."
    assert len(_query_calls(context)) == 1


def test_fallback_legacy_failure_is_terminal_and_chained_to_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying either path or losing the native rate-limit cause must fail this test."""
    native_error = TooManyRequestsException("native-secret")
    legacy_error = _http_error(429)
    context = _context(legacy_error)
    native_calls = _install_native(monkeypatch, result=native_error)

    with pytest.raises(ProfileLookupFailure) as raised:
        ProfileLookupResolver("fallback", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    assert raised.value.terminal_error is legacy_error
    assert raised.value.__cause__ is native_error
    assert raised.value.__context__ is legacy_error
    assert native_calls == [(context, "Target")]
    assert len(_query_calls(context)) == 1


@pytest.mark.parametrize("status_code", [400, 401, 429])
def test_legacy_mode_http_failure_has_no_native_call_or_retry(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    """Retrying legacy or switching to native after a legacy HTTP failure must fail."""
    legacy_error = _http_error(status_code)
    context = _context(legacy_error)
    native_calls = _install_native(
        monkeypatch,
        result=AssertionError("native must not run"),
    )

    with pytest.raises(ProfileLookupFailure) as raised:
        ProfileLookupResolver("legacy", logging.getLogger(__name__)).resolve(
            context, "Target"
        )

    assert raised.value.terminal_error is legacy_error
    assert raised.value.__cause__ is legacy_error
    assert native_calls == []
    assert len(_query_calls(context)) == 1


def test_lookup_events_use_closed_fields_and_do_not_disclose_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Extra fields, raw exception data, target/query data, or traceback must fail."""
    username = "query-user-secret"
    native_error = TooManyRequestsException(
        "https://instagram.test/private?query=query-secret Cookie: cookie-secret"
    )
    context = _context(
        _users(
            {
                "id": 123,
                "username": username,
                "payload_secret": "response-secret",
            }
        )
    )
    _install_native(monkeypatch, result=native_error)
    logger_name = "test.profile_lookup.safe_events"

    with caplog.at_level(logging.INFO, logger=logger_name):
        result = ProfileLookupResolver(
            "fallback", logging.getLogger(logger_name)
        ).resolve(context, username)

    assert isinstance(result, Profile)
    records = _records(caplog, logger_name=logger_name)
    assert len(records) == 2
    assert [_event_values(record) for record in records] == [
        ("fallback", "native", "fallback", "rate_limited"),
        ("fallback", "legacy", "success", "success"),
    ]
    for record in records:
        application_fields = set(record.__dict__) - _STANDARD_LOG_RECORD_FIELDS
        assert application_fields == _EVENT_FIELDS
        mode, path, outcome, status_class = _event_values(record)
        assert mode in _ALLOWED_MODES
        assert path in _ALLOWED_PATHS
        assert outcome in _ALLOWED_OUTCOMES
        assert status_class in _ALLOWED_STATUS_CLASSES
        assert record.exc_info is None
        rendered = f"{record.getMessage()} {record.__dict__!r}"
        for forbidden in (
            username,
            "query-secret",
            "cookie-secret",
            "response-secret",
            "payload_secret",
            "26347858941511777",
            "hasQuery",
        ):
            assert forbidden not in rendered


@pytest.mark.parametrize(
    ("error", "expected_status_class"),
    [
        (_http_error(400), "bad_request"),
        (_http_error(401), "unauthorized"),
        (_http_error(403), "forbidden"),
        (_http_error(404), "not_found"),
        (_http_error(418), "other_4xx"),
        (_http_error(503), "server_error"),
        (ConnectionError("transport-secret"), "transport_error"),
        (RuntimeError("unexpected-secret"), "unexpected_error"),
    ],
)
def test_failure_events_classify_typed_status_without_raw_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: BaseException,
    expected_status_class: str,
) -> None:
    """Wrong typed status mapping or raw-detail logging must fail this test."""
    context = _context(_users(_node("Target")))
    _install_native(monkeypatch, result=error)
    logger_name = f"test.profile_lookup.status.{expected_status_class}"

    expected_type = (
        ProfileLookupFailure if isinstance(error, RequestException) else type(error)
    )
    with (
        caplog.at_level(logging.INFO, logger=logger_name),
        pytest.raises(expected_type),
    ):
        ProfileLookupResolver("native", logging.getLogger(logger_name)).resolve(
            context, "Target"
        )

    [record] = _records(caplog, logger_name=logger_name)
    assert _event_values(record) == (
        "native",
        "native",
        "failure",
        expected_status_class,
    )
    assert "secret" not in f"{record.getMessage()} {record.__dict__!r}"
