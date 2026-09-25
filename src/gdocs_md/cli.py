"""Argument parsing and top-level error -> exit-code translation.

Every raised GdocsMdError carries its own exit code (errors.py). This is
the one place that catches it, prints the message (plain text, or JSON on
stderr under --json), and turns it into sys.exit(code) -- see AGENTS.md's
exit-code table for the documented contract.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import commands
from .config import resolve_config
from .errors import GdocsMdError
from . import exit_codes


def _add_common_args(parser):
    parser.add_argument("--account", default=None, help="Account to use (see config resolution in README)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON on stdout")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gdocs-md", description="Read and write Google Docs from markdown."
    )
    parser.add_argument("--config-file", default=None, help="Path to config.json (default: XDG config dir)")
    parser.add_argument("--credentials-dir", default=None, help="Base directory for OAuth tokens/client")
    parser.add_argument("--oauth-client", dest="oauth_client_file", default=None, help="Path to oauth-client.json")
    parser.add_argument("--registry", dest="registry_file", default=None, help="Path to the document registry JSON file")

    subparsers = parser.add_subparsers(dest="command")

    # auth
    auth_parser = subparsers.add_parser("auth", help="Authenticate / check authentication status")
    auth_sub = auth_parser.add_subparsers(dest="auth_command")

    login_parser = auth_sub.add_parser("login", help="Run the OAuth consent flow and save a token")
    _add_common_args(login_parser)
    login_parser.add_argument("--headless", action="store_true", help="Bind the loopback auth server to 0.0.0.0:8085 instead of opening a local browser")
    login_parser.set_defaults(func=commands.cmd_auth_login)

    status_parser = auth_sub.add_parser("status", help="Check whether an account is authenticated")
    _add_common_args(status_parser)
    status_parser.set_defaults(func=commands.cmd_auth_status)

    # get
    get_parser = subparsers.add_parser("get", help="Read a Google Doc and output its content")
    _add_common_args(get_parser)
    get_parser.add_argument("doc_id", nargs="?", default=None, help="Google Doc ID (or full URL)")
    get_parser.add_argument("--url", metavar="URL", default=None, help="Full Google Docs URL")
    get_parser.add_argument("--with-title", action="store_true", help="Prepend the document title as a markdown heading")
    get_parser.add_argument("--tab", metavar="TAB_ID", default=None, help="Read a single named tab")
    get_parser.add_argument("--all-tabs", action="store_true", dest="all_tabs", default=False, help="Read all tabs")
    get_parser.add_argument("--output", "-o", metavar="FILE", default=None, help="Write output to FILE instead of stdout")
    get_parser.set_defaults(func=commands.cmd_get)

    # create
    create_parser = subparsers.add_parser("create", help="Create a new Google Doc from a markdown file")
    _add_common_args(create_parser)
    create_parser.add_argument("file", help="Path to markdown file")
    create_parser.add_argument("--title", help="Document title (defaults to filename)")
    create_parser.add_argument("--folder", help="Google Drive folder ID to place the doc in")
    create_parser.set_defaults(func=commands.cmd_create)

    # update
    update_parser = subparsers.add_parser("update", help="Update an existing Google Doc from a markdown file")
    _add_common_args(update_parser)
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
    _add_common_args(tabs_parser)
    tabs_parser.add_argument("doc_id", help="Google Doc ID")
    tabs_parser.add_argument("--create", action="store_true", dest="create", default=False, help="Create a new tab")
    tabs_parser.add_argument("--title", metavar="TITLE", default="New Tab", help="Title for the new tab")
    tabs_parser.add_argument("--index", metavar="N", type=int, default=None, help="Position index for the new tab")
    tabs_parser.add_argument("--parent", metavar="TAB_ID", default=None, help="Parent tab ID for nesting")
    tabs_parser.set_defaults(func=commands.cmd_tabs)

    # sheets
    sheets_parser = subparsers.add_parser("sheets", help="Read a Google Spreadsheet as a markdown table")
    _add_common_args(sheets_parser)
    sheets_parser.add_argument("spreadsheet_id", help="Google Spreadsheet ID")
    sheets_parser.add_argument("--sheet", metavar="SHEET_NAME", default=None, help="Sheet (tab) name to read")
    sheets_parser.add_argument("--range", metavar="A1_RANGE", dest="range_param", default=None, help="A1 notation range (overrides --sheet)")
    sheets_parser.add_argument("--list-sheets", action="store_true", dest="list_sheets", default=False, help="List sheet names and exit")
    sheets_parser.set_defaults(func=commands.cmd_sheets)

    # list
    list_parser = subparsers.add_parser("list", help="List documents in the registry")
    _add_common_args(list_parser)
    list_parser.set_defaults(func=commands.cmd_list)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return exit_codes.USAGE

    if args.command == "auth" and not getattr(args, "auth_command", None):
        parser.parse_args((argv or sys.argv[1:]) + ["--help"])
        return exit_codes.USAGE

    if not hasattr(args, "func"):
        parser.print_help()
        return exit_codes.USAGE

    json_mode = getattr(args, "json", False)

    try:
        config = resolve_config(args)
        args.func(args, config)
        return exit_codes.OK
    except GdocsMdError as e:
        if json_mode:
            json.dump({"error": e.message, "exit_code": e.exit_code}, sys.stderr, indent=2)
            sys.stderr.write("\n")
        else:
            print(f"Error: {e.message}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return exit_codes.API_ERROR


def entry_point():
    sys.exit(main())


if __name__ == "__main__":
    entry_point()
