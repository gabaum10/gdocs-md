"""The diff-based (comments-preserving) doc updater.

Algorithm:
1. Fetch the doc with includeTabsContent=True and count tabs. Multi-tab
   docs are refused here (caller decides whether to retry with --tab or
   --replace-all).
2. Extract each body paragraph's position, plain text, and *state*
   (named style, whether it's bulleted, whether it carries this tool's
   blockquote indent, and whether the next body element is also a
   paragraph).
3. Diff old-plain-text-paragraphs against the new markdown's flattened
   diff units with difflib.SequenceMatcher.
4. For each changed opcode, build delete/insert/replace operations, each
   carrying whatever paragraph state is needed to decide styling.
5. Sort all operations in reverse document order and build batchUpdate
   requests.

Three fixes live in here that go beyond "just make the diff work":

F1 (bullet identity): a replace or insert that lands on/next-to an
already-bulleted paragraph does NOT call createParagraphBullets on it. The
paragraph mark that carries listId/nestingLevel is never deleted by a
replace (only the text before it), so leaving its bullet formatting alone
is what preserves list identity and nesting through an edit. See
`style_requests_for_unit`.

F2 (delete-before-structural-element): a pure paragraph deletion only
removes the paragraph's own trailing newline (the full range that lets the
next paragraph merge upward) when the next body element is itself a
paragraph. DeleteContentRangeRequest rejects deleting the newline
immediately before a table/TOC/section-break without deleting that element
too; falling back to content-only deletion (leaving an empty paragraph)
keeps the whole batchUpdate from failing atomically on docs with those
elements.

F3 (style/bullet/indent reset, both directions): `style_requests_for_unit`
compares the unit's target state against the *anchor* paragraph's (insert)
or the *replaced* paragraph's (replace) state and emits a reset any time
they differ -- not just when the target has an explicit non-default style.
Previously this only worked when moving *into* a non-NORMAL_TEXT/bulleted/
indented state; moving *out* of one silently kept the old formatting.
"""

from __future__ import annotations

import difflib

from . import docs_api
from .errors import UnrepresentableDiffError, ApiError
from .markdown_parser import build_text_run_requests, flatten_for_diff, parse_inline, parse_markdown_blocks


def extract_paragraphs_with_positions(doc, tab_body=None):
    """
    Extract paragraphs from a Google Doc with their character positions and
    state.

    Returns a list of dicts:
        'start', 'end'   -- startIndex/endIndex of the paragraph element
        'text'           -- plain text content (including trailing '\\n')
        'plain'          -- stripped text for comparison (no trailing '\\n')
        'style'          -- namedStyleType, default 'NORMAL_TEXT'
        'bullet'         -- True if this paragraph carries a `bullet` (is a
                             list item)
        'indent'         -- True if paragraphStyle.indentStart has a
                             positive magnitude (this tool's blockquote
                             convention)
        'next_is_paragraph' -- True if the next element in the same content
                             list is itself a paragraph (False if it's a
                             table/TOC/section-break, or if there is none)

    If tab_body is provided (a dict with a 'content' key from a tab's
    documentTab.body), it is used instead of doc['body']. This enables
    tab-aware diffing.
    """
    paragraphs = []
    body = tab_body if tab_body is not None else doc.get("body", {})
    content = body.get("content", [])
    for i, element in enumerate(content):
        if "paragraph" not in element:
            continue
        para = element["paragraph"]
        start = element.get("startIndex", 0)
        end = element.get("endIndex", 0)
        text_parts = [
            elem["textRun"]["content"] for elem in para.get("elements", []) if "textRun" in elem
        ]
        text = "".join(text_parts)
        plain = text.rstrip("\n").strip()
        pstyle = para.get("paragraphStyle", {})
        next_elem = content[i + 1] if i + 1 < len(content) else None
        paragraphs.append(
            {
                "start": start,
                "end": end,
                "text": text,
                "plain": plain,
                "style": pstyle.get("namedStyleType", "NORMAL_TEXT"),
                "bullet": "bullet" in para,
                "indent": bool(pstyle.get("indentStart", {}).get("magnitude", 0)),
                "next_is_paragraph": next_elem is not None and "paragraph" in next_elem,
            }
        )
    return paragraphs


