# Unified Profile Lookup Gateway Design

## Status

Approved in conversation on 2026-08-02. This document defines the application
boundary and test requirements. It does not authorize a dependency upgrade,
deployment, release, or live Instagram operation beyond the completed
diagnostic.

## Context

Instaloader owns the `Profile` class and its upstream
`Profile.from_username(context, username)` class method. Instaloader 4.15.3
changed that method from the 4.15.2 doc-id GraphQL search to
`api/v1/users/web_profile_info/`.

The application already contains a guarded `ProfileLookupResolver`. It calls
the upstream method first and, only after a typed rate-limit failure, can issue
the 4.15.2-compatible lookup. Public Profile creation and Profile sync use this
resolver, but followee discovery still calls `Profile.from_username()`
directly. The fallback is therefore a partial integration rather than a single
application boundary.

On 2026-08-02, a controlled live diagnostic used one user-supplied Instagram
Cookie session with Instaloader 4.15.3, one fail-fast rate controller, and no
response or credential logging. It observed:

- the Cookie session validated successfully;
- the native 4.15.3 `web_profile_info` request returned HTTP 429 and surfaced
  as an Instaloader `ConnectionException`;
- the 4.15.2-compatible doc-id lookup succeeded with the same session;
- the returned Profile successfully yielded a non-empty first followee page.

The live diagnostic did not enumerate all followees and is not part of the
automated test suite. The Cookie contents, username, query values, response
body, and raw exception were not recorded.

## Goals

- Make one application-owned Profile lookup gateway the only production entry
  point for resolving a username into an Instaloader `Profile`.
- Keep the upstream `Profile` class and `Profile.from_username()` method
  unmodified.
- Call the current upstream method first so a future Instaloader fix becomes
  effective through a dependency update alone.
- Use the existing narrow legacy lookup only after a bounded typed native 429.
- Route followee discovery through the same gateway as Profile creation and
  Profile sync.
- Prevent future application code from bypassing the gateway accidentally.
- Keep callers unchanged when the fallback implementation is eventually
  removed.

## Non-goals

- Monkey-patching or replacing `instaloader.Profile.from_username` globally.
- Subclassing Instaloader `Profile` or changing the type returned to callers.
- Forking, vendoring, or modifying the installed Instaloader package.
- Adding a second implementation of followee pagination.
- Falling back when `Profile.get_followees()` or a later pagination request is
  rate-limited.
- Retrying native or legacy requests, adding backoff loops, or switching from a
  failed legacy request back to native.
- Changing the public API, UI, database schema, or stored Instagram session.
- Logging Instagram usernames, Cookie material, query values, URLs, response
  bodies, or raw upstream exceptions.

## Considered Approaches

### Selected: application-owned gateway

Retain `ProfileLookupResolver.resolve(context, username)` as the sole gateway.
Every application call site receives or reaches the same resolver through
dependency injection. Only the resolver may directly invoke upstream
`Profile.from_username()`.

This keeps upstream behavior authoritative, localizes compatibility code, and
allows a dependency-only upgrade to use a repaired native implementation
without editing callers.

### Rejected: process-wide monkey patch

Replacing `Profile.from_username` at import time would also alter Instaloader
internals and any unrelated library consumer in the process. Its behavior would
depend on import order and could silently conflict with a future upstream
signature or implementation change.

### Rejected: application `Profile` subclass

Instaloader factories and iterators return upstream `Profile` objects, not an
application subclass. A subclass would not reliably intercept resolution and
would introduce type and construction inconsistencies.

## Architecture

`ProfileLookupResolver` is an application-owned compatibility boundary, not an
upstream replacement. Its public contract remains:

```python
class ProfileLookupResolver:
    def resolve(
        self,
        context: InstaloaderContext,
        username: str,
    ) -> Profile: ...
```

The gateway returns an ordinary Instaloader `Profile` bound to the supplied
context. Callers do not know whether native or legacy resolution produced it.
They continue using normal upstream properties and iterators.

Production code follows this dependency direction:

```text
Profile creation -----------+
Profile sync ---------------+--> ProfileLookupResolver --> upstream method
Followee discovery ---------+                           \-> legacy lookup on 429
```

