import { FormEvent, useEffect, useRef, useState } from "react";

import { ApiError } from "../app/api";
import type { SessionData } from "../auth/useSession";
import { getInstagramSession, importInstagramSession, removeInstagramSession } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { formatDateTime } from "./MediaGrid";
import type { InstagramSessionStatus } from "./types";

type InstagramSessionCardProps = Readonly<{ session: SessionData }>;

const CHROME_WEB_STORE_URL = "https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc";
const SOURCE_URL = "https://github.com/kairi003/Get-cookies.txt-LOCALLY";

export function InstagramSessionCard({ session }: InstagramSessionCardProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<InstagramSessionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void loadStatus(controller.signal);
    return () => controller.abort();
  }, []);

  async function loadStatus(signal?: AbortSignal) {
    setLoading(true);
    setError(null);
    try {
      setStatus(await getInstagramSession(signal));
    } catch (cause) {
      if (signal?.aborted) {
        return;
      }
      setError(errorMessage(cause, "The Instagram session status could not be loaded."));
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
      }
    }
  }

  function selectFile(file: File | null) {
    if (file !== null && !file.name.toLowerCase().endsWith(".txt")) {
      setSelectedFile(null);
      setError("Choose a Cookie file with a .txt filename.");
      setNotice(null);
      return;
    }
    setSelectedFile(file);
    setError(null);
    setNotice(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedFile === null) {
      setError("Choose a .txt Cookie file before importing.");
      return;
    }
    if (!selectedFile.name.toLowerCase().endsWith(".txt")) {
      setError("Choose a Cookie file with a .txt filename.");
      return;
    }

    setUploading(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await importInstagramSession(selectedFile, session.csrf_token);
      setStatus(updated);
      setSelectedFile(null);
      if (fileInputRef.current !== null) {
        fileInputRef.current.value = "";
      }
      setNotice("Instagram session validated and imported. Sync all profiles now to use it for new jobs.");
    } catch (cause) {
      setError(errorMessage(cause, "The Cookie file could not be imported."));
    } finally {
      setUploading(false);
    }
  }

  async function confirmRemoval() {
    setRemoving(true);
    setError(null);
    setNotice(null);
    try {
      setStatus(await removeInstagramSession(session.csrf_token));
      setRemoveOpen(false);
      setNotice("Instagram session removed. Future jobs will use anonymous access unless you import a new Cookie file.");
    } catch (cause) {
      setRemoveOpen(false);
      setError(errorMessage(cause, "The Instagram session could not be removed."));
    } finally {
      setRemoving(false);
    }
  }

  const configured = status?.configured === true;
  const uploadDisabled = loading || uploading || removing;

  return (
    <section className="instagram-session-card" aria-labelledby="instagram-session-title">
      <header>
        <p className="eyebrow">Instagram access</p>
        <h2 id="instagram-session-title">Instagram session</h2>
        <p>Instagram can require a signed-in session even when the profile or post you want is public.</p>
      </header>

      {loading ? <p className="loading-copy" aria-live="polite">Loading Instagram session…</p> : null}
      {error ? (
        <div className="inline-error" role="alert">
          <span>{error}</span>
          {status === null && !loading ? <button className="text-button" type="button" onClick={() => void loadStatus()}>Try again</button> : null}
        </div>
      ) : null}
      {notice ? <p className="success-note" aria-live="polite">{notice}</p> : null}

      {status !== null ? (
        <div className="instagram-session-status" aria-live="polite">
          {configured ? (
            <>
              <strong>Connected as @{status.username ?? "Instagram user"}</strong>
              <dl>
                <div><dt>Imported</dt><dd>{formatDateTime(status.imported_at)}</dd></div>
                <div><dt>Last validated</dt><dd>{formatDateTime(status.last_validated_at)}</dd></div>
              </dl>
            </>
          ) : <span>No Instagram session is connected.</span>}
        </div>
      ) : null}

      <div className="cookie-import-instructions">
        <h3>Export a Netscape Cookie file</h3>
        <p>
          We recommend <a href={CHROME_WEB_STORE_URL} target="_blank" rel="noreferrer">Get cookies.txt LOCALLY</a> for Chrome and Edge. It is open source on <a href={SOURCE_URL} target="_blank" rel="noreferrer">GitHub</a>. Edge can install this Chrome Web Store extension.
        </p>
        <ol>
          <li>Sign in to <a href="https://www.instagram.com/" target="_blank" rel="noreferrer">instagram.com</a> in Chrome or Edge.</li>
          <li>Keep an Instagram tab active.</li>
          <li>Export only the current Instagram domain in Netscape format.</li>
          <li>Upload the resulting <code>.txt</code> file here.</li>
        </ol>
        <p className="credential-warning">This file is equivalent to an account credential. Delete the exported local file after a successful import.</p>
      </div>

      <form className="cookie-import-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="instagram-cookie-file">Cookie file (.txt)</label>
        <input
          accept=".txt,text/plain"
          disabled={uploadDisabled}
          id="instagram-cookie-file"
          ref={fileInputRef}
          type="file"
          onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
        />
        <p className="field-hint">Only the selected filename is checked here; file contents are sent directly for server-side validation.</p>
        <button className="primary-button" disabled={uploadDisabled} type="submit">
          {uploading ? "Validating…" : configured ? "Replace Cookie file" : "Validate and import"}
        </button>
      </form>

      {configured ? (
        <button className="danger-button danger-button-outline" disabled={uploading || removing} type="button" onClick={() => setRemoveOpen(true)}>
          Remove session
        </button>
      ) : null}

      <ConfirmDialog
        confirmLabel="Remove session"
        description="This removes the stored Instagram session. Future jobs may be denied anonymous access until you import a new Cookie file."
        open={removeOpen}
        title="Remove this Instagram session?"
        onClose={() => {
          if (!removing) {
            setRemoveOpen(false);
          }
        }}
        onConfirm={confirmRemoval}
      />
    </section>
  );
}

function errorMessage(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}
