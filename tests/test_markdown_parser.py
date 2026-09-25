"""Inline formatting and block parsing, independent of any Docs API calls."""

from gdocs_md.markdown_parser import flatten_for_diff, parse_inline, parse_markdown_blocks, strip_inline


# ---------------------------------------------------------------------------
# Regression: intraword underscore emphasis must NOT fire.
# CommonMark suppresses `_..._` emphasis when it would split a word; the
# baseline's original regexes had no word-boundary check and mangled
# identifiers like snake_case names and bare underscore runs.
# ---------------------------------------------------------------------------


def test_snake_case_untouched():
    assert parse_inline("snake_case_name") == [("snake_case_name", {})]
    assert strip_inline("snake_case_name") == "snake_case_name"


def test_bare_underscore_run_untouched():
    for run in ("___", "_____"):
        assert parse_inline(run) == [(run, {})]
        assert strip_inline(run) == run


def test_number_with_underscores_untouched():
    assert parse_inline("1_000_000") == [("1_000_000", {})]


def test_mixed_word_and_emphasis_in_one_line():
    segs = parse_inline("file_name.md and _it_")
    text = "".join(t for t, _ in segs)
    assert text == "file_name.md and it"
    styled = [t for t, st in segs if st.get("italic")]
    assert styled == ["it"]


def test_word_boundary_underscore_emphasis_still_works():
    assert parse_inline("_it_") == [("it", {"italic": True})]
    assert parse_inline("__bold__") == [("bold", {"bold": True})]
    assert parse_inline("___both___") == [("both", {"bold": True, "italic": True})]


def test_asterisk_emphasis_stays_permissive():
    # Asterisk emphasis has no CommonMark word-boundary rule -- unchanged.
    assert parse_inline("**bold**") == [("bold", {"bold": True})]
    assert parse_inline("*it*") == [("it", {"italic": True})]


def test_code_span_untouched_by_underscore_fix():
    assert parse_inline("`co_de_x`") == [("co_de_x", {"weightedFontFamily": {"fontFamily": "Courier New"}})]


# ---------------------------------------------------------------------------
# Regression: block-map misalignment. A multi-line blockquote is ONE block
# from parse_markdown_blocks but ONE paragraph PER LINE in the doc; a table
# is one block but one paragraph per row. Diffing must use the same
# flattened unit count both parsers used to disagree on.
# ---------------------------------------------------------------------------


def test_blockquote_flattens_to_one_unit_per_line():
    md = (
        "# Part 2\n"
        "\n"
        "> Say this first.\n"
        "> Then say this.\n"
        "\n"
        "Old para\n"
        "\n"
        "New one\n"
        "\n"
        "*This is a working document.*\n"
    )
    units = flatten_for_diff(parse_markdown_blocks(md))
    plain = [u["plain"] for u in units]
    assert plain == [
        "Part 2",
        "Say this first.",
        "Then say this.",
        "Old para",
        "New one",
        "This is a working document.",
    ]
    # Formatting metadata must line up with the SAME index -- the whole
    # point of flatten_for_diff being the single source of truth.
    assert units[0]["block"]["style"] == "HEADING_1"
    assert units[1]["kind"] == "blockquote_line"
    assert units[5]["markdown"] == "*This is a working document.*"


def test_table_flattens_to_one_unit_per_row_not_one_per_block():
    md = "Intro\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nTail one\n\nTail two\n"
    blocks = parse_markdown_blocks(md)
    # One 'table' block in the parse...
    assert sum(1 for b in blocks if b["type"] == "table") == 1
    # ...but TWO diff units (header row + one data row), matching what the
    # doc-side writer actually produces (one paragraph per row).
    units = flatten_for_diff(blocks)
    plain = [u["plain"] for u in units]
    assert plain == ["Intro", "a  b", "1  2", "Tail one", "Tail two"]
    # Everything after the table must draw from the RIGHT block -- this is
    # the exact misalignment the fix closes: before it, "Tail one" and
    # "Tail two" would have picked up formatting metadata shifted by one.
    assert units[3]["block"]["text"] == "Tail one"
    assert units[4]["block"]["text"] == "Tail two"
