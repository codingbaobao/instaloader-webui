# Guarded Instaloader Profile Lookup Fallback Design

> **Status — superseded for schema work (2026-08-01):** This is a historical
> design record. Do not execute any migration or schema-startup instruction in
> this document. The current pre-1.0 runtime supports only the exact fresh
> schema and does not migrate older databases.

## Status

Approved for implementation. This document records the agreed design; it does
not authorize a NAS or Docker diagnostic, a push, a release, or a production
deployment.

## Context

The application is pinned to `instaloader==4.15.3`. That release fixed Post
metadata retrieval needed by this application, but it also replaced
`Profile.from_username()`'s former doc-id GraphQL search with
`users/web_profile_info/`. The current environment has observed rate limiting
at that new Profile lookup boundary. Downgrading the whole dependency would
therefore trade the Profile failure for a known Post/Reel regression.

The application currently calls `Profile.from_username()` at two owned
boundaries in `PublicInstaloaderAdapter`:

- `fetch_profile()` when a user adds a Profile;
- `sync_profile()` during the authenticated profile preflight.

Direct Post, Reel, and Story resolution has separate owner-reuse rules and does
not need a Profile lookup when an active local owner is already available.
Changing the global `Profile` class would affect those unrelated paths and any
other Instaloader consumer in the process.

An isolated probe is recorded at
`/var/tmp/instaloader-webui-profile-ab/20260731TFcsmab/summary.json`. With the
4.15.2 Profile lookup patched into a 4.15.3 runtime, it recorded one request
after a 60-second backoff, a `2xx` response, an authenticated session, and
matching Profile/username results. The copied Cookie was removed. This is
useful evidence that the narrow compatibility lookup can work, but it was a
patched-only run rather than a contemporaneous native-versus-patched A/B test.
It does not establish comparative success rates, latency, or rate-limit
behavior.

## Goals

- Keep Instaloader pinned to `4.15.3`, including its transport, session,
  sanitizer, Post, Reel, Story, and authentication behavior.
- Put a guarded, application-owned Profile lookup resolver behind both
  `PublicInstaloaderAdapter.fetch_profile()` and
  `PublicInstaloaderAdapter.sync_profile()`.
- In the default mode, try the native 4.15.3 lookup first and use one narrow
  4.15.2-compatible lookup only after a typed rate-limit failure.
- Bound each lookup operation to at most one native request and one legacy
  request, with no retry loop.
- Fail closed when the legacy response shape is incomplete or malformed.
- Preserve the existing safe API/job error classification and the
  `loader.context.error` sanitizer.
- Make selection and fallback behavior observable without logging a username,
  query, URL, Cookie, session material, header, response body, or raw exception.
- Preserve existing session preflight, avatar, Story, coordinator, direct-media,
  and local-owner invariants.

## Non-goals

- Upgrading or downgrading Instaloader from `4.15.3`.
- Forking or vendoring Instaloader, or globally monkey-patching
  `Profile.from_username`.
- Reimplementing 4.15.2 `Profile.from_id()` or `Profile._obtain_metadata()`.
- Replacing 4.15.3 transport, request sessions, rate controller, sanitizer,
  authentication, Profile hydration, Post, Reel, or Story behavior.
- Routing direct Post, Reel, or Story resolution through this resolver.
- Adding a lookup for an active Profile that direct-media resolution already
  reuses.
- Changing the rule that a missing direct-media owner may be created, or that
  an inactive local owner is rejected.
- Changing followee discovery. In particular,
  `instagram/followee_discovery.py` remains on its current path and does not use
  this resolver.
- Adding a UI, API, database, or per-job mode selector.
- Adding request retries, exponential backoff, or failover from legacy back to
  native.
- Performing a NAS/Docker diagnostic, pushing commits, publishing an image,
  creating a release, or changing production as part of this work.

## Approaches

### Selected: adapter-owned guarded resolver

Add a small resolver owned by the application and inject its configured mode
into `PublicInstaloaderAdapter`. Both owned Profile lookup sites call the same
resolver. The resolver delegates to the unmodified 4.15.3 method or to the
narrow compatibility query according to the configured mode.

