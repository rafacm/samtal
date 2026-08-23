# Move the network doctor out of config/cli.py: implementation

Companion to
[`2026-08-23-network-doctor-move.md`](2026-08-23-network-doctor-move.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the doctor moves whole

### What was done

Four commits, each green on its own: the printing module and the config
CLI's adoption of it, the derivation's move to `onboarding/origin.py`,
the doctor module with the entry point's dispatch and the config CLI's
deletions and test split, and the documentation.

**`config/printing.py`.** `parsed_url` and `printable` moved
byte-identical out of `config/cli.py` (extracted mechanically rather
than retyped), and `GLIMPSE_LENGTH` with them, its comment reworded
only where it said "what `doctor` reaches" to say "what a command
reaches". `shown_url` is `_without_userinfo` with the query filtered:
the host is rebuilt from the parsed parts as before, and the query is
rebuilt from `parse_qsl` with every secret-shaped key dropped, using
`models.is_secret_option`, which is the predicate
`models.without_url_credential` filters with. The module's interface is
those three names plus the constant, and it is exported through
`__all__` so the display door is the only door.

Call sites: `cli._permitted` takes both, `cli._reported_websocket`
(which moved on to the doctor) takes `printable(shown_url(...))`, and
the four rendering helpers take `printable`. They import the names
directly rather than the module, because `printing.printable(...)`
inside two of the existing f-strings put them over the line length.

**`onboarding/origin.py`.** Gains `onboarding_url(server, fix)`, the
`ONBOARDING_OFF` template and the auth-secret-unset refusal, all moved
whole; gains `from vinga_server.config.loader import ConfigError`, which
introduced no cycle as predicted. Its module docstring's command
spellings are updated and a paragraph names the new function. Both
names are added to the package `__init__`'s aggregation and `__all__`,
which is that file's stated rule for anything importable from
`vinga_server.onboarding`.

**`doctor.py`.** The nine census functions and the doctor-only
constants, extracted mechanically from `config/cli.py` so the sentences
are byte-identical, plus `main`, `_Parser`, the usage table, `_parser`,
its own `_server_config` and its own `build_client(url)`. The parser's
grammar is `vinga-server doctor [URL] [--config PATH]`. The usage table
carries the two shapes this grammar can actually produce
(`unrecognized arguments`, `expected one argument`) and the
conversations group's deliberately vague fallback; `invalid choice` and
`required` are unreachable without subparsers, so they are not listed.

Four prose edits inside moved bodies, all of them because the code
changed file rather than because it changed:
`ONBOARDING_OFF_FOR_DOCTOR`'s fix sentence names `vinga-server doctor
URL`; `_device_url`'s docstring names `config/cli.py` where it used to
say "the API's policy" from inside that file; the `onboarding.origin`
import is function-local with the import-graph reason beside it; and
`build_client` is a new body rather than a moved one.

**`main.py`.** `DOCTOR_COMMAND` and a `COMMANDS` tuple the refusal
sentence is built from, the lazy dispatch beside the three existing
ones, the unknown-first-word refusal (fixed sentence naming the four
words, nothing of what was typed, exit 2), and `_Parser` with an
`error()` override so the server's own argument shapes answer fixed
sentences too. `USAGE_EXIT_CODE` is 2, which is what argparse always
answered a usage error with, so nothing scripted around the entry point
learns a new number from a change that is about what gets printed.

**`config/cli.py`.** The doctor cluster, the doctor's parser block and
the moved helpers are gone; `build_client`'s `token` is required and
its docstring is the API's alone; the module docstring's "two commands
stand outside" paragraph is one command; the two section comments that
counted commands are recounted. `tests/support/config_cli.py` needed no
change: its replacement factory already declared `token: str`.

**The tests.** `tests/unit/test_config_cli_onboarding.py` keeps the
`ota-url` suites (12 tests) and narrows its docstring to one command;
everything from `# What answers on it` onward is
`tests/unit/test_doctor.py`, moved mechanically. The only changes
inside moved bodies are the ones the plan allows: entry-point call
sites (`cli.main(["doctor", X])` became `doctor.main([X])`), patch
targets, module-qualified names, the seam factories losing their token
parameter, and the one assertion pinning the
`ONBOARDING_OFF_FOR_DOCTOR` spelling. Every sentinel assertion is
byte-unchanged, and the real-describe-handler case moved with the file.

New pins, all in `test_doctor.py` unless noted:

- the doctor seam's construction policy (no Authorization header, its
  own timeouts), which is the two old client pins' doctor half; the
  config half is a new
  `test_the_client_carries_the_token_it_was_built_with` beside the
  timeouts pin already in the transport suite;
