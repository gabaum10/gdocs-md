"""The document registry: a JSON file mapping local markdown file paths to
the Google Doc they were created from (doc_id, url, title, timestamps).

The registry's location is configuration (Config.registry_file), not a
hardcoded path -- see config.py. It is one shared file, not split per
account: `create`/`update` record whichever account created the doc, but
don't namespace the registry by account. That's a known simplification
(AGENTS.md), not a bug this build fixes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import AuthOrConfigError


def load_registry(registry_file: Path) -> dict:
    if not registry_file.exists():
        return {}
    try:
        with open(registry_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AuthOrConfigError(f"Failed to read registry '{registry_file}': {e}") from e


def save_registry(registry_file: Path, registry: dict) -> None:
    try:
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        with open(registry_file, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)
    except OSError as e:
        raise AuthOrConfigError(f"Failed to save registry '{registry_file}': {e}") from e


def find_by_doc_id(registry: dict, doc_id: str) -> tuple[str | None, dict | None]:
    for key, entry in registry.items():
        if entry.get("doc_id") == doc_id:
            return key, entry
    return None, None