This is the smallest boundary that can retain 4.15.3 everywhere else, provide
an immediate operator kill switch, and make the additional request strictly
conditional.

### Rejected: global monkey patch

Replacing `Profile.from_username` process-wide would silently alter
Instaloader behavior outside the two approved adapter calls, would be sensitive
to import order, and would make tests poor evidence of production behavior. It
also prevents native and compatibility behavior from coexisting under an
explicit mode.

### Rejected: vendor fork

A fork would make this application responsible for merging every upstream
transport, authentication, sanitizer, and data-shape change. The required
compatibility surface is one old method body, so a fork is disproportionate.

### Rejected: downgrade to 4.15.2

The application already requires the 4.15.3 Post metadata fix. A downgrade
would reintroduce the `CmzV2H-rrlI` Post regression and would also discard other
4.15.3 behavior outside Profile lookup.

### Rejected: legacy-first fallback

Calling legacy first would keep the compatibility path hot even after the
native endpoint recovers and could require a second request for failures that
native handles correctly. Native success must be terminal.

### Rejected: `native` as the default

`native` remains an operator-selectable kill switch, but it is not the default.
Making it the default would leave the reproduced native Profile lookup failure
unmitigated and would not use the narrow compatibility path supported by the
local probe. The approved default is `fallback`. The probe's lack of a
contemporaneous A/B comparison is recorded as a risk rather than used to claim
that legacy is generally superior.

## Architecture

The change has three application-owned pieces:

1. Startup configuration validates a closed `native|fallback|legacy` mode.
2. A Profile lookup resolver owns native selection, typed rate-limit
   recognition, the compatibility query, and lookup-only safe events.
3. `PublicInstaloaderAdapter` uses the resolver at its two existing lookup
   sites and leaves all work before and after lookup unchanged.

The resolver is not installed on `Profile`, `Instaloader`, or
`InstaloaderContext`. It receives the active 4.15.3 context and returns an
ordinary 4.15.3 `Profile(context, node)`. Consequently the existing loader,
Cookie session, rate controller, request sanitization, and downstream Profile
properties remain authoritative.

The fallback decision encloses only the initial native
`Profile.from_username(context, username)` call. Profile normalization,
logged-in metadata hydration, avatar retrieval, Story enumeration, Post/Reel
enumeration, filesystem work, and database work are downstream operations.
Even if one of those operations later fails with a rate-limit error, it must
not initiate the legacy lookup.

## Interfaces

The implementation should expose one closed mode and one resolver interface,
with names equivalent to:

```python
ProfileLookupMode = Literal["native", "fallback", "legacy"]

class ProfileLookupResolver:
    def __init__(self, mode: ProfileLookupMode, logger: logging.Logger) -> None: ...

    def resolve(
        self,
        context: InstaloaderContext,
        username: str,
    ) -> Profile: ...
```

`resolve()` has these contracts:

- it does not mutate `context`, `Profile`, or global process state;
- it returns an Instaloader 4.15.3 `Profile` bound to the supplied context;
- it makes zero or one native call and zero or one legacy call;
- it never catches a successful native result in order to run legacy;
- it preserves typed exception chaining with `raise ... from error` whenever
  it translates an error;
- it never makes fallback eligibility depend on `str(error)`, `error.args`, a
  raw response body, a URL, or a substring/class-name comparison.

`PublicInstaloaderAdapter.__init__()` receives a required
`profile_lookup_resolver: ProfileLookupResolver`. The production composition
root constructs it once from the already validated setting and the dedicated
logger; tests may inject a resolver/fake directly. The adapter calls
`resolve()` in exactly two places: where `fetch_profile()` and `sync_profile()`
currently call `Profile.from_username()`. There is no third integration point
for direct media or followee discovery.

The legacy implementation should be a private application function equivalent
to:

```python
def _resolve_profile_with_legacy_query(
    context: InstaloaderContext,
    username: str,
) -> Profile: ...
```

No public API response shape or persisted database schema changes.

### Typed rate-limit recognition

