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
from datetime import datetime
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

DEFAULT_LOGIN_TIMEOUT = 180.0


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


def _parse_expiry(token_data: dict):
    """`google.oauth2.credentials.Credentials.expiry` must be an
    offset-naive UTC datetime (see `google.auth._helpers.utcnow`) or None.
    Returns None on anything unparseable -- a bad/missing expiry falls
    back to "never expires per this field", which is exactly today's
    behavior, not a new failure mode."""
    raw = token_data.get("expiry")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def _write_token_atomically(token_path: Path, token_data: dict):
    """Write the token file atomically AND restrictively-permissioned from
    the moment it exists -- never a plain `open()` followed by a separate
    `chmod` afterward, which leaves a window where the file exists at the
    process umask's (looser) default permissions before the chmod call
    lands (W14)."""
    import tempfile

    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(token_path.parent), prefix=".token-", suffix=".tmp")
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)
        os.replace(tmp_path, token_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_credentials(config: Config, account: str):
    """Load and, if needed, refresh credentials for `account`.

    Refresh is built with the scopes the token file itself records --
    never this module's narrower SCOPES list. See module docstring.

    Loads `expiry` from the token file (W12): without it, `creds.expired`
    is always False (there's nothing to compare against) and the
    refresh-and-persist branch below is dead code -- refresh still happens,
    but reactively, inside the HTTP transport on a 401, and the refreshed
    token is never written back. Loading expiry makes the proactive path
    here the one that actually runs.

    If persisting a successful refresh fails (the token file isn't
    writable -- e.g. deployed group-read-only), that is NOT an error: warn
    once on stderr and keep going with the in-memory refreshed token. The
    call that needed the refresh still succeeds; only the next call will
    need to refresh again too, which is a cost, not a failure.
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
            expiry=_parse_expiry(token_data),
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_data["token"] = creds.token
            if creds.expiry:
                token_data["expiry"] = creds.expiry.isoformat()
            try:
                _write_token_atomically(token_path, token_data)
            except OSError as e:
                print(
                    f"[Warning] Refreshed the token for '{account}' but could not "
                    f"persist it to {token_path}: {e}. Continuing with the "
                    "in-memory token; the next call will refresh again.",
                    file=sys.stderr,
                )

        return creds
    except AuthOrConfigError:
        raise
    except Exception as e:
        raise AuthOrConfigError(f"Failed to load/refresh credentials for '{account}': {e}") from e


def _run_local_server_prompt_to_stderr(flow, **kwargs):
    """`InstalledAppFlow.run_local_server` prints its "please visit this
    URL" prompt with a bare `print()` -- stdout, unconditionally, no
    `file=` override available through its own kwargs. That's a real
    problem under `--json`: `auth login --json` piping this straight
    through would put that prompt line INSIDE what's supposed to be a
    single JSON object on stdout. Redirect stdout to a buffer for the
    duration of this one call, and re-emit whatever it printed to stderr
    instead -- the prompt is still shown, just not where it would corrupt
    `--json` output.
    """
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        creds = flow.run_local_server(**kwargs)
    captured = buf.getvalue()
    if captured:
        sys.stderr.write(captured)
        if not captured.endswith("\n"):
            sys.stderr.write("\n")
    return creds


def run_login_flow(
    config: Config, account: str, headless: bool = False, timeout: float = DEFAULT_LOGIN_TIMEOUT
) -> str:
    """Run the OAuth consent flow and save a token for `account`. Returns
    the signed-in email address.

    Mirrors the tool this was built from: a loopback server on port 8085,
    redirect URI `http://localhost:8085/`. `headless=True` binds the
    LISTENING socket to 0.0.0.0 (reachable from another machine on the
    network) while still declaring the `localhost` redirect URI -- exactly
    what that original setup script did (its own docstring claims a
    stdin-code flow that the code never actually implements; the real
    behavior, verified by reading it, is this loopback-server-on-8085
    approach in every case).

    The non-headless path no longer falls back to a network-bound
    0.0.0.0 server SILENTLY when the local browser flow fails (e.g. no
    display, no browser found) -- it errors out and tells the caller to
    pass --headless explicitly. Binding to all interfaces is a real
    exposure change; it needs an explicit ask, not an exception handler
    picking it silently on the caller's behalf.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

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
            print(
                "Starting the OAuth loopback server on port 8085 (bound to all "
                "interfaces -- reachable from other machines on this network).",
                file=sys.stderr,
            )
            print(
                "Open the URL this prints in a browser (on this host or another "
                "machine that can reach it), sign in, and approve access. "
                f"Waiting up to {timeout:.0f}s.",
                file=sys.stderr,
            )
            creds = _run_local_server_prompt_to_stderr(
                flow, host="0.0.0.0", port=8085, open_browser=False, timeout_seconds=timeout
            )
        else:
            try:
                creds = _run_local_server_prompt_to_stderr(flow, port=0, timeout_seconds=timeout)
            except Exception as browser_err:
                raise AuthOrConfigError(
                    "Could not open a local browser for the OAuth consent flow "
                    f"({browser_err}). Re-run with --headless to use a loopback "
                    "server you open from a browser on another machine, or "
                    "without a browser at all."
                ) from browser_err
    except AuthOrConfigError:
        raise
    except Exception as e:
        raise AuthOrConfigError(f"Authentication failed: {e}") from e

    try:
        from googleapiclient.discovery import build

        oauth2 = build("oauth2", "v2", credentials=creds)
        email = oauth2.userinfo().get().execute().get("email", "unknown")
    except Exception:
        email = "unknown"

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }
    if creds.expiry:
        token_data["expiry"] = creds.expiry.isoformat()

    _write_token_atomically(config.token_path(account), token_data)

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
