# Architecture

Where vinga's boundaries are, and what a change is held to. Issues
hold evidence, ADRs hold decisions, plans hold execution, and these
pages hold direction and standard. What each class of page in the
repository may claim is stated once, in
[`../README.md`](../README.md).

This page is the index for the corpus, and it is organized by the
question you arrived with rather than by filename.

## Designing a feature or deciding direction

- [**principles.md**](principles.md): the standing fundamentals.
  vinga's identity, its product promises, and the architecture
  principles that keep them, each with an example and a
  counterexample. Promises take precedence over architecture, and the
  page is meant to be read before a direction is chosen, so a
  boundary is never crossed one reasonable-looking pull request at a
  time.
- [**pipeline-ownership.md**](pipeline-ownership.md): which parts of
  the conversation pipeline are shared shape, the thing any streaming
  voice framework provides, and which parts are vinga's own semantics
  that none of them has. The inventory a framework question is
  answered against, with the conditions that reopen it.
- [**../adr/**](../adr/README.md): one record per decision that was
  hard to reverse, surprising without context, and the result of a
  real trade-off. When a principle cites a decision, this is where
  its reasoning is; records are immutable and date-prefixed.

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

- [**cli-guide.md**](cli-guide.md): what a command looks like, and
  what a reviewer holds a new one to. The noun-verb grammar and why
  it is that way, and each practice with an example and a
  counterexample.
- [**../reference/cli.md**](../reference/cli.md): the other half of
  that pair, and a different question. Half generated from the
  command tree, so it says what the grammar currently *is* and cannot
  describe a CLI this repository does not build.

## Placing a datum: where may this go?

- [**observability-surfaces.md**](observability-surfaces.md): the
  four surfaces (structured events, the conversation store, capture,
  and a future audit surface), what each may carry, which needs the
  split serves, and the external practice it was checked against. The
  reasoning behind
  [the 2026-08-15 ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md),
  which holds the decision itself.
- [**../reference/events.md**](../reference/events.md) and
  [**../reference/conversations-schema.md**](../reference/conversations-schema.md):
  the exact vocabulary each of the two landed surfaces has, generated
  from the declarations and the metadata.

## Understanding a conversation end to end

- [**../system-overview.md**](../system-overview.md): one turn from
  the wake word to the spoken reply, explained step by step with the
  diagrams, each concept introduced before its acronym is used.
- [**diagrams/**](diagrams/README.md): all five diagrams, indexed by
  the question each answers, with the rendering and synchronization
  instructions beside the files they apply to.
- [**../xiaozhi-notes.md**](../xiaozhi-notes.md): the upstream
  architecture and the device to server protocol key by key. What the
  overview calls a `hello` exchange, this describes on the wire.
