"""Google credentials for the GA4 and GTM APIs."""

from __future__ import annotations

import threading
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import credentials as user_credentials
from google.oauth2 import service_account
from googleapiclient import discovery

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/tagmanager.readonly",
    "https://www.googleapis.com/auth/tagmanager.edit.containers",
    "https://www.googleapis.com/auth/tagmanager.edit.containerversions",
    "https://www.googleapis.com/auth/tagmanager.publish",
]


class GoogleAuthMissing(RuntimeError):
    pass


class GoogleCredentials:
    """Loads credentials once and refreshes them when needed. Thread-safe."""

    def __init__(self, service_account_file: Path | None, token_file: Path):
        self._sa_file = service_account_file
        self._token_file = token_file
        self._creds = None
        self._lock = threading.Lock()

    def describe(self) -> str:
        if self._sa_file:
            return f"service account ({self._sa_file.name})"
        return f"OAuth user token ({self._token_file})"

    def get(self):
        with self._lock:
            if self._creds is None:
                self._creds = self._load()
            if not self._creds.valid:
                self._creds.refresh(Request())
            return self._creds

    def _load(self):
        if self._sa_file:
            if not self._sa_file.exists():
                raise GoogleAuthMissing(f"Service account file not found: {self._sa_file}")
            return service_account.Credentials.from_service_account_file(str(self._sa_file), scopes=SCOPES)
        if self._token_file.exists():
            return user_credentials.Credentials.from_authorized_user_file(str(self._token_file), SCOPES)
        raise GoogleAuthMissing(
            "No Google credentials. Set GOOGLE_SERVICE_ACCOUNT_FILE, or run "
            "`python scripts/google_login.py` to create an OAuth token."
        )

    def build(self, api: str, version: str):
        # httplib2 is not thread-safe, so build a fresh client per call (discovery docs are bundled).
        return discovery.build(api, version, credentials=self.get(), cache_discovery=False)