No production module outside `instagram/profile_lookup.py` may call
`Profile.from_username()` directly. An architecture regression test enforces
this source-level rule. This makes the gateway mandatory for existing and
future WebUI call sites without modifying the third-party class globally.

## Integration Changes

`FolloweeDiscoveryAdapter` receives a required `ProfileLookupResolver`
dependency. During discovery it uses:

```python
source_profile = resolver.resolve(loader.context, logged_in_username)
```

instead of calling `Profile.from_username()` directly.

The worker composition root constructs one resolver from the validated lookup
mode. `JobRunner` already owns that instance for public adapters; it passes the
same instance into every `FolloweeDiscoveryAdapter` it creates. Resolver mode,
logging, and fallback rules therefore cannot diverge between job types.

Public Profile creation and Profile sync retain their existing resolver calls.
No caller duplicates native-versus-legacy selection.

## Lookup and Followee Data Flow

1. Followee discovery acquires the authenticated loader for the exact queued
   Cookie revision.
2. It verifies that `loader.test_login()` matches the queued source account.
3. It asks the shared Profile lookup gateway to resolve that username.
4. The gateway calls upstream `Profile.from_username()` once.
5. Native success returns immediately.
6. A bounded typed native 429 permits exactly one legacy doc-id lookup.
7. Every other native failure is terminal and makes no legacy request.
8. The adapter obtains the followee count when available and iterates the
   returned Profile's upstream `get_followees()` iterator.
9. Followee normalization, progress, all-or-nothing persistence, and Cookie
   revision checks remain unchanged.

The gateway encloses only Profile resolution. A rate limit during metadata
hydration or `get_followees()` pagination remains a terminal discovery failure.
Instaloader 4.15.2 and 4.15.3 use the same followee iterator query, so there is
no older pagination implementation to select.

## Error and Fallback Rules

The existing resolver remains fail-closed:

- only an actual `TooManyRequestsException`, or a typed Requests `HTTPError`
  with integer status 429 in a bounded, cycle-safe exception chain, is eligible;
- error-message text, class-name strings, and raw response content never
  trigger an additional request;
- native success never calls legacy;
- native non-429 failure never calls legacy;
- legacy failure is terminal and never retries either path;
- final failures continue through the existing safe error classifier;
- lookup observability records only mode, path, outcome, and safe status class.

The existing `native|fallback|legacy` configuration retains its semantics and
default. Both public Profile jobs and followee discovery honor the same mode.

## Dependency Upgrade and Fallback Removal

The gateway always delegates to the installed upstream method first in
`fallback` mode. When a newer Instaloader version fixes native Profile lookup,
updating the dependency makes native resolution succeed and leaves legacy code
dormant. No production caller changes are required.

After the repaired dependency is validated and the compatibility path is no
longer required, removal is isolated to the gateway and its legacy-specific
configuration/tests. The gateway interface remains, so caller code still does
not change. Keeping the gateway after fallback removal preserves a stable
application boundary for future upstream compatibility work.

## Automated Tests

Implementation uses test-driven development and adds these regressions before
production changes:

1. Followee discovery receives a resolver and uses its returned Profile.
2. A test makes direct upstream `Profile.from_username()` raise if followee
   discovery bypasses the injected resolver.
3. `JobRunner` passes its shared resolver to the created followee adapter.
4. Native success, typed-429 fallback, non-429 terminal failure, bounded
   exception traversal, schema validation, and safe events retain existing
   resolver coverage.
5. An architecture test scans production Python syntax and fails when a direct
   `Profile.from_username()` call exists outside `instagram/profile_lookup.py`.
6. Existing followee all-or-nothing, progress, session-revision, error-safety,
   worker-composition, and full backend tests remain green.

Automated tests use fakes and synthetic exceptions. They never read a real
Cookie or contact Instagram.

## Acceptance Criteria

- The only production `Profile.from_username()` call is inside the Profile
  lookup gateway.
- Profile creation, Profile sync, and followee discovery share the configured
  gateway instance and mode.
- A typed native 429 can resolve the source Profile through the legacy path and
  proceed to normal followee iteration.
- Non-429 failures and followee-pagination 429s do not cause legacy lookup or
  retries.
- No sensitive Instagram data is added to application logs or test output.
- Focused tests and the full backend test suite pass.
- The installed Instaloader package remains unmodified.
