# Database upgrades have a compatibility floor

**Status:** Accepted (recorded 2026-08-20, as milestone M7 of the
#210 governance simplification plan)

## Context

The repository is labeled early development, but it publishes a
deployable image on every push to `main` and documents persistent
database volumes, backup, restore, and encryption key handling. The
repository cannot prove whether third parties run deployments, but
its deployment model permits them, so pre-release does not
automatically mean disposable data. The #210 architecture review
asked whether the migration history (four configuration revisions,
one conversations baseline) was governance worth its cost, and
concluded the cost of keeping it is three small files while the cost
of rewriting it is real: a database stamped at a rewritten baseline
would be considered migrated while lacking folded-in columns, later
revisions would refer to removed ones, there is no complete YAML
importer to rebuild configuration and encrypted secrets from, and
the existing upgrade tests deliberately prove that data survives
migration from the first revision.

**Addendum, 2026-08-22 (issue #225).** The audit the last consequence
below sent to its own issue settled the stance this Context left open,
and the settlement belongs here rather than only in that issue. The
project is pre-release, says so in a notice nobody reading it can miss,
and has declared no beta. No third-party installation is known: a
deployment model that permits one is not evidence that one exists, and
the maintainer's own boards are resettable, their configuration a file
and a database that can be rebuilt from it. So tolerance for a state
only a pre-release build could have produced is cost with no
beneficiary, and a refusal path may stop accepting a shape no supported
deployment produces. That is what licenses the deletions recorded under
issue #225, and it is bounded twice: it does not reach the databases,
whose floor the Decision below sets and which this addendum leaves
exactly as it is, and it ends the day a beta is declared.

## Decision

- **Upgrades are supported from the first beta image onward.** The
  day a build is called a beta, every database that image creates or
  touches is upgradeable by every later image, and that promise is
  what "beta" means here.
- **Until a beta is declared, upgrades are supported best-effort
  from revision `0001` forward.** A deployment tracking `latest`
  gets reviewed migrations for every schema change, which the
  existing upgrade tests already prove from the baseline; what it
  does not get is a promise that no future decision will ever
  require a reset, only that such a decision would be recorded, not
  slipped.
- **Migration history is never rewritten as a cleanup.** Squashing
  or pruning revisions is a compatibility decision: it requires a
  new record superseding this one, an explicit statement of which
  databases become unsupported, and a tested reset or
  export-and-reimport path. Saving migration files is never by
  itself a reason.
- **Every schema change continues to arrive as a reviewed
  migration**, with generated revisions treated as candidates for
  review, not as truth.

## Consequences

- The four configuration revisions and the conversations baseline
  stay as they are; nothing is squashed.
- The wheel-migration CI step keeps proving that a fresh database
  built from the shipped artifact reaches the head of both chains,
  and the upgrade tests keep proving data survives from `0001`.
- Declaring a beta acquires a new obligation and therefore a
  checklist item: from that image forward, a migration that cannot
  upgrade in place is a bug, not a decision.
- The wider question the review raised, which other compatibility
  branches in the code are load-bearing corruption recovery and
  which are obsolete pre-release history (old MCP grant forms,
  previously accepted provider URL shapes, recovery for legacy
  names), is a separate audit tracked in its own issue; this record
  covers the databases only.
