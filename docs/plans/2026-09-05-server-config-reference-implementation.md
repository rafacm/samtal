# Generate the server-half configuration reference: implementation

Companion to
[`2026-09-05-server-config-reference.md`](2026-09-05-server-config-reference.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the server-half models carry their own documentation

### What was done

`config/docgen.py`. `_nested_model` is `nested_model`, public, with a
docstring paragraph saying why: walking a model graph is not this
document's private business, and the M1 coverage test is its second
caller. Its three call sites moved with it and nothing else in the
module changed. `__all__` was left alone, which is the shape
`type_name` and `default` already have there: both are public, both are
read from other modules and from the grammar tests, and neither is
listed.

`config/models.py`, at the fields' one home. All 47 fields reachable
from `ServerConfig` carry a `Field(description=...)`, counted by the
same reflection walk the coverage test runs: `ServerConfig`'s own 23,
and the 24 across `OnboardingConfig`, `AuthConfig`, `ApiConfig`,
`LimitsConfig`, `DatabaseConfig`, `CaptureConfig` and
`ConversationsConfig`. One of them existed before (`api.secret_env`);
the other 46 are the operator-facing content of the `#` comments beside
them, moved rather than written twice. Every description is written so
its first sentence stands alone, since that is the sentence
`docgen._sentence` takes for a help line.

The comments whose whole content became a description are gone. Three
kinds of comment stayed, each because it is implementation-facing:
`conversations.retention_days` keeps the note that its default is the
store's own default and the store's tests pin it, `_LOG_LEVELS` keeps
the note that it is the one home of the level set (rewritten, since the
NOTSET reasoning it carried is an operator fact and moved to the field's
description), and the module-level constants keep their own reasoning.

The validator-enforced rules are stated on the fields they bound, per
the plan's finding-6 resolution:

- the environment-variable-name shape on both `EnvName` fields
  (`api.secret_env`, `auth.secret_env`): letters, digits and
  underscores, not starting with a digit, so a pasted credential is
  refused where it was written rather than at a lookup that could never
  have found it;
- the eight base32 characters (A-Z and 2-7) on `onboarding.key`, with
  the normalization to upper case;
- the five log levels on `log_level`, named from `_LOG_LEVELS` rather
  than restated, so the description, the refusal and the reference all
  read one tuple;
- every `ota_path` restriction: the leading and trailing `/`, the
  reserved API mount, the reserved onboarding mount and the two probe
  paths, each named from `API_MOUNT_PATH`, `ONBOARDING_MOUNT_PATH`,
  `HEALTH_PATH` and `READY_PATH` and each with what the collision would
  do, plus the boot refusal it is half of;
- both URL contracts: `websocket_url` states ws/wss, a host, and no
  userinfo; `public_url` states http/https, an origin with an optional
  path prefix, no userinfo, and no query or fragment.

`ServerConfig` gained the docstring it never had: what the server half
is (the `server:` section of the YAML file, the whole of what the file
holds), that it is read once at start and never re-read by a running
process, which is the line between it and the domain half, the
`VINGA_SERVER__<PATH>` override scheme with the database section as its
recorded exception, and that no secret is ever written here.

`NOTHING_DISCOVERABLE` is a module-level constant beside
`RESUMPTION_NEEDS_RECORDING` and `RESUMPTION_NEEDS_TEXT`, raised by
`ServerConfig._check_something_is_discoverable`. The sentence is
byte-identical to the inline string it replaces, verified against the
pre-change file rather than by eye.

`tests/unit/test_config.py` gained the coverage test, in three cases
over one reflection walk: every reachable model carries a docstring,
every reachable field a nonempty description, and a vacuity guard
insisting the walk finds `ServerConfig` first, at least eight models and
at least 47 fields, since an empty walk would pass the other two
silently. The walk goes through `docgen.nested_model`, never a hand list
and never an underscore. Both guards were proven to bite by deleting one
description and blanking one docstring in a scratch copy of the module
and watching each case fail by name, with the module restored from the
copy and touched afterwards.

That file rather than a new one: it is where these models are already
tested from the outside, holding the log-level normalization, the
`ota_path` refusals, the example-config parse and the boot cases.

### Deviations from the plan

Two, both small, both recorded rather than absorbed.

**`ConversationsConfig`'s docstring was factually stale and is
corrected.** It said the conversation record lives in a `conversations`
schema of the database. It lives in `record`
(`conversations/schema.py`'s `SCHEMA`, and `deploy/postgres-init.sql`
grants the read-only role `SELECT` on `record`). M1 is the milestone
that makes docstrings render as a page's section prose, so publishing
the wrong schema name was the alternative. `database.name`'s new
description names all three schemas (`domain`, `record`, `memory`) from
the same reading.

**`config.example.yaml` was reviewed and left untouched.** The plan
allows touching it "only where a comment restates a bound the
description now owns". Nothing there does: what the example states are
defaults and reasoning in its own voice, which is what an annotated
starting point is for, and the file's coverage guard
(`test_config_examples.py`) keeps it honest independently. So the
milestone's documentation footprint is genuinely none.

Two smaller judgment calls, neither a deviation from anything the plan
says, noted because a reviewer would otherwise have to reconstruct
them. The issue references the moved comments carried (`#14`, `#28`,
`#30`, `#68`) stayed in the descriptions they moved into: they are the
provenance of numbers chosen against field data, and the docstrings
beside them already carry references that will render on the same page.
And `nested_model` was not added to `__all__`, for the reason given
above.

### No changelog entry

Deliberate, and the plan assigns it to M2. M1 surfaces nowhere a user
can see: no page is committed, no command changed, no validation moved,
and every generated artifact is byte-identical. The entry lands with the
page that publishes these descriptions.

### Verification

Run from `vinga-server/`, against the development Postgres on 5432, with
`PYTHONDONTWRITEBYTECODE=1` exported for everything run outside pytest.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `5534 passed, 19 skipped in 589.57s`
  with one failure, the stale census below, run before it was
  regenerated.
- `uv run pytest tests/unit -q -n auto --dist loadfile`, which is how CI
  runs the lane, on the finished tree: `5535 passed, 19 skipped in
  84.04s`.
- `uv run pytest tests/integration -q`: `243 passed in 356.26s`
- `python3 scripts/check_doc_links.py .` from the repository root:
  `checked 194 files, 0 failures`.
- The census manifest went stale, as the plan's risk list predicted,
  and for two reasons rather than one. The plan document itself quotes
  seven command spellings and was committed without the manifest being
  regenerated with it, so the census was already red at this branch's
  head before any code moved; the descriptions then shifted the line
  numbers of the `respell` rows in `models.py` and `docgen.py`.
  Regenerated with `uv run python -m tests.unit.test_command_spellings`
  after staging the new document, never by hand, in this same commit.
  The implementation document adds no row of its own: it quotes no
  command of the grammar.
  `uv run pytest tests/unit/test_command_spellings.py -q`:
  `48 passed`.
- The generated artifacts are byte-stable, as the plan claims. The four
  freshness pins are green inside the unit lane
  (`test_the_committed_reference_matches_the_models`,
  `test_the_committed_cli_reference_matches_the_grammar`,
  `test_the_committed_cli_recipes_match_the_example_fragments` and the
  OpenAPI document's), and `git status` shows no generated file
  changed: `docs/reference/domain-config.md`,
  `docs/reference/cli.md` and `docs/reference/api-openapi.json` are
  untouched by this milestone, which is what M1 predicted, since these
  descriptions render nowhere yet.

### PR review round

External review of the branch as pushed to PR #400, at `64a1d35d`:
backend codex (codex-cli 0.153.0), model gpt-5.6-sol, 2026-09-05,
runtime 6m30s. Four findings, three P2 and one P3, verdict as received:
mergeable after the listed fixes. All four were confirmed against the
sources before being fixed; none rejected.

All four are the same shape, and it is worth naming: **a docstring
sentence that was true when nobody read it.** These paragraphs have sat
in the module for months as comments a reader skims past. M1 turns them
into published prose, which is what made four wrong sentences worth
finding, and the review found them the milestone before the page that
would have printed them.

1. **P2: the onboarding docstring contradicts the field below it.**
   `OnboardingConfig`'s docstring said the key is "never stored and
   never written here" and "rotates only when the secret does", one
   paragraph above a `key` field that stores one in the file precisely
   so that it survives a rotation. Fix: qualify the paragraph as the
   unset case and say what `key` pins.

   *Resolution* (`ddf5dbe6`): accepted in full. The paragraph now says
   it is describing the normal, unset case, where nothing about the key
   is stored and it moves only when the secret does. A second paragraph
   names `key` as the one exception and what pinning buys: the boards
   already provisioned keep reaching the URL they were given while the
   new secret takes over everything else.

2. **P2: "no secret is ever written here" is false for two paths.**
   `ServerConfig`'s docstring made the claim unqualified, and
   `docs/xiaozhi-notes.md` records the opposite in a heading of its own:
   the OTA URL is the one field an operator can put a secret into,
   because a stock board can present nothing but a MAC at its first
   call, so the token issuer is protected by its path. A pinned
   `onboarding.key` stands in front of the same endpoint. Fix: restrict
   the claim to credentials and name the two path fields.

   *Resolution* (`05a5d05f`): accepted in full. The claim says
   credentials now, and names the three that are named rather than held
   (the API's bearer token, the device-auth secret, the database
   password) beside the two that have no key at all. A second paragraph
   names `ota_path`'s random segment and `onboarding.key`, says why
   they are sensitive without looking it, ties that to their refusals
   never quoting them back, and gives the two environment spellings
   that keep them out of a committed file.

3. **P2: one exception named where the contract has two.** The
   docstring said a domain change is an apply except for a device
   binding. The recorded contract has two kinds applying at check-in,
   `devices` and `default_agent`: the comparison's boundary table in
   `tests/unit/test_config_cli_rendering.py` labels both, and the
   domain reference says the same in prose. Fix: name both with the
   check-in timing.

   *Resolution* (`3e0d857e`): accepted in full. The sentence names both
   kinds and says what check-in means here: a running server re-reads
   them as a device asks for them, so the change reaches that board at
   its next check-in with nothing asked of the server at all.

4. **P3: capture's default compared to a flag that defaults the other
   way.** `CaptureConfig`'s docstring said it is off by default "the
   same shape as `auth.enabled`", and `auth.enabled` is on by default,
   so the comparison said the opposite of what it meant. Fix: compare
   with `conversations.enabled` or drop the comparison, keeping the
   pair coherent rather than circular.

   *Resolution* (`07023243`): accepted, second option, since
   `ConversationsConfig` already points at capture and pointing back
   would have been the circle. `CaptureConfig` states the shape itself,
   being the first of the two models to have it: the section has to
   exist and the flag has to say so, and neither writing the section
   nor leaving it in place is consent. `ConversationsConfig`'s sentence
   is reworded by one phrase to read as a pointer at where the rule is
   stated.

#### Verification after the review round

Run from `vinga-server/`, against the development Postgres on 5432.

- `uv run ruff check .`: `All checks passed!`
- The four touched suites together
  (`test_config.py`, `test_config_docgen.py`, `test_config_examples.py`,
  `test_command_spellings.py`): `240 passed in 10.88s`.
- The four generated-artifact freshness pins, run by name:
  `4 passed`. `git status` shows no generated file changed, which is the
  claim M1 makes and which docstring and description edits cannot move:
  nothing renders them yet.
- The census manifest went stale again, and only from line shifts: the
  three `respell` rows in `models.py` moved by twenty lines as the
  docstrings grew. Regenerated the standard way in this record's own
  commit.

## M2: the generator, the command, the committed page and the drift check

### What was done

Ordered so that the one claim this milestone cannot assert is proven by
where it sits. The commits, in order: the database environment names
moved to one home; `paragraph` and `cell` promoted; `BOOT_REFUSALS`
declared; the renderer and its suite; the `HALF` selector with the
regenerated `cli.md`; the committed page with its CI step and freshness
pin; the domain preamble pointed at the new page, regenerating
`domain-config.md`; the cross-links and the changelog; the CLI's import
inventory; this record with the milestone tick and the census; and the
live lane's refusal row, which the integration lane turned up after the
tick and which is the eighth deviation below.

**`config/models.py` is the one home of the database environment
names.** `DATABASE_ENV_PREFIX` and the four `DATABASE_ENV_NAMES` came
from `loader.py`, and the two credential-only names came from
`vinga_server.db` as `DATABASE_PASSWORD_ENV` and `DATABASE_URL_ENV`,
with `db/__init__.py` aliasing `PASSWORD_ENV` and `URL_ENV` to them so
none of its callers moved. `loader.py` imports all of them back. Every
string is byte-identical to the one it replaced, which is what makes
the move behavior-neutral.

**`config/server_reference.py`, the new module.** `reference()` renders
the page and the ordered `HALVES` registry maps `domain` to
`docgen.reference` and `server` to it, with `render(half)` beside them.
The page: the do-not-edit header naming `vinga reference server` from
`PROGRAM` and the registry's word, a preamble stating the split and
linking both neighbours, `## Environment overrides`, `## What
deliberately has no key`, a `##` section per model in field-declaration
order (`server`, then `server.onboarding`, `server.auth`, `server.api`,
`server.limits`, `server.database`, `server.capture`,
`server.conversations`) carrying that model's whole docstring and a
five-column table, and `## Refused at boot`. Every model section's
heading is its path in a code span, which is what tells the suite a
section from the prose around it.

**The Constraints column** renders `Ge`, `Gt`, `Le` and `Lt` from
`FieldInfo.metadata`: a closed pair as a range (`1 to 65535`), anything
else as its symbols (`>= 512`, `> 0`), and an empty cell where a field
has no bound. Metadata that is not a bound is deliberately not
rendered: an `AfterValidator` is a rule with no numeric form, and those
are stated in the field's own description, which M1 wrote them into.

**`BOOT_REFUSALS`** sits below the models rather than beside the
sentences, because a row names a model and a model has to exist to be
named. Three rows, each carrying the owning model, the validator's
name, the sentence constant and a provoking mapping.

**The selector.** `reference` takes one optional positional declared by
`_of_a_half`, scoped to that row alone; `openapi` and `cli-reference`
keep `_rendered`, whose docstring now says it is the two documents
rendered from the routes and the command tree. The help lists the
registry's keys, the default is the registry's first row, and the
refusal for a name that is neither reads the same tuple.

**Tests** are `tests/unit/test_server_reference.py`, 23 cases: the
child-interpreter isolation pin on `server_reference.reference()`
imported directly, the CLI-level case that `reference server` runs with
no database reachable and no key, determinism, the section inventory
against the reflected model graph, each section's exact field sequence,
the bounds sweep and the two readable shapes, six semantic assertions
for the validator-enforced rule families, the environment names derived
from the moved constants, the refusals in both directions with the
validator sweep, the selector's three cases including the planted
credential, and the committed-copy pin. Each of the coverage,
constraints and refusal checks was proven to bite by mutating the
renderer (dropping a nested section, truncating a bound list, dropping
a refusal row) and watching the right cases fail by name, with the
module restored from a copy and touched afterwards.

### How the byte-identical claim was proven

The plan's finding-1 resolution asks for sequencing rather than an
assertion, and that is what the history holds. The selector landed in
`6b7f1a6a`, which touches `config/cli.py`, the new suite and the
regenerated `docs/reference/cli.md`, and touches neither
`config/docgen.py` nor `docs/reference/domain-config.md`
(`git show --stat` on it says so). The domain suite's
`test_the_committed_reference_matches_the_models` is green at that
commit, and since the committed page did not move there, its passing is
the statement that bare `reference` renders the same bytes it rendered
before the selector existed. The preamble pointer is `f0f1459d`, its own
commit, with its own regeneration of `domain-config.md`.

### Deviations from the plan

Eight, none of them a change of direction, all recorded rather than
absorbed.

**The override scheme's own constants moved with the database names.**
The plan lists four declarations; keeping `DATABASE_ENV_PREFIX` derived
rather than written out needs `ENV_PREFIX`, and rendering the refused
generic spelling from a constant rather than from prose needs the
nesting delimiter and the `server` root. So `ENV_PREFIX`, `ENV_NESTING`,
`SERVER_ENV_PREFIX`, `DATABASE_SECTION` and
`DATABASE_GENERIC_ENV_PREFIX` are declared beside them, and `FileConfig`
now reads the first two as its settings configuration instead of
repeating them as two literals, which closes a duplication that was
already there.

**`_LOG_LEVELS` is `LOG_LEVELS`, public.** The page publishes the five
levels as a rule an operator is held to, and the test that holds the
page to naming every one of them is the constant's second caller.
Reaching for an underscore to do that is the review flag the design
conventions name, so this is the M1 promotion of `nested_model` applied
to the same shape.

**The child-interpreter harness moved to `tests/support/isolation.py`
rather than being imported.** The plan says to reuse the docgen suite's
harness; a test module may not import another test module
(`test_support_boundaries.py` enforces it), so the runner and the
`ALLOWED_IMPORTS` set moved to support, which is that rule's own
remedy. The two suites now read one allow list, and the server suite's
expectation is that set plus its own module, which states the
relationship rather than copying it.

**The committed-copy pin runs under `packaged_database`.** The unit
lane rewrites `DatabaseConfig`'s four field defaults onto the database
the run provisioned. That is invisible in the domain reference and is a
Default cell here, so an in-process render inside the lane cannot equal
the page CI regenerates outside it. The fixture exists for exactly this
("a test about what a deployment gets rather than about what this lane
runs on") and is what the pin takes.

**The positional suppresses Click's own default.** With a real string
default, Click prints `[default: domain]` beside a help sentence that
already ends in `(default: domain)`. `show_default=False` is the
`--mac` precedent, and `schema`'s `ENTITY` avoids the question only
because its default is `None`.

**The live lane's refusal table gained a sentence.** `reference extra`
was a usage error while the verb took no arguments; the word is a half
now, so the row that holds this command family to the whole of its
stderr moves to the registry's sentence, read from the constant. The
value it hands the command is the planted credential, because the
positional is where this command's own input can carry one, and both
cases over that table already assert the plant reaches neither stream,
neither the server's logs nor the exception chain.

**`test_cli_import_weight.py` gained a row.** That suite pins the
grammar's whole import inventory in both directions, so the new module
appearing on the CLI's path is a failure until it is named. It is named
with what it costs: `docgen`, `entities`, `loader`, `models`,
`textwrap` and annotated-types, all of which the grammar already paid
for.

**The refusal section renders its sentences as wrapped list items.**
Everything else on the page wraps, so the sentences do too, and the
forward assertion therefore compares each item with its line breaks
undone against the registry's sentence rather than searching the page
for a verbatim one-line copy. The comparison is still exact and still
in both directions.

### Discoveries

**A docstring's angle brackets reach the page as written.** The
onboarding section's prose and the discoverability refusal both contain
`/x/<key>/` outside a code span, which a browser rendering of the
markdown drops as an unknown tag. Left as it is deliberately: the fix is
either editing prose M1 wrote or escaping markup inside the shared
`paragraph`, neither of which this milestone is, and the committed file
is read as a file at least as often as it is rendered. Worth a follow-up
if the browser rendering is the surface that matters.

**The example file's coverage guard needed nothing.** `config.example.yaml`
gained a sentence in its header saying what it is and where the complete
contract is; `test_config_examples.py`, which insists the file mentions
every field, is unaffected.

### Verification

Run from `vinga-server/`, against the development Postgres on 5432, with
`PYTHONDONTWRITEBYTECODE=1` exported for everything run outside pytest.

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q` (serial): `5558 passed, 19 skipped in
  588.09s`.
- `uv run pytest tests/unit -q -n auto --dist loadfile`, which is how CI
  runs the lane, on the finished tree: `5558 passed, 19 skipped in
  85.69s`.
- `uv run pytest tests/integration -q`: `243 passed in 354.36s`, after
  the refusal row above; the run before it had that one row's two cases
  red, which is how the changed behavior was found.
- `python3 scripts/check_doc_links.py .` from the repository root:
  `checked 195 files, 0 failures`.
- The census manifest went stale, as the plan's risk list predicted: the
  new page, the two READMEs, the changelog, the example file's header and the
  workflow's new step quote command spellings. Regenerated with
  `uv run python -m tests.unit.test_command_spellings` after staging the
  documents, never by hand, in the same commit; and again with the live
  lane's refusal row, since the manifest records a file and a line.
  `uv run pytest tests/unit/test_command_spellings.py -q`: `48 passed`.
- The five generated artifacts are what their generators render:
  `domain-config.md` (regenerated with the preamble change),
  `server-config.md` (new), `cli.md` (regenerated for the positional),
  and `api-openapi.json` and `conversations-schema.md` untouched. Their
  freshness pins are green inside the unit lane above.
- Not verified locally: the CI step itself. It is the domain step with
  the other half named, and the command it runs was exercised by hand
  (`uv run vinga-server config reference server > ../docs/reference/server-config.md`
  is what produced the committed page), but the workflow lane has not
  run on this branch.
