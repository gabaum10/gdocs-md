Agent contract for `gdocs-md`. This is the reference an agent should read
before calling the tool -- every command, when to use which update mode,
exit codes, `--json` shapes, and known limits, stated from what the code
actually does.

## Install

**Not on PyPI.** `uv tool install gdocs-md` does not work. Install from
git, pinned to a tag -- the repo is public, no credentials needed (see
README.md's Install section for the full upgrade details, the
`~/.local/bin`-on-PATH note, and the release-feed pointer):

```sh
uv tool install git+https://github.com/gabaum10/gdocs-md@v0.1.1
```

Requires `pandoc` >= 2.11.2 on PATH for `create` and `update --replace-all`
(not for `get`, `update` in smart-diff or `--tab` mode, `tabs`, `sheets`,
or `list` -- `get`'s Drive-export fallback for uploaded/non-native files
is the one exception: it also needs pandoc).

## Flag ordering

Every global flag -- `--config-file`, `--credentials-dir`,
`--oauth-client`, `--registry`, `--account`, `--json` -- works in EITHER
position: before the subcommand (`gdocs-md --account X get ...`) or after
it (`gdocs-md get ... --account X`). Both forms are equivalent; use
whichever reads better for the call you're building. (Prior to the fix
round this only worked in one position per flag; if you're holding an
older AGENTS.md or a cached memory of this tool, both positions now work
for everything.)

## Before you do anything: config and auth

Every path is configuration -- see README.md's "Configuration" section for
the full precedence table. There is no default account. Check first:

```sh
gdocs-md auth status --account <name>
```

If it's not configured, either the caller runs `gdocs-md auth login
--account <name>` interactively (it opens a browser / prints a URL), or a
human has to do that first. An agent cannot complete the OAuth consent flow
unattended.

Every subcommand takes `--account <name>` (or reads it from
`GDOCS_MD_ACCOUNT` / `default_account` in the config file). There is no
account name baked into the tool.

## Commands

### `get [doc_id | --url URL]`

Reads a doc's content to stdout (or `--output FILE`).

- `--with-title` -- prepend `# <title>`.
- `--tab <id>` -- read one tab. `--all-tabs` -- read every tab, each under
  its own `# <title>`.
- No `--tab`/`--all-tabs` on a multi-tab doc: reads tab 0 and prints a
  `[notice]` to stderr (not an error -- `get` never refuses to read).
- `--json`: `{"doc_id", "title", "text", "suggestions": {"insertions": [...],
  "deletions": [...]}, "comments": [...], "warnings": [...], "notice"?}`.
  `--all-tabs --json` instead carries `"tabs": [{"tab_id","title","text"}]`.

**Known limit -- tables are dropped, not just unformatted.** `get` has no
table-reading branch: a `table` element in the doc's content list (also
`tableOfContents`, `sectionBreak`) is skipped entirely, not rendered as
degraded text. When this happens, a WARNING prints to stderr and the
`warnings` list in `--json` output names what was dropped. Don't treat
`get`'s output as a complete transcript of a doc containing tables.

### `create <file> [--title T] [--folder ID]`

Converts markdown to DOCX via pandoc, uploads to Drive as a new Google Doc,
and records it in the registry keyed by the resolved local file path.
Refuses if that exact path is already in the registry (use `update`
instead).

Image paths in the markdown are resolved relative to the **markdown file's
own directory**, not the caller's current directory -- `![x](img.png)` next
to `notes.md` works regardless of where you invoke `gdocs-md` from.

### `update <doc_id> [file] [options]`

Three modes:

**Smart diff (default).** `gdocs-md update <doc_id> <file>`. Diffs the new
markdown's paragraphs against the doc's current paragraphs and only
touches what changed -- comments and suggestions anchored to unchanged
text survive. Refuses on a multi-tab doc (exit `UNREPRESENTABLE_DIFF`);
retry with `--tab` or `--replace-all`. `--dry-run` computes and reports
the diff without writing.

Use this whenever the doc has comments/suggestions worth preserving, or
you're making a small, targeted edit.

**`--tab <id> --input <file>`.** Full rewrite of one tab: clears it,
re-renders the markdown from scratch (including real Docs tables). This
DESTROYS comments and suggestions anchored in that tab. Other tabs are
untouched.

