#!/usr/bin/env python3
"""The upstream drift watch: one manifest parser, four subcommands.

Usage:
  upstream_watch.py check [--manifest PATH] [--notes PATH]
  upstream_watch.py clone --clones DIR [--manifest PATH]
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
- `clone` fetches every watched upstream into a directory, blobless
  and unchecked-out, with an explicit all-tags fetch behind it. It
  lives here rather than in the workflow's shell so that no URL is
  ever handed to a shell and no git diagnostic ever reaches the log.
- `print` emits one `<directory> <url>` row per repository.
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

Every failure leaves through one door, `Refusal`, whose message is
always a fixed sentence assembled from literals and already-validated
identifiers. Three consequences worth stating, because each was a real
hole:

- The argument parser does not answer a bad invocation in argparse's
  own words. Argparse repeats what was typed ("unrecognized arguments:
  ..."), and a secret typed as an argument would land in a public
  Actions log. `_FixedMessageParser` answers with a usage line of this
  module's own instead.
- Bytes are decoded under a stated policy rather than by accident.
  Files this repository owns (the manifest, the notes, the issues JSON,
  the report) are decoded strictly, and undecodable input is a refusal,
  because a document of ours that is not UTF-8 is a fault to fix.
  Upstream's own bytes (git's stdout) are decoded with
  `errors="replace"`, because a subject in some other encoding is not a
  reason to refuse to report drift. That is safe here and only here:
  U+FFFD is not a backtick, a newline, a tab or a `#`, so a replaced
  byte can neither close a fence early nor forge a heading, a row or a
  name-status separator. It can make a subject less legible, which is
  what "go and read the source" is for.
- A refusal is raised after its `except` arm, never inside it, which
  is the discipline `vinga_server.config.cli` states at length: an
  exception raised while another is being handled carries the handled
  one on `__context__` for anything walking the chain, and
  `from None` suppresses the traceback rather than the chain.

Shell discipline: every git and subprocess call goes out as an
argument list, never through a shell. Upstream commit subjects and
file paths reach the report as file content, so nothing upstream
writes is ever evaluated.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

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
#
# No leading zeros, which is semver's rule and here is a determinism
# rule: `v01.2.0` and `v1.2.0` would compare equal on their numbers,
# and "highest" would then mean "whichever git listed first".
TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?$")

# A ceiling on any one git call. A clone that stops to ask a runner
# for a password would otherwise hold the job until the job's own
# timeout, with nothing in the log saying why.
GIT_TIMEOUT_SECONDS = 900

TABLE_HEADER = "| Upstream project | Commit | Clone read |"
TABLE_ROW_RE = re.compile(
    r"^\|\s*\[([^\]]*)\]\([^)]*\)\s*\|\s*`([^`]*)`\s*\|\s*([^|]*?)\s*\|\s*$"
)

USAGE = (
    "usage: upstream_watch.py {check|clone|print|report|decide} [options]\n"
    "the arguments were not understood; see the module docstring"
)


class Refusal(Exception):
    """A refusal. Its message is always a fixed sentence."""


class _FixedMessageParser(argparse.ArgumentParser):
    """An argument parser that never repeats what was typed.

    Argparse's own error path prints the offending arguments verbatim.
    This workflow's logs are public and its arguments are paths, so the
    parser answers with its own usage line and nothing else.
    """

    def error(self, message: str) -> None:
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)


class GitResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def decode_upstream(raw: bytes) -> str:
    """Upstream's bytes, made printable without letting them forge structure.

    See the module docstring for why `replace` is the right policy on
    this side of the boundary and the wrong one on the other.
    """
    return raw.decode("utf-8", errors="replace")


def read_ours(path: Path, what: str) -> str:
    """A document this repository owns, decoded strictly.

    `what` is a noun phrase literal from the call site, never
    anything read.
    """
    problem = None
    try:
        raw = path.read_bytes()
    except OSError:
        problem = f"{what} could not be read"
    if problem is not None:
        raise Refusal(problem)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        problem = f"{what} could not be decoded as UTF-8"
    raise Refusal(problem)


def write_output(path: Path, text: str) -> None:
    problem = None
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        problem = "the report could not be written to the given output path"
    if problem is not None:
        raise Refusal(problem)


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
    text = read_ours(path, "the manifest")
    problem = None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        problem = "the manifest is not valid YAML"
    if problem is not None:
        raise Refusal(problem)
    if not isinstance(raw, dict) or not isinstance(raw.get("repositories"), list):
        raise Refusal("the manifest has no repositories list")
    rows = []
    for entry in raw["repositories"]:
        if not isinstance(entry, dict):
            raise Refusal("a manifest entry is not a mapping")
        name = entry.get("repository")
        url = entry.get("url")
        pinned = entry.get("pinned")
        read = entry.get("read")
        paths = entry.get("paths")
        if not isinstance(name, str) or not REPO_NAME_RE.match(name):
            raise Refusal("a manifest entry has no owner/name repository")
        # https is what upstreams are fetched over and what the
        # committed manifest carries; `check` holds it to that. file://
        # is accepted by the parser alone, so the clone path can be
        # exercised against a local repository with no network.
        if not isinstance(url, str) or not url.startswith(("https://", "file://")):
            raise Refusal(f"{name}: the manifest url is not an https or file URL")
        if not isinstance(pinned, str) or not SHA_RE.match(pinned):
            raise Refusal(f"{name}: the manifest pin is not a full commit")
        if not isinstance(read, str) or not DATE_RE.match(read):
            raise Refusal(f"{name}: the manifest read date is not YYYY-MM-DD")
        if not isinstance(paths, list) or not paths:
            raise Refusal(f"{name}: the manifest entry lists no paths")
        for p in paths:
            if not isinstance(p, str) or not p or p.startswith("-"):
                raise Refusal(f"{name}: a watched path is not usable")
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
        raise Refusal("the manifest lists no repositories")
    return rows


def table_rows(path: Path) -> list:
    """The currency table's rows, in the table's own order.

    Rows are read positionally from the one table whose header is the
    currency table's, so prose above and below it is not table content
    and a second table on the page is not this one.
    """
    lines = read_ours(path, "the notes").splitlines()
    if TABLE_HEADER not in lines:
        raise Refusal("the notes have no upstream currency table")
    start = lines.index(TABLE_HEADER)
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        m = TABLE_ROW_RE.match(line)
        if not m:
            raise Refusal("a currency table row is not in the expected shape")
        rows.append(
            {
                "repository": m.group(1).strip(),
                "pinned": m.group(2).strip(),
                "read": m.group(3).strip(),
            }
        )
    if not rows:
        raise Refusal("the notes' upstream currency table has no rows")
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
    manifest = load_manifest(args.manifest)
    notes = table_rows(args.notes)

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
    for row in load_manifest(args.manifest):
        print(f"{clone_dir(row['repository'])} {row['url']}")
    return 0


def run_git(argv: list) -> GitResult:
    """A git call as an argument list. Never a shell, ever.

    Both streams are captured, always. On a failed clone or fetch git
    writes the URL it was handed and whatever the remote said straight
    to stderr, and an inherited stderr puts all of that into a public
    Actions log; every caller below answers with a fixed sentence
    instead.

    The environment is narrowed for the same reason a timeout exists:
    a network call that stops to ask a human for a password does not
    fail, it hangs, and a runner has no human.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["GCM_INTERACTIVE"] = "never"
    problem = None
    try:
        done = subprocess.run(
            ["git", *argv],
            capture_output=True,
            check=False,
            env=env,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except OSError:
        problem = "git could not be run"
    except subprocess.TimeoutExpired:
        problem = "git took too long and was stopped"
    if problem is not None:
        raise Refusal(problem)
    return GitResult(
        done.returncode, decode_upstream(done.stdout), decode_upstream(done.stderr)
    )


def git(repo: Path, *args: str) -> GitResult:
    return run_git(["-C", str(repo), *args])


def cmd_clone(args) -> int:
    """Fetch every watched upstream into --clones, quietly.

    This is a subcommand rather than three lines of shell in the
    workflow because the shell version had to be handed the URLs, and
    handing a URL to a shell loop is both an injection surface and, on
    failure, git's stderr in the log.
    """
    manifest = load_manifest(args.manifest)
    problem = None
    try:
        args.clones.mkdir(parents=True, exist_ok=True)
    except OSError:
        problem = "the clones directory could not be created"
    if problem is not None:
        raise Refusal(problem)

    for row in manifest:
        name = row["repository"]
        target = args.clones / clone_dir(name)
        if target.exists():
            raise Refusal(f"{name}: a clone directory for it already exists")
        # Blobless and unchecked-out: this needs history and trees,
        # never file contents.
        done = run_git(
            [
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "--quiet",
                row["url"],
                str(target),
            ]
        )
        if done.returncode != 0:
            raise Refusal(f"{name}: cloning it failed")
        # Not redundant with the clone, which brings only the tags
        # reachable from the history it fetched; a release branch's tag
        # can sit outside that.
        fetched = git(target, "fetch", "--tags", "--filter=blob:none", "--quiet")
        if fetched.returncode != 0:
            raise Refusal(f"{name}: fetching its tags failed")
    print(f"cloned {len(manifest)} repositories")
    return 0


def latest_release_tag(repo: Path, name: str):
    """The highest tag matching the stated release policy, or None.

    None means the repository has no tag the policy accepts, which is
    an ordinary state a report says out loud. A git that could not list
    the tags at all is not that state and must not be read as it: it is
    a refusal, because "no releases" and "we could not look" lead a
    reader to opposite conclusions.
    """
    done = git(repo, "tag", "--list")
    if done.returncode != 0:
        raise Refusal(f"{name}: git could not list its tags")
    best = None
    for line in done.stdout.splitlines():
        tag = line.strip()
        m = TAG_RE.match(tag)
        if not m:
            continue
        # The order is major, minor, patch, and then the two-or-three
        # part distinction, because `v1.2` and `v1.2.0` name the same
        # numbers and something has to break the tie the same way every
        # week. The three-part form wins: it is the more specific
        # spelling of the same release, and a project that ships both
        # is telling you which one it means to be current.
        #
        # The tag text is the last component so the ordering is total
        # even if the syntax above is ever widened.
        key = (
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3) or 0),
            1 if m.group(3) is not None else 0,
            tag,
        )
        if best is None or key > best[0]:
            best = (key, tag)
    return None if best is None else best[1]


