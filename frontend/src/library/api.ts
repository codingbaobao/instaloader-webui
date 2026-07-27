import { apiRequest } from "../app/api";
import type {
  JobSummary,
  LibrarySettings,
  MediaDetail,
  MediaSummary,
  ProfileCreateResult,
  ProfileDetail,
  ProfileSummary,
  InstagramSessionStatus,
  SyncAllResult,
} from "./types";

type MediaListOptions = Readonly<{
  profileId?: string;
  kind?: "post" | "reel";
  limit?: number;
}>;

function pathSegment(value: string): string {
  return encodeURIComponent(value);
}

export function mediaAssetUrl(mediaId: string, assetId: string): string {
  return `/api/media/${pathSegment(mediaId)}/assets/${pathSegment(assetId)}`;
}

export function listProfiles(
  signal?: AbortSignal,
): Promise<readonly ProfileSummary[]> {
  return apiRequest<readonly ProfileSummary[]>("/api/profiles", { signal });
}

export function getProfile(
  profileId: string,
  signal?: AbortSignal,
): Promise<ProfileDetail> {
  return apiRequest<ProfileDetail>(`/api/profiles/${pathSegment(profileId)}`, {
    signal,
  });
}

export function addProfile(
  input: string,
  csrfToken: string,
): Promise<ProfileCreateResult> {
  return apiRequest<ProfileCreateResult>("/api/profiles", {
    method: "POST",
    body: { input },
    csrfToken,
  });
}

export function syncProfile(profileId: string, csrfToken: string): Promise<JobSummary> {
  return apiRequest<JobSummary>(`/api/profiles/${pathSegment(profileId)}/sync`, {
    method: "POST",
    csrfToken,
  });
}

export function deleteProfile(
  profileId: string,
  csrfToken: string,
): Promise<JobSummary> {
  return apiRequest<JobSummary>(`/api/profiles/${pathSegment(profileId)}`, {
    method: "DELETE",
    csrfToken,
  });
}

export function listMedia(
  options: MediaListOptions = {},
  signal?: AbortSignal,
): Promise<readonly MediaSummary[]> {
  const query = new URLSearchParams();
  if (options.profileId) {
    query.set("profile_id", options.profileId);
  }
  if (options.kind) {
    query.set("kind", options.kind);
  }
  if (options.limit !== undefined) {
    query.set("limit", String(options.limit));
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return apiRequest<readonly MediaSummary[]>(`/api/media${suffix}`, { signal });
}

export function getMedia(
  mediaId: string,
  signal?: AbortSignal,
): Promise<MediaDetail> {
  return apiRequest<MediaDetail>(`/api/media/${pathSegment(mediaId)}`, {
    signal,
  });
}

export function addMedia(input: string, csrfToken: string): Promise<JobSummary> {
  return apiRequest<JobSummary>("/api/media", {
    method: "POST",
    body: { input },
    csrfToken,
  });
}

export function deleteMedia(mediaId: string, csrfToken: string): Promise<JobSummary> {
  return apiRequest<JobSummary>(`/api/media/${pathSegment(mediaId)}`, {
    method: "DELETE",
    csrfToken,
  });
}

export function listJobs(signal?: AbortSignal): Promise<readonly JobSummary[]> {
  return apiRequest<readonly JobSummary[]>("/api/jobs", { signal });
}

export function getJob(jobId: string, signal?: AbortSignal): Promise<JobSummary> {
  return apiRequest<JobSummary>(`/api/jobs/${pathSegment(jobId)}`, { signal });
}

export function getLibrarySettings(signal?: AbortSignal): Promise<LibrarySettings> {
  return apiRequest<LibrarySettings>("/api/settings", { signal });
}

export function updateLibrarySettings(
  profileSyncIntervalMinutes: number,
  csrfToken: string,
): Promise<LibrarySettings> {
  return apiRequest<LibrarySettings>("/api/settings", {
    method: "PATCH",
    body: { profile_sync_interval_minutes: profileSyncIntervalMinutes },
    csrfToken,
  });
}

export function syncAllProfiles(csrfToken: string): Promise<SyncAllResult> {
  return apiRequest<SyncAllResult>("/api/settings/sync-all", {
    method: "POST",
    csrfToken,
  });
}

export function getInstagramSession(
  signal?: AbortSignal,
): Promise<InstagramSessionStatus> {
  return apiRequest<InstagramSessionStatus>("/api/settings/instagram-session", {
    signal,
  });
}

export function importInstagramSession(
  file: File,
  csrfToken: string,
): Promise<InstagramSessionStatus> {
  const body = new FormData();
  body.append("cookie_file", file);
  return apiRequest<InstagramSessionStatus>("/api/settings/instagram-session", {
    method: "POST",
    body,
    csrfToken,
  });
}

export function removeInstagramSession(
  csrfToken: string,
): Promise<InstagramSessionStatus> {
  return apiRequest<InstagramSessionStatus>("/api/settings/instagram-session", {
    method: "DELETE",
    csrfToken,
  });
}
