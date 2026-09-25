"""Three fixes to the diff-based (smart) update path, referred to elsewhere
in this codebase by the finding numbers from the review that caught them:
F1, F2, F3.

F1 (blocks): editing an existing list item must not re-bullet it -- doing
so mints a new list and can flatten nesting on a real doc. Preserving
listId/nestingLevel means never calling createParagraphBullets on a
paragraph that's already bulleted.

F2: a pure-paragraph delete whose next body element isn't itself a
paragraph (a table/TOC/section-break) must not try to delete through that
paragraph's own trailing newline -- DeleteContentRangeRequest rejects that.

F3: style/bullet/indent resets must fire in BOTH directions -- moving into
a style and moving out of one -- not just the "into" direction.
"""

from fake_docs_service import FakeDoc, FakeService

from gdocs_md.smart_update import (
    _paragraph_state,
    extract_paragraphs_with_positions,
    smart_update_doc,
    style_requests_for_unit,
)


# ---------------------------------------------------------------------------
# F1: bullet identity survives an edit.
# ---------------------------------------------------------------------------


def test_editing_a_numbered_list_item_preserves_list_identity():
    doc = FakeDoc(
        [
            ("intro", None),
            ("one", {"bullet": {"listId": "L-existing", "nestingLevel": 0}}),
            ("two", {"bullet": {"listId": "L-existing", "nestingLevel": 0}}),
            ("three", {"bullet": {"listId": "L-existing", "nestingLevel": 0}}),
        ]
    )
    md = "intro\n\n1. one\n2. TWO edited\n3. three\n"
    # FakeDoc.apply raises AssertionError if createParagraphBullets is ever
    # called on an already-bulleted paragraph -- that's the oracle. A
    # regression here raises instead of silently mis-bulleting.
    smart_update_doc(FakeService(doc), "doc-x", md)
    paras = doc.paras()
    assert paras[2][0] == "TWO edited"
    assert paras[2][1]["bullet"] == {"listId": "L-existing", "nestingLevel": 0}
    # Untouched siblings keep their identity too.
    assert paras[1][1]["bullet"]["listId"] == "L-existing"
    assert paras[3][1]["bullet"]["listId"] == "L-existing"


def test_editing_a_nested_sub_item_preserves_nesting_level():
    doc = FakeDoc(
        [
            ("one", {"bullet": {"listId": "L1", "nestingLevel": 0}}),
            ("sub-item", {"bullet": {"listId": "L1", "nestingLevel": 1}}),
            ("two", {"bullet": {"listId": "L1", "nestingLevel": 0}}),
        ]
    )
    md = "1. one\n2. sub-item EDITED\n3. two\n"
    smart_update_doc(FakeService(doc), "doc-x", md)
    paras = doc.paras()
    edited = [p for p in paras if p[0] == "sub-item EDITED"][0]
    assert edited[1]["bullet"] == {"listId": "L1", "nestingLevel": 1}


def test_new_list_item_still_gets_bulleted():
    doc = FakeDoc([("intro", None)])
    smart_update_doc(FakeService(doc), "doc-x", "intro\n\n- first item\n")
    paras = doc.paras()
    new_item = [p for p in paras if p[0] == "first item"][0]
    assert new_item[1]["bullet"] is not None
    assert new_item[1]["bullet"]["nestingLevel"] == 0


# ---------------------------------------------------------------------------
# F2: delete adjacency to a non-paragraph element.
# ---------------------------------------------------------------------------


def test_next_is_paragraph_true_between_two_paragraphs():
    doc = {
        "body": {
            "content": [
                {"startIndex": 1, "endIndex": 5, "paragraph": {"elements": [{"textRun": {"content": "a\n"}}]}},
                {"startIndex": 5, "endIndex": 9, "paragraph": {"elements": [{"textRun": {"content": "b\n"}}]}},
            ]
        }
    }
    paras = extract_paragraphs_with_positions(doc)
    assert paras[0]["next_is_paragraph"] is True


def test_next_is_paragraph_false_before_a_table():
    doc = {
        "body": {
            "content": [
                {"startIndex": 1, "endIndex": 5, "paragraph": {"elements": [{"textRun": {"content": "a\n"}}]}},
                {"startIndex": 5, "endIndex": 20, "table": {"tableRows": []}},
            ]
        }
    }
    paras = extract_paragraphs_with_positions(doc)
    assert paras[0]["next_is_paragraph"] is False


