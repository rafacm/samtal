#!/usr/bin/env python3
"""The upstream drift watch: one manifest parser, four subcommands.

Usage:
  upstream_watch.py check [--manifest PATH] [--notes PATH]
  upstream_watch.py print [--manifest PATH]
  upstream_watch.py report --clones DIR --output FILE [--manifest PATH]
  upstream_watch.py decide --report FILE --issues FILE

`docs/upstream-watch.yaml` names the upstream repositories vinga reads
its wire contract from, the commit each was read at, and the paths that
carry the contract. This script is the only thing that parses it, so
the fact has one home and the workflows below do no selection logic in
shell.

- `check` holds the manifest and the currency table in
  `docs/xiaozhi-notes.md` to full agreement in both directions:
  identical repository sets (a row missing from either side, or
  duplicated in either, is its own failure), equal full commits, equal
  read dates. The docs workflow runs it beside the link checker.
- `print` emits one `<directory> <url>` row per repository for the
  drift workflow's clone loop.
- `report` takes a directory of already-fetched clones, resolves each
  repository's `origin/HEAD` and latest release tag, validates that the
  pin is an ancestor of each target, diffs the watched paths, and
  writes the whole issue body to a file.
- `decide` takes that file and the JSON of open labeled issues and
  answers `create`, `update <number>`, or a refusal.

Exit codes follow `check_doc_links.py`: 0 success, 1 a failure the
caller has to act on, 2 a bad invocation.

Output discipline, also that script's: a failure line names the
repository that disagrees and the kind of disagreement, and nothing
else. Commits, dates and paths are not echoed into a CI log by the
tool that finds them wrong; the repository name is enough to open the
two files side by side. Repository names are themselves held to
`owner/name` before they are printed, so a mangled manifest cannot
turn a failure line into an echo of arbitrary text.

Shell discipline: every git and subprocess call goes out as an
argument list, never through a shell. Upstream commit subjects and
file paths reach the report as file content, so nothing upstream
writes is ever evaluated.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "upstream-watch.yaml"
DEFAULT_NOTES = ROOT / "docs" / "xiaozhi-notes.md"

# The identity of a drift report, so a run updates the page it wrote
# last week rather than whatever happens to carry the label.
ISSUE_TITLE = "Upstream drift report"
MARKER = "<!-- vinga-upstream-drift-report -->"

REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The release-tag policy, stated rather than inferred: `v` then two or
# three dotted numbers and nothing else. A prerelease, a build suffix
# or any other tag shape is not a release for this purpose, because
# "latest tag" over an unconstrained set selects whatever upstream
# happened to push last.
TAG_RE = re.compile(r"^v(\d+)\.(\d+)(?:\.(\d+))?$")

TABLE_HEADER = "| Upstream project | Commit | Clone read |"
TABLE_ROW_RE = re.compile(
    r"^\|\s*\[([^\]]*)\]\([^)]*\)\s*\|\s*`([^`]*)`\s*\|\s*([^|]*?)\s*\|\s*$"
)


class ManifestError(Exception):
    """The manifest is not usable. The message is a fixed sentence."""


def safe_name(name: str) -> str:
    """A repository name, or a placeholder if it is not one.

    Nothing this script prints comes from the manifest unchecked.
    """
    return name if REPO_NAME_RE.match(name) else "<a row with a malformed name>"


def clone_dir(name: str) -> str:
    """The directory a repository's clone lives in under --clones."""
    return name.replace("/", "__")


