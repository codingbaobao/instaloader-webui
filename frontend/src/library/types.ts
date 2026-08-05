/**
 * Immutable shapes returned by the public-library endpoints. These names and
 * fields intentionally mirror backend/src/instaloader_webui/api/library_dtos.py.
 */
export type MediaKind = "post" | "reel" | "story";
export type AssetKind = "image" | "video";
export type AssetRole = "content" | "poster";

export type MediaAsset = Readonly<{
  id: string;
  media_id: string;
  relative_path: string;
  mime_type: string;
  kind: AssetKind;
  role: AssetRole;
  position: number;
  file_size: number;
  created_at: string;
}>;

export type MediaSummary = Readonly<{
  id: string;
  instagram_media_id: string | null;
  shortcode: string | null;
  story_media_id: string | null;
  identity_type: string;
  identity_value: string;
  owner_profile_id: string;
  kind: MediaKind;
  caption: string;
  accessibility_caption: string;
  published_at: string;
  original_url: string;
  story_expires_at: string | null;
  downloaded_at: string | null;
  created_at: string;
  updated_at: string;
  assets: readonly MediaAsset[];
}>;

/** The backend's media list and detail DTOs have the same fields. */
export type MediaDetail = MediaSummary;

export type MediaFeedPage = Readonly<{
  items: readonly MediaDetail[];
  newer_cursor: string | null;
  older_cursor: string | null;
}>;

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

export type JobIssue = Readonly<{
  identity_type: "shortcode" | "story_media_id";
  identity_value: string;
  shortcode: string | null;
  story_media_id: string | null;
  media_kind: MediaKind;
  error_code: string;
  safe_message: string;
  exception_class_chain: readonly string[];
  occurred_at: string;
}>;

export type JobSummary = Readonly<{
  id: string;
  type: string;
  state: string;
  payload: Readonly<Record<string, unknown>>;
  progress_current: number;
  progress_total: number | null;
  status_text: string;
  error: string | null;
  phase: string | null;
  issue_count: number;
  issues: readonly JobIssue[];
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}>;

/** The job detail endpoint adds issue records to the shared job shape. */
export type JobDetail = JobSummary;

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

export type InstagramSessionStatus = Readonly<{
  configured: boolean;
  username: string | null;
  imported_at: string | null;
  last_validated_at: string | null;
}>;

export type FolloweeImportCandidate = Readonly<{
  id: string;
  instagram_user_id: string;
  username: string;
  full_name: string;
  profile_pic_url: string | null;
  is_private: boolean;
  already_exists: boolean;
}>;

export type FolloweeImportBatch = Readonly<{
  id: string;
  state: string;
  source_username: string;
  session_imported_at: string;
  job: JobSummary;
  total_count: number;
  importable_count: number;
  existing_count: number;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  imported_at: string | null;
  candidates: readonly FolloweeImportCandidate[];
}>;

export type FolloweeImportCommitResult = Readonly<{
  imported_count: number;
  existing_count: number;
  jobs: readonly JobSummary[];
}>;
