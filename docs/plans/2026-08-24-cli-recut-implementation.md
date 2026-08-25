# Turning the CLI around: implementation

The companion to [`2026-08-24-cli-recut.md`](2026-08-24-cli-recut.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: the grammar turns around

PR #295.

### What landed

The whole noun-verb tree, in the order the plan's milestone entry
sketched: the census manifest first, then the differential pins, then
the turn, then the artifacts, then the prose.

- The shared longest-prefix matcher (`tests/support/config_cli.py`),
  because the live lane's own helper could see at most two words and
  the tree is three deep in places. The spelling census reads it too,
  and M3's wheel lane will be the third reader.
- The spelling census as one checked-in tool
  (`tests/unit/test_command_spellings.py`) with its committed manifest
  (`tests/unit/command-spellings.txt`), run before the rename to emit
  the classification and standing afterwards as the guard.
- The differential (`tests/unit/test_config_cli_respelling.py` and
  `tests/unit/data/cli-respelling.txt`), captured from the old
  spellings on the commit before the turn.
- The registration table turned around: `GROUPS` keyed by noun path
  with the five entity nouns derived, `Command.kind` as an explicit
  fact, `Command.destroys`, and registration building the full prefix
  tree from `row.words[:-1]`, with every row held to being reachable by
  its own words through that tree.
- The `vinga` console script, `.env` loading in `cli.main`, the
  canonical `PROGRAM` and the closed invocation map.
- `diff`, seated flat.
- The owed items that ride: `-h`, `--version`, the confirmation with
  `--force` and `--no-input`, `-f -` at a terminal, and description
  normalization including `apply`.
- The four generated artifacts regenerated, `events.md` among them.
- `cli.md`'s two-spellings head, `cli-guide.md`'s open case and paid
  owed rows, and the changelog entries.

### The census, in numbers

1000 matches over 124 files, after the rename, the rebase and the two
review rounds: 247 `respell`, 576 `historical`, 177 `generated`. The
second round moved six of them from `respell` to `historical` by
respelling the descriptions that fed the generated documents. The round's own numbers and what moved
them are in its section above; before it, the same sweep reported 567
matches over 94 files. Before the rename the same tool found 525
matches over 92 files (200 `respell`, 148 `historical`, 177
`generated`). The manifest grew rather than shrank, which is the
expected shape: the new grammar's noun words are recognized invocation
openings that the old grammar had no equivalent of, so a line that used
to carry one match now carries one of a longer form, and two files
joined the historical class (the differential and its transcript).

The plan measured 87 files and 497 lines for the single prefix
`vinga-server config` on `main`. This branch forked before the plan
document itself landed, and that document is a large historical site, so
the two counts are not comparable and neither is wrong.

### The differential's evidence

The transcript was captured green on `29f916f3`, driving 37 commands
against one store in one order with the old spellings, and it has not
been regenerated since. After the turn the same sequence in the new
spellings differs only through the substitutions `RESPELLINGS` names,
and every one of them is a command word inside a line these commands
print:

- the five entity export headers, which name the command that writes
  one;
- the step an export's header tells an operator to run after applying
  (`the set-secret commands` becomes `the secret set commands`);
- the program word, `vinga-server config ` to `vinga `, which the
  console script shortened.

Nothing else on either stream and nothing in the store read back at the
end moved.

### Rebased onto the #293 fix round

The branch was written against `fix/cli-refusal-leaks` at its first
shape and rebased onto `main` after #293 merged, which is when the
fix round's five commits arrived: the request-quieting boundary
(`REQUEST_LOGGERS`, `logs.quieted`), client construction moved inside
`_sent`'s value-free boundary with its `httpx.InvalidURL` arm and its
close-failure sentence, `Address` in three parts with
`Address.endpoint(path)` composing the path before the query,
`printable(shown_url(...))`, all six `_FILE_PROBLEMS` rows pinned as a
table, and the both-logger quieting pins.

The three #293 commits this branch was stacked on were dropped as
already upstream, and the verbatim plan copy was dropped as empty, which
is what it was carried for. One conflict, in
`tests/unit/test_config_cli.py`: the fix round replaced three
hand-written file-refusal tests with the parametrized table that
supersedes them, and this branch had respelled the three. Resolved by
keeping the fix round's structure whole and respelling the command words
inside it, which left the file's test inventory identical to `main`'s.
Nothing of the fix round was weakened: the boundary, the three-part
address, the quieting and every pin are intact, and the only thing that
moved in them is the command words the rename legitimately moved.

The census and the differential were both re-run against the new base.
The manifest's classification did not change at all (the same 186, 193
and 188 over the same 94 files); only line numbers moved, in the files
the fix round edited. The differential still passes with the same six
substitutions and no others, and its transcript is byte-unchanged: the
fix round's new sentences are the transport ones, and the differential
drives no transport failure, so nothing in it moved for a reason other
than the rename.

### The sol round on PR #295

Seven findings, four P1 and three P2, all adopted as prescribed, one
commit each. Four were bugs this milestone introduced or left open, and
three were tests that were not testing what they said.

- **The `.env` was read in front of the boundary.** A file that would
  not open left as a traceback and one that would not decode left as
  one holding somebody's credentials. It is read through
  `loader.load_environment_file` now, which both entry points call:
  `vinga-server` had the same hole in front of its own dispatch, and
  the fix in `config/cli.py` alone would have closed it for one
  spelling of two.
- **A malformed token in a comparison escaped as a `TypeError`.** The
  lookup used the answer as a dictionary key and nothing bounds what a
  body puts where a token belongs; a list or an object there is
  unhashable. Only a string is looked up now, and every other shape
  meets strict validation.
- **The confirmation's read had nothing around it.** A terminal that
  had gone or bytes it would not decode left through `main` as a
  traceback holding what was typed at a delete.
- **Three kinds of prescription outlived the grammar**: `cli.md`'s
  hand-written recovery step, a runtime refusal in `store.py`, and the
  server README's operator vocabulary. The refusal builds its command
  from the kind's descriptor now, so it cannot go stale again; no test
  pinned its old wording, so nothing had to be moved deliberately.
- **The census guard had four gaps**, two in each direction, and
  closing them is what surfaced the sweep above: a quote was not a
  terminator, a backtick forbade the shorthand it claimed to cover,
  `cli.md` was classified generated whole though half of it is prose,
  and a group reference was accepted by leading prefix so `vinga
  provider frobnicate` passed. A fifth family joined the four: a
  compound this grammar coined and then took away, quoted alone.
