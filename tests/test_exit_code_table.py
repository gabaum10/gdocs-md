"""Walks every code in AGENTS.md's exit-code table against a real
`cli.main()` call, so the table and the code can't silently drift apart
(item 3 of the fix round: "AGENTS.md's table matches the code exactly").
This complements, not replaces, the more detailed scenario tests in
test_exit_paths.py and test_cli.py -- one test per documented code here,
each pointing at where the fuller coverage lives.
"""

import json

from gdocs_md import cli, exit_codes
from gdocs_md.docs_api import check_pandoc
from gdocs_md.errors import MissingPandocError
from gdocs_md.smart_update import smart_update_doc


class _HttpErrorRaisingService:
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


def test_0_ok(tmp_path, capsys):
    assert cli.main(["--credentials-dir", str(tmp_path), "list"]) == exit_codes.OK


def test_2_usage_missing_subcommand():
    assert cli.main([]) == exit_codes.USAGE


def test_2_usage_bad_argument(tmp_path):
    assert (
        cli.main(["--credentials-dir", str(tmp_path), "update", "doc-x", "--account", "a"])
        == exit_codes.USAGE
    )


def test_2_usage_bare_auth():
    assert cli.main(["auth"]) == exit_codes.USAGE


def test_2_usage_argparse_parse_error():
    assert cli.main(["not-a-command"]) == exit_codes.USAGE


def test_3_auth_or_config_no_account(tmp_path):
    assert (
        cli.main(["--credentials-dir", str(tmp_path), "get", "doc-x"]) == exit_codes.AUTH_OR_CONFIG
    )


def test_3_auth_or_config_http_401(tmp_path, monkeypatch):
    from gdocs_md import commands

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(_http_error(401)))
    assert (
        cli.main(["--credentials-dir", str(tmp_path), "tabs", "doc-x", "--account", "a"])
        == exit_codes.AUTH_OR_CONFIG
    )


def test_4_not_found_missing_file(tmp_path):
    assert (
        cli.main(["--credentials-dir", str(tmp_path), "create", str(tmp_path / "nope.md"), "--account", "a"])
        == exit_codes.NOT_FOUND_OR_PERMISSION
    )


def test_4_not_found_http_404(tmp_path, monkeypatch):
    from gdocs_md import commands

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(_http_error(404)))
    assert (
        cli.main(["--credentials-dir", str(tmp_path), "tabs", "doc-x", "--account", "a"])
        == exit_codes.NOT_FOUND_OR_PERMISSION
    )


def test_5_unrepresentable_diff():
    class MultiTab:
        def documents(self):
            return self

        def get(self, **_kw):
            return self

        def execute(self):
            return {
                "tabs": [
                    {"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": {"content": []}}},
                    {"tabProperties": {"tabId": "t.1"}, "documentTab": {"body": {"content": []}}},
                ]
            }

    try:
        smart_update_doc(MultiTab(), "doc-x", "hello\n")
        raised = None
    except Exception as e:
        raised = e
    assert raised is not None and raised.exit_code == exit_codes.UNREPRESENTABLE_DIFF


def test_6_destructive_refused(tmp_path, monkeypatch):
    from gdocs_md import commands
    from gdocs_md.config import resolve_config

    class MultiTab:
        def documents(self):
            return self

        def get(self, **_kw):
            return self

        def execute(self):
            return {
                "tabs": [
                    {"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": {"content": []}}},
                    {"tabProperties": {"tabId": "t.1"}, "documentTab": {"body": {"content": []}}},
                ]
            }

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: MultiTab())
    md = tmp_path / "notes.md"
    md.write_text("hello\n")

    class Args:
        account = "a"
        doc_id = "doc-x"
        file = str(md)
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
    try:
        commands.cmd_update(Args(), config)
        raised = None
    except Exception as e:
        raised = e
    assert raised is not None and raised.exit_code == exit_codes.DESTRUCTIVE_REFUSED


def test_7_missing_pandoc(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    try:
        check_pandoc()
        raised = None
    except MissingPandocError as e:
        raised = e
    assert raised is not None and raised.exit_code == exit_codes.MISSING_PANDOC


def test_8_api_error_other_http_status(tmp_path, monkeypatch):
    from gdocs_md import commands

    monkeypatch.setattr(commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(_http_error(500)))
    assert (
        cli.main(["--credentials-dir", str(tmp_path), "tabs", "doc-x", "--account", "a"])
        == exit_codes.API_ERROR
    )


def test_8_api_error_unhandled_exception_last_resort(tmp_path, monkeypatch):
    from gdocs_md import commands

    monkeypatch.setattr(
        commands.docs_api, "get_docs_service", lambda c, a: _HttpErrorRaisingService(KeyError("boom"))
    )
    code = cli.main(["--credentials-dir", str(tmp_path), "tabs", "doc-x", "--account", "a", "--json"])
    assert code == exit_codes.API_ERROR
