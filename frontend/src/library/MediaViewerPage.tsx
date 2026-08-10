import {
  type WheelEvent as ReactWheelEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";

import { ApiError } from "../app/api";
import type { SessionData } from "../auth/useSession";
import { InstagramIcon, TrashIcon } from "./ActionIcons";
import {
  deleteMedia,
  getMedia,
  getProfile,
  listMediaFeed,
  mediaAssetUrl,
} from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { formatDateTime } from "./dateFormatters";
import {
  contentAssets,
  mediaDisplayIdentifier,
  mediaLabel,
  posterFor,
} from "./mediaPresentation";
import { ProfileAvatar } from "./ProfileAvatar";
import type {
  MediaDetail,
  MediaFeedPage,
  MediaKind,
  ProfileDetail,
} from "./types";

type MediaViewerPageProps = Readonly<{ session: SessionData }>;
type FeedSource =
  | Readonly<{ type: "recent" }>
  | Readonly<{ type: "profile"; profileId: string; kind: MediaKind }>;
type FeedState = Readonly<{
  source: FeedSource;
  items: readonly MediaDetail[];
  profiles: Readonly<Record<string, ProfileDetail>>;
  newerCursor: string | null;
  olderCursor: string | null;
}>;
type PageLoadError = Readonly<{
  message: string;
  direction: "newer" | "older";
  cursor: string;
}>;

const MUTED_STORAGE_KEY = "instaloader-webui:media-muted";
const FEED_PAGE_SIZE = 20;
const RENDER_RADIUS = 2;

function isMediaKind(value: string | null): value is MediaKind {
  return value === "post" || value === "reel" || value === "story";
}

function requestedSource(searchParams: URLSearchParams): FeedSource | null {
  const source = searchParams.get("source");
  if (source === "recent") {
    return { type: "recent" };
  }
  const profileId = searchParams.get("profileId");
  const kind = searchParams.get("kind");
  if (source === "profile" && profileId && isMediaKind(kind)) {
    return { type: "profile", profileId, kind };
  }
  return null;
}

function feedOptions(source: FeedSource) {
  return source.type === "profile"
    ? { profileId: source.profileId, kind: source.kind }
    : {};
}

function returnTarget(source: FeedSource): Readonly<{
  href: string;
  label: string;
}> {
  if (source.type === "recent") {
    return { href: "/", label: "Back to recent media" };
  }
  const query = new URLSearchParams({ tab: source.kind });
  return {
    href: `/profiles/${encodeURIComponent(source.profileId)}?${query.toString()}`,
    label: "Back to profile",
  };
}

function storedMutedPreference(): boolean {
  try {
    return window.localStorage.getItem(MUTED_STORAGE_KEY) !== "false";
  } catch {
    return true;
  }
}

async function profilesForItems(
  items: readonly MediaDetail[],
  existing: Readonly<Record<string, ProfileDetail>>,
  signal?: AbortSignal,
): Promise<Record<string, ProfileDetail>> {
  const missingIds = [...new Set(items.map((item) => item.owner_profile_id))]
    .filter((profileId) => existing[profileId] === undefined);
  const loaded = await Promise.all(
    missingIds.map((profileId) => getProfile(profileId, signal)),
  );
  const profiles = { ...existing };
  for (const profile of loaded) {
    profiles[profile.id] = profile;
  }
  return profiles;
}

function mergeItems(
  current: readonly MediaDetail[],
  incoming: readonly MediaDetail[],
  direction: "newer" | "older",
): readonly MediaDetail[] {
  const existingIds = new Set(current.map((item) => item.id));
  const unique = incoming.filter((item) => !existingIds.has(item.id));
  return direction === "newer" ? [...unique, ...current] : [...current, ...unique];
}

function isInteractiveTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement
    && target.closest("a, button, input, textarea, select, video, [contenteditable]")
      !== null;
}