- **Both model-derived `set` help tests were vacuous**, selecting rows
  whose first word is `set`, of which the turn left none.
- **Three acceptance cases the plan named were missing**: the hostile
  `argv[0]` across its six surfaces, the `.env` sentinels, and a stage
  sentinel in every provider-shaped confirmation case.

The census numbers moved a long way with the guard, which is the
finding rather than a side effect: it now reports 1003 matches over 124
files (253 `respell`, 573 `historical`, 177 `generated`), against 567
over 94 before. The `generated` count fell because `cli.md`'s prose half
stopped counting as rendering.

### The terra re-review on PR #295

Four more, two P1 and two P2, all adopted as prescribed. Two were the
same hole in a second place, and two were the canonical-spelling rule
applied in the wrong direction.

- **`--no-input` at a terminal still blocked.** It fell through to a
  plain read of stdin, and a plain read of a real terminal waits for an
  end-of-file only a person can send, so the flag that exists to remove
  the person made the command need one. It answers the empty-secret
  sentence immediately now, which is the value such a read would
  eventually have yielded. The case drives a stdin that fails if it is
  read and a prompt that fails if it is printed, because a preloaded
  buffer cannot tell "read it" from "did not read it".
- **Neither secret read had a boundary**, though the confirmation's had
  just been given one. `EOFError` is what a prompt raises when the
  stream ends underneath and is in none of the families the other arms
  catch. The three interactive reads share one boundary now rather than
  three copies of it, and an unreadable secret gets its own sentence,
  because a stream that answered with nothing and a stream that did not
  answer are different facts.
- **Five field descriptions and two paragraphs of generated prose named
  commands in the long spelling**, and rendered into `domain-config.md`
  and `api-openapi.json`. The drift checks structurally cannot catch
  this: they regenerate and diff, so a wrong program word moves the
  committed copy and the fresh render together. The spelling guard
  catches it instead, held to going red on exactly the text that was
  committed. `PROGRAM` moved one module down to `models.py`, the only
  module the descriptions, the descriptors, the renderer and the CLI all
  reach, and `SERVER_PROGRAM` joined it.
- **Boot refusals inherited the canonical spelling** through the
  descriptors, so a domain section left in the YAML file told an
  operator to run a script the image does not install. The two rules are
  one rule read from both ends: a document may not vary with the
  invocation because its reader has none, and a refusal names the
  invocation because that is what its reader has. `loader.served()` is
  the one place the program word is swapped, and one sentence is pinned
  to the long form and to not carrying the short one.

Two artifacts moved deliberately with this round, `domain-config.md`
and `api-openapi.json`, each regenerated under its own lane.

### The third pass on PR #295

One P2, and it was the `.env` boundary meeting the version contract.
`--version` has to succeed whatever else is wrong, since it is the
question an operator asks while already comparing two halves of a
deployment that disagree; reading the environment first made the one
command that must always answer exit 1 with a sentence about a file it
was never asked about.

The root position is recognized before the read now, without a parser,
which is possible because the root's options are a closed set: the scan
consumes the flags and the option values the built tree declares and
stops at the first word it does not recognize. So `--config path
--version` answers and `--config --version` does not, because there the
word is the option's value; that distinction is why this reads the
declared parameters rather than searching the list for a string.
Everything the scan does not claim goes to the parser with the answer
it always had, and every other command still meets the `.env` sentence,
which the same suite holds.

### Deviations from the plan

Six, each with what was done and why.

1. **`PROGRAM` is defined in `entities.py`, not in `config/cli.py`.**
   The plan's module layout puts the canonical constant in `cli.py`,
   and `cli.PROGRAM` is still the public name every reader uses. The
   definition had to move one module down because three surfaces need
   the same string and only one module is below all three:
   `entities.py`'s `command` fields render into `domain-config.md`,
   `docgen`'s prose renders into the same page, and `cli.py` renders the
   export header and the recipes. `docgen` importing `cli` would be a
   cycle today and a violation of M2's "no module imports `config.cli`
   except `main.py`" pin tomorrow.

2. **`docgen.recipes` lost its `program` argument**, which the plan's
   milestone footprint asked for ("one canonical prefix rather than a
   passed-in one") and which decision 6's wording did not require. It
   became possible only because of deviation 1.

3. **`--force` and `--no-input` are offered wherever `--config` and
   `--api-url` are**, rather than only on the commands that prompt. The
   plan says they are accepted "at the root and at the leaf, like the
   two existing globals", and the alternative reading (offer each flag
   only where it means something) needs a second declarer per argument
   shape, because `_named` is shared by `mcp-server delete` and
   `mcp-server show`. Duplicating four declarers to spare four help
   pages two inert lines was the worse trade. `--version` is the one
   root option no command declares, and a test says so.

4. **`-f -` at a terminal answers one sentence rather than printing the
   help page.** clig 15 and the guide say "print the help and quit".
   Every other mistake in this grammar is one sentence on stderr with
   the tail `run with --help for the grammar`, and a second shape for
   one case is worth less than the guideline's wording. The guide's row
   is `Adapted` now, with the reason on it.

5. **Server-side sentences keep the long spelling.** `store.py`,
   `api.py` and `secrets.py` quote commands in refusals an operator
   reads from a server. Decision 6 enumerates what the canonical
   constant renders (the CLI reference, the export header and its
   secret lines, the reference intro, the descriptors' `command`
   strings) and those are the sites that moved. A sentence composed by
   the server names `vinga-server config ...`, which is the spelling
   inside the image, which is where a server runs. `entities.py`'s and
   `docgen`'s prose did move, because both render into a generated
   document and decision 6's rule is that a generated document carries
   one spelling.

6. **The README's command words moved in M1; its installation prose did
   not.** The plan gives both READMEs to M2, which owns the installation
   head and the `[serve]` sweep. A README quoting `vinga config
   add-device` would name a command this image does not have the moment
   M1 merges, so the words moved; the `vinga()` shell function and every
   install line are untouched and are M2's.

### Resolutions of what the plan left open

- **Decision 11's "if it turns out larger than it looks it moves to
  M1"** did not trigger. The four acts that leave `answers=None` still
  do; `diff` did not need that work, because its shape is a model and it
  carries `answers=ConfigDiff` like every other typed act.
