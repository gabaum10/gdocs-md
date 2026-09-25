"""A fake Google Docs service that applies batchUpdate requests under the
real API's index rules -- in particular, UTF-16 code-unit indexing, not
Python string length. No network. Used by every test that exercises
smart_update_doc / write_tab_content against something that behaves like
the real API closely enough to catch index-math bugs.

Modeled on the toy Docs simulators built during diagnosis (a per-unit list
with paragraph state attached to each '\\n' unit), extended to:
  - index in UTF-16 code units (an astral character, e.g. most emoji, is
    two units) instead of Python characters -- the whole reason this file
    exists as its own module instead of a one-off in a single test file.
  - track bullet identity (listId/nestingLevel) as a dict, not a bare
    preset string, and assert loudly if createParagraphBullets is ever
    called on a paragraph that's already bulleted -- that assertion is the
    regression oracle for the "bullet identity" fix: code that regresses
    to always re-bulleting an edited list item makes this fake service
    raise, not silently drift.
  - track paragraph style and this tool's blockquote indent convention the
    same way real paragraphStyle does, so style/indent reset requests can
    be asserted on too.
"""

from __future__ import annotations

NEWLINE_UNIT = 0x000A


def to_units(text: str) -> list[int]:
    b = text.encode("utf-16-le")
    return [b[i] | (b[i + 1] << 8) for i in range(0, len(b), 2)]


def units_to_str(units: list[int]) -> str:
    b = bytearray()
    for u in units:
        b.append(u & 0xFF)
        b.append((u >> 8) & 0xFF)
    return bytes(b).decode("utf-16-le")


def _default_state(overrides=None):
    state = {"style": "NORMAL_TEXT", "bullet": None, "indent": False}
    if overrides:
        state.update(overrides)
    return state


