"""Markdown -> Google Docs structure.

Two things live here: block-level parsing (`parse_markdown_blocks`, plus
`_flatten_blocks_for_diff` which expands those blocks into the flat list of
diff units both the smart-update diff and the full-rewrite writer share) and
inline-level parsing (`parse_inline`, the bold/italic/code segmenter).

One parser, one paragraph count. `parse_markdown_blocks` used to be
shadowed by a second, independently-written plain-text stripper that the
diff engine used for comparison while the request builder used this one for
formatting -- the two didn't always agree on how many paragraphs a
multi-line blockquote or a table produces (a table is one block here but one
row-paragraph per row in the doc; a blockquote is one block here but one
line-paragraph per line). Every paragraph after the first blockquote or
table drew its formatting from the wrong block as a result. `flatten_for_diff`
is the fix: it is the single source of truth for both the plain-text
comparison list and the formatting metadata, so the two can't drift apart
again.
"""

from __future__ import annotations

import re

from .indexing import utf16_len

# Mapping from ATX heading level to Docs named style type
HEADING_STYLE = {
    1: "HEADING_1",
    2: "HEADING_2",
    3: "HEADING_3",
    4: "HEADING_4",
    5: "HEADING_5",
    6: "HEADING_6",
}

# ---------------------------------------------------------------------------
# Inline formatting
# ---------------------------------------------------------------------------
#
# Underscore variants require word-boundary flanking (CommonMark suppresses
# intraword underscore emphasis; asterisk emphasis has no such rule, so the
# asterisk patterns are left permissive): the opening `_`(s) can't be preceded
# by a word char (blocks `snake_case_name`) or followed by whitespace/another
# `_`, and the closing `_`(s) can't be preceded by whitespace/another `_` or
# followed by a word char. Both conditions together also block a bare run
# like `___` or `_____` from matching itself (content collapsing to a lone
# `_`) -- every possible opening position in a same-char run is either
# preceded by a word char (another `_`) or followed by one, so the run has no
# valid open/close pair at all.

_BOLD_ITALIC_STAR_RE = re.compile(r"\*{3}(.+?)\*{3}")
_BOLD_ITALIC_UNDER_RE = re.compile(r"(?<!\w)_{3}(?!\s|_)(.+?)(?<!\s|_)_{3}(?!\w)")
_BOLD_STAR_RE = re.compile(r"\*{2}(.+?)\*{2}")
_BOLD_UNDER_RE = re.compile(r"(?<!\w)_{2}(?!\s|_)(.+?)(?<!\s|_)_{2}(?!\w)")
_ITALIC_STAR_RE = re.compile(r"\*(.+?)\*")
_ITALIC_UNDER_RE = re.compile(r"(?<!\w)_(?!\s|_)(.+?)(?<!\s|_)_(?!\w)")
_CODE_RE = re.compile(r"`(.+?)`")

_INLINE_PATTERNS = [
    (_BOLD_ITALIC_STAR_RE, {"bold": True, "italic": True}),
    (_BOLD_ITALIC_UNDER_RE, {"bold": True, "italic": True}),
    (_BOLD_STAR_RE, {"bold": True}),
    (_BOLD_UNDER_RE, {"bold": True}),
    (_ITALIC_STAR_RE, {"italic": True}),
    (_ITALIC_UNDER_RE, {"italic": True}),
    (_CODE_RE, {"weightedFontFamily": {"fontFamily": "Courier New"}}),
]


def parse_inline(text):
    """
    Parse inline markdown formatting in a line of text.
    Returns a list of (text_segment, style_dict) tuples.
    Segments with no formatting have an empty style_dict.

    <br> tags are converted to vertical tab (\\v) which Google Docs treats as
    a soft line break (shift-enter) within a paragraph.
    """
    text = text.replace("<br>", "\v").replace("<BR>", "\v")

    if not text:
        return []

    earliest_match = None
    earliest_style = None

    for pattern, style in _INLINE_PATTERNS:
        m = pattern.search(text)
        if m and (earliest_match is None or m.start() < earliest_match.start()):
            earliest_match = m
            earliest_style = style

    if earliest_match is None:
        return [(text.replace("\\", ""), {})]

    result = []
    before = text[: earliest_match.start()]
    if before:
        result.extend(parse_inline(before))

    inner = next(g for g in earliest_match.groups() if g is not None)
    result.append((inner, earliest_style))

    after = text[earliest_match.end():]
    if after:
        result.extend(parse_inline(after))

    return result


def strip_inline(text):
    """Strip inline markdown formatting markers from a text segment, for
    plain-text diffing."""
    text = _BOLD_ITALIC_STAR_RE.sub(lambda m: m.group(1), text)
    text = _BOLD_ITALIC_UNDER_RE.sub(lambda m: m.group(1), text)
    text = _BOLD_STAR_RE.sub(lambda m: m.group(1), text)
    text = _BOLD_UNDER_RE.sub(lambda m: m.group(1), text)
    text = _ITALIC_STAR_RE.sub(lambda m: m.group(1), text)
    text = _ITALIC_UNDER_RE.sub(lambda m: m.group(1), text)
    text = _CODE_RE.sub(lambda m: m.group(1), text)
    text = text.replace("\\", "")
    text = re.sub(r"<[Bb][Rr]>", " ", text)
    return text


