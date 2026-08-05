import { Link } from "react-router-dom";

import { mediaAssetUrl } from "./api";
import {
  contentAssets,
  mediaDisplayIdentifier,
  mediaLabel,
  thumbnailAsset,
} from "./mediaPresentation";
import type { MediaSummary } from "./types";

export type MediaGridSource =
  | Readonly<{ type: "recent" }>
  | Readonly<{
      type: "profile";
      profileId: string;
      kind: MediaSummary["kind"];
    }>;

type MediaGridProps = Readonly<{
  media: readonly MediaSummary[];
  emptyTitle?: string;
  emptyDetail?: string;
  source?: MediaGridSource;
}>;

function mediaViewerUrl(mediaId: string, source?: MediaGridSource): string {
  const path = `/media/${encodeURIComponent(mediaId)}`;
  if (source === undefined) {
    return path;
  }
  const query = new URLSearchParams({ source: source.type });
  if (source.type === "profile") {
    query.set("profileId", source.profileId);
    query.set("kind", source.kind);
  }
  return `${path}?${query.toString()}`;
}

function MediaThumbnail({ media }: Readonly<{ media: MediaSummary }>) {
  const asset = thumbnailAsset(media);
  if (asset === null) {
    return <span className="media-thumbnail-empty">Preparing media</span>;
  }
  const source = mediaAssetUrl(media.id, asset.id);
  const identifier = mediaDisplayIdentifier(media);
  if (asset.kind === "video") {
    return (
      <video
        aria-label={`Video from ${identifier}`}
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
      alt={
        media.accessibility_caption
        || `Instagram ${mediaLabel(media)} ${identifier}`
      }
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
  source,
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
          aria-label={`Open ${mediaLabel(item)} ${mediaDisplayIdentifier(item)}`}
          className="media-grid-item"
          key={item.id}
          to={mediaViewerUrl(item.id, source)}
        >
          <MediaThumbnail media={item} />
          <span className="media-grid-overlay" aria-hidden="true">
            <span>{mediaLabel(item)}</span>
            {contentAssets(item).length > 1 ? (
              <span>{contentAssets(item).length} items</span>
            ) : null}
          </span>
        </Link>
      ))}
    </div>
  );
}
