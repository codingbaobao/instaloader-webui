export type AddInputKind = "profile" | "media";

const mediaPathTypes = new Set(["p", "reel", "tv"]);
const storyUsernamePattern = /^[a-z0-9._]{1,30}$/i;
const storyIdPattern = /^\d{1,32}$/;

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
    || url.username
    || url.password
    || url.port
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
    && storyUsernamePattern.test(identifier ?? "")
    && storyIdPattern.test(storyId ?? "")
  ) {
    return "media";
  }
  return "profile";
}
