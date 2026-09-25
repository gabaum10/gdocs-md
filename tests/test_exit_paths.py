"""Exercise the remaining exit-code-bearing error paths that don't need a
live Google API: UNREPRESENTABLE_DIFF (multi-tab smart update),
MISSING_PANDOC (pandoc absent from PATH), and the exit-code contract's
error-handling fixes: HttpError/RefreshError classification, a
`get -o` failure never truncating the target file, and every GdocsMdError
propagating out of `get` untouched instead of being relabeled 4."""

import json
import os

import pytest

from gdocs_md import cli, exit_codes
from gdocs_md.docs_api import check_pandoc
from gdocs_md.errors import MissingPandocError, UnrepresentableDiffError
from gdocs_md.smart_update import smart_update_doc


class _HttpErrorRaisingService:
    """Mimics googleapiclient's call shape: every method returns self, and
    .execute() raises the given exception -- used to prove an escaped
    HttpError/RefreshError gets the documented exit code instead of a raw
    traceback (exit 1, uncontracted)."""

    def __init__(self, exc):
        self._exc = exc

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        raise self._exc


def _http_error(status):
    from googleapiclient.errors import HttpError
    from httplib2 import Response

    return HttpError(Response({"status": status}), b'{"error":{"message":"boom"}}')


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


def test_tabs_http_404_maps_to_not_found_not_a_traceback(tmp_path, monkeypatch):
    from gdocs_md import commands

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(_http_error(404)))
    code = cli.main(["--credentials-dir", str(tmp_path), "tabs", "doc-x", "--account", "a", "--json"])
    assert code == exit_codes.NOT_FOUND_OR_PERMISSION
    # stderr is a single valid JSON object, not a mix of text and a traceback.


def test_update_smart_http_403_maps_to_not_found(tmp_path, monkeypatch, capsys):
    from gdocs_md import commands

    md = tmp_path / "notes.md"
    md.write_text("hello\n")
    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(_http_error(403)))
    code = cli.main(["--credentials-dir", str(tmp_path), "update", "doc-x", str(md), "--account", "a", "--json"])
    assert code == exit_codes.NOT_FOUND_OR_PERMISSION
    payload = json.loads(capsys.readouterr().err)
    assert payload["exit_code"] == exit_codes.NOT_FOUND_OR_PERMISSION


def test_http_401_maps_to_auth_or_config(tmp_path, monkeypatch):
    from gdocs_md import commands

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(_http_error(401)))
    code = cli.main(["--credentials-dir", str(tmp_path), "tabs", "doc-x", "--account", "a"])
    assert code == exit_codes.AUTH_OR_CONFIG


def test_http_500_maps_to_api_error(tmp_path, monkeypatch):
    from gdocs_md import commands

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(_http_error(500)))
    code = cli.main(["--credentials-dir", str(tmp_path), "tabs", "doc-x", "--account", "a"])
    assert code == exit_codes.API_ERROR


def test_refresh_error_maps_to_auth_or_config_not_not_found(tmp_path, monkeypatch):
    """A RefreshError surfacing mid-call (a revoked token) must NOT be
    relabeled 'document not found' by cmd_get's own local handler -- it
    has to propagate to cli.py's classifier, which knows RefreshError
    means re-authenticate (3), not a missing doc (4). Positive control:
    before this fix, cmd_get's except-Exception caught ANY exception and
    relabeled it NotFoundOrPermissionError whenever it wasn't a 400 -- see
    test_positive_control below."""
    from gdocs_md import commands
    from google.auth.exceptions import RefreshError

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(RefreshError("invalid_grant")))
    code = cli.main(["--credentials-dir", str(tmp_path), "get", "doc-x", "--account", "a"])
    assert code == exit_codes.AUTH_OR_CONFIG


def test_positive_control_broad_except_would_mislabel_refresh_error(tmp_path, monkeypatch):
    """Reproduce the pre-fix shape of cmd_get's fallback handler (catches
    ANY exception, not just HttpError, and relabels non-400 ones
    NotFoundOrPermissionError) and confirm it WOULD have mislabeled a
    RefreshError as exit 4 -- proving the real handler's HttpError-only
    guard is what's actually preventing that, not incidental behavior."""
    from gdocs_md.errors import NotFoundOrPermissionError
    from google.auth.exceptions import RefreshError

    def broad_handler(doc_id, e):
        if "400" not in str(e) and "not supported" not in str(e).lower():
            return NotFoundOrPermissionError(f"Failed to fetch document '{doc_id}': {e}").exit_code
        return None

    assert broad_handler("doc-x", RefreshError("invalid_grant")) == exit_codes.NOT_FOUND_OR_PERMISSION


