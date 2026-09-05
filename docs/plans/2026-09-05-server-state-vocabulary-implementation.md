# State the boundary, and let the client name the command: implementation

Companion to
[`2026-09-05-server-state-vocabulary.md`](2026-09-05-server-state-vocabulary.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the boundary an acknowledgement announces, on the wire

### What was done

`config/responses.py`. `Applies` gained `STORE_BOOT = "store-boot"` and
its docstring says four boundaries and no fifth, with a paragraph on why
a comparison announces three of them. `DiffApplies =
Literal[Applies.RESTART, Applies.RELOAD, Applies.CHECK_IN]` sits under
the enum with the reasoning and the rendering consequence, and the seven
`applies: Applies` annotations of the comparison narrowed to it.
`Acknowledgement` and `AppliedEntry` each gained `applies:
tuple[Applies, ...]` defaulted to `()`, with the description saying why
the default is this API's one exception to nullable-and-required and how
an unrecognised token is to be read. `Acknowledgement.notice`'s own
description, stale since the verb rename on two counts, was rewritten to
describe what the server sends today and to point a program at the field
beside it.

`config/entities.py`. A frozen `Notice` dataclass carrying `applies` and
`sentence`, and the five notice constants became five of them:
`RESTART_NOTICE` (`restart`), `BINDING_NOTICE` (`check-in`),
`APPLY_NOTICE` (`reload`), `BINDING_UNSERVED_NOTICE` (`reload`,
`check-in`) and `SNAPSHOT_NOTICE` (`store-boot`). `EntityDescriptor`'s
`notice` is one of those, the module docstring records the new
`responses` edge and why it is safe, and `Notice` joins `__all__`. No
sentence moved: the five are byte-identical to what they were, checked
against the committed file by evaluating both and comparing.

`config/api.py`. `_acknowledge` and `_applied` write both halves onto
the answer, `_binding_notice` and `_applied_notice` answer a `Notice`,
`_SECTION_NOTICE` maps a section to one, and the five device and
default-agent handlers widened their return annotations from
`dict[str, str]` to `dict[str, Any]`. An entry that wrote nothing
carries a null sentence and an empty set, which is the same fact twice
and is what `_one_outcome` already refuses a disagreement about.

`config/cli.py`. `_declared`'s tuple branch gained the read-whole-or-not-
at-all rule for a sequence of a closed token, stated about the shape and
not about a field name. A member this client cannot resolve to an enum
member makes the whole sequence unreadable, which the branch answers
with a module-private sentinel; the model branch drops any key whose
value came back as that sentinel, so the field takes the model's own
default, which is exactly the key an older server never sent. The scalar
enum branch is untouched, so no existing field's reading moved.

`tests/support/notices.py`. The four token names became `Applies`
members and the `_ANNOUNCED_BY` phrase table is gone. `boundaries()`
reads `applies` off a body, and matches what a command printed against
the five sentences `entities` composes, taking each matched sentence's
own `applies`. Every downstream assertion kept its token set; what
changed is the argument at the sixteen body-side call sites, which now
pass the whole body.

The pins. `test_config_diff.py` gained the closed-set equality pin
(`set(get_args(DiffApplies)) | {Applies.STORE_BOOT} == set(Applies)`), a
completeness pin deriving from `responses` the set of models that carry
a narrowed boundary, and a per-model case constructing each of them with
`STORE_BOOT` and asserting it raises, then with each `DiffApplies`
member and asserting it is accepted. `test_config_api_writes.py` gained
the producer-side pin: five notices, each announcing at least one
boundary and none outside the vocabulary. `test_config_cli_rendering.py`
gained the four reading states driven through `Act.read()`, once through
`BIND_DEVICE` (`Acknowledgement`) and once through `IMPORT`
(`AppliedDocument`), plus a case asserting an unknown token reaches
neither stream.

Documents. `docs/reference/api-openapi.json` regenerated through
`vinga-server config openapi`, and `vinga-server/tests/unit/command-spellings.txt`
through its own module, last. `CHANGELOG.md` gained the `Added` entry.
`docs/reference/cli.md` and `docs/reference/domain-config.md` are
byte-identical, as the plan expects.

### Deviations from the plan

Nine, all small, and none changing what the milestone delivers.

**`boundaries()` reads a body's field and matches printed output against
the sentences.** The plan says the module "reads the field instead of
the prose", and half its callers hand it what a command printed to
stderr, where there is no field to read. So it takes either. The prose
arm no longer holds a table of phrases beside the real pairing, which is
what the plan objected to: it matches the five sentences `entities`
composes and takes each one's declared boundaries, so there is still
exactly one encoding and a prose edit that kept a sentence in the
registry keeps every suite green.

**The default is reached by dropping the key, not by substituting a
value.** `_declared` walks values and cannot see a field's default; only
the model above one can. The tuple branch therefore answers with a
sentinel and the model branch drops that key, which is literally the
body an older server sends. The rule the plan states is unchanged.

**Two import allow lists and one comparison helper moved.**
`tests/support/isolation.py` and `test_config_entities.py` each gained
`vinga_server.config.responses`, which is the new edge the plan
sanctions, each with the note saying why it is safe; the `heavy`
assertions beside them are still empty. The registry's fact comparison
learned to serialize a dataclass field by field, since a notice is now
one. `test_cli_import_weight.py` needed nothing: `responses` was already
in `CLI_REACH`.

**Two body-shape assertions widened.** A write's answer carries three
keys now, so `test_config_api_writes.py` and `test_config_round_trip.py`
say so. The plan does not name them; they are the additive wire change
arriving where a test counted keys.

**One OpenAPI test had to be inverted.** The plan calls
`Acknowledgement.notice`'s description stale but does not name what held
it there: `test_api_openapi.py::test_the_acknowledgement_notice_names_two_boundaries_and_no_start`
asserted `/runtime/config/reload` was *in* it. It now asserts the route
is absent, that the description points at `applies`, and that the two
boundaries an operator waits on are still described.

**The narrowing pins live in `test_config_diff.py`.** The plan does not
place them. They are claims about what a comparison can announce, and
that module already imports `Applies` and holds the completeness pin for
the kinds.

**Eight models carry a narrowed boundary, not seven.** The plan counts
the seven annotations; `AgentsDiff` extends `EntityDiff` and inherits an
eighth field. The pin derives its set from the module rather than
listing it, so the count is checked rather than asserted, and a ninth
arrives with the completeness pin failing.

**Five handler annotations widened.** `applies` is a tuple, so
`_acknowledge` answers `dict[str, Any]` and the five handlers that
declared `dict[str, str]` follow. Nothing about the responses changes;
the annotation was describing the dictionary, not the contract, which is
`response_model=Acknowledgement`.

**An unchanged entry's `applies` is `()` rather than null.** The plan
does not say, and the field's default settles it: a second nullable
field would offer the same disagreement `_one_outcome` exists to refuse,
in a shape a client would have to tell apart from an older server's
silence.

### Discoveries

**A model's docstring is published, so an internal alias name in it
leaks into the served contract.** The first draft of the `Applies`
docstring ended "which is why `DiffApplies` below leaves it out", and
that sentence went into `api-openapi.json`, where the name means nothing
and "below" refers to nothing. It says "the comparison read never
reports this one and its own fields carry the other three" instead.

**The inlining is exactly the size the plan predicted.** The regenerated
document is 78 lines added and 11 removed: the two new fields, the
fourth enum member, two rewritten descriptions, and the eight
comparison fields trading a `$ref` for a three-value enum each. The
`Applies` component survives, now reached only through the two new
fields.

**The new branch's blast radius is exactly the two fields, measured
rather than argued.** Walking every model in `responses` for a field
annotated `tuple[<Enum>, ...]` finds `Acknowledgement.applies` and
`AppliedEntry.applies` and nothing else, which is the bound the plan's
risk section claims. A required field whose value the rule could not
read would be dropped and then refused as missing, which is the same
refusal by a different sentence; no such field exists.

**Nothing this milestone touched is printed.** The proof is three
things, none of which needed editing to stay green: the frozen
pre-rename transcript and its substitution table in
`test_config_cli_respelling.py`; every stream assertion in
`test_config_cli.py` and `test_config_cli_rendering.py`, whose expected
bytes are unchanged; and the five sentences themselves, compared
byte-for-byte against the committed constants by evaluating both.
`docs/reference/cli.md` is byte-identical, which its own freshness pin
asserts.

### Open questions

None. The plan's questions were resolved in the plan and in its review
round, and building M1 did not reopen any. The one thing worth recording
for M2 is that the tolerance rule was verified against pydantic before
and after writing: a missing key and an explicit `[]` both validate to
the default under `strict=True`, and a tuple carrying one unrecognized
member raises `ValidationError`, which is the refusal the new branch
intercepts.

### Verification

- `uv run ruff check .`: all checks passed.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5704 passed,
  19 skipped, in 87s.
- `uv run pytest tests/integration -q`: 243 passed in 404s.
- The generated-document drift checks:
  `tests/unit/test_config_docgen.py` and
  `tests/unit/test_command_spellings.py`, 80 passed, with
  `docs/reference/api-openapi.json` regenerated through
  `vinga-server config openapi` and
  `vinga-server/tests/unit/command-spellings.txt` through
  `uv run python -m tests.unit.test_command_spellings`, last, after this
  document was added.
- `uv run python scripts/check_doc_links.py .`: checked 203 files, 0
  failures.
- Not verified locally: nothing. No device, no image and no network is
  on this milestone's path.
