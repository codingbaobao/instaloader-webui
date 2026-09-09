import type { MediaAsset, MediaSummary } from "./types";

export function contentAssets(media: MediaSummary): readonly MediaAsset[] {
  return media.assets.filter((asset) => asset.role === "content");
}

export function posterFor(
  media: MediaSummary,
  position: number,
): MediaAsset | null {
  return (
    media.assets.find(
      (asset) => asset.role === "poster" && asset.position === position,
    ) ?? null
  );
}

function lowestPosition(
  assets: readonly MediaAsset[],
): MediaAsset | null {
  return assets.reduce<MediaAsset | null>(
    (lowest, asset) =>
      lowest === null || asset.position < lowest.position ? asset : lowest,
    null,
  );
}

export function thumbnailAsset(media: MediaSummary): MediaAsset | null {
  return (
    lowestPosition(media.assets.filter((asset) => asset.role === "poster"))
    ?? lowestPosition(contentAssets(media))
  );
}

export function mediaLabel(media: MediaSummary): "Feed media" | "Story" {
  return media.collection === "story" ? "Story" : "Feed media";
}

export function mediaDisplayIdentifier(media: MediaSummary): string {
  return media.shortcode ?? media.story_media_id ?? media.identity_value;
}
