# vinga documentation

Two kinds of page live here, and the difference is the first thing to
navigate by. **User-facing documentation** describes vinga to somebody
running it: the READMEs, the device guides, and the reference section
below. **Working notes** are how this project thinks and what it
decided: the research notes, the architecture pages, and the records.

## Start here

- [**The project overview**](../README.md): what vinga is, the hardware
  it targets, and the seven-step path from a container to a board that
  answers.
- [**vinga-server**](../vinga-server/README.md): the server in full.
  Every provider option, the two halves of the configuration, the
  security defaults, running it in a container, and onboarding a device.
- [**vinga-esp32**](../vinga-esp32/README.md): the thin firmware
  customization and the boards it targets.
- [**devices/**](devices/README.md): the per-board guides, described
  under [Device guides](#device-guides) below.

## Reference

What each thing means and what each command does, for somebody
configuring a deployment. Everything under [`reference/`](reference/)
that says it is generated is rendered from the code and diffed by CI, so
it cannot come to describe a server this repository does not build.

- [**reference/cli.md**](reference/cli.md): the configuration CLI.
  Installing it (a client on a workstation, a checkout, the image), the
  two spellings, reaching a deployment and the token it carries, writing
  a whole deployment in one document, rebuilding one whose server will
  not boot, and every command's own help page.
- [**reference/domain-config.md**](reference/domain-config.md): every
  field of the domain half, generated from the models.
- [**reference/api-openapi.json**](reference/api-openapi.json): the
  configuration API's contract, generated from the routes. It is what an
  install carrying the client alone reads instead of asking a server.
- [**reference/events.md**](reference/events.md): the structured events,
  which are this server's observability surface, generated from the
  declarations.
- [**reference/conversations-schema.md**](reference/conversations-schema.md):
  the conversation store's tables, generated from the metadata.
- [**concepts.md**](concepts.md): the domain model from the user's
  point of view: device, agent, binding, conversation, session, and
  the decided semantics that connect them (wake word, switching,
  memory, meta capabilities, the help agent). Says which parts are
  implemented and which are decided direction.
- [**glossary.md**](glossary.md): the concepts, techniques, and
  technologies the project is built on, one short definition each with
  pointers for going deeper.

## Device guides

[**devices/**](devices/README.md) holds one user-facing guide per board
vinga targets, describing the hardware in front of the user: which
button starts and stops a conversation, how long to hold PWR to power
off, whether a wake word is enabled and which word it is, the commands
the device answers by voice, what the display shows, and the board's
known quirks. Its common page carries what every board running the
upstream firmware shares, so a guide covers only what is specific to
its board. Only the Touch-LCD-1.54 guide is written in full; the other
two are stubs marked 🚧 that grow as those boards reach working status.
Each section says whether its facts are read from the upstream board
support code, verified in hands-on use, or not verified at all. These
guides are also the knowledge source the planned built-in help agent
reads to explain the device it is speaking through, which is why they
are reviewable markdown rather than prompt text.

## Architecture

Where the boundaries are, and what a change is held to. Issues hold
evidence, ADRs hold decisions, plans hold execution, and these pages
hold direction and standard.

- [**architecture/principles.md**](architecture/principles.md): the
  standing fundamentals: vinga's identity, its product promises, and
  the architecture principles that keep them, each with an example and
  a counterexample. Promises take precedence over architecture. Read
  this before designing a feature or deciding direction.
- [**architecture/design-guide.md**](architecture/design-guide.md): what
  a module looks like once it is on the right side of one of those
  boundaries. The vocabulary (module, interface, seam, adapter, depth,
  locality) defined against this codebase, the deletion test, the rule
  that a test reaches the interface, and four merged changes worked
  through: what each one's shape was before, what it became, and the
  lens it teaches. Its short form is the design section of
  [`../AGENTS.md`](../AGENTS.md).
- [**architecture/cli-guide.md**](architecture/cli-guide.md): what a
  command looks like, and what a reviewer holds a new one to. Read it
  before adding a command, a noun, a verb or a flag;
  [`reference/cli.md`](reference/cli.md) is the other half of the pair
  and says what the grammar currently is.
- [**architecture/observability-surfaces.md**](architecture/observability-surfaces.md):
  which needs the design balances, the four surfaces and what each may
  carry, and the external practice it was checked against. The reasoning
  behind the ADR that separates content from telemetry.
- [**architecture/pipeline-ownership.md**](architecture/pipeline-ownership.md):
  which parts of the conversation pipeline are shared shape that any
  streaming voice framework provides, and which parts are vinga's own
  semantics that none of them has.

### Diagrams

[**architecture/**](architecture/README.md) embeds the hand-drawn diagrams and walks through them: the high-level overview the root README leads with, and a step-by-step teaching tour of one conversation turn, from wake word to spoken reply, that explains each concept and the problem it solves before using its acronym. A directory per authoring tool sits under it. `architecture/excalidraw/` holds those two, whose editable originals are scenes of the same names in the team workspace and whose committed files are exports kept in sync by hand, so flag them when a pipeline change makes them stale. `architecture/plantuml/` holds three whose source is text here and whose renders come from a command, so they cannot drift unnoticed: what leaves the host, the ordering inside one turn, and the barge-in decision.

## Research notes

- [**xiaozhi-notes.md**](xiaozhi-notes.md): the upstream architecture, the
  device↔server protocol, ports, configuration keys, and the validated
  end-to-end demo procedure. Read this first for anything protocol-related,
  and for every serial gotcha a board has.
- [**related-projects.md**](related-projects.md): the neighbouring voice
  assistant and agent projects, and the projects vinga is built from.
  For an alternative: what it is, where it overlaps, where vinga is
  deliberately different, and what vinga borrows. For a dependency: what
  it is and why vinga touches it, with the license terms left in
  [`THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md). Entries are
  added as a project is actually read, never assumed.
- [**conversational-quality-regression-suite.md**](conversational-quality-regression-suite.md):
  why field tests exist, the shape of a conversation turn, what a test
  round needs before anyone leaves the desk and what it yields, and the
  three layers findings age in (instrument, interaction, calibration).
  The starting point for setting up and analyzing a field-test round.

## The record

What was decided, what was planned, and what each change did. These are
working notes: they report what was true when they were written, and
they are not rewritten when the code moves on.

**Plans.** One file per accepted plan under [`plans/`](plans/), named
with a `YYYY-MM-DD-` prefix, each with a companion `-implementation` doc
recording deviations, resolved open questions, and discoveries. A plan's
milestone checklist doubles as its milestone descriptions: each ticked
item links to its implementation-doc section, so a fresh session can
resume from the repository alone. The first of them is
[**vinga-server v1**](plans/2026-08-02-samtal-server-v1.md) ·
[implementation notes](plans/2026-08-02-samtal-server-v1-implementation.md),
architecture and milestones M0 to M7, from package skeleton to a
published container image.

**Features.** [`features/`](features/) holds a doc per significant change
made outside any active plan, same date-prefix naming, covering Problem,
Changes, Key parameters, Verification, and Files modified. Milestone work
under a plan is documented by that plan's implementation doc and its pull
request instead.

**Decisions.** [`adr/`](adr/README.md) holds one architecture decision
record per decision that is hard to reverse, surprising without context,
and the result of a real trade-off. Records are immutable and
date-prefixed; conventions are in [`adr/README.md`](adr/README.md).

## Conventions

Documentation process, writing conventions, and the workflow these documents
follow are defined in [`../AGENTS.md`](../AGENTS.md).