def build_text_run_requests(text_with_styles, insert_index, tab_id):
    """
    Build insertText + updateTextStyle requests for a list of (text, style)
    segments. Returns (list_of_requests, new_index).

    Every segment gets an explicit updateTextStyle to prevent style bleed
    from adjacent formatted runs -- Google Docs inherits the style of the
    character at the insertion point, so plain segments must explicitly
    reset bold/italic.

    Indices advance by UTF-16 code units (`indexing.utf16_len`), not Python
    `len()` -- the Docs API counts positions in UTF-16 units, and an astral
    character (most emoji) is two of those but one Python character. See
    indexing.py's module docstring for why this is the one place that
    matters.
    """
    requests = []
    idx = insert_index

    for segment_text, style in text_with_styles:
        if not segment_text:
            continue
        start = idx
        loc = {"index": idx}
        if tab_id:
            loc["tabId"] = tab_id
        requests.append({"insertText": {"location": loc, "text": segment_text}})
        end = idx + utf16_len(segment_text)
        idx = end

        text_style = {
            "bold": style.get("bold", False),
            "italic": style.get("italic", False),
        }
        if "weightedFontFamily" in style:
            text_style["weightedFontFamily"] = style["weightedFontFamily"]

        fields = "bold,italic"
        if "weightedFontFamily" in style:
            fields += ",weightedFontFamily"

        # Skip style reset on the trailing newline / vertical tab to avoid
        # overriding paragraph style.
        if segment_text not in ("\n", "\v"):
            rng = {"startIndex": start, "endIndex": end}
            if tab_id:
                rng["tabId"] = tab_id
            requests.append(
                {"updateTextStyle": {"range": rng, "textStyle": text_style, "fields": fields}}
            )

    return requests, idx


# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------


def _is_table_separator(line):
    stripped = line.strip()
    if not stripped.startswith("|") and not stripped.endswith("|"):
        return False
    return bool(re.match(r"^[\s|:\-]+$", stripped))


def _parse_table_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def parse_markdown_blocks(markdown_text):
    """
    Parse markdown text into a list of block descriptors.

    Each block is a dict with 'type' key:
      {'type': 'paragraph', 'text': str, 'style': 'NORMAL_TEXT'|'HEADING_N'}
      {'type': 'list_item', 'text': str, 'list_type': 'unordered'|'ordered'}
      {'type': 'blockquote', 'lines': [str, ...]}
      {'type': 'table', 'headers': [str, ...], 'rows': [[str, ...], ...], 'cols': int}

    Table cells may contain <br> which will be converted at write time.

    List nesting (indentation) is not tracked: the list regexes match
    against the stripped line, so a markdown sub-item parses to the same
    flat 'list_item' block as a top-level one. New lists written by this
    tool are therefore always flat. This is a known limitation, not fixed
    here -- see AGENTS.md.
    """
    blocks = []
    lines = markdown_text.splitlines()
    i = 0
    paragraph_buffer = []
    paragraph_style = "NORMAL_TEXT"
    blockquote_buffer = []

    def flush_paragraph():
        nonlocal paragraph_buffer, paragraph_style
        if not paragraph_buffer:
            return
        blocks.append(
            {"type": "paragraph", "text": " ".join(paragraph_buffer), "style": paragraph_style}
        )
        paragraph_buffer = []
        paragraph_style = "NORMAL_TEXT"

    def flush_blockquote():
        nonlocal blockquote_buffer
        if not blockquote_buffer:
            return
        blocks.append({"type": "blockquote", "lines": list(blockquote_buffer)})
        blockquote_buffer = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            flush_blockquote()
            i += 1
            continue

        if re.match(r"^[-*_]{3,}\s*$", stripped):
            flush_paragraph()
            flush_blockquote()
            i += 1
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if heading_match:
            flush_paragraph()
            flush_blockquote()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            heading_text = re.sub(r"\s+#+\s*$", "", heading_text)
            blocks.append(
                {
                    "type": "paragraph",
                    "text": heading_text,
                    "style": HEADING_STYLE.get(level, "HEADING_6"),
                }
            )
            i += 1
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            while i < len(lines) and (
                lines[i].strip().startswith(">") or (blockquote_buffer and lines[i].strip())
            ):
                bline = lines[i].strip()
                if bline.startswith(">"):
                    blockquote_buffer.append(bline[1:].strip())
                    i += 1
                else:
                    break
            flush_blockquote()
            continue

        if "|" in stripped and i + 1 < len(lines) and _is_table_separator(lines[i + 1]):
            flush_paragraph()
            flush_blockquote()
            headers = _parse_table_row(stripped)
            cols = len(headers)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i]:
                row = _parse_table_row(lines[i])
                while len(row) < cols:
                    row.append("")
                rows.append(row[:cols])
                i += 1
            blocks.append({"type": "table", "headers": headers, "rows": rows, "cols": cols})
            continue

        ulist_match = re.match(r"^[-*+]\s+(.*)", stripped)
        if ulist_match:
            flush_paragraph()
            flush_blockquote()
            blocks.append(
                {"type": "list_item", "text": ulist_match.group(1).strip(), "list_type": "unordered"}
            )
            i += 1
            continue

        olist_match = re.match(r"^\d+[.)]\s+(.*)", stripped)
        if olist_match:
            flush_paragraph()
            flush_blockquote()
            blocks.append(
                {"type": "list_item", "text": olist_match.group(1).strip(), "list_type": "ordered"}
            )
            i += 1
            continue

        flush_blockquote()
        paragraph_buffer.append(stripped)
        i += 1

    flush_paragraph()
    flush_blockquote()
    return blocks


