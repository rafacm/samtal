# Architecture

Where vinga's boundaries are, and what a change is held to. Issues
hold evidence, ADRs hold decisions, plans hold execution, and these
pages hold direction and standard. What each class of page in the
repository may claim is stated once, in
[`../README.md`](../README.md).

This page is the index for the corpus, and it is organized by the
question you arrived with rather than by filename.

## Designing a feature or deciding direction

- [**product-promises.md**](product-promises.md): the three standing
  commitments to the person running vinga, each falsifiable from
  outside. The stock-firmware compatibility floor, the first-class
  local deployment, and the database promise. They take precedence
  over everything else in this corpus, so read them before a
  direction is chosen and a boundary is never crossed one
  reasonable-looking pull request at a time.
- [**guidelines.md**](guidelines.md): vinga's identity and the
  revisable defaults that keep those promises, each with an example
  and a counterexample. Thin device and smart server, where the
  hardware edge is normalized and why runtimes stay siblings, whose
  reason a decision carries, and which half of the pipeline a
  framework could own, with the conditions that reopen that last
  question.
- [**../adr/**](../adr/README.md): one record per decision that was
  hard to reverse, surprising without context, and the result of a
  real trade-off. When a promise or a guideline cites a decision, this
  is where its reasoning is; records are immutable and date-prefixed.

The first two used to be one page, and dated records cite it by name.
[`principles.md`](principles.md) is now a signpost to them and holds
nothing of its own; `pipeline-ownership.md` is gone, its durable
inventory being the last guideline.

## Splitting a file, adding a layer, or naming an interface

- [**design-guide.md**](design-guide.md): what a module looks like
  once it is on the right side of one of those boundaries. The
  vocabulary (module, interface, seam, adapter, depth, locality)
  defined against this codebase, the deletion test, the rule that a
  test reaches the interface, and four merged changes worked through:
  what each one's shape was before, what it became, and the lens it
  teaches. Its short form is the design section of
  [`../../AGENTS.md`](../../AGENTS.md).

## Adding a command, a noun, a verb or a flag

- [**cli-guide.md**](cli-guide.md): what a reviewer holds a new
  command to, as sixteen questions at the top of the page, each linked
  to the rule behind it. Everything after that checklist is the
  reasoning: the noun-verb grammar and why it is that way, and each
  practice with an example and a counterexample.
- [**../reference/cli.md**](../reference/cli.md): the other half of
  that pair, and a different question. Half generated from the
  command tree, so it says what the grammar currently *is* and cannot
  describe a CLI this repository does not build. It is where a
  current spelling is looked up; the guide links it rather than
  restating one.
- [**cli-guide-audit.md**](cli-guide-audit.md): the evidence behind
  the guide, dated. Four published CLI guides walked one guideline at
  a time on 2026-08-24, every guideline with a disposition. A
  research record, not a standard: where it and the guide disagree
  about today, the guide is right.

## Placing a datum: where may this go?

- [**observability-surfaces.md**](observability-surfaces.md): the
  current map, four surfaces to a table. Structured events, the
  conversation store and capture, all three landed, and a future
  audit surface: what each may carry, how long it is kept, who may
  read it, and where it stands in the code today. Then the four
  invariants a new field is placed by. The 2026-08-15 needs
  assessment and external survey are a dated appendix at the foot,
  and
  [the 2026-08-15 ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)
  holds the decision itself.
- [**../reference/events.md**](../reference/events.md) and
  [**../reference/conversations-schema.md**](../reference/conversations-schema.md):
  the exact vocabulary each of the two landed surfaces has, generated
  from the declarations and the metadata.

## Understanding a conversation end to end

- [**../system-overview.md**](../system-overview.md): one turn from
  the wake word to the spoken reply, explained step by step with the
  diagrams, each concept introduced before its acronym is used.
- [**../concepts.md**](../concepts.md): the nouns the overview moves
  audio between, and what they mean to a user. Device, agent,
  binding, conversation and session, and the semantics decided on
  purpose. It is ahead of the code and says where: every section
  carries its status, and a decided direction names the issue that
  owns it, or says plainly that none does yet.
- [**diagrams/**](diagrams/README.md): all five diagrams, indexed by
  the question each answers, with the rendering and synchronization
  instructions beside the files they apply to.
- [**../xiaozhi-notes.md**](../xiaozhi-notes.md): the device to server
  protocol key by key, and the upstream projects it came from. What the
  overview calls a `hello` exchange, this describes on the wire. Its
  protocol sections are maintained and say which upstream commits and
  which observed firmware versions they were last read against; its
  dated observations and its reading of the upstream server say that
  too, and are not corrected as upstream moves.
