"""The document registry: local JSON read/write, no network."""

import pytest

from gdocs_md.errors import AuthOrConfigError
from gdocs_md.registry import find_by_doc_id, load_registry, save_registry


def test_load_missing_registry_is_empty(tmp_path):
    assert load_registry(tmp_path / "registry.json") == {}


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "sub" / "registry.json"
    data = {"/tmp/a.md": {"doc_id": "d1", "url": "u", "title": "t", "created": "c", "updated": "u2"}}
    save_registry(path, data)
    assert load_registry(path) == data


def test_load_malformed_registry_errors_loudly(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("not json")
    with pytest.raises(AuthOrConfigError):
        load_registry(path)


def test_find_by_doc_id():
    registry = {
        "/tmp/a.md": {"doc_id": "d1"},
        "/tmp/b.md": {"doc_id": "d2"},
    }
    key, entry = find_by_doc_id(registry, "d2")
    assert key == "/tmp/b.md"
    assert entry["doc_id"] == "d2"

    key, entry = find_by_doc_id(registry, "missing")
    assert key is None
    assert entry is None
