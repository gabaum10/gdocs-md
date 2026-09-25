"""Stable exit codes.

These are part of the CLI's contract with scripts and agents: a caller should
be able to branch on the exit code alone, without parsing stderr text. Once a
code is assigned to a meaning here, don't repurpose it -- add a new one
instead. See AGENTS.md for the documented table.
"""

OK = 0
USAGE = 2
AUTH_OR_CONFIG = 3
NOT_FOUND_OR_PERMISSION = 4
UNREPRESENTABLE_DIFF = 5
DESTRUCTIVE_REFUSED = 6
MISSING_PANDOC = 7
API_ERROR = 8
