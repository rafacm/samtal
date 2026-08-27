#!/usr/bin/env python3
"""Check relative markdown links and heading anchors in a checkout.

Usage: python3 scripts/check_doc_links.py <repo-root>

Scans README.md, AGENTS.md, vinga-server/README.md,
vinga-esp32/README.md and everything under docs/ for markdown links.
A relative link must resolve to an existing file or directory inside
the checkout; a #fragment must match a heading anchor (GitHub
slugification) in the target file. External schemes are skipped.
Exit 1 on any failure, 2 on a bad invocation.

A failure line names the file and line and the kind of failure, and
deliberately nothing else: link destinations are repository content,
and a value that should never have been in a document must not be
republished into a CI log by the tool that finds its link broken.
The file and line are enough to open the failure.

Two stated limits, both discovered in use during the #310 chain. A
link wrapped across two source lines is invisible to this checker
(it reads line by line), so a link it should verify must sit whole
on one line; and it reads Markdown only, so a docs link inside a
Python docstring is not checked (PR #326's review found exactly one
of those wrong; scanning source docstrings is the known extension
if a second one appears).
"""

import re
import sys
from pathlib import Path

LINK_RE = re.compile(
    r"(?<!\!)\[[^\]]*\]\((<[^>]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\)"
)
IMG_RE = re.compile(
    r"\!\[[^\]]*\]\((<[^>]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\)"
)
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
CODE_FENCE_RE = re.compile(r"^(```|~~~)")
SKIP_SCHEMES = ("http://", "https://", "mailto:", "ftp://")


def slugify(heading: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("*", "")
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "-", text)


def anchors_of(path: Path, cache: dict) -> set:
    """Every anchor the file's headings occupy.

    Duplicate slugs are suffixed the way github-slugger does it: the
    base slug's counter advances until it lands on a slug nothing has
    emitted yet, so `Foo`, `Foo-1`, `Foo` yields `foo`, `foo-1`,
    `foo-2`, never a second `foo-1`.
    """
    if path not in cache:
        counters: dict = {}
        anchors: set = set()
        in_fence = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if CODE_FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = HEADING_RE.match(line)
            if not m:
                continue
            slug = slugify(m.group(1))
            candidate = slug
            while candidate in anchors:
                n = counters.get(slug, 0) + 1
                counters[slug] = n
                candidate = f"{slug}-{n}"
            anchors.add(candidate)
        cache[path] = anchors
    return cache[path]


def links_of(path: Path):
    in_fence = False
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for regex in (LINK_RE, IMG_RE):
            for m in regex.finditer(line):
                raw = m.group(1)
                if raw.startswith("<") and raw.endswith(">"):
                    raw = raw[1:-1]
                yield lineno, raw


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_doc_links.py <repo-root>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    if not root.is_dir():
        print("the given repo-root is not a directory", file=sys.stderr)
        return 2
    root = root.resolve()
    files = [
        p
        for p in [
            root / "README.md",
            root / "AGENTS.md",
            root / "vinga-server" / "README.md",
            root / "vinga-esp32" / "README.md",
        ]
        if p.exists()
    ] + sorted((root / "docs").rglob("*.md"))
    cache: dict = {}
    failures = 0

    def fail(md: Path, lineno: int, kind: str) -> None:
        nonlocal failures
        print(f"{md.relative_to(root)}:{lineno}: {kind}")
        failures += 1

    for md in files:
        try:
            found = list(links_of(md))
        except (OSError, UnicodeDecodeError):
            fail(md, 0, "unreadable file")
            continue
        for lineno, raw in found:
            if raw.startswith(SKIP_SCHEMES):
                continue
            target, _, fragment = raw.partition("#")
            if target == "":
                resolved = md
            else:
                try:
                    resolved = (md.parent / target).resolve()
                except OSError:
                    fail(md, lineno, "unresolvable target")
                    continue
                if not resolved.is_relative_to(root) or ".git" in resolved.parts:
                    fail(md, lineno, "target outside the checkout")
                    continue
                if not resolved.exists():
                    fail(md, lineno, "missing target")
                    continue
            if fragment and resolved.suffix == ".md" and resolved.is_file():
                try:
                    known = anchors_of(resolved, cache)
                except (OSError, UnicodeDecodeError):
                    fail(md, lineno, "unreadable target")
                    continue
                if fragment not in known:
                    fail(md, lineno, "missing anchor")
    print(f"checked {len(files)} files, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