- **Reading a `ConfigDiff` needed two arms on `_declared`**, and the
  plan did not anticipate either. A JSON array is a list and strict
  validation will not make a `tuple[str, ...]` of one; a closed token
  arrives as its string and strict validation will not make an enum
  member of one. Both are shape-guided conversions beside the ones
  already there, and the token is looked up rather than constructed so
  that an unknown one stays a string and meets the fixed refusal rather
  than raising a `ValueError` out of a boundary that catches validation
  errors.
- **`HELP_OPTION_NAMES` had to be shared** between the live tree and the
  reference renderer. The renderer builds its own root context by hand,
  and a context built by hand does not carry the app's
  `context_settings`, so the committed pages would have listed `--help`
  alone while the live tree answered `-h` as well. One constant, two
  readers.
- **The refusal inventory's "family" is `row.words[:-1]`** for a row
  deeper than one word and `row.words` for a flat one, which is
  decision 5a's rule made concrete. Twenty-two families now, one
  refusal case each.

### Discoveries

- **Binding a board by its MAC retires the code it was showing**, which
  the differential found the hard way: a claim ordered after a bind on
  the same board is refused. The transcript claims first.
- **`tests/support/notices.py` carried the reload command as a
  literal**, so it moved with the notice. It reads `entities.PROGRAM`
  now, which is the same fact the notice is built from.
- **Two files joined the historical class deliberately**: the
  differential's transcript and the module beside it, which names in the
  old spelling exactly what the rename licensed to move. Respelling
  either would destroy the only thing they are for.

### Verification

From `vinga-server/`, everything green:

- `uv run ruff check .`
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 3649 passed,
  19 skipped
- `uv run pytest tests/integration -q`: 131 passed
- `uv run mypy`: no issues in 4 source files
- The six drift checks exactly as CI runs them (`domain-config.md`,
  `conversations-schema.md`, `events.md`, `api-openapi.json`, `cli.md`,
  and the recipes inside it), each regenerated under its own lane and
  diffed.
- The wheel builds and installs into a clean venv with both console
  scripts present, and `vinga --version` answers from it.

Not verified here, and not claimed: the image smoke lane, which needs a
`workflow_dispatch` run, and anything on hardware. Neither is M1's.

## M2: the tiers and the thin client

PR #296.

### What landed

- `config/transport.py`, the recursive transportability policy, read by
  the store and by the CLI.
- Four light names out of heavy modules: `addressed` and
  `provider_identity` to `entities.py`, `MASK` to `models.py`,
  `reference_value` inlined into its only caller. `secrets.py` keeps the
  two re-exports its many server-side readers ask it for.
- `vinga_server/serving.py`, the whole serve lifecycle, so `main.py`
  holds dispatch and its sentences and weighs pydantic and dotenv.
- The three gated sites, `openapi`, `ota-url` and the
  `vinga-server conversations` group, and the one sentence they share
  (`loader.NEEDS_THE_SERVER_HALF`); the serve refusal in `main.py` and
  the sentence it answers with.
- The tiers in `pyproject.toml`: the client half as the default
  install, `serve` as an extra, and the dev group's `vinga-server[serve]`
  entry that keeps the contributor door one command.
- The `serve` extra named at the image build and at the two CI wheel
  steps, with each `uv sync` site's tier stated.
- The three #287 structure tests
  (`tests/unit/test_cli_import_weight.py`), the missing-half refusals
  with their sentinels (`tests/unit/test_missing_server_half.py`, whose
  simulation is a meta-path finder because a module resolved by name
  never reaches `builtins.__import__`), and the tier closure with the
  contributor smoke (`tests/integration/test_tier_closure.py`).
- Decision 13's M2 prose: `cli.md`'s installation head, the server
  README's two classified sites, and the changelog.

### The dependency inventory, every entry with its tier

The plan's list was the expectation and this is the finding: every one
of the sixteen runtime dependencies is where the plan said it would be,
and nothing was in a third category. The inventory is worth writing out
anyway, because the reason is what a future dependency is classified
against.

| Distribution | Tier | Why |
| --- | --- | --- |
| `httpx` | client | the transport the grammar speaks the configuration API over |
| `pydantic` | client | the models the grammar is derived from and validates against |
| `pydantic-settings` | client | `models.py` imports it eagerly for the file half, and the CLI imports the models |
| `python-dotenv` | client | both entry points read a `.env` file before anything looks at the environment |
| `pyyaml` | client | fragments in, documents out |
| `typer` | client | the argument layer of the grammar itself |
| `alembic` | serve | the migrations, run on every database open |
| `anthropic` | serve | an LLM SDK |
| `av` | serve | the Opus codec on the device socket |
| `cryptography` | serve | Fernet, for the stored secrets |
| `fastapi` | serve | the application, the API and the onboarding routers |
| `mcp` | serve | the tool clients |
| `openai` | serve | an LLM, ASR and TTS SDK |
| `pysilero-vad` | serve | the VAD, on every audio frame whichever ASR is configured |
| `sqlalchemy` | serve | the repository behind the API |
| `uvicorn[standard]` | serve | what serves it |
| `faster-whisper`, `piper` | their own extras, unchanged | weight and licensing, which is what an optional engine is |

The dev group gains one entry, `vinga-server[serve]`, and keeps the
eight it had.

### The `[serve]` sweep, site by site

Every documented install and sync site, classified before it was
touched, per decision 8.

| Site | Audience | What happened |
| --- | --- | --- |
| `Dockerfile`, both `uv sync` steps | image build | names `--extra serve`, for both variants |
| `.github/workflows/vinga-server.yml`, three `uv sync --frozen` | contributor | unchanged, with the tier stated in a comment: the dev group carries the extra, and a plain sync is exactly what a contributor types |
| the workflow's wheel install | image-adjacent | installs `[serve]` explicitly, because the steps migrate a database and render the API document |
| `AGENTS.md` Commands | contributor | **not edited**, per settled decision 3. It is a proof instead: the sync-then-run smoke runs that exact string, and a test holds the string to being the one written down |
| `README.md` (root) | operator throughout | **not edited.** It starts a container and configures it through the shim; it never installs a Python package |
| `vinga-server/README.md` provider table | contributor | kept, with a sentence saying whose column it is and what "core" now means |
| `vinga-server/README.md` Development | contributor | kept, with a paragraph saying why a plain `uv sync` is still the whole of it |
| `vinga-server/README.md` slim-refusal quote | quoted output | unchanged; it is what the server prints, and the paragraph under it already says a container's answer is a different image |
| `vinga-server/README.md` `ota-url` `uvx` door | operator | **removed.** See the deviation below |
| `docs/reference/cli.md` installation head | all three | rewritten around the three doors |
| `config.deploy.example.{sh,yaml}`, `config.example.yaml` | operator | **no install or sync line in any of them**, checked rather than assumed |

