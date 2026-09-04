# One process, one replica is the supported topology

**Status:** Accepted (recorded 2026-09-04, deciding
[#316](https://github.com/rafacm/vinga/issues/316)).

## Context

The server is one ASGI application in one process: `serving.py` runs
uvicorn without worker fan-out, and the compose file runs one `vinga`
service. Nothing has ever claimed more, but nothing said so either,
and several passages (the token docstring, the security section of the
server README) mentioned replicas in a way a reader could take as
support for running two.

Whether two replicas actually work is answered by inventorying what is
process-local. The database half is already safe across processes: the
configuration store takes a transaction-scoped advisory lock on every
write (`config/store.py`), so two writers cannot validate against the
same snapshot and persist over one another, and conversations and
memory are rows in the same Postgres. Device tokens are stateless HMAC
(`auth.py`), so any process holding the secret verifies any other's
tokens. That is compatibility at the storage and auth edges. It is not
coordination, and everything a running server *serves from* is local
to its process:

- **Pending device activation.** The six-digit claim codes live in an
  in-memory table (`onboarding/pending.py`), deliberately
  non-persistent, shared across handlers under one mutex. A second
  process would mint codes the first cannot claim; the bind path
  already hedges against exactly this in a comment
  (`config/api.py`, the retire after a bind).
- **Configuration generation and reload.** An apply
  (`config/reload.py`) prepares and swaps a world inside one process.
  A second replica reads the same store but never hears the apply; it
  would serve its boot-time generation until something restarted it.
- **Session admission.** Capacity is a per-process count in
  `registry.py`, and `/readyz` reports that one process's admission
  state. Two replicas would enforce two independent caps with no
  shared view.
- **Providers and engines.** Startup-built singletons per process
  (see
  [providers are stateless singletons](2026-08-04-providers-are-stateless-singletons.md)):
  every process loads its own models into memory at startup, whatever
  volume the downloaded weights are cached on.
- **Runtime lifecycle.** Drain on SIGTERM is the process closing its
  own sessions; nothing hands a conversation to a peer.

## Decision

One process, one replica, is the supported topology for the first
self-hosted release. The image is not to be scaled horizontally, and
the documentation must not imply that it can be. The readiness probes
(#318) exist so an orchestrator can manage that one replica's
lifecycle, a rollout, a restart, a drain, not so a balancer can spread
devices across several.

The topology is exercised, not just asserted: CI's `image` job boots
the committed `docker-compose.yml` against the image it builds, and
that file runs exactly one server service with no replica setting. The
compose file is the executable statement of the supported shape.

A future clustered topology gets one named module, **cluster
coordination**, owning the three facts replicas would have to agree
on: activation claims, configuration revisions (which generation each
node serves, and how an apply reaches all of them), and node status
(a shared view of admission for whatever routes devices). Until that
module exists, no partial coordination is to be distributed across the
modules above; the advisory lock in the store and the retire-after-bind
hedge in the API are the only cross-process concessions, and they stay
where they are.

## Consequences

What it buys: every module in the inventory keeps its simplest shape.
The pending table stays a mutex around a dict, reload stays an
in-process swap, admission stays a count, and none of them grow a
distributed second mode that would have to be tested against peers
that do not exist.

What it costs: capacity is one process's `limits.max_sessions`, a
redeploy interrupts service for the length of the drain, and
availability is that of a single host. These are accepted for a
self-hosted server whose fleet is a household's worth of devices.

Reconsideration triggers, any one of which reopens this record with
evidence in hand:

- A deployment whose demand exceeds one process: `/readyz` answering
  `full` under normal load, not as a transient.
- A requirement for zero-interruption deploys, where the drain's
  pause is no longer acceptable.
- An availability requirement above a single host.

Reopening means designing the cluster coordination module named above,
in one place, rather than teaching individual modules about peers.
