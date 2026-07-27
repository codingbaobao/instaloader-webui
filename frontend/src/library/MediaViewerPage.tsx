import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../app/api";
import type { SessionData } from "../auth/useSession";
import { deleteMedia, getMedia, getProfile, mediaAssetUrl } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { formatDateTime } from "./MediaGrid";
import { usePolling } from "./usePolling";

type MediaViewerPageProps = Readonly<{ session: SessionData }>;

export function MediaViewerPage({ session }: MediaViewerPageProps) {
  const { mediaId = "" } = useParams();
  const navigate = useNavigate();
  const [assetIndex, setAssetIndex] = useState(0);
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

  useEffect(() => {
    setAssetIndex(0);
  }, [data?.media.id]);

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
  const asset = media.assets[assetIndex] ?? null;
  const hasMultipleAssets = media.assets.length > 1;
  const previousAsset = () => setAssetIndex((index) => (index - 1 + media.assets.length) % media.assets.length);
  const nextAsset = () => setAssetIndex((index) => (index + 1) % media.assets.length);

  return (
    <section className="library-page viewer-page" aria-labelledby="viewer-title">
      <Link className="back-link" to={`/profiles/${encodeURIComponent(media.owner_profile_id)}`}>Back to profile</Link>
      <div className="media-viewer-layout">
        <div className="viewer-asset-stage">
          {asset === null ? (
            <div className="viewer-empty-asset">This item is still preparing its downloaded asset.</div>
          ) : asset.kind === "video" ? (
            <video className="viewer-media" controls playsInline src={mediaAssetUrl(media.id, asset.id)} />
          ) : (
            <img alt={media.accessibility_caption || `Instagram ${media.kind} ${media.shortcode}`} className="viewer-media" src={mediaAssetUrl(media.id, asset.id)} />
          )}
          {hasMultipleAssets ? (
            <div className="viewer-carousel-controls" aria-label="Carousel controls">
              <button aria-label="Previous image or video" className="carousel-button" type="button" onClick={previousAsset}>Previous</button>
              <span>{assetIndex + 1} / {media.assets.length}</span>
              <button aria-label="Next image or video" className="carousel-button" type="button" onClick={nextAsset}>Next</button>
            </div>
          ) : null}
        </div>
        <article className="viewer-details">
          <div className="viewer-owner">
            <span className="profile-avatar" aria-hidden="true">{owner.username.slice(0, 1).toUpperCase()}</span>
            <Link to={`/profiles/${encodeURIComponent(owner.id)}`}>@{owner.username}</Link>
          </div>
          <h1 id="viewer-title">{media.kind === "reel" ? "Reel" : "Post"}</h1>
          <p className="viewer-meta">Published {formatDateTime(media.published_at)} · {media.kind}</p>
          <p className="viewer-caption">{media.caption || media.accessibility_caption || "No caption was saved for this public item."}</p>
          <a className="text-link" href={media.original_url} rel="noreferrer" target="_blank">Open original on Instagram</a>
          <div className="viewer-actions">
            <button className="danger-button danger-button-outline" type="button" onClick={() => setDeleteOpen(true)}>Delete downloaded media</button>
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