function jumpToFeedIndex(container: HTMLDivElement, index: number) {
  const previousScrollBehavior = container.style.scrollBehavior;
  container.style.scrollBehavior = "auto";
  try {
    const top = index * container.clientHeight;
    if (typeof container.scrollTo === "function") {
      container.scrollTo({ top, behavior: "auto" });
    } else {
      container.scrollTop = top;
    }
  } finally {
    container.style.scrollBehavior = previousScrollBehavior;
  }
}

export function MediaViewerPage({ session }: MediaViewerPageProps) {
  const { mediaId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const feedRef = useRef<HTMLDivElement>(null);
  const feedStateRef = useRef<FeedState | null>(null);
  const feedGenerationRef = useRef(0);
  const loadingCursorsRef = useRef(new Map<string, Promise<number>>());
  const failedCursorsRef = useRef(new Set<string>());
  const pendingScrollIndexRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const wheelLockedRef = useRef(false);
  const [feed, setFeed] = useState<FeedState | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<PageLoadError | null>(null);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [muted, setMuted] = useState(storedMutedPreference);
  const [deleteTarget, setDeleteTarget] = useState<MediaDetail | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const searchKey = searchParams.toString();

  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    const generation = feedGenerationRef.current + 1;
    feedGenerationRef.current = generation;
    feedStateRef.current = null;
    loadingCursorsRef.current.clear();
    failedCursorsRef.current.clear();
    pendingScrollIndexRef.current = null;
    const controller = new AbortController();
    async function loadInitialFeed() {
      setLoading(true);
      setLoadError(null);
      setPageError(null);
      try {
        let source = requestedSource(new URLSearchParams(searchKey));
        if (!mediaId) {
          throw new Error("The media item was not found.");
        }
        if (source === null) {
          const media = await getMedia(mediaId, controller.signal);
          source = {
            type: "profile",
            profileId: media.owner_profile_id,
            kind: media.kind,
          };
        }
        const page = await listMediaFeed(
          {
            anchorId: mediaId,
            ...feedOptions(source),
            limit: FEED_PAGE_SIZE,
          },
          controller.signal,
        );
        const profiles = await profilesForItems(page.items, {}, controller.signal);
        const nextFeed: FeedState = {
          source,
          items: page.items,
          profiles,
          newerCursor: page.newer_cursor,
          olderCursor: page.older_cursor,
        };
        const anchorIndex = Math.max(
          0,
          page.items.findIndex((item) => item.id === mediaId),
        );
        pendingScrollIndexRef.current = anchorIndex;
        feedStateRef.current = nextFeed;
        setFeed(nextFeed);
        setActiveIndex(anchorIndex);
      } catch (cause) {
        if (controller.signal.aborted) {
          return;
        }
        setLoadError(
          cause instanceof ApiError
            ? cause.message
            : "The media feed could not be loaded.",
        );
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }
    void loadInitialFeed();
    return () => controller.abort();
  }, [mediaId, reloadVersion, searchKey]);

  useLayoutEffect(() => {
    const index = pendingScrollIndexRef.current;
    const container = feedRef.current;
    if (index === null || container === null) {
      return;
    }
    pendingScrollIndexRef.current = null;
    jumpToFeedIndex(container, index);
  }, [feed, mediaId, searchKey]);

  const loadCursor = useCallback((
    direction: "newer" | "older",
    cursor: string,
    source: FeedSource,
    retry = false,
  ): Promise<number> => {
    const generation = feedGenerationRef.current;
    const existingRequest = loadingCursorsRef.current.get(cursor);
    if (existingRequest !== undefined) {
      return existingRequest;
    }
    if (!retry && failedCursorsRef.current.has(cursor)) {
      return Promise.resolve(0);
    }
    failedCursorsRef.current.delete(cursor);
    setPageError((current) => current?.cursor === cursor ? null : current);
    const request = (async () => {
      try {
        const page: MediaFeedPage = await listMediaFeed({
          cursor,
          ...feedOptions(source),
          limit: FEED_PAGE_SIZE,
        });
        if (
          !mountedRef.current
          || feedGenerationRef.current !== generation
        ) {
          return 0;
        }
        const current = feedStateRef.current;
        if (current === null) {
          return 0;
        }
        const profiles = await profilesForItems(page.items, current.profiles);
        if (
          !mountedRef.current
          || feedGenerationRef.current !== generation
        ) {
          return 0;
        }
        const latest = feedStateRef.current;
        if (latest === null) {
          return 0;
        }
        const existingIds = new Set(latest.items.map((item) => item.id));
        const addedCount = page.items
          .filter((item) => !existingIds.has(item.id)).length;
        const nextFeed: FeedState = {
          ...latest,
          items: mergeItems(latest.items, page.items, direction),
          profiles: { ...latest.profiles, ...profiles },
          newerCursor: direction === "newer"
            ? page.newer_cursor
            : latest.newerCursor,
          olderCursor: direction === "older"
            ? page.older_cursor
            : latest.olderCursor,
        };
        if (direction === "newer" && addedCount > 0) {
          setActiveIndex((index) => {
            const shifted = index + addedCount;
            pendingScrollIndexRef.current = shifted;
            return shifted;
          });
        }
        feedStateRef.current = nextFeed;
        setFeed(nextFeed);
        return addedCount;
      } catch (cause) {
        if (
          mountedRef.current
          && feedGenerationRef.current === generation
        ) {
          failedCursorsRef.current.add(cursor);
          setPageError({
            message: cause instanceof ApiError
              ? cause.message
              : "More media could not be loaded.",
            direction,
            cursor,
          });
        }
        return 0;
      } finally {
        if (feedGenerationRef.current === generation) {
          loadingCursorsRef.current.delete(cursor);
        }
      }
    })();
    loadingCursorsRef.current.set(cursor, request);
    return request;
  }, []);

  useEffect(() => {
    if (feed === null) {
      return;
    }
    if (activeIndex <= 2 && feed.newerCursor !== null) {
      void loadCursor("newer", feed.newerCursor, feed.source);
    }
    if (
      activeIndex >= feed.items.length - 3
      && feed.olderCursor !== null
    ) {
      void loadCursor("older", feed.olderCursor, feed.source);
    }
  }, [activeIndex, feed, loadCursor]);

  const target = feed === null ? null : returnTarget(feed.source);

  const moveTo = useCallback((requestedIndex: number) => {
    const current = feedStateRef.current;
    if (current === null) {
      return;
    }
    const nextIndex = Math.max(
      0,
      Math.min(requestedIndex, current.items.length - 1),
    );
    setActiveIndex(nextIndex);
    const container = feedRef.current;
    if (container !== null) {
      if (typeof container.scrollTo === "function") {
        container.scrollTo({
          top: nextIndex * container.clientHeight,
          behavior: "smooth",
        });
      } else {
        container.scrollTop = nextIndex * container.clientHeight;
      }
    }
  }, []);

  const moveBy = useCallback(async (offset: -1 | 1) => {
    const current = feedStateRef.current;
    if (current === null) {
      return;
    }
    const currentMedia = current.items[activeIndex];
    if (currentMedia === undefined) {
      return;
    }
    const requestedIndex = activeIndex + offset;
    if (requestedIndex >= 0 && requestedIndex < current.items.length) {
      moveTo(requestedIndex);
      return;
    }
    const cursor = offset < 0 ? current.newerCursor : current.olderCursor;
    if (cursor === null) {
      return;
    }
    await loadCursor(
      offset < 0 ? "newer" : "older",
      cursor,
      current.source,
    );
    const latest = feedStateRef.current;
    if (latest === null) {
      return;
    }
    const preservedIndex = latest.items.findIndex(
      (item) => item.id === currentMedia.id,
    );
    if (preservedIndex < 0) {
      return;
    }
    const nextIndex = preservedIndex + offset;
    if (nextIndex >= 0 && nextIndex < latest.items.length) {
      moveTo(nextIndex);
    }
  }, [activeIndex, loadCursor, moveTo]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (deleteTarget === null && target !== null) {
          event.preventDefault();
          navigate(target.href);
        }
        return;
      }
      if (
        event.altKey
        || event.ctrlKey
        || event.metaKey
        || event.shiftKey
        || isInteractiveTarget(event.target)
      ) {
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        void moveBy(-1);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        void moveBy(1);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [deleteTarget, moveBy, navigate, target]);

  function handleFeedScroll() {
    const container = feedRef.current;
    if (container === null || container.clientHeight === 0) {
      return;
    }
    const nextIndex = Math.round(container.scrollTop / container.clientHeight);
    setActiveIndex((current) => current === nextIndex ? current : nextIndex);
  }

  function handleWheel(event: ReactWheelEvent<HTMLDivElement>) {
    if (
      wheelLockedRef.current
      || Math.abs(event.deltaX) > Math.abs(event.deltaY)
      || Math.abs(event.deltaY) < 4
    ) {
      return;
    }
    event.preventDefault();
    wheelLockedRef.current = true;
    void moveBy(event.deltaY > 0 ? 1 : -1);
    window.setTimeout(() => {
      wheelLockedRef.current = false;
    }, 300);
  }

  function updateMuted(nextMuted: boolean) {
    setMuted(nextMuted);
    try {
      window.localStorage.setItem(MUTED_STORAGE_KEY, String(nextMuted));
    } catch {
      // Playback remains usable when storage is unavailable.
    }
  }

  async function confirmDeletion() {
    if (deleteTarget === null) {
      return;
    }
    setActionError(null);
    try {
      await deleteMedia(deleteTarget.id, session.csrf_token);
      setDeleteTarget(null);
      navigate("/activity");
    } catch (cause) {
      setActionError(
        cause instanceof ApiError
          ? cause.message
          : "The media item could not be deleted.",
      );
    }
  }

  if (feed === null) {
    return (
      <section className="library-page narrow-page" aria-live="polite">
        {loading ? <p className="loading-copy">Loading media…</p> : null}
        {loadError ? (
          <div className="inline-error" role="alert">
            <span>{loadError}</span>
            <button
              className="text-button"
              type="button"
              onClick={() => setReloadVersion((version) => version + 1)}
            >
              Try again
            </button>
          </div>
        ) : null}
      </section>
    );
  }

  const hasPrevious = activeIndex > 0 || feed.newerCursor !== null;
  const hasNext = activeIndex < feed.items.length - 1
    || feed.olderCursor !== null;
  const renderStart = Math.max(0, activeIndex - RENDER_RADIUS);
  const renderEnd = Math.min(
    feed.items.length,
    activeIndex + RENDER_RADIUS + 1,
  );
  const visibleItems = feed.items.slice(renderStart, renderEnd);

  return (
    <section className="viewer-feed-page">
      {target ? (
        <Link className="viewer-feed-back" to={target.href}>{target.label}</Link>
      ) : null}
      <div
        aria-label="Media feed"
        aria-roledescription="carousel"
        className="viewer-feed-track"
        ref={feedRef}
        role="region"
        onScroll={handleFeedScroll}
        onWheel={handleWheel}
      >
        {renderStart > 0 ? (
          <div
            aria-hidden="true"
            className="viewer-feed-spacer"
            style={{ height: `${renderStart * 100}dvh` }}
          />
        ) : null}
        {visibleItems.map((media, visibleIndex) => {
          const index = renderStart + visibleIndex;
          const isActive = index === activeIndex;
          return (
            <div
              aria-current={isActive ? "true" : undefined}
              aria-label={`${mediaLabel(media)} ${mediaDisplayIdentifier(media)}`}
              aria-roledescription="slide"
              className="viewer-feed-slide"
              key={media.id}
              role="group"
            >
              <MediaSlide
                active={isActive}
                media={media}
                muted={muted}
                owner={feed.profiles[media.owner_profile_id]}
                onDelete={() => setDeleteTarget(media)}
                onMutedChange={updateMuted}
              />
            </div>
          );
        })}
        {renderEnd < feed.items.length ? (
          <div
            aria-hidden="true"
            className="viewer-feed-spacer"
            style={{ height: `${(feed.items.length - renderEnd) * 100}dvh` }}
          />
        ) : null}
      </div>
      {hasPrevious ? (
        <button
          aria-label="Previous media"
          className="viewer-feed-button viewer-feed-button-previous"
          type="button"
          onClick={() => void moveBy(-1)}
        >
          <span aria-hidden="true">↑</span>
        </button>
      ) : null}
      {hasNext ? (
        <button
          aria-label="Next media"
          className="viewer-feed-button viewer-feed-button-next"
          type="button"
          onClick={() => void moveBy(1)}
        >
          <span aria-hidden="true">↓</span>
        </button>
      ) : null}
      <span aria-live="polite" className="viewer-feed-counter">
        {activeIndex + 1}
      </span>
      {pageError ? (
        <div className="viewer-feed-page-error" role="alert">
          <span>{pageError.message}</span>
          <button
            className="text-button"
            type="button"
            onClick={() => void loadCursor(
              pageError.direction,
              pageError.cursor,
              feed.source,
              true,
            )}
          >
            Retry
          </button>
        </div>
      ) : null}
      {actionError ? <p className="viewer-feed-action-error" role="alert">{actionError}</p> : null}
      <ConfirmDialog
        confirmLabel="Delete media"
        description="This queues removal of this downloaded media item and its locally saved assets. This cannot be undone."
        open={deleteTarget !== null}
        title="Delete this media item?"
        onClose={() => setDeleteTarget(null)}
        onConfirm={confirmDeletion}
      />
    </section>
  );
}

