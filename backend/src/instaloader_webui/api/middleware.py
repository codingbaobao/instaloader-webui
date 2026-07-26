import logging
from collections.abc import Awaitable, Callable

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

MAXIMUM_REQUEST_BODY_BYTES = 16 * 1024
MAXIMUM_REQUEST_BODY_FRAMES = 128

SECURITY_HEADERS = (
    (
        b"content-security-policy",
        (
            b"default-src 'self'; script-src 'self'; style-src 'self'; "
            b"img-src 'self' data:; media-src 'self'; connect-src 'self'; "
            b"font-src 'self'; frame-ancestors 'none'; object-src 'none'; "
            b"base-uri 'none'; form-action 'self'"
        ),
    ),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (
        b"permissions-policy",
        b"camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    ),
)

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
logger = logging.getLogger(__name__)


def _request_too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": "request_too_large",
                "message": "The request body is too large.",
            },
            "meta": {},
        },
    )


def _internal_error_response() -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": "internal_error",
                "message": "An internal server error occurred.",
            },
            "meta": {},
        },
    )


class RequestBodyLimitMiddleware:
    """Bound HTTP request bodies before framework JSON parsing."""

    def __init__(
        self,
        app: ASGIApp,
        maximum_bytes: int = MAXIMUM_REQUEST_BODY_BYTES,
    ) -> None:
        self._app = app
        self._maximum_bytes = maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if self._declared_too_large(scope):
            await _request_too_large_response()(scope, receive, send)
            return

        buffered = await self._read_bounded(receive)
        if buffered is None:
            await _request_too_large_response()(scope, receive, send)
            return
        position = 0

        async def replay_receive() -> Message:
            nonlocal position
            if position < len(buffered):
                message = buffered[position]
                position += 1
                return message
            return await receive()

        await self._app(scope, replay_receive, send)

    def _declared_too_large(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                return int(value) > self._maximum_bytes
            except ValueError:
                return False
        return False

    async def _read_bounded(self, receive: Receive) -> tuple[Message, ...] | None:
        body = bytearray()
        frame_count = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                prefix: tuple[Message, ...] = ()
                if body:
                    prefix = (
                        {
                            "type": "http.request",
                            "body": bytes(body),
                            "more_body": True,
                        },
                    )
                return (*prefix, message)
            frame_count += 1
            if frame_count > MAXIMUM_REQUEST_BODY_FRAMES:
                return None
            body.extend(message.get("body", b""))
            if len(body) > self._maximum_bytes:
                return None
            if not message.get("more_body", False):
                return (
                    {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    },
                )


class SafeExceptionMiddleware:
    """Normalize unhandled errors before Starlette's outer server middleware."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, receive, tracked_send)
        except Exception as error:
            logger.error(
                "Unhandled request error method=%s path=%s type=%s",
                scope.get("method", ""),
                scope.get("path", ""),
                type(error).__name__,
            )
            if response_started:
                raise
            await _internal_error_response()(scope, receive, send)


class SecurityHeadersMiddleware:
    """Apply browser defenses without adding transport-layer HSTS."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] != "http.response.start":
                await send(message)
                return
            additions = list(SECURITY_HEADERS)
            if scope.get("path", "").startswith("/api/"):
                additions.append((b"cache-control", b"no-store"))
            names = {name for name, _value in additions}
            current = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() not in names
            ]
            await send({**message, "headers": [*current, *additions]})

        await self._app(scope, receive, send_with_headers)
