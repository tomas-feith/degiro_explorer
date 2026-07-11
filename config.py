"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Settings sourced from the .env file (see .env.example)."""

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_prefix="DEGIRO_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    username: str = ""
    password: str = ""
    totp_secret: str = ""
    int_account: int | None = None
    db_path: str = "data/degiro.db"

    @property
    def db_file(self) -> Path:
        """Absolute path to the SQLite database, with parent dir ensured."""
        path = Path(self.db_path)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def tickers_file(self) -> Path:
        return ROOT / "tickers.yml"

    def require_credentials(self) -> None:
        """Raise a friendly error if mandatory credentials are missing."""
        missing = [
            name
            for name, val in (("DEGIRO_USERNAME", self.username), ("DEGIRO_PASSWORD", self.password))
            if not val
        ]
        if missing:
            raise SystemExit(
                "Missing credentials: "
                + ", ".join(missing)
                + ".\nCopy .env.example to .env and fill it in."
            )


settings = Settings()
