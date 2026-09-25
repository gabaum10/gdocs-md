---
name: gdocs-md
description: Read and write Google Docs from markdown using the gdocs-md CLI -- diff-based updates that preserve comments/suggestions, tab management, and spreadsheet reads. Use when the task involves reading, creating, or editing a Google Doc, or reading a Google Sheet.
---

# gdocs-md

A command-line tool for Google Docs, driven by markdown. Full contract
(every command, exit codes, `--json` shapes, known limits): read
`AGENTS.md` in this tool's own repository, or `gdocs-md --help` /
`gdocs-md <command> --help`.

## Before first use

Check whether an account is authenticated:

```sh
gdocs-md auth status --account <name>
```

If not, either run `gdocs-md auth login --account <name>` (requires a
human to complete the browser OAuth consent -- an agent can't do this
unattended) or ask the user which already-configured account to use.

## Core loop

```sh
# Read
gdocs-md get <doc-id> --account <name>

# Create from a markdown file
gdocs-md create notes.md --title "Title" --account <name>

# Edit locally, then push only what changed (preserves comments/suggestions
# on everything else)
gdocs-md update <doc-id> notes.md --account <name>

# Preview first if unsure
gdocs-md update <doc-id> notes.md --dry-run --account <name>
```

## Picking an update mode

- **Default (smart diff)**: the common case. Only changed paragraphs are
  touched. Refuses on a multi-tab doc -- retry with `--tab` or
  `--replace-all`.
- **`--tab <id> --input <file>`**: full rewrite of ONE tab (supports real
  markdown tables). Destroys that tab's comments/suggestions.
- **`--replace-all --force`**: full document replacement, last resort.
  Destroys ALL tabs' comments/suggestions and resets fonts to Calibri.

## Use `--json`

Every command supports `--json` for a stable, parseable result on stdout
(and the same shape as an error on stderr on failure). Always prefer it
over parsing human-formatted text. Check the exit code first -- see
AGENTS.md's exit-code table -- before parsing output.

## Watch for

- Tables inside an EXISTING doc don't diff reliably with smart-diff; use
  `--tab` or `--replace-all` for a doc where tables matter.
- `get` silently drops tables/TOC/section-breaks unless you check its
  stderr WARNING or the `warnings` field in `--json` output.
- `--replace-all` resets fonts to Calibri.
