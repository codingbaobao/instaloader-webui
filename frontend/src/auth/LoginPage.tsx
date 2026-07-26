import { type FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { ApiError } from "../app/api";
import { requestSession } from "./sessionData";
import { useSession } from "./useSession";

export function LoginPage() {
  const navigate = useNavigate();
  const { session, applySessionOperation, logoutPending } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (session !== null) {
    return (
      <Navigate
        replace
        to={session.must_change_password ? "/change-password" : "/"}
      />
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || logoutPending) {
      return;
    }
    setSubmitting(true);
    setErrorMessage("");
    try {
      const authenticated = await applySessionOperation(() =>
        requestSession("/api/auth/login", {
          method: "POST",
          body: { username, password },
        }),
      );
      setPassword("");
      if (authenticated === null) {
        return;
      }
      navigate(
        authenticated.must_change_password ? "/change-password" : "/",
        { replace: true },
      );
    } catch (error) {
      setPassword("");
      setErrorMessage(
        error instanceof ApiError
          ? error.message
          : "Sign in could not be completed.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="brand-mark" aria-hidden="true">
          iw
        </div>
        <p className="eyebrow">Personal media archive</p>
        <h1 id="login-title">Sign in</h1>
        <p className="auth-intro">
          Use your WebUI administrator account to continue.
        </p>
        <form onSubmit={handleSubmit}>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            maxLength={64}
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {errorMessage ? (
            <p className="form-error" role="alert">
              {errorMessage}
            </p>
          ) : null}
          <button
            className="primary-button"
            type="submit"
            disabled={submitting || logoutPending}
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
