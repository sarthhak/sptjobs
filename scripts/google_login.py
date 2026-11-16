"""One-time Google sign-in for the OAuth option.

1. In Google Cloud Console: enable "Google Analytics Data API", "Google Analytics Admin API" and
   "Tag Manager API"; create an OAuth client of type "Desktop app"; download it as client_secret.json.
2. Run on a machine with a browser:  python scripts/google_login.py --client-secret client_secret.json
3. Copy the written token file (default data/google_token.json) to the server, or set GOOGLE_TOKEN_FILE.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from app.google_auth import SCOPES  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-secret", default="client_secret.json")
    parser.add_argument("--out", default="data/google_token.json")
    args = parser.parse_args()
    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json())
    out.chmod(0o600)
    print(f"Saved token to {out}")


if __name__ == "__main__":
    main()
