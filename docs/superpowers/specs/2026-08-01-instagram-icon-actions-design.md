# Instagram Icon Actions Design

**Date:** 2026-08-01

## Goal

Make the Instagram profile and media links easy to recognize without adding
prominent text buttons, and make downloaded-media deletion visually consistent
with those actions.

## Scope

- Replace the subtle profile `Instagram` text link with an icon-only Instagram
  action beside the profile username.
- Replace `Open original on Instagram` in the media viewer with the same
  icon-only Instagram action.
- Replace the `Delete downloaded media` text button with an icon-only trash
  action.
- Preserve the existing profile-delete button and media-delete confirmation
  dialog.
- Do not change APIs, persistence, routes, or download behavior.

## Visual Design

The actions use a compact 38-by-38-pixel rounded-square shape. Instagram links
use a black Instagram camera glyph on a low-contrast neutral gray background.
The media-delete action uses a red trash glyph on a very pale red background.
Hover states deepen the background slightly without introducing the prominent
Instagram-blue fill that was rejected during visual review.

The profile action remains beside the username. In the media viewer, the
Instagram and delete actions share one right-aligned action row and wrap safely
on narrow screens.

## Components and Semantics

Create focused, reusable SVG icon components for the Instagram camera and trash
glyphs. SVG elements are decorative: they use `aria-hidden="true"` and cannot
receive focus. The surrounding anchor or button owns the accessible name.

Both actions use a shared base action class, with a destructive modifier for the
trash action. Each control has:

- an explicit `aria-label` containing the full former text;
- a matching `data-tooltip` value shown on hover and keyboard focus;
- a visible `:focus-visible` outline;
- at least a 38-by-38-pixel pointer target.

The profile link continues to use the canonical encoded username URL and opens
in a new tab with `rel="noopener noreferrer"`. The media link continues to use
the stored canonical `original_url` with the same safe new-tab behavior.

## Interaction and Error Handling

Instagram actions remain ordinary anchors, so browser link behavior and fallback
remain unchanged. The trash action only opens the existing confirmation dialog;
no deletion request is sent until the user confirms. Existing API error handling
and navigation after deletion are unchanged.

## Testing

Use test-driven development for the DOM behavior:

- Profile tests verify the icon-only link's accessible name, canonical URL,
  safe new-tab attributes, and decorative icon semantics.
- Media viewer tests verify the Instagram link and delete button are exposed by
  their full accessible names inside the media action group.
- Tests verify the trash button opens the existing confirmation dialog, so the
  icon-only treatment does not weaken the safety step.
- Run the focused Vitest tests first, then the CI-equivalent frontend Vitest
  suite, ESLint, and production build under Node 22.

## Acceptance Criteria

1. A recognizable Instagram icon links from a profile to its canonical
   Instagram profile URL.
2. The media viewer exposes icon-only Instagram and trash actions with the
   approved neutral styling.
3. No visible action label remains on those three controls.
4. Screen readers and keyboard users receive the complete action names and
   focus feedback.
5. Media deletion still requires explicit confirmation.
6. Frontend tests, lint, and build pass under the CI Node major version.
