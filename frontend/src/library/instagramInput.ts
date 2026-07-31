export type AddInputKind = "profile" | "media";

const mediaPathTypes = new Set(["p", "reel", "tv"]);

export function classifyAddInput(value: string): AddInputKind {
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    return "profile";
  }

  const hostname = url.hostname.toLowerCase();
  if (
    url.protocol !== "https:"
    || (hostname !== "instagram.com" && hostname !== "www.instagram.com")
  ) {
    return "profile";
  }

  const path = url.pathname.split("/").filter(Boolean);
  const [pathType, identifier, storyId, ...remainingPath] = path;
  if (remainingPath.length > 0) {
    return "profile";
  }
  if (mediaPathTypes.has(pathType?.toLowerCase() ?? "") && identifier && !storyId) {
    return "media";
  }
  if (
    pathType?.toLowerCase() === "stories"
    && identifier
    && storyId
    && /^\d+$/.test(storyId)
  ) {
    return "media";
  }
  return "profile";
}
