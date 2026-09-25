"""L2: a bulleted paragraph's own list indent must not be read/reset as
this tool's separate blockquote-indent convention, in either direction.

Horn (a): the Docs API reports indentStart/indentFirstLine on a bulleted
paragraph too (its own list indentation), not just on this tool's
blockquote paragraphs. Before the fix, editing an existing list item's
text emitted a spurious `indentStart: 0` reset on every edit, because the
extraction read that indentStart as "this needs resetting to plain" --
nestingLevel/listId (what the live F1 gate reads) don't show this at all.

Horn (b): converting a list item to a plain paragraph
(deleteParagraphBullets) leaves the API's own visual indent behind --
nothing else clears it, and the paragraph stays permanently indented
because the next diff run sees equal text and does 0 ops.
"""

from fake_docs_service import FakeDoc, FakeService

from gdocs_md.smart_update import extract_paragraphs_with_positions, smart_update_doc


def _requests_of_kind(recorder, *kinds):
    return [r for r in recorder if list(r)[0] in kinds]


class _RecordingService(FakeService):
    """Wraps FakeService.documents().batchUpdate to also record every
    request issued, in order, so a test can assert the ABSENCE of a
    request kind (e.g. "no indent reset happened"), not just the resulting
    state."""

    def __init__(self, doc):
        super().__init__(doc)
        self.requests = []

    def documents(self):
        docs = super().documents()
        original_batch_update = docs.batchUpdate

        def batch_update(documentId=None, body=None):
            self.requests.extend(body["requests"])
            return original_batch_update(documentId=documentId, body=body)

        docs.batchUpdate = batch_update
        return docs


def test_extraction_masks_list_indent_as_not_this_tools_blockquote_indent():
    doc = {
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 5,
                    "paragraph": {
                        "elements": [{"textRun": {"content": "one\n"}}],
                        "paragraphStyle": {
                            "namedStyleType": "NORMAL_TEXT",
                            "indentStart": {"magnitude": 18, "unit": "PT"},
                        },
                        "bullet": {"listId": "kix.a", "nestingLevel": 0},
                    },
                }
            ]
        }
    }
    paras = extract_paragraphs_with_positions(doc)
    assert paras[0]["bullet"] is True
    assert paras[0]["indent"] is False  # masked -- this is list indent, not blockquote indent


def test_editing_a_list_item_emits_no_indent_reset_horn_a():
    doc = FakeDoc(
        [
            ("intro", None),
            ("one", {"bullet": {"listId": "kix.a", "nestingLevel": 0}, "indent": True}),
            ("two", {"bullet": {"listId": "kix.a", "nestingLevel": 0}, "indent": True}),
            ("three", {"bullet": {"listId": "kix.a", "nestingLevel": 0}, "indent": True}),
        ]
    )
    svc = _RecordingService(doc)
    smart_update_doc(svc, "doc-x", "intro\n\n1. one\n2. TWO edited\n3. three\n")
    indent_ops = _requests_of_kind(svc.requests, "updateParagraphStyle")
    indent_ops = [op for op in indent_ops if "indentStart" in op["updateParagraphStyle"].get("paragraphStyle", {})]
    assert indent_ops == []  # no indent op at all -- not even a no-op one


def test_converting_a_list_item_to_plain_paragraph_resets_indent_horn_b():
    doc = FakeDoc([("intro", None), ("old item", {"bullet": {"listId": "kix.a", "nestingLevel": 0}, "indent": True})])
    svc = _RecordingService(doc)
    smart_update_doc(svc, "doc-x", "intro\n\nnow plain\n")
    kinds = [list(r)[0] for r in svc.requests]
    assert "deleteParagraphBullets" in kinds
    indent_reset = [
        r
        for r in svc.requests
        if "updateParagraphStyle" in r
        and r["updateParagraphStyle"]["paragraphStyle"].get("indentStart", {}).get("magnitude") == 0
    ]
    assert len(indent_reset) == 1
    # And the fake's own resulting state actually cleared it too.
    paras = {t: st for t, st in doc.paras()}
    assert paras["now plain"]["indent"] is False


def test_extraction_mask_matters_when_inserting_a_blockquote_next_to_a_bulleted_anchor():
    """The `not target["bullet"]` guard in style_requests_for_unit alone
    is enough to stop horn (a)'s spurious reset on a list-item-stays-a-
    list-item edit (see test_editing_a_list_item_emits_no_indent_reset_
    horn_a) -- that guard fires regardless of what the extraction mask
    says. The extraction mask (`indent = magnitude>0 and not bullet`)
    earns its own keep in a DIFFERENT case: inserting a genuine blockquote
    line whose anchor is a bulleted, indented paragraph. Without the mask,
    the anchor's (list) indent would be misread as "this new blockquote
    paragraph already has the indent it needs", and the real indentStart:
    36 request that actually establishes the blockquote's own indent gets
    skipped."""
    from gdocs_md.smart_update import style_requests_for_unit

    anchor_masked = {"style": "NORMAL_TEXT", "bullet": True, "indent": False}  # current (fixed) extraction
    anchor_unmasked = {"style": "NORMAL_TEXT", "bullet": True, "indent": True}  # pre-fix extraction

    unit = {
        "kind": "blockquote_line",
        "block": {"type": "blockquote", "lines": ["quoted"]},
    }

    fixed_reqs = style_requests_for_unit(unit, 10, 20, None, anchor_masked)
    assert any(
        "indentStart" in r.get("updateParagraphStyle", {}).get("paragraphStyle", {})
        and r["updateParagraphStyle"]["paragraphStyle"]["indentStart"]["magnitude"] == 36
        for r in fixed_reqs
    )

    buggy_reqs = style_requests_for_unit(unit, 10, 20, None, anchor_unmasked)
    assert not any(
        "indentStart" in r.get("updateParagraphStyle", {}).get("paragraphStyle", {})
        and r["updateParagraphStyle"]["paragraphStyle"]["indentStart"]["magnitude"] == 36
        for r in buggy_reqs
    )  # the real blockquote indent never gets set -- the anchor's LIST indent was misread as already-satisfying it
