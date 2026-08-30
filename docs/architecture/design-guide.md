# Design guide

How vinga's code is shaped inside the boundaries
[`product-promises.md`](product-promises.md) and
[`guidelines.md`](guidelines.md) draw. Those pages say what vinga
promises and where its edges are; this page says what a module looks
like once it is on the right side of one. Read it before splitting a
file, adding a layer, or naming a new interface.

The method is the one the 2026-08-14 architecture review applied
round after round, and its claim is narrow: complexity is what a
reader has to hold in their head to change one thing safely, and the
way to reduce it is to hide more behind each name, not to spread the
same knowledge over more names. A layer that forwards its arguments
adds a name and hides nothing, so it makes the system harder, not
easier, even though it looks like structure.

Every rule here is anchored in merged code and cites the issue and
pull request it came from, because a rule with no worked example is a
preference. The short form an agent carries into every session is the
design section of [`../../AGENTS.md`](../../AGENTS.md); this page is
where its terms are defined and its claims are shown.

## On this page

- [The vocabulary](#the-vocabulary): module, interface,
  implementation, depth, seam, adapter and locality, each defined
  against code in this repository rather than in the abstract.
- [The deletion test](#the-deletion-test): the one question that tells
  a layer apart from a pass-through, and what it does not ask.
- [The interface is the test
  surface](#the-interface-is-the-test-surface): why an underscore
  reach-in in a new test is a review flag, and which of the two things
  it is evidence of.
- [Worked examples](#worked-examples): four merged changes, each with
  the shape it had before, the shape it took, and the lens it teaches.
- [What this guide does not
  license](#what-this-guide-does-not-license): why depth is bought at
  plan time and not by rewriting working code, and what outranks this
  page.

## The vocabulary

**Module.** A file, or a package when its parts have separate reasons
to change. `runtime/prompt.py` is one module in one file;
`tools/mcp/` is one module in six, because a transport change and a
reload change are different changes arriving on different days. The
unit is a responsibility, never a line count. Line count is evidence
that a file holds more than one responsibility, and only evidence: it
is what makes the second responsibility easy to see, not what makes
it wrong.

**Interface and implementation.** The interface is everything a
caller must know: the names it calls, the types it passes, the order
it must call them in, and the failures it must handle. The
implementation is everything the module knows so that its callers do
not. Anything true of a module that its callers must also know is
part of its interface whether or not it is written down, which is why
an undocumented ordering rule is an interface, and a bad one.

**Depth.** Implementation divided by interface: how much a caller
gets for how little it has to know. `ToolSource`
(`vinga-server/vinga_server/tools/source.py`) is four methods, and
behind one of its implementations sit six modules of MCP session
management, reload and transport. That ratio is the thing to
maximize. Its opposite is the pass-through, whose interface is its
implementation restated.

**Seam.** A named crossing between two parts, stated as a type rather
than implied by a shared object both of them mutate. The canonical
one is `device/boundary.py`: `SessionInput` is a conversation runtime
as the device edge sees it, `DeviceOutput` is the device as a runtime
sees it, and each side's whole knowledge of the other is one protocol
it can read in a minute.

**Adapter.** A module whose job is to translate at a seam so that one
side stops speaking the other's vocabulary. An adapter is allowed to
be thin. It is not allowed to be a pass-through: `DeviceTools` in
`tools/source.py` is four short methods over `DeviceOutput`, and it
earns its place because after it the runtime asks the board's tools
the same question it asks the other two sources.

**Locality.** Every fact has one home, and everything that needs it
reads it from there. Two structures that must agree are one structure
with a bug pending, and the bug is filed on the day someone renames a
field in the one they are reading. The counterexample below is
exactly this failure.

**Composition root.** The one place that knows how the parts are
wired: `create_app` and the `Composition` dataclass in
`vinga-server/vinga_server/composition.py`. Wiring knowledge
collects there deliberately, so that no module below it has to know
what else exists. Before #142 (PRs #187 to #189, #196) the wiring was
thirteen untyped attributes hung on `app.state` plus a
seven-attribute bag beside them, which meant every reader recovered
the types by finding the function that had set them. A field list is
an interface; a bag of attributes is a scavenger hunt.

## The deletion test

Before adding a module, ask: if this module did not exist and its body
were inlined into its only caller, would the caller get harder to
read? If the honest answer is no, the module is a pass-through and
should not exist. The test is asked in both directions, which is what
makes it useful during a split as well as during an addition: a module
that survives deletion is one whose absence would put a decision back
in a place that does not own it.

**Example.** The onboarding and OTA split (#143, PRs #197 and #198)
produced `onboarding/unbound.py`, a small module centered on one
decision: `activation_for`, with the `Unbound` result type and the
`activation_object` rendering helper beside it. It passes the test
because deleting it would put the answer to "what does a device with
no agent receive" back where it came from, which was three files that
had to agree. Its size is not the point; the decision having exactly
one home is.

**Counterexample.** A `runtime/prompt_helpers.py` that wraps
`prompt.know_how` to pass its arguments through in a different order,
or a `config/cli_render.py` that exists only because `cli.py` is long.
Both add a name, hide nothing, and make a reader open two files where
one would have done. Length alone is a reason to look for the second
responsibility, never a reason to cut the file in half at the line
that felt tiring.

## The interface is the test surface

A test reaches for the same names a caller reaches for. When a test
needs a private attribute or an underscore-prefixed function to say
what it means, that is evidence about the design and not about the
test: either the module is missing an interface for something callers
legitimately need, or the test is pinning an implementation detail
that is free to change. In review, an underscore reach-in in a new
test is a flag, and the answer is one of those two, decided
explicitly.

**Example.** `FillerCache` in `runtime/filler_runner.py` is declared
as a protocol of three reads and no writes, and its own docstring
says why: a test that hands the runner two clips should not have to
build the server's cache to do it. The protocol is simultaneously the
narrowest thing the runner needs and the cheapest thing a test can
supply, and that is not a coincidence. It is what a well-chosen
interface looks like.

**Counterexample.** A test that sets `pipeline._know_how` directly to
check the prompt a session sends. It passes, and it pins a name no
caller uses. The caller-facing surface is right there: the model
provider is what receives the prompt, so `RecordingLlm` in
`tests/support/providers.py` keeps the system prompt of every round
it was asked for, and `tests/unit/test_session_prompt.py` asserts
against `llm.systems`, which is what the session actually sent.

One test in that file used to compare `llm.systems` with
`session.runtime._know_how.text`, with a stated reason, because its
claim is exactly that with nothing remembered the cached half is the
whole prompt. The #210 sweep found the reason did not hold: what the
half holds is `runtime.prompt.know_how`'s answer, which is a public
name and the one the activation itself calls, and that it was
assembled once rather than rebuilt is what `CountingServers.asked`
says, since rebuilding is what asks. The test compares against those
two now. The lesson is the rule's own, and worth keeping beside it: a
stated reason is the flag being answered rather than an exemption from
it, and an answer can turn out to be wrong later, which is what
re-asking the question across a whole suite is for.

The operator-facing read, `GET /runtime/agents/{name}/prompt`, is a
separate interface with a separate job. It previews what an agent
would be sent, so it can see neither a live session's cached half nor
what the provider received, and it is not the test surface for
either. Two questions, two interfaces: a surface built for a person
is not automatically the one a test should use.

## Worked examples

### Deepening in place: the prompt assembler

**Issue #122, PR #130**, extended by PRs #131 and #133.
`vinga-server/vinga_server/runtime/prompt.py`

Prompt text used to be glued together in exactly one place,
`tools.builtin.with_memory`, which appended remembered facts to a
persona string. That was fine while there was one thing to append.
Per-server guidance was the second (PR #130), shared fragments the
third (PR #131) and a connected server's own words the fourth
(PR #133), so the choice was between a second joiner beside the first
or one module that owns joining.

What landed is the second option, and the reason it is the good
pattern is not that a file was added. `know_how` and `with_memory`
return an `Assembled`: the text to send and the ordered `Block`s it
was made of, each with a provenance and a character count, produced
together by one pure function. Nothing else in the server joins
prompt text or counts it. Three surfaces read what it produces: the
pipeline sends the text to the model, the `prompt_assembled` event
carries the block sizes, and the inspection route behind
`vinga-server config agent preview <agent>` prints the blocks whole.

What the module guarantees is worth stating precisely, because the
tempting claim is larger than the truth. It does not make those three
surfaces agree, and they are not meant to: the event fires once per
activation and leaves memory out deliberately, since `llm_round`
already carries a round's numbers, while the route assembles a fresh
preview that reads memory as a new session would rather than
reporting the half a live session has cached. Nor does the module own
the two clocks. The pipeline caches the know-how half in
`_activate_agent` and appends memory per round; the API's
`_prompt_preview` builds both on the spot. What is centralized is the
rule: for the same inputs, the block order, the joining and the
accounting are computed in one place, so two surfaces that differ are
showing different moments rather than different arithmetic.

That is where the depth is. The interface is two functions and a
handful of frozen dataclasses; behind it sit the fixed block order
and the reasons for it, the provenance vocabulary, and the rule that
the prompt is the blocks joined by blank lines and nothing else, so
that a character reported against a block is a character the model
receives. That last rule is what makes the accounting exact, and it
is enforceable only because one module owns the joining.

Two details worth copying. The move was justified by a principle, not
by taste: prompt assembly fails the telephone-call test on
[`guidelines.md`](guidelines.md), so it is runtime code and not
configuration code, and that is why it lives under `runtime/`. And it
was proven behavior-preserving before anything new was added, by a
test that transcribed the old function and compared the two over an
empty prompt, a whitespace one and an indented one.

### Two encodings of one shape: the CLI's response predicates

**Issue #139, PR #175.** `vinga-server/vinga_server/config/cli.py`

The anti-pattern, and it is instructive because nothing about it
looked careless. `cli.py` decided whether a body it had been handed
could be read as a pending listing, a status entry, a reload result
or a prompt block by checking it against frozensets of field names
written out by hand: `PENDING_FIELDS`, `STATUS_FIELDS`,
`STATUS_STATES`, `PROMPT_BLOCK_FIELDS`, plus ten predicate functions
walking the body key by key. Each set was commented, each was
defensive on purpose, and each was a second encoding of a model the
API already declares in `config/responses.py`.

Nothing connected the two encodings. A field renamed on the model
left the CLI refusing every well-formed answer the server gave, and
neither file said so. The shape is shallow twice over: the frozensets
hide nothing (they restate a model that exists), and they impose the
knowledge on their reader anyway, who now has to check both files to
learn what an answer is.

The intermediate step is worth naming, because it is what an honest
codebase does while waiting for the real fix. A test file,
`tests/unit/test_config_cli_shapes.py`, was written as a bridge
between the two encodings, stating the relation each pair actually
held, with a docstring that said it existed to be deleted wholesale
by #139. A pin on a duplication is better than an unpinned
duplication, and it is not a solution: it makes the drift loud
instead of removing the possibility of drift.

The fix removed the possibility. One helper,
`_understood(shape, answer, refusal)`, reads an answer as the response
model the route promised, strictly, dropping unknown fields so a newer
server stays readable and refusing anything that is not that shape. The
frozensets, the ten predicates and the bridge test went in the same
commit. The derived fact survived, as `outcomes`, read off the result
model's own fields, which is the same lesson in its positive form: the
fact stayed, and it stopped being written twice. It lives in `cli.py`
now, beside the renderer that is its only caller (#242).

### A seam is what a part asks of its parent

**Issue #141, PRs #184 and #186.**
`vinga-server/vinga_server/runtime/turntaking.py`,
`runtime/filler_runner.py`

`runtime/pipeline.py` was 1,820 lines and one class of 47 methods
holding around ten responsibilities, tied together by mutable fields
that several of them read and several of them wrote. The interesting
question in a split like that is not which methods look related. It
is which cluster can state, as a handful of signatures, what it needs
back from the part it is leaving.

Turn-taking could: `ReplyControl` is four members (is a reply in
flight, start one, cancel one, confirm a transcript), and the
pipeline satisfies it structurally and passes itself. The filler
runner could state even less: `TurnView` is two reads and no writes,
how much of what was fed the endpointer counted as speech, and
whether outgoing frames are paused for a barge-in confirmation. Two
reads and no writes is a proof, not a convenience. It says in the
type system that the filler never owned the floor, which is the fact
that made the extraction safe.

The lens: a field soup is not converted into a seam by moving code.
It becomes a seam when the crossing can be written down, and if it
cannot be written down in a few signatures, the cluster is not the
one to extract yet. `PipelineRuntime` came out of those two PRs at
1,484 lines and 38 methods, which is the smallest thing that
happened.

### One question, three answers: the tool sources

**Issue #140, PRs #178 to #181.**
`vinga-server/vinga_server/tools/source.py`

The runtime knew its three tool origins by heart. Builtin tools,
device tools and MCP tools each had their own way of being listed,
their own way of being called, their own ownership check, and a
timeout that forked on where the call had come from: four calling
conventions, spelled out at every site that touched tools.

The duplication was never the code. It was that the pipeline had to
know which of three worlds a tool name belonged to before it could do
anything with it. `ToolSource` names the shared question in four
members: `snapshot(agent)`, `owns(claim)`, `dispatch(claim, agent)`,
`timeout_for(claim)`. `BuiltinTools`, `DeviceTools` and `McpTools`
answer it, and the pipeline holds one tuple of sources and loops over
it in three places. A fourth origin is a fourth implementation and no
change at the call sites.

Note what the interface refuses to promise, because that is the part
that is easy to get wrong. `owns` is about names and not outcomes: a
source owns a name it publishes even when it cannot run it, and says
so itself in `dispatch`. Had `owns` meant "can run right now", every
caller would have needed the fallback logic back again, and the
interface would have been four members wide and zero deep.

## What this guide does not license

It is not an argument for rewriting working code. Depth is bought at
the cost of a change that has to be proven behavior-preserving, which
is why every example above pinned the old behavior before moving it,
and the cheapest moment to buy it is at plan time, before the shallow
version exists. That is where the process asks for it: a plan names
the modules each milestone deepens and the seams it adds, and the
external plan review applies the deletion test to every new module a
plan proposes.

And it never outranks
[`product-promises.md`](product-promises.md). A deep module
that breaks a product promise is a deep module that is wrong. When
this page and that one pull in different directions, that one wins.
