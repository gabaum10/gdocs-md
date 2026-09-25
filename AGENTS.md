Agent contract for `gdocs-md`. This is the reference an agent should read
before calling the tool -- every command, when to use which update mode,
exit codes, `--json` shapes, and known limits, stated from what the code
actually does.

## Install

```sh
uv tool install gdocs-md
```

Requires `pandoc` on PATH for `create` and `update --replace-all` (not for
`get`, `update` in smart-diff or `--tab` mode, `tabs`, `sheets`, or `list`).

## Flag ordering

`--config-file`, `--credentials-dir`, `--oauth-client`, and `--registry`
are top-level flags: they must come BEFORE the subcommand
(`gdocs-md --credentials-dir DIR list`, not `gdocs-md list --credentials-dir
DIR` -- the latter is a usage error, exit `USAGE`). `--account` and
`--json` are per-subcommand flags: they come AFTER the subcommand
(`gdocs-md list --json`, not `gdocs-md --json list`).

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

`--json` on all three modes emits `{"mode", "doc_id", "url"?, "changed"?,
"unchanged"?, "ops"?, "tab_id"?, "dry_run"}` (fields vary by mode -- see
the exit-code/shape table below for the concrete keys per mode).

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

### `auth login --account <name> [--headless]` / `auth status --account <name>`

`login` runs the OAuth consent flow and saves a token. `--headless` binds
the loopback server to `0.0.0.0:8085` instead of opening a local browser
(for a machine with no display). `status` checks whether a token exists,
refreshes if needed, and confirms it against a live API call.

## Exit codes

| Code | Name | Meaning |
|---|---|---|
| 0 | OK | success |
| 2 | USAGE | bad arguments, no subcommand, or an argparse-level error |
| 3 | AUTH_OR_CONFIG | no account resolved, missing/unreadable token, missing/malformed config or oauth-client file |
| 4 | NOT_FOUND_OR_PERMISSION | doc/tab/file not found, or the API denied access |
| 5 | UNREPRESENTABLE_DIFF | smart-diff can't represent this change (currently: multi-tab doc without `--tab`/`--replace-all`) -- retry with one of those |
| 6 | DESTRUCTIVE_REFUSED | a destructive op (`--replace-all` on a multi-tab doc) was refused because `--force` wasn't passed |
| 7 | MISSING_PANDOC | pandoc isn't on PATH (only for `create` / `--replace-all`) |
| 8 | API_ERROR | any other Google API failure |

A caller should branch on the exit code alone; the message text is for
humans and may change between versions. Codes never get repurposed --
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
- **`get` drops tables, TOC, and section breaks silently unless you're
  watching for the warning.** See `get`'s section above.
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
