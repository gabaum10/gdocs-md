# gdocs-md

A command-line tool for reading and writing Google Docs from markdown. Built
so both humans and AI agents can drive Google Docs without guesswork:
stable exit codes, a `--json` mode on every command that needs one, and a
diff-based update mode that edits only what changed so comments and
suggestions on the rest of the doc survive.

If you're an agent (or building one), read [AGENTS.md](AGENTS.md) instead --
it's the full contract: every command, when to use which update mode, exit
codes, JSON shapes, and known limits. There's also a drop-in skill file at
[skills/gdocs-md/SKILL.md](skills/gdocs-md/SKILL.md).

## Install

Requires Python 3.10+ and [pandoc](https://pandoc.org/installing.html) >=
2.11.2 (used to convert markdown to DOCX for `create` and
`update --replace-all`; `gdocs-md` checks the version and fails loudly,
naming the minimum, if it's older).

This package is **not published to PyPI** -- `uv tool install gdocs-md`
does not work (the name is unclaimed there; don't run that line, it would
install whatever anyone else eventually registers under it). Install from
git, pinned to a release tag:

```sh
uv tool install git+https://github.com/gabaum10/gdocs-md@v0.1.1
```

Or from a local checkout:

```sh
uv tool install /path/to/gdocs-md
```

Both install the `gdocs-md` console command. The repo is public, so a
plain `https://` clone or `git+https://` install works with no
credentials or collaborator access needed.

**`uv tool` installs to `~/.local/bin`**, which may not be on your
`PATH`. If `gdocs-md` isn't found after install, run
`uv tool update-shell` (adds it for future shells) or add it directly:
`export PATH="$HOME/.local/bin:$PATH"`.

**Upgrading:** re-run the install command with the new tag --
`uv tool install git+https://github.com/gabaum10/gdocs-md@v0.2.0` (no
`--force`/`--reinstall` needed; installing a different pinned ref is
already a different requirement as far as uv is concerned). Plain
`uv tool upgrade gdocs-md` does **not** move to a new release when you
installed pinned to a specific tag -- verified: it reports "Nothing to
upgrade", because the tag itself (and therefore the ref you're pinned to)
hasn't changed. `uv tool upgrade` only helps if you installed without a
tag (tracking a branch), which isn't what the command above does. Both
of these are real, verified behaviors of `uv tool` -- pick the one that
matches how you installed.

**Hearing about new releases:** watch the no-account Atom feed
(`https://github.com/gabaum10/gdocs-md/releases.atom`) or, if you have a
GitHub account, use Watch -> Custom -> Releases on the repo.

## Quickstart

```sh
# One-time: put a Desktop-app OAuth client credentials file where gdocs-md
# looks for it (default: ~/.config/gdocs-md/credentials/oauth-client.json;
# see "Configuration" below to change that).
gdocs-md auth login --account personal
gdocs-md auth status --account personal

# Create a doc from a markdown file
gdocs-md --account personal create notes.md

# Read it back
gdocs-md --account personal get <doc-id>

# Edit the markdown file, then push only the changed paragraphs -- comments
# and suggestions on unchanged text are preserved
gdocs-md --account personal update <doc-id> notes.md

# Preview a change without writing anything
gdocs-md --account personal update <doc-id> notes.md --dry-run

# Full replace (destroys comments/suggestions; resets fonts to Calibri)
gdocs-md --account personal update <doc-id> notes.md --replace-all
```

## Getting an OAuth client

`gdocs-md` needs a Google OAuth client (Desktop app type) to run the login
flow against. In [Google Cloud Console](https://console.cloud.google.com/):

1. Create (or reuse) a project, enable the Google Docs API, Google Drive
   API, and Google Sheets API.
2. Create OAuth 2.0 credentials of type "Desktop app".
3. Download the JSON and save it at your configured `oauth_client_file`
   path (default `~/.config/gdocs-md/credentials/oauth-client.json`).
4. Run `gdocs-md auth login --account <name>`.

The scopes requested at login are narrow on purpose: Docs, Drive, Sheets
(read-only), and the identity scope needed to show which account signed in.
No Gmail, no Contacts.

## Configuration

Every path gdocs-md needs is configuration, resolved with this precedence
(highest wins):

1. CLI flag (`--credentials-dir`, `--oauth-client`, `--registry`, `--account`, `--config-file`)
2. Environment variable (`GDOCS_MD_CREDENTIALS_DIR`, `GDOCS_MD_OAUTH_CLIENT`, `GDOCS_MD_REGISTRY`, `GDOCS_MD_ACCOUNT`, `GDOCS_MD_CONFIG`)
3. A JSON config file (default `$XDG_CONFIG_HOME/gdocs-md/config.json`, or `~/.config/gdocs-md/config.json`)
4. Built-in default

Config file example:

```json
{
  "credentials_dir": "/home/you/.config/gdocs-md/credentials",
  "default_account": "personal"
}
```

There is no baked-in account name or default. A fresh install has nothing
until you run `gdocs-md auth login --account <name>` (or set
`default_account`).

**Flag ordering.** Every global flag -- `--config-file`,
`--credentials-dir`, `--oauth-client`, `--registry`, `--account`, and
`--json` -- works in EITHER position: before the subcommand
(`gdocs-md --credentials-dir DIR list`) or after it
(`gdocs-md list --credentials-dir DIR`). Both forms are equivalent; use
whichever reads better for the call you're building.

## Commands

`get`, `create`, `update` (smart-diff / `--tab` / `--replace-all`), `tabs`,
`sheets`, `list`, `auth login`, `auth status`. Full usage, exit codes, and
`--json` shapes: [AGENTS.md](AGENTS.md).

## Development

```sh
uv sync --group dev
uv run pytest
```

See [CHANGELOG.md](CHANGELOG.md) for release history and
[CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) for the agent-facing
contract.

## License

MIT -- see [LICENSE](LICENSE).
