# Native Profile Avatar Formats Design

## Goal

Preserve valid JPEG and WebP profile avatars exactly as Instagram returns them.
Serve each file with the matching HTTP media type without changing the database
schema or converting image data.

## Storage Contract

- Accept only `image/jpeg` with a JPEG magic prefix and `image/webp` with a
  RIFF/WEBP magic prefix.
- Store JPEG as `profile-avatars/<profile-id>.jpg` and WebP as
  `profile-avatars/<profile-id>.webp`.
- Stream the original bytes to disk without decoding or re-encoding them.
- Atomically install the new file, then remove the obsolete alternate format.
- If both formats remain after an interrupted update, readers select the file
  with the newest modification time.
- Existing `.jpg` files remain immediately readable and require no migration.

## API and Lifecycle

The existing extensionless endpoint, `/api/profiles/{profile_id}/avatar`, stays
unchanged. It resolves the stored file and returns `image/jpeg` or `image/webp`
to match its actual format. The frontend therefore requires no change.

Direct-media owner reuse treats either stored format as an existing avatar.
Profile deletion removes every supported avatar format but continues to count
the avatar as one logical progress item.

## Validation and Failure Behavior

Unsupported media types, missing or unreadable prefixes, and mismatches between
the response `Content-Type` and body magic retain the existing fatal profile
sync error. The bounded invalid-response diagnostic uses the already-read
prefix when available, so validation does not consume or duplicate the response
stream and does not expose signed URLs or credentials.

## Database and Compatibility

No database column or migration is required. Local avatar format is derived
from the validated deterministic file path. The existing remote
`profile_pic_url` field is unchanged.

## Testing

- Prove WebP sync saves the original bytes, removes a stale JPEG, and continues
  into media enumeration.
- Prove JPEG behavior remains compatible and removes a stale WebP.
- Prove MIME/body mismatches remain fatal and diagnostically identify the body.
- Prove the avatar API serves both formats with the correct Content-Type and
  selects the newest file if both exist.
- Prove profile deletion removes both formats and direct-media reuse recognizes
  either format.
- Run focused tests, the full backend suite, Ruff, and Mypy.

## Out of Scope

- Image transcoding or quality changes.
- PNG, AVIF, animated-format handling, or transparency processing.
- Database schema changes.
- Frontend URL or component changes.