### Rebased onto the merged M1

This milestone was written against M1's branch tip and rebased onto
`main` at `2238f19b`, after M1 (PR #295) merged with three review
rounds behind it. Fourteen commits dropped as already upstream; the nine
of this milestone replayed, four of them with conflicts.

- **`config/cli.py`'s import block.** M1's fix round added
  `load_environment_file` to the loader import while this branch was
  dropping `views` from the line above it. Both kept.
- **`main.py`'s import block.** This branch rewrites the file and M1
  changed its imports and the head of `main()`. Resolved by keeping
  this branch's dispatch-only module and M1's `load_environment_file`
  boundary inside `main()` whole, which is where it already merged.
- **`docs/reference/cli.md`, three hunks.** M1's finding-4 fix
  (`f710a3a0`) respelled the same three stale spellings this branch's
  prose commit had fixed, and spelled two of them differently: no
  backticks around `secret set` in prose, and `provider secret set --
  llm claude api_key` with the separator. **Main's respelling was taken
  at every one of the three**, and this branch's own additions (the
  three-doors installation head and the paragraph naming the gated
  commands) kept. M2's deviation 6 below is therefore now about a fix
  that landed upstream first.
- **`tests/unit/command-spellings.txt`, at four commits.** A generated
  artifact, so it was regenerated with its own tool at each step rather
  than merged.

Nothing of M1 was weakened. The `.env` boundary, `load_environment_file`,
`_read_from`/`_answered`, the pre-parse `--version`, `PROGRAM` and
`SERVER_PROGRAM` in `models.py`, `loader.served()` and the strengthened
census all survive; the last of them is what re-classified
`docs/reference/cli.md` by marker, which is how its hand-written head
became fifteen checked `respell` rows instead of one unchecked
`generated` file.

### Deviations from the plan

Seven.

1. **The sweep found a site the plan did not name, and it was broken
   rather than stale.** `vinga-server/README.md` documented
   `uvx --from git+... vinga-server config --config ./config.yaml
   ota-url` as the door for "a machine with neither a checkout nor an
   installed server". Gating `ota-url` closes exactly that door. The
   paragraph now names the two doors that remain (the container and a
   checkout) and says why the third went, which is decision 9's own
   reasoning: it is a server-host command by nature, since the file half
   it reads is the one a workstation does not have. This is the plan's
   finding-5 classification working: the site was operator-facing, so it
   was replaced rather than re-tiered into
   `uvx --from "git+...[serve]"`.