- the subprocess import-weight pin over all four forbidden modules, and
  the behavioral pin beside it asserting a probe of a supplied URL
  creates no database file anywhere under the working directory;
- the two usage-error tests, one of which plants a secret-shaped URL
  twice so argparse would have echoed it;
- three entry-point tests: the word dispatches into this module,
  a misspelled word repeats nothing (and names the four known words),
  and `config doctor` meets the config grammar's invalid-choice
  refusal;
- the far-side query-credential sentinel (`?token=` on the websocket
  URL the endpoint reports), and its operator-typed sibling in the
  transport suite (`--api-url http://host/api?token=`, which the
  plain-http refusal names).

`tests/unit/test_config_cli_rendering.py` retargets `GLIMPSE_LENGTH`
and the `_printable` mention in a docstring to `config.printing`.

### Deviations from the plan

Three, all small.

1. **`doctor.py` imports `config.models` as well as the three modules
   the plan lists.** `_server_config` is annotated `-> ServerConfig`,
   and the name has to come from somewhere. It adds no weight
   whatsoever: `config.loader`, which the plan does list, imports
   `config.models` itself, so the module is in `sys.modules` either
   way. The import-weight contract is about the four modules the
   subprocess test names, and it holds.

2. **The two client-construction pins became one test in the doctor
   suite rather than a split of the old pair.** The old pair asserted
   the header in one test and the timeouts in another, both against
   `cli.build_client`. Since the doctor's seam has no token parameter
   at all, "no Authorization header" and "these timeouts" are one
   statement about one constructor, and writing them as two tests would
   have been two tests calling the same one-line factory. The config
   seam's bearer half is its own test in the transport suite, where the
   timeouts pin already lived.

3. **The doctor suite's environment fixture does not clear
   `VINGA_API_URL`.** The `ota-url` suite's copy does, because that
   file's fixture predates the split and `cli.API_URL_ENV` is where the
   name lives. The doctor reads no such variable and has no `--api-url`
   flag, so clearing it would have been the copied fixture importing
   `config.cli` for a line that cannot matter. This is the fixture
   duplication the plan's risk section asked to be recorded: the copy
   is the `VINGA_CONFIG` and `VINGA_API_SECRET` clears, the auth secret
   and the database directory, plus `SECRET`, `KEY`, `PASTED`,
   `DESCRIBE`, `_config_file` and `_chain`. Nothing was added to
   `tests/support/`.

### Resolutions

- **Finding 5's retained reach-in.** `doctor._device_url` is still
  called directly by the exception-chain test, and the comment above it
  now says both halves of why: the chain is not observable through
  `main()`, which consumes the exception and prints one sanitized line,
  and promoting the helper would create a public name whose only caller
  is that test.
- **The `ONBOARDING_OFF` census hit.** `grep -n ONBOARDING_OFF
  config/cli.py` still returns three lines. All three are
  `ONBOARDING_OFF_FOR_URL`, which is `ota-url`'s own fix sentence and
  stays, or prose naming `origin.ONBOARDING_OFF` as the template it
  goes into. The moved template itself is gone from the file.
- **The `GLIMPSE_LENGTH` census hit.** One line, in the docstring of
  `_prompt_listing`, explaining why that renderer deliberately does not
  bound its output. The constant itself is `config.printing`'s.

### Discoveries

- **The no-echo boundary was easy to verify by accident.** A shell
  mistake during the drift checks passed `"config reference"` as a
  single argument, and the entry point answered `that is not a command;
  expected one of: config, conversations, events, doctor` instead of
  argparse's echo. The new path is on the ordinary route.
- **`shown_url` re-encodes the query it keeps.** `urlencode` over
  `parse_qsl` normalizes percent-encoding, so a displayed URL with a
  surviving query parameter may differ from the typed one in its
  escaping. This is what `models.without_url_credential` already does
  for the same reason, and it only ever affects a string that is being
  displayed rather than requested.
- **A `?token=` in an operator-typed API URL only reaches a stream
  through the plain-http refusal.** The other two refusals in
  `_permitted` fire on shapes (no host, userinfo) that a
  query-credential test cannot also produce, so the sentinel test uses
  the plain-http one, which is the refusal that names the address.

### Verification

