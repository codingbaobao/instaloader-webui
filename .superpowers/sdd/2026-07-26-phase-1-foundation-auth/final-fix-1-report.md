# Phase 1 Final Fix Round 1 Report

> **Status — superseded for schema work (2026-08-01):** This report preserves
> historical migration-era evidence. Its migration commands and behavior are
> superseded by the [current pre-1.0 fresh-schema-only policy](../../../README.md#pre-10-database-schema-policy)
> and must not be executed as current guidance.

Date: 2026-07-26
Scope: backend authentication/security hardening and the minimum related
Docker, Compose, README, test-tooling, migration, and smoke-test changes.

## Outcome

The final-review blockers were addressed without adding Phase 2 behavior:

- A pure-ASGI 16 KiB request-body ceiling rejects both declared and streamed
  oversized bodies with the stable `request_too_large` envelope. Frames are
  coalesced into one byte buffer and requests over 128 ASGI frames are rejected,
  preventing low-byte framing amplification.
- Login and password-change fields enforce a 1,024-byte UTF-8 maximum.
  Bootstrap passwords use the same post-newline-removal maximum.
- Usernames are NFKC-normalized, stripped, case-folded, and capped at 64 UTF-8
  bytes before login comparison, admission, Argon2, and account-scope digesting.
- Login admission is atomic across independent account (5), canonical client
  IP (5), and service-global (20) scopes in a 15-minute window.
- Admission prunes globally expired reservations and stale failure rows, and
  enforces a hard weighted cardinality cap of 1,024 before inserting.
- Argon2 hash/verify work is process-wide bounded to two concurrent operations.
  Saturation returns a retryable `503 authentication_busy` response and releases
  the admission reservation.
- Unknown accounts still verify against a fixed provider-owned dummy Argon2id
  hash after admission.
- Unexpected exceptions return the stable JSON 500 envelope. Logging includes
  only the method, path, and exception class, not exception text or credentials.
  Exception normalization runs inside the security-header layer, so 500
  responses retain CSP and API no-store headers.
- Browser security headers apply to all HTTP responses; `/api/` responses use
  `Cache-Control: no-store`. HSTS remains intentionally owned by the external
  TLS terminator.
- A presented stale session cookie is cleared on `authentication_required`.
- Malformed Argon2 hashes and invalid Unicode verification inputs fail closed.
- Migration `0005_multiscope_login_admission` replaces transient combined-key
  reservations and resets non-decomposable legacy failure digests.
- The image starts with `umask 077`; `/app` stays root-owned and `/data` is the
  only persistent writable location.
- Compose uses a read-only root filesystem, `/tmp` tmpfs, `CapDrop=ALL`,
  `no-new-privileges`, a PID limit, and an init process.
- Trusted proxy addresses are explicitly mapped through
  `IW_FORWARDED_ALLOW_IPS`. Documentation warns never to use `*` on an exposed
  deployment and assigns HSTS/forwarded-header overwriting to the TLS proxy.
- Node and Python base images are pinned by digest.
- Test tooling now requires `pytest>=9.0.3,<10` and `pytest-cov>=7,<8`.

## Preserved concurrency policy

A successful login clears only the account failure history. Completed client-IP
and global failure history intentionally remains, so a successful administrator
login cannot erase network/service abuse evidence.

The successful login also marks other in-flight reservations for the exact
account/IP tuple as unable to record a late failure. A concurrently verified
correct login may still consume its reservation and create a session. Focused
tests cover both properties.

## TDD evidence

The first focused RED run had 11 intended failures and one exact-limit control
pass. Missing behavior included declared/chunked body rejection, multibyte
credential limits, stale-cookie deletion, the JSON catch-all, security headers,
and defensive Argon verification. After implementation, all 12 cases passed.

The multi-scope RED run failed for rotating usernames, account attempts across
IPs, distributed global attempts, IPv4-mapped canonicalization, stale-row
pruning, cardinality denial, and the new migration schema. The same seven cases
then passed.

Argon admission first failed at collection because the busy exception did not
exist. Bounded-concurrency and reservation-release coverage passed after the
minimal implementation.

Final review found two further middleware boundary cases and the missing
64-byte username boundary. Deterministic RED runs showed a 66-byte username
reached Argon/admission, unexpected 500s omitted headers, and 129 one-byte frames
reached validation. The remediations passed focused API/service tests and both
reviewers returned PASS.

## Verification

- Backend full suite: `99 passed`, `93.33%` coverage, Python 3.12.9,
  pytest 9.1.1, warnings treated as errors.
- Focused auth/race suite before the final three boundary additions:
  `83 passed`; all final boundary tests also passed in the full suite.
- Ruff lint: passed.
- MyPy: passed for all 23 source files.
- Frontend: `30 passed`, 93.98% statement coverage.
- Frontend ESLint: passed.
- Frontend TypeScript/Vite production build: passed.
- `npm audit --audit-level=high`: exited zero. Two moderate React Router
  advisories remain; the reviewed Phase 1 code uses only constant internal
  navigation targets. A breaking React Router 7 migration is deferred.
- Installed Python environment audit with pip-audit 2.10.1: no known
  vulnerabilities; the local project itself is not published on PyPI and was
  reported as unauditable by name.
- Docker image build with digest-pinned bases: passed.
- Compose render validation: passed with required environment supplied.
- Container/Compose smoke suite: `5 passed`.
- Live container inspection confirmed UID/GID 10001, Uvicorn umask 0077,
  database directory/file modes 0700/0600, zero effective capabilities,
  `NoNewPrivs=1`, read-only rootfs, `/tmp` tmpfs, and persistence across
  recreation/restart.
- `git diff --check`: passed.
- Final read-only code review: PASS, no Critical/Important findings.
- Final read-only security review: RELEASE/PASS, no blocker/high/medium findings.

## Explicit deferrals

- Revoked/expired web-session pruning remains deferred. Session creation
  requires successful administrator authentication, so it is not part of the
  unauthenticated cardinality/DoS blocker fixed here.
- React Router 7 is a breaking frontend migration and is deferred while all
  Phase 1 destinations remain constants.
- A reviewed hash-locked Python production dependency workflow is broader than
  this final-fix scope. Runtime dependencies were audited and image bases were
  digest-pinned.

This report records Final Fix Round 1 only. It does not declare overall Phase 1
completion.
