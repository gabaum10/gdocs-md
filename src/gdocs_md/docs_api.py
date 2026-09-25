"""Google API service builders, pandoc conversion, and tab enumeration
helpers shared across commands."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .auth import load_credentials
from .config import Config
from .errors import ApiError, MissingPandocError, NotFoundOrPermissionError

# `--markdown-headings=atx` (used below) requires pandoc >= 2.11.2; on an
# older pandoc (e.g. Ubuntu 22.04's apt package, 2.9.x) it's an unrecognized
# option and the conversion fails outright. check_pandoc() checks the
# installed version against this floor up front, so the failure is exit 7
# ("pandoc missing or too old", naming the minimum) instead of a generic
# exit 8 pandoc-argument error with no indication of why.
_MIN_PANDOC_VERSION = (2, 11, 2)


def get_docs_service(config: Config, account: str):
    creds = load_credentials(config, account)
    from googleapiclient.discovery import build

    try:
        return build("docs", "v1", credentials=creds)
    except Exception as e:
        raise ApiError(f"Failed to build Docs service: {e}") from e


def get_drive_service(config: Config, account: str):
    creds = load_credentials(config, account)
    from googleapiclient.discovery import build

    try:
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        raise ApiError(f"Failed to build Drive service: {e}") from e


def get_sheets_service(config: Config, account: str):
    creds = load_credentials(config, account)
    from googleapiclient.discovery import build

    try:
        return build("sheets", "v4", credentials=creds)
    except Exception as e:
        raise ApiError(f"Failed to build Sheets service: {e}") from e


def check_pandoc():
    """Verify pandoc is installed AND new enough (>= 2.11.2 -- see
    `_MIN_PANDOC_VERSION`). Raises MissingPandocError (exit 7) either way,
    naming the minimum version when the installed one is too old rather
    than letting an old pandoc fail later with an "Unknown option" error
    that gives no indication of why."""
    try:
        proc = subprocess.run(["pandoc", "--version"], capture_output=True, check=True, text=True)
    except FileNotFoundError as e:
        if shutil.which("brew"):
            raise MissingPandocError(
                "pandoc is not installed. Install it with: brew install pandoc"
            ) from e
        raise MissingPandocError(
            "pandoc is not installed. See: https://pandoc.org/installing.html"
        ) from e
    except (subprocess.CalledProcessError, PermissionError) as e:
        raise MissingPandocError(f"pandoc is installed but could not be run: {e}") from e

    match = re.search(r"pandoc(?:\.exe)?\s+(\d+)\.(\d+)(?:\.(\d+))?", proc.stdout)
    if match:
        version = tuple(int(g) if g else 0 for g in match.groups())
        if version < _MIN_PANDOC_VERSION:
            min_str = ".".join(str(n) for n in _MIN_PANDOC_VERSION)
            found_str = ".".join(str(n) for n in version)
            raise MissingPandocError(
                f"pandoc {found_str} is installed, but gdocs-md requires pandoc "
                f">= {min_str} (uses --markdown-headings=atx). "
                "See: https://pandoc.org/installing.html"
            )
    # If the version string can't be parsed, proceed rather than block on a
    # format this check doesn't recognize -- the loud failure here is
    # "pandoc too old", not "pandoc's --version output changed shape".


def convert_markdown_to_docx(markdown_file: Path) -> str:
    """Convert a markdown file to a temp DOCX using pandoc. Returns the temp
    file path.

    Runs pandoc with `cwd` set to the source file's own directory, so a
    relative image path in the markdown (e.g. `![x](img.png)`) resolves
    against the file it's written in rather than the caller's current
    directory. Without this, a relative image path silently produces a doc
    with no image, pandoc still exits 0, and nothing downstream notices.

    Passes `--` before the filename: running from the file's own directory
    means the filename is now just its bare name (no leading `/`), and a
    markdown file whose name starts with `-` (e.g. `-notes.md`) would
    otherwise be parsed as a pandoc option instead of a filename.
    """
    check_pandoc()

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp_path = tmp.name

    markdown_file = Path(markdown_file).resolve()
    try:
        subprocess.run(
            [
                "pandoc",
                "-f",
                "markdown-auto_identifiers",
                "--markdown-headings=atx",
                "-o",
                tmp_path,
                "--",
                markdown_file.name,
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(markdown_file.parent),
        )
        return tmp_path
    except subprocess.CalledProcessError as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise ApiError(f"pandoc conversion failed: {e.stderr}") from e


def enumerate_tabs(tabs_list, depth=0):
    """Recursively yield (tab_properties_dict, depth) for every tab in the
    tree, top-level tabs first, then their childTabs, depth-first."""
    for tab in tabs_list:
        props = tab.get("tabProperties", {})
        yield props, depth
        children = tab.get("childTabs", [])
        if children:
            yield from enumerate_tabs(children, depth + 1)


def fetch_tabs(docs_service, doc_id):
    """Fetch doc with includeTabsContent=True. Returns (doc, tabs_list)."""
    doc = docs_service.documents().get(documentId=doc_id, includeTabsContent=True).execute()
    return doc, doc.get("tabs", [])


def get_tab_end_index(docs_service, doc_id, tab_id):
    """Return the endIndex of the last content element in the specified
    tab -- the safe deletion range end."""
    doc = docs_service.documents().get(documentId=doc_id, includeTabsContent=True).execute()

    for tab in doc.get("tabs", []):
        if tab.get("tabProperties", {}).get("tabId") == tab_id:
            body = tab.get("documentTab", {}).get("body", {})
            content = body.get("content", [])
            if not content:
                return 1
            return content[-1].get("endIndex", 1)

    raise NotFoundOrPermissionError(f"Tab '{tab_id}' not found in document '{doc_id}'")


def extract_text_from_doc(doc):
    """Extract plain text from a legacy (no-tabs) Google Docs API document."""
    content = []
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" in element:
            for elem in element["paragraph"].get("elements", []):
                if "textRun" in elem:
                    content.append(elem["textRun"]["content"])
    return "".join(content)


def extract_text_from_tab(tab):
    """Extract plain text from a single tab object (from an
    includeTabsContent response)."""
    body = tab.get("documentTab", {}).get("body", {})
    content = []
    for element in body.get("content", []):
        if "paragraph" in element:
            for elem in element["paragraph"].get("elements", []):
                if "textRun" in elem:
                    content.append(elem["textRun"]["content"])
    return "".join(content)


def find_tab_by_id(tabs, tab_id):
    """Recursively search tabs (and childTabs) for a tab matching tab_id."""
    for tab in tabs:
        props = tab.get("tabProperties", {})
        if props.get("tabId") == tab_id:
            return tab
        child = find_tab_by_id(tab.get("childTabs", []), tab_id)
        if child is not None:
            return child
    return None
