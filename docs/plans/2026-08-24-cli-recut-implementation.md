# Turning the CLI around: implementation

The companion to [`2026-08-24-cli-recut.md`](2026-08-24-cli-recut.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: the grammar turns around

PR TBD.

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
  tree from `row.words[:-1]`.
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

567 matches over 94 files, after the rename: 186 `respell`, 193
`historical`, 188 `generated`. Before the rename the same tool found 525
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
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 3464 passed,
  21 skipped
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
