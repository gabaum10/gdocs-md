"""Config resolution precedence: CLI flag > env var > config file > default."""

import json
from pathlib import Path

import pytest

from gdocs_md.config import ENV_ACCOUNT, ENV_CREDENTIALS_DIR, resolve_account, resolve_config
from gdocs_md.errors import AuthOrConfigError


class Args:
    """Minimal stand-in for argparse.Namespace."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_default_credentials_dir_is_xdg_based(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv(ENV_CREDENTIALS_DIR, raising=False)
    args = Args(config_file=None, credentials_dir=None, oauth_client_file=None, registry_file=None, account=None)
    config = resolve_config(args)
    assert config.credentials_dir == tmp_path / "gdocs-md" / "credentials"


def test_env_var_overrides_default(tmp_path, monkeypatch):
    custom = tmp_path / "custom-creds"
    monkeypatch.setenv(ENV_CREDENTIALS_DIR, str(custom))
    args = Args(config_file=None, credentials_dir=None, oauth_client_file=None, registry_file=None, account=None)
    config = resolve_config(args)
    assert config.credentials_dir == custom


def test_cli_flag_overrides_env_var(tmp_path, monkeypatch):
    env_dir = tmp_path / "env-creds"
    cli_dir = tmp_path / "cli-creds"
    monkeypatch.setenv(ENV_CREDENTIALS_DIR, str(env_dir))
    args = Args(config_file=None, credentials_dir=str(cli_dir), oauth_client_file=None, registry_file=None, account=None)
    config = resolve_config(args)
    assert config.credentials_dir == cli_dir


def test_config_file_overrides_default_but_not_env_or_cli(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_CREDENTIALS_DIR, raising=False)
    config_file = tmp_path / "config.json"
    file_creds_dir = tmp_path / "file-creds"
    config_file.write_text(json.dumps({"credentials_dir": str(file_creds_dir)}))

    args = Args(config_file=str(config_file), credentials_dir=None, oauth_client_file=None, registry_file=None, account=None)
    config = resolve_config(args)
    assert config.credentials_dir == file_creds_dir

    # env var still beats the config file
    monkeypatch.setenv(ENV_CREDENTIALS_DIR, str(tmp_path / "env-wins"))
    config2 = resolve_config(args)
    assert config2.credentials_dir == tmp_path / "env-wins"


def test_oauth_client_and_registry_default_under_credentials_dir(tmp_path):
    args = Args(config_file=None, credentials_dir=str(tmp_path), oauth_client_file=None, registry_file=None, account=None)
    config = resolve_config(args)
    assert config.oauth_client_file == tmp_path / "oauth-client.json"
    assert config.registry_file == tmp_path / "registry.json"


def test_no_account_name_is_baked_in(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_ACCOUNT, raising=False)
    args = Args(config_file=None, credentials_dir=str(tmp_path), oauth_client_file=None, registry_file=None, account=None)
    config = resolve_config(args)
    assert config.default_account is None
    with pytest.raises(AuthOrConfigError):
        resolve_account(None, config)


def test_cli_account_wins_over_config_default(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"default_account": "from-file"}))
    args = Args(config_file=str(config_file), credentials_dir=None, oauth_client_file=None, registry_file=None, account=None)
    config = resolve_config(args)
    assert resolve_account("from-cli", config) == "from-cli"
    assert resolve_account(None, config) == "from-file"


def test_malformed_config_file_errors_loudly(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("not json")
    args = Args(config_file=str(config_file), credentials_dir=None, oauth_client_file=None, registry_file=None, account=None)
    with pytest.raises(AuthOrConfigError):
        resolve_config(args)


def test_token_path_is_under_credentials_dir(tmp_path):
    args = Args(config_file=None, credentials_dir=str(tmp_path), oauth_client_file=None, registry_file=None, account=None)
    config = resolve_config(args)
    assert config.token_path("personal") == tmp_path / "personal" / "token.json"


def test_unknown_config_key_warns(tmp_path, capsys):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"credential_dir": "/typo"}))  # missing the 's'
    args = Args(config_file=str(config_file), credentials_dir=None, oauth_client_file=None, registry_file=None, account=None)
    config = resolve_config(args)
    err = capsys.readouterr().err
    assert "credential_dir" in err
    assert "unrecognized" in err.lower()
    # And it fell back to the default rather than silently adopting the typo.
    assert config.credentials_dir != Path("/typo")


def test_known_config_keys_do_not_warn(tmp_path, capsys):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"default_account": "x", "credentials_dir": str(tmp_path)}))
    args = Args(config_file=str(config_file), credentials_dir=None, oauth_client_file=None, registry_file=None, account=None)
    resolve_config(args)
    assert capsys.readouterr().err == ""
