# samtal documentation

Reference material and working notes. User-facing documentation lives in the
READMEs: [the project overview](../README.md),
[samtal-server](../samtal-server/README.md), and
[samtal-esp32](../samtal-esp32/README.md).

## Research notes

- [**xiaozhi-notes.md**](xiaozhi-notes.md): the upstream architecture, the
  device↔server protocol, ports, configuration keys, and the validated
  end-to-end demo procedure. Read this first for anything protocol-related.

## Plans

One file per accepted plan, named with a `YYYY-MM-DD-` prefix, each with a
companion `-implementation` doc recording deviations, resolved open
questions, and discoveries. A plan's milestone checklist doubles as its
milestone descriptions: each ticked item links to its implementation-doc
section, so a fresh session can resume from the repository alone.

- [**samtal-server v1**](plans/2026-08-02-samtal-server-v1.md) ·
  [implementation notes](plans/2026-08-02-samtal-server-v1-implementation.md):
  architecture and milestones M0 to M7, from package skeleton to a published
  container image.

## Features

`features/` holds a doc per significant change made outside any active plan,
same date-prefix naming, covering Problem, Changes, Key parameters,
Verification, and Files modified. Milestone work under a plan is documented
by that plan's implementation doc and its pull request instead. Empty for
now.

## Conventions

Documentation process, writing conventions, and the workflow these documents
follow are defined in [`../AGENTS.md`](../AGENTS.md).
