"""The upstream drift watch's contract, exercised as a subprocess.

The script lives at the repository root (`scripts/upstream_watch.py`),
runs in the docs workflow and in the weekly drift workflow, and its
output is a CI log surface and an issue body. So these tests run the
real script the way the workflows do and read both streams whole, the
way the link checker's suite from #329 does.

Everything the drift half touches is a synthetic git repository built
under `tmp_path`: no network, no clock, and nothing shared between
tests, because the unit lane runs distributed (`-n auto --dist
loadfile`). The agreement half reads the committed manifest and notes
once, to prove the committed state passes, and works on copies under
`tmp_path` for every mutation.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "upstream_watch.py"
MANIFEST = ROOT / "docs" / "upstream-watch.yaml"
NOTES = ROOT / "docs" / "xiaozhi-notes.md"

MARKER = "<!-- vinga-upstream-drift-report -->"
FIRMWARE = "78/xiaozhi-esp32"
SERVER = "xinnan-tech/xiaozhi-esp32-server"

# A repository whose git behavior is the subject: the ambient user's
# git config is not, so it is excluded rather than inherited.
GIT_ENV = {
    "PATH": os.environ.get("PATH", ""),
    "HOME": os.environ.get("HOME", "/tmp"),
    "GIT_AUTHOR_NAME": "vinga tests",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "vinga tests",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def git(repo: Path, *args: str) -> None:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=GIT_ENV,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr


def head_of(repo: Path, rev: str = "HEAD") -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", rev],
        capture_output=True,
        text=True,
        env=GIT_ENV,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def commit(repo: Path, files: dict, subject: str) -> str:
    """Write files, stage everything, commit, and return the sha."""
    for name, text in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            target.unlink()
        else:
            target.write_text(text, encoding="utf-8")
    git(repo, "add", "--all")
    git(repo, "commit", "--allow-empty", "-m", subject)
    return head_of(repo)


def upstream(tmp_path: Path, name: str) -> Path:
    """A synthetic upstream with one commit under the watched path."""
    repo = tmp_path / "upstream" / name
    repo.mkdir(parents=True)
    git(repo, "init", "--quiet", "--initial-branch=main")
    commit(
        repo,
        {"watched/one.txt": "first\n", "elsewhere/other.txt": "ignored\n"},
        "the commit the notes were read at",
    )
    return repo


def stage(tmp_path: Path, entries: list) -> tuple:
    """A manifest plus a clone per entry, the way the workflow lays them out.

    Each entry is (repository name, upstream path, pinned sha, watched
    paths). Clones are made the way the workflow makes them, from a
    local path rather than the network.
    """
    clones = tmp_path / "clones"
    clones.mkdir(exist_ok=True)
    rows = []
    for repository, source, pinned, paths in entries:
        target = clones / repository.replace("/", "__")
        done = subprocess.run(
            [
                "git",
                "clone",
                "--no-checkout",
                # Over the git transport rather than git's local
                # hardlink-or-copy shortcut, which is both what the
                # workflow's network clone does and a flake this suite
                # has already seen (a copy into an object directory
                # that did not exist yet).
                "--no-local",
                "--quiet",
                str(source),
                str(target),
            ],
            capture_output=True,
            text=True,
            env=GIT_ENV,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
        rows.append(
            {
                "repository": repository,
                "url": f"https://example.invalid/{repository}",
                "pinned": pinned,
                "read": "2026-07-29",
                "paths": list(paths),
            }
        )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"repositories": rows}), encoding="utf-8")
    return manifest, clones


def report(tmp_path: Path, manifest: Path, clones: Path):
    out = tmp_path / "report.md"
    done = run(
        "report",
        "--manifest",
        str(manifest),
        "--clones",
        str(clones),
        "--output",
        str(out),
    )
    return done, out


# ---------------------------------------------------------------- report


def test_nothing_moved_writes_an_empty_report(tmp_path: Path) -> None:
    repo = upstream(tmp_path, "acme__thing")
    manifest, clones = stage(
        tmp_path, [("acme/thing", repo, head_of(repo), ["watched/"])]
    )
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    assert out.read_text(encoding="utf-8") == ""
    assert "no watched upstream path moved" in done.stdout


def test_a_commit_outside_the_watched_paths_is_not_drift(tmp_path: Path) -> None:
    repo = upstream(tmp_path, "acme__thing")
    pinned = head_of(repo)
    commit(repo, {"elsewhere/other.txt": "changed\n"}, "unrelated churn")
    manifest, clones = stage(tmp_path, [("acme/thing", repo, pinned, ["watched/"])])
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    assert out.read_text(encoding="utf-8") == ""


def test_a_changed_watched_path_is_reported(tmp_path: Path) -> None:
    repo = upstream(tmp_path, "acme__thing")
    pinned = head_of(repo)
    commit(repo, {"watched/one.txt": "second\n"}, "move the wire")
    manifest, clones = stage(tmp_path, [("acme/thing", repo, pinned, ["watched/"])])
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    body = out.read_text(encoding="utf-8")
    assert body.startswith(MARKER)
    assert "## acme/thing" in body
    assert "### upstream HEAD" in body
    assert "M\twatched/one.txt" in body
    assert "move the wire" in body
    assert "## Resolving this" in body


def test_a_deleted_watched_path_is_reported_as_a_delete(tmp_path: Path) -> None:
    repo = upstream(tmp_path, "acme__thing")
    pinned = head_of(repo)
    commit(repo, {"watched/one.txt": None}, "drop the file")
    manifest, clones = stage(tmp_path, [("acme/thing", repo, pinned, ["watched/"])])
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    body = out.read_text(encoding="utf-8")
    assert "D\twatched/one.txt" in body
    assert "drop the file" in body


def test_a_tag_behind_the_pin_is_reported_and_never_diffed(tmp_path: Path) -> None:
    repo = upstream(tmp_path, "acme__thing")
    git(repo, "tag", "v1.0.0")
    commit(repo, {"watched/one.txt": "second\n"}, "the commit the notes moved to")
    pinned = head_of(repo)
    manifest, clones = stage(tmp_path, [("acme/thing", repo, pinned, ["watched/"])])
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    body = out.read_text(encoding="utf-8")
    assert "release v1.0.0" in body
    assert "is behind the pinned commit" in body
    # The pin is at HEAD, so the only content in the report is the
    # backwards-target line: no diff was taken in either direction.
    assert "Changed files:" not in body
    assert "the commit the notes moved to" not in body


def test_a_divergent_tag_is_reported_and_never_diffed(tmp_path: Path) -> None:
    repo = upstream(tmp_path, "acme__thing")
    git(repo, "checkout", "--quiet", "-b", "side")
    commit(repo, {"watched/one.txt": "sideways\n"}, "a release off to one side")
    git(repo, "tag", "v9.9.9")
    git(repo, "checkout", "--quiet", "main")
    # The pin sits on mainline past the fork, so neither it nor the tag
    # is an ancestor of the other. That is the divergence.
    pinned = commit(repo, {"watched/one.txt": "onwards\n"}, "the notes moved here")
    commit(repo, {"watched/one.txt": "onwards again\n"}, "mainline keeps going")
    manifest, clones = stage(tmp_path, [("acme/thing", repo, pinned, ["watched/"])])
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    body = out.read_text(encoding="utf-8")
    assert "release v9.9.9" in body
    assert "has diverged from the pinned commit" in body
    assert "a release off to one side" not in body
    # Mainline is a descendant, so its half is a real diff.
    assert "mainline keeps going" in body


def test_a_missing_pin_refuses_and_names_the_repository(tmp_path: Path) -> None:
    repo = upstream(tmp_path, "acme__thing")
    absent = "0" * 40
    manifest, clones = stage(tmp_path, [("acme/thing", repo, absent, ["watched/"])])
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 1
    assert "acme/thing" in done.stderr
    assert "does not resolve" in done.stderr
    assert "Traceback" not in done.stderr
    assert not out.exists()


def test_no_qualifying_tag_says_so_and_still_compares_head(tmp_path: Path) -> None:
    repo = upstream(tmp_path, "acme__thing")
    pinned = head_of(repo)
    for tag in ("v1.0.0-rc1", "nightly", "2.0.0", "v1.0.0.1"):
        git(repo, "tag", tag)
    commit(repo, {"watched/one.txt": "second\n"}, "move the wire")
    manifest, clones = stage(tmp_path, [("acme/thing", repo, pinned, ["watched/"])])
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    body = out.read_text(encoding="utf-8")
    assert "No tag matches the release policy" in body
    assert "### release" not in body
    assert "move the wire" in body


def test_the_highest_qualifying_tag_wins_over_lexical_order(tmp_path: Path) -> None:
    repo = upstream(tmp_path, "acme__thing")
    pinned = head_of(repo)
    commit(repo, {"watched/one.txt": "second\n"}, "on the way to ten")
    git(repo, "tag", "v9.0.0")
    commit(repo, {"watched/one.txt": "third\n"}, "the tenth release")
    git(repo, "tag", "v10.0.0")
    manifest, clones = stage(tmp_path, [("acme/thing", repo, pinned, ["watched/"])])
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    body = out.read_text(encoding="utf-8")
    assert "release v10.0.0" in body
    assert "release v9.0.0" not in body


def test_repositories_are_reported_in_the_manifests_own_order(tmp_path: Path) -> None:
    first = upstream(tmp_path, "one")
    second = upstream(tmp_path, "two")
    pins = (head_of(first), head_of(second))
    commit(first, {"watched/one.txt": "changed\n"}, "first moves")
    commit(second, {"watched/one.txt": "changed\n"}, "second moves")
    entries = [
        ("zeta/one", first, pins[0], ["watched/"]),
        ("alpha/two", second, pins[1], ["watched/"]),
    ]
    manifest, clones = stage(tmp_path, entries)
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    body = out.read_text(encoding="utf-8")
    assert body.index("## zeta/one") < body.index("## alpha/two")


def test_upstream_metacharacters_round_trip_into_the_report(tmp_path: Path) -> None:
    """Nothing upstream writes is evaluated, and nothing is mangled.

    The path and the subject below are a command substitution, a
    pipeline and a fence break. Had any of this gone through a shell,
    `$(echo evaluated)` would have collapsed to one word; had the fence
    been a fixed three backticks, the subject's own three would have
    closed the block early. Both survive byte for byte or this fails.
    """
    nasty_path = "watched/a$(echo evaluated)|b`echo evaluated`&c;d.txt"
    nasty_subject = "fix: $(echo evaluated) `id` && rm -rf / ; ``` \" ' | * > x"
    repo = upstream(tmp_path, "acme__thing")
    pinned = head_of(repo)
    commit(repo, {nasty_path: "payload\n"}, nasty_subject)
    manifest, clones = stage(tmp_path, [("acme/thing", repo, pinned, ["watched/"])])
    done, out = report(tmp_path, manifest, clones)
    assert done.returncode == 0, done.stderr
    body = out.read_text(encoding="utf-8")
    assert nasty_path in body
    assert nasty_subject in body
    assert "evaluated\n" not in body.replace(nasty_path, "").replace(nasty_subject, "")
    # The fence grew past the subject's own three backticks, so the
    # block still closes where it should.
    assert "````" in body


# ---------------------------------------------------------------- decide


def issues_file(tmp_path: Path, issues: list) -> Path:
    path = tmp_path / "issues.json"
    path.write_text(json.dumps(issues), encoding="utf-8")
    return path


def report_file(tmp_path: Path) -> Path:
    path = tmp_path / "body.md"
    path.write_text(f"{MARKER}\n\nsomething moved\n", encoding="utf-8")
    return path


def decide(body: Path, issues: Path) -> subprocess.CompletedProcess:
    return run("decide", "--report", str(body), "--issues", str(issues))


def test_no_candidate_issue_means_create(tmp_path: Path) -> None:
    issues = issues_file(
        tmp_path,
        [
            {"number": 7, "title": "Something else", "body": MARKER},
            {"number": 8, "title": "Upstream drift report", "body": "no marker here"},
        ],
    )
    done = decide(report_file(tmp_path), issues)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "create"


def test_one_candidate_issue_means_update_that_number(tmp_path: Path) -> None:
    issues = issues_file(
        tmp_path,
        [
            {"number": 7, "title": "Something else", "body": MARKER},
            {
                "number": 412,
                "title": "Upstream drift report",
                "body": f"{MARKER}\n\nlast week's report\n",
            },
        ],
    )
    done = decide(report_file(tmp_path), issues)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "update 412"


def test_two_candidate_issues_refuse_and_name_both(tmp_path: Path) -> None:
    issues = issues_file(
        tmp_path,
        [
            {"number": 412, "title": "Upstream drift report", "body": MARKER},
            {"number": 511, "title": "Upstream drift report", "body": MARKER},
        ],
    )
    done = decide(report_file(tmp_path), issues)
    assert done.returncode == 1
    assert done.stdout.strip() == ""
    assert "#412" in done.stderr
    assert "#511" in done.stderr
    assert "Traceback" not in done.stderr


def test_an_empty_report_is_never_written_anywhere(tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    done = decide(empty, issues_file(tmp_path, []))
    assert done.returncode == 2
    assert done.stdout.strip() == ""
    assert "Traceback" not in done.stderr


# ----------------------------------------------------------------- check


def test_the_committed_manifest_and_notes_agree() -> None:
    done = run("check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "0 failures" in done.stdout


def copies(tmp_path: Path) -> tuple:
    manifest = tmp_path / "upstream-watch.yaml"
    notes = tmp_path / "xiaozhi-notes.md"
    manifest.write_text(MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
    notes.write_text(NOTES.read_text(encoding="utf-8"), encoding="utf-8")
    return manifest, notes


def check(manifest: Path, notes: Path) -> subprocess.CompletedProcess:
    return run("check", "--manifest", str(manifest), "--notes", str(notes))


def test_the_copies_agree_before_any_mutation(tmp_path: Path) -> None:
    manifest, notes = copies(tmp_path)
    assert check(manifest, notes).returncode == 0


def test_a_mutated_commit_fails_and_names_the_repository(tmp_path: Path) -> None:
    manifest, notes = copies(tmp_path)
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace(
            "dd99da00dc4c89ed4ab07fcec038c03f13f4de50",
            "dd99da00dc4c89ed4ab07fcec038c03f13f4de51",
        ),
        encoding="utf-8",
    )
    done = check(manifest, notes)
    assert done.returncode == 1
    assert f"{FIRMWARE}: " in done.stdout
    assert "different commits" in done.stdout
    assert SERVER not in done.stdout
    # The disagreeing values are the thing being reported, not echoed.
    assert "dd99da00" not in done.stdout


def test_a_mutated_read_date_fails_and_names_the_repository(tmp_path: Path) -> None:
    manifest, notes = copies(tmp_path)
    notes.write_text(
        notes.read_text(encoding="utf-8").replace("| 2026-07-28 |", "| 2026-08-28 |"),
        encoding="utf-8",
    )
    done = check(manifest, notes)
    assert done.returncode == 1
    assert f"{SERVER}: " in done.stdout
    assert "different read dates" in done.stdout
    assert "2026-08-28" not in done.stdout


def test_a_row_missing_from_the_notes_fails(tmp_path: Path) -> None:
    manifest, notes = copies(tmp_path)
    kept = [
        line
        for line in notes.read_text(encoding="utf-8").splitlines()
        if "xiaozhi-esp32-server)" not in line
    ]
    notes.write_text("\n".join(kept) + "\n", encoding="utf-8")
    done = check(manifest, notes)
    assert done.returncode == 1
    assert f"{SERVER}: in the manifest, missing from the notes' table" in done.stdout


def test_a_row_missing_from_the_manifest_fails(tmp_path: Path) -> None:
    manifest, notes = copies(tmp_path)
    parsed = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    parsed["repositories"] = [
        row for row in parsed["repositories"] if row["repository"] != SERVER
    ]
    manifest.write_text(yaml.safe_dump(parsed), encoding="utf-8")
    done = check(manifest, notes)
    assert done.returncode == 1
    assert f"{SERVER}: in the notes' table, missing from the manifest" in done.stdout


def test_a_duplicated_manifest_row_fails(tmp_path: Path) -> None:
    manifest, notes = copies(tmp_path)
    parsed = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    parsed["repositories"].append(dict(parsed["repositories"][0]))
    manifest.write_text(yaml.safe_dump(parsed), encoding="utf-8")
    done = check(manifest, notes)
    assert done.returncode == 1
    assert f"{FIRMWARE}: listed more than once in the manifest" in done.stdout


def test_a_duplicated_notes_row_fails(tmp_path: Path) -> None:
    manifest, notes = copies(tmp_path)
    out = []
    for line in notes.read_text(encoding="utf-8").splitlines():
        out.append(line)
        if line.startswith(f"| [{FIRMWARE}]("):
            out.append(line)
    notes.write_text("\n".join(out) + "\n", encoding="utf-8")
    done = check(manifest, notes)
    assert done.returncode == 1
    assert (
        f"{FIRMWARE}: listed more than once in the notes' currency table" in done.stdout
    )


def test_an_unparseable_manifest_is_a_sentence_not_a_traceback(
    tmp_path: Path,
) -> None:
    manifest, notes = copies(tmp_path)
    manifest.write_text("repositories: [ unterminated\n", encoding="utf-8")
    done = check(manifest, notes)
    assert done.returncode == 1
    assert done.stdout == ""
    assert len(done.stderr.strip().splitlines()) == 1
    assert "Traceback" not in done.stderr


# ----------------------------------------------------------------- print


def test_print_emits_one_clone_row_per_repository(tmp_path: Path) -> None:
    done = run("print", "--manifest", str(MANIFEST))
    assert done.returncode == 0, done.stderr
    rows = done.stdout.strip().splitlines()
    assert rows == [
        "78__xiaozhi-esp32 https://github.com/78/xiaozhi-esp32",
        "xinnan-tech__xiaozhi-esp32-server "
        "https://github.com/xinnan-tech/xiaozhi-esp32-server",
    ]


def test_a_bad_invocation_is_exit_two() -> None:
    done = run()
    assert done.returncode == 2
    assert "Traceback" not in done.stderr