All from `vinga-server/`, on the final tree.

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q -n auto --dist loadfile` (the lane the
  way CI runs it): **2872 passed, 20 skipped** in 42.32s.
- `uv run pytest tests/integration -q`: **61 passed** in 192.75s.
- `uv run mypy` (the events package's type check, the unit lane's other
  step): **Success: no issues found in 4 source files**.
- The four generated-document drift checks, each regenerated and
  diffed against its committed copy: `config reference` against
  `docs/reference/domain-config.md`, `conversations schema` against
  `docs/reference/conversations-schema.md`, `events reference` against
  `docs/reference/events.md`, `config openapi` against
  `docs/reference/api-openapi.json`. All four byte-identical.
- **The config-schema pin**, which CI does not cover (plan review
  finding 4). Captured on the base commit `4984fd06` before any code
  change with `uv run vinga-server config schema >
  /tmp/schema-base-244.txt` (30,756 bytes), regenerated after the move
  and compared with `diff -u` and `cmp`: **identical, byte for byte**.
- **The census.** `grep -n` in `config/cli.py` for each of the twenty
  names the plan censuses returns nothing for eighteen of them; the two
  residual hits are the `ONBOARDING_OFF` and `GLIMPSE_LENGTH` lines
  resolved above, neither of which is the moved thing.
  `grep -rn "config doctor"` over the repository with `vendor/` and
  `.git/` excluded returns sixteen lines: the new `CHANGELOG.md` entry
  announcing the rename, two older changelog entries (#225 and the
  original `samtal-server` pair), and thirteen lines in
  `docs/plans/`, which is this plan and the four older plan documents
  that describe the command as it was. No README, no example
  configuration, no source file and no test.

The wheel-migration step is CI's own and was not run locally; nothing
in this milestone touches a migration, a packaged data file or the
package metadata.

## PR review round, M1 (PR #266)

External review of the PR diff: codex backend (first codex PR round
since the quota reset), codex CLI 0.149.0, model `gpt-5.6-sol`,
read-only sandbox, 2026-08-23, runtime 6m01s, posted on the PR
(comment 5387241568). Verdict as received: mergeable after the
listed fixes. Two P1 and two P2, every finding fixed with its own
commit and a revert-or-mutate proof:

1. **P1: the doctor logged the secret OTA URL at INFO.** httpx's
   INFO record carries method, URL and status, so a supplied legacy
   OTA URL put its secret path segment in a log record even though
   every verdict hides it. Fixed in `008da7e4`: `logs.py` gains
   `quieted(names, level)`, the scoped sibling of
   `quiet_vendor_libraries` (raise-never-lower, restored in a
   finally), and `_probed` runs inside it for `httpx` and
   `httpcore`. Proof: with the boundary removed, the new caplog test
   fails with the real record. Severity note recorded honestly: the
   shipped CLI path attaches no handler, so today the record is
   created and goes nowhere; it would land the moment anything adds
   a handler, which is why the fix stands. Discovery worth keeping:
   the dev venv carries `httpx2` (Starlette's TestClient imports
   it), which neither `REQUEST_LOGGERS` nor `VENDOR_LOG_FLOORS`
   covers; correct today since nothing in `src/` imports it, a trap
   if it ever becomes a runtime dependency.

2. **P1: a failing `client.close()` escaped the sanitizing
   boundary.** The close ran in the outer finally, outside the
   handler, so an OSError from it left as a library traceback,
   possibly secret-bearing, and could replace an already-sanitized
   failure. Fixed in `c11e8376`: `_close_failed` keeps only the
   class name, the caller raises after the block (`__cause__` and
   `__context__` both None), and `problem = problem or
   _close_failed(...)` makes the earlier failure win. Three tests;
   proof by putting the close back in the finally.

3. **P2: the root parser's no-echo override had no regression
   test.** Fixed in `0a03d307`: two entry-point tests drive shapes
   that reach the root parser with a planted secret-shaped URL;
   swapping the override back to plain argparse fails them with the
   literal echo. Premise correction recorded: argparse's
   missing-value sentence carries no value, so that test pins the
   sentence being ours; the unrecognized-argument test is the one
   that bites.

4. **P2: the query-credential tests checked one stream each.**
   Fixed in `7c2b6a66`: both assert over `captured.out +
   captured.err` and hunt the parameter as well as the sentinel; the
   transport test's needle is `?token=`/`token=<SECRET>` because
   that refusal's own prose speaks of the bearer token.

A fifth commit, `9574a79f`, adds the two operator-visible fixes
(the quieted probe log, the sanitized close) to the changelog entry.