def flatten_for_diff(blocks):
    """
    Expand parsed markdown blocks into a flat list of diff units, one per
    paragraph that would actually land in the Google Doc body if this
    markdown were written out. See module docstring for why this must be
    the single source of truth.

    Returns a list of dicts, each with:
        'plain'    -- stripped plain text, for diffing against the live doc
        'markdown' -- text to run through parse_inline at write time
        'kind'     -- 'paragraph' | 'list_item' | 'blockquote_line' | 'table_row'
        'block'    -- the originating block descriptor (style/list_type/etc.)

    Table note: table cells live inside a Docs 'table' element, not
    top-level body paragraphs, so a doc-side paragraph extraction never
    reads them back and the diff-based updater never builds a real Docs
    table for them either -- it falls back to inserting each row as one
    plain paragraph of pipe-stripped cell text, same as the full-rewrite
    path. Diffing a doc that already contains a real table is unreliable
    (known limitation -- see AGENTS.md).
    """
    units = []
    for block in blocks:
        btype = block.get("type")
        if btype == "skip":
            continue
        elif btype == "blockquote":
            for line in block.get("lines", []):
                units.append(
                    {"plain": strip_inline(line), "markdown": line, "kind": "blockquote_line", "block": block}
                )
        elif btype == "table":
            for row in [block["headers"]] + list(block["rows"]):
                text = strip_inline("  ".join(row))
                units.append({"plain": text, "markdown": text, "kind": "table_row", "block": block})
        elif btype == "list_item":
            units.append(
                {
                    "plain": strip_inline(block["text"]),
                    "markdown": block["text"],
                    "kind": "list_item",
                    "block": block,
                }
            )
        else:  # 'paragraph' (covers plain paragraphs and ATX headings alike)
            units.append(
                {
                    "plain": strip_inline(block["text"]),
                    "markdown": block["text"],
                    "kind": "paragraph",
                    "block": block,
                }
            )
    return units


def build_block_requests(block, tab_id, idx):
    """
    Build batchUpdate requests for a single non-table block (full-rewrite
    path). Returns (requests, new_idx).
    """
    requests = []
    btype = block["type"]

    if btype == "paragraph":
        text = block["text"]
        style = block.get("style", "NORMAL_TEXT")
        segments = parse_inline(text)
        segments_with_newline = segments + [("\n", {})]
        para_start = idx
        reqs, idx = build_text_run_requests(segments_with_newline, idx, tab_id)
        requests.extend(reqs)
        if style != "NORMAL_TEXT":
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": para_start, "endIndex": idx, "tabId": tab_id},
                        "paragraphStyle": {"namedStyleType": style},
                        "fields": "namedStyleType",
                    }
                }
            )

    elif btype == "list_item":
        text = block["text"]
        list_type = block.get("list_type", "unordered")
        segments = parse_inline(text)
        segments_with_newline = segments + [("\n", {})]
        para_start = idx
        reqs, idx = build_text_run_requests(segments_with_newline, idx, tab_id)
        requests.extend(reqs)
        preset = (
            "BULLET_DISC_CIRCLE_SQUARE"
            if list_type == "unordered"
            else "NUMBERED_DECIMAL_ALPHA_ROMAN"
        )
        requests.append(
            {
                "createParagraphBullets": {
                    "range": {"startIndex": para_start, "endIndex": idx, "tabId": tab_id},
                    "bulletPreset": preset,
                }
            }
        )

    elif btype == "blockquote":
        for bline in block["lines"]:
            segments = parse_inline(bline)
            segments_with_newline = segments + [("\n", {})]
            para_start = idx
            reqs, idx = build_text_run_requests(segments_with_newline, idx, tab_id)
            requests.extend(reqs)
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": para_start, "endIndex": idx, "tabId": tab_id},
                        "paragraphStyle": {
                            "indentFirstLine": {"magnitude": 0, "unit": "PT"},
                            "indentStart": {"magnitude": 36, "unit": "PT"},
                        },
                        "fields": "indentFirstLine,indentStart",
                    }
                }
            )

    return requests, idx
