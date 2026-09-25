"""extract_text_and_warnings (the loud-failure fix for `get` silently
dropping tables/TOC/section-breaks) and convert_markdown_to_docx's
relative-image-path fix (pandoc must run from the source file's own
directory, not the caller's cwd)."""

import base64
import os

import pytest

from gdocs_md.commands import extract_text_and_warnings
from gdocs_md.docs_api import convert_markdown_to_docx
from gdocs_md.errors import MissingPandocError

# A 1x1 transparent PNG, for a real relative-image-path round trip.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_extract_text_and_warnings_plain_doc_has_no_warnings():
    content = [
        {"paragraph": {"elements": [{"textRun": {"content": "hello\n"}}]}},
    ]
    text, warnings = extract_text_and_warnings(content)
    assert text == "hello\n"
    assert warnings == []


def test_extract_text_and_warnings_flags_dropped_table():
    content = [
        {"paragraph": {"elements": [{"textRun": {"content": "before\n"}}]}},
        {"table": {"tableRows": []}},
        {"paragraph": {"elements": [{"textRun": {"content": "after\n"}}]}},
    ]
    text, warnings = extract_text_and_warnings(content)
    # Text still reads back for the paragraphs that ARE readable...
    assert text == "before\nafter\n"
    # ...but the drop is surfaced, not silent.
    assert len(warnings) == 1
    assert "table" in warnings[0]


def test_extract_text_and_warnings_flags_multiple_kinds_once_each():
    content = [
        {"table": {}},
        {"table": {}},
        {"tableOfContents": {}},
        {"paragraph": {"elements": []}},
    ]
    _, warnings = extract_text_and_warnings(content)
    assert len(warnings) == 2  # deduplicated per kind, not per occurrence


def test_extract_text_and_warnings_never_flags_a_leading_section_break():
    # Every real Docs body/tab body starts with a sectionBreak element
    # that carries no text at all. Warning on it made `warnings`
    # non-empty on every real `get`, which buried the table warning that
    # actually matters and meant an agent could never use `warnings == []`
    # as a signal. Positive control: reverting this (putting sectionBreak
    # back in the label map) makes this assert fail -- see
    # test_positive_control_section_break_would_warn_if_labeled below.
    content = [
        {"endIndex": 1, "sectionBreak": {"sectionStyle": {}}},
        {"paragraph": {"elements": [{"textRun": {"content": "hello\n"}}]}},
    ]
    _, warnings = extract_text_and_warnings(content)
    assert warnings == []


def test_positive_control_section_break_would_warn_if_labeled(monkeypatch):
    from gdocs_md import commands

    monkeypatch.setitem(commands._NON_PARAGRAPH_LABELS, "sectionBreak", "section break(s)")
    content = [
        {"endIndex": 1, "sectionBreak": {"sectionStyle": {}}},
        {"paragraph": {"elements": [{"textRun": {"content": "hello\n"}}]}},
    ]
    _, warnings = extract_text_and_warnings(content)
    assert warnings != []


@pytest.mark.skipif(not __import__("shutil").which("pandoc"), reason="pandoc not installed")
def test_convert_markdown_to_docx_resolves_relative_image_against_source_dir(tmp_path):
    subdir = tmp_path / "notes"
    subdir.mkdir()
    (subdir / "pic.png").write_bytes(_TINY_PNG)
    md_file = subdir / "doc.md"
    md_file.write_text("# Title\n\n![alt](pic.png)\n")

    # Run pandoc from an UNRELATED cwd -- if the fix regresses (pandoc run
    # from the caller's cwd instead of the source file's own directory),
    # this relative path fails to resolve and the image is silently
    # dropped instead.
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    old_cwd = os.getcwd()
    os.chdir(other_cwd)
    try:
        docx_path = convert_markdown_to_docx(md_file)
    finally:
        os.chdir(old_cwd)

    try:
        # A docx with the image embedded is meaningfully larger than one
        # with just the heading -- a dropped image collapses this file to
        # a few hundred bytes; a resolved one includes the PNG payload.
        assert os.path.getsize(docx_path) > len(_TINY_PNG)
    finally:
        os.unlink(docx_path)
