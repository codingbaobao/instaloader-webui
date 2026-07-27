/**
 * Immutable shapes returned by the public-library endpoints. These names and
 * fields intentionally mirror backend/src/instaloader_webui/api/library_dtos.py.
 */
export type MediaAsset = Readonly<{
  id: string;
  media_id: string;
  relative_path: string;
  mime_type: string;
  kind: string;
  position: number;
  file_size: number;
  created_at: string;
}>;

export type MediaSummary = Readonly<{
  id: string;
  instagram_media_id: string | null;
  shortcode: string;
  owner_profile_id: string;
  kind: string;
  caption: string;
  accessibility_caption: string;
  published_at: string;
  original_url: string;
  downloaded_at: string | null;
  created_at: string;
  updated_at: string;
  assets: readonly MediaAsset[];
}>;

/** The backend's media list and detail DTOs have the same fields. */
export type MediaDetail = MediaSummary;

export type ProfileSummary = Readonly<{
  id: string;
  instagram_user_id: string | null;
  username: string;
  full_name: string;
  biography: string;
  profile_pic_url: string | null;
  tracked: boolean;
  status: string;
  last_sync_attempted_at: string | null;
  last_sync_succeeded_at: string | null;
  created_at: string;
  updated_at: string;
  media_count: number;
}>;

/** The backend's profile list and detail DTOs have the same fields. */
export type ProfileDetail = ProfileSummary;

export type JobSummary = Readonly<{
  id: string;
  type: string;
  state: string;
  payload: Readonly<Record<string, unknown>>;
  progress_current: number;
  progress_total: number | null;
  status_text: string;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}>;

export type LibrarySettings = Readonly<{
  id: string;
  profile_sync_interval_minutes: number;
  next_sync_at: string;
  created_at: string;
  updated_at: string;
}>;

export type ProfileCreateResult = Readonly<{
  profile: ProfileDetail;
  job: JobSummary;
}>;

export type SyncAllResult = Readonly<{
  jobs: readonly JobSummary[];
}>;
