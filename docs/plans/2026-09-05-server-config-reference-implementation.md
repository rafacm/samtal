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
