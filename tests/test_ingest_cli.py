"""Every store-only CLI command must actually run.

This file exists because of a real bug that every other test missed. The price
tests call `pipeline.sample_price(client, store)` directly, so they never went
through `scripts/ingest.py` — where `--price-sample` sat above the line that
builds the RPC client and crashed with `UnboundLocalError: client`. The command
was broken from the moment it was written and only surfaced when someone ran it.

Calling a function under test is not the same as running the program. These
tests start the CLI the way an operator does, so a name that exists in the
module but not at that point in `main()` fails here rather than in production.

The store-only commands run against an empty temporary store and must exit
cleanly while saying they have nothing yet — "no data" is a normal state of a
fresh deployment, not an error. Commands that need the network are checked for
*startup* only (`--help` lists them), because a test suite must not depend on
someone else's server being up.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INGEST = ROOT / "scripts" / "ingest.py"


def run(args: list[str], db: Path, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INGEST), "--db", str(db), *args],
        capture_output=True, text=True, cwd=ROOT, timeout=timeout,
    )


@pytest.fixture()
def empty_db(tmp_path: Path) -> Path:
    return tmp_path / "qdr.db"


# The commands that read the store and never need the network. Each must run to
# completion on an empty store: they are exactly what an operator types first,
# on a deployment that has not filled yet.
STORE_ONLY = [
    ["--status"],
    ["--price-status"],
    ["--mining-status"],
    ["--burn-status"],
]


@pytest.mark.parametrize("args", STORE_ONLY, ids=lambda a: a[0].lstrip("-"))
def test_store_only_command_runs_on_an_empty_store(args, empty_db):
    r = run(args, empty_db)
    assert r.returncode == 0, (
        f"{' '.join(args)} failed on an empty store:\n{r.stderr[-1500:]}")
    # A NameError/UnboundLocalError is the specific failure this file guards
    # against, and it can hide inside a zero exit if something swallowed it.
    for bad in ("Traceback", "UnboundLocalError", "NameError"):
        assert bad not in r.stderr, f"{' '.join(args)} printed a {bad}:\n{r.stderr[-1500:]}"


def test_price_status_says_it_has_nothing_yet(empty_db):
    """An empty store reports absence in words rather than printing zeros."""
    r = run(["--price-status"], empty_db)
    assert r.returncode == 0
    assert "not run yet" in r.stdout.lower() or "no data" in r.stdout.lower(), r.stdout


def test_help_lists_the_price_commands():
    """A command that argparse does not know cannot be typed. This also catches
    an option removed from the parser while its handler stayed behind."""
    r = subprocess.run([sys.executable, str(INGEST), "--help"],
                       capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert r.returncode == 0, r.stderr
    for flag in ("--price-sample", "--price-status", "--price-interval", "--no-price"):
        assert flag in r.stdout, f"{flag} is missing from --help"


def test_every_handler_only_uses_names_bound_before_it():
    """The structural version of the bug, checked without running anything.

    `main()` builds the store early and the RPC client late, with the
    store-only commands in between. A handler above that line that mentions
    `client` is broken however well its underlying function is tested — so the
    source between those two points must not reference it.
    """
    src = INGEST.read_text(encoding="utf-8")
    body = src[src.index("def main()"):]
    store_line = body.index("store = Store(")
    client_line = body.index("client = CachedClient(")
    between = body[store_line:client_line]

    for i, line in enumerate(between.splitlines()):
        code = line.split("#", 1)[0]
        if "client" not in code:
            continue
        # Building one locally is the fix, not the bug: those lines name the
        # constructor rather than relying on the shared variable.
        if "CachedClient(" in code:
            continue
        pytest.fail(
            "a store-only handler references `client` before it is built "
            f"(line {i + 1} of that block): {line.strip()!r}")


def test_no_console_output_can_break_a_windows_terminal():
    """A Windows console defaults to cp1252 and raises on characters outside it.

    This is not hypothetical: `--price-status` printed a "→" between two prices
    and died with UnicodeEncodeError the first time a store held two of them —
    taking the whole command down over a decoration. Comments and the docstring
    may hold anything; what gets printed may not.
    """
    src = INGEST.read_text(encoding="utf-8")
    offenders: dict[int, set[str]] = {}
    for n, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        if "print(" not in code and 'f"' not in code:
            continue
        for ch in code:
            if ord(ch) < 128:
                continue
            try:
                ch.encode("cp1252")
            except UnicodeEncodeError:
                offenders.setdefault(n, set()).add(ch)
    assert not offenders, (
        "console output holds characters a cp1252 terminal cannot encode: "
        + "; ".join(f"line {n}: {sorted(c)}" for n, c in sorted(offenders.items())))