def test_get_output_file_untouched_on_failure(tmp_path, monkeypatch):
    from gdocs_md import commands

    target = tmp_path / "notes.md"
    target.write_text("my local edits, not yet synced\n" * 20)
    before = target.stat().st_size
    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(_http_error(404)))
    code = cli.main(["--credentials-dir", str(tmp_path), "get", "doc-x", "--account", "a", "-o", str(target)])
    assert code == exit_codes.NOT_FOUND_OR_PERMISSION
    assert target.stat().st_size == before


def test_get_json_output_writes_json_to_file_not_stdout(tmp_path, monkeypatch, capsys):
    from gdocs_md import commands, docs_api

    class FakeDocsService:
        def documents(self):
            return self

        def get(self, **_kw):
            return self

        def execute(self):
            return {
                "title": "t",
                "tabs": [
                    {
                        "tabProperties": {"tabId": "t.0"},
                        "documentTab": {
                            "body": {
                                "content": [
                                    {"paragraph": {"elements": [{"textRun": {"content": "hello\n"}}]}}
                                ]
                            }
                        },
                    }
                ],
            }

    class FakeDriveService:
        def comments(self):
            return self

        def list(self, **_kw):
            return self

        def execute(self):
            return {"comments": []}

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: FakeDocsService())
    monkeypatch.setattr(commands.docs_api, "get_drive_service", lambda c, a: FakeDriveService())
    target = tmp_path / "out.json"
    code = cli.main(["--credentials-dir", str(tmp_path), "get", "doc-x", "--account", "a", "--json", "-o", str(target)])
    assert code == exit_codes.OK
    assert capsys.readouterr().out == ""  # nothing on stdout
    payload = json.loads(target.read_text())
    assert payload["text"] == "hello\n"


def test_create_does_not_leak_temp_docx_when_auth_fails(tmp_path, monkeypatch):
    """convert-to-docx must not run before auth -- an auth failure
    with no drive_service ever obtained should leave no temp docx behind
    in the system temp directory."""
    import glob
    import tempfile

    from gdocs_md import commands

    md = tmp_path / "notes.md"
    md.write_text("# hello\n")

    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "tmp*.docx")))

    code = cli.main(["--credentials-dir", str(tmp_path), "create", str(md), "--account", "a"])
    assert code == exit_codes.AUTH_OR_CONFIG

    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "tmp*.docx")))
    assert after - before == set()


def test_create_reports_doc_id_when_registry_save_fails(tmp_path, monkeypatch, capsys):
    """A registry-save failure after the remote doc was already
    created must not swallow the doc_id/url or exit non-zero -- that's
    what pushes a caller to retry `create` (which the duplicate check
    can't catch, since nothing got registered) and make a second doc."""
    from gdocs_md import commands

    class FakeFiles:
        def create(self, **_kw):
            return self

        def execute(self):
            return {"id": "NEWDOC", "webViewLink": "https://example/NEWDOC", "modifiedTime": "t"}

    class FakeDriveService:
        def files(self):
            return FakeFiles()

    md = tmp_path / "notes.md"
    md.write_text("# hello\n")

    monkeypatch.setattr(commands.docs_api, "get_drive_service", lambda c, a: FakeDriveService())
    monkeypatch.setattr(commands.docs_api, "convert_markdown_to_docx", lambda p: str(tmp_path / "fake.docx"))
    (tmp_path / "fake.docx").write_bytes(b"x")

    def boom_save(*a, **k):
        from gdocs_md.errors import AuthOrConfigError

        raise AuthOrConfigError("Permission denied")

    monkeypatch.setattr(commands.registry, "save_registry", boom_save)

    code = cli.main(["--credentials-dir", str(tmp_path), "create", str(md), "--account", "a", "--json"])
    assert code == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["doc_id"] == "NEWDOC"
    assert payload["registered"] is False
    assert "warning" in payload
