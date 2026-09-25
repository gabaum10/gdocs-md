"""UTF-16 index arithmetic.

The Google Docs API indexes every position (insertText locations, range
startIndex/endIndex) in UTF-16 code units, not Unicode code points. Python's
`len()` on a `str` counts code points. Those agree for the BMP (ASCII,
accented Latin, CJK -- one code point, one UTF-16 unit each) but not for
anything outside it: an astral character (most emoji, some CJK extension
characters) is one Python character but *two* UTF-16 code units (a surrogate
pair). Advancing an insertion index by `len(text)` after inserting text that
contains such a character silently drifts every index after it by one unit
per astral character -- "\U0001F3A4 Say" (an emoji followed by "Say") lands
as "\U0001F3A4 Sa" because the trailing "y" never gets written: the index
math thought the emoji only consumed one slot, so everything downstream is
off by one and the last unit of the run falls outside the range actually
written.

`utf16_len` is the one place that conversion happens. Every function in this
package that advances a Docs-API index by the length of a text segment it is
about to insert must route through it instead of `len()`.

Audited call sites (see the build return for the file:function list): the
only place that ever advanced an index by `len(segment_text)` was
`markdown_parser.build_text_run_requests` -- every other index used in this
package (paragraph startIndex/endIndex from `documents().get()`, the
smart-diff insertion anchors derived from those, the clamp against
doc_end_index) is either read directly from the Docs API response (already
UTF-16-native) or arithmetic on such values (+1/-1), never a `len()` of
arbitrary text. Fixing `build_text_run_requests` therefore fixes every
consumer downstream of it (style ranges, text-run ranges, the smart-diff
insert/replace paths, table-cell fills) transitively, since they all thread
the index it returns rather than recomputing their own.
"""

from __future__ import annotations


def utf16_len(text: str) -> int:
    """Return the length of `text` in UTF-16 code units (what the Docs API
    counts), not Python code points."""
    return len(text.encode("utf-16-le")) // 2