class _AssertingService:
    """Executes exactly one batchUpdate and asserts none of its
    deleteContentRange requests reach into the table's own range -- the
    real API's rejection, modeled as an assertion instead of an HttpError so
    the test fails loudly if the fix regresses."""

    def __init__(self, doc, table_start):
        self._doc = doc
        self._table_start = table_start

    def documents(self):
        return self

    def get(self, **_kwargs):
        return self

    def execute_get(self):
        return self._doc

    def batchUpdate(self, documentId=None, body=None):
        for req in body["requests"]:
            if "deleteContentRange" in req:
                rng = req["deleteContentRange"]["range"]
                assert rng["endIndex"] <= self._table_start, (
                    f"deleteContentRange {rng} reaches into/through the table "
                    f"starting at {self._table_start} -- the real API rejects "
                    "deleting the newline immediately before a table without "
                    "deleting the table itself."
                )
        self._last_body = body
        return self

    def execute(self):
        if hasattr(self, "_last_body"):
            return {"replies": []}
        return self._doc


def test_delete_before_table_falls_back_to_content_only():
    # doc: "keep\n" (1..6) "drop me\n" (6..14) then a table (14..40) then a
    # trailing paragraph (40..41, the doc's own last paragraph).
    doc = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {
                    "body": {
                        "content": [
                            {"startIndex": 1, "endIndex": 6, "paragraph": {"elements": [{"textRun": {"content": "keep\n"}}]}},
                            {"startIndex": 6, "endIndex": 14, "paragraph": {"elements": [{"textRun": {"content": "drop me\n"}}]}},
                            {"startIndex": 14, "endIndex": 40, "table": {"tableRows": []}},
                            {"startIndex": 40, "endIndex": 41, "paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
                        ]
                    }
                },
            }
        ]
    }
    svc = _AssertingService(doc, table_start=14)
    smart_update_doc(svc, "doc-x", "keep\n")


# ---------------------------------------------------------------------------
# F3: style/bullet/indent resets fire in both directions.
# ---------------------------------------------------------------------------


def test_replacing_a_heading_with_plain_text_resets_style():
    doc = FakeDoc([("Old head", {"style": "HEADING_2"}), ("body", None)])
    smart_update_doc(FakeService(doc), "doc-x", "now normal\n\nbody\n")
    paras = doc.paras()
    assert paras[0][0] == "now normal"
    assert paras[0][1]["style"] == "NORMAL_TEXT"


def test_replacing_a_bulleted_item_with_plain_text_removes_bullet():
    doc = FakeDoc([("intro", None), ("old item", {"bullet": {"listId": "L1", "nestingLevel": 0}})])
    smart_update_doc(FakeService(doc), "doc-x", "intro\n\nno longer a list item\n")
    paras = doc.paras()
    edited = [p for p in paras if p[0] == "no longer a list item"][0]
    assert edited[1]["bullet"] is None


def test_replacing_a_blockquote_line_with_plain_text_resets_indent():
    doc = FakeDoc([("quoted line", {"indent": True}), ("body", None)])
    smart_update_doc(FakeService(doc), "doc-x", "no longer quoted\n\nbody\n")
    paras = doc.paras()
    assert paras[0][0] == "no longer quoted"
    assert paras[0][1]["indent"] is False


def test_inserting_a_heading_before_a_normal_paragraph_does_not_bleed_style():
    doc = FakeDoc([("Intro", None), ("Sec", {"style": "HEADING_2"}), ("x", None)])
    smart_update_doc(FakeService(doc), "doc-x", "Intro\n\nadded\n\n## Sec\n\nx\n")
    paras = doc.paras()
    added = [p for p in paras if p[0] == "added"][0]
    assert added[1]["style"] == "NORMAL_TEXT"


def test_style_requests_for_unit_skips_when_state_already_matches():
    unit = {"kind": "list_item", "block": {"type": "list_item", "list_type": "unordered"}}
    prior = {"style": "NORMAL_TEXT", "bullet": True, "indent": False}
    reqs = style_requests_for_unit(unit, 1, 5, None, prior)
    assert reqs == []


def test_paragraph_state_default_for_no_anchor():
    assert _paragraph_state(None) == {"style": "NORMAL_TEXT", "bullet": False, "indent": False}
