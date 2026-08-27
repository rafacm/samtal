"""The docs link checker's contract, exercised as a subprocess.

The script lives at the repository root (`scripts/check_doc_links.py`)
and runs in the docs workflow before anything else, so its output IS
a CI log surface: the no-leak standard applies to it the way it
applies to the server. These tests run the real script the way the
workflow does and read both streams whole.
"""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_doc_links.py"

# Credential-shaped, and never a value that exists anywhere real.
SENTINEL = "sk-SENTINEL8f3a1b2c4d5e6f70"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def tree(tmp_path: Path, name: str, text: str) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    page = docs / name
    page.write_text(text, encoding="utf-8")
    return page


def test_a_broken_link_fails_without_republishing_its_destination(
    tmp_path: Path,
) -> None:
    tree(tmp_path, "page.md", f"[key]({SENTINEL}.md)\n")
    done = run(str(tmp_path))
    assert done.returncode == 1
    assert "docs/page.md:1" in done.stdout
    assert "missing target" in done.stdout
    for stream in (done.stdout, done.stderr):
        assert SENTINEL not in stream
        assert "Traceback" not in stream


def test_a_bad_invocation_is_a_sentence_and_exit_two() -> None:
    for done in (run(), run("/nonexistent-root-for-this-test")):
        assert done.returncode == 2
        assert done.stdout == ""
        assert len(done.stderr.strip().splitlines()) == 1
        assert "Traceback" not in done.stderr


def test_a_link_may_not_escape_the_checkout(tmp_path: Path) -> None:
    # /etc/passwd exists on every runner this executes on, so passing
    # would be the bug: existence outside the root must not count.
    tree(tmp_path, "page.md", "[out](../../../../../../etc/passwd)\n")
    done = run(str(tmp_path))
    assert done.returncode == 1
    assert "target outside the checkout" in done.stdout
    assert "passwd" not in done.stdout


def test_duplicate_anchors_advance_past_occupied_slugs(
    tmp_path: Path,
) -> None:
    tree(
        tmp_path,
        "target.md",
        "# Foo\n\n## Foo-1\n\n## Foo\n",
    )
    good = "[a](target.md#foo) [b](target.md#foo-1) [c](target.md#foo-2)\n"
    tree(tmp_path, "page.md", good)
    assert run(str(tmp_path)).returncode == 0
    tree(tmp_path, "page.md", good + "[d](target.md#foo-3)\n")
    done = run(str(tmp_path))
    assert done.returncode == 1
    assert "missing anchor" in done.stdout


def test_an_angle_bracketed_destination_is_checked_not_skipped(
    tmp_path: Path,
) -> None:
    tree(tmp_path, "other.md", "# Other\n")
    tree(tmp_path, "page.md", "[ok](<other.md>)\n")
    assert run(str(tmp_path)).returncode == 0
    tree(tmp_path, "page.md", "[broken](<missing.md>)\n")
    done = run(str(tmp_path))
    assert done.returncode == 1
    assert "missing target" in done.stdout
    assert "missing.md" not in done.stdout
