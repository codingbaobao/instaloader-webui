"""Application-owned Instaloader Profile lookup selection."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal, cast

from instaloader import (
    InstaloaderContext,
    InstaloaderException,
    Profile,
    ProfileNotExistsException,
    TooManyRequestsException,
)
from requests.exceptions import HTTPError, RequestException

ProfileLookupMode = Literal["native", "fallback", "legacy"]
ProfileLookupPath = Literal["native", "legacy"]
ProfileLookupOutcome = Literal["success", "fallback", "failure"]
ProfileLookupStatusClass = Literal[
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
]

_LOOKUP_MODES = frozenset({"native", "fallback", "legacy"})
_LEGACY_DOC_ID = "26347858941511777"
_LEGACY_RESULT_KEY = "xdt_api__v1__fbsearch__non_profiled_serp"
_MAX_EXCEPTION_NODES = 8
_SCHEMA_ERROR_MESSAGE = "Instagram profile lookup response was unavailable."


class _LegacyProfileLookupSchemaError(InstaloaderException):
    """Signal an incomplete legacy result without retaining response data."""


class ProfileLookupResolver:
    """Resolve a Profile through the configured lookup path."""

    def __init__(self, mode: ProfileLookupMode, logger: logging.Logger) -> None:
        if mode not in _LOOKUP_MODES:
            raise ValueError("Invalid Instagram profile lookup mode.")
        self._mode = mode
        self._logger = logger

    def resolve(self, context: InstaloaderContext, username: str) -> Profile:
        """Resolve ``username`` into a Profile bound to ``context``."""
        if self._mode == "legacy":
            return self._resolve_legacy(context, username)

        try:
            profile = Profile.from_username(context, username)
        except Exception as native_error:
            if self._mode == "fallback" and _is_bounded_typed_rate_limit(
                native_error
            ):
                self._log_event(
                    path="native",
                    outcome="fallback",
                    status_class="rate_limited",
                )
                try:
                    return self._resolve_legacy(context, username)
                except Exception as legacy_error:
                    raise legacy_error from native_error

            self._log_event(
                path="native",
                outcome="failure",
                status_class=_classify_status(native_error),
            )
            raise

        self._log_event(
            path="native",
            outcome="success",
            status_class="success",
        )
        return profile

    def _resolve_legacy(
        self,
        context: InstaloaderContext,
        username: str,
    ) -> Profile:
        try:
            profile = _resolve_profile_with_legacy_query(context, username)
        except Exception as error:
            self._log_event(
                path="legacy",
                outcome="failure",
                status_class=_classify_status(error),
            )
            raise

        self._log_event(
            path="legacy",
            outcome="success",
            status_class="success",
        )
        return profile

    def _log_event(
        self,
        *,
        path: ProfileLookupPath,
        outcome: ProfileLookupOutcome,
        status_class: ProfileLookupStatusClass,
    ) -> None:
        self._logger.info(
            "instagram_profile_lookup",
            extra={
                "mode": self._mode,
                "path": path,
                "outcome": outcome,
                "status_class": status_class,
            },
        )


def _resolve_profile_with_legacy_query(
    context: InstaloaderContext,
    username: str,
) -> Profile:
    payload = context.doc_id_graphql_query(
        _LEGACY_DOC_ID,
        {"hasQuery": True, "query": username},
    )
    users = _validate_legacy_payload(payload)
    requested_username = username.casefold()
    for node in users:
        if cast(str, node["username"]).casefold() == requested_username:
            try:
                return Profile(context, cast(dict[str, Any], node))
            except (AssertionError, KeyError, TypeError, ValueError) as error:
                raise _LegacyProfileLookupSchemaError(
                    _SCHEMA_ERROR_MESSAGE
                ) from error

    raise ProfileNotExistsException("Profile does not exist.")


def _validate_legacy_payload(payload: object) -> list[Mapping[str, object]]:
    if not isinstance(payload, Mapping) or not payload:
        raise _LegacyProfileLookupSchemaError(_SCHEMA_ERROR_MESSAGE)

    data = payload.get("data")
    if not isinstance(data, Mapping) or not data:
        raise _LegacyProfileLookupSchemaError(_SCHEMA_ERROR_MESSAGE)

    result = data.get(_LEGACY_RESULT_KEY)
    if not isinstance(result, Mapping) or not result:
        raise _LegacyProfileLookupSchemaError(_SCHEMA_ERROR_MESSAGE)

    users = result.get("users")
    if not isinstance(users, list):
        raise _LegacyProfileLookupSchemaError(_SCHEMA_ERROR_MESSAGE)

    validated_users: list[Mapping[str, object]] = []
    for node in users:
        if not isinstance(node, Mapping):
            raise _LegacyProfileLookupSchemaError(_SCHEMA_ERROR_MESSAGE)
        node_username = node.get("username")
        if not isinstance(node_username, str) or not node_username:
            raise _LegacyProfileLookupSchemaError(_SCHEMA_ERROR_MESSAGE)
        validated_users.append(node)
    return validated_users


def _is_bounded_typed_rate_limit(error: BaseException) -> bool:
    discovered: set[int] = set()
    active: set[int] = set()
    complete: set[int] = set()

    def visit(current: BaseException) -> tuple[bool, bool]:
        identity = id(current)
        if identity in active:
            return False, False
        if identity in complete:
            return False, True
        if len(discovered) >= _MAX_EXCEPTION_NODES:
            return False, False

        discovered.add(identity)
        active.add(identity)
        matched = _is_typed_rate_limit_node(current)
        children = (current.__cause__, current.__context__)

        valid = True
        for child in children:
            if child is None:
                continue
            child_matched, child_valid = visit(child)
            matched = matched or child_matched
            valid = valid and child_valid

        active.remove(identity)
        complete.add(identity)
        return matched, valid

    matched, valid = visit(error)
    return matched and valid


def _is_typed_rate_limit_node(error: BaseException) -> bool:
    if isinstance(error, TooManyRequestsException):
        return True
    return isinstance(error, HTTPError) and _http_status(error) == 429


def _http_status(error: HTTPError) -> int | None:
    try:
        response = error.response
        status_code = response.status_code if response is not None else None
    except Exception:  # noqa: BLE001 - hostile response metadata must fail closed
        return None
    return status_code if type(status_code) is int else None


def _classify_status(error: BaseException) -> ProfileLookupStatusClass:
    for current in _bounded_exception_nodes(error):
        if isinstance(current, _LegacyProfileLookupSchemaError):
            return "schema_drift"
        if isinstance(current, TooManyRequestsException):
            return "rate_limited"
        if isinstance(current, ProfileNotExistsException):
            return "not_found"
        if isinstance(current, HTTPError):
            status_code = _http_status(current)
            if status_code == 429:
                return "rate_limited"
            if status_code == 400:
                return "bad_request"
            if status_code == 401:
                return "unauthorized"
            if status_code == 403:
                return "forbidden"
            if status_code == 404:
                return "not_found"
            if status_code is not None and 400 <= status_code < 500:
                return "other_4xx"
            if status_code is not None and 500 <= status_code < 600:
                return "server_error"
        if isinstance(current, RequestException):
            return "transport_error"
    return "unexpected_error"


def _bounded_exception_nodes(error: BaseException) -> tuple[BaseException, ...]:
    pending = [error]
    seen: set[int] = set()
    nodes: list[BaseException] = []
    while pending and len(nodes) < _MAX_EXCEPTION_NODES:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        nodes.append(current)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
    return tuple(nodes)
