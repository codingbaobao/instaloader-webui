import { useState } from "react";

import { profileAvatarUrl } from "./api";
import type { ProfileSummary } from "./types";

type ProfileAvatarProfile = Pick<
  ProfileSummary,
  "id" | "profile_pic_url" | "updated_at" | "username"
>;

type ProfileAvatarProps = Readonly<{
  className?: string;
  profile: ProfileAvatarProfile;
}>;

export function ProfileAvatar({ className, profile }: ProfileAvatarProps) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const imageUrl = `${profileAvatarUrl(profile.id)}?revision=${encodeURIComponent(profile.updated_at)}`;
  const showImage = profile.profile_pic_url !== null && failedUrl !== imageUrl;
  const wrapperClassName = ["profile-avatar", className].filter(Boolean).join(" ");

  return (
    <span className={wrapperClassName} aria-hidden="true">
      {showImage ? (
        <img
          alt=""
          className="profile-avatar-image"
          src={imageUrl}
          onError={() => setFailedUrl(imageUrl)}
        />
      ) : (
        profile.username.slice(0, 1).toUpperCase()
      )}
    </span>
  );
}
