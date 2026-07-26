from pathlib import Path

from alembic import command
from alembic.config import Config

from instaloader_webui.config import Settings


def run_migrations(settings: Settings) -> None:
    """Upgrade the configured application database to the latest revision."""
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    config_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{settings.database_path.resolve().as_posix()}"
    )
    command.upgrade(config, "head")
