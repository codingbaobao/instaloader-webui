from pathlib import Path

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

ADMIN_USERNAME_PATTERN = r"^[A-Za-z0-9._-]{3,64}$"
MINIMUM_ADMIN_PASSWORD_LENGTH = 16
MAXIMUM_BOOTSTRAP_PASSWORD_FILE_BYTES = 4 * 1024


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
    app_secret_key: SecretStr = Field(min_length=32)
    admin_username: str
    admin_password: SecretStr | None = None
    admin_password_file: Path | None = None
    session_cookie_secure: bool = False

    @computed_field
    @property
    def database_path(self) -> Path:
        return self.data_root / "database" / "app.sqlite3"