type MediaSlideProps = Readonly<{
  active: boolean;
  media: MediaDetail;
  muted: boolean;
  owner: ProfileDetail | undefined;
  onDelete: () => void;
  onMutedChange: (muted: boolean) => void;
}>;

function MediaSlide({
  active,
  media,
  muted,
  owner,
  onDelete,
  onMutedChange,
}: MediaSlideProps) {
  return (
    <div className="viewer-feed-layout">
      <div className="viewer-asset-stage">
        <AssetCarousel
          active={active}
          media={media}
          muted={muted}
          onMutedChange={onMutedChange}
        />
      </div>
      <article className="viewer-details">
        {owner ? (
          <div className="viewer-owner">
            <ProfileAvatar profile={owner} />
            <Link to={`/profiles/${encodeURIComponent(owner.id)}?tab=${media.kind}`}>
              @{owner.username}
            </Link>
          </div>
        ) : null}
        <h1 id={`viewer-title-${media.id}`}>{mediaLabel(media)}</h1>
        <p className="viewer-meta">
          Published {formatDateTime(media.published_at)}
          {" · "}
          {mediaLabel(media)}
          {" · "}
          {mediaDisplayIdentifier(media)}
        </p>
        <p className="viewer-caption">
          {media.caption
            || media.accessibility_caption
            || "No caption was saved for this public item."}
        </p>
        <div aria-label="Media actions" className="viewer-actions" role="group">
          <a
            aria-label="Open original on Instagram"
            className="icon-action"
            data-tooltip="Open original on Instagram"
            href={media.original_url}
            rel="noopener noreferrer"
            target="_blank"
          >
            <InstagramIcon />
          </a>
          <button
            aria-label="Delete downloaded media"
            className="icon-action icon-action-danger"
            data-tooltip="Delete downloaded media"
            type="button"
            onClick={onDelete}
          >
            <TrashIcon />
          </button>
        </div>
      </article>
    </div>
  );
}

