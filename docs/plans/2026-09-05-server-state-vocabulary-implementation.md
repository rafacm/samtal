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

## M2: the sentence states, the client advises

### What was done

`config/entities.py`. `APPLY_NOTICE` and `BINDING_UNSERVED_NOTICE` lost
their command halves. The first says the write is stored and not yet
serving and that the running server goes on serving what it already has
until the stored configuration is installed on it; the second says the
binding is live at the device's next check and that the agent it names
arrives with the install that adds it. Neither names a command, and the
comment above the first records why: what a write is waiting at is a
fact of this server, and which command crosses that boundary is a fact
of a client's grammar. The other three sentences never named one and
are untouched.

`config/cli.py`. `INSTALLS` is the one home of the one command this
grammar has that crosses a boundary, and `REMEDIES` maps a boundary set
to what this client has to say about it: the two sets that have
something to run about, which are `{reload}` and
`{reload, check-in}`. `_announced` composes the server's sentence with
the client's line under it, and answers the sentence alone where the
set is not a key. `_acknowledged` and `_imported_entries` both go
through it, and `DIFF_INTRO` reads `INSTALLS` where it used to spell
the command out again. The import dedupe keys on the boundary set where
there is one and on the sentence where there is not.

`config/responses.py`. `AppliedEntry.notice`'s description, accurate
until this milestone, now describes a reader's sentence that names no
command and points a program at `applies`; `_one_outcome`'s docstring
and refusal text stop calling the notice the boundary its change takes
effect at, and the rule they enforce is unchanged.

The pins. The two that held the sentences to naming `vinga apply` and
`vinga diff` invert, and assert the boundaries are on `applies` instead.
`test_config_api_writes.py` gained the invariant that keeps the class
closed: no sentence this server composes contains the client program
word, over every notice rather than over the two that moved.
`test_config_cli_rendering.py` gained the five reading states as
rendering states, once through `BIND_DEVICE` and once through `IMPORT`
(a set this client knows is advised; a set with no command that crosses
it, an absent set, an empty set and a token this client cannot name
each print the sentence alone), plus the two dedupe cases: two entries
at one boundary advised once, and two entries from an older server
keeping both sentences.

Documents. `docs/reference/api-openapi.json` regenerated through
`vinga-server config openapi`, `docs/architecture/cli-guide.md`'s "A
write says what it did and when it takes effect" rewritten with a new
paragraph stating the practice, `vinga-server/README.md`'s transcript
and both prose passages moved onto the new output, `CHANGELOG.md`
gained the `Changed` entry and
`vinga-server/tests/unit/command-spellings.txt` regenerated last.
`docs/reference/cli.md` is byte-identical, as the plan expects.

### Deviations from the plan

Five, none of them changing what the milestone delivers.

**`DIFF_INTRO` is rebuilt from the table's command, not from its
sentences.** The plan says the head's gloss is rebuilt from the remedy
table so that the `{PROGRAM} apply` in it and the one in the new advice
are one string. The two renderings want different things from that
fact: the head glosses one token in a sentence about all three, and the
advice answers a whole set. So the shared structure is the fact under
both, `INSTALLS`, and the head and the two remedy sentences all read
it. What the plan claims the table ends is delivered exactly: the
command has one home in `cli.py`, and the head's bytes did not move.

**The table has two keys, not five.** The plan says the keys are the
boundary sets actually produced. Three of the five produced sets
(`{check-in}`, `{restart}`, `{store-boot}`) are crossed by a device
asking, a process starting and a server reading the store at boot, none
of which is a command of this grammar, so an entry for one would have
to invent advice this client cannot give. A set that is not a key
prints the server's sentence alone, which is the same arm the absent,
empty and unrecognized sets take, and one lookup decides all four.

**The respelling table's substitution is two entries amended rather
than one added.** Both sentences that lost a command are in the frozen
transcript, so both of the #371 entries' right-hand sides gained the
second line. The transcript itself is not recaptured, which is the
whole point of it.

**Three assertions outside the plan's list moved.**
`test_config_cli.py`'s pin that a write prints one line and not a
paragraph now names the two lines it prints, which is the pin doing its
job: it is the one that would have caught an accidental paragraph.
`test_config_diff_read.py` asserted the acknowledgement named
`vinga apply` beside the comparison's `reload`; the pair it is really
about is one vocabulary in two places, so it asserts the tokens now and
the module stopped importing `PROGRAM`.

**`_one_outcome`'s refusal text is not in the served document.** The
plan expects the OpenAPI document to regenerate for both it and the
description. It is a validator's message rather than a published
string, so the regeneration is one description, and the diff is one
line.

### Discoveries

**The census sees a file's text, so an interpolated command spelling is
invisible to it and the comment beside it is the tripwire.** The plan's
central claim is that moving the spelling to the client's side brings
it inside the command-spellings guard's reach. It does, but not through
the constant: `f"{PROGRAM} apply"` contains no program word to match,
and what the manifest records for this block is the comment two lines
above it, which spells `vinga apply` out. That is the merged precedent
rather than a new arrangement, and it is exactly how `DIFF_INTRO`'s
spelling was already held (the manifest's old `cli.py:3328` was that
comment, not the f-string beneath it). The guard is real: a verb rename
that left this block behind fails
`test_command_spellings.py` in the same checkout, which is the reach
#386 says the census does not have across the version boundary. It is
worth knowing that the tripwire is prose beside the code rather than
the code.

**Nothing else in the repository quoted either sentence.** Swept for
the distinctive phrases across every tracked Markdown and YAML file:
the two READMEs, the guide, the example fragments and the reference
pages carry none of them, so the two documents the plan names are the
whole of the documentation footprint.

**The follow-up issue is drafted, not filed.** This milestone ran with
no GitHub access by instruction, so class (a)'s five refusals are
written up as a draft for the coordinator to file. It carries the
mechanism the fix needs (a problem-type vocabulary on `Problem`, with
this milestone's shape as its precedent), the two facts #386's census
established about the `SERVER_PROGRAM` mitigation, and the table of the
five constants. Nothing committed here names an issue number for it.

### Open questions

None. The plan's questions were resolved in the plan and in its review
round, and building M2 reopened none of them.

### Verification

- `uv run ruff check .`: all checks passed.
- `uv run pytest tests/unit -q -n auto --dist loadfile`: 5721 passed,
  19 skipped, in 88s.
- `uv run pytest tests/integration -q`: 243 passed in 396s.
- The generated-document drift checks:
  `tests/unit/test_config_docgen.py` and
  `tests/unit/test_command_spellings.py`, 80 passed, with
  `docs/reference/api-openapi.json` regenerated through
  `vinga-server config openapi` and
  `vinga-server/tests/unit/command-spellings.txt` through
  `uv run python -m tests.unit.test_command_spellings`, last, after this
  document was added. `tests/unit/test_api_openapi.py` and
  `tests/unit/test_api_contract.py`, 148 passed, are what say the
  committed document moved deliberately and in one piece.
- `uv run python scripts/check_doc_links.py .`: checked 203 files, 0
  failures.
- Not verified locally: nothing on this milestone's path. No device, no
  image and no network is on it, and the one thing that cannot be
  checked from a checkout at all is the pairing the issue is about: an
  image built before this commit still carries its own bytes, which is
  what the plan says the guarantee is forward from here.
