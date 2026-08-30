# Product promises

vinga's standing commitments to the person running it. A product
promise is falsifiable from outside: someone with a board, a server
and no access to the source can check whether it holds. Breaking one
does not refactor vinga, it changes what vinga is.

Promises outrank [the guidelines](guidelines.md). The guidelines are
how the code keeps these promises, and any of them may be revised
given new evidence, provided the promises still hold; when a guideline
and a promise pull in different directions, the promise wins.

A promise changes the way a product decision changes rather than the
way an implementation does: deliberately, rarely, and recorded, with
this page updated to cite the record in the same change. Each promise
below cites the decision record or the issue where its reasoning
lives, so this page stays an index rather than a replacement for
either.

This introduction is not itself a promise. The three sections below
are, and they are the whole of what this page claims.

## Stock xiaozhi firmware is the compatibility floor

An ESP32-S3 board running upstream xiaozhi firmware, pointed at a
vinga server, holds a conversation without a reflash. If vinga
ships its own firmware one day, that raises the ceiling, never the
floor: protocol extensions are additive and negotiated, the server
never requires vinga firmware for ordinary conversation, and vinga
firmware never drifts into a private dialect a stock board cannot
join.

The promise is bounded three ways, and the bounds are part of it:

- It covers the transport vinga implements: the WebSocket channel.
  Upstream also speaks an MQTT-plus-UDP pairing; vinga does not
  promise every transport upstream carries.
- Its version target is the firmware actually running on boards in
  the field. Upstream protocol changes are absorbed as shipped
  devices adopt them, not chased at upstream's commit log.
- It is a floor, not a ceiling: ordinary conversation, not every
  vinga feature.

**Example.** Onboarding a stock board is repointing one NVS
`ota_url` entry at the vinga server
([xiaozhi-notes](../xiaozhi-notes.md)); everything after that is the
standard OTA fetch and hello exchange.

**Counterexample.** "Cleaning up" the hello exchange in a way stock
firmware does not parse; or a vinga-firmware-only message becoming
load-bearing for ordinary conversation, so stock boards quietly stop
being full citizens.

The promise has a named cost, paid knowingly: server features are
constrained to what stock firmware can express, and some rough edges
(onboarding a device by typing a long OTA URL on a phone) are the
price of meeting devices where they are.

Evidence and tradeoffs:
[issue #84](https://github.com/rafacm/vinga/issues/84).

## A fully local deployment is first-class

Every core conversational capability is reachable with local
providers; a cloud provider is an upgrade, never a requirement. A
`server.local_only: true` server that starts can hold a complete
conversation, the way the original all-local chain (Silero,
faster-whisper, Ollama, Piper) did from the beginning.

The enforcement mechanism is declared egress: every provider declares
whether it sends session data off the host, and `local_only` refuses
at startup to build one that does. A provider type that cannot answer
for itself must say so explicitly (an OpenAI-compatible base URL is
equally a vendor or an Ollama on localhost, so the configuration
states which). The guarantee is enforced, not documented.

**Example.** An inherently-cloud runtime (a native realtime session)
arriving as a sibling runtime is fine and expected; the local
pipeline remains complete without it.

**Counterexample.** A core capability (memory, end-of-turn detection,
barge-in quality) implemented only against a cloud API, so local
deployments drift into the second-class configuration nobody chose to
demote. Nobody would delete the local path; features would just stop
landing on it. Also: assuming locality from a provider's shape, or a
new provider type skipping the egress declaration because it is
"obviously" local; an undeclared provider is a hole in the guarantee.

## A beta database is never left behind

From the first image called a beta onward, every database that image
creates or touches is upgradeable by every later image: a migration
that cannot upgrade in place is a bug, not a decision. Until a beta
is declared, upgrades are best-effort forward-only, which the CI
wheel-migration step exercises whenever the server workflow runs, by
taking a fresh database from the shipped artifact to the head of every
chain, and migration history is never rewritten as a cleanup: a
squash or a prune is a compatibility decision requiring a record that
supersedes the standing one, a statement of which databases become
unsupported, and a tested reset path.

Where "forward" starts is a fact an operator needs, so the promise
states it rather than leaving it to be inferred:

- **In-place upgrades begin at the three current baselines**,
  `3001_postgres_domain` for the domain configuration,
  `1002_conversation_threads` for the conversation record, and
  `2001_agent_memory` for what each agent was asked to remember, added
  by [the 2026-08-30 storage move](../plans/2026-08-30-memory-postgres.md)
  and a forward extension rather than a priced exit: nothing existing
  is re-cut, and a deployment that has never had the schema meets it
  as an empty one. That is
  where "forward" starts today: a database stamped at or after them
  is what a later image is built to upgrade, best-effort, through the
  reviewed migration every schema change arrives as. Before a beta
  that is the floor rather than a guarantee about every future image,
  because the recorded-reset licence two bullets below still stands.
  The conversation record's floor moved: `1001_postgres_conversations`
  was the floor until the conversations chain was re-cut for
  first-class conversations, and a database stamped at it is
  unsupported rather than upgradeable. That is a recorded pre-beta
  reset, taken under this promise's own terms and priced by the
  [2026-08-28 addendum](../adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md#addendum-2026-08-28-issue-190-the-conversations-chain-re-cuts),
  which names what it strands and carries the tested path back.
- **The current build opens no SQLite file at all.** There is no
  driver in it, no code path and no configuration key that would let
  it try, so a SQLite-era database is not something this build can
  half-read.
- **A recorded decision may still require a reset** while the project
  is pre-beta. What the promise guarantees before a beta is that such
  a decision is recorded rather than slipped, not that it will never
  be taken.
- **Recovery is export and reapply, with the secrets re-entered from
  the environment.** A stored credential never travels in an export,
  so the document carries the command that enters each one and the
  values come from wherever the deployment already keeps them. The
  procedure is in the server README, under
  [When the server will not start](../../vinga-server/README.md#when-the-server-will-not-start),
  and is not restated here.
- **The conversation record crossed the Postgres cutover only by manual
  archiving.** There was no export format for it and no importer; a
  deployment that wanted to keep what it recorded copied the SQLite
  file aside before the upgrade.

**Example.** The priced exit the standing record grants, exercised
three times and every time by a recorded addendum to it: the
2026-08-23 squash of the domain chain onto one reviewed baseline
(#243), the 2026-08-26 re-baseline of both chains onto Postgres
(#283), and the 2026-08-28 re-cut of the conversations chain for
first-class conversations (#190). Each named the databases it
stranded, showed the tested path back, and left the beta obligation
exactly where it was.

**Counterexample.** The same reset carried out without that record:
an image that quietly stops reading what the last one wrote, so the
first an operator hears of it is a server that will not boot. The
recorded kind is the promise working; the unrecorded kind is the
promise broken, and the two look identical from inside the commit
that makes the change.

Decision:
[ADR](../adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md),
with its 2026-08-23, 2026-08-26 and 2026-08-28 addenda.
