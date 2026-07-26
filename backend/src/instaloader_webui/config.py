from pathlib import Path

from pydantic import SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IW_",
        env_file=".env",
        frozen=True,
        extra="ignore",
    )

    data_root: Path = Path("/data")
    static_root: Path = Path("/app/static")
    app_secret_key: SecretStr
    admin_username: str
    admin_password: SecretStr | None = None
    admin_password_file: Path | None = None
    session_cookie_secure: bool = False

    @computed_field
    @property
    def database_path(self) -> Path:
        return self.data_root / "database" / "app.sqlite3"
