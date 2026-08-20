"""Where the tests reach past an interface, counted rather than remembered.

The design guide's rule is that a test reaches the names a caller
reaches, so an underscore reach-in is a review flag: either the module
is missing an interface callers need, or the test pins a detail that is
free to change. #210's M6 acts on that rule across the whole suite, and
acting on it needs a census rather than an impression.

What counts as a site: a NAME token spelled `_x` (one leading
underscore, then a letter) immediately after an `OP` `.`, anywhere under
`tests/`. A tokenizer rather than an AST walk because the question is
lexical: `session._opened_at` is a reach-in whatever the expression in
front of the dot evaluates to, and a walk that resolved receivers would
have to know what every fixture returns.

What does not count: `self._x` and `cls._x`. A test file's own fakes and
its own test classes keep private state of their own, and that state is
theirs; reaching for it crosses no interface. Those are counted
separately and reported, so the exclusion is visible rather than
silent.

    uv run python -m tests.tools.reach_ins            # summary
    uv run python -m tests.tools.reach_ins --by-site  # file:line per site
    uv run python -m tests.tools.reach_ins --json     # the whole census

The before-and-after numbers this produces are recorded in the
implementation doc of
`docs/plans/2026-08-19-governance-simplification.md`.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import token
import tokenize
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

PRIVATE = re.compile(r"^_[A-Za-z]")
"""One leading underscore then a letter: `_x`, never `__x__` or `_`."""

OWN_STATE = frozenset({"self", "cls"})
"""Receivers whose private state is the reading file's own."""


@dataclass(frozen=True)
class Site:
    """One `receiver._name` in one place."""

    path: str
    line: int
    receiver: str
    name: str


def sites(source: str, path: str) -> tuple[list[Site], list[Site]]:
    """The reach-ins in `source`, and the own-state accesses beside them.

    Returns (reach_ins, own_state). Both lists are in source order.
    """
    reached: list[Site] = []
    own: list[Site] = []
    previous: list[tokenize.TokenInfo] = []
    stream = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok in stream:
        if tok.type in (token.NEWLINE, token.NL, token.INDENT, token.DEDENT):
            continue
        if tok.type == token.COMMENT:
            continue
        if (
            tok.type == token.NAME
            and PRIVATE.match(tok.string)
            and len(previous) >= 1
            and previous[-1].type == token.OP
            and previous[-1].string == "."
        ):
            before = previous[-2] if len(previous) >= 2 else None
            receiver = before.string if before is not None else "?"
            site = Site(path, tok.start[0], receiver, tok.string)
            if before is not None and before.type == token.NAME and receiver in OWN_STATE:
                own.append(site)
            else:
                reached.append(site)
        previous.append(tok)
    return reached, own


def walk(root: Path) -> tuple[list[Site], list[Site]]:
    """Every site under `root`, in path order."""
    reached: list[Site] = []
    own: list[Site] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found, mine = sites(text, str(path.relative_to(root.parent)))
        reached.extend(found)
        own.extend(mine)
    return reached, own


def _census(reached: list[Site], own: list[Site]) -> dict[str, object]:
    per_file = Counter(site.path for site in reached)
    per_name = Counter(site.name for site in reached)
    per_pair = Counter((site.path, site.name) for site in reached)
    return {
        "sites": len(reached),
        "names": len(per_name),
        "files": len(per_file),
        "own_state_excluded": len(own),
        "per_file": dict(per_file.most_common()),
        "per_name": dict(per_name.most_common()),
        "per_file_name": [
            {"path": path, "name": name, "count": count}
            for (path, name), count in sorted(
                per_pair.items(), key=lambda item: (item[0][0], -item[1], item[0][1])
            )
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="the tests directory to walk (default: this file's own)",
    )
    parser.add_argument("--json", action="store_true", help="the whole census as JSON")
    parser.add_argument(
        "--by-site", action="store_true", help="one file:line line per site"
    )
    args = parser.parse_args(argv)

    reached, own = walk(Path(args.root))
    census = _census(reached, own)

    if args.json:
        json.dump(
            {**census, "site_list": [asdict(site) for site in reached]},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    if args.by_site:
        for site in reached:
            print(f"{site.path}:{site.line}: {site.receiver}.{site.name}")
        print()

    print(
        f"{census['sites']} reach-in sites over {census['names']} names "
        f"across {census['files']} files "
        f"({census['own_state_excluded']} self/cls accesses excluded)"
    )
    print()
    print("By file:")
    for path, count in census["per_file"].items():  # type: ignore[union-attr]
        print(f"  {count:4d}  {path}")
    print()
    print("By name:")
    for name, count in census["per_name"].items():  # type: ignore[union-attr]
        print(f"  {count:4d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
