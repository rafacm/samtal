# Stop stale bytecode making the tree lie about what it runs

## Problem

Issue #16: CPython validates a cached `.pyc` against two properties of
its source, the size in bytes and the mtime *truncated to whole
seconds*. Both matching means "unchanged". Two ordinary operations in
this repository produce a change that matches on both and is therefore
invisible:

- **Checking that a regression test really fails without its fix.**
  Reverting a fix often means swapping two statements, which preserves
  the byte count exactly, and a scripted revert-run-restore cycle
  finishes well inside one second.
- **Restoring a file from a backup with `mv` or `cp -p`,** which carries
  the backup's older mtime. The source then looks *older* than its own
  cache, so the cache wins.

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
- The same conftest removes its own `__pycache__` directory. The flag
  cannot cover the file that sets it: `rewrite.py` writes a conftest's
  bytecode *before* it executes the body, so by the time the flag is
  set this run has already cached it. Deleting it means the next run
  finds nothing to read and rewrites from source, which is the same
  guarantee the flag gives everything else, and it is what makes the
  issue's `find` acceptance literally true rather than nearly true.
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

The acceptance criteria, in the real test lane. `samtal_server/ota.py`
defines `UNKNOWN_VERSION = "0.0.0"` and `tests/unit/test_ota.py` asserts
that literal, so rewriting it to `"9.9.9"` is a same-size edit that must
fail four tests. The probe breaks the file, runs pytest, and restores
the original with `mv` (older mtime), all in one scripted cycle.

| | cycle | broken source | restored source |
|---|---|---|---|
| bytecode enabled (control) | 603 ms | 4 failed | **4 failed** |
| this change | 646 ms | 4 failed | 23 passed |

The control's second column is the #13 failure mode reproduced: the
restored, correct file was not believed, because the broken version's
bytecode was the same size and had a newer mtime. A correct fix looked
broken. With bytecode off, the restored tree is believed immediately.

`find samtal-server -name '__pycache__' -not -path '*/.venv/*'` is empty
after a full run, and after two consecutive full runs.

Measured cost: 51.97 s and 51.98 s for `tests/unit` over two runs
against 52.45 s before the change, which is noise. The expensive imports
live in site-packages and keep their own bytecode.

## Files modified

- `samtal-server/tests/conftest.py`
- `.github/workflows/samtal-server.yml`
- `AGENTS.md`
- `CHANGELOG.md`
