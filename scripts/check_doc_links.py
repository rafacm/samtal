#!/usr/bin/env python3
"""Check relative markdown links and heading anchors in a checkout.

Usage: python3 scripts/check_doc_links.py <repo-root>

Scans README.md, AGENTS.md, vinga-server/README.md,
vinga-esp32/README.md and everything under docs/ for markdown links.
Relative links must resolve to an existing file or directory; a
#fragment must match a heading anchor (GitHub slugification) in the
target file. External schemes are skipped. Exit 1 on any failure.

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

LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMG_RE = re.compile(r"\!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
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
    if path not in cache:
        seen: dict = {}
        anchors = set()
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
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            anchors.add(slug if n == 0 else f"{slug}-{n}")
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
                yield lineno, m.group(1)


def main() -> int:
    root = Path(sys.argv[1]).resolve()
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
    for md in files:
        for lineno, raw in links_of(md):
            if raw.startswith(SKIP_SCHEMES) or raw.startswith("<"):
                continue
            target, _, fragment = raw.partition("#")
            if target == "":
                resolved = md
            else:
                resolved = (md.parent / target).resolve()
                if not resolved.exists():
                    print(f"{md.relative_to(root)}:{lineno}: "
                          f"missing target {raw}")
                    failures += 1
                    continue
            if fragment and resolved.suffix == ".md" and resolved.is_file():
                if fragment not in anchors_of(resolved, cache):
                    print(f"{md.relative_to(root)}:{lineno}: "
                          f"missing anchor {raw}")
                    failures += 1
    print(f"checked {len(files)} files, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
