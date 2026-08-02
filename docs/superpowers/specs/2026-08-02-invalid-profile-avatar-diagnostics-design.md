# Invalid Profile Avatar Diagnostics Design

## Goal

When profile synchronization rejects an avatar response because its normalized
`Content-Type` is not `image/jpeg`, emit one bounded diagnostic event that
explains what the worker actually received. Keep the existing user-facing error
and failure behavior unchanged.

## Current Behavior and Evidence

`PublicInstaloaderAdapter._refresh_profile_avatar()` obtains the avatar with
Instaloader's `get_raw()`, normalizes the response `Content-Type`, and rejects
anything other than `image/jpeg`. The current code discards the response without
recording its status, headers, final CDN host, or body signature.

Static review did not find a path that would turn a literal `image/jpeg` header
into a different normalized value. A local authenticated legacy lookup also
returned the same avatar URL on two consecutive property reads, and both
responses were `200 image/jpeg` with a JPEG magic prefix. The production failure
therefore cannot be explained further from existing logs.

The existing unit fixture labels arbitrary bytes (`b"avatar-image"`) as JPEG and
only models the `Content-Type` header. It does not exercise diagnostic evidence
or verify that sensitive URL and response data remain out of logs.

## Proposed Behavior

Add a small private diagnostic helper beside the avatar refresh code. Call it
only after `Content-Type` normalization fails and before raising the existing
`PublicInstagramAdapterError`.

The helper emits one warning event named
`instagram_profile_avatar_invalid_response` with these bounded fields rendered
in the log message so they remain visible with the worker's current logging
configuration:

- HTTP status code;
- raw and normalized `Content-Type`;
- `Content-Length`, `Content-Encoding`, and `Transfer-Encoding`;
- final response CDN hostname and URL path suffix;
- redirect count;
- SHA-256 digest of the complete avatar URL for correlation without disclosure;
- at most the first 64 response bytes as hexadecimal;
- a detected prefix kind: `jpeg`, `png`, `webp`, `gif`, `html`, `json`, `empty`,
  `unreadable`, or `other`;
- the local profile identifier for correlation with the job database.

Header values will be represented safely and bounded before rendering. The
event must never contain the complete URL, URL query, Cookie values, request
headers, complete response headers, or the complete response body.
If the bounded body prefix contains a credential marker such as `Cookie`,
`sessionid`, or `token`, its hexadecimal value is replaced by `[redacted]`.

## Data Flow

1. Resolve the profile and avatar URL as today.
2. Call `loader.context.get_raw()` as today.
3. Normalize the response `Content-Type` as today.
4. If it equals `image/jpeg`, save the response exactly as today and emit no
   diagnostic event.
5. Otherwise, collect the bounded diagnostic fields. Reading the body prefix is
   safe because this branch always rejects and never persists the response.
6. Emit one warning and raise the existing
   `Instagram returned an invalid profile avatar image.` error.
7. Close the response in the existing `finally` block.

## Failure Containment

Diagnostic collection is best-effort. Missing or hostile response attributes,
URL parsing failures, and body-prefix read failures must not replace the stable
user-facing error. An unreadable prefix is reported as `unreadable`; unavailable
scalar fields use a bounded sentinel. No exception traceback or upstream body is
logged.

The change does not alter which MIME types are accepted, does not retry
Instagram, does not make avatar failure non-fatal, and does not persist a
diagnostic artifact under `/data`.

## Testing

Use test-driven development against the latest `origin/main` baseline.

1. Extend the avatar response fixture with status, URL, redirect history,
   encoding headers, and a realistic body.
2. Add a failing test proving that an invalid response emits exactly one warning
   containing all approved diagnostic fields and a correctly detected body
   prefix.
3. Assert that signed query values, Cookie-like markers, unbounded header text,
   and body content beyond the prefix are absent from the rendered log.
4. Add a failure-containment test proving that diagnostic read errors preserve
   the existing `PublicInstagramAdapterError` and still close the response.
5. Confirm valid `image/jpeg` responses emit no invalid-response warning and
   retain existing save behavior.
6. Run the focused avatar/profile-sync tests, then the complete backend test,
   Ruff, and Mypy checks available to CI.

## Out of Scope

- Changing native-versus-legacy profile lookup selection.
- Relaxing the accepted avatar MIME type.
- Retrying, pacing, or changing Instagram rate-limit behavior.
- Logging or storing complete upstream responses.
- Changing job state, API responses, or frontend behavior.
