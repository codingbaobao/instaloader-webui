import { useCallback } from "react";
import { Link } from "react-router-dom";

import type { SessionData } from "../auth/useSession";
import { listMedia, listProfiles } from "./api";
import { MediaGrid } from "./MediaGrid";
import { ProfileAvatar } from "./ProfileAvatar";
import { usePolling } from "./usePolling";

type HomePageProps = Readonly<{ session: SessionData }>;

export function HomePage({ session }: HomePageProps) {
  const loadHome = useCallback(async (signal: AbortSignal) => {
    const [profiles, media] = await Promise.all([
      listProfiles(signal),
      listMedia({ limit: 18 }, signal),
    ]);
    return { profiles, media };
  }, []);
  const { data, error, loading, reload } = usePolling(loadHome, 0, true);

  return (
    <section className="library-page home-page" aria-labelledby="home-title">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Your library</p>
          <h1 id="home-title">Welcome back, {session.username}</h1>
          <p className="page-intro">The latest public Instagram media saved to your library.</p>
        </div>
        <Link className="primary-button compact-button" to="/add">
          Add public link
        </Link>
      </header>

      <section aria-labelledby="shortcuts-title" className="profile-shortcuts-section">
        <div className="section-heading">
          <h2 id="shortcuts-title">Profiles</h2>
          <Link className="text-link" to="/profiles">See all</Link>
        </div>
        {data?.profiles.length ? (
          <div className="profile-shortcuts">
            {data.profiles.slice(0, 10).map((profile) => (
              <Link className="profile-shortcut" key={profile.id} to={`/profiles/${encodeURIComponent(profile.id)}`}>
                <ProfileAvatar className="profile-avatar-ring" profile={profile} />
                <span>@{profile.username}</span>
              </Link>
            ))}
          </div>
        ) : (
          <div className="shortcuts-empty">Add a public profile to create a shortcut.</div>
        )}
      </section>

      <section aria-labelledby="recent-media-title" className="recent-media-section">
        <div className="section-heading">
          <h2 id="recent-media-title">Recent media</h2>
          <button className="text-button" type="button" disabled={loading} onClick={() => void reload()}>
            Refresh
          </button>
        </div>
        {error ? (
          <div className="inline-error" role="alert">
            <span>{error}</span>
            <button className="text-button" type="button" onClick={() => void reload()}>Try again</button>
          </div>
        ) : null}
        {data === null && loading ? <p className="loading-copy">Loading your library…</p> : null}
        {data !== null ? (
          <MediaGrid
            media={data.media}
            emptyDetail="Add a profile, post, reel, or TV link to begin collecting public media."
            emptyTitle="Your library is ready"
          />
        ) : null}
      </section>
    </section>
  );
}
