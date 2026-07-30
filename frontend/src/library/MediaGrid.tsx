import { Link } from "react-router-dom";

import { mediaAssetUrl } from "./api";
import type { MediaAsset, MediaSummary } from "./types";

type MediaGridProps = Readonly<{
  media: readonly MediaSummary[];
  emptyTitle?: string;
  emptyDetail?: string;
}>;

function firstAsset(media: MediaSummary): MediaAsset | null {
  return media.assets[0] ?? null;
}

function MediaThumbnail({ media }: Readonly<{ media: MediaSummary }>) {
  const asset = firstAsset(media);
  if (asset === null) {
    return <span className="media-thumbnail-empty">Preparing media</span>;
  }
  const source = mediaAssetUrl(media.id, asset.id);
  if (asset.kind === "video") {
    return (
      <video
        aria-label={`Video from ${media.shortcode}`}
        className="media-thumbnail-media"
        muted
        playsInline
        preload="metadata"
        src={source}
      />
    );
  }
  return (
    <img
      alt={media.accessibility_caption || `Instagram post ${media.shortcode}`}
      className="media-thumbnail-media"
      loading="lazy"
      src={source}
    />
  );
}

export function MediaGrid({
  media,
  emptyTitle = "No media yet",
  emptyDetail = "Downloaded posts and reels will appear here.",
}: MediaGridProps) {
  if (media.length === 0) {
    return (
      <section className="empty-state media-empty-state">
        <span className="empty-state-mark" aria-hidden="true">+</span>
        <h2>{emptyTitle}</h2>
        <p>{emptyDetail}</p>
      </section>
    );
  }

  return (
    <div className="media-grid" aria-label="Media library">
      {media.map((item) => (
        <Link
          aria-label={`Open ${item.kind} ${item.shortcode}`}
          className="media-grid-item"
          key={item.id}
          to={`/media/${encodeURIComponent(item.id)}`}
        >
          <MediaThumbnail media={item} />
          <span className="media-grid-overlay" aria-hidden="true">
            <span>{item.kind === "reel" ? "Reel" : "Post"}</span>
            {item.assets.length > 1 ? <span>{item.assets.length} items</span> : null}
          </span>
        </Link>
      ))}
    </div>
  );
}