def load_manifest(path: Path) -> list:
    """The manifest's rows, in the manifest's own order.

    Every ordering downstream is this one, so two runs over the same
    manifest produce byte-identical output.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        raise ManifestError("the manifest could not be read") from None
    except yaml.YAMLError:
        raise ManifestError("the manifest is not valid YAML") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("repositories"), list):
        raise ManifestError("the manifest has no repositories list")
    rows = []
    for entry in raw["repositories"]:
        if not isinstance(entry, dict):
            raise ManifestError("a manifest entry is not a mapping")
        name = entry.get("repository")
        url = entry.get("url")
        pinned = entry.get("pinned")
        read = entry.get("read")
        paths = entry.get("paths")
        if not isinstance(name, str) or not REPO_NAME_RE.match(name):
            raise ManifestError("a manifest entry has no owner/name repository")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ManifestError(f"{name}: the manifest url is not an https URL")
        if not isinstance(pinned, str) or not SHA_RE.match(pinned):
            raise ManifestError(f"{name}: the manifest pin is not a full commit")
        if not isinstance(read, str) or not DATE_RE.match(read):
            raise ManifestError(f"{name}: the manifest read date is not YYYY-MM-DD")
        if not isinstance(paths, list) or not paths:
            raise ManifestError(f"{name}: the manifest entry lists no paths")
        for p in paths:
            if not isinstance(p, str) or not p or p.startswith("-"):
                raise ManifestError(f"{name}: a watched path is not usable")
        rows.append(
            {
                "repository": name,
                "url": url,
                "pinned": pinned,
                "read": read,
                "paths": list(paths),
            }
        )
    if not rows:
        raise ManifestError("the manifest lists no repositories")
    return rows


def table_rows(path: Path) -> list:
    """The currency table's rows, in the table's own order.

    Rows are read positionally from the one table whose header is the
    currency table's, so prose above and below it is not table content
    and a second table on the page is not this one.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise ManifestError("the notes could not be read") from None
    try:
        start = lines.index(TABLE_HEADER)
    except ValueError:
        raise ManifestError("the notes have no upstream currency table") from None
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        m = TABLE_ROW_RE.match(line)
        if not m:
            raise ManifestError("a currency table row is not in the expected shape")
        rows.append(
            {
                "repository": m.group(1).strip(),
                "pinned": m.group(2).strip(),
                "read": m.group(3).strip(),
            }
        )
    if not rows:
        raise ManifestError("the notes' upstream currency table has no rows")
    return rows


def duplicates(names: list) -> list:
    seen: set = set()
    dupes: list = []
    for name in names:
        if name in seen and name not in dupes:
            dupes.append(name)
        seen.add(name)
    return dupes


def cmd_check(args) -> int:
    """Manifest and currency table agree, in both directions."""
    try:
        manifest = load_manifest(args.manifest)
        notes = table_rows(args.notes)
    except ManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    failures = 0

    def fail(sentence: str) -> None:
        nonlocal failures
        print(sentence)
        failures += 1

    manifest_names = [r["repository"] for r in manifest]
    notes_names = [r["repository"] for r in notes]
    for name in duplicates(manifest_names):
        fail(f"{safe_name(name)}: listed more than once in the manifest")
    for name in duplicates(notes_names):
        fail(f"{safe_name(name)}: listed more than once in the notes' currency table")

    by_notes = {}
    for row in notes:
        by_notes.setdefault(row["repository"], row)
    manifest_set = set(manifest_names)
    for name in manifest_names:
        if name not in by_notes:
            fail(f"{safe_name(name)}: in the manifest, missing from the notes' table")
    for name in notes_names:
        if name not in manifest_set:
            fail(f"{safe_name(name)}: in the notes' table, missing from the manifest")

    for row in manifest:
        other = by_notes.get(row["repository"])
        if other is None:
            continue
        name = safe_name(row["repository"])
        if row["pinned"] != other["pinned"]:
            fail(f"{name}: the manifest and the notes' table pin different commits")
        if row["read"] != other["read"]:
            fail(f"{name}: the manifest and the notes' table give different read dates")

    print(f"checked {len(manifest)} repositories, {failures} failures")
    return 1 if failures else 0


