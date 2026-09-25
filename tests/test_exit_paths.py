"""Exercise the remaining exit-code-bearing error paths that don't need a
live Google API: UNREPRESENTABLE_DIFF (multi-tab smart update) and
MISSING_PANDOC (pandoc absent from PATH)."""

import pytest

from gdocs_md import exit_codes
from gdocs_md.docs_api import check_pandoc
from gdocs_md.errors import MissingPandocError, UnrepresentableDiffError
from gdocs_md.smart_update import smart_update_doc


class _MultiTabService:
    def documents(self):
        return self

    def get(self, **_kwargs):
        return self

    def execute(self):
        return {
            "tabs": [
                {"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": {"content": []}}},
                {"tabProperties": {"tabId": "t.1"}, "documentTab": {"body": {"content": []}}},
            ]
        }


def test_multi_tab_smart_update_raises_unrepresentable_diff():
    with pytest.raises(UnrepresentableDiffError) as exc:
        smart_update_doc(_MultiTabService(), "doc-x", "hello\n")
    assert exc.value.exit_code == exit_codes.UNREPRESENTABLE_DIFF
    assert "t.0" in str(exc.value) and "t.1" in str(exc.value)


def test_missing_pandoc_raises_with_documented_exit_code(monkeypatch):
    import subprocess

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("no pandoc")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(MissingPandocError) as exc:
        check_pandoc()
    assert exc.value.exit_code == exit_codes.MISSING_PANDOC


def test_replace_all_multi_tab_without_force_raises_destructive_refused(tmp_path, monkeypatch):
    from gdocs_md import commands
    from gdocs_md.config import resolve_config
    from gdocs_md.errors import DestructiveRefusedError

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda config, account: _MultiTabService())

    md_file = tmp_path / "notes.md"
    md_file.write_text("hello\n")

    class Args:
        account = "x"
        doc_id = "doc-x"
        file = str(md_file)
        tab = None
        input = None
        replace_all = True
        force = False
        dry_run = False
        json = False

    class ConfigArgs:
        config_file = None
        credentials_dir = str(tmp_path)
        oauth_client_file = None
        registry_file = None
        account = None

    config = resolve_config(ConfigArgs())
    with pytest.raises(DestructiveRefusedError) as exc:
        commands.cmd_update(Args(), config)
    assert exc.value.exit_code == exit_codes.DESTRUCTIVE_REFUSED