Fallback eligibility is determined from a bounded, cycle-safe walk of the
native exception object, following `__cause__` and `__context__` for at most
eight unique exception objects. The result is rate-limited only when either:

- a node is an actual `TooManyRequestsException`; or
- a node is `requests.exceptions.HTTPError`, its typed `response` is present,
  and `type(response.status_code) is int` with value `429`.

The second rule permits an upstream wrapper to retain an underlying 429 while
preserving the exception chain. It is deliberately limited to the Requests
`HTTPError` type already used under Instaloader, not duck-typed arbitrary
objects. A `BadResponseException` is eligible only when its typed cause/context
meets one of the rules above; the text `429` in its message is insufficient.
Missing, malformed, non-integer, or inaccessible status metadata is not
eligible. A cycle, an overlong chain, or an unrecognized wrapper fails closed
and does not fallback.

This lookup trigger is intentionally narrower than the existing user-facing
error classifier. That classifier may continue using safe broad
classification for a final error, but broad message matching must never cause
an extra Instagram request.

## Configuration

The only configuration surface is the environment variable:

```text
IW_INSTAGRAM_PROFILE_LOOKUP_MODE=native|fallback|legacy
```

The corresponding application setting is a `Literal["native", "fallback",
"legacy"]` (or an enum with the same serialized values) and defaults to
`fallback`. Values are lowercase and exact; whitespace, empty strings, mixed
case, and every other value are invalid. Existing Pydantic startup validation
must reject an invalid value before the web or worker begins serving/processing
jobs. Input values remain hidden by the existing `hide_input_in_errors=True`
setting.

The setting is process-wide, environment-only, and read at startup. Changing
it requires restarting the affected web/worker processes. It is not stored in
SQLite, returned by an API, editable in the UI, or selectable per request.

Mode semantics are exact:

| Mode | Native 4.15.3 lookup | Legacy lookup | Fallback |
| --- | --- | --- | --- |
| `native` | Exactly once | Never | Never |
| `legacy` | Never | Exactly once | Never |
| `fallback` | Exactly once | At most once, only after a typed native 429 | Never after legacy |

## Legacy Resolver Data Contract

The legacy resolver reproduces only the 4.15.2
`Profile.from_username()` lookup query:

```python
context.doc_id_graphql_query(
    "26347858941511777",
    {"hasQuery": True, "query": username},
)
```

The response path is:

```text
data.xdt_api__v1__fbsearch__non_profiled_serp.users
```

Before selecting a result, the resolver validates the entire structural path:

- the top-level response is a mapping;
- `data` is a mapping;
- `xdt_api__v1__fbsearch__non_profiled_serp` is a mapping;
- `users` is a list;
- every list item is a mapping with a non-empty string `username`.

A missing or empty top-level/data/container payload, a missing `users` member,
the wrong type at any level, or a malformed user node is schema drift. It
raises a private `InstaloaderException` subclass whose fixed message contains
no payload data; the existing adapter boundary safely classifies it as
`instagram_unavailable`/the existing transient Profile message. It must not be
converted to `ProfileNotExistsException`.

An explicitly present, structurally valid `users: []` is a complete result
list. A complete list with no node whose `user["username"].casefold()` exactly
equals `username.casefold()` raises `ProfileNotExistsException`, which preserves
the existing safe Profile-not-found classification. The resolver does not use
substring, prefix, fuzzy, locale-sensitive, or trimmed matching. A matching
node returns `Profile(context, node)` using the 4.15.3 class.

The legacy resolver does not set `_has_full_metadata`, reproduce `from_id()`, or
replace `_obtain_metadata()`. When the returned Profile needs logged-in
metadata hydration, the unmodified 4.15.3 doc-id path performs it. A failure
during that hydration is terminal for the operation and does not trigger a
native retry.

## Data Flow

### `fetch_profile()`

1. Acquire the existing loader and determine whether an authenticated session
   is configured. Session-store errors retain their current safe handling.
2. Call `resolver.resolve(loader.context, username)`.
3. Normalize the returned 4.15.3 Profile through the existing private-profile
   and metadata logic.
4. Return the existing `PublicProfile`, or send the final exception through the
   existing safe adapter classification.

