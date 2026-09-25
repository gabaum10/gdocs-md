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