def _find_anchor_paragraph(doc_paragraphs, position):
    """Return the paragraph text inserted at `position` will actually land
    inside (and therefore inherit the style/bullet/indent of), per the Docs
    API's own text-insertion inheritance rule: text inserted at index N
    takes on the formatting of whatever paragraph currently occupies N --
    the first paragraph, in document order, whose endIndex is past N. This
    is the SAME paragraph whether N sits at the very start of it (a
    "prepend" insert) or partway through it (an insert that will be
    followed by a '\\n', splitting it in two) -- both inherit that
    paragraph's current style, not the style of whatever precedes it.

    Getting this wrong is exactly F3's mid-doc case: inserting a plain
    paragraph immediately before an existing HEADING_2 lands INSIDE that
    heading's paragraph until the new '\\n' is written, so its anchor is
    the heading, not whatever paragraph came before it -- treating the
    *previous* paragraph as the anchor skips the needed style reset and
    the new paragraph reads back with the heading's style.
    """
    for para in doc_paragraphs:
        if para["end"] > position:
            return para
    return None


def _paragraph_state(para):
    """The (style, bullet, indent) triple `style_requests_for_unit` diffs
    against -- either a real extracted paragraph, or the safe default for
    'nothing here' (top of an empty doc)."""
    if para is None:
        return {"style": "NORMAL_TEXT", "bullet": False, "indent": False}
    return {"style": para["style"], "bullet": para["bullet"], "indent": para["indent"]}


def style_requests_for_unit(unit, para_start, para_end, target_tab_id, prior_state):
    """
    Build the paragraph-level style request(s) -- heading/reset, bullet
    create/delete, indent set/reset -- for one diff unit (see
    `markdown_parser.flatten_for_diff`), covering the range
    [para_start, para_end) just inserted or replaced.

    `prior_state` is the anchor paragraph's state (insert) or the replaced
    paragraph's state (replace), as returned by `_paragraph_state`. Emits a
    request only when the target state differs from it -- this is both F1
    (skip createParagraphBullets, and therefore preserve listId/
    nestingLevel, when the paragraph is already bulleted the way we want)
    and F3 (emit the reset when moving OUT of a style/bullet/indent, not
    just into one).
    """
    kind = unit["kind"]
    block = unit["block"]
    rng = {"startIndex": para_start, "endIndex": para_end}
    if target_tab_id:
        rng["tabId"] = target_tab_id

    requests = []

    target_style = block.get("style", "NORMAL_TEXT") if kind == "paragraph" else "NORMAL_TEXT"
    if prior_state["style"] != target_style:
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": rng,
                    "paragraphStyle": {"namedStyleType": target_style},
                    "fields": "namedStyleType",
                }
            }
        )

    target_bullet = kind == "list_item"
    if target_bullet and not prior_state["bullet"]:
        preset = (
            "BULLET_DISC_CIRCLE_SQUARE"
            if block.get("list_type") == "unordered"
            else "NUMBERED_DECIMAL_ALPHA_ROMAN"
        )
        requests.append({"createParagraphBullets": {"range": rng, "bulletPreset": preset}})
    elif not target_bullet and prior_state["bullet"]:
        requests.append({"deleteParagraphBullets": {"range": rng}})
    # else: bullet state already matches -- nothing to do. This is the F1
    # fix: an edited list item that was already bulleted keeps its
    # listId/nestingLevel because we never touch its bullet formatting.

    target_indent = kind == "blockquote_line"
    if target_indent and not prior_state["indent"]:
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": rng,
                    "paragraphStyle": {
                        "indentFirstLine": {"magnitude": 0, "unit": "PT"},
                        "indentStart": {"magnitude": 36, "unit": "PT"},
                    },
                    "fields": "indentFirstLine,indentStart",
                }
            }
        )
    elif not target_indent and prior_state["indent"]:
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": rng,
                    "paragraphStyle": {
                        "indentFirstLine": {"magnitude": 0, "unit": "PT"},
                        "indentStart": {"magnitude": 0, "unit": "PT"},
                    },
                    "fields": "indentFirstLine,indentStart",
                }
            }
        )

    return requests