def cmd_print(args) -> int:
    """One `<directory> <url>` row per repository, for the clone loop."""
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for row in manifest:
        print(f"{clone_dir(row['repository'])} {row['url']}")
    return 0


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """A git call as an argument list. Never a shell, ever."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def latest_release_tag(repo: Path):
    """The highest tag matching the stated release policy, or None."""
    done = git(repo, "tag", "--list")
    if done.returncode != 0:
        return None
    best = None
    for line in done.stdout.splitlines():
        m = TAG_RE.match(line.strip())
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
        if best is None or key > best[0]:
            best = (key, line.strip())
    return None if best is None else best[1]


def fence_for(text: str) -> str:
    """A fence longer than any backtick run the block contains.

    Upstream owns these subjects and paths. A three-backtick fence
    around a subject that itself carries three backticks would end the
    block early and change what the report says, so the fence grows
    instead and the content travels byte for byte.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def block(text: str) -> str:
    fence = fence_for(text)
    return f"{fence}\n{text}\n{fence}\n"


def cmd_report(args) -> int:
    """Build the drift report, or write an empty file when nothing moved."""
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    clones = args.clones
    if not clones.is_dir():
        print("the given clones directory is not a directory", file=sys.stderr)
        return 2

    # Validation before any diffing, deliberately: a clone that is not
    # there, or a pin that does not resolve in it, means the run
    # measured nothing and must not report "no drift".
    resolved = []
    invalid = 0
    for row in manifest:
        name = safe_name(row["repository"])
        repo = clones / clone_dir(row["repository"])
        if not (repo / ".git").exists() and not (repo / "HEAD").exists():
            print(f"{name}: no clone was found for this repository", file=sys.stderr)
            invalid += 1
            continue
        if git(repo, "rev-parse", "--verify", "--quiet", row["pinned"] + "^{commit}").returncode:
            print(f"{name}: the pinned commit does not resolve in its clone", file=sys.stderr)
            invalid += 1
            continue
        head = git(repo, "rev-parse", "--verify", "--quiet", "origin/HEAD")
        if head.returncode:
            print(f"{name}: origin/HEAD does not resolve in its clone", file=sys.stderr)
            invalid += 1
            continue
        resolved.append((row, repo, head.stdout.strip()))
    if invalid:
        return 1

    sections = []
    news = False
    for row, repo, head_sha in resolved:
        name = safe_name(row["repository"])
        lines = [f"## {name}", ""]
        lines.append(
            f"Pinned at `{row['pinned']}`, read {row['read']}."
        )
        lines.append("")

        targets = [("upstream HEAD", "origin/HEAD", head_sha)]
        tag = latest_release_tag(repo)
        if tag is None:
            lines.append(
                "No tag matches the release policy, so only upstream HEAD "
                "is compared for this repository."
            )
            lines.append("")
        else:
            tag_sha = git(repo, "rev-parse", "--verify", "--quiet", tag + "^{commit}")
            if tag_sha.returncode:
                print(
                    f"{name}: the latest release tag does not resolve in its clone",
                    file=sys.stderr,
                )
                return 1
            targets.append((f"release {tag}", tag, tag_sha.stdout.strip()))

        for label, rev, sha in targets:
            ahead = git(repo, "merge-base", "--is-ancestor", row["pinned"], rev)
            if ahead.returncode != 0:
                behind = git(repo, "merge-base", "--is-ancestor", rev, row["pinned"])
                relation = (
                    "is behind the pinned commit"
                    if behind.returncode == 0
                    else "has diverged from the pinned commit"
                )
                lines.append(f"### {label} (`{sha}`)")
                lines.append("")
                lines.append(
                    f"Not diffed: {label} {relation}, so a diff from the pin "
                    "would read backwards. This needs a human look."
                )
                lines.append("")
                news = True
                continue

            span = f"{row['pinned']}..{sha}"
            names = git(
                repo, "diff", "--name-status", span, "--", *row["paths"]
            )
            log = git(repo, "log", "--oneline", span, "--", *row["paths"])
            if names.returncode != 0 or log.returncode != 0:
                print(f"{name}: git could not diff the watched paths", file=sys.stderr)
                return 1
            changed = names.stdout.strip("\n")
            commits = log.stdout.strip("\n")
            if not changed and not commits:
                continue
            news = True
            lines.append(f"### {label} (`{sha}`)")
            lines.append("")
            if changed:
                lines.append("Changed files:")
                lines.append("")
                lines.append(block(changed))
            if commits:
                lines.append("Commits:")
                lines.append("")
                lines.append(block(commits))

        sections.append("\n".join(lines).rstrip() + "\n")

    if not news:
        args.output.write_text("", encoding="utf-8")
        print("no watched upstream path moved")
        return 0

    # Every prose line below is one whole paragraph, however long it
    # runs. GitHub renders an issue body with the `breaks` extension,
    # so a newline inside a paragraph becomes a literal line break and
    # a hand-wrapped sentence arrives shattered mid-clause.
    body = [
        MARKER,
        "",
        "Watched upstream paths have moved since the commits vinga's protocol notes were read against. This issue is rewritten by each run of the drift watch, so it stays one page rather than becoming a pile: what it says now is what is outstanding now.",  # noqa: E501
        "",
        "It lists file names and commit subjects and no patch text, on purpose. Triage needs to know whether to go and read; the reading happens in a clone.",  # noqa: E501
        "",
    ]
    body.extend(sections)
    body.extend(
        [
            "## Resolving this",
            "",
            "1. Read the changed upstream source in a clone.",
            "2. Update the affected sections of `docs/xiaozhi-notes.md`.",
            "3. Bump the pinned commit and the read date in `docs/upstream-watch.yaml` and in the notes' currency table, together. The docs workflow fails if they disagree.",  # noqa: E501
            "4. Adjust implementation and tests only where the wire actually moved. Most upstream commits move neither.",  # noqa: E501
            "",
            "Only the deletion side of a rename is visible here: a file moved out of the watched paths shows up as a delete and nothing else. Treat this report as a prompt to re-read, never as the whole of what changed.",  # noqa: E501
            "",
        ]
    )
    args.output.write_text("\n".join(body), encoding="utf-8")
    print("wrote a drift report")
    return 0


