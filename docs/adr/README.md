# Architecture decision records

One file per decision that shapes the server's structure. A record holds
the context a decision was made in, the decision itself, and what it
commits us to, so a later session (human or agent) does not re-litigate
it without new evidence.

## When to write one

Record a decision when all three hold:

1. It is hard to reverse.
2. It is surprising without context.
3. It is the result of a real trade-off.

Routine choices that the code explains on its own do not get a record.

## Format

Each record has four sections: **Status**, **Context**, **Decision**,
and **Consequences**. Status is `Accepted`, `Deprecated`, or
`Superseded by <file>`. Records are immutable: a reversed decision gets
a new file naming the one it supersedes, and only the old file's status
line changes; its body is never rewritten.

## Naming

`YYYY-MM-DD-short-slug.md`, dated the day the record is written. The
common ADR convention numbers records (`0001-use-postgres.md`); this
repository uses date prefixes instead, matching `docs/plans/` and
`docs/features/`, and dates sort just as well.

## Relationship to the rest of the repository

Issues hold evidence, ADRs hold decisions, plans hold execution, and
[`../architecture/principles.md`](../architecture/principles.md) holds
direction: the standing principles distilled from these records. A
record cites the issues and measurements that motivated it; the plan or
feature doc that implements a change links back to the records it rests
on; a record that establishes or reverses a principle is cited from the
principles page in the same change.
