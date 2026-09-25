"""Exception types that carry an exit code.

The CLI layer (cli.py) catches GdocsMdError and turns it into a clean
message + sys.exit(e.exit_code), instead of a traceback. Every place in the
library that wants to fail loudly with a specific, documented exit code
raises one of these rather than calling sys.exit itself -- that keeps the
library importable and testable without a process boundary.
"""

from . import exit_codes


class GdocsMdError(Exception):
    """Base class for all errors this tool raises on purpose."""
    exit_code = exit_codes.API_ERROR

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class UsageError(GdocsMdError):
    exit_code = exit_codes.USAGE


class AuthOrConfigError(GdocsMdError):
    exit_code = exit_codes.AUTH_OR_CONFIG


class NotFoundOrPermissionError(GdocsMdError):
    exit_code = exit_codes.NOT_FOUND_OR_PERMISSION


class UnrepresentableDiffError(GdocsMdError):
    """Smart-diff can't represent the requested change; caller should retry
    with --replace-all (or --tab, for a multi-tab doc)."""
    exit_code = exit_codes.UNREPRESENTABLE_DIFF


class DestructiveRefusedError(GdocsMdError):
    """A destructive operation (e.g. --replace-all on a multi-tab doc) was
    refused because it wasn't explicitly confirmed with --force."""
    exit_code = exit_codes.DESTRUCTIVE_REFUSED


class MissingPandocError(GdocsMdError):
    exit_code = exit_codes.MISSING_PANDOC


class ApiError(GdocsMdError):
    """Wraps an underlying Google API failure that doesn't fit a more
    specific category above."""
    exit_code = exit_codes.API_ERROR