def smart_update_doc(docs_service, doc_id, markdown_text, dry_run=False):
    """
    Diff-based update of a Google Doc that preserves comments and
    suggestions.

    dry_run=True: compute ops but do NOT call batchUpdate. Returns the same
    dict shape with 'dry_run': True and no writes performed.

    Returns a dict with:
        'changed': int   -- number of paragraphs changed
        'unchanged': int -- number of paragraphs left alone
        'ops': int       -- number of API operations computed
        'tab_id': str    -- the tab ID targeted (None for legacy single-tab body)
        'dry_run': bool

    Raises UnrepresentableDiffError if the doc is multi-tab (caller should
    retry with --tab or --replace-all).
    """
    doc, tabs = docs_api.fetch_tabs(docs_service, doc_id)

    all_tab_props = list(docs_api.enumerate_tabs(tabs))

    if len(all_tab_props) > 1:
        tab_ids = [p.get("tabId", "?") for p, _ in all_tab_props]
        raise UnrepresentableDiffError(
            f"doc has {len(all_tab_props)} tabs ({', '.join(tab_ids)}); "
            f"specify --tab <id> or --replace-all"
        )

    if all_tab_props:
        target_tab_id = all_tab_props[0][0].get("tabId")
        tab_body = tabs[0].get("documentTab", {}).get("body", {}) if tabs else None
    else:
        target_tab_id = None
        tab_body = None

    doc_paragraphs = extract_paragraphs_with_positions(doc, tab_body=tab_body)

    # A paragraph's endIndex at the very end of the body covers the doc's
    # immutable trailing newline. insertText locations must be strictly
    # less than this, so any insert point computed at doc_end_index lands
    # one past the last legal index and the API rejects it.
    doc_end_index = doc_paragraphs[-1]["end"] if doc_paragraphs else 1

    diff_units = flatten_for_diff(parse_markdown_blocks(markdown_text))
    new_plain_paras = [u["plain"] for u in diff_units]

    old_plain = [p["plain"] for p in doc_paragraphs if p["plain"]]
    old_paras_nonempty = [p for p in doc_paragraphs if p["plain"]]

    matcher = difflib.SequenceMatcher(None, old_plain, new_plain_paras, autojunk=False)
    opcodes = matcher.get_opcodes()

    changed_count = sum(1 for tag, *_ in opcodes if tag != "equal")
    unchanged_count = sum(1 for tag, *_ in opcodes if tag == "equal")

    if changed_count == 0:
        return {
            "changed": 0,
            "unchanged": unchanged_count,
            "ops": 0,
            "tab_id": target_tab_id,
            "dry_run": dry_run,
        }

    # Each operation: {'doc_start', 'doc_end', 'unit', 'is_insert', 'seq',
    # 'anchor' (insert only), 'old_para' (replace only), 'next_is_paragraph'
    # (pure delete only)}. unit is None for a pure delete.
    operations = []

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue

        elif tag == "replace":
            old_slice = old_paras_nonempty[i1:i2]
            new_slice_units = diff_units[j1:j2]

            for k in range(max(len(old_slice), len(new_slice_units))):
                if k < len(old_slice) and k < len(new_slice_units):
                    operations.append(
                        {
                            "doc_start": old_slice[k]["start"],
                            "doc_end": old_slice[k]["end"],
                            "unit": new_slice_units[k],
                            "is_insert": False,
                            "seq": len(operations),
                            "old_para": old_slice[k],
                        }
                    )
                elif k < len(old_slice):
                    operations.append(
                        {
                            "doc_start": old_slice[k]["start"],
                            "doc_end": old_slice[k]["end"],
                            "unit": None,
                            "is_insert": False,
                            "seq": len(operations),
                            "next_is_paragraph": old_slice[k]["next_is_paragraph"],
                        }
                    )
                else:
                    insert_at = old_slice[-1]["end"] if old_slice else 1
                    operations.append(
                        {
                            "doc_start": insert_at,
                            "doc_end": insert_at,
                            "unit": new_slice_units[k],
                            "is_insert": True,
                            "seq": len(operations),
                        }
                    )

        elif tag == "delete":
            for old_para in old_paras_nonempty[i1:i2]:
                operations.append(
                    {
                        "doc_start": old_para["start"],
                        "doc_end": old_para["end"],
                        "unit": None,
                        "is_insert": False,
                        "seq": len(operations),
                        "next_is_paragraph": old_para["next_is_paragraph"],
                    }
                )

        elif tag == "insert":
            if i1 > 0 and (i1 - 1) < len(old_paras_nonempty):
                insert_at = old_paras_nonempty[i1 - 1]["end"]
            elif old_paras_nonempty:
                insert_at = old_paras_nonempty[0]["start"]
            else:
                insert_at = 1

            for j in range(j1, j2):
                operations.append(
                    {
                        "doc_start": insert_at,
                        "doc_end": insert_at,
                        "unit": diff_units[j],
                        "is_insert": True,
                        "seq": len(operations),
                    }
                )

    # Sort in REVERSE document order (highest start index first). Ties --
    # a run of inserts anchored to the same position, e.g. every paragraph
    # appended at the end of the doc -- are broken by DESCENDING seq.
    #
    # batchUpdate applies requests sequentially against the doc as it
    # stands after each prior request. A run of same-position inserts
    # behaves like a stack: each one lands immediately before whatever was
    # already inserted there, so whichever is applied LAST ends up FIRST in
    # the doc. To read out in original order (A, B, C) we must apply C,
    # then B, then A -- i.e. descending original order. Python's sort is
    # stable, so sorting on doc_start alone would leave same-position
    # inserts in original build order (applies A, B, C; reads back C, B,
    # A). Including seq, sorted descending too, makes the tie-break
    # explicit.
    operations.sort(key=lambda op: (op["doc_start"], op["seq"]), reverse=True)

    all_requests = []
    for op in operations:
        doc_start, doc_end, unit, is_insert = op["doc_start"], op["doc_end"], op["unit"], op["is_insert"]

        if is_insert:
            if not unit["markdown"]:
                continue
            # Pure insertion at doc_start (an exclusive endIndex, i.e. the
            # start of the next paragraph). Clamp to doc_end_index - 1: an
            # insert computed at the very end of the body would otherwise
            # land on the segment's endIndex, which the API rejects (the
            # trailing newline there is immutable). Floored at 1:
            # doc_end_index falls back to 1 on an empty extraction, and
            # index 0 is not a legal insertText location. When the clamp
            # fires, prefix the insert with '\n' so it lands as its own new
            # paragraph, reusing the doc's existing trailing paragraph mark
            # as this new paragraph's own close instead of fusing onto the
            # last one.
            idx = max(1, min(doc_start, doc_end_index - 1))
            clamped = idx < doc_start
            segments = parse_inline(unit["markdown"])
            if clamped:
                reqs, new_idx = build_text_run_requests(
                    [("\n", {})] + segments, idx, target_tab_id
                )
                para_start, para_end = idx + 1, new_idx + 1
            else:
                reqs, new_idx = build_text_run_requests(segments + [("\n", {})], idx, target_tab_id)
                para_start, para_end = idx, new_idx
            if reqs:
                all_requests.extend(reqs)
                anchor = _find_anchor_paragraph(doc_paragraphs, idx)
                prior_state = _paragraph_state(anchor)
                all_requests.extend(
                    style_requests_for_unit(unit, para_start, para_end, target_tab_id, prior_state)
                )

        elif unit is None:
            # Pure deletion: remove the whole paragraph including its own
            # trailing newline, so no empty paragraph is left behind --
            # unless the next body element isn't itself a paragraph (a
            # table/TOC/section-break), in which case deleting through this
            # newline is rejected by the API, or this is the doc's own last
            # paragraph, whose trailing newline is immutable. Both fall
            # back to content-only deletion, which does leave one empty
            # paragraph (known limitation, not fixed here).
            next_is_paragraph = op.get("next_is_paragraph", True)
            if doc_end < doc_end_index and next_is_paragraph:
                end = doc_end
            elif doc_end - 1 > doc_start:
                end = doc_end - 1
            else:
                end = None
            if end is not None:
                rng = {"startIndex": doc_start, "endIndex": end}
                if target_tab_id:
                    rng["tabId"] = target_tab_id
                all_requests.append({"deleteContentRange": {"range": rng}})

        else:
            # Replace: delete old content (the paragraph mark itself
            # stays), then insert styled new content at the same position.
            if doc_end - 1 > doc_start:
                rng = {"startIndex": doc_start, "endIndex": doc_end - 1}
                if target_tab_id:
                    rng["tabId"] = target_tab_id
                all_requests.append({"deleteContentRange": {"range": rng}})
            segments = parse_inline(unit["markdown"])
            if segments:
                reqs, new_idx = build_text_run_requests(segments, doc_start, target_tab_id)
                all_requests.extend(reqs)
                prior_state = _paragraph_state(op.get("old_para"))
                all_requests.extend(
                    style_requests_for_unit(unit, doc_start, new_idx, target_tab_id, prior_state)
                )

    result_base = {
        "changed": changed_count,
        "unchanged": unchanged_count,
        "ops": len(all_requests),
        "tab_id": target_tab_id,
    }

    if dry_run:
        return {**result_base, "dry_run": True}

    if all_requests:
        try:
            docs_service.documents().batchUpdate(
                documentId=doc_id, body={"requests": all_requests}
            ).execute()
        except Exception as e:
            raise ApiError(f"Smart update failed: {e}") from e

    return {**result_base, "dry_run": False}
