# Stop stale bytecode making the tree lie about what it runs

## Problem

Issue #16: a cached `.pyc` records two properties of its source, the
size in bytes and the mtime in whole seconds, and CPython accepts the
cache when both are *equal* to the source's current values. Note
equal, not newer: an edit is invisible when it keeps the byte count and
leaves the mtime on the second the cache was compiled on. Two ordinary
operations in this repository do exactly that:

- **Checking that a regression test really fails without its fix.**
  Reverting a fix often means swapping two statements, which preserves
  the byte count exactly, and a scripted revert-run-restore cycle
  finishes well inside one second, so the compile and the restore share
  a second.
- **Restoring a file from a backup,** which carries the backup's mtime
  rather than the current time. When the backup was taken moments
  before, that mtime is the one the cache was compiled against.

The second is how it bit while addressing the review on #13: a `_speak`
fix was restored, the interpreter kept running the pre-fix bytecode, and
a correct fix looked broken. It cost about half an hour and, worse,
produced two contradictory results from the same tree: a test that
passed alone and failed in the full suite, and a fix that
`inspect.getsource` showed in place while the interpreter ran the
version before it.

## Changes

- `tests/conftest.py` sets `sys.dont_write_bytecode = True`. One line,
  no dependency, automatic for every pytest run local and in CI. It
  covers both halves of the hazard: the package's own modules, and
  pytest's assertion-rewritten test bytecode, which uses the same
  mtime-and-size check and matters just as much because test files are
  edited constantly (`_pytest/assertion/rewrite.py` gates its cache
  write on that flag).
- The same conftest clears the existing `__pycache__` directories under
  `samtal_server/` and `tests/`, once, before the first import of
  anything under test. The flag stops writes, not reads: a cache that
  already exists is still consulted, and with writes off it would never
  be refreshed, so a stale one would stay stale forever. Caches do get
  written outside pytest, by `uv run samtal-server` or a bare `python -c
  "import samtal_server..."`, and every tree predating this change has a
  full set. This was a review finding, not part of the issue's proposal,
  and it is load-bearing: the verification below shows the flag alone
  failing the scenario.
  It also covers the one file the flag cannot. `rewrite.py` writes a
  conftest's bytecode *before* it executes the body that sets the flag,
  so by then this run has already cached this file; clearing leaves the
  next run nothing stale to read. `.venv` is deliberately excluded:
  site-packages bytecode is legitimate, expensive to rebuild, and its
  sources do not get edited.
  One residual is accepted and documented in place: a run whose own
  conftest cache was already stale on entry reads it before reaching the
  clearing line. It cannot be closed from inside the file that would
  have to close it, it is one run wide, and it is self-healing, because
  from here on no run ends with a conftest cache on disk to go stale.
  Closing it properly would mean clearing before pytest starts, which
  means a wrapper script everyone has to remember to use, which is the
  thing a conftest exists to avoid.
- `PYTHONDONTWRITEBYTECODE: "1"` as a workflow-level `env` on
  `.github/workflows/samtal-server.yml`, for the steps that are not
  pytest. A runner starts from a fresh checkout every time, so the
  cache buys nothing there in any case.
- `AGENTS.md` gains a "Restoring a file mid-experiment" subsection with
  the two traps, since neither is guessable: `touch` after restoring,
  and never restore with `git checkout <file>`, which also silently
  discards unrelated uncommitted edits to that file (it did, mid
  experiment, on #13).

The container image is deliberately left alone. It sets
`UV_COMPILE_BYTECODE=1` and its sources never change after the build, so
timestamp validation is correct there and the cached bytecode is worth
having at startup.

Hash-based `.pyc` files (PEP 552) were considered in the issue and not
adopted. `compileall --invalidation-mode checked-hash` works, and the
runtime preserves the mode when it regenerates a file, but there is no
interpreter switch that makes the import system *write* hash-based
bytecode in the first place. A fresh clone, a cleared cache, or any new
module gets timestamp validation again, so it would have to be
re-applied continuously. That is a good fit for a build step and an
awkward one for a working tree.

## Key parameters

None. No configuration surface changes: no config schema, no CLI flag,
no environment variable an operator sets. The only knob is the CI
variable above, and it is not deployment-facing.

## Verification

The mechanism, exactly as the issue states it:

```
$ printf 'VALUE = "AAA"\n' > probe.py
$ python3 -c "import probe"
$ printf 'VALUE = "BBB"\n' > probe.py
$ python3 -c "import probe; print(probe.VALUE)"
AAA
```

Both hazard shapes were then reproduced in the real test lane and run
against a control with neither the flag nor the cache clearing.
`samtal_server/ota.py` defines `UNKNOWN_VERSION = "0.0.0"` and
`tests/unit/test_ota.py` asserts that literal, so rewriting it to
`"9.9.9"` is a same-size edit that *must* fail four tests. Any run that
reports 23 passed is a run that executed code the source no longer
contains.

**Shape 1, restore from a backup.** Break the file, run pytest, restore
the original by `mv` from a backup taken moments earlier, run pytest
again. Whole cycle about 600 ms.

| | broken source | restored source |
|---|---|---|
| control | 4 failed | **4 failed** (should be 23 passed) |
| this change | 4 failed | 23 passed |

The control's second column is the #13 failure mode exactly: the
restored, correct file kept failing, because the broken version's
bytecode was the same size and shared its second. A correct fix looked
broken.

**Shape 2, a cache written outside pytest.** Seed the cache with `uv run
python -c "import samtal_server.ota"`, break the file same-size, and put
the mtime back exactly, which is what "lands inside the same second"
reduces to once timing is controlled for.

| | pytest against the broken source |
|---|---|
| control | 23 passed (stale bytecode served the old code) |
| flag only, clearing limited to `tests/` | 23 passed (still stale) |
| this change | 4 failed |

The middle row is the review finding: the flag by itself does not close
this, because it never stops a *read*.

`find samtal-server -name '__pycache__' -not -path '*/.venv/*'` is empty
after a full run, and after two consecutive full runs.

Measured cost: 51.97 s and 51.98 s for `tests/unit` over two runs
against 52.45 s before the change, which is noise. The expensive imports
live in site-packages and keep their own bytecode.

Full suite on the final tree: 588 passed and 2 skipped (unit), 27 passed
(integration), `ruff check` clean.

## Files modified

- `samtal-server/tests/conftest.py`
- `.github/workflows/samtal-server.yml`
- `AGENTS.md`
- `CHANGELOG.md`