No filesystem or database failure can enter the resolver or cause fallback.

### `sync_profile()`

1. Read the stored Profile and preserve the existing missing, untracked, and
   inactive behavior.
2. Create the existing staging directory.
3. Acquire and validate the required authenticated session revision. A failed
   session preflight makes zero Profile lookup requests.
4. Report the existing `profile_preflight` progress state.
5. Call `resolver.resolve(loader.context, stored_profile.username)`.
6. Normalize Profile metadata using 4.15.3 behavior.
7. Fetch and validate the avatar.
8. Persist refreshed metadata and run the existing Story-first Profile sync
   coordinator, including Reel/Post enumeration and exact progress.

The session preflight remains before Profile lookup and avatar work. Profile
lookup remains before avatar and coordinator work. A successful fallback must
continue through avatar and the Story/Reel/Post coordinator exactly like a
native Profile result.

### `fallback` request sequence

1. Call native 4.15.3 `Profile.from_username()` once.
2. Return immediately on success.
3. On failure, inspect only the bounded typed exception chain.
4. Re-raise every non-rate-limit failure unchanged.
5. On a typed rate limit, call the legacy resolver once.
6. Return its Profile on success; otherwise propagate/classify the legacy
   failure while retaining exception chaining to the native rate limit.

There is no transition from legacy to native and no transition from a failed
legacy attempt to another legacy attempt.

## Error Matrix

| Mode/path result | Next action | Requests | Final safe behavior |
| --- | --- | ---: | --- |
| `fallback`: native success | Return native Profile | 1 | Success; no legacy event/request |
| `fallback`: native typed 429, legacy success | Return legacy Profile | 2 | Success |
| `fallback`: native typed 429, legacy complete list has no exact match | Stop | 2 | Safe Profile not found |
| `fallback`: native typed 429, legacy malformed/missing/empty payload | Stop | 2 | Safe transient `instagram_unavailable` |
| `fallback`: native typed 429, legacy HTTP 400/401/403/404/429/5xx or transport failure | Stop | 2 | Existing safe classification of the legacy failure; no retry |
| `fallback`: native 400/401/403/404/5xx, Profile not found, schema/shape, session, or transport failure | Stop | 1 | Existing safe final classification; no legacy call |
| `fallback`: native wrapper message merely contains `429` | Stop | 1 | Existing safe final classification; no legacy call |
| `native`: native success/failure | Return or stop | 1 | Existing native result/classification; no legacy call |
| `legacy`: legacy success/failure | Return or stop | 1 | Legacy result or existing safe classification; no native call |
| Any mode: session preflight fails before lookup | Stop | 0 | Existing safe session rejection |
| Any mode: normalization/avatar/Story/Post/Reel/filesystem/database failure after lookup | Stop or preserve existing item-warning behavior | Lookup already complete | Existing behavior; never starts fallback |

HTTP `400`, `401`, `403`, and `404` are never fallback triggers. A legacy `429`
is also terminal: it is classified safely but does not retry native or legacy.
Only a complete, structurally valid `users` list without a casefold-exact match
means Profile not found. An absent or empty enclosing payload means Instagram
is unavailable/schema drift.

## Security and Observability

The existing `loader.context.error` sanitizer remains installed and unchanged.
Final job and API failures continue through the existing safe classification;
no raw Instaloader exception string or response content becomes a persisted job
error or API payload.

The resolver emits one structured event per attempted path. The fixed event
name is `instagram_profile_lookup`. Apart from normal logger metadata supplied
by the runtime, the event has exactly these four application fields:

| Field | Allowed values |
| --- | --- |
| `mode` | `native`, `fallback`, `legacy` |
| `path` | `native`, `legacy` |
| `outcome` | `success`, `fallback`, `failure` |
| `status_class` | `success`, `rate_limited`, `bad_request`, `unauthorized`, `forbidden`, `not_found`, `other_4xx`, `server_error`, `transport_error`, `schema_drift`, `unexpected_error` |