2. **`serving.py` owns the boot, not only the serve.** The plan gives it
   "the serve lifecycle, `DrainingServer`, the uvicorn configuration,
   startup, shutdown, the banner and every serve-only import" and leaves
   the boot's home unstated. It went in too, as `serving.run(config_path)
   -> int`, because `load_boot_config` opens a database and `create_app`
   builds a FastAPI application: a `main.py` that kept them would import
   SQLAlchemy and FastAPI before dispatch, which is the whole thing the
   split exists to prevent. `main.py` keeps the argument parsing, since
   `--config` is its own option and the refusals for a mistake in it are
   its own sentences.

3. **The transportability walk became public.** The plan says
   `transport.py` owns "`check_transportable`, `APPLY_LOCATION` and
   their helpers". The walk is not only a helper of
   `check_transportable`: the store asks it directly with
   `numbers_only=True`, of a stored row rather than of a fragment. A
   private name reached from another module is a fact with no home, so
   it is `untransportable` rather than `_untransportable`.

4. **The onboarding import-weight case inverted rather than moved.**
   `test_the_configuration_cli_loads_no_conversation_either` asserted
   the CLI imports `onboarding.origin` at module scope, which is what
   #143 bought and what this milestone gives back. It now asserts the
   CLI loads none of that package, which is the stronger claim the lazy
   import made true, with the reason on it. The whole inventory is in
   the new import-weight test; that case stays where the cost of
   importing the package is measured.

5. **The help strings of the gated commands are unchanged**, so
   `cli.md`'s generated region is byte-identical after M2. Saying "needs
   the server half" on a help page would move an artifact the plan's
   move list gives to M1 alone, and the fact belongs to the installation
   rather than to the command: inside the image and from a checkout,
   which is where those pages are read, both commands work exactly as
   they did.

6. **Three stale spellings in `cli.md`'s hand-written head were fixed
   on both branches.** They are M1's territory, and M1's own third
   review round fixed them in `f710a3a0` while this branch was fixing
   them too. Main's spelling was taken at every one of the three on the
   rebase; the fix is recorded here because it was found from this side,
   and because the guard's blindness to it is a discovery below.

7. **The plan's gated pair is a gated trio, and the third site is not
   in the grammar at all.** `vinga-server conversations schema` renders
   the conversation store's tables off the SQLAlchemy metadata, so on a
   client-only install it ended in a `ModuleNotFoundError` traceback.
   The plan enumerates two gated commands, and its inventory is the
   `vinga` grammar's own tree, which this group is a sibling of rather
   than a member of; the standard it is held to is the entry point's,
   and `main.py` has no other answer that is a traceback. So it answers
   the same sentence, which moved its definition down to
   `config/loader.py`: `main.py` and `config/cli.py` are its two
   readers and only the loader is below both. `cli` re-exports it.
   `config`, `events` and `doctor` are deliberately NOT gated: they are
   the client half, so an installation that reached `main.py` has them,
   and gating them would turn a real bug into a sentence saying
   something untrue about the installation. A case asserts that
   division from the production side.

### Discoveries

- **The census classified `docs/reference/cli.md` `generated`, and half
  of it is not.** The page is written by hand above the marker and
  generated below it, so the standing guard covered the half that
  cannot drift and skipped the half that can. That is how a `set-secret`
  invocation survived the rename inside the very document the rename
  regenerates. M1's third round fixed it by classifying by marker
  rather than by path, and the head is fifteen checked `respell` rows
  on this branch.

- **`registered` matches the longest registered prefix and stops**,
  which the marker fix does not reach. A quoted
  `export agent assistant` resolves to the flat `export` row, and the
  three words after it are never asked about, though the row itself
  takes no positional and the tree refuses that line. That is how
  `tests/unit/test_docker_entrypoint.py:128` kept driving
  `("config", "show", "provider", "llm", "claude")` after the rename;
  it is quoted in this milestone's grammar order now, and the matcher
  is left alone, since the live lane and the wheel lane read it too and
  changing it under a dependency change is the wrong place for it.

- **A dependency group can name its own project's extra.** `dev =
  ["vinga-server[serve]", ...]` resolves, and it is what keeps
  AGENTS.md's row a proof rather than an edit. `uv.lock` moves by
  twenty-five lines and no version resolves differently: the tiering is
  the whole of the diff.

- **The tier closure costs about 45 seconds** of the integration lane,
  which is 3m23s with it on this machine against the 3m11s the lane
  cost before this milestone. Three environments built once per module,
  and the largest single items are the serve install's boot and the
  thirty-odd `--help` subprocesses that run the ungated inventory.

- **`uv pip install` and `uv sync` do not install the same thing.**
  The first re-resolves from the index; the second installs the lock.
  On this tree `uv pip install ".[serve]"` produced `httpx2`,
  `httpcore2` and `truststore`, none of which the lock reaches, which
  is why the closure comparison had to move onto `uv sync --frozen`
  before it could be exact. The laptop door really is a fresh
  resolution (`uvx --from git+...` carries no lockfile), so what this
  lane proves is the declared graph rather than whatever PyPI resolves
  on the day; the plan already records that difference.

### Resolutions of what the plan left open

- **Which top-level module each serve distribution installs** is written
  out rather than derived, because a distribution's import name is not
  in its requirement string and guessing it by replacing hyphens is how
  a typo becomes a check that always passes. The map is held to covering
  the declared tier exactly, so a dependency added to `serve` without a
  name here fails the lane.
- **The cache globs did not change.** `uv.lock` still moves whenever a
  tier does, so `cache-dependency-glob: "vinga-server/uv.lock"` covers
  the tiering by construction; a second glob would be a second thing to
  keep true.

### The PR review round

External review of PR #296's diff (`main...b71790b2`), 2026-08-25.
Backend: codex CLI 0.149.1, model `gpt-5.6-sol`, read-only sandbox.
Verdict as received: not mergeable, on two P1 findings and one P2. All
three are adopted, one commit each.

1. **P1: transport refusals leaked rejected identities and mapping
   keys.** The walk joined each mapping key into the path it reported,
   and the CLI built the location out of the stage and the name the
   command line carried, so a rejected value under a credential-shaped
   key printed both. The paste that produces this refusal most often is
   a credential typed one argument early, which made the sentence print
   exactly the value it exists to protect.

   *Resolution:* a key is never said. A mapping step is the fixed word
   `transport.FIELD` (`<field>`) and a list step is its index, which is
   a fact about the document's shape rather than about anything written
   in it; an operator can still count the steps to the value.
   `check_transportable` takes the fixed section (`providers`) rather
   than the addressed location (`providers.<stage>.<name>`), and a case
   holds every call site to a word with no separator in it.
   `models.safe_location` was checked first, as the reviewer asked, and
   is deliberately not the mechanism: it keeps the prefix a pydantic
   model declares, and there is no model in hand in front of
   validation, of a fragment whose kind may not be known yet.
   `tests/unit/test_config_cli_untransportable.py` plants five
   sentinels (stage, name, key, nested key, value) over stdout, stderr,
   the requests the client would have sent (bodies and headers), the
   log records whole and the exception chain; reverting the fix turns
   six of its nine cases red.

2. **P1: a bare install tracebacked from `vinga-server doctor` with no
   URL.** The derivation imports the onboarding package, whose
   `__init__` reaches FastAPI, outside the missing-half boundary, and
   the tier proof ran only `doctor --help`, which never enters that
   branch.

   *Resolution:* the gate goes on the derivation and not on the
   command, which is the half of this that matters. A workstation
   diagnosing a deployment it does not host passes the URL: it opens a
   socket, reads what answers and wants nothing of the server half, and
   that is the laptop case the thin install exists for. Only deriving
   the URL reads the onboarding key, so only that answers
   `NEEDS_THE_SERVER_HALF`, recorded inside the handler and raised
   outside it. Both halves are now driven for real from the bare client
   environment, and both from the serve environment to show the gate
   opens. Four-surface sentinels in
   `tests/unit/test_missing_server_half.py`; the changelog entry names
   four gated sites and the case that pins the division was rewritten
   to say that `doctor` gates one branch inside itself rather than at
   the dispatch.

3. **P2 (re-review, terra): the no-leak guard tested the vocabulary and
   not the callers.** The section words were held to being fixed, and
   what production actually passes was never observed, so a call site
   that went back to `providers.<stage>.<name>` would have stayed
   green.

   *Resolution:* both halves. A static walk over `src/` finds every
   call to `check_transportable`, unparses its first argument and holds
   it to a closed set of three written expressions, following the one
   forwarding hop this tree has (the store's `_readable` takes the
   section and hands it on, so a guard stopping at the direct call
   would be reading a parameter name and calling it fixed). The set of
   modules that call it at all is asserted too, so a third call site is
   a review event and a walk that finds nothing fails. Beside it, a spy
   on both modules' bound name records what each production path
   actually receives, driven with credential-shaped identities: the
   CLI's entity write gets `providers`, its apply gets `document`, and
   the repository's own write gets `providers` while keeping the
   addressed location for every other refusal it makes. Reintroducing
   the addressed form at the CLI call site turns seven cases red;
   reintroducing it at the store's forwarding hop turns three red,
   including the static walk both times.

4. **P2: the tier "closure" checked a subset.** The six direct client
   names had to be present and the ten direct serve names absent, which
   says nothing about a transitive distribution, and that is the shape
   a heavy dependency comes back in.

   *Resolution:* the expected set is the recursive walk of `uv.lock`
   from each tier's roots, extras and markers included, compared to the
   installed set with `==` in both directions. Markers are evaluated
   against the environment being installed into, read from that
   interpreter with the standard library alone, and the walk carries
   `(name, extra)` pairs, since `uvicorn[standard]` and a bare uvicorn
   install different sets. The environments moved from
   `uv pip install` to `uv sync --frozen --no-dev --no-editable`, which
   is what makes the comparison possible at all: `uv pip install`
   re-resolves from the index and had produced three distributions
   (`httpx2`, `httpcore2`, `truststore`) that the lock does not reach,
   so it was being compared to a graph it did not come from. It is also
   what the image build and a contributor run. The six direct client
   names stay as an independent oracle read from `pyproject.toml`, and
   a bite case doctors the expected set in each direction to prove the
   comparison rejects what it claims to. `packaging` becomes a declared
   dev dependency, since the walk imports it; the lock moves two
   dev-group lines and no version resolves differently, so the image
   build is untouched.

### Verification

From `vinga-server/`, on the rebased tree, everything green:

- `uv run ruff check .`: `All checks passed!`
- `uv run mypy`: `Success: no issues found in 4 source files`
- `uv run pytest tests/unit -q -n auto --dist loadfile`: `3680 passed,
  19 skipped in 43.33s`
- `uv run pytest tests/integration -q`: `154 passed in 203.17s`
- The six drift checks exactly as CI runs them (`domain-config.md`,
  `conversations-schema.md`, `events.md`, `api-openapi.json`, `cli.md`,
  and the recipes inside it), each regenerated under its own lane and
  diffed: all six identical.
- The spelling census over the whole tree: 999 matches over 125 files
  (243 `respell`, 579 `historical`, 177 `generated`), the manifest
  regenerated and diffed, and every `respell` this milestone added or
  moved naming a command the tree has.
- Importing `config.cli` loads 16 `vinga_server` modules and none of
  FastAPI, SQLAlchemy, cryptography or Alembic. Importing `main` loads
  12 and none of them either.
- Both tiers into clean environments, which is
  `tests/integration/test_tier_closure.py` (23 cases) and also run by
  hand: the client install resolves to its own `site-packages`, holds
  exactly the 22 distributions the lock's client closure reaches and
  not one more, carries all 6 direct client names and none of the 10
  serve ones, imports `config.cli`, answers `vinga --version`, prints a
  help page for every ungated row of `COMMANDS` as a subprocess,
  refuses `openapi`, `ota-url`, `vinga-server conversations schema` and
  `vinga-server doctor` with no URL with the fixed sentence and exit 1,
  and still answers `events reference` and
  `vinga-server doctor <url>`; the serve install holds exactly the 56
  the serve closure reaches, renders the conversations schema, derives
  an onboarding URL, and reaches the boot rather than the cannot-serve
  sentence.
- The contributor smoke: `uv sync --frozen` into an environment of the
  test's own, the whole serve tier present, and `vinga-server` reaching
  the boot.

Not verified here, and not claimed:

- **The image build.** `docker` is on this machine and its daemon is
  not running, so neither variant was built or booted locally. The
  Dockerfile changed, so the pre-merge `workflow_dispatch` run is what
  covers it, per the standing rule.
- **The `uvx --from git+...` invocation itself**, which needs the branch
  to be pushed and the network. What it resolves to is the bare wheel's
  closure, and that closure is what the client half of the tier lane
  installs and drives.
- Anything on hardware. None of it is M2's.

## M3: the wheel-grade lane and the install story

PR #298.

### What landed

- The wheel-grade subprocess lane
  (`tests/integration/test_cli_wheel.py`): the wheel built, installed
  bare into a clean environment, and the `vinga` binary driven as a
  program against a live in-process server, with provenance proven and
  the full registered inventory run in both directions.
- The in-process security lane retained beside it, every case unchanged
  and still green, still complete against `cli.COMMANDS`.
- The document-as-data contract check
  (`tests/unit/test_api_contract.py`), and the one production change it
  needed: both of an act's contract shapes are now facts on the act.
- The shared matcher taught to ask what a row could TAKE, which is the
  discovered work below and the reason a retired verb-first spelling
  could pass the census guard for two milestones.
- `serving`, `Live` and `check_in` moved to
  `tests/support/deployment.py`, so the two lanes read one live server
  rather than two copies of one.
- Decision 12's skew policy on `docs/reference/cli.md`, the note on
  what the wheel lane proves beside the laptop door, and one changelog
  entry.

### Rebased onto the merged M2

This milestone was written against M2's branch tip and rebased onto
`main` at `46a34b4e`, after M2 (PR #296) merged with two review passes
behind it. Thirteen commits dropped as already upstream; the seven of
this milestone replayed, two of them with conflicts, both in
`tests/unit/command-spellings.txt`. It is a generated artifact, so it
was regenerated with its own tool at each step rather than merged.

M2's fix round arrived with four commits, and each was checked against
what this milestone touches:

- **`transport.py` reports fixed `<field>` and index steps only**, and
  `check_transportable` takes the fixed section rather than an
  addressed location. It is called from `_fragment_body` and
  `_document_body`, which this milestone changed the ROWS around and
  not the bodies, so nothing of it moved. The static AST walk and the
  runtime spies over every caller pass on the rebased tree.
- **The doctor's no-URL derivation is gated** and `doctor <url>` stays
  thin. `doctor` is a sibling of the `config` group rather than a row
  of `cli.COMMANDS`, so neither the wheel lane's inventory nor the
  spelling census's matcher reaches it, and the tier lane is where both
  of its invocations are driven.
- **The tier closure became a recursive `uv.lock` walk compared exactly
  both ways**, and its fixtures moved from `uv pip install` to
  `uv sync --frozen --no-dev --no-editable`. This lane deliberately did
  NOT follow, and the reason is now written in its own head: that lane's
  question is what the DECLARATION resolves to, which is why it needs an
  environment it can hold to a graph; this lane's question is what the
  built ARTIFACT carries, which means installing that file and nothing
  else, and a sync would install the project from the source tree rather
  than from the wheel. Neither lane makes the other's claim: nothing
  here compares a distribution set to anything.

Nothing of M2 was weakened. The gated trio, `NEEDS_THE_SERVER_HALF`,
`serving.py`, the three #287 structure tests, the tier lanes and the
meta-path-finder simulation all survive untouched, and the tier lane and
the in-process security lane are both green on the rebased tree.

The contract check and the census were re-run against the merged tree
rather than assumed. Neither flagged anything from the fix round: the
document carries the same forty operations, and no `respell` spelling
names a command the tree does not have. The manifest moved by line
numbers alone in the files the fix round edited, plus the one line this
milestone's own record adds.

### The wheel lane, its shape and its cost

Twelve cases, nineteen seconds on this machine, including the build and
the install. The shape, in the order it runs:

- **Provenance first**, before any command. `vinga_server` resolves
  inside the clean environment, does not resolve to `src/`, and the
  installed distribution's `direct_url.json` names the wheel this lane
  built. The third is the one the first two cannot make: an editable
  install of the same version passes both of them.
- **One operator's session**, from an empty database: a whole
  deployment applied from a document, thirteen reads, the two device
  writes, the reload and the three reads of the running process, the
  onboarding ceremony with a real check-in, every destructive verb, and
  the three documents that reach no server.
- **The gated pair run**, not imported, and asserted to print the fixed
  sentence and exit 1.
- **Completeness both ways**, last: every ungated row ran and answered,
  no gated row answered, and nothing was driven that the table does not
  hold. Held against the recording rather than a list, so the only way
  into it is a command that ran.

Every command goes through the installed binary, from a directory
outside the checkout, with `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`
and `VINGA_CONFIG` scrubbed. The fragments the session writes are the
lane's own, in that directory, so no command can reach the source tree
by a relative path. That the wheel carries the example fragments is
proven the other way round, by the recipes region of the reference
rendering non-empty from the installed artifact, which is the one
assertion in the file that would go red on a packaging mistake rather
than on a code one.

What it does not claim, and what the in-process lane is retained for:
anything about a client-side log record, an unformatted argument, an
extra attribute or an exception chain. All four are invisible from
outside a child process, so this lane asserts on exit codes, stdout and
stderr and nothing else.

Held to going red: removing one read from the session's list makes the
completeness case name it (`agent show`), which is the check that the
recording is a recording.

### The contract check, in numbers

Forty operations in the committed document. Thirty-one covered by acts
of the grammar, nine excluded with a reason apiece: the five collection
reads (paging a kind is the admin UI's), `GET /default-agent` (a
setting with two verbs and no reader, since what is stored is a line of
the document `show` prints), and the three conversation routes.

What turns it red, each proven by mutating one thing and watching the
assertion fire:

- a route added to the document with neither a command nor an
  exclusion, or a command pointed at a path the document does not have
  (the union);
- an operation in both sets (the overlap);
- an exclusion naming an operation the document no longer has;
- an act declaring a response shape the document does not answer with;
- an act that stopped naming the body it sends, or that names one where
  the document declares none.

The paths are not written down anywhere in the test. An act's path is a
function of an invocation, so it is given one whose identities are
their own parameter names (`{stage}`, `{name}`, `{mac}`, `{code}`,
`{slot}`) and the percent-encoding is undone afterwards. That is what
let the comparison run through the production code that builds the
address without `Act` gaining a `route` field it has no runtime use
for.

### The renderer-validation move, and how big it actually was

Sixty-nine lines added and thirty removed in `config/cli.py`, and
twenty-two lines across three cases in one test file. Decision 11's
escape hatch ("if it turns out larger than it looks it moves to M1 with
the rest of the table work") did not trigger.

It went slightly further than the four acts the plan names, and the
reason is the plan's own rule about two structures that must agree.
`answers` became REQUIRED rather than optional, because with the four
listings declaring their shapes every act has one and an optional field
would have been a hole nothing could be held to. `_diff_listing` lost
the same duplication from the other side: its row already declared
`ConfigDiff` and its renderer validated against `ConfigDiff` again.
And `_status_listing` and `_status_block` collapsed into one function,
since the only reason there were two was that the reload answers a
status document inside its own shape and validating it twice would have
been a second encoding.

The reading is `Act.read`, a method, rather than a call to
`_understood` at the dispatch site. That is what "onto the act" means
for a caller: the same fact the contract check compares against the
document is the one the command validates with, and the three refusal
cases that used to drive a renderer directly now drive the row.

### The census matcher, and what it newly caught

`registered` matched the longest registered prefix and stopped, so
`show provider llm claude` resolved to the flat `show` row and the
three words behind it went unread, though `show` takes no positional at
all. Every retired verb-first spelling in the tree therefore passed the
census guard while naming a command the grammar no longer has; M2's
discovery section had found it from one site and left the matcher
alone.

The fix is that a candidate row also has to be able to be GIVEN what
follows it. The budget is walked off `cli.command()` rather than listed,
so a command that gains an argument gains the room for it and a
variadic argument is no bound at all; the count stops at the first
option, which is leniency in the safe direction, since what this exists
to catch is a retired spelling carrying its old address and those carry
no options.

That alone was not enough, and the second half is a deviation the plan
does not name. The recognizer's capture took every bare token after the
command word, which ran an address and the rest of an English sentence
into the invocation alike. Harmless while the tail was ignored; the
difference between two things once it is not. With the budget rule
alone, two prose sites went red as stale commands:

- `events/catalog.py`, an OTA warning ending "check the URL typed into
  the device's captive portal against the one `vinga-server config
  ota-url` prints", where `prints` was read as an argument to a command
  that takes none;
- `docs/concepts.md`, the English phrase "the config reference documents
  that distinction where".

Neither is a stale command, and neither could be told from
`show provider llm claude` by counting words. So a word after the first
is taken only when the grammar uses it as a command word somewhere,
which is what `_command_line`'s own docstring already claimed ("the
words stop where an address begins") and what it did not do.

The manifest moved with both: the invocations of addressed commands
shorten to their command words, so two sites quoting one command with
different arguments now read as one entry. 1011 matches over 125 files
(243 `respell`, 591 `historical`, 177 `generated`), against M2's 999
over 125 before its own fix round; the growth is this milestone's own
negative fixtures, which live in a file classified `historical` for the
reason its own comment gives.

No stale `respell` spelling survives, and no site in the tree had to be
reworded to make that true.

### Deviations from the plan

Six.

1. **The renderer-validation move covered five acts rather than four,
   `answers` became required, and two status functions became one.**
   The plan names the four that leave `answers=None`. Doing only those
   would have left `diff` declaring its shape on the row and validating
   it again in the renderer, which is the duplication the move exists
   to remove. Size and reasoning above.

2. **The reading is a method on `Act` rather than a call at the
   dispatch site.** The plan says the validation "moves out of the
   renderer and onto the act". A method is what that means for a
   caller, and it is what let the three refusal cases stop reaching for
   a private helper.

3. **`serving`, `Live` and `check_in` moved to
   `tests/support/deployment.py`.** The plan says the in-process lane
   is retained unchanged, and every one of its cases is; what moved is
   three helpers the wheel lane needs too. A second copy of a uvicorn
   thread and its readiness loop would be one pending bug of the usual
   kind: the copies drift, and for a readiness loop that means a flake
   nobody can place.

4. **The exclusion set carries no `/healthz` entry, and does carry
   `GET /default-agent`.** The plan names the conversation routes, the
   collection reads and `/healthz`. The committed document is the
   configuration API's alone and has no `/healthz` operation, so an
   entry for it would have been an exclusion excusing nothing; the
   union assertion is what says so, and a case holds every exclusion to
   naming an operation the document has. `GET /default-agent` is the
   entry the plan did not anticipate: the setting has two verbs and no
   read command, because what is stored is a line of the document
   `show` prints.

5. **The wheel lane does not run the published recipes.** The
   in-process lane already runs them verbatim, and it runs them against
   a server of their own because they configure real engines
   (anthropic, faster-whisper, piper, elevenlabs): a reload of that
   store is refused, since the engines cannot be built here. Driving
   them here would have meant a second server and about nine more
   seconds to re-prove what the other lane proves directly. What this
   lane needed from `examples/` instead is the one thing only an
   installed artifact can show, which is that the wheel carries them,
   and the reference render says that in one assertion.

6. **The progress-line issue is not filed.** The plan's M3 entry says
   this section files the owed TTY progress line for `apply` and
   `reload` as its own issue. This run has no write access to the
   tracker, so it is recorded here instead and is owed: the deliverable
   is a progress line at a terminal for the two long waits, with a
   determinism proof of its own (the non-terminal path byte-identical),
   which is the reason decision 7 left it owed rather than riding M1.

### Resolutions of what the plan left open

- **Decision 11's "if it turns out larger than it looks it moves to
  M1"** did not trigger, for the second milestone running. The whole
  production change is one module.
- **How an act's path is compared to a templated one** without giving
  `Act` a field with no runtime reader: the path is a function, so it
  is called with an invocation whose identities are their own parameter
  names. Recorded because the obvious alternative, a `route` string
  beside `path`, would have been two structures that must agree.
- **What `sends` is for.** It is declared and never validated against.
  A fragment is the operator's YAML and the server is what refuses a
  bad one, so a second refusal here would be a second encoding of one
  rule; what reads the field is the contract check, and #287's
  generator after it. The field cannot go stale quietly because the
  check compares it against the document in both directions.
- **The census manifest's invocation strings** are the command words
  and no longer the addresses after them. That was implicit in what
  `_command_line` claimed to do and explicit in nothing.

### Discoveries

- **`uv pip install <wheel>` writes a PEP 610 `direct_url.json` naming
  the wheel file**, which is the one thing that tells an installed
  artifact from an editable install of the same version. Both of the
  other provenance checks pass on an editable install.
- **The published recipes cannot be driven against a server the lane
  then reloads.** They configure real engines, and a reload builds what
  the stored configuration names, so the refusal is correct and the
  in-process lane's use of a second, isolated server for them is not
  incidental.
- **Every act has an answer**, which is what made `answers` a required
  field rather than a defaulted one. There was no act in the grammar
  that sends a request and reads nothing.
- **The recognizer's docstring was right and its code was not**, which
  is a shape worth naming: the sentence "the words stop at the first
  token that is not a bare command word, which is where an address, an
  option or the rest of a sentence begins" describes the vocabulary
  rule, and `_bare` implemented a character-class rule. Nothing failed
  while the tail went unread.

### Verification

From `vinga-server/`, everything green:

- `uv run ruff check .`: `All checks passed!`
- `uv run mypy`: `Success: no issues found in 4 source files`
- `uv run pytest tests/unit -q -n auto --dist loadfile`: `3790 passed,
  19 skipped in 77.64s`. Two later runs of the same lane on the same
  tree reported one failure,
  `test_tts_lookahead.py::test_the_frame_cadence_stays_smooth`, and
  both took seven minutes rather than one: this machine was at a load
  average of 139 while other worktrees ran their own suites. That case
  measures wall-clock intervals between audio frames and detects the
  pacer catching up after a stall, which is what a loaded scheduler
  produces; it passes alone in 3.42s, and this milestone touches
  nothing in the TTS or session path. Recorded rather than smoothed
  over, because a timing case that goes red under load is worth knowing
  about even when it is not this milestone's.
- `uv run pytest tests/integration -q`: `166 passed in 221.53s`, of
  which the wheel lane is 19 seconds
- The wheel lane on its own: `12 passed in 18.92s`, of which the build
  and the install are the module fixtures.
- The six drift checks exactly as CI runs them (`domain-config.md`,
  `conversations-schema.md`, `events.md`, `api-openapi.json`, `cli.md`,
  and the recipes inside it), each regenerated under its own lane and
  diffed: all six identical. No artifact moved in this milestone, which
  is what the plan's move list requires of M3.
- The tier proofs from M2 still green on the rebased tree, including
  its fix round's exact `uv.lock` closure and both real `doctor`
  invocations (`tests/integration/test_tier_closure.py`), and the
  in-process security lane still green and still complete
  (`tests/integration/test_cli_live.py`, `70 passed in 6.86s`;
  `tests/integration/test_tier_closure.py`, `23 passed in 77.71s`).
- The spelling census over the whole tree: 1011 matches over 125 files
  (243 `respell`, 591 `historical`, 177 `generated`), the manifest
  regenerated and diffed, and no `respell` naming a command the tree
  does not have.
- The contract check held to going red under five separate mutations,
  one per assertion, run by hand rather than asserted about.
- The wheel lane's completeness case held to going red by removing one
  driven command from the session.

Not verified here, and not claimed:

- **The image build and its smoke lane**, which needs a
  `workflow_dispatch` run. M2 changed the Dockerfile; M3 changed
  nothing about it.
- **The `uvx --from git+...` invocation itself**, which needs the branch
  pushed and the network. What it resolves to is the bare wheel's
  closure, and that closure is now what this lane builds, installs and
  drives.
- Anything on hardware. None of it is M3's.