Use this for a from-scratch rewrite of one tab, or when the tab contains a
markdown table (smart-diff doesn't build real tables -- see Known Limits).

**`--replace-all [--force]`.** Full document replacement via Drive DOCX
upload (pandoc, same as `create`). Destroys ALL tabs' comments and
suggestions. **Resets fonts to Calibri** (permissions on the doc survive).
Requires `--force` on a multi-tab doc (exit `DESTRUCTIVE_REFUSED` without
it).

Use this as the last resort: a doc badly out of sync, or when smart-diff
can't represent the change (exit `UNREPRESENTABLE_DIFF`) and you don't
want `--tab`'s narrower scope.

**Decision order:** try smart diff. If it refuses because the doc is
multi-tab, decide: does the target tab need real Docs tables, or is a
full rewrite of just that tab acceptable? Use `--tab`. Otherwise, and
only if you're fine losing every tab's comments, use `--replace-all
--force`.

`--json` shape (all three modes, dry-run and not, with concrete examples):
see "## JSON schemas" below.

### `tabs <doc_id> [--create --title T [--index N] [--parent ID]]`

Lists tabs (recursive, with nesting depth) or creates one.
`tabs` makes a real network call and is a fast way to check the tool's
plumbing is working end-to-end (`get` and `list` don't -- see Known
Limits). Creating a child tab (`--parent`) does NOT make it the implicit
write target; follow up with `update --tab <new-child-id>`.

### `sheets <spreadsheet_id> [--sheet NAME | --range A1RANGE] [--list-sheets]`

Reads a spreadsheet range as a markdown table. Read-only.

### `list`

Lists the local registry (file path -> doc_id/url/title/timestamps). No
network call.

### `auth login --account <name> [--headless] [--timeout SECONDS]` / `auth status --account <name>`

`login` runs the OAuth consent flow and saves a token. `--headless` binds
the loopback server to `0.0.0.0:8085` (reachable from another machine on
the network) instead of opening a local browser on this one -- there is no
other supported flow; if a local browser can't be opened and `--headless`
wasn't passed, `login` now ERRORS (exit `AUTH_OR_CONFIG`) rather than
silently falling back to the network-bound server on your behalf. An
agent cannot complete the interactive consent step itself either way --
this needs a human at a browser. `--timeout` (default 180s) bounds how
long it waits for that human. `status` checks whether a token exists,
refreshes if needed, and confirms it against a live API call.

## JSON schemas

Every shape below is what a SUCCESSFUL call's `--json` produces on stdout
(or the `--output` file, for `get`). A failed call's shape is always the
same regardless of command: `{"error": "<message>", "exit_code": <int>}`
on stderr -- see "`--json`" below.

**`get`** (default, single/no-tab):
```json
{"doc_id": "abc123", "warnings": [], "title": "My Doc", "text": "...",
 "suggestions": {"insertions": [], "deletions": []}, "comments": [],
 "notice": "doc has 2 tabs (...); reading tab 0. ..."}
```
`notice` is present only when the doc is multi-tab and neither `--tab` nor
`--all-tabs` was given. `--tab <id>`: same shape minus `suggestions`/
`comments`, plus `"tab_id"`. `--all-tabs`: minus `text`/`suggestions`/
`comments`, plus `"tabs": [{"tab_id", "title", "text"}, ...]`.

**`create`**:
```json
{"doc_id": "abc123", "url": "https://docs.google.com/document/d/abc123/edit",
 "title": "My Doc", "registered": true}
```
`registered: false` plus a `"warning"` string means the Drive doc WAS
created (`doc_id`/`url` are real) but the local registry write failed --
do not re-run `create` on the same file (see the warning text for what to
do instead).

**`update` (smart diff, the default)**:
```json
{"mode": "smart_diff", "doc_id": "abc123",
 "url": "https://docs.google.com/document/d/abc123/edit",
 "changed": 2, "unchanged": 5, "ops": 6, "tab_id": null, "dry_run": false}
```
**This is how `--dry-run --json` tells you whether anything would
change: the `changed` field.** `changed: 0` (with `ops: 0`) means nothing
would be written -- the doc already matches. `changed` counts diff
OPCODES, not paragraphs (a run of 5 consecutive new paragraphs is one
`insert` opcode, so `changed` can be smaller than the number of paragraphs
actually touched -- use `ops` for a finer-grained sense of how much would
change, though `ops` counts API requests, not paragraphs either). With
`--dry-run`, `dry_run: true` and NOTHING is written regardless of
`changed`.

**`update --tab`**:
```json
{"doc_id": "abc123", "tab_id": "t.1", "url": "https://docs.google.com/document/d/abc123/edit"}
```
`--dry-run` with `--tab`: `{"dry_run": true, "tab_id": "t.1", "chars": 512}`
-- there is no `changed`/`ops` here. `--tab` is a full rewrite, not a
diff: it always replaces everything, so "would anything change" isn't a
meaningful question for this mode the way it is for smart diff.

**`update --replace-all`**:
```json
{"mode": "replace_all", "doc_id": "abc123",
 "url": "https://docs.google.com/document/d/abc123/edit", "modified": "2026-01-01T00:00:00.000Z"}
```
`--dry-run` with `--replace-all`: `{"dry_run": true, "mode": "replace_all", "tab_count": 2}`
-- also no `changed`/`ops`; like `--tab`, this mode always replaces
everything.

**`tabs`** (list): `{"doc_id": "abc123", "tabs": [{"tab_id", "title", "index", "depth", "child_count"}, ...]}`.
`tabs --create`: `{"doc_id", "tab_id", "title", "url", "warning": null}`
(`warning` is set when `--parent` was used -- the new tab isn't
automatically the write target).

**`sheets`**: `{"spreadsheet_id", "range", "values": [["a","b"],[...]]}`
(rows as returned by the Sheets API; `--list-sheets` instead gives
`{"spreadsheet_id", "sheets": ["Sheet1", "Sheet2"]}`).

**`list`**: `{"registry": {"<local file path>": {"doc_id", "url", "title", "created", "updated"}, ...}}`.

**`auth login`**: `{"account": "personal", "email": "you@example.com", "token_path": "/path/to/token.json"}`.

**`auth status`**: `{"configured": true, "account": "personal", "token_path": "...", "email": "you@example.com", "error": null}`
(`configured: false` on failure, with `error` set and `email: null` --
this shape appears on stdout even though the command ALSO exits
non-zero in that case, so both are readable).

## Exit codes

| Code | Name | Meaning |
|---|---|---|
| 0 | OK | success |
| 2 | USAGE | bad/missing arguments (a required `<file>`, `--input` with `--tab`, a doc ID), no subcommand, bare `gdocs-md auth` with no `login`/`status`, or ANY argparse-level parse error (bad flag, invalid choice) -- these all route through the same `UsageError`, so `--json` gives valid JSON on stderr for a parse error too, not raw argparse text |
| 3 | AUTH_OR_CONFIG | no account resolved, missing/unreadable token, missing/malformed config or oauth-client file, an HTTP 401 from a live call, or a `RefreshError` (revoked/invalid refresh token) |
| 4 | NOT_FOUND_OR_PERMISSION | doc/tab/file/table-index not found, "document already exists" is NOT this (it's USAGE), or an HTTP 403/404 from a live call |
| 5 | UNREPRESENTABLE_DIFF | smart-diff can't represent this change (currently: multi-tab doc without `--tab`/`--replace-all`) -- retry with one of those |
| 6 | DESTRUCTIVE_REFUSED | a destructive op (`--replace-all` on a multi-tab doc) was refused because `--force` wasn't passed |
| 7 | MISSING_PANDOC | pandoc isn't on PATH, isn't runnable, or is older than 2.11.2 (named in the message) -- for `create`, `update --replace-all`, and `get`'s Drive-export fallback for non-native files |
| 8 | API_ERROR | any other Google API failure (any HTTP status besides 401/403/404), Ctrl-C, or any OTHER unhandled exception (a last-resort catch-all in `cli.py` -- nothing escapes as a bare traceback / exit 1; set `GDOCS_MD_DEBUG=1` to also print the traceback to stderr) |

A caller should branch on the exit code alone; the message text is for
humans and may change between versions. `tests/test_exit_code_table.py`
walks every code above against a real code path, so this table can't
silently drift from what the code does. Codes never get repurposed --
see `src/gdocs_md/exit_codes.py`.

## `--json`

Every command accepts `--json`. On success, the result is a single JSON
object on stdout (never mixed with human text). On failure, the SAME shape
of error -- `{"error": "<message>", "exit_code": <int>}` -- is written to
**stderr**, and the process exits with that same code. stdout stays clean
either way; don't parse stderr as part of a successful result.

## What survives which update mode

| | smart diff | `--tab` | `--replace-all` |
|---|---|---|---|
| Comments (unaffected paragraphs) | survive | destroyed (whole tab) | destroyed (whole doc) |
| Comments (edited paragraphs) | destroyed on that paragraph | destroyed | destroyed |
| Suggestions | same as comments | destroyed | destroyed |
| Other tabs | untouched | untouched | destroyed unless `--force` refuses first |
| Fonts | untouched | untouched | **reset to Calibri** |
| Permissions/sharing | untouched | untouched | untouched |
| List identity (listId/nestingLevel) of an edited item | preserved | rebuilt from scratch | rebuilt from scratch |
| Real Docs tables | not built; diffed as flattened plain-text rows | built for real | built for real (via pandoc) |

## Known limits

- **Tables don't round-trip through smart-diff.** A markdown table becomes
  one flattened plain-text "row" paragraph per row when diffed against an
  existing doc, and a doc that already contains a *real* Docs table can't
  be diffed reliably at all -- the table's cells aren't top-level body
  paragraphs, so they're invisible to the diff. Use `--tab` or
  `--replace-all` for any doc where tables matter.
- **`get` drops tables and TOC content silently unless you're watching
  for the warning.** See `get`'s section above. (Section breaks are
  deliberately NOT warned on -- every real doc/tab starts with one and it
  never carries text, so warning on it would fire on every single `get`
  and bury the table warning that matters.)
- **Deleting the doc's own last paragraph leaves an empty paragraph
  behind.** Its trailing newline is immutable, so smart-diff falls back to
  content-only deletion. Same for a paragraph deletion that would delete
  the newline immediately before a table/TOC/section-break -- the API
  rejects that, so it falls back the same way.
- **New lists don't nest.** The markdown parser doesn't track indentation
  on list items, so a markdown sub-item (`  - nested`) parses the same as
  a top-level one -- a NEW list this tool writes is always flat. An
  *existing* nested list item, edited via smart-diff, keeps its own
  nesting (its paragraph mark is never rewritten) -- this limit is about
  writing new nested structure, not preserving it.
- **A doc's DOCX->Docs conversion (via `create` / `--replace-all`) may
  give a nested list item a different `listId` than its parent**, not the
  same list at a deeper nesting level. Don't assume same-listId implies
  same-nesting-family across a pandoc round trip.
- **`\v` / `<br>` round-trip through Docs as a soft line break**, not
  re-exported back to `<br>` by `get` -- it reads back as a literal
  vertical-tab character inside the paragraph's plain text.
- **The registry is one shared file, not split per account.** `create`
  records whichever account made the doc; `list` doesn't distinguish.
- **A new list item inserted directly next to an existing list joins that
  list**, inheriting its glyph preset and nesting level -- there's no
  "start a new, separate list here" signal in plain markdown, so a new
  top-level item placed right before an existing sub-item comes out
  nested, and a new ordered item butted against an unordered list takes
  the unordered list's preset. This is a property of the insert-anchor
  model (an inserted list item's bullet inherits from whatever paragraph
  it lands next to when that paragraph is already bulleted -- see
  bullet-identity preservation in `smart_update.py`), not a bug fixed in
  this build.
- **`update`'s registry lookup only affects the smart-diff and
  `--replace-all` paths**, and only for updating the `updated` timestamp
  on an already-registered doc; a doc not in the registry (e.g. one
  created outside this tool) still updates fine, it's just not tracked
  locally.

## Scopes and token compatibility

`auth login` requests a narrow scope list: Docs, Drive, Sheets (read-only),
and the identity scopes needed to show which account signed in. No Gmail,
no Contacts. A **refresh**, though, is built from whatever scopes the
saved token file itself records (`token.json`'s own `"scopes"` field) --
not this narrower list. A token minted by an older tool or a shared OAuth
client configured with a broader consent scope keeps refreshing normally;
forcing a narrower scope list onto a refresh request has produced
`invalid_scope` failures on real tokens in the past. If you're building
tooling that reads `token.json` directly: don't reconstruct
`google.oauth2.credentials.Credentials` with a hardcoded scope list for a
refresh. Read the file's own `scopes` field.

## Config file

`gdocs-md.json` at `$XDG_CONFIG_HOME/gdocs-md/config.json` (or wherever
`--config-file` / `GDOCS_MD_CONFIG` points):

```json
{
  "credentials_dir": "/path/to/credentials",
  "oauth_client_file": "/path/to/oauth-client.json",
  "registry_file": "/path/to/registry.json",
  "default_account": "some-account-name"
}
```

Every key is optional. Precedence for each: CLI flag > env var > this file
> built-in default. Full table: README.md.

An unrecognized key (a typo like `credential_dir`) is warned about on
stderr and otherwise ignored -- it does NOT fail the command, and the
corresponding setting falls back through the rest of the precedence chain
as if the key weren't there at all.

**Relative paths resolve against the current working directory**, not the
config file's own directory -- `"credentials_dir": "../creds"` in
`~/.config/gdocs-md/config.json` means a different real path depending on
where you invoke `gdocs-md` from. Use an absolute path (or `~/...`, which
IS expanded) in the config file if you need it to mean the same thing
regardless of cwd.
