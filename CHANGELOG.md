# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.1.0] - 2026-09-25

Initial release. Standalone package extracted from an internal single-file
CLI, restructured into a `src/`-layout package with an installable
`gdocs-md` console entry point and a full test suite (no network required).

### Added
- Subcommands: `get`, `create`, `update` (smart-diff / `--tab` /
  `--replace-all`), `tabs`, `sheets`, `list`, `auth login`, `auth status`.
- Configuration resolution (CLI flag > env var > config file > default) for
  credentials directory, OAuth client file, registry file, and default
  account -- no account name or path baked into the tool.
- `--json` on every command, with errors emitted as JSON on stderr under
  `--json`.
- Stable, documented exit codes (see `AGENTS.md`).
- `AGENTS.md`, a `skills/gdocs-md/SKILL.md`, and this changelog.
- GitHub Actions CI (test matrix across supported Python versions) and a
  release workflow that builds and publishes sdist/wheel to GitHub
  Releases on a `v*` tag push.

### Fixed (carried over from the CLI this package was built from)
- The diff-based update path's same-position insert ordering: a run of
  paragraphs appended or inserted at the same document position now reads
  back in the order they were written, not reversed.
- Intraword underscore emphasis (`snake_case_name`, `___`) is no longer
  misread as italic/bold markup -- underscore emphasis now requires a word
  boundary, matching CommonMark; asterisk emphasis is unaffected.
- Block/paragraph alignment when the new markdown contains a multi-line
  blockquote or a table: a single parser (and a single flattened
  paragraph-unit list derived from it) now backs both the diff comparison
  and the formatting metadata, so paragraphs after a blockquote or table no
  longer draw formatting from the wrong block.
- Editing an existing list item's text no longer re-bullets it -- its
  `listId`/nesting level are preserved instead of being reset by an
  unconditional `createParagraphBullets` call.
- A paragraph deletion immediately before a table/table-of-contents/section
  break no longer risks failing the whole batch update; it falls back to
  content-only deletion in that case.
- Style, bullet, and indent resets on an inserted or replaced paragraph now
  fire in both directions (moving into a style/bullet/indent AND moving out
  of one), not just into.
- `get` now warns (stderr, and in `--json` `warnings`) instead of silently
  dropping tables/table-of-contents/section-breaks it can't read back.
- `create` (and `--replace-all`) resolve a relative image path in the
  source markdown against the markdown file's own directory, not the
  caller's current working directory.
- Every index and length computation that advances a Google Docs API
  position now uses UTF-16 code-unit length, not Python string length --
  an astral character (most emoji) is one Python character but two UTF-16
  units, and the old length math silently dropped the last character of a
  run following one.

### Fixed (first review round, still 0.1.0 -- nothing has shipped yet)
- A stacked insert of 2+ paragraphs at the same document position judged
  every paragraph after the first against the wrong anchor (the original
  neighbouring paragraph, not the paragraph the previous insert in the
  same run had just written) -- wrong/lost style, and a re-bullet crash on
  a multi-item new list. Every insert in a run now judges itself against
  where it actually lands.
- A bulleted paragraph's own list indentation was read and reset as this
  tool's separate blockquote-indent convention: editing an existing list
  item emitted a spurious indent reset on every edit, and converting a
  list item to a plain paragraph left the API's own visual indent behind
  forever (nothing cleared it, and the next diff run saw equal text and
  did nothing). List indent is no longer touched when the target is a
  list item, and a bullet removal now always resets it when the new
  target isn't a blockquote line.
- The exit-code contract only held on the happy path: an escaped
  `HttpError`/`RefreshError` from most commands surfaced as an
  uncontracted crash (exit 1, a bare traceback, no JSON under `--json`);
  `get` relabeled an auth/config failure (missing token, revoked refresh)
  as "document not found"; several usage-shaped failures (a missing
  required argument, a bad flag, bare `auth` with no subcommand) exited 3
  instead of 2; and every global flag only worked in one position
  relative to the subcommand, contradicting the baseline's and every
  existing caller's before-subcommand usage. All fixed: every Google API
  failure and every other unhandled exception now maps to a documented
  code with JSON on stderr under `--json`; argument-validation failures
  are `UsageError` (exit 2); every global flag works in either position.
- `get -o FILE` truncated FILE before the fetch even started, so any
  failure (auth, network, bad ID) left a caller's existing file at 0
  bytes; `--json` together with `--output` wrote the JSON to stdout and
  left the file empty while still claiming "Written to". Output is now
  buffered and written once, atomically, only on success.
- Registry writes were not atomic and had no locking: a concurrent read
  could see a torn/partial file, and two concurrent `create`/`update`
  calls each starting from their own stale snapshot could silently lose
  one another's entry. Writes are now atomic (temp file + rename) under
  an exclusive lock, and merge with whatever is currently on disk instead
  of blindly overwriting it.
- The CI matrix silently tested Python 3.12 on every leg regardless of
  what the matrix declared (the action version in use didn't support the
  input naming the interpreter, and GitHub Actions only warned, silently,
  about the unrecognized input). CI now pins actions by commit SHA,
  proves the interpreter version, and runs `uv sync --locked`; the release
  workflow now runs the test suite and checks the tag against
  `pyproject.toml`'s version before publishing.
- README/AGENTS install instructions said `uv tool install gdocs-md`,
  which doesn't work (the package isn't on PyPI, and the name is
  unclaimed there) -- replaced with the git-URL, tag-pinned install that
  actually works, plus the (verified) upgrade path.
- A `<br>` paragraph never converged: the plain-text comparison used for
  diffing mapped it to a space while the actual write path used a
  vertical-tab character, so every sync deleted and reinserted that
  paragraph, killing any comment anchored there. Both sides now agree.
- pandoc older than 2.11.2 (the version `--markdown-headings=atx` needs)
  now fails loudly with the minimum version named, instead of an
  unlabeled pandoc-argument error; a markdown filename starting with `-`
  no longer gets parsed as a pandoc flag; `get`'s Drive-export fallback
  now runs the same missing-pandoc check pandoc's other two call sites do.
- `create`'s temp DOCX conversion ran before authentication, so an auth
  failure left the converted file in the system temp directory forever;
  auth now happens first.
- `create` no longer loses the created doc's `doc_id`/`url` when the
  local registry write fails afterward -- it warns and reports
  `"registered": false` instead of exiting non-zero with nothing to show
  for a doc that already exists (which was exactly what pushed a retry
  into creating a second one).
- OAuth token refresh now loads the token's `expiry` field, so the
  proactive refresh-and-persist path actually runs instead of being dead
  code (refresh happened reactively inside the HTTP transport before, and
  was never written back). If the token file isn't writable after a
  refresh, that's now a warning, not an error -- the in-memory token
  still works for the current call. The non-interactive login path no
  longer silently falls back to a network-bound server when a local
  browser can't be opened; token files are now created at `0600` from the
  moment they exist, never `chmod`'ed after the fact.
- A section-break warning from `get` fired on every real document
  (every Docs body/tab body starts with one, and it never carries text)
  and buried the table-drop warning that actually matters; it's no longer
  labeled as a droppable-content warning.
- An unrecognized config-file key is now warned about on stderr instead
  of silently falling back to the default with no signal at all.
