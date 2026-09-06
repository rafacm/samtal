# What the provider build may call a stored entry

**Date:** 2026-09-06

## Problem

A stored identity leaves this server by three doors, and until now two
of them were closed.

A name is held to one URL path segment at write time only. A row written
before that rule still boots and still reads, so it can hold
`https://user:password@host/named` and be a lawful row. #381 (PR #408)
put every DISPLAY of such a name through `without_url_credential`, and
#382 (PR #412) put every REFUSAL through the same door: the reference
check, the completeness check, the location a per-row storage refusal is
built from, and the walk over a validation error's locations.

Both of those live inside the composition, the stretch that turns a
stored snapshot into a `Config`. What runs next is the build, and it
names the same entries again in a vocabulary of its own. PR #412 found
this while closing its own hole and filed it rather than widening into
it, because it is the same rule behind a different renderer, after the
composition rather than during it.

Four compositions carried a stored name whole:

| Where | What it composes |
| --- | --- |
| `providers/registry.py` | `providers.<stage>.<name>`, the label every refusal the constructor raises carries, and the label handed to every factory and option reader below it |
| `providers/world.py` | the same label again, for the checks that only run once an object exists |
| `providers/world.py` | `agents.<agent>: no <stage> provider is named`, which only the build can say |
| `models.Config.provider_for_agent` | `agents.<agent>.<stage>`, the location it answers beside the name |

And the sweep the issue asked for found a fifth and a sixth, in the same
function as the second: the `ProviderIdentity` the build stamps onto
every provider, which is what every provider event calls the entry, and
the loopback warning the build emits itself, which names the entry in
its sentence and in a structured field.

The sol round on PR #422 then found three more, recorded below: the
model that rides beside the name in that same stamp, the rejected type
the unknown-type refusal quoted back, and an option name beside it. All
of them are the same rule, and none of them is the entry's NAME, which
is why the first pass over the label did not close them.

## Changes

### One home for where an entry is written

The label the build composes is the string the store already composes
for the same row: `store._location` joins a kind's section to the
identity that addresses one entry under it, with the strip on every
part, and #382 wrote the reasoning for that strip beside it. Its readers
were all in `store.py`, which is why it lived there.

Spelling it a third and a fourth time in the provider package is how a
boot comes to name one entry two ways, `providers.llm.x` for a row it
could not read and something else for the entry it could not build. So
the composition moves to `config/entities.py`, beside the addressing
tuple it is built from and beside `addressed`, which reads it back
apart. `provider_label(stage, name)` sits beside it for the one kind
whose location the build says out loud, and both halves of the build
read it.

It is called `entity_location` rather than `location` because
`location` is `store.py`'s most common local name, and an import
shadowed by a loop variable is a trap rather than a name. Ruff's F402
catches that, which is how the collision was found.

### The strip, on the rest of what a build hands out

- **The agent's own name in the stage refusal.** Spelled inline rather
  than through a helper, unlike the label: one sentence says it, and a
  function forwarding its argument would hide nothing.
- **The location `provider_for_agent` answers.** No caller renders it
  today, so this one is defence rather than a reachable leak; it is the
  composition every message quoting that layer would be built from, and
  its own docstring says so. The provider name beside it is deliberately
  untouched: that half is an address, read straight back out of
  `providers.<stage>` by both callers.
- **The stamped `ProviderIdentity`.** Not an address either: it is what
  every provider event calls the entry, and an event is written to a
  log, to whatever collects one, to the live stream and into the
  conversation's own record.
- **The loopback warning.** Now told the identity the stamp just made
  rather than the columns it was made from. The four fields the warning
  carries are exactly the four that identity holds, so this removes the
  second reading rather than adding a second strip.

### What the sol round added

**The sol round on PR #422, 2 P1s and 1 P2, all three adopted**, and one
neighbouring leak found while reproducing the second.

- **The stamped identity leaked through `model` (P1).** The stamp put
  the name through the strip and copied `provider.model` beside it. A
  model is free text a vendor names, written as an option, so a row
  stored before the URL rule can hold one carrying a credential and put
  it in the `gen_ai.request.model` field of every round the entry
  answers. The identity now carries it stripped; `provider.model`, which
  is what goes into the request, is untouched and pinned that way. The
  other three fields of the stamp cannot need it and now say so: the
  stage is one of four words, the type is one of the table's own keys
  because a type that is not is refused before anything is built, and
  the host is `urlsplit().hostname`.
- **The unknown-type refusal quoted the rejected type (P1).** The
  sharper half of that sentence: the label beside it was stripped and
  the type was not, so a planted `type:
  https://user:password@host/x` published a credential anyway. The type
  is no longer quoted. What comes back is the entry, the rule it broke
  and the closed set it should have been in, which is what a stage
  column holding what no stage is already gets (`store._NOT_A_STAGE`),
  worded as `check_references` words the same shape. One pin in
  `test_providers.py` moved deliberately, with the reason in its
  docstring.
- **The stderr claim never touched stderr (P2).** The boot case entered
  the lifespan through `TestClient` and read the sentence off
  `startup_failure`, which passes with the print gone, with the print
  unsafe, or with uvicorn's traceback beside it. It now boots from
  planted rows through `serving.run(None)` and asserts the sanitized
  location on stderr with no sentinel and no traceback on either stream
  or in either log format. That covers what TestClient skips: uvicorn's
  `sys.exit(3)` on a refused lifespan, the swallow in `serve`, and the
  log filter that drops uvicorn's own rendering. Measured while writing
  it: the run binds no port, because uvicorn's `startup` awaits the
  lifespan and leaves before it creates a socket.
