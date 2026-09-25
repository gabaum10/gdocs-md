"""The document registry: a JSON file mapping local markdown file paths to
the Google Doc they were created from (doc_id, url, title, timestamps).

The registry's location is configuration (Config.registry_file), not a
hardcoded path -- see config.py. It is one shared file, not split per
account: `create`/`update` record whichever account created the doc, but
don't namespace the registry by account. That's a known simplification
(AGENTS.md), not a bug this build fixes.

Writes are atomic (temp file + os.replace) under an exclusive lock, and
merge with whatever is currently on disk rather than blindly overwriting
-- see `save_registry`'s docstring for why both of those matter for
concurrent `create`/`update` calls, which is the normal case for multiple
agents sharing one registry.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path

from .errors import AuthOrConfigError

try:
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # Windows has no fcntl
    fcntl = None  # type: ignore[assignment]
    _HAVE_FCNTL = False


@contextlib.contextmanager
def _locked(registry_file: Path, timeout: float = 10.0):
    """Exclusive advisory lock, held for the duration of the `with` block,
    on a sidecar `<registry_file>.lock` file (never the registry file
    itself, so a lock attempt never has to worry about the target not
    existing yet).

    POSIX (fcntl available): `flock` -- the kernel releases it
    automatically if the holding process dies, so a crash mid-write never
    leaves a stuck lock.

    Documented fallback (no fcntl, e.g. Windows): an atomic-create
    lockfile (`O_CREAT | O_EXCL`) with a retry-with-backoff loop and a
    stale-lock timeout. This is less safe than flock -- a crash while
    holding the lock leaves it stuck until another writer's timeout
    elapses and breaks it -- but it needs no extra dependency and is
    functional for the single-host, cooperating-processes case this tool
    targets.
    """
    lock_path = Path(str(registry_file) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if _HAVE_FCNTL:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return

    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            if time.time() > deadline:
                with contextlib.suppress(OSError):
                    os.unlink(lock_path)
                continue
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(lock_path)


def load_registry(registry_file: Path) -> dict:
    if not registry_file.exists():
        return {}
    try:
        with open(registry_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AuthOrConfigError(f"Failed to read registry '{registry_file}': {e}") from e


def save_registry(registry_file: Path, registry: dict, merge: bool = True) -> None:
    """Save the document registry.

    Writes atomically (temp file + `os.replace`) under an exclusive lock,
    so a concurrent `load_registry` never sees a torn/partial file, and a
    crash mid-write never corrupts the registry -- the old file stays
    valid until the new one is fully written and renamed into place in one
    filesystem operation.

    `merge=True` (the default): under the same lock, re-read whatever is
    CURRENTLY on disk and overlay `registry` on top of it key by key,
    instead of blindly replacing the whole file with the caller's
    snapshot. This is what makes two concurrent `create`/`update` calls,
    each starting from its own slightly-stale `load_registry` snapshot,
    both survive -- without it, the second writer's save silently erases
    whatever the first one added, because it never saw it. Pass
    merge=False for a deliberate full replace (e.g. rewriting the whole
    registry on purpose, such as a future prune/gc command).
    """
    registry_file = Path(registry_file)
    try:
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        with _locked(registry_file):
            if merge and registry_file.exists():
                try:
                    with open(registry_file, "r", encoding="utf-8") as f:
                        on_disk = json.load(f)
                    if not isinstance(on_disk, dict):
                        on_disk = {}
                except (OSError, json.JSONDecodeError):
                    on_disk = {}
                merged = dict(on_disk)
                merged.update(registry)
            else:
                merged = registry

            fd, tmp_path = tempfile.mkstemp(
                dir=str(registry_file.parent), prefix=".registry-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(merged, f, indent=2)
                os.replace(tmp_path, registry_file)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
    except OSError as e:
        raise AuthOrConfigError(f"Failed to save registry '{registry_file}': {e}") from e


def find_by_doc_id(registry: dict, doc_id: str) -> tuple[str | None, dict | None]:
    for key, entry in registry.items():
        if entry.get("doc_id") == doc_id:
            return key, entry
    return None, None
