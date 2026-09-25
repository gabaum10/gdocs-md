"""Incremental-update path: small edits, inserts, deletes land correctly,
and a second run of the same markdown against the result is zero ops (it
converges)."""

from fake_docs_service import FakeDoc, FakeService

from gdocs_md.smart_update import smart_update_doc


def _run_twice(doc, md):
    svc = FakeService(doc)
    first = smart_update_doc(svc, "doc-x", md)
    second = smart_update_doc(svc, "doc-x", md)
    return first, second


def test_single_small_insert_converges():
    doc = FakeDoc([("Intro", None), ("Body", None)])
    first, second = _run_twice(doc, "Intro\n\nX\n\nBody\n")
    assert doc.plain_texts() == ["Intro", "X", "Body"]
    assert first["ops"] > 0
    assert second["changed"] == 0
    assert second["ops"] == 0


def test_delete_middle_converges():
    doc = FakeDoc([(t, None) for t in ("a", "b", "c", "d")])
    first, second = _run_twice(doc, "a\n\nd\n")
    assert doc.plain_texts() == ["a", "d"]
    assert first["ops"] > 0
    assert second["changed"] == 0


def test_delete_last_paragraph_falls_back_to_content_only():
    """Deleting the doc's own last paragraph can't remove its trailing
    newline (immutable) -- content-only deletion leaves one empty
    paragraph, a known, documented limitation, not a crash."""
    doc = FakeDoc([("a", None), ("b", None)])
    result = smart_update_doc(FakeService(doc), "doc-x", "a\n")
    assert result["ops"] > 0
    texts = doc.plain_texts()
    assert texts[0] == "a"
    assert texts[-1] == ""


def test_in_place_edit_converges():
    doc = FakeDoc([("intro", None), ("one", None), ("two", None), ("three", None)])
    first, second = _run_twice(doc, "intro\n\none\n\nTWO edited\n\nthree\n")
    assert doc.plain_texts() == ["intro", "one", "TWO edited", "three"]
    assert first["changed"] >= 1
    assert second["changed"] == 0


def test_mixed_replace_insert_delete_converges():
    doc = FakeDoc([(t, None) for t in ("a", "b", "c", "d", "e")])
    md = "a\n\nB2\n\nnew1\n\nnew2\n\nd\n\ne\n\nf\n\ng\n"
    first, second = _run_twice(doc, md)
    assert doc.plain_texts() == ["a", "B2", "new1", "new2", "d", "e", "f", "g"]
    assert second["changed"] == 0
    assert second["ops"] == 0


def test_no_op_update_reports_zero_changed_without_touching_service():
    doc = FakeDoc([("a", None), ("b", None)])
    result = smart_update_doc(FakeService(doc), "doc-x", "a\n\nb\n")
    assert result == {"changed": 0, "unchanged": 1, "ops": 0, "tab_id": "t.0", "dry_run": False}


def test_dry_run_computes_but_does_not_write():
    doc = FakeDoc([("a", None), ("b", None)])
    before = doc.plain_texts()
    result = smart_update_doc(FakeService(doc), "doc-x", "a\n\nX\n\nb\n", dry_run=True)
    assert result["dry_run"] is True
    assert result["ops"] > 0
    assert doc.plain_texts() == before


def test_br_paragraph_converges():
    """L7: a <br> paragraph must reach 0 ops on a second identical run, not
    get deleted and reinserted every time (which kills any comment
    anchored there)."""
    doc = FakeDoc([("a", None), ("old", None)])
    md = "a\n\nline one<br>line two\n"
    first, second = _run_twice(doc, md)
    assert first["ops"] > 0
    assert second["changed"] == 0
    assert second["ops"] == 0
    assert doc.plain_texts()[1] == "line one\x0bline two"


def test_positive_control_br_as_space_would_never_converge(monkeypatch):
    """Revert the fix (map <br> to a plain space instead of \\v, matching
    what parse_inline actually writes) and confirm the SAME paragraph
    then never converges -- proving the mapping mismatch, not something
    else, was the cause."""
    import re as re_module

    from gdocs_md import markdown_parser

    def buggy_strip_inline(text):
        text = markdown_parser._BOLD_ITALIC_STAR_RE.sub(lambda m: m.group(1), text)
        text = markdown_parser._BOLD_ITALIC_UNDER_RE.sub(lambda m: m.group(1), text)
        text = markdown_parser._BOLD_STAR_RE.sub(lambda m: m.group(1), text)
        text = markdown_parser._BOLD_UNDER_RE.sub(lambda m: m.group(1), text)
        text = markdown_parser._ITALIC_STAR_RE.sub(lambda m: m.group(1), text)
        text = markdown_parser._ITALIC_UNDER_RE.sub(lambda m: m.group(1), text)
        text = markdown_parser._CODE_RE.sub(lambda m: m.group(1), text)
        text = text.replace("\\", "")
        text = re_module.sub(r"<[Bb][Rr]>", " ", text)  # pre-fix mapping
        return text

    monkeypatch.setattr(markdown_parser, "strip_inline", buggy_strip_inline)

    doc = FakeDoc([("a", None), ("old", None)])
    md = "a\n\nline one<br>line two\n"
    first, second = _run_twice(doc, md)
    assert second["changed"] != 0  # never converges under the buggy mapping