In `fallback` mode, a native typed rate limit emits
`mode=fallback path=native outcome=fallback status_class=rate_limited`; the
single legacy attempt then emits its own success or failure event. Native
success emits one event. A non-fallback failure emits one failure event. No
request attempt may emit more than one lookup event.

The event must not contain or interpolate the username, requested query,
doc-id variables, URL, query string, Cookie, session identifier, CSRF token,
header, response body/node, job/profile identifier, raw status text, raw
exception, exception message, traceback, or exception stack. Logging must not
pass `exc_info`, and allowed values must come from the closed tables above.
Status classification uses typed exceptions/status metadata; unknown cases use
`unexpected_error`, never sanitized raw text.

Tests and live diagnostics treat the following as a release gate:

- neither lookup events nor surrounding `context.error`, API, job, or worker
  output contains secret/query fixtures;
- fallback evidence consists only of the two safe lookup events and bounded
  request counts;
- the existing context sanitizer continues redacting query-bearing URLs and
  sensitive markers.

## Automated Testing

All automated Instagram network behavior is mocked. Required coverage is:

### Configuration

- unset `IW_INSTAGRAM_PROFILE_LOOKUP_MODE` produces `fallback`;
- each exact value `native`, `fallback`, and `legacy` loads successfully;
- empty, whitespace-padded, mixed-case, and unknown values fail startup without
  exposing the rejected input.

### Resolver modes and request bounds

- native success in `fallback` returns the native Profile with one native call
  and zero legacy calls;
- native `TooManyRequestsException` followed by legacy success makes exactly
  one call to each path;
- a `requests.exceptions.HTTPError` with integer status `429` in the preserved
  cause/context chain may fallback once;
- the string `429` in an untyped exception, including
  `BadResponseException`, never triggers fallback;
- native 400, 401, 403, 404, Profile-not-found, shape, session, transport,
  filesystem, and database errors make zero legacy calls;
- `legacy` makes one legacy call and zero native calls;
- `native` makes one native call and zero legacy calls;
- legacy 400, 401, and 429 each fail after one legacy call, with no native call
  or retry;
- a fallback legacy failure never returns to native and never loops;
- cyclic and longer-than-eight exception chains terminate safely without raw
  string matching or repeated requests.

### Legacy response contract

- the exact doc ID and variables are sent once;
- usernames differing only by case match via `casefold()` and return an
  ordinary 4.15.3 `Profile` bound to the supplied context;
- prefix, substring, whitespace-normalized, and fuzzy candidates do not match;
- a valid `users: []` and a valid nonmatching list raise
  `ProfileNotExistsException` and produce the safe Profile-not-found adapter
  error;
- missing/empty enclosing payloads, missing path members, wrong container/list
  types, and malformed user nodes fail closed as schema drift and produce the
  safe transient adapter error rather than Profile not found;
- a legacy Profile that requires metadata hydration uses the 4.15.3 logged-in
  doc-id path; hydration failure does not invoke native.

### Adapter and coordinator integration

- `fetch_profile()` covers native success, fallback success, each terminal
  failure family, and exact call counts;
- the real user flow through `sync_profile()` covers native success and native
  429 to legacy success, not only a resolver unit test;
- `sync_profile()` performs session preflight before lookup and avatar work;
- a successful legacy Profile proceeds through normalization, avatar refresh,
  metadata persistence, Story-first coordinator execution, Reel/Post
  enumeration, and exact progress reporting;
- session preflight failure performs zero lookups and zero avatar/Story work;
- avatar or coordinator failure after successful lookup never initiates
  fallback;
- direct Post/Reel/Story resolution reuses an existing active Profile without
  a resolver call or an extra network request;
- direct media with a missing owner retains the current owner-creation behavior;
- direct media associated with an inactive/deletion-pending/deletion-failed
  owner retains the current rejection and no-repair behavior;
- followee discovery is unchanged and never receives the resolver.

### Regression and log safety

- existing Post and WebP fixtures continue to pass;
- Reel classification, video/poster roles, and direct Reel behavior continue to
  pass;
- Story owner validation, direct Story behavior, Story-first sync, and progress
  continue to pass;
