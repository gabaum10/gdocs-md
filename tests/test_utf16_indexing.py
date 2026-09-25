"""UTF-16 code-unit indexing.

Google Docs indexes every position in UTF-16 code units, not Python code
points. An astral character (most emoji, e.g. U+1F3A4 microphone) is ONE
Python character but TWO UTF-16 units (a surrogate pair). Any index that
advances by `len(text)` after inserting text containing one silently drifts
every position after it by one unit per astral character -- "🎤 Say" (an
emoji, a space, then "Say") lands as "🎤 Sa" because the index thought the
emoji consumed one slot instead of two, so the last unit of whatever ran
through that math falls outside the range actually written.

`fake_docs_service.FakeDoc` itself indexes in UTF-16 (see its module
docstring), so any test here would already fail if that model were
Python-length-based -- these tests exercise the package's own index math,
not just check it against a permissive fake.
"""

import pytest

from fake_docs_service import FakeDoc, FakeService

from gdocs_md import indexing
from gdocs_md.smart_update import smart_update_doc
from gdocs_md.tab_writer import write_tab_content

MIC = "\U0001F3A4"  # microphone, astral -- 2 UTF-16 units
FACE = "\U0001F600"  # grinning face, astral
ACCENTED = "café"  # BMP non-ASCII, 1 unit per char -- should never need this fix
CJK = "文書"  # BMP non-ASCII (CJK), 1 unit per char


def test_tab_write_preserves_text_after_an_emoji():
    doc = FakeDoc([])
    write_tab_content(FakeService(doc), "doc-x", "t.0", f"{MIC} Say hello\n\nSecond paragraph\n")
    texts = doc.plain_texts()
    assert texts[0] == f"{MIC} Say hello"
    assert texts[1] == "Second paragraph"


def test_tab_write_styled_run_after_emoji_lands_on_the_right_text():
    doc = FakeDoc([])
    write_tab_content(FakeService(doc), "doc-x", "t.0", f"{MIC} **bold word** after\n")
    assert doc.plain_texts()[0] == f"{MIC} bold word after"


def test_smart_diff_insert_with_emoji_reads_back_exact():
    doc = FakeDoc([("Intro", None), ("Body", None)])
    smart_update_doc(FakeService(doc), "doc-x", f"Intro\n\n{MIC} new section\n\nBody\n")
    assert doc.plain_texts() == ["Intro", f"{MIC} new section", "Body"]


def test_smart_diff_replace_with_emoji_and_bold_reads_back_exact():
    doc = FakeDoc([("a", None), ("old text", None)])
    smart_update_doc(FakeService(doc), "doc-x", f"a\n\n{FACE} some **bold** text {MIC} tail\n")
    assert doc.plain_texts()[1] == f"{FACE} some bold text {MIC} tail"


def test_smart_diff_delete_around_emoji_paragraph_does_not_corrupt_survivors():
    doc = FakeDoc([("a", None), (f"{MIC} drop me", None), ("survivor with trailing text", None)])
    smart_update_doc(FakeService(doc), "doc-x", "a\n\nsurvivor with trailing text\n")
    assert doc.plain_texts() == ["a", "survivor with trailing text"]


def test_mixed_bmp_non_ascii_and_astral_in_one_paragraph():
    text = f"{ACCENTED} {CJK} {MIC} tail text that must survive"
    doc = FakeDoc([("a", None), ("old", None)])
    smart_update_doc(FakeService(doc), "doc-x", f"a\n\n{text}\n")
    assert doc.plain_texts()[1] == text


def test_multiple_astral_characters_in_one_run():
    text = f"{MIC}{FACE}{MIC} three astral chars then real words follow after"
    doc = FakeDoc([("a", None), ("old", None)])
    smart_update_doc(FakeService(doc), "doc-x", f"a\n\n{text}\n")
    assert doc.plain_texts()[1] == text


# ---------------------------------------------------------------------------
# Positive control: prove these tests actually catch the bug they're named
# for, by reverting the fix (monkeypatching utf16_len back to Python len())
# and confirming the exact same scenario then reads back WRONG.
# ---------------------------------------------------------------------------


def test_positive_control_len_based_indexing_mangles_emoji_text(monkeypatch):
    monkeypatch.setattr(indexing, "utf16_len", len)
    # markdown_parser imported utf16_len by name at module load time, so the
    # patch has to land on the name it actually calls.
    import gdocs_md.markdown_parser as markdown_parser

    monkeypatch.setattr(markdown_parser, "utf16_len", len)

    doc = FakeDoc([("Intro", None), ("Body", None)])
    smart_update_doc(FakeService(doc), "doc-x", f"Intro\n\n{MIC} new section\n\nBody\n")
    texts = doc.plain_texts()
    # Under Python-len()-based indexing this does NOT read back correctly --
    # if it does, the control has stopped controlling anything and the real
    # tests above are not exercising what they claim to.
    assert texts != ["Intro", f"{MIC} new section", "Body"]