def is_ancestor(repo: Path, name: str, earlier: str, later: str) -> bool:
    """Whether `earlier` is an ancestor of `later`, or a refusal.

    Only 0 and 1 are answers. See the caller for why the difference
    matters.
    """
    done = git(repo, "merge-base", "--is-ancestor", earlier, later)
    if done.returncode not in (0, 1):
        raise Refusal(f"{name}: git could not compare the pinned commit with a target")
    return done.returncode == 0


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
    manifest = load_manifest(args.manifest)

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
        pin = git(repo, "rev-parse", "--verify", "--quiet", row["pinned"] + "^{commit}")
        if pin.returncode:
            print(
                f"{name}: the pinned commit does not resolve in its clone",
                file=sys.stderr,
            )
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
        lines.append(f"Pinned at `{row['pinned']}`, read {row['read']}.")
        lines.append("")

        targets = [("upstream HEAD", "origin/HEAD", head_sha)]
        tag = latest_release_tag(repo, name)
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
            # merge-base --is-ancestor documents exactly two answers:
            # 0 for an ancestor and 1 for anything else it could
            # compute. Every other exit code is git failing, and
            # reading one of those as "not an ancestor" turns a broken
            # clone into a confident claim about upstream's history.
            if not is_ancestor(repo, name, row["pinned"], rev):
                relation = (
                    "is behind the pinned commit"
                    if is_ancestor(repo, name, rev, row["pinned"])
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
            names = git(repo, "diff", "--name-status", span, "--", *row["paths"])
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
        write_output(args.output, "")
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
    write_output(args.output, "\n".join(body))
    print("wrote a drift report")
    return 0


def cmd_decide(args) -> int:
    """create, update <number>, or a refusal naming the ambiguity."""
    if not read_ours(args.report, "the report file").strip():
        print("the report is empty, so there is nothing to write", file=sys.stderr)
        return 2
    text = read_ours(args.issues, "the issues file")
    problem = None
    try:
        issues = json.loads(text)
    except json.JSONDecodeError:
        problem = "the issues file is not valid JSON"
    if problem is not None:
        print(problem, file=sys.stderr)
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
        if not isinstance(number, int) or isinstance(number, bool):
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


def build_parser() -> argparse.ArgumentParser:
    parser = _FixedMessageParser(
        prog="upstream_watch.py",
        description="The upstream wire-contract drift watch.",
    )
    subs = parser.add_subparsers(
        dest="command", required=True, parser_class=_FixedMessageParser
    )

    check = subs.add_parser("check", help="manifest and notes agree")
    check.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    check.add_argument("--notes", type=Path, default=DEFAULT_NOTES)
    check.set_defaults(func=cmd_check)

    clone = subs.add_parser("clone", help="fetch the watched upstreams")
    clone.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    clone.add_argument("--clones", type=Path, required=True)
    clone.set_defaults(func=cmd_clone)

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

    return parser


def main(argv=None) -> int:
    """The one exception boundary.

    Everything below raises `Refusal` and nothing else escapes as a
    traceback: a traceback would print the local variables' repr for
    anything reading the log, and those locals are the very documents
    this module refuses to echo.
    """
    args = build_parser().parse_args(argv)
    problem = None
    try:
        return args.func(args)
    except Refusal as exc:
        problem = str(exc)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        problem = "the drift watch failed while reading or running something"
    print(problem, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
