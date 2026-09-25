"""Shared fixtures for the whole suite.

The CLI/config tests exercise real config-resolution code (XDG lookup,
env vars) against a developer's ACTUAL machine unless something isolates
them first -- a developer with their own `default_account` set in
`~/.config/gdocs-md/config.json`, or a `GDOCS_MD_*` var exported in their
shell, would see tests that assert "no account configured" fail for a
reason that has nothing to do with the code under test. This fixture
isolates every test in the suite from that: a fresh HOME/XDG_CONFIG_HOME
under pytest's own tmp_path, and every GDOCS_MD_* var cleared before the
test body runs. Individual tests still set their own XDG_CONFIG_HOME /
credentials-dir / config file when the test is specifically about that --
this fixture only guarantees the STARTING point is hermetic, not that
every test's own setup is a no-op.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "isolated-xdg-config"))
    for var in (
        "GDOCS_MD_CONFIG",
        "GDOCS_MD_CREDENTIALS_DIR",
        "GDOCS_MD_OAUTH_CLIENT",
        "GDOCS_MD_REGISTRY",
        "GDOCS_MD_ACCOUNT",
        "GDOCS_MD_DEBUG",
    ):
        monkeypatch.delenv(var, raising=False)