type AssetCarouselProps = Readonly<{
  active: boolean;
  media: MediaDetail;
  muted: boolean;
  onMutedChange: (muted: boolean) => void;
}>;

function AssetCarousel({
  active,
  media,
  muted,
  onMutedChange,
}: AssetCarouselProps) {
  const carouselRef = useRef<HTMLDivElement>(null);
  const activeVideoRef = useRef<HTMLVideoElement | null>(null);
  const [assetIndex, setAssetIndex] = useState(0);
  const assets = contentAssets(media);
  const hasMultipleAssets = assets.length > 1;

  useEffect(() => {
    const nextVideo = active
      ? carouselRef.current
        ?.querySelector<HTMLVideoElement>(`video[data-asset-index="${assetIndex}"]`)
        ?? null
      : null;
    if (
      activeVideoRef.current !== null
      && activeVideoRef.current !== nextVideo
    ) {
      activeVideoRef.current.pause();
    }
    activeVideoRef.current = nextVideo;
    if (nextVideo !== null) {
      nextVideo.muted = muted;
      void nextVideo.play().catch(() => undefined);
    }
  }, [active, assetIndex, muted]);

  useEffect(() => () => {
    activeVideoRef.current?.pause();
  }, []);

  function selectAsset(index: number) {
    const bounded = Math.max(0, Math.min(index, assets.length - 1));
    setAssetIndex(bounded);
    const carousel = carouselRef.current;
    if (carousel !== null && typeof carousel.scrollTo === "function") {
      carousel.scrollTo({ left: bounded * carousel.clientWidth });
    }
  }

  function synchronizeCarousel() {
    const carousel = carouselRef.current;
    if (carousel === null || carousel.clientWidth === 0) {
      return;
    }
    setAssetIndex(Math.round(carousel.scrollLeft / carousel.clientWidth));
  }

  if (assets.length === 0) {
    return (
      <div className="viewer-empty-asset">
        This item is still preparing its downloaded asset.
      </div>
    );
  }

  return (
    <>
      <div
        aria-label={`${mediaLabel(media)} media carousel`}
        aria-roledescription="carousel"
        className="viewer-carousel-track"
        ref={carouselRef}
        role="region"
        onScroll={synchronizeCarousel}
      >
        {assets.map((asset, index) => {
          const poster = posterFor(media, asset.position);
          return (
            <div
              aria-label={hasMultipleAssets
                ? `${index + 1} of ${assets.length}`
                : "1 of 1"}
              aria-roledescription="slide"
              className="viewer-carousel-slide"
              key={asset.id}
              role="group"
            >
              {asset.kind === "video" ? (
                <video
                  autoPlay={active && index === assetIndex}
                  className="viewer-media"
                  controls
                  data-asset-index={index}
                  muted={muted}
                  playsInline
                  poster={poster === null
                    ? undefined
                    : mediaAssetUrl(media.id, poster.id)}
                  preload={active ? "auto" : "metadata"}
                  src={mediaAssetUrl(media.id, asset.id)}
                  onCanPlay={(event) => {
                    if (active && index === assetIndex) {
                      void event.currentTarget.play().catch(() => undefined);
                    }
                  }}
                  onVolumeChange={(event) => {
                    if (event.currentTarget.muted !== muted) {
                      onMutedChange(event.currentTarget.muted);
                    }
                  }}
                />
              ) : (
                <img
                  alt={media.accessibility_caption
                    || `Instagram ${mediaLabel(media)} ${mediaDisplayIdentifier(media)}`}
                  className="viewer-media"
                  src={mediaAssetUrl(media.id, asset.id)}
                />
              )}
            </div>
          );
        })}
      </div>
      {hasMultipleAssets && assetIndex > 0 ? (
        <button
          aria-label="Previous image or video"
          className="viewer-carousel-button viewer-carousel-button-previous"
          type="button"
          onClick={() => selectAsset(assetIndex - 1)}
        >
          <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">
            <path d="m15 18-6-6 6-6" />
          </svg>
        </button>
      ) : null}
      {hasMultipleAssets ? (
        <span aria-live="polite" className="viewer-carousel-counter">
          {assetIndex + 1} / {assets.length}
        </span>
      ) : null}
      {hasMultipleAssets && assetIndex < assets.length - 1 ? (
        <button
          aria-label="Next image or video"
          className="viewer-carousel-button viewer-carousel-button-next"
          type="button"
          onClick={() => selectAsset(assetIndex + 1)}
        >
          <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">
            <path d="m9 18 6-6-6-6" />
          </svg>
        </button>
      ) : null}
    </>
  );
}
