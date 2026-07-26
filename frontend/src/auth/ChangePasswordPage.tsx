import { type FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { ApiError, apiRequest } from "../app/api";
import { type SessionData, useSession } from "./useSession";

type ChangePasswordPageProps = {
  csrfToken?: string;
};

export function ChangePasswordPage({
  csrfToken,
}: ChangePasswordPageProps = {}) {
  const navigate = useNavigate();
  const { session, setSession } = useSession();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (session === null && csrfToken === undefined) {
    return <Navigate replace to="/login" />;
  }
  if (session !== null && !session.must_change_password) {
    return <Navigate replace to="/" />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    if (newPassword !== confirmation) {
      setErrorMessage("The new passwords do not match.");
      return;
    }

    setSubmitting(true);
    setErrorMessage("");
    try {
      const updated = await apiRequest<SessionData>(
        "/api/auth/change-password",
        {
          method: "POST",
          csrfToken: csrfToken ?? session?.csrf_token,
          body: {
            current_password: currentPassword,
            new_password: newPassword,
          },
        },
      );
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setSession(updated);
      navigate("/", { replace: true });
    } catch (error) {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setErrorMessage(
        error instanceof ApiError
          ? error.message
          : "The password could not be changed.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-card" aria-labelledby="password-title">
        <p className="eyebrow">First sign-in</p>
        <h1 id="password-title">Change your password</h1>
        <p className="auth-intro">
          Choose a unique administrator password with at least 16 characters.
        </p>
        <form onSubmit={handleSubmit}>
          <label htmlFor="current-password">Current password</label>
          <input
            id="current-password"
            type="password"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
          />
          <label htmlFor="new-password">New password</label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            minLength={16}
            required
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
          <label htmlFor="confirm-password">Confirm new password</label>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            minLength={16}
            required
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
          />
          {errorMessage ? (
            <p className="form-error" role="alert">
              {errorMessage}
            </p>
          ) : null}
          <button className="primary-button" type="submit" disabled={submitting}>
            {submitting ? "Changing password…" : "Change password"}
          </button>
        </form>
      </section>
    </main>
  );
}
