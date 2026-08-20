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
