"""Regression tests for the three bugs fixed in the baseline this package
was built from. Each of these fails against the pre-fix code (see the
fails-first evidence file referenced in the build return) and passes here.
"""

from fake_docs_service import FakeDoc, FakeService

from gdocs_md.smart_update import smart_update_doc
from gdocs_md.tab_writer import write_tab_content


def test_insert_run_at_end_does_not_reverse_or_duplicate():
    """A large restructure that appends several new paragraphs at the tail
    must read back in the SAME order they were written, not reversed and
    duplicated. (Symptom: sections out of order, tail reversed/duplicated
    after a large restructure -- caused by a same-position tie-break that
    didn't account for batchUpdate's stack-like insert-at-same-index
    behavior.)"""
    doc = FakeDoc([("Intro", None), ("Body", None)])
    result = smart_update_doc(
        FakeService(doc), "doc-x", "Intro\n\nBody\n\nA\n\nB\n\nC\n\nD\n\nE\n"
    )
    assert result["changed"] >= 1
    assert doc.plain_texts() == ["Intro", "Body", "A", "B", "C", "D", "E"]


def test_insert_run_mid_doc_does_not_reverse():
    doc = FakeDoc([("Intro", None), ("Body", None)])
    smart_update_doc(FakeService(doc), "doc-x", "Intro\n\nX\n\nY\n\nZ\n\nBody\n")
    assert doc.plain_texts() == ["Intro", "X", "Y", "Z", "Body"]


def test_intraword_underscore_survives_a_replace():
    """A paragraph replace must not turn `snake_case_name` into emphasis --
    this exercises the fix through smart_update_doc's replace path, not
    just parse_inline in isolation."""
    doc = FakeDoc([("a", None), ("old text", None)])
    smart_update_doc(FakeService(doc), "doc-x", "a\n\nsome snake_case_name and ___ here\n")
    texts = doc.plain_texts()
    assert texts[1] == "some snake_case_name and ___ here"


def test_intraword_underscore_survives_a_tab_write():
    """The pre-fix baseline's regex had no word-boundary check: this exact
    line, run through write_tab_content, mangled to
    'some snakecasename and _ here' -- underscores inside identifiers were
    consumed as emphasis markers. This is the tab-rewrite path's version of
    the same fix (the fails-first evidence file has both directions run
    against the pre-fix script itself)."""
    doc = FakeDoc([])
    write_tab_content(FakeService(doc), "doc-x", "t.0", "some snake_case_name and ___ here\n")
    assert doc.plain_texts()[0] == "some snake_case_name and ___ here"


def test_blockquote_before_tail_paragraphs_diffs_correctly():
    """Before the fix, a multi-line blockquote shifted block_map so every
    paragraph after it drew formatting from the wrong block. Editing just
    the tail paragraph after a blockquote must touch only that paragraph."""
    doc = FakeDoc(
        [
            ("Part 2", {"style": "HEADING_1"}),
            ("Say this first.", None),
            ("Then say this.", None),
            ("Old para", None),
        ]
    )
    md = (
        "# Part 2\n\n> Say this first.\n> Then say this.\n\nNew para\n\n"
        "*trailing.*\n"
    )
    result = smart_update_doc(FakeService(doc), "doc-x", md)
    texts = doc.plain_texts()
    assert texts[0] == "Part 2"
    assert texts[1] == "Say this first."
    assert texts[2] == "Then say this."
    assert texts[3] == "New para"
    assert texts[4] == "trailing."
    # The blockquote lines and the heading are equal and must not get
    # caught up in a misaligned diff.
    assert result["unchanged"] >= 1
