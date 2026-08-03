import { useCallback, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../app/api";
import type { SessionData } from "../auth/useSession";
import { InstagramIcon, TrashIcon } from "./ActionIcons";
import { deleteMedia, getMedia, getProfile, mediaAssetUrl } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { formatDateTime } from "./dateFormatters";
import {
  contentAssets,
  mediaDisplayIdentifier,
  mediaLabel,
  posterFor,
} from "./mediaPresentation";
import { ProfileAvatar } from "./ProfileAvatar";
import { usePolling } from "./usePolling";

type MediaViewerPageProps = Readonly<{ session: SessionData }>;

export function MediaViewerPage({ session }: MediaViewerPageProps) {
  const { mediaId = "" } = useParams();
  const navigate = useNavigate();
  const carouselRef = useRef<HTMLDivElement>(null);
  const [assetSelection, setAssetSelection] = useState({
    mediaId: "",
    index: 0,
  });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadMedia = useCallback(async (signal: AbortSignal) => {
    if (!mediaId) {
      throw new Error("The media item was not found.");
    }
    const media = await getMedia(mediaId, signal);
    const owner = await getProfile(media.owner_profile_id, signal);
    return { media, owner };
  }, [mediaId]);
  const { data, error, loading, reload } = usePolling(loadMedia, 0, true);

  async function confirmDeletion() {
    if (!mediaId) {
      return;
    }
    setActionError(null);
    try {
      await deleteMedia(mediaId, session.csrf_token);
      setDeleteOpen(false);
      navigate("/activity");
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "The media item could not be deleted.");
    }
  }

  if (data === null) {
    return (
      <section className="library-page narrow-page" aria-live="polite">
        {loading ? <p className="loading-copy">Loading media…</p> : null}
        {error ? (
          <div className="inline-error" role="alert">
            <span>{error}</span>
            <button className="text-button" type="button" onClick={() => void reload()}>Try again</button>
          </div>
        ) : null}
      </section>
    );
  }

  const { media, owner } = data;
  const assets = contentAssets(media);
  const assetIndex =
    assetSelection.mediaId === media.id ? assetSelection.index : 0;
  const hasMultipleAssets = assets.length > 1;

  const selectAsset = (index: number) => {
    const boundedIndex = Math.max(0, Math.min(index, assets.length - 1));
    if (boundedIndex === assetIndex) {
      return boundedIndex;
    }
    const slides = carouselRef.current?.querySelectorAll<HTMLElement>(
      ".viewer-carousel-slide",
    );
    slides?.forEach((slide, slideIndex) => {
      if (slideIndex !== boundedIndex) {
        slide.querySelector("video")?.pause();
      }
    });
    setAssetSelection({
      mediaId: media.id,
      index: boundedIndex,
    });
    return boundedIndex;
  };

  const scrollToAsset = (index: number) => {
    const boundedIndex = Math.max(0, Math.min(index, assets.length - 1));
    const carousel = carouselRef.current;
    if (carousel === null) {
      return;
    }
    carousel.scrollTo({
      left: boundedIndex * carousel.clientWidth,
    });
  };

  const synchronizeCarousel = () => {
    const carousel = carouselRef.current;
    if (carousel === null || carousel.clientWidth === 0) {
      return;
    }
    selectAsset(Math.round(carousel.scrollLeft / carousel.clientWidth));
  };

  return (
    <section className="library-page viewer-page" aria-labelledby="viewer-title">
      <Link className="back-link" to={`/profiles/${encodeURIComponent(media.owner_profile_id)}`}>Back to profile</Link>
      <div className="media-viewer-layout">
        <div className="viewer-asset-stage">
          {assets.length === 0 ? (
            <div className="viewer-empty-asset">This item is still preparing its downloaded asset.</div>
          ) : (
            <div
              aria-label={`${mediaLabel(media)} media carousel`}
              aria-roledescription="carousel"
              className="viewer-carousel-track"
              key={media.id}
              ref={carouselRef}
              role="region"
              onScroll={synchronizeCarousel}
            >
              {assets.map((asset, index) => {
                const poster = posterFor(media, asset.position);
                const assetLabel = hasMultipleAssets
                  ? `${index + 1} of ${assets.length}`
                  : "1 of 1";
                return (
                  <div
                    aria-label={assetLabel}
                    aria-roledescription="slide"
                    className="viewer-carousel-slide"
                    key={asset.id}
                    role="group"
                  >
                    {asset.kind === "video" ? (
                      <video
                        className="viewer-media"
                        controls
                        playsInline
                        poster={
                          poster === null
                            ? undefined
                            : mediaAssetUrl(media.id, poster.id)
                        }
                        src={mediaAssetUrl(media.id, asset.id)}
                      />
                    ) : (
                      <img
                        alt={
                          media.accessibility_caption
                          || `Instagram ${mediaLabel(media)} ${mediaDisplayIdentifier(media)}`
                        }
                        className="viewer-media"
                        src={mediaAssetUrl(media.id, asset.id)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {hasMultipleAssets ? (
            <>
              {assetIndex > 0 ? (
                <button
                  aria-label="Previous image or video"
                  className="viewer-carousel-button viewer-carousel-button-previous"
                  type="button"
                  onClick={() => scrollToAsset(assetIndex - 1)}
                >
                  <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">
                    <path d="m15 18-6-6 6-6" />
                  </svg>
                </button>
              ) : null}
              <span aria-live="polite" className="viewer-carousel-counter">
                {assetIndex + 1} / {assets.length}
              </span>
              {assetIndex < assets.length - 1 ? (
                <button
                  aria-label="Next image or video"
                  className="viewer-carousel-button viewer-carousel-button-next"
                  type="button"
                  onClick={() => scrollToAsset(assetIndex + 1)}
                >
                  <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">
                    <path d="m9 18 6-6-6-6" />
                  </svg>
                </button>
              ) : null}
            </>
          ) : null}
        </div>
        <article className="viewer-details">
          <div className="viewer-owner">
            <ProfileAvatar profile={owner} />
            <Link to={`/profiles/${encodeURIComponent(owner.id)}`}>@{owner.username}</Link>
          </div>
          <h1 id="viewer-title">{mediaLabel(media)}</h1>
          <p className="viewer-meta">
            Published {formatDateTime(media.published_at)}
            {" · "}
            {mediaLabel(media)}
            {" · "}
            {mediaDisplayIdentifier(media)}
          </p>
          <p className="viewer-caption">{media.caption || media.accessibility_caption || "No caption was saved for this public item."}</p>
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
              onClick={() => setDeleteOpen(true)}
            >
              <TrashIcon />
            </button>
          </div>
          {actionError ? <p className="form-error" role="alert">{actionError}</p> : null}
        </article>
      </div>
      <ConfirmDialog
        confirmLabel="Delete media"
        description="This queues removal of this downloaded media item and its locally saved assets. This cannot be undone."
        open={deleteOpen}
        title="Delete this media item?"
        onClose={() => setDeleteOpen(false)}
        onConfirm={confirmDeletion}
      />
    </section>
  );
}
