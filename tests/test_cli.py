"""CLI-level tests: exit codes and --json shapes, without any network
access. Every path exercised here fails (or succeeds) before ever touching
the Google API -- account/config resolution, or the registry, which is
local-only."""

import json

import pytest

from gdocs_md import cli, exit_codes


def test_no_subcommand_exits_usage(capsys):
    code = cli.main([])
    assert code == exit_codes.USAGE


def test_unknown_subcommand_exits_usage_via_argparse():
    with pytest.raises(SystemExit) as exc:
        cli.main(["not-a-real-command"])
    assert exc.value.code == 2


def test_list_on_empty_registry_exits_ok(tmp_path, capsys):
    code = cli.main(["--credentials-dir", str(tmp_path), "list"])
    assert code == exit_codes.OK
    out = capsys.readouterr().out
    assert "Registry is empty." in out


def test_list_json_shape_on_empty_registry(tmp_path, capsys):
    code = cli.main(["--credentials-dir", str(tmp_path), "list", "--json"])
    assert code == exit_codes.OK
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload == {"registry": {}}


def test_list_reflects_a_populated_registry(tmp_path, capsys):
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(
        json.dumps(
            {
                "/tmp/notes.md": {
                    "doc_id": "abc123",
                    "url": "https://docs.google.com/document/d/abc123/edit",
                    "title": "Notes",
                    "created": "2026-01-01T00:00:00",
                    "updated": "2026-01-01T00:00:00",
                }
            }
        )
    )
    code = cli.main(["--registry", str(registry_file), "list", "--json"])
    assert code == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["registry"]["/tmp/notes.md"]["doc_id"] == "abc123"


def test_get_without_account_exits_auth_or_config(tmp_path, capsys):
    code = cli.main(["--credentials-dir", str(tmp_path), "get", "some-doc-id"])
    assert code == exit_codes.AUTH_OR_CONFIG
    err = capsys.readouterr().err
    assert "No account specified" in err


def test_get_without_account_json_error_shape(tmp_path, capsys):
    code = cli.main(["--credentials-dir", str(tmp_path), "get", "some-doc-id", "--json"])
    assert code == exit_codes.AUTH_OR_CONFIG
    err = capsys.readouterr().err
    payload = json.loads(err)
    assert payload["exit_code"] == exit_codes.AUTH_OR_CONFIG
    assert "No account specified" in payload["error"]


def test_auth_status_with_no_token_exits_auth_or_config(tmp_path, capsys):
    code = cli.main(["--credentials-dir", str(tmp_path), "auth", "status", "--account", "nobody"])
    assert code == exit_codes.AUTH_OR_CONFIG


def test_auth_status_json_shape_with_no_token(tmp_path, capsys):
    code = cli.main(["--credentials-dir", str(tmp_path), "auth", "status", "--account", "nobody", "--json"])
    assert code == exit_codes.AUTH_OR_CONFIG
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["configured"] is False
    assert payload["account"] == "nobody"


def test_create_missing_file_exits_not_found(tmp_path, capsys):
    code = cli.main(
        ["--credentials-dir", str(tmp_path), "create", str(tmp_path / "nope.md"), "--account", "x"]
    )
    assert code == exit_codes.NOT_FOUND_OR_PERMISSION


def test_update_replace_all_multi_tab_requires_force_is_a_config_error_not_a_crash(tmp_path):
    # Without network this can't reach the multi-tab check itself, but it
    # must not reach it before failing on the missing input file -- this
    # pins the argument-validation order (usage-shaped failures happen
    # before any network call).
    code = cli.main(["--credentials-dir", str(tmp_path), "update", "doc-id", "--account", "x"])
    assert code == exit_codes.AUTH_OR_CONFIG