- **An unknown option's name, found while reproducing the type.**
  `OptionsReader.finish` lists the keys a type never asked about, and
  those are the caller's. Answered by shortening rather than by
  withholding, which is the line between the two: a refusal that can
  list the closed set the value should have been in names the set and
  not the value; one that cannot names what was written, through the
  strip, as a display of the same key does. The typed half needs
  nothing, since a model forbidding extras refuses through
  `validation_problems`, which names only declared keys.

## Key parameters

- `entities.entity_location(descriptor, *identity)`: where an entry is
  written in the configuration document, and the one home for that
  string. Read by `store.py` for every per-row refusal and by
  `provider_label` for the build's.
- `entities.provider_label(stage, name)`
  (`vinga-server/src/vinga_server/config/entities.py`): what every
  refusal about one provider entry names it. One home because the two
  halves of a build have to agree: the constructor composes it for its
  own refusals and for everything below it, and the owner composes it
  again for the checks that only run once an object exists.
- `providers/world.py: build_entry`: composes the label and stamps the
  identity, both through the strip, while `name` is passed on exactly as
  stored, because that is the key the entry's stored credentials are
  filed under.
- `Config.provider_for_agent`: the agent half of its location goes
  through the strip and the provider half does not, which is the
  address-versus-sentence line this whole rule sits on.

No configuration key, event field or event sentence changed, and every
committed reference regenerates byte-identical.

## The sweep, and what it deliberately leaves

The issue asked for a sweep of the provider-build path rather than the
list it came with, on the evidence that the reported list was
incomplete both times this pattern ran. It was incomplete again: the
stamped identity and the loopback warning are in the same function as
the label and were not on it.

The grep the sweep is built on, over `src/`:

```
grep -rnE '(agents|providers|mcp_servers|prompt_fragments|agent_defaults|devices)\.\{' src/
grep -rn 'Identifier(' src/
grep -rn 'label' src/vinga_server/providers/*.py src/vinga_server/egress.py
```

Four surfaces of the same shape are outside this change, each named
here so that leaving them named is what carries them.

- **The MCP build path composes `mcp_servers.<name>` twice**, in
  `egress.check_mcp_server` and in `tools/mcp/manager.py`, both of them
  boot refusals over a stored name. The same hole under a different
  noun, and a decision about the MCP build rather than a consequence of
  this one.
- **Every event that names an AGENT carries the stored name whole.**
  `Identifier(agent)` appears about twenty times across the pipeline,
  the filler, the session, the memory store and the OTA reply, and none
  of them is composed by the provider build. The one home for an answer
  would be the events package rather than each emitter, which is a
  policy decision of its own: `Identifier` is documented as a name the
  operator or this server chose, and it also carries hosts, paths and
  origins.
- **A session's manifest names its agent and its provider entries
  verbatim.** `device/session.py: _manifest` writes `agent`, `agents`
  and each stage's `name` beside `views.provider_record(entry)`, which
  strips the entry's own values and keys. The manifest is written into a
  capture file and a conversation's session row, both of which outlive
  the conversation.
- **An apply's answer lists entries by identity.** `ProvidersReload`
  carries `built`, `reused` and `retired` as `<stage>.<name>`, and those
  are the keys the build dedups and disposes by, so a strip there would
  have to be at the rendering rather than at the identity.

Two more things are recorded as measured rather than assumed. An apply
that cannot build the stored world answers with a fixed sentence and
logs the exception class alone (`reload._built`), so no label reaches
that surface at all: the boot's stderr is the only place these
refusals are read. And `provider_for_agent`'s location has no caller
that renders it today; both callers take the name and discard it.

## Verification

- Lint: `uv run ruff check .` clean.
- Unit, the shape CI runs: `uv run pytest tests/unit -q -n auto --dist
  loadfile`, 5819 passed and 19 skipped, eight of them the cases added
  here.
- Integration: `uv run pytest tests/integration -q`, 245 passed.
- All six committed-reference drift checks (domain config, server
  config, conversations schema, events, OpenAPI, CLI reference) diff
  empty; `uv run mypy` over the events package clean;
  `scripts/check_doc_links.py .` checked 207 files with 0 failures. The
  census manifest is regenerated: the changelog entry moved the line
  numbers under it and nothing else.
- Every leak was reproduced before it was fixed, by writing the case
  first and watching the planted name appear whole, and every fix was
  then proven to bite by reverting it in place, watching the case fail
  for the right reason, and restoring the file from a copy and touching
  it (never `git checkout`, per `AGENTS.md`).
  - The strip inside `entity_location`, reverted: the two label cases
    and the boot case failed, and so did #382's own case about a row
    that will not read, which is what says the two are one home.
  - The stage refusal, reverted: the sentence carried the agent's name
    whole.
  - The stamped identity, reverted: the event payload and the loopback
    warning both carried it.
  - The location, reverted: `provider_for_agent` answered with it.

## Files modified

- `vinga-server/src/vinga_server/config/entities.py`
- `vinga-server/src/vinga_server/config/store.py`
- `vinga-server/src/vinga_server/config/models.py`
- `vinga-server/src/vinga_server/providers/world.py`
- `vinga-server/src/vinga_server/providers/registry.py`
- `vinga-server/tests/unit/test_config_url_credential_display.py`
- `CHANGELOG.md`
