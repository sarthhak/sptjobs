"""'Connect Google account' sign-in, so the app can use your own Google login instead of a service account.

Uses a Google OAuth client of type "Web application". The resulting token (with a refresh token) is saved
in DATA_DIR and used for all GA4 and GTM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

import requests

from .google_auth import SCOPES

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class OAuthError(RuntimeError):
    pass


def authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",     # ask for a refresh token
        "prompt": "consent",          # always return one, even on re-connect
        "include_granted_scopes": "true",
        "state": state,
    })


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, timeout=30)
    if resp.status_code != 200:
        raise OAuthError(f"Google rejected the sign-in ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def save_token(path: Path, token: dict, client_id: str, client_secret: str) -> None:
    if not token.get("refresh_token"):
        raise OAuthError("Google did not return a refresh token. Remove the app's access at "
                         "myaccount.google.com/permissions and connect again.")
    granted = set(token.get("scope", "").split())
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        raise OAuthError("Some permissions were not granted: " + ", ".join(s.rsplit("/", 1)[1] for s in missing)
                         + ". Connect again and tick every box on Google's consent screen.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "token": token["access_token"], "refresh_token": token["refresh_token"], "token_uri": TOKEN_URL,
        "client_id": client_id, "client_secret": client_secret, "scopes": SCOPES,
    }))
    path.chmod(0o600)