- `CmzV2H-rrlI` and `DOqEJyxCRGJ` regression fixtures remain covered;
- every lookup log tuple uses only the closed field/value sets;
- username, query variables, query-bearing URLs, Cookie/session/header values,
  response payloads, and raw exception fixtures are absent from captured logs,
  job errors, and API responses;
- native-success logs prove no fallback event, and native-429/legacy-success
  logs prove exactly two attempts without disclosing the target.

## Live Diagnostic

After all automated checks pass, run a local, isolated live diagnostic with a
fresh data root and a freshly imported/copied Cookie revision. Do not reuse a
populated local database as evidence. Keep secret files private, never print
their contents, and remove any temporary Cookie copy after the run. This
diagnostic does not use NAS or Docker and does not touch production.

Run the application in the approved default `fallback` mode and exercise the
actual API/job flow, including `sync_profile()`. Record a sanitized result
matrix with:

| Target | Required evidence |
| --- | --- |
| Profile metadata | Stored Instagram user ID, username consistency, full name/biography availability without logging the lookup target |
| Avatar | A validated local avatar produced after lookup |
| Story | Successful Story manifest/enumeration and processing, or a complete safe failure if no Story is visible |
| Progress | Profile preflight precedes Story work; exact coordinator progress and terminal job state |
| `CmzV2H-rrlI` | 4.15.3 Post metadata/download regression remains successful or returns a complete safe classified failure |
| `DOqEJyxCRGJ` | Post/Reel resolution succeeds or returns a complete safe classified failure; classification/asset roles remain correct when saved |
| Logs | Lookup event field allowlist passes and Cookie/session/query/raw-exception gates remain clean |

Capture per-lookup request counts without capturing URLs or variables. If
native succeeds, evidence must show one native request and no legacy request.
If native returns a typed 429 and legacy succeeds, retain only safe evidence:
one native event with `outcome=fallback/status_class=rate_limited`, one legacy
event with `outcome=success/status_class=success`, and total request count two.
Confirm that avatar, Story, and progress work continued after that fallback.

The diagnostic report must explicitly confirm the `CmzV2H-rrlI` gate, the
`DOqEJyxCRGJ` gate, and the log-redaction/allowlist gate. It must also state
whether fallback was actually observed. A native-success run does not prove the
fallback live branch; in that case the mocked typed-429 test remains the branch
evidence. Do not induce a rate limit or repeat live requests merely to exercise
fallback.

The earlier `20260731TFcsmab` probe may be linked as supporting evidence, but
must remain labeled patched-only and non-contemporaneous. It cannot replace the
fresh user-flow diagnostic or be described as an A/B win.

## Rollout and Rollback

Implementation lands with `fallback` as the default and with no database
migration. Before any later release decision, require the automated matrix and
fresh local diagnostic above. This design phase does not authorize push,
Docker/NAS work, release, or production replacement.

Operators have two environment-only controls, both requiring a process restart:

- set `IW_INSTAGRAM_PROFILE_LOOKUP_MODE=native` to disable all compatibility
  requests immediately while retaining 4.15.3;
- set `IW_INSTAGRAM_PROFILE_LOOKUP_MODE=legacy` only for a deliberate
  diagnostic or temporary bypass when native is known unusable.

The first rollback is the `native` kill switch because it removes the new
request path without changing the dependency, schema, or stored data. If code
rollback is later required, revert the resolver integration and setting while
keeping `instaloader==4.15.3`. Do not roll back by downgrading Instaloader to
4.15.2.

Lookup events should be reviewed after deployment authorization to determine
whether native rate limits persist and whether the compatibility endpoint
develops schema drift. Removal or default changes require a new evidence-based
decision; this design does not silently flip modes at runtime.

## Risks

- In `fallback`, a typed native 429 can double one lookup from one request to
  two. The strict one-native/one-legacy cap and absence of retries bound this.
- The old doc-id response may change or disappear. Full shape validation fails
  closed as transient unavailability instead of creating or updating the wrong
  Profile.
- A complete search result might omit a real Profile. Exact matching avoids
  attaching a near match, but a complete no-match result is still reported as
  Profile not found.
