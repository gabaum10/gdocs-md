"""L1: stacked inserts at the same document position must each judge
their own style/bullet decision against where they ACTUALLY land (inside
the paragraph the previous insert in the same run just wrote), not against
the original doc's neighbour paragraph, which by the second insert in the
run no longer touches that position at all.

Every assertion here checks STATE (style/bullet), not just text -- a
pre-fix run would still read back the right TEXT in the right order (that
part was already fixed); the bug was specifically the wrong formatting on
paragraphs after the first in a stacked run, and a second run converging
to 0 ops means the wrong state is never repaired.
"""

from fake_docs_service import FakeDoc, FakeService

from gdocs_md.smart_update import smart_update_doc

H2 = {"style": "HEADING_2"}


def _edit_and_reedit(doc, md):
    svc = FakeService(doc)
    first = smart_update_doc(svc, "doc-x", md)
    second = smart_update_doc(svc, "doc-x", md)
    return first, second


def test_stacked_heading_and_paragraph_before_an_existing_heading():
    doc = FakeDoc([("Title", {"style": "HEADING_1"}), ("Intro", None), ("Old", H2), ("body", None)])
    md = "# Title\n\nIntro\n\n## New\n\nnew body\n\n## Old\n\nbody\n"
    first, second = _edit_and_reedit(doc, md)
    paras = {t: st for t, st in doc.paras()}
    assert paras["New"]["style"] == "HEADING_2"
    assert paras["new body"]["style"] == "NORMAL_TEXT"
    assert paras["Old"]["style"] == "HEADING_2"
    assert second["changed"] == 0  # converges -- nothing left to repair


def test_stacked_paragraph_and_list_item_before_a_plain_paragraph():
    doc = FakeDoc([("Intro", None), ("Tail", None)])
    md = "Intro\n\nLead-in:\n\n- item one\n\nTail\n"
    first, second = _edit_and_reedit(doc, md)
    paras = {t: st for t, st in doc.paras()}
    assert paras["Lead-in:"]["bullet"] is None
    assert paras["Lead-in:"]["style"] == "NORMAL_TEXT"
    assert paras["item one"]["bullet"] is not None
    assert second["changed"] == 0


def test_stacked_paragraph_and_heading_before_a_plain_paragraph():
    doc = FakeDoc([("Intro", None), ("Tail", None)])
    md = "Intro\n\nplain first\n\n## Heading\n\nTail\n"
    first, second = _edit_and_reedit(doc, md)
    paras = {t: st for t, st in doc.paras()}
    assert paras["plain first"]["style"] == "NORMAL_TEXT"
    assert paras["Heading"]["style"] == "HEADING_2"
    assert second["changed"] == 0


def test_three_item_list_inserted_between_two_plain_paragraphs_joins_one_list():
    """The regression oracle here is FakeDoc.apply itself: it raises if
    createParagraphBullets is ever called on an already-bulleted paragraph
    (F1's exact failure, reproduced on the insert side by L1 -- the second
    and third list items in the stack land inside the first item's
    freshly-written paragraph, which style_requests_for_unit had ALREADY
    decided was bulleted one op ago)."""
    doc = FakeDoc([("Intro", None), ("Tail", None)])
    md = "Intro\n\n- one\n- two\n- three\n\nTail\n"
    smart_update_doc(FakeService(doc), "doc-x", md)  # must not raise
    paras = {t: st for t, st in doc.paras()}
    ids = {paras["one"]["bullet"]["listId"], paras["two"]["bullet"]["listId"], paras["three"]["bullet"]["listId"]}
    assert len(ids) == 1  # all three joined the SAME list, not three separate ones


def test_two_headings_stacked_before_an_existing_heading():
    doc = FakeDoc([("Intro", None), ("Old", H2)])
    md = "Intro\n\n## New A\n\n## New B\n\n## Old\n"
    first, second = _edit_and_reedit(doc, md)
    paras = {t: st for t, st in doc.paras()}
    assert paras["New A"]["style"] == "HEADING_2"
    assert paras["New B"]["style"] == "HEADING_2"
    assert paras["Old"]["style"] == "HEADING_2"
    assert second["changed"] == 0


def test_clamped_stacked_append_at_doc_end_is_unaffected():
    """A clamped run (appending past the doc's own last
    paragraph) splits that SAME last paragraph for every op in the run --
    there's no "previous op's freshly-written paragraph" to land inside,
    so this path was already correct before the L1 fix and must stay so."""
    doc = FakeDoc([("Title", {"style": "HEADING_1"}), ("Body", None)])
    md = "# Title\n\nBody\n\nA\n\nB\n\nC\n"
    smart_update_doc(FakeService(doc), "doc-x", md)
    paras = {t: st for t, st in doc.paras()}
    for t in ("A", "B", "C"):
        assert paras[t]["style"] == "NORMAL_TEXT"
    assert doc.plain_texts() == ["Title", "Body", "A", "B", "C"]


def test_positive_control_disabling_the_chain_reproduces_l1(monkeypatch):
    """Disable the L1 chaining decision (force it to always take the
    fresh-anchor path, the pre-fix behavior) and confirm the stacked-insert
    case then reads back the WRONG style -- proving the tests above
    actually depend on `_chain_applies`, not on some other accidental
    correctness."""
    from gdocs_md import smart_update

    monkeypatch.setattr(smart_update, "_chain_applies", lambda prev, cur: False)

    doc = FakeDoc([("Title", {"style": "HEADING_1"}), ("Intro", None), ("Old", H2), ("body", None)])
    md = "# Title\n\nIntro\n\n## New\n\nnew body\n\n## Old\n\nbody\n"
    smart_update_doc(FakeService(doc), "doc-x", md)
    paras = {t: st for t, st in doc.paras()}
    # Pre-fix: 'New' loses its heading style because the second-applied op
    # in the stack judged itself against the stale original anchor.
    assert paras["New"]["style"] != "HEADING_2"
