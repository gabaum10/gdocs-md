"""gdocs-md: read and write Google Docs from markdown, with a diff-based
update mode that preserves comments and suggestions.

See README.md for a human quickstart, AGENTS.md for the full agent
contract (commands, exit codes, --json shapes, known limits).
"""

__version__ = "0.1.0"

from .cli import main

__all__ = ["main", "__version__"]
