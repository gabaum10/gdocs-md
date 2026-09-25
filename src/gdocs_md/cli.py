"""Argument parsing and top-level error -> exit-code translation.

Every raised GdocsMdError carries its own exit code (errors.py). This is
the one place that catches errors -- ours and the ones the Google client
libraries raise directly -- prints the message (plain text, or JSON on
stderr under --json), and turns it into an exit code. See AGENTS.md's
exit-code table for the documented contract; `test_exit_code_table.py`
walks every code in that table against real code paths so the two can't
drift apart silently.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

from . import commands
from . import exit_codes
from .config import resolve_config
from .errors import ApiError, AuthOrConfigError, GdocsMdError, NotFoundOrPermissionError, UsageError

_GLOBAL_FLAGS = (
    # (flag names, dest, kwargs) -- added to BOTH the top-level parser and
    # every leaf subcommand parser, each with default=SUPPRESS (see
    # `_add_global_flags`), so `gdocs-md --account X get ...` and
    # `gdocs-md get ... --account X` both work. The baseline and every
    # existing caller (including scripts written against it) use the
    # before-subcommand form, so that form has to keep working even though
    # every flag here is defined per-subcommand -- SUPPRESS is what stops
    # the subparser's own (unset) default from clobbering a value the
    # top-level parser already captured.
    (("--config-file",), "config_file", {"default": argparse.SUPPRESS, "help": "Path to config.json (default: XDG config dir)"}),
    (("--credentials-dir",), "credentials_dir", {"default": argparse.SUPPRESS, "help": "Base directory for OAuth tokens/client"}),
    (("--oauth-client",), "oauth_client_file", {"default": argparse.SUPPRESS, "help": "Path to oauth-client.json"}),
    (("--registry",), "registry_file", {"default": argparse.SUPPRESS, "help": "Path to the document registry JSON file"}),
    (("--account",), "account", {"default": argparse.SUPPRESS, "help": "Account to use (see config resolution in README)"}),
    (("--json",), "json", {"action": "store_true", "default": argparse.SUPPRESS, "help": "Emit machine-readable JSON on stdout"}),
)

# Normalized defaults applied once, after parsing, for any flag neither
# position supplied (SUPPRESS means the attribute may not exist at all).
_GLOBAL_DEFAULTS = {
    "config_file": None,
    "credentials_dir": None,
    "oauth_client_file": None,
    "registry_file": None,
    "account": None,
    "json": False,
}


class _ArgumentParser(argparse.ArgumentParser):
    """argparse calls `.error()` and then exits the process directly --
    that bypasses our own --json error rendering and exit-code contract
    entirely (a bad flag always exited 2 through argparse's own stderr
    write, plain text, no matter what). Raising UsageError instead routes
    a parse failure through the exact same GdocsMdError handling as every
    other error in this file."""

    def error(self, message):
        raise UsageError(f"{self.prog}: {message}")


def _add_global_flags(parser):
    for flags, dest, kwargs in _GLOBAL_FLAGS:
        parser.add_argument(*flags, dest=dest, **kwargs)


def build_parser():
    parser = _ArgumentParser(
        prog="gdocs-md", description="Read and write Google Docs from markdown."
    )
    _add_global_flags(parser)

    subparsers = parser.add_subparsers(dest="command", parser_class=_ArgumentParser)

    # auth
    auth_parser = subparsers.add_parser("auth", help="Authenticate / check authentication status")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", parser_class=_ArgumentParser)

    login_parser = auth_sub.add_parser("login", help="Run the OAuth consent flow and save a token")
    _add_global_flags(login_parser)
    login_parser.add_argument("--headless", action="store_true", help="Bind the loopback auth server to 0.0.0.0:8085 instead of opening a local browser")
    login_parser.add_argument("--timeout", type=float, default=180.0, help="Seconds to wait for the OAuth consent redirect before giving up (default: 180)")
    login_parser.set_defaults(func=commands.cmd_auth_login)

    status_parser = auth_sub.add_parser("status", help="Check whether an account is authenticated")
    _add_global_flags(status_parser)
    status_parser.set_defaults(func=commands.cmd_auth_status)

    # get
    get_parser = subparsers.add_parser("get", help="Read a Google Doc and output its content")
    _add_global_flags(get_parser)
    get_parser.add_argument("doc_id", nargs="?", default=None, help="Google Doc ID (or full URL)")
    get_parser.add_argument("--url", metavar="URL", default=None, help="Full Google Docs URL")
    get_parser.add_argument("--with-title", action="store_true", help="Prepend the document title as a markdown heading")
    get_parser.add_argument("--tab", metavar="TAB_ID", default=None, help="Read a single named tab")
    get_parser.add_argument("--all-tabs", action="store_true", dest="all_tabs", default=False, help="Read all tabs")
    get_parser.add_argument("--output", "-o", metavar="FILE", default=None, help="Write output to FILE instead of stdout")
    get_parser.set_defaults(func=commands.cmd_get)

    # create
    create_parser = subparsers.add_parser("create", help="Create a new Google Doc from a markdown file")
    _add_global_flags(create_parser)
    create_parser.add_argument("file", help="Path to markdown file")
    create_parser.add_argument("--title", help="Document title (defaults to filename)")
    create_parser.add_argument("--folder", help="Google Drive folder ID to place the doc in")
    create_parser.set_defaults(func=commands.cmd_create)

    # update
    update_parser = subparsers.add_parser("update", help="Update an existing Google Doc from a markdown file")
    _add_global_flags(update_parser)
    update_parser.add_argument("doc_id", help="Google Doc ID to update")
    update_parser.add_argument("file", nargs="?", default=None, help="Path to markdown file (omit when using --tab)")
    update_parser.add_argument("--tab", metavar="TAB_ID", default=None, help="Write content into a specific tab (requires --input)")
    update_parser.add_argument("--input", metavar="FILE", default=None, help="Path to markdown file when using --tab")
    update_parser.add_argument("--replace-all", action="store_true", dest="replace_all", default=False, help="Full document replacement via Drive DOCX upload (destroys comments/suggestions)")
    update_parser.add_argument("--force", action="store_true", dest="force", default=False, help="Confirm destructive --replace-all on a multi-tab doc")
    update_parser.add_argument("--dry-run", action="store_true", dest="dry_run", default=False, help="Preview what would change without writing")
    update_parser.set_defaults(func=commands.cmd_update)

    # tabs
    tabs_parser = subparsers.add_parser("tabs", help="List or create tabs in a Google Doc")
    _add_global_flags(tabs_parser)
    tabs_parser.add_argument("doc_id", help="Google Doc ID")
    tabs_parser.add_argument("--create", action="store_true", dest="create", default=False, help="Create a new tab")
    tabs_parser.add_argument("--title", metavar="TITLE", default="New Tab", help="Title for the new tab")
    tabs_parser.add_argument("--index", metavar="N", type=int, default=None, help="Position index for the new tab")
    tabs_parser.add_argument("--parent", metavar="TAB_ID", default=None, help="Parent tab ID for nesting")
    tabs_parser.set_defaults(func=commands.cmd_tabs)

    # sheets
    sheets_parser = subparsers.add_parser("sheets", help="Read a Google Spreadsheet as a markdown table")
    _add_global_flags(sheets_parser)
    sheets_parser.add_argument("spreadsheet_id", help="Google Spreadsheet ID")
    sheets_parser.add_argument("--sheet", metavar="SHEET_NAME", default=None, help="Sheet (tab) name to read")
    sheets_parser.add_argument("--range", metavar="A1_RANGE", dest="range_param", default=None, help="A1 notation range (overrides --sheet)")
    sheets_parser.add_argument("--list-sheets", action="store_true", dest="list_sheets", default=False, help="List sheet names and exit")
    sheets_parser.set_defaults(func=commands.cmd_sheets)

    # list
    list_parser = subparsers.add_parser("list", help="List documents in the registry")
    _add_global_flags(list_parser)
    list_parser.set_defaults(func=commands.cmd_list)

    return parser


def _classify_exception(e):
    """Map an exception that escaped a command function to a GdocsMdError
    with the documented exit code. GdocsMdError instances pass through
    unchanged (they already carry the right code). Everything else is
    inspected by type/status and given the most specific code that fits;
    anything unrecognized is API_ERROR (8), never a bare traceback.

    Mapping: HttpError 404/403 -> 4 (not-found/permission), HttpError
    401 -> 3 (auth), RefreshError -> 3 (auth), any other HttpError status
    or unrecognized exception -> 8.
    """
    if isinstance(e, GdocsMdError):
        return e

    try:
        from googleapiclient.errors import HttpError
    except ImportError:  # pragma: no cover - always installed, defensive only
        HttpError = ()  # noqa: N806

    try:
        from google.auth.exceptions import RefreshError
    except ImportError:  # pragma: no cover
        RefreshError = ()  # noqa: N806

    if HttpError and isinstance(e, HttpError):
        status = getattr(getattr(e, "resp", None), "status", None)
        if status == 401:
            return AuthOrConfigError(f"Google API returned 401 (authentication required or expired): {e}")
        if status in (403, 404):
            return NotFoundOrPermissionError(f"Google API returned {status}: {e}")
        return ApiError(f"Google API error{f' ({status})' if status else ''}: {e}")

    if RefreshError and isinstance(e, RefreshError):
        return AuthOrConfigError(f"Token refresh failed, re-authenticate: {e}")

    return ApiError(f"{type(e).__name__}: {e}")


def _emit_error(err: GdocsMdError, json_mode: bool) -> int:
    if json_mode:
        json.dump({"error": err.message, "exit_code": err.exit_code}, sys.stderr, indent=2)
        sys.stderr.write("\n")
    else:
        print(f"Error: {err.message}", file=sys.stderr)
    return err.exit_code


def main(argv=None):
    argv = list(argv) if argv is not None else sys.argv[1:]
    # Best-effort --json detection for errors raised before argument
    # parsing can tell us for certain (a bad flag, or no subcommand at
    # all) -- a real parse failure means args.json was never assigned, so
    # this is the only signal available for deciding how to render it.
    json_mode = "--json" in argv

    parser = build_parser()
    debug = bool(os.environ.get("GDOCS_MD_DEBUG"))

    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        if debug:
            traceback.print_exc()
        return _emit_error(e, json_mode)

    for name, default in _GLOBAL_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, default)

    json_mode = args.json

    if not getattr(args, "command", None):
        parser.print_help()
        return exit_codes.USAGE

    if args.command == "auth" and not getattr(args, "auth_command", None):
        return _emit_error(
            UsageError("gdocs-md auth: a subcommand is required: login, status"), json_mode
        )

    if not hasattr(args, "func"):
        parser.print_help()
        return exit_codes.USAGE

    try:
        config = resolve_config(args)
        args.func(args, config)
        return exit_codes.OK
    except GdocsMdError as e:
        if debug:
            traceback.print_exc()
        return _emit_error(e, json_mode)
    except KeyboardInterrupt:
        return _emit_error(ApiError("Interrupted."), json_mode)
    except BrokenPipeError:
        # The reader (e.g. `| head`) went away. Writing anything more --
        # even an error -- can itself raise BrokenPipeError; swallow it.
        try:
            sys.stderr.close()
        except Exception:
            pass
        return exit_codes.API_ERROR
    except Exception as e:
        if debug:
            traceback.print_exc()
        return _emit_error(_classify_exception(e), json_mode)


def entry_point():
    sys.exit(main())


if __name__ == "__main__":
    entry_point()
