"""Settings, read from environment variables (and a local .env file if present)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


@dataclass(frozen=True)
class Settings:
    # Claude
    model: str = "claude-opus-5-5"
    effort: str = "high"
    # Web app access
    app_password: str | None = None
    session_secret: str = ""
    # Storage
    data_dir: Path = Path("data")
    # Google auth: a service-account key file, or an OAuth user token created by scripts/google_login.py
    google_service_account_file: Path | None = None
    # Or the key's JSON pasted into an environment variable (for hosts without file uploads)
    google_service_account_json: str | None = None
    google_token_file: Path = Path("data/google_token.json")
    # GTM write safety switch: apply/publish endpoints refuse to run unless this is true
    allow_gtm_writes: bool = False
    # Seconds between GTM write calls (the GTM API quota is low)
    gtm_write_interval: float = 4.0
    # Tag naming convention checked by the audit (regex); empty disables the check
    tag_naming_pattern: str = r"^[^-]+ - [^-]+ - .+$"
    # Optional site URL that is checked with a headless browser after a publish
    site_url: str | None = None
    # 'Connect Google account' button: an OAuth client of type Web application
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    # Public address of the app, e.g. https://myapp.up.railway.app (worked out from requests if unset)
    public_url: str | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"


def load_settings() -> Settings:
    data_dir = _path("DATA_DIR") or Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if not secret:
        secret_file = data_dir / ".session_secret"
        if secret_file.exists():
            secret = secret_file.read_text().strip()
        else:
            secret = secrets.token_urlsafe(32)
            secret_file.write_text(secret)
    return Settings(
        model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"),
        effort=os.environ.get("CLAUDE_EFFORT", "high"),
        app_password=os.environ.get("APP_PASSWORD") or None,
        session_secret=secret,
        data_dir=data_dir,
        google_service_account_file=_path("GOOGLE_SERVICE_ACCOUNT_FILE"),
        google_service_account_json=os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or None,
        google_token_file=_path("GOOGLE_TOKEN_FILE") or data_dir / "google_token.json",
        allow_gtm_writes=_bool("ALLOW_GTM_WRITES", False),
        gtm_write_interval=float(os.environ.get("GTM_WRITE_INTERVAL", "4")),
        tag_naming_pattern=os.environ.get("TAG_NAMING_PATTERN", r"^[^-]+ - [^-]+ - .+$"),
        site_url=os.environ.get("SITE_URL") or None,
        google_oauth_client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or None,
        google_oauth_client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or None,
        public_url=(os.environ.get("PUBLIC_URL") or "").rstrip("/") or None,
    )
