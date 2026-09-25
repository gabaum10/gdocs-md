"""Google API service builders, pandoc conversion, and tab enumeration
helpers shared across commands."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .auth import load_credentials
from .config import Config
from .errors import AuthOrConfigError, ApiError, MissingPandocError


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
    try:
        subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        if shutil.which("brew"):
            raise MissingPandocError(
                "pandoc is not installed. Install it with: brew install pandoc"
            ) from e
        raise MissingPandocError(
            "pandoc is not installed. See: https://pandoc.org/installing.html"
        ) from e


def convert_markdown_to_docx(markdown_file: Path) -> str:
    """Convert a markdown file to a temp DOCX using pandoc. Returns the temp
    file path.

    Runs pandoc with `cwd` set to the source file's own directory, so a
    relative image path in the markdown (e.g. `![x](img.png)`) resolves
    against the file it's written in rather than the caller's current
    directory. Without this, a relative image path silently produces a doc
    with no image, pandoc still exits 0, and nothing downstream notices.
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
                markdown_file.name,
                "-o",
                tmp_path,
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

    raise AuthOrConfigError(f"Tab '{tab_id}' not found in document '{doc_id}'")


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
