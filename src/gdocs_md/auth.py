"""OAuth: login, status, and credential loading.

Two different scope lists matter here, and conflating them breaks tokens:

- SCOPES: what a *fresh* `auth login` requests. Narrow, on purpose --
  just what this tool touches (Docs, Drive, Sheets read, and the identity
  scopes needed to show which account got signed in).
- What a *refresh* requests: the scopes the token itself was already
  granted, read back from the saved token file, not this module's SCOPES
  list. A token minted by an older, broader tool (or a shared OAuth client
  whose consent screen was configured with more scopes) still has to keep
  refreshing under gdocs-md. Building the refresh Credentials object with a
  *narrower* scope list than what the token was granted has produced
  invalid_scope failures on real tokens before -- see AGENTS.md's token
  compatibility note. So: request narrow only at login; on refresh, trust
  the token file's own `scopes` field (or omit scopes entirely if the file
  doesn't have one).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import Config
from .errors import AuthOrConfigError

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# The local redirect this tool's own headless/loopback flow uses. Never read
# from oauth-client.json's `redirect_uris` -- see load_oauth_client below.
_REDIRECT_URIS = ["http://localhost:8085/", "http://localhost"]


def load_oauth_client(config: Config) -> tuple[str, str]:
    """Load (client_id, client_secret) from the configured oauth-client file.

    Accepts both shapes Google hands out: the `installed`-wrapped Desktop
    client download, and a flat {"client_id": ..., "client_secret": ...}
    object. Deliberately never reads `redirect_uris` from this file --
    `run_login_flow` below builds its own client_config with the documented
    loopback redirect baked in, and adopting whatever the downloaded file
    says would silently break that.
    """
    path = config.oauth_client_file
    if not path.exists():
        raise AuthOrConfigError(
            f"OAuth client file not found: {path}\n"
            "Download a Desktop-app OAuth client from Google Cloud Console "
            "and save it there (or point --oauth-client / "
            "GDOCS_MD_OAUTH_CLIENT at it)."
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AuthOrConfigError(f"Failed to read OAuth client file '{path}': {e}") from e

    try:
        if "installed" in data:
            inner = data["installed"]
            return inner["client_id"], inner["client_secret"]
        return data["client_id"], data["client_secret"]
    except KeyError as e:
        raise AuthOrConfigError(
            f"OAuth client file '{path}' is missing {e}."
        ) from e


def _load_token_data(token_path: Path) -> dict:
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AuthOrConfigError(f"Failed to read token file '{token_path}': {e}") from e


def load_credentials(config: Config, account: str):
    """Load and, if needed, refresh credentials for `account`.

    Refresh is built with the scopes the token file itself records --
    never this module's narrower SCOPES list. See module docstring.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = config.token_path(account)
    if not token_path.exists():
        raise AuthOrConfigError(
            f"Token file not found for account '{account}': {token_path}\n"
            f"Run 'gdocs-md auth login --account {account}' to authenticate."
        )

    token_data = _load_token_data(token_path)
    token_scopes = token_data.get("scopes") or None

    try:
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_scopes,
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_data["token"] = creds.token
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=2)
            os.chmod(token_path, 0o600)

        return creds
    except AuthOrConfigError:
        raise
    except Exception as e:
        raise AuthOrConfigError(f"Failed to load/refresh credentials for '{account}': {e}") from e


def run_login_flow(config: Config, account: str, headless: bool = False) -> str:
    """Run the OAuth consent flow and save a token for `account`. Returns the
    signed-in email address."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    client_id, client_secret = load_oauth_client(config)
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": _REDIRECT_URIS,
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    try:
        if headless:
            print("Starting auth server on port 8085 (bound to all interfaces).", file=sys.stderr)
            print("Open the printed URL from another machine on your network,", file=sys.stderr)
            print("or forward port 8085 to this host.", file=sys.stderr)
            creds = flow.run_local_server(host="0.0.0.0", port=8085, open_browser=False)
        else:
            try:
                creds = flow.run_local_server(port=0)
            except Exception:
                print("Local browser flow failed; falling back to a headless-style", file=sys.stderr)
                print("loopback server on port 8085.", file=sys.stderr)
                creds = flow.run_local_server(host="0.0.0.0", port=8085, open_browser=False)
    except Exception as e:
        raise AuthOrConfigError(f"Authentication failed: {e}") from e

    try:
        oauth2 = build("oauth2", "v2", credentials=creds)
        email = oauth2.userinfo().get().execute().get("email", "unknown")
    except Exception:
        email = "unknown"

    token_path = config.token_path(account)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    os.chmod(token_path, 0o600)

    return email


def check_status(config: Config, account: str) -> dict:
    """Return a status dict without raising: {'configured': bool, 'account':
    str, 'token_path': str, 'email': str|None, 'error': str|None}."""
    token_path = config.token_path(account)
    result = {
        "configured": False,
        "account": account,
        "token_path": str(token_path),
        "email": None,
        "error": None,
    }
    if not token_path.exists():
        result["error"] = "no token file"
        return result

    try:
        creds = load_credentials(config, account)
        from googleapiclient.discovery import build

        oauth2 = build("oauth2", "v2", credentials=creds)
        email = oauth2.userinfo().get().execute().get("email")
        result["configured"] = True
        result["email"] = email
    except Exception as e:
        result["error"] = str(e)

    return result
