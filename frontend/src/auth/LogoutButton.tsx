import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../app/api";
import { useSession } from "./useSession";

type LogoutButtonProps = {
  className?: string;
};

export function LogoutButton({ className = "text-button" }: LogoutButtonProps) {
  const navigate = useNavigate();
  const { logout, logoutPending } = useSession();
  const [errorMessage, setErrorMessage] = useState("");

  async function handleLogout() {
    setErrorMessage("");
    try {
      if (await logout()) {
        navigate("/login", { replace: true });
      }
    } catch (error) {
      setErrorMessage(
        error instanceof ApiError
          ? error.message
          : "Sign out could not be completed.",
      );
    }
  }

  return (
    <span className="logout-control">
      <button
        className={className}
        type="button"
        disabled={logoutPending}
        onClick={() => void handleLogout()}
      >
        {logoutPending ? "Signing out…" : "Sign out"}
      </button>
      {errorMessage ? (
        <span className="form-error" role="alert">
          {errorMessage}
        </span>
      ) : null}
    </span>
  );
}