def cmd_decide(args) -> int:
    """create, update <number>, or a refusal naming the ambiguity."""
    try:
        report = args.report.read_text(encoding="utf-8")
    except OSError:
        print("the report file could not be read", file=sys.stderr)
        return 2
    if not report.strip():
        print("the report is empty, so there is nothing to write", file=sys.stderr)
        return 2
    try:
        issues = json.loads(args.issues.read_text(encoding="utf-8"))
    except OSError:
        print("the issues file could not be read", file=sys.stderr)
        return 2
    except json.JSONDecodeError:
        print("the issues file is not valid JSON", file=sys.stderr)
        return 2
    if not isinstance(issues, list):
        print("the issues file is not a list of issues", file=sys.stderr)
        return 2

    # Identity is the exact title plus the marker in the body. A label
    # alone is not an identity: anyone can apply it, and a run that
    # overwrote a human's issue because it shared a label would be a
    # worse failure than refusing.
    matches = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        if issue.get("title") != ISSUE_TITLE:
            continue
        body = issue.get("body")
        if not isinstance(body, str) or MARKER not in body:
            continue
        matches.append(number)

    if len(matches) > 1:
        listed = ", ".join(f"#{n}" for n in sorted(matches))
        print(
            f"more than one open issue matches the drift report: {listed}. "
            "Close or retitle all but one and re-run.",
            file=sys.stderr,
        )
        return 1
    if matches:
        print(f"update {matches[0]}")
    else:
        print("create")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="upstream_watch.py",
        description="The upstream wire-contract drift watch.",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    check = subs.add_parser("check", help="manifest and notes agree")
    check.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    check.add_argument("--notes", type=Path, default=DEFAULT_NOTES)
    check.set_defaults(func=cmd_check)

    emit = subs.add_parser("print", help="clone rows for the workflow")
    emit.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    emit.set_defaults(func=cmd_print)

    report = subs.add_parser("report", help="build the drift report")
    report.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    report.add_argument("--clones", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    report.set_defaults(func=cmd_report)

    decide = subs.add_parser("decide", help="create, update or refuse")
    decide.add_argument("--report", type=Path, required=True)
    decide.add_argument("--issues", type=Path, required=True)
    decide.set_defaults(func=cmd_decide)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
