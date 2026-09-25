"""CLI-level tests: exit codes and --json shapes, without any network
access. Every path exercised here fails (or succeeds) before ever touching
the Google API -- account/config resolution, or the registry, which is
local-only."""

import json

from gdocs_md import cli, exit_codes


def test_no_subcommand_exits_usage(capsys):
    code = cli.main([])
    assert code == exit_codes.USAGE


def test_unknown_subcommand_exits_usage_gracefully(capsys):
    # argparse errors route through UsageError now (cli._ArgumentParser),
    # not a raw SystemExit -- main() returns the code like any other error
    # path, and the message is on stderr as plain text (or JSON under
    # --json; see test_argparse_error_is_json_under_json_flag).
    code = cli.main(["not-a-real-command"])
    assert code == exit_codes.USAGE
    assert "invalid choice" in capsys.readouterr().err


def test_argparse_error_is_json_under_json_flag(capsys):
    code = cli.main(["not-a-real-command", "--json"])
    assert code == exit_codes.USAGE
    payload = json.loads(capsys.readouterr().err)
    assert payload["exit_code"] == exit_codes.USAGE


def test_bare_auth_exits_usage(capsys):
    code = cli.main(["auth"])
    assert code == exit_codes.USAGE
    assert "subcommand" in capsys.readouterr().err.lower()


def test_global_flags_work_before_subcommand(tmp_path, capsys):
    code = cli.main(["--credentials-dir", str(tmp_path), "--account", "x", "list", "--json"])
    assert code == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"registry": {}}


def test_global_flags_work_after_subcommand(tmp_path, capsys):
    code = cli.main(["list", "--credentials-dir", str(tmp_path), "--account", "x", "--json"])
    assert code == exit_codes.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"registry": {}}


def test_before_subcommand_flag_wins_when_not_repeated_after(tmp_path, capsys):
    # --account before the subcommand must survive even though the `get`
    # subparser ALSO declares --account (for the after-subcommand form) --
    # its own unset default must not clobber the value the top-level
    # parser already captured. Missing account would be exit 3; this
    # instead fails later, on the missing doc id, proving --account X did
    # take effect.
    code = cli.main(["--account", "x", "--credentials-dir", str(tmp_path), "get"])
    assert code == exit_codes.USAGE
    assert "No account specified" not in capsys.readouterr().err


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


def test_update_missing_file_arg_exits_usage_not_auth_or_config(tmp_path):
    # `update <doc-id>` with no <file-path> and no --tab is a usage error
    # (a missing required argument), not an auth/config one -- this is the
    # exact drift a prior review found: it used to exit 3. Named for what it
    # actually checks (the previous name claimed to cover the multi-tab
    # --force guard, which this can't reach without a network call at all).
    code = cli.main(["--credentials-dir", str(tmp_path), "update", "doc-id", "--account", "x"])
    assert code == exit_codes.USAGE
