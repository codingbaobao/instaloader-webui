import { apiRequest } from "../app/api";
import type {
  JobSummary,
  LibrarySettings,
  MediaDetail,
  MediaSummary,
  ProfileCreateResult,
  ProfileDetail,
  ProfileSummary,
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

export function listProfiles(): Promise<readonly ProfileSummary[]> {
  return apiRequest<readonly ProfileSummary[]>("/api/profiles");
}

export function getProfile(profileId: string): Promise<ProfileDetail> {
  return apiRequest<ProfileDetail>(`/api/profiles/${pathSegment(profileId)}`);
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
  return apiRequest<readonly MediaSummary[]>(`/api/media${suffix}`);
}

export function getMedia(mediaId: string): Promise<MediaDetail> {
  return apiRequest<MediaDetail>(`/api/media/${pathSegment(mediaId)}`);
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

export function listJobs(): Promise<readonly JobSummary[]> {
  return apiRequest<readonly JobSummary[]>("/api/jobs");
}

export function getJob(jobId: string): Promise<JobSummary> {
  return apiRequest<JobSummary>(`/api/jobs/${pathSegment(jobId)}`);
}

export function getLibrarySettings(): Promise<LibrarySettings> {
  return apiRequest<LibrarySettings>("/api/settings");
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
