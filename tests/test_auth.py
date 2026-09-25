"""auth.py: expiry loading/proactive refresh (W12), the unwritable-token
best-effort persist (W12), and atomic/0600-from-creation token writes
(W14). No live Google calls -- refresh itself is monkeypatched onto
Credentials.refresh."""

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from gdocs_md import auth
from gdocs_md.config import resolve_config
from gdocs_md.errors import AuthOrConfigError


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _config(tmp_path):
    args = Args(config_file=None, credentials_dir=str(tmp_path), oauth_client_file=None, registry_file=None, account=None)
    return resolve_config(args)


def _write_token(config, account, **fields):
    path = config.token_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "token": "old-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "csecret",
        "scopes": ["openid"],
        **fields,
    }
    path.write_text(json.dumps(data))
    return path


def test_no_expiry_field_does_not_refresh(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _write_token(config, "a")  # no "expiry" key at all

    def boom_refresh(self, request):
        raise AssertionError("refresh should not have been called")

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", boom_refresh)
    creds = auth.load_credentials(config, "a")
    assert creds.token == "old-token"


def test_past_expiry_triggers_proactive_refresh(tmp_path, monkeypatch):
    config = _config(tmp_path)
    past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)).isoformat()
    token_path = _write_token(config, "a", expiry=past)

    def fake_refresh(self, request):
        self.token = "new-token"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", fake_refresh)
    creds = auth.load_credentials(config, "a")
    assert creds.token == "new-token"
    # Persisted back to the file too.
    saved = json.loads(token_path.read_text())
    assert saved["token"] == "new-token"
    assert "expiry" in saved


def test_future_expiry_does_not_refresh(tmp_path, monkeypatch):
    config = _config(tmp_path)
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)).isoformat()
    _write_token(config, "a", expiry=future)

    def boom_refresh(self, request):
        raise AssertionError("refresh should not have been called")

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", boom_refresh)
    creds = auth.load_credentials(config, "a")
    assert creds.token == "old-token"


def test_unwritable_token_after_refresh_warns_not_errors(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)).isoformat()
    _write_token(config, "a", expiry=past)

    def fake_refresh(self, request):
        self.token = "new-token"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", fake_refresh)

    def boom_write(path, data):
        raise OSError("Permission denied")

    monkeypatch.setattr(auth, "_write_token_atomically", boom_write)

    creds = auth.load_credentials(config, "a")  # must NOT raise
    assert creds.token == "new-token"
    assert "could not persist" in capsys.readouterr().err.lower()


def test_malformed_expiry_falls_back_to_none(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _write_token(config, "a", expiry="not-a-date")

    def boom_refresh(self, request):
        raise AssertionError("refresh should not have been called")

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", boom_refresh)
    creds = auth.load_credentials(config, "a")  # must not crash
    assert creds.token == "old-token"


def test_write_token_atomically_creates_0600_from_the_start(tmp_path):
    path = tmp_path / "sub" / "token.json"
    auth._write_token_atomically(path, {"token": "x"})
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    assert json.loads(path.read_text()) == {"token": "x"}


def test_write_token_atomically_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "token.json"
    auth._write_token_atomically(path, {"token": "x"})
    leftovers = [p for p in tmp_path.iterdir() if p.name != "token.json"]
    assert leftovers == []
