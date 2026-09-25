"""Suggestions and comments: reading and formatting the parts of a doc that
`get` appends after the main text."""

from __future__ import annotations

import sys


def extract_suggestions_from_doc(doc):
    """
    Extract suggested insertions and deletions from a Google Docs API
    response.

    Returns two lists: (insertions, deletions), each a list of text strings.
    Deduplicates adjacent identical entries that arise from multi-run
    suggestions.
    """
    insertions = []
    deletions = []

    for element in doc.get("body", {}).get("content", []):
        if "paragraph" not in element:
            continue
        for elem in element["paragraph"].get("elements", []):
            tr = elem.get("textRun")
            if not tr:
                continue
            text = tr.get("content", "").rstrip("\n")
            if not text:
                continue
            if tr.get("suggestedInsertionIds"):
                if not insertions or insertions[-1] != text:
                    insertions.append(text)
            if tr.get("suggestedDeletionIds"):
                if not deletions or deletions[-1] != text:
                    deletions.append(text)

    return insertions, deletions


def format_suggestions(insertions, deletions):
    lines = ["## Suggested Edits", ""]

    if insertions:
        lines.append("**Insertions:**")
        for text in insertions:
            lines.append(f'  [+] "{text}"')
        lines.append("")

    if deletions:
        lines.append("**Deletions:**")
        for text in deletions:
            lines.append(f'  [-] "{text}"')
        lines.append("")

    return "\n".join(lines)


def fetch_comments(drive_service, doc_id):
    """Fetch all comments on a document via the Drive v3 API. Returns the
    raw comments list (may be empty). Fails soft: a comments-fetch failure
    warns on stderr and returns [] rather than aborting the whole `get`."""
    try:
        result = drive_service.comments().list(fileId=doc_id, fields="*").execute()
        return result.get("comments", [])
    except Exception as e:
        print(f"[Warning: could not fetch comments: {e}]", file=sys.stderr)
        return []


def format_comments(comments):
    lines = ["## Comments", ""]

    for i, comment in enumerate(comments, 1):
        author = comment.get("author", {}).get("displayName", "Unknown")
        content = comment.get("content", "").strip()
        quoted = comment.get("quotedFileContent", {}).get("value", "").strip()
        created = comment.get("createdTime", "")
        resolved = comment.get("resolved", False)

        status = " [resolved]" if resolved else ""
        lines.append(f"### Comment {i} — {author}{status}")
        if created:
            lines.append(f"*{created[:19].replace('T', ' ')}*")
        if quoted:
            lines.append(f'> "{quoted}"')
        lines.append("")
        lines.append(content)

        for reply in comment.get("replies", []):
            reply_author = reply.get("author", {}).get("displayName", "Unknown")
            reply_content = reply.get("content", "").strip()
            reply_time = reply.get("createdTime", "")
            lines.append("")
            lines.append(f"  **Reply — {reply_author}**")
            if reply_time:
                lines.append(f"  *{reply_time[:19].replace('T', ' ')}*")
            lines.append(f"  {reply_content}")

        lines.append("")

    return "\n".join(lines)