- The 4.15.2 method body can become stale relative to future Instaloader
  releases. Pinning 4.15.3 and keeping the reproduction private and narrow make
  upgrades deliberate.
- A default fallback can mask recovery or regression of the native endpoint.
  Per-path safe events reveal actual selection, and `native` is an immediate
  operator kill switch.
- The local probe demonstrates compatibility-path feasibility but not relative
  reliability because it was patched-only and delayed by 60 seconds. Rollout
  conclusions must use the fresh diagnostic and production-authorized evidence.
- Returning a partial legacy node means later logged-in metadata hydration
  still depends on 4.15.3. This is intentional; broadening the compatibility
  implementation would increase security and maintenance risk.
- Overly broad rate-limit recognition could add traffic after unrelated
  failures. The bounded typed chain, exact status 429, explicit HTTP types, and
  prohibition on message parsing prevent that.
- Lookup-only logs provide less target-level correlation. This is an accepted
  privacy tradeoff; job/API safe classification and aggregate mode/path events
  are sufficient for this change.

## Acceptance Criteria

- `instaloader==4.15.3` remains the exact dependency.
- The only legacy behavior reproduced is 4.15.2's doc-id
  `26347858941511777` username search and exact case-insensitive selection.
- No global monkey patch, vendor fork, `from_id()` copy, or
  `_obtain_metadata()` copy exists.
- `fetch_profile()` and the actual `sync_profile()` user flow both use the
  guarded resolver; followee discovery and direct media do not.
- `IW_INSTAGRAM_PROFILE_LOOKUP_MODE` accepts exactly
  `native|fallback|legacy`, defaults to `fallback`, and invalid input fails
  startup safely.
- Native success is terminal. Fallback occurs only for
  `TooManyRequestsException` or an exact typed 429 preserved in the bounded
  exception chain, never because raw text contains `429`.
- Native 400/401/403/404, Profile-not-found, shape, session, transport,
  filesystem, and database failures never call legacy.
- Each operation makes at most one native and one legacy request. Legacy never
  retries, calls native, or loops.
- Malformed/missing/empty enclosing legacy payloads fail as safe transient
  unavailability. Only a structurally complete `users` list with no casefold
  exact match becomes `ProfileNotExistsException`.
- A legacy match returns an ordinary 4.15.3 `Profile(context, node)` and leaves
  subsequent logged-in hydration on the 4.15.3 path.
- Session preflight remains before lookup/avatar, and a successful lookup still
  runs avatar plus the Story/Reel/Post coordinator and exact progress.
- Active direct-owner reuse makes no Profile lookup; missing-owner creation and
  inactive-owner rejection remain unchanged.
- Safe lookup events contain exactly `mode`, `path`, `outcome`, and
  `status_class` from closed allowlists and contain none of the prohibited raw
  data.
- Existing context sanitization and safe job/API error classification remain in
  force.
- Automated tests cover all configuration, mode, trigger, response-shape,
  request-count, adapter-entry, direct-media-invariant, log-safety, Post/WebP,
  Reel, Story, avatar, and coordinator cases listed above.
- A fresh isolated local diagnostic covers Profile metadata, avatar, Story,
  progress, `CmzV2H-rrlI`, `DOqEJyxCRGJ`, and the log gate without NAS, Docker,
  push, release, or production changes.

## References

- [Instaloader v4.15.2...v4.15.3 comparison](https://github.com/instaloader/instaloader/compare/v4.15.2...v4.15.3)
- [Profile resolution change, commit a4adeb083506cf53c5096451d4f5d8d1aa87e0a6](https://github.com/instaloader/instaloader/commit/a4adeb083506cf53c5096451d4f5d8d1aa87e0a6)
- [Post metadata change, commit 6dd77e7b56acdf0a0017d473678b15d3cd7a2c72](https://github.com/instaloader/instaloader/commit/6dd77e7b56acdf0a0017d473678b15d3cd7a2c72)
- Local probe summary:
  `/var/tmp/instaloader-webui-profile-ab/20260731TFcsmab/summary.json`
- Related design:
  `docs/superpowers/specs/2026-07-31-story-reel-sync-reliability-design.md`
