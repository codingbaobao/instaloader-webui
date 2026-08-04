# Immersive Media Feed Design

## Goal

Replace the single-item media viewer with an immersive, Instagram-style feed. A
viewer can move through every applicable saved media item without returning to
Home or a profile between items. Videos autoplay when ready, playback preference
persists in the browser, and Escape returns to the correct source page.

## Scope

This change covers the media viewer, links from Home and profile grids, profile
tab restoration, and a cursor-paginated feed API. It retains the existing media
detail actions and the horizontal carousel inside multi-asset posts.

It does not add timed advancement for images, loop the feed at its boundaries,
or change the media download and deletion workflows.

## User Experience

### Immersive viewer

The viewer occupies the available viewport height and presents one media item
per vertical snap point. On desktop, the asset stage and details appear side by
side. On smaller screens, concise owner and caption details overlay or follow the
asset without reducing the media to the existing card-sized layout.

Users move exactly one item at a time with:

- a vertical touch gesture;
- the mouse wheel;
- the Up and Down arrow keys; or
- visible previous and next controls.

The feed stops at its true first and last records. It does not wrap. A
multi-asset post keeps its existing horizontal carousel, and horizontal gestures
must not trigger vertical feed navigation.

### Playback

The active slide's video starts automatically after it can play. Videos outside
the active slide are paused. Returning to a previously viewed slide attempts to
play it again. A video inside a multi-asset post plays only when both its media
slide and its horizontal asset are active.

The first visit defaults to muted playback so browsers can allow autoplay. The
viewer stores the user's latest muted or unmuted choice in browser local storage
and applies it on future viewer visits. If a browser still rejects programmatic
playback, native controls remain available; rejection is handled without an
unhandled promise or a broken feed.

### Source and return behavior

Media links carry durable source parameters in the URL:

- `source=recent` browses the globally ordered library and returns to Home.
- `source=profile`, plus `profileId` and `kind`, browses one profile media tab
  and returns to that same profile and tab.

Profile tabs are reflected in the profile URL, such as `?tab=reel`, so refresh,
viewer return, and browser history restore the selected tab.

An old or direct `/media/:mediaId` URL without source parameters falls back to a
feed filtered by the media item's owner and kind, with that profile as its return
target.

The viewer has a visible source-aware return control. Escape invokes the same
return behavior. When a confirmation dialog is open, its native Escape handling
closes the dialog first and prevents viewer navigation.

## Feed API

Add a cursor-paginated media feed operation without imposing a total browsing
limit. Each request returns a bounded page around or beyond an anchor and cursor
metadata for both directions.

The operation accepts:

- an anchor media ID for initial loading;
- optional `profile_id` and `kind` filters;
- an opaque newer or older cursor for subsequent loading; and
- a bounded page size, with the UI defaulting to 20 items.

Records have a deterministic descending order by `published_at`, then `id`.
Cursors encode both ordered fields and the direction needed to continue the
same filtered query. The server validates cursor shape and direction and returns
a safe 422 response for an invalid cursor.

The initial anchor response includes the anchor plus neighboring items in both
directions. This allows an older direct link to browse newer and older records
without scanning every preceding page. A response indicates independently when
no newer or older page remains. Only exhaustion of the filtered database query
creates a feed boundary.

The existing non-paginated list endpoint remains available to current Home and
profile grids. The new feed response has an explicit DTO with `items`,
`newer_cursor`, and `older_cursor` rather than requiring consumers to interpret
untyped envelope metadata.

## Frontend Architecture

### Source links

`MediaGrid` receives a source descriptor. Home passes the recent descriptor;
Profile passes its profile ID and active media kind. The grid serializes that
descriptor into each media viewer link.

### Viewer coordination

`MediaViewerPage` owns:

- parsing and validating source parameters;
- resolving the direct-link fallback;
- loading the anchored feed;
- appending and prepending cursor pages;
- selecting the source-aware return destination;
- Escape and arrow-key behavior; and
- deletion-dialog coordination.

A vertical feed component owns scroll snapping, wheel/touch coordination,
active-index detection, and near-boundary load signals. A media slide component
renders one item and its details. The existing horizontal asset carousel is
retained within that slide and reports its active asset for playback control.

Profile data is fetched only as needed for owners visible in the loaded feed and
cached by profile ID for the viewer session. Loading duplicate cursor pages or
profiles is deduplicated.

### Rendering and memory

Fetched media records remain in the session feed model so backward navigation
does not re-request pages. DOM rendering is windowed to the current slide and a
small number of neighboring slides, preventing every visited video and image
element from remaining mounted during a long session. Spacer measurements keep
the scroll position stable when old DOM slides are removed or newer data is
prepended.

The viewer requests the next page before the user reaches the final rendered
slide. Loading the newer direction follows the same rule when the anchored item
is not the newest record.

## Loading and Error States

- An initial load shows a viewer-level loading state. Failure replaces it with a
  retryable error.
- A later page failure keeps the active media usable and exposes a retry action
  at the affected boundary.
- Empty or not-yet-downloaded assets show the existing preparing treatment and
  do not block movement to another media item.
- Autoplay rejection leaves native controls usable and does not surface an
  application error.
- A missing anchor follows the existing media-not-found API behavior.
- Deletion confirmation and deletion job behavior remain unchanged.

## Accessibility

The vertical feed and horizontal carousel have distinct accessible labels and
carousel descriptions. Controls have directional names that distinguish media
navigation from asset navigation. The active item counter is announced
politely, while routine scrolling does not move focus unexpectedly. Keyboard
handling ignores modified keystrokes and editable controls. The existing
reduced-motion rule disables smooth transitions while preserving navigation.

## Testing

Backend tests cover:

- deterministic ordering when timestamps match;
- anchored initial pages;
- newer and older cursor traversal without duplicates or omissions;
- profile and kind filters on every page;
- true first and last boundaries;
- invalid and mismatched cursors; and
- missing anchors.

Frontend tests cover:

- source-aware links from Recent media and every profile tab;
- profile tab URL restoration;
- direct-link source fallback;
- touch/scroll synchronization, wheel controls, arrow keys, and boundaries;
- separation of vertical media and horizontal asset navigation;
- active-video autoplay, inactive-video pause, and autoplay rejection;
- local-storage persistence of the muted choice;
- source-aware return links and Escape behavior, including an open dialog;
- bidirectional page loading, deduplication, and retry; and
- windowed rendering during a long feed.

Verification runs the focused backend and frontend tests during TDD, followed by
the full frontend test suite, ESLint, production build, and the relevant backend
suite.
