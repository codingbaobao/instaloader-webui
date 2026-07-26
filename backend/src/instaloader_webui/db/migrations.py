from importlib.resources import as_file, files
from pathlib import Path

from alembic import command
from alembic.config import Config

from instaloader_webui.config import Settings


def _upgrade(settings: Settings, script_location: Path) -> None:
    config = Config()
    config.set_main_option("script_location", str(script_location.resolve()))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{settings.database_path.resolve().as_posix()}"
    )
    command.upgrade(config, "head")


def run_migrations(settings: Settings) -> None:
    """Upgrade the database from packaged assets or a source checkout."""
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    packaged_migrations = files("instaloader_webui").joinpath("migrations")
    if packaged_migrations.is_dir():
        with as_file(packaged_migrations) as script_location:
            _upgrade(settings, script_location)
        return

    source_migrations = Path(__file__).resolve().parents[3] / "migrations"
    _upgrade(settings, source_migrations)
