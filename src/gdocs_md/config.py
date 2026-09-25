"""Configuration resolution.

Precedence, highest wins, for every setting below:

    1. CLI flag (e.g. --credentials-dir)
    2. Environment variable (e.g. GDOCS_MD_CREDENTIALS_DIR)
    3. Config file value (JSON, see `find_config_file`)
    4. Built-in default

The config file itself is located the same way (CLI flag > env var >
default), except its own default location follows the XDG Base Directory
spec: ``$XDG_CONFIG_HOME/gdocs-md/config.json``, falling back to
``~/.config/gdocs-md/config.json`` when XDG_CONFIG_HOME is unset.

Nothing here bakes in an account name, a credentials path belonging to any
particular person, or a registry location -- a fresh install has none of
those until the user runs ``gdocs-md auth login`` or writes a config file.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import AuthOrConfigError

ENV_CONFIG_FILE = "GDOCS_MD_CONFIG"
ENV_CREDENTIALS_DIR = "GDOCS_MD_CREDENTIALS_DIR"
ENV_OAUTH_CLIENT = "GDOCS_MD_OAUTH_CLIENT"
ENV_REGISTRY = "GDOCS_MD_REGISTRY"
ENV_ACCOUNT = "GDOCS_MD_ACCOUNT"

_CONFIG_KEYS = {
    "credentials_dir": ENV_CREDENTIALS_DIR,
    "oauth_client_file": ENV_OAUTH_CLIENT,
    "registry_file": ENV_REGISTRY,
    "default_account": ENV_ACCOUNT,
}


def _xdg_config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".config"


def default_config_file_path() -> Path:
    return _xdg_config_home() / "gdocs-md" / "config.json"


def default_credentials_dir() -> Path:
    return _xdg_config_home() / "gdocs-md" / "credentials"


def find_config_file(cli_value: str | None) -> Path | None:
    """Return the config file path to read (which may not exist), applying
    CLI-flag > env-var > default precedence to the *location* itself."""
    if cli_value:
        return Path(cli_value).expanduser()
    env_value = os.environ.get(ENV_CONFIG_FILE)
    if env_value:
        return Path(env_value).expanduser()
    return default_config_file_path()


def load_config_file(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AuthOrConfigError(f"Failed to read config file '{path}': {e}") from e
    if not isinstance(data, dict):
        raise AuthOrConfigError(f"Config file '{path}' must contain a JSON object.")
    return data


@dataclass
class Config:
    """Resolved configuration. All paths are absolute, expanded."""

    config_file: Path | None
    credentials_dir: Path
    oauth_client_file: Path
    registry_file: Path
    default_account: str | None

    def token_path(self, account: str) -> Path:
        return self.credentials_dir / account / "token.json"


def resolve_config(cli_args=None) -> Config:
    """Resolve the effective configuration from CLI args, env vars, an
    optional config file, and built-in defaults.

    `cli_args` is an argparse.Namespace (or anything with matching
    attributes/None) carrying the top-level --config-file, --credentials-dir,
    --oauth-client, --registry, --account flags. Any of these may be absent
    or None, in which case env var / config file / default apply.
    """
    cli_args = cli_args or {}

    def cli(name):
        if isinstance(cli_args, dict):
            return cli_args.get(name)
        return getattr(cli_args, name, None)

    config_file_path = find_config_file(cli("config_file"))
    file_data = load_config_file(config_file_path)

    unknown_keys = set(file_data) - set(_CONFIG_KEYS)
    if unknown_keys:
        # A typo'd key (credential_dir instead of credentials_dir) would
        # otherwise silently fall back to the default with no signal at
        # all -- W18.
        print(
            f"[Warning] {config_file_path}: unrecognized config key(s): "
            f"{', '.join(sorted(unknown_keys))} (ignored; known keys: "
            f"{', '.join(sorted(_CONFIG_KEYS))})",
            file=sys.stderr,
        )

    def resolved(key, default):
        cli_value = cli(key)
        if cli_value:
            return cli_value
        env_value = os.environ.get(_CONFIG_KEYS[key])
        if env_value:
            return env_value
        file_value = file_data.get(key)
        if file_value:
            return file_value
        return default

    credentials_dir = Path(
        resolved("credentials_dir", str(default_credentials_dir()))
    ).expanduser()

    oauth_client_default = str(credentials_dir / "oauth-client.json")
    oauth_client_file = Path(
        resolved("oauth_client_file", oauth_client_default)
    ).expanduser()

    registry_default = str(credentials_dir / "registry.json")
    registry_file = Path(resolved("registry_file", registry_default)).expanduser()

    default_account = resolved("default_account", None)

    return Config(
        config_file=config_file_path,
        credentials_dir=credentials_dir,
        oauth_client_file=oauth_client_file,
        registry_file=registry_file,
        default_account=default_account,
    )


def resolve_account(cli_account: str | None, config: Config) -> str:
    """Pick the account to operate as: --account flag, else config's
    default_account (itself CLI/env/file resolved). Errors loudly if
    neither is set -- there is no baked-in account name to fall back to."""
    if cli_account:
        return cli_account
    if config.default_account:
        return config.default_account
    raise AuthOrConfigError(
        "No account specified. Pass --account <name>, set "
        f"{ENV_ACCOUNT}, or set \"default_account\" in "
        f"{config.config_file or default_config_file_path()}. "
        "Run 'gdocs-md auth login --account <name>' first if you haven't "
        "authenticated yet."
    )