class FakeDoc:
    """paragraphs: list of (text, state_overrides_dict_or_None). Each
    paragraph becomes text + a trailing '\\n' carrying that state."""

    def __init__(self, paragraphs, tab_id="t.0"):
        self.tab_id = tab_id
        self._next_list_id = 1
        # index 0 is an unaddressable placeholder (mirrors the real API:
        # index 0 is never a legal location).
        self.c = [[0x00A7, None]]
        # Every applied updateTextStyle request, as (start, end, textStyle
        # dict) -- lets a test assert WHICH span a style landed on, not
        # just that some in-bounds updateTextStyle happened. Paired with
        # `text_at`, a test can assert "the range that says bold=True
        # covers exactly this substring".
        self.text_style_events = []
        # A real Docs tab always has at least one paragraph, even empty --
        # there is no such thing as a body with zero paragraphs. Seed one
        # so an empty `paragraphs` list models a genuinely empty tab rather
        # than an impossible zero-paragraph one.
        if not paragraphs:
            paragraphs = [("", None)]
        for text, overrides in paragraphs:
            for u in to_units(text):
                self.c.append([u, None])
            self.c.append([NEWLINE_UNIT, _default_state(overrides)])

    def end(self):
        return len(self.c)

    def para_of(self, i):
        j = i
        while self.c[j][0] != NEWLINE_UNIT:
            j += 1
        return j

    def apply(self, request):
        (kind, v), = request.items()

        if kind == "insertText":
            i = v["location"]["index"]
            text = v["text"]
            assert 1 <= i < self.end(), f"insert oob {i} end {self.end()}"
            inherited = self.c[self.para_of(i)][1]
            new_units = []
            for u in to_units(text):
                if u == NEWLINE_UNIT:
                    new_units.append([u, dict(inherited)])
                else:
                    new_units.append([u, None])
            self.c[i:i] = new_units

        elif kind == "deleteContentRange":
            s, e = v["range"]["startIndex"], v["range"]["endIndex"]
            assert 1 <= s < e <= self.end() - 1, f"delete oob {s},{e} end {self.end()}"
            del self.c[s:e]

        elif kind == "createParagraphBullets":
            s, e = v["range"]["startIndex"], v["range"]["endIndex"]
            assert 1 <= s < e <= self.end(), f"createParagraphBullets oob {s},{e}"
            i = s
            while i < e:
                j = self.para_of(i)
                state = self.c[j][1]
                if state.get("bullet") is not None:
                    raise AssertionError(
                        "createParagraphBullets called on an already-bulleted "
                        f"paragraph at unit {j} (listId={state['bullet']['listId']!r}, "
                        f"nestingLevel={state['bullet']['nestingLevel']!r}) -- this "
                        "mints a new list and resets nesting on a real doc, which "
                        "is exactly the bug the bullet-identity fix prevents."
                    )
                state["bullet"] = {"listId": f"L{self._next_list_id}", "nestingLevel": 0}
                self._next_list_id += 1
                i = j + 1

        elif kind == "deleteParagraphBullets":
            s, e = v["range"]["startIndex"], v["range"]["endIndex"]
            assert 1 <= s < e <= self.end(), f"deleteParagraphBullets oob {s},{e}"
            i = s
            while i < e:
                j = self.para_of(i)
                self.c[j][1]["bullet"] = None
                i = j + 1

        elif kind == "updateParagraphStyle":
            s, e = v["range"]["startIndex"], v["range"]["endIndex"]
            assert 1 <= s < e <= self.end(), f"updateParagraphStyle oob {s},{e}"
            ps = v["paragraphStyle"]
            i = s
            while i < e:
                j = self.para_of(i)
                state = self.c[j][1]
                if "namedStyleType" in ps:
                    state["style"] = ps["namedStyleType"]
                if "indentStart" in ps:
                    state["indent"] = ps["indentStart"].get("magnitude", 0) > 0
                i = j + 1

        elif kind == "updateTextStyle":
            s, e = v["range"]["startIndex"], v["range"]["endIndex"]
            assert 1 <= s < e <= self.end(), f"updateTextStyle oob {s},{e}"
            # Snapshot the covered text NOW, at application time -- later
            # requests in the same batch can still shift/rewrite content
            # at these same numeric indices, so capturing the substring
            # immediately is what makes text_style_events answer "which
            # text did this style land on" rather than "what's at these
            # indices by the time you look, afterward".
            covered = self.units_at(s, e)
            self.text_style_events.append((s, e, dict(v.get("textStyle", {})), covered))

        else:
            raise AssertionError(f"unhandled request kind: {kind}")

    def units_at(self, start, end):
        """The decoded text currently occupying [start, end)."""
        return units_to_str([self.c[i][0] for i in range(start, end)])

    # -- introspection -----------------------------------------------------

    def paras(self):
        """List of (text, state_dict) per paragraph, decoded back to str."""
        out = []
        buf = []
        for i in range(1, self.end()):
            if self.c[i][0] == NEWLINE_UNIT:
                out.append((units_to_str(buf), self.c[i][1]))
                buf = []
            else:
                buf.append(self.c[i][0])
        return out

    def plain_texts(self):
        return [t for t, _st in self.paras()]

    def api_doc(self):
        content = []
        start = 1
        buf = []
        for i in range(1, self.end()):
            buf.append(self.c[i][0])
            if self.c[i][0] == NEWLINE_UNIT:
                state = self.c[i][1]
                text = units_to_str(buf)
                paragraph_style = {"namedStyleType": state["style"]}
                if state["indent"]:
                    paragraph_style["indentStart"] = {"magnitude": 36, "unit": "PT"}
                    paragraph_style["indentFirstLine"] = {"magnitude": 0, "unit": "PT"}
                para = {
                    "elements": [{"textRun": {"content": text}}],
                    "paragraphStyle": paragraph_style,
                }
                if state["bullet"] is not None:
                    para["bullet"] = dict(state["bullet"])
                content.append({"startIndex": start, "endIndex": i + 1, "paragraph": para})
                start = i + 1
                buf = []
        return {
            "title": "fake doc",
            "body": {"content": []},
            "tabs": [
                {
                    "tabProperties": {"tabId": self.tab_id},
                    "documentTab": {"body": {"content": content}},
                }
            ],
        }


class FakeDocuments:
    def __init__(self, doc: FakeDoc):
        self._doc = doc

    def get(self, **_kwargs):
        return _Execute(lambda: self._doc.api_doc())

    def batchUpdate(self, documentId=None, body=None):
        def run():
            for request in body["requests"]:
                self._doc.apply(request)
            return {"replies": [{} for _ in body["requests"]]}

        return _Execute(run)


class _Execute:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeService:
    """Matches the subset of `docs_service` surface smart_update_doc /
    write_tab_content use: .documents().get(...).execute() and
    .documents().batchUpdate(...).execute()."""

    def __init__(self, doc: FakeDoc):
        self.doc = doc

    def documents(self):
        return FakeDocuments(self.doc)
