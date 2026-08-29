# vinga info, and a CLI polish round: implementation

The companion to
[`2026-08-29-vinga-info-cli-polish.md`](2026-08-29-vinga-info-cli-polish.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations
says so explicitly.

## M1: `vinga info`

PR TBD.

### What landed

In the order the commits tell it: the route, then the row, then the
generated documents, then the tests, then the pages the change
falsified.

- **`RuntimeInfo` in `config/responses.py`**, strict like every model
  there, with five fields: `version`, `revision`, `onboarding_enabled`,
  and the nullable `onboarding_url` and `onboarding_provenance`. Its
  docstring carries the security argument rather than leaving it in a
  plan, because the model is what a client generator reads.
- **`GET /runtime/info`**, first route of the `_runtime` namespace,
  answering from `ApiRuntime.identity` with `Cache-Control: no-store`
  on the way back and `_problems(401, 503)` in the document. The 503
  has a description of its own (`api_descriptions/no-runtime-info.md`),
  because the shared sentence says the reads in this namespace answer
  emptily and there is no honest empty identity, which is the prompt
  read's and the diff's argument met a third time.
- **`api.py` imports nothing new from `onboarding`.** The identity
  reaches it as a value on `ApiRuntime`, filled by the composition root
  (`app.py:_runtime_identity`), which already sits downstream of
  `onboarding.origin`. `test_onboarding_import_weight.py` is untouched
  and green.
- **`build_api` and `build_api_runtime` gained `identity` as a
  trailing defaulted parameter**, so every existing positional call
  still means what it meant.
- **The `info` row**, flat, first of the flat verbs, two acts in order
  (`IDENTITY` on the new route, then `COUNTS`, which is `GET /config`
  rendered as a count per kind). The kinds and their order come from
  `entities.ENTITIES`: a kind addressed by no segment is the singleton
  and has no count, one addressed by two is nested a level deeper, and
  the devices and the default agent are written out for the reason
  `_summary` writes them out.
- **The pages this falsified**: the cli-guide's flat-verbs section and
  its credential practice, the root README's step 5, and the paragraph
  in `vinga-server/README.md` that said `ota-url` was the one place the
  URL is printed.

### Deviations from the plan

Two, both small, both in the same direction: the plan specified what to
print and this milestone had to decide how.

1. **`Command` gained one field, `opens`.** The plan says the row runs
   two acts and prints the contacted address before either, and says
   `conversation show` proves no new machinery is needed for the two
   acts. That is true of the acts and not of the line in front of them:
   an act's renderer is handed the answer and nothing else, which is
   what keeps a rendering a function of what came back, and the address
   is a fact of the invocation that no answer carries. Rather than give
   an act the invocation (the `Act` interface change M3 owns) or fold
   the print into a `declare` function (whose job is the argument
   shape, one per shape rather than one per command), `Command` carries
   an optional callable that runs before the first act. One row uses
   it. `Command.acts()` is unchanged, so the API contract test still
   enumerates coverage from the row.

2. **The two long values are bounded at their own length, not at
   `GLIMPSE_LENGTH`.** `printable`'s default bound is 120 characters,
   which is right for a glimpse of far-side text quoted inside a
   sentence and wrong for both values here: the guessed-origin
   provenance `onboarding.origin` composes runs to 191 characters and
   was truncated mid-clause in the live lane, losing the fix it ends
   with, and a long origin would have truncated the URL itself, which
   is the one value in this output an operator retypes by hand. So
   `IDENTITY_LENGTH = 512` is passed for those two. They are still
   bounded, and for the two reasons everything an answer carries is
   bounded: no answer chooses how long a command's output is, and
   nothing an answer carries steers a terminal.

### Discoveries

- **The census was already stale on the plan branch.** `docs/plans/
  2026-08-29-vinga-info-cli-polish.md` landed without regenerating
  `tests/unit/command-spellings.txt`, so `test_the_manifest_is_the_census`
  was failing before this milestone began. The regeneration in this
  milestone's last documentation commit clears it along with its own
  additions.
- **A generated document carries one spelling, and a response model's
  description is a generated document.** The first draft of
  `RuntimeInfo.onboarding_url` said `vinga-server config ota-url`,
  which is the spelling source files use and the one
  `test_a_generated_document_carries_one_spelling` refuses in
  `api-openapi.json`. The canonical spelling in anything rendered is
  `vinga <verb>`. Worth knowing before writing prose into a model.
- **The lane's own onboarding URL is a guess, and that is the honest
  answer.** `tests/support/deployment.py` boots on an ephemeral port
  while the configuration's `server.port` is the default, so
  `public_origin` guesses from the listen address and says so at
  length. The live-lane case asserts the whole provenance sentence
  including its fix, which is what caught the truncation above.
- **The onboarding key is exactly eight characters**, in an alphabet
  with no `0`/`O` and no `1`/`I`/`l`. A sentinel test that hunts for
  the key segment on its own has to allow for that rather than assume a
  long token.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q`: green.
- `uv run pytest tests/integration -q`: green against the Postgres this
  machine was already running with the compose defaults.
- The wheel lane's `info` case is run by the same integration command;
  the image lane is CI's.
- Not verified here: nothing on hardware. M1 adds no board or device
  procedure, and no page under `docs/devices/` speaks about the API.
