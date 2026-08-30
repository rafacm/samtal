# vinga documentation

Two questions place a page. **What may it claim?** is authority, and
the seven classes below answer it. **Who is it for?** is audience,
and it decides only where a page is listed and how it is written:
**user-facing documentation** describes vinga to somebody running it
(the READMEs, the device guides, and the reference section below),
**working notes** are how this project thinks and what it decided
(the research notes, the architecture pages, and the record).
Audience never settles authority. A working note does not outrank a
user-facing page by being a working note, and a page is not
authoritative for being written for the person running vinga.

## Authority

Seven classes, covering every page under `docs/`, the three READMEs,
[`../AGENTS.md`](../AGENTS.md) and the changelog. The set is closed:
a new page joins one of these classes, or this list changes in the
commit that adds it. Three directories hold one class each and are
classified as directories; every other page is classified here rather
than by claiming a rank for itself.

**Product promises** are commitments to the person running vinga,
falsifiable from outside. Breaking one does not refactor vinga, it
changes what vinga is, and they outrank every other class here. Today
they are the three in
[`architecture/product-promises.md`](architecture/product-promises.md).

**Guidelines** are how the code keeps those promises. Any of them can
be revised given new evidence, provided the promises still hold:
[`architecture/guidelines.md`](architecture/guidelines.md),
[`architecture/design-guide.md`](architecture/design-guide.md),
[`architecture/cli-guide.md`](architecture/cli-guide.md), and
[`../AGENTS.md`](../AGENTS.md).

**Maintained maps and explanations** describe the system as it is now
and are corrected when it moves:
[`system-overview.md`](system-overview.md),
[`concepts.md`](concepts.md), [`glossary.md`](glossary.md),
[`architecture/observability-surfaces.md`](architecture/observability-surfaces.md),
the whole [`architecture/diagrams/`](architecture/diagrams/README.md)
tree (its index, each tool directory's own authoring guide, and the
diagram sources and renders they describe),
[`devices/`](devices/README.md), and the three READMEs: the
[project](../README.md), the [server](../vinga-server/README.md), and
the [firmware](../vinga-esp32/README.md). Such a page may summarize an
authoritative source and link it; it may not quietly become a second
one. [`concepts.md`](concepts.md) is the one that also carries
decided direction, and it stays inside this class by marking every
such claim with the record that owns it, or by saying plainly that
nothing owns it yet.

**Generated references** are rendered from the code and diffed by CI,
so they cannot come to describe a server this repository does not
build: [`reference/`](reference/), whole directory.
[`reference/cli.md`](reference/cli.md) is hand-written prose around
two generated halves and is held to the same rule, since what it says
the grammar is comes from the command tree. Correcting a generated
page means changing its generator.

**Decisions** are one immutable, date-prefixed record per decision
that was hard to reverse, surprising without context, and the result
of a real trade-off: [`adr/`](adr/README.md), whole directory. A
record is superseded by a later one, never edited into agreement with
the code.

**Dated execution records** report what was true when they were
written and are not rewritten when the code moves on:
[`plans/`](plans/) with their `-implementation` companions and
[`features/`](features/), both whole directories, plus
[`../CHANGELOG.md`](../CHANGELOG.md), which is the same thing in one
file. They are evidence about a change, never current guidance.

**Research and field notes** are what was read, measured, or observed,
carrying the date and provenance that make them worth trusting:
[`xiaozhi-notes.md`](xiaozhi-notes.md),
[`related-projects.md`](related-projects.md),
[`conversational-quality-regression-suite.md`](conversational-quality-regression-suite.md),
and
[`architecture/cli-guide-audit.md`](architecture/cli-guide-audit.md),
the 2026-08-24 walk of four published CLI guides that the CLI guide's
practices were dispositioned from. A note is evidence: where one and
the guideline it fed disagree about the code today, the guideline is
the one that was corrected.
[`xiaozhi-notes.md`](xiaozhi-notes.md) is the mixed one and says so on
its own first screen: its protocol sections are maintained, and carry a
statement of which upstream commits and which observed firmware
versions they were last read against, while its reading of the upstream
server and its field observations keep their dates and are not chased.

Index pages carry no authority of their own, because they route
rather than claim: this page,
[`architecture/README.md`](architecture/README.md),
[`architecture/diagrams/README.md`](architecture/diagrams/README.md),
[`devices/README.md`](devices/README.md) and
[`adr/README.md`](adr/README.md) say where a thing is, and the page
they send you to is the one that says it.
[`architecture/principles.md`](architecture/principles.md) is one of
these too, and only that: the promises and the guidelines it used to
hold are now the two pages above, and the path stays because dated
records link it.

## Start here

- [**The project overview**](../README.md): what vinga is, the hardware
  it targets, and the seven-step path from a container to a board that
  answers.
- [**system-overview.md**](system-overview.md): one conversation turn
  from the wake word to the spoken reply, with the diagrams and each
  concept explained before its acronym is used.
- [**vinga-server**](../vinga-server/README.md): the server in full.
  Every provider option, the two halves of the configuration, the
  security defaults, running it in a container, and onboarding a device.
- [**vinga-esp32**](../vinga-esp32/README.md): the thin firmware
  customization and the boards it targets.
- [**devices/**](devices/README.md): the per-board guides, described
  under [Device guides](#device-guides) below.
- [**deploy/postgres-init.sql**](../deploy/postgres-init.sql): the one
  thing a deployment runs against its own Postgres before the server
  does. It creates the three schemas the server owns, `domain`,
  `record` and `memory`, and the read-only role the conversation record
  is read through, and its header says what the executor needs and why
  the file is safe to run again after a reset or after a release that
  adds a schema. The `docker-compose.yml`
  at the repository root runs the same file against the database it
  starts, whether that is the development one alone or the pair a trial
  runs.

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
  memory, meta capabilities, the help agent). Deliberately ahead of
  the code, and explicit about it: every section opens with its
  status, each decided direction names the issue that owns it, and a
  direction with no owner says so rather than borrowing authority
  from the page.
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

- [**architecture/**](architecture/README.md): the index for that
  corpus, organized by the question you arrived with. Designing a
  feature or deciding direction, splitting a file or naming an
  interface, adding a command, placing a datum, and understanding a
  conversation end to end: each routes to the page that answers it,
  and the promises, the guidelines, the design and CLI guides, the
  observability map and the diagrams are behind it.

### Diagrams

The diagrams live under
[**architecture/diagrams/**](architecture/diagrams/README.md), one
index over a directory per authoring tool.
`architecture/diagrams/excalidraw/` holds the hand-drawn pair, whose
editable originals are scenes of the same names in the team workspace
and whose committed files are exports kept in sync by hand, so flag
them when a pipeline change makes them stale.
`architecture/diagrams/plantuml/` holds three whose source is text
here and whose renders come from a command, so they cannot drift
unnoticed: what leaves the host, the ordering inside one turn, and
the barge-in decision.

## Research notes

- [**xiaozhi-notes.md**](xiaozhi-notes.md): the device↔server protocol,
  key by key, and the upstream projects it came from. Read this first
  for anything protocol-related. Its sections say which of four things
  each is: maintained protocol facts, corrected as the wire moves and
  carrying an explicit statement of which upstream commits and which
  observed firmware versions they were last read against; dated field
  observations; the historical reading of the upstream server; and
  licensing evidence. Board procedures and board behavior are not here,
  they are in [`devices/`](devices/README.md).
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
