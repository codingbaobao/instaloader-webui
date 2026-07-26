from pathlib import Path

from pydantic import SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

ADMIN_USERNAME_PATTERN = r"^[A-Za-z0-9._-]{3,64}$"
MAXIMUM_USERNAME_BYTES = 64


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IW_",
        env_file=".env",
        frozen=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    data_root: Path = Path("/data")
    static_root: Path = Path("/app/static")
    admin_username: str
    admin_password: SecretStr | None = None
    admin_password_file: Path | None = None
    session_cookie_secure: bool = False

    @computed_field
    @property
    def database_path(self) -> Path:
        return self.data_root / "database" / "app.sqlite3"
