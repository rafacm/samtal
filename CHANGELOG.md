# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
using dates (`## YYYY-MM-DD`) as section headers instead of version numbers.

## 2026-08-18

### Changed

- **A shutdown waits for the drain it started** (#142, M4). The task
  that lets conversations finish their sentence was created and
  forgotten: nothing held a reference to it, so the event loop was free
  to collect it mid-drain, and anything it raised was reported by the
  garbage collector instead of by the shutdown that started it. The
  server now owns that task and does not finish serving until the task
  has finished, or until its bound expires (the drain budget plus the
  margin the registry holds back for the closes), in which case the
  drain is abandoned with a warning rather than allowed to hold the
  process up, whatever it makes of being cancelled. A drain that failed
  is reported in one line naming the class of what went wrong and not
  what it said, since a client failing on its way out can quote the
  endpoint or the credential it was given. What an operator sees on a
  redeploy is otherwise unchanged: the first signal drains, a second one
  still forces the exit on the spot,
  and a signal arriving before there is anything to drain still passes
  straight through.

- **The configuration API is served over one database engine, not one
  per request** (#142, M3). Every call to `/api` opened the
  configuration database, ran an Alembic up-to-dateness check inside
  it, re-parsed `SAMTAL_MASTER_KEY` and threw the engine away. The
  engine is now opened once by whichever lifespan owns the application
  (the server's when the API is mounted, the API's own when it is run
  standalone, never both, since a mounted application gets no lifespan
  of its own) and disposed when that lifespan ends, with the encryption
  keys derived once beside it. The open still migrates, so a first
  deployment whose first act is an API write still gets a schema, and a
  server that migrated at boot pays one no-op check. Nothing an
  operator sees changed: a write still takes the lock per transaction,
  contention is still met inside the request and still answered 409
  with the repository's own sentence, and a database another writer is
  holding at startup is refused as the boot failure it is, with the one
  sanitized sentence a refused boot prints. The conversation store's
  reads keep opening per request, deliberately: a store restored or
  purged under a running server is met as it is now, which is that
  store's own property.

- **A server acquires what it is made of when it starts serving, and
  lets go of it when it stops** (#142, M2). `create_app` built
  everything: an application that was built and never served held a
  connection pool on the configuration database, a migrated
  conversation store and every provider model its agents named. It now
  describes the application (its routes, its mounted configuration API
  with the gate armed, and the refusals a deployment missing a secret
  must meet whatever launched it) and builds nothing; the lifespan
  builds, in the order the old function documented, registering every
  acquisition for release as it makes it, so a startup that fails part
  way through leaves nothing open. The provider build, which loads
  models for seconds to minutes, runs on a worker thread instead of the
  event loop. A boot failure is still one sanitized sentence on stderr
  and an exit code of 1: the lifespan records it and raises
  `StartupFailed` with nothing chained to it, and the entry point
  prints it. The captive-portal banner is now said after a successful
  startup rather than before one, so a server that fails to start
  announces nothing, and a signal arriving before the composition
  exists goes straight to uvicorn's shutdown rather than to a drain
  with nothing to drain. Nothing on the wire changed, and all four
  generated references regenerate identical.

- **What a running server is made of is one typed object** (#142, M1).
  The composition root hung thirteen untyped attributes on `app.state`,
  and the mounted configuration API kept a seven-attribute bag of its
  own beside them, so a handler reading any one of them could learn
  what was there and what type it had only by reading the function that
  set it. `samtal_server/composition.py` declares it instead: the
  `Composition` a server is (the configuration, the device auth, the
  bindings, the pending table, the MCP managers, the memory store, the
  session registry, the providers, the filler cache, the conversation
  store, the runtime factory, the device facts, the capture store, and
  the API's own `ApiRuntime`). `app.state` carries that one attribute
  and the sub-application carries `api_runtime`; the websocket
  endpoint, the OTA endpoint, the shutdown drain and the API's
  dependencies bind one of the two and read declared fields. The filler
  cache is an `AgentFillers` rather than a bare dictionary, which
  answers exactly as the empty dictionary did before boot fills it and
  exactly as the filled one after, and can now say which of the two it
  is doing. Nothing an operator sees changed: construction still
  happens where it happened, the API token is still resolved into a
  local and stored nowhere, and all four generated references
  regenerate identical.

- **The latency mask is its own module** (#141, M2). The second of the
  two clusters the conversation runtime was carrying: the filler timer
  armed at the transcription, the fire-time checks that stand it down
  when the user is still speaking or a barge-in is being confirmed, the
  cached clip that goes out when neither does, and the arbitration that
  makes the reply's first real sentence queue behind a clip's tail
  rather than cut it mid-word. `runtime/filler_runner.py` holds it as
  `FillerRunner`, which asks whoever holds the floor two questions
  through a `TurnView` interface and tells it nothing, so the one piece
  of state the two clusters share crosses as a read-only property.
  Nothing an operator sees changed: `filler_played` and
  `filler_skipped` keep their messages, their fields and their firing
  conditions, the clip is still encoded whole before the first byte
  goes out, and the generated events reference regenerates identical.
  What did change is that the mask can be driven onto each of its
  outcomes without a session, a socket or a synthesis behind it, and
  that the runtime is 1,484 lines against the 1,820 it started at, with
  the responsibilities it kept named in its own class docstring.

- **Who holds the floor is its own module** (#141, M1). The
  conversation runtime kept the turn-taking core alongside everything
  else it does: the utterance buffer and the tail cap that bounds it,
  the endpointer feed, the pre-roll trim, and the gate ladder that
  decides whether speech arriving mid-reply may cancel that reply.
  `runtime/turntaking.py` holds that cluster as `TurnTaking`, reaching
  the rest of the runtime through one narrow `ReplyControl` interface,
  and the runtime's device-facing methods are one-line delegations to
  it. Nothing an operator sees changed: every decision event keeps its
  message, its fields and its firing conditions, both halves of the
  log still ride the one session channel, and the generated events
  reference regenerates byte-identical. What did change is that the
  gate ladder can now be driven onto each of its five decisions
  without building a session around it, and that the tail cap and the
  trim arithmetic have tests of their own for the first time.

- **The MCP subsystem answers for itself** (#140, M4, which closes the
  issue). The last of the four milestones is about what the tests are
  allowed to know: a registry says which manager an entry has and
  whether a reload is between its two phases, a manager says which
  session it is calling over, the tasks a stop gave up waiting for
  have a name rather than a hiding place, and `McpManager` states what
  a registry needs of one server, so a suite can stand something in
  for one instead of inheriting from the real thing and reaching
  inside it. Four tests that used to rewrite a running server's
  configuration to make it look like a box coming back now let the box
  come back. Nothing an operator sees changed, which is the point of
  the milestone as it was of the three before it.

  With it #140 is done. A 2,338-line file is a package of six modules
  with a stated responsibility each, under the module name it always
  had, so every retained record still reads the same (M1); the status
  and reload surface the API sends is built where the knowledge is,
  with the document byte-identical (M2); the three places a tool can
  come from answer one interface, and the runtime loops over them
  instead of knowing each by heart (M3).

- **One interface under the three tool sources** (#140, M3). The
  conversation runtime knew each of the three places a tool comes from
  by heart: the builtins it built itself, the device's list it scanned
  twice, and the MCP registry it asked by name, with four calling
  conventions between them and a timeout that forked on where the call
  had come from. `samtal_server/tools/source.py` states what they have
  in common as `ToolSource`, and the runtime's snapshot, dispatch and
  timeout are one loop over the three sources it is built with.
  Every question a source is asked is asked about the same claim, the
  classification reserved on the turn's record before anything ran, so
  MCP routing is never in a position to resolve a name a second time
  (the device source deliberately keeps its live-list ownership scan,
  the recorded edge behavior) and a
  reload cannot reroute a call in flight. What no source can answer
  stays with the runtime: arguments a model never closed, a name nobody
  publishes, and the handover, which ends the tool loop rather than
  answering the model. Nothing an operator sees moved: the same tools
  are offered in the same order, the same sentences come back, and the
  `tool_call` event says what it said.

## 2026-08-17

### Added

- **One descriptor per domain entity** (#139, M1).
  `samtal_server/config/entities.py` declares what the pydantic models
  cannot carry, in three tiers: the five kinds written with a command
  of their own, the two shapes that exist only nested inside one of
  those, and the two domain-level fields written with their own verbs.
  Each entry holds its documentation prose, how the kind is addressed
  (the API path prefix and the parameters under it, which are also the
  CLI's positional arguments), whether it has a delete, whether stored
  secrets hang on it, and which moved configuration key its command is
  quoted for. The generated reference now renders those descriptors
  rather than keeping a second copy of them: docgen's own entity
  dataclass and its two data tables are gone. Nothing else reads the
  registry yet, and nothing changed for a reader or an operator, which
  is the point of doing it this way: both committed references,
  `docs/reference/domain-config.md` and
  `docs/reference/api-openapi.json`, regenerate byte for byte, so the
  move is provable rather than asserted.
- **The event schema reference cannot drift** (#155, M3).
  `samtal-server events reference` prints the whole registry as a
  document: one section per event, one subsection per shape it may be
  emitted in, with the channel it rides, its level, the byte-exact
  sentence it renders, the kind of every argument position and a field
  table naming every field's kind, requiredness, nullability, token
  set, syntax or bounds, plus the taxonomies, syntaxes and grammars
  those tables point at. It is committed at `docs/reference/events.md`,
  and CI regenerates it and diffs it byte for byte, the fourth such
  step beside the domain configuration, the conversation store and the
  OpenAPI document. The command opens nothing and dispatches before the
  entrypoint resolves `SAMTAL_EVENTS_ENFORCEMENT`, so an unusable value
  of a server-only variable cannot stand between a reader and the
  document.
  With this, #155 is done. One registry declares every event, the
  emitters hold every emission to it, and the documentation is that
  registry rendered rather than a second copy of it: a new field
  carrying far-side bytes is now a schema violation a test lane
  refuses, not a review finding somebody has to notice. The far-side
  values the surface deliberately keeps are the declared `DESCRIPTOR`
  fields and only those, which the amended content-and-telemetry ADR
  admits as bounded device-descriptor metadata; they are named as such
  in the reference, held to their bounds at emit, and an undeclared one
  beside them is refused like any other.
- **The emitters enforce the event schema at emit time** (#155, M2).
  Both `_emit` paths now hold an emission to its declaration in two
  ordered steps: what the caller passed is judged before the base
  fields are merged, so a `**fields` spread carrying `session=` is
  refused as the identity spoofing it is rather than merged over the
  emitter's own; then the finished payload is matched whole against the
  event's declared variants, the emitting channel, the level, the
  sentence compared byte for byte against the registry's template, the
  argument tuple against its per-position kinds, and the fields against
  the variant's table. `SAMTAL_EVENTS_ENFORCEMENT` picks the mode.
  `strict` raises `EventSchemaError`, which is what every context that
  is not a running server gets by default: the test lanes, an import, a
  REPL. `forgiving` recovers, which is what a running server gets:
  the emission is rebuilt against its variant where that produces a
  declared shape, and becomes a `schema_violation` event carrying
  nothing but the emitter's own identity where it does not, with one
  ERROR line on the emitter's channel naming what was refused. A
  telemetry bug can therefore no longer cost a reply, and the whole
  path runs under one guard so that a bug in the enforcement itself
  cannot either. Diagnostics render registry-owned identifiers only: a
  declared event or field name, a fixed violation code, a count, an
  argument position, never a rejected name or a value.
- `SAMTAL_EVENTS_ENFORCEMENT` (`strict` or `forgiving`), resolved by
  `create_app` and by the entrypoint after it has loaded `.env`. Unset
  means `forgiving` at both, because a running server is a deployment
  whatever artifact it runs from; anything else refuses to start,
  naming the variable and the two values it takes. The container image
  sets `forgiving` explicitly, redundant with that default on purpose,
  so the production posture is visible in the artifact. Documented in
  the README's Logging section.
- **Every event's schema is declared as data** (#155, M1):
  `samtal_server/events_schema.py` holds one declaration per event,
  naming its channel, its level, the exact sentence template it
  renders, the kind of every argument position, and its field set with
  per-field kinds, closed token sets, syntaxes and bounds. 58 events in
  99 variants: the 57 with ordinary emit sites, plus the internal
  recovery event the forgiving mode will emit. Declarations only in
  this change; the emitters do not read the registry yet, so no
  behaviour depends on it, and the release is behaviour-identical
  except where the four sanitization fixes below bite. A conformance
  test walks all 81 emit sites and holds the declarations to them both
  ways, so a field the registry declares that nothing emits fails as
  loudly as one the code emits and the registry does not know.
- The five conversation-store event paths gained exact pins
  (`tests/unit/test_conversations_event_pins.py`), in the two contract
  pin suites' style. They postdate those suites' baseline and had none.

### Fixed

- **Four decision sites bound the far-side strings their events
  retain** (#155, M1). Each takes an event-only copy: printable
  characters only, trimmed, and cut to a declared limit, with what the
  site answers elsewhere untouched. Visible to adversarial input only;
  every value a real device sends passes through unaltered.
  - `ota_check`'s `board` and `firmware` are bounded in the payload and
    in the four sentences that render them. The OTA reply still echoes
    the reported version verbatim, which is how the firmware decides
    whether it is up to date, and the recorded device facts a capture
    manifest is built from are unchanged.
  - `ota_check`'s `client` is bounded, and null where nothing printable
    survives. The device token is still signed for the Client-Id header
    exactly as it arrived.
  - `session_open`'s `client` is bounded in its field and in its
    sentence. The capture manifest and the conversation store still
    hold the header itself.
  - The websocket capacity refusal names the normalized MAC or no
    device at all. With device authentication off nothing had verified
    that header, so a full server could be made to write one
    caller-chosen string per attempt into the retained log.

### Changed

- **The MCP runtime routes know what they were handed** (#140, M2).
  The status read and the reload action declared their two
  dependencies as `Any`, because the registry that answers them imports
  the MCP SDK and the provider layer and rendering the committed
  OpenAPI document must load neither. They now declare
  `McpStatusSource` and `McpReloader`, a protocol and a callable shape
  stated in `config/responses.py` out of typing and the response models
  alone, so a route says what it asks of a running server and the
  document still renders without any of that being importable. The
  answers are composed where the knowledge is: the registry validates
  its own status into the models the contract declares, and the reload
  returns the endpoint's whole reply, its outcomes and the status taken
  with no await between them, which used to be an invariant a request
  handler was holding by hand. Nothing changed on the wire, which the
  byte-identical OpenAPI document proves.
- **Content a voice assistant cannot speak is named once** (#140, M2).
  This server speaks MCP twice, to the device over JSON-RPC and to
  configured servers over the SDK, and each side had its own copy of
  the sentence a model reads out when a tool answers with an image or
  audio. The sentence and the join are one function in
  `protocol/mcp.py` now, over a normalized sequence of a content type
  and the text it carries; both sides keep the decoding that is theirs,
  including the device channel's tolerance of a result of any shape.
- **The MCP subsystem is six files under one name** (#140, M1).
  `samtal_server/tools/mcp.py` was 2,338 lines and at least twelve
  responsibilities, of which one class was 885 lines. It is now the
  package `samtal_server/tools/mcp/`, whose `__init__` is still the
  module `samtal_server.tools.mcp`: it builds the one emitter, so the
  channel these events ride is unchanged by construction, and it
  re-exports what the six submodules define. `transport` brings a
  connection up and classifies what went wrong, `prompts` captures what
  a server ships about itself under its bounds, `manager` runs one
  server's lifecycle, `slice` holds the configuration a registry was
  built from, `reload` applies a newly read one, and `registry` answers
  what needs the managers and the slice together. Straight moves rather
  than rewrites: every sentence, token, grant rule, refusal and capture
  bound behaves as it did, and nothing an operator reads or a collector
  groups by has moved, which the untouched event pin suites and a
  byte-identical events reference prove. Two rules hold across the
  split and are stated in the package docstring: events go through the
  package emitter, and ordinary prose records go through the package
  logger, so what an operator watches is still one logger's worth of
  lines. The conformance suite's channel-ownership rule gained the
  package form to match, with planted cases for the shapes it accepts
  and the three it refuses.
- **Importing the MCP layer no longer rearranges logging** (#140, M1).
  Importing `tools/mcp` used to install a filter on four of the MCP
  SDK's loggers and turn propagation off at the root of its namespace,
  as a side effect of the import, so a tool that imported it to read a
  type paid for it too. The same filters and the same propagation are
  now `transport.quiet_sdk_loggers()`, called where a manager creates
  the task one connection lives in. That is the one place a connect is
  ever begun, and it is the boundary for a reason: a start is not the
  only way in, since a session opening revives a down server through a
  background reconnect that comes straight there. What a third-party
  server's own client writes still reaches no handler of ours, on
  either path.
- **The domain configuration's schema is single-sourced** (#139, M5).
  With this, #139 is done. A domain entity was spelled at 14 to 19
  places across 12 to 14 files, and adding the most recent kind
  hand-edited 13 of them; it is now its pydantic model plus one entry in
  `samtal_server/config/entities.py`, which the documentation renderer,
  the repository, the read views, the API's route factory and the CLI's
  dispatch table all read. M1 built the registry and pointed docgen at
  it, M2 gave the repository one read, one write and one delete over all
  five kinds, M3 built the twenty-two entity routes from the endpoint
  facts their descriptors carry, M4 made a command one row that both
  paths run and deleted the hand-kept shapes the CLI used to check an
  answer against, and M5 splits the 2,305-line acceptance file along the
  boundaries the other four produced. Nothing an operator or a client
  reads changed at any of the five: every refusal, notice, column and
  document is byte for byte what it was, and both committed references
  regenerate unchanged with no regeneration commit anywhere on the
  branch. What a new field costs was measured rather than asserted: a
  scratch field added to one entity's model, its column and its
  migration reached the store, the API, the CLI and both generated
  references with no descriptor edit and no other hand edit, the whole
  unit lane green but for the two drift pins a regeneration command
  answers. The one surface it does not reach on its own is the read
  view, whose per-kind body builders are written key by key, which is
  deliberate for `provider_record` and incidental for the other four;
  filed rather than changed here, since changing it would change what a
  read prints.
- **The config CLI's acceptance suite is six files** (#139, M5).
  `tests/unit/test_config_cli.py` was 2,305 lines and 101 tests because
  the module it tested was one module. It keeps the acceptance spine,
  the empty-database-to-working-configuration walk and the per-kind
  write, show, list and delete behavior that #139 made
  descriptor-driven; the rest moved, one file per concern, to
  `test_config_cli_transport.py`, `test_config_cli_rendering.py`,
  `test_config_cli_secrets.py`, `test_config_cli_grammar.py` and the
  `test_config_cli_local.py` M4 started. The scaffolding six suites
  share (the runner that drives a command against a server of the test's
  own, the sentinels, the fragment constants) is in
  `tests/support/config_cli.py`, per #144's rule that no test module
  imports another. A pure move: every one of the 101 test functions is
  byte for byte what it was, and the lane collects exactly what it
  collected before.
- **The CLI dispatches from one table and renders from the API's own
  shapes** (#139, M4). A command was two implementations of one act:
  fourteen `if args.local:` branches chose between reaching the API and
  opening the database, each with its own copy of the sentence the act
  answers with and the notice saying when it applies. An act is one row
  now, carrying the verb, the path this command's arguments address,
  the body it sends, the shape it is answered with, how that is
  printed, and, for the four commands `--local` covers, the same act
  against the database; one dispatcher reads a row, and the
  acknowledgement and the notice are printed in one place whichever
  path produced them. The five commanded kinds' rows are built from
  their descriptors rather than written per kind, the summary tree asks
  a kind how one of its entries reads, and `--local`, its preamble and
  its four-command subset are exactly what they were. What the CLI
  accepts as an answer is the shape `responses.py` declares the route
  answers with: the four frozensets naming a body's fields and a
  state's vocabulary, and the ten predicate functions that walked a
  body by hand, are gone, and with them a whole class of drift where a
  field renamed on the model left the CLI refusing every well-formed
  answer. The fifth of those constants, `RELOAD_OUTCOMES`, survives in
  derived form: it is the order the reload prints its four lines in,
  read off the result model's own fields rather than listed again.
  Validation is strict, so nothing is coerced into a rendering, and
  unknown fields are dropped rather than refused, so a newer server
  stays readable; the rejected body reaches neither the output nor an
  exception chain.
  Nothing an operator reads changed: every refusal, notice, column and
  document is byte for byte what it was, and each of the sixteen acts
  with a break-glass path is now pinned to print the same thing on both
  of them.
- **The API's entity routes are built from the descriptors** (#139,
  M3). The nine reads and thirteen writes of the five commanded kinds
  were hand-written handlers saying the same nine things in five
  vocabularies; they are one factory now, walking the registry. What
  the committed OpenAPI document carries about each route is written
  down on the kind's descriptor, because those bytes are contract: the
  operation's name, its description, the response model and the
  refusals it declares. What the verb settles the same way for every
  kind is not written down at all: the method, the path under the
  kind's route, the parameters, the request body and which repository
  call is made. The pydantic response and request models move to
  `samtal_server/config/responses.py`, which imports pydantic and
  nothing else, so that the CLI can render an answer against the shape
  the API declared without importing FastAPI to do it; `api.py` imports
  them back and re-exports them under the names they had. The routes
  the tiers do not describe (the whole-configuration read, the devices
  and the pending claim, the default agent, the runtime namespace) stay
  written by hand, because a setting is not an entity. Nothing changed
  for a caller: `docs/reference/api-openapi.json` regenerates byte for
  byte, every operation id, summary, description, parameter and status
  in it included.
- **The repository and the read views work from the descriptors**
  (#139, M2). `store.py` no longer names its five entity kinds at
  fourteen methods and a whole-configuration read: the table a kind is
  rowed in, how one of its rows maps to its model and back, what to
  refuse when an entry is not there, and which checks the write runs
  are facts on the kind's descriptor, and there is one read, one write
  and one delete over all of them. The default row mapping is the model
  itself, so a kind pays a hook only where its model asks for something
  a dump cannot say: the MCP server's per-column omissions, the layer's
  None-inherits tri-state, and the provider's split between declared
  fields and options extras, all three moved rather than rewritten.
  `views.py` reads which builder shows a kind from the same registry,
  and `views.provider_record` stays deliberately hand-built. The two
  walkers that refused a value JSON cannot carry merge into one, since
  the second was the first's float branch. Nothing changed for a reader
  or an operator: every refusal sentence is byte for byte what it was,
  both committed references regenerate unchanged, and the acceptance
  suites are untouched.
- **The README's event table is a name-and-when index** (#155, M3). Its
  fields column is gone: prose field and token claims nothing parses go
  stale while a name-level check stays green, and half-checked
  documentation reads as checked. The index now names all 58 events,
  the 24 it had never listed included, each with a sentence saying when
  it fires, and every field, token, kind and bound lives in the
  generated reference it points at. A test holds the index to the
  registry at name level: every declared event in exactly one row,
  every row a declared event, no duplicates. The lead sentence's
  base-field claim is scoped to the session channel, where it is true.
  The conversation store's reference points at the event reference for
  the same reason.
- **Breaking: the echo guard's recovered sentence no longer says what
  was recovered** (#165): `asr_prompt_echo`'s one INFO outcome, the
  retry that heard a real utterance where the guard was about to
  discard the clip, stopped rendering the transcript into the retained
  log. The sentence now names the duration alone, which with the
  event's unchanged `outcome`, `duration_s`, `retry_ms` and `host`
  fields is the whole of what the branch has to report; the template
  drops from two arguments to one, and its level, its channel and its
  field set are untouched. This was the last conversation text on the
  event surface, which the content-and-telemetry ADR bans without
  exception, and it was there however innocently the transcript was
  recovered: a user reading a credential aloud is a turn like any
  other. A sentinel test plants a credential-shaped transcript and
  asserts it stays in the transcription result the provider answers
  with, which is what the session goes on to hear, and reaches neither
  the log records nor an attached event consumer. **Migration:** what
  was said comes from the conversation store instead, keyed by the same
  session id and subject to `server.conversations.enabled` with
  `text: true`; an operator who greps the logs for recovered utterances
  queries the store's `turns` rows.
- The content-and-telemetry ADR gained a dated amendment: bounded
  device-descriptor metadata (the board, firmware, and client id a
  device reports at check-in, sanitized at their decision sites) is
  metadata the events may carry; conversation-derived text remains
  banned without exception, and the one standing violation (the
  `asr_prompt_echo` recovered-transcript sentence) was removed by its
  own narrowing above (#165), ahead of the #155 schema registry.

## 2026-08-16

### Added

- **The conversation store is real: `server.conversations` records what
  was said** (#120): the machinery and the content record landed
  dormant; this is the release in which an operator can switch them on,
  and in which switching them on does everything the documentation
  says. The section is optional and off by default, with `enabled`, two
  independent storage switches (`metrics` for the events and every
  measured number, `text` for conversation text and tool names,
  arguments and results) and `retention_days`, which defaults to 90 and
  takes `0` as the deliberate keep-forever. Enabled, the server opens
  and migrates `conversations.db` beside `samtal.db` in
  `server.database.dir` at boot, warns that it is recording and names
  the file, and writes one session row per conversation, one turn row
  per utterance-and-reply with its text and its ASR, LLM and TTS
  numbers, one row per tool call under it, and one row per structured
  event, which is the same decision track a capture writes beside its
  audio. Nothing of it is on the conversation's path: a background
  thread does every database call behind a queue no producer waits on,
  commits at turn boundaries and at session close, and drops events
  with one warning per session when the database is wedged rather than
  ever delaying a reply. Retention prunes whole sessions older than the
  window at startup and at each close. The server README now carries
  the store's own section: what is kept, what each switch takes away in
  all four combinations, the shared-device limit stated out loud
  (deployment-wide switches are the only privacy control this release
  has), purge and its physical-deletion semantics, and the WAL-safe way
  to read a live file. **Operator-visible:** a server with no
  `conversations` section behaves exactly as before and creates no
  file; one that has recorded before has its file migrated at every
  start even with recording off, so history stays readable. Four new
  events on the store's own channel: `conversations_enabled` (a warning
  at startup, like capture's, because the server is keeping what is
  said to it), `conversations_dropped`, `conversations_failed` and
  `conversations_pruned`.
- **The conversation record is readable over the API** (#120): three
  gated reads under `/api/conversations`, in the committed OpenAPI
  document with the rest of the contract. `GET /api/conversations`
  lists the sessions newest first, filtered by `?device=` when given;
  `GET /api/conversations/{session}` is one session whole, with how
  many turns and events hang off it; `GET
  /api/conversations/{session}/turns` is one session's timeline oldest
  first, each turn carrying its numbers and the tool calls it made
  nested in the order the model issued them. The list and the timeline
  page on the store's monotonic row ids, which are never reused, and the
  detail read is singular and takes neither argument: `?limit=` holds 50
  rows by default and 200 at most, `?cursor=` means the sessions before
  it in the listing and the turns after it in a timeline, and a page
  answers `{"items": [...], "next_cursor": <id or null>}`. Each read
  opens the file for the length of one request, takes no lock and
  creates nothing. **Operator-visible:** a deployment that never
  recorded answers 404 naming `server.conversations.enabled`, and one
  that recorded and has since switched recording off still serves what
  it recorded, because switching recording off stops the writer and not
  the reader. Content columns come back as they were stored, which is
  null where text storage was off, and every session says which way its
  switches were set. The events themselves are deliberately not served:
  the database is that surface. Deletion over HTTP is not here either;
  `samtal-server conversations purge` is what deletes, and it needs no
  server.

### Changed

- **The API namespace redirects nothing, so no response header quotes a
  request back** (#120): the router's default answered a path with a
  stray trailing slash (`/api/config/`, `/api/agents/<name>/`,
  `/api/conversations/?limit=<value>`) with a 307 whose `Location`
  carried the request's own path segments and query string. That is a
  value quoted back in a header, where a proxy and a browser both keep
  it, and it is the one place this API still did so. Trailing-slash
  redirects are off for the whole gated namespace now.
  **Operator-visible:** a request to a route path with a trailing slash
  that used to be redirected is answered 404 (401 without a token, as
  ever); address the route as the committed OpenAPI document spells it.
  `/api` and `/api/` both still resolve, which was never a redirect.
- **A provider's address may not carry a credential, and no record
  keeps one that does** (#120): `base_url:
  https://user:password@host/v1` names nothing secret-shaped, so every
  rule this project had about inline secrets passed it, and it was
  stored as written, shown on every read, and copied verbatim into the
  manifest of every session held against that provider. Two halves fix
  it. Writing such a URL is refused where a provider is written (both
  write paths, since the rule is the repository's), for a user and
  password before the host and for a credential-shaped query parameter
  alike, with a refusal that names the option and the rule and never the
  value; the rule is write-time only, exactly like the addressability
  rule, so a deployment that already has such a row still boots, still
  reads it and can still edit it out. And a record is built through an
  explicit representation instead of a model dump, masking secret-shaped
  keys at every depth and taking the credential out of any URL-shaped
  value, so a row written before the rule cannot leak either.
  **Operator-visible, and a deliberate narrowing of an existing
  surface:** the same builder feeds the **capture manifest**, so a
  capture taken from now on records such an address without its
  credential where it used to record it whole. The entry name, the type
  and the exact model string are untouched, which is what a manifest is
  kept for.
- **Breaking: the conversation events carry no conversation text**
  (#120): `heard`, `replied` and `agent_said` lose their `text` field,
  and their sentences stop rendering it. `heard` says how long the user
  spoke and, where the engine detected it, in what language; `replied`
  and `agent_said` say which agent spoke and how many sentences of the
  reply the user actually heard, which is a number no event carried
  before and which reports what went out rather than what was
  generated. The retained logs are now metadata only, which is what
  makes the no-leak contract on them a property of the schema instead
  of a review finding, and it is the supersession the 2026-08-04
  observability ADR anticipated: its follow-up note records that it has
  happened. **Migration:** transcripts come from the conversation store
  instead, keyed by the same session id
  (`select heard, reply from turns where session = ?`, with
  `server.conversations.enabled` and `text: true`); every duration,
  count and identifier a latency brief reads is unchanged.
- **Breaking: `llm_round`'s token counts are named as the GenAI
  conventions name them** (#120): `prompt_tokens` becomes
  `input_tokens` and `completion_tokens` becomes `output_tokens`, which
  is the vocabulary the observability ADR adopts where one exists and
  the names the store's `turns` and `turns.legs` columns have carried
  since their first migration, so a dashboard and a SQL query no longer
  need a translation between them. **Migration:** a token-count
  dashboard renames two fields.
- **Breaking: `tool_call` names only what this server authored** (#120):
  the event drops `tool` for every call whose name a peer chose and
  gains `source`, one of `builtin`, `device`, `mcp` or `unknown`, from
  the same classifier the store's `tool_invocations` rows are written
  with. A builtin still carries `tool`, because those names are this
  application's own; an MCP call carries `entry`, the name an operator
  wrote in their configuration, and never the far side's tool name; a
  device tool and a name nobody publishes are named by their `source`
  and nothing else. A published tool name is half whatever the far side
  called it and an alphanumeric credential survives sanitizing intact,
  which is the exposure #154 closed on the MCP lifecycle events and
  this closes on the one event that still carried a name.
  **Migration:** a tool dashboard groups by `source`, and by `entry`
  for MCP calls; the full name, its arguments and its result are on the
  store's `tool_invocations` rows. Alongside it, the warning about a
  model's unparseable tool arguments reports how many characters they
  were instead of printing them.
- **The provider-bearing events say which model answered** (#120):
  `llm_round`, `llm_retry` and `provider_failed` gain `model`, the
  configured model identifier, wherever the entry names one, which is
  OTel's `gen_ai.request.model`. Two entries of one type can run
  different models, and a turn's token totals blend the rounds that
  answered it, so the per-round, per-model truth needed somewhere to
  be. Additive: an entry with no model to name (the bundled VAD, a
  Piper voice, the mocks) carries no field rather than an invented one.
- **An MCP tool call is executed by the entry it was resolved against,
  or by nobody** (#120): a reload can move a published name between
  entries (`home__inside` coming up takes `home__inside__x` over from
  `home`), and a call resolved just before one landed could be executed
  by the new owner, under that owner's timeout, while being recorded
  and logged as the entry that had not run it. The entry a call was
  classified against now decides its routing and its timeout, and the
  registry refuses a name that has moved instead of following it. The
  model is told the tool did not run and answers in its own words, the
  same as for any tool that fails. **Operator-visible:** only in that
  window, and only as a tool call that failed where it used to
  succeed against a server the caller did not mean.
- **The session capture's decision track inherits the narrowing**
  (#120): the capture is a consumer of the same events, so its
  `<session>.jsonl` keeps every event minus the text, minus every tool
  name a peer chose, and with the renamed token fields. A builtin's own
  name is still on a captured `tool_call`, exactly as it is on the
  event; the conversation store's `events` rows drop even that, keeping
  every called tool's name on `tool_invocations` where the text switch
  governs it. Nothing else about a capture
  changes: the WAV beside it still records everything said in the room,
  which is the division of labour the capture was built on, and the
  session id correlates all three records. The startup warning says so
  in the same words: it announced "room audio and transcripts" while the
  events carried them, and now announces room audio and a track of the
  session's events.
- **`session_closed` says why a conversation ended** (#120): the event
  carried only `duration_s`, so the reason was inferable from whichever
  line happened to precede it and from nothing else. It gains `reason`,
  one of `limit`, `idle`, `drain`, `client` or `error`, decided at the
  site that decides and latched by the first cause to fire, so a drain
  closing a session an idle timer was about to hang up on reads
  `drain`. The conversation store copies the token onto the session
  row. Additive: no field was renamed or removed, and the README's
  event table and the pin suite moved with it. Alongside it, the three
  cleanup steps ahead of the event are now guarded individually, so one
  that fails is reported by class and the close still completes rather
  than the session ending with no record of having ended.
- **The test suite gets a shared fakes package, starting with the LLM
  SDK shapes** (#144): `tests/support` held four real MCP subprocess
  servers and nothing else, so a double for a vendor SDK object had to
  live in whichever suite happened to need it first, and the next suite
  copied it. `tests/support/llm_sdk.py` now holds the fourteen classes
  that fake the two streaming dialects a provider talks (the anthropic
  messages stream and the openai chat completions one), moved
  byte-identical out of the tool-calling suite, plus the one probe that
  answers False to a truth test, which four provider suites had each
  hand-rolled. Test-only: no source file changes, no assertion changes,
  and the moved classes are compared against their origin by normalized
  AST rather than by eye. A contract test pins the probe's falsiness
  where the probe lives, because the suites that inject it assert only
  that the provider kept the object it was handed, which a truthy probe
  would satisfy while testing nothing.
- **The session suites stop importing each other** (#144): the session
  family was a web of test modules borrowing helpers from each other,
  with `test_session.py` alone imported by thirteen, so a suite about
  capture or about the conversation store could not be read without
  first reading the session suite it borrowed its handshake from. Seven
  more modules under `tests/support` now hold what was shared, each
  named for the seam it serves: `configs.py` (the configurations and
  their constants), `providers.py` (the scripted models, ears, voices
  and endpointer), `sockets.py` (the three device-socket stand-ins),
  `wire.py` (driving a session over a real websocket and reading the
  reply back), `sessions.py` (building one in process and driving a
  reply through it), `events.py` (reading the structured log), and
  `device_tools.py` (the board's half of the device tool channel). The
  boundary pair (`StubRuntime` and its `FakeDevice`) is promoted to
  `boundary.py` as the package's stated seam-testing template: name the
  far side after the side it replaces, and give it only the calls the
  near side makes. No test module imports `test_session*`,
  `test_tools_device` or `test_boundary_contract` any more. Test-only:
  no source file changes, no assertion changes, the collected count
  unchanged, and every relocated definition compared against its origin
  by normalized AST rather than by eye.
- **No test module imports another, and a test says so** (#144): the
  feature suites were the other half of the web, thirty-three imports
  reaching into the OTA, onboarding, MCP, capture, conversations,
  bindings, drain, memory and ws-auth suites. Four more modules under
  `tests/support` hold what was shared: `checkin.py` (what a board says
  at its configuration check and the ceremony that binds it),
  `tools_mcp.py` (the MCP entries, the configurations that grant them,
  and one real server hosted for the length of a block), `stores.py`
  (the capture directory, the conversations database and the corrupted
  memory file) and `registry.py` (the sessions a drain walks and the
  bindings an operator writes). `configs.py`, `events.py`, `wire.py`
  and `sessions.py` gained the rest. Collisions were settled by naming
  rather than by merging: two manifests, two client builders, two
  MAC constants and two configuration builders that were not the same
  thing keep separate definitions with names that say which seam they
  serve, and every importing site keeps its own spelling through an
  import alias, so no assertion moved. The new
  `tests/unit/test_support_boundaries.py` walks the whole lane with
  `ast` and fails on a test module that imports another, at the top of
  a file or inside a function, and on anything under `tests/support`
  that imports a test module, so the criterion is enforced rather than
  remembered; a conftest stays allowed. Test-only: no source file
  changes, no assertion changes, the collected count up by exactly the
  guard's four tests, and every relocated definition compared against
  its origin by normalized AST rather than by eye.
- **Three places where two encodings have to agree are pinned by a
  test** (#144, completing it): the drift the 2026-08-14 review found
  was in pairs nothing checked against each other, so each pair is now
  a test that states the relation the two sides actually hold.
  `config.example.yaml` is walked against `ServerConfig`: every leaf
  field, through the nested sections found in the annotations rather
  than from a list kept by hand, has to be in the example, written or
  commented out, at the depth it belongs to, since the file's own
  convention is that a default worth keeping appears as a commented
  `# key:` line with its reasoning. The example filenames in
  `docgen.ENTITIES` are checked against `examples/` both ways: every
  name an entity gives is a file that exists, and every file is claimed
  by exactly one entity, so neither a renamed example nor an unclaimed
  new one can pass. And the five frozensets in `cli.py` that decide
  whether a body can be read as a pending listing, a status entry, a
  reload's answer or a prompt block are bridged to the pydantic models
  in `api.py` that produce those bodies, each with its true relation:
  a subset where the CLI renders less than the model carries, equality
  for the status fields and the state vocabulary, the reload outcomes
  read off the model's annotations with the tuple's no-duplicates
  clause pinned separately, and the prompt-block fields against what
  `PromptBlock` requires rather than everything it has, since `name` is
  optional there. Every branch of every pin was exercised by mutation,
  applied and reverted: each proof watched failing with its message,
  and the one control that must pass (deleting a member of the pending
  predicate, whose relation is a subset rather than an equality)
  watched passing. Test-only: no source file changes, no assertion
  changes, and no existing test touched. The bridge file says in its
  docstring that it exists to be deleted whole by #139, which deletes
  the predicates it pins.

## 2026-08-15

### Added

- **The MCP lifecycle is on the event surface, and consumers have an
  interface to attach to** (#138): the subsystem that runs an
  operator's MCP servers recorded nothing structured, so a collector
  could not count a connection that came up, group failures by kind,
  or tell a reload that applied from one that refused. Five events now
  say all of it on the channel that module already logged on:
  `mcp_connected` (`entry`, `transport`, a count of `tools`,
  `duration_ms`), `mcp_down` (`entry`, `reason` from a closed set of
  six, `duration_ms` where a connect ran), `mcp_call_dropped`
  (`entry`, `position`, `error`), `mcp_reload` (`outcome` applied or
  refused, with the four counts or a refusal reason) and
  `mcp_tool_shadowed` (`entry`, `position`, `owner`). All five are in
  the README's event table and are a compatibility surface from here
  on. None of them carries a byte a third-party server chose: the
  reasons and the failure classes are tokens and type names picked
  where a failure is classified, never a message, and no line names a
  tool at all. A published tool name is half whatever the far side
  called it, sanitizing replaces only the characters both LLM APIs
  refuse, and an alphanumeric credential goes through that untouched,
  so every line about one tool says which one by its position in that
  server's listing and `samtal-server config status` prints the names
  to a terminal for whoever asks.
  Alongside them, every event this server emits is now offered to an
  explicit **tap** interface (`Emission`, `EventTap`) before it is
  logged, with a hub a server-scope consumer attaches to once for
  every subsystem; the JSON log and the session capture are its first
  two implementations, and #120's conversation store and the #66/#67
  exporters attach without touching a single emit site. **Operator-visible:**
  two new lines an MCP deployment did not see before, an intentional
  stop (at INFO, because a shutdown is not a problem) and a refused
  reload (at WARNING). A tool call that fails because its MCP server
  did also changes what the model is told: it now gets a fixed sentence
  naming the entry, where it used to be handed whatever the far side's
  SDK put in its exception, which could quote a response body straight
  into the conversation. What the failure was is in the
  `mcp_call_dropped` event instead, by class.
- **The conversation store's foundation, dormant** (#120): a second
  SQLite database, `conversations.db`, beside `samtal.db` in
  `server.database.dir`, with its own metadata and its own migration
  chain, holding sessions, turns, tool invocations and events as the
  queryable record of what was said. Nothing constructs it yet: no
  configuration key exists, no server behaviour changes, and no
  `conversations.db` is created by a server that is not asked for one.
  What landed is the machinery. The writer runs on one background
  thread behind an unbounded queue, so no producer on the session loop
  can ever wait on it; the bound sits on the droppable class, so a
  wedged database drops events with one warning per session and a count
  on the session row, and never refuses a turn or a close at the queue
  (a close whose own transaction fails still leaves the session row
  open-shaped, which is the documented incomplete state). It commits at
  markers into per-session batches, so a session's turn commits its own
  session and holds no write lock between turns, and every marker first
  confirms its session row still exists, so a purge of a running
  session is final rather than a race the next turn undoes. Retention
  deletes whole sessions older than a stated window (90 days by
  default, `0` an explicit opt-out) and deletion is physical:
  `secure_delete` and a truncating checkpoint, so a purge reaches the
  file's bytes and not only its index. The two storage switches
  (metrics, text) are applied at write time, with the events table
  stripped of conversation text from its first row.
  Alongside it, `samtal-server conversations purge --session | --device
  | --before` deletes from the file with no server running, because
  deletion has to work exactly when the server is broken or gone, and
  `samtal-server conversations schema` prints the generated reference
  committed at `docs/reference/conversations-schema.md`, which states
  the compatibility promise, what each switch takes away, the retention
  and deletion semantics with their limits, the WAL-safe way to take a
  copy, and the OpenTelemetry GenAI correspondence. **Operator-visible:**
  nothing yet, by design.
- **The conversation store's content record, dormant** (#120): the
  pipeline now assembles one record per completed turn and hands it to
  an optional recorder where `replied` is emitted, so a cancelled or
  failed reply records what its finally saw and an utterance nobody
  transcribed records nothing. The record carries what was heard with
  its duration and language, the joined reply and its per-agent legs
  after a handover (each with its own token counts, because a turn's
  totals blend agents that may run different models), every call the
  model issued with the position it issued it at, and the turn's
  measured numbers. Tool calls are recorded from a closed set of
  sources (`builtin`, `device`, `mcp`, `unknown`) decided by one
  classifier consulted before anything runs, so the record covers the
  paths the routing hides: a malformed call flagged as one, a name
  nobody publishes with its refusal, and a handover with its refusal or
  with the switch it made. Two measurements are new: the elapsed of the
  transcription a turn ran (null when the turn reused a barge-in's,
  which was measured elsewhere for another decision), and the reply's
  first synthesis request to its first audio bytes, taken at the
  synthesis provider and deliberately not at the device. This is a
  content channel beside the event tap rather than text read back off
  the events, because tool arguments and results never rode the events
  at all and the events are about to lose their text.
  **Operator-visible:** nothing yet, by design: no configuration key
  exists, nothing injects a recorder, and no event, log line or
  timing changed.

### Changed

- **One emitter serves every event, at an altitude no subsystem owns**
  (#138): the emitter behind the declared observability surface lived
  in `device/events.py` and was called by the device edge and the
  pipeline alone, while eleven other modules hand-built an
  `extra={...}` dict at each of forty-two sites and one provider had
  invented a private builder of its own. It now lives in
  `samtal_server/events.py`, which imports nothing from the packages
  that import it, and it emits rather than returning a payload for a
  call site to log around: a site says
  `events.info("heard %r", text, event="heard", ...)` and the emitter
  builds the payload, wraps it and offers it to every consumer.
  Session-scoped events keep the pinned `samtal_server.session`
  channel and server-scoped ones each keep the module logger name they
  already had, so every retained record's `logger` field, event name,
  level and field set is byte-identical; two characterization suites
  written before the move and unchanged after it are the evidence, and
  an AST test now fails any production logging call that carries its
  own `extra=`. The capture is offered every event before it is
  logged, as it always was, and a consumer that raises no longer costs
  the operator a log line. **Operator-visible:** writing a pin for every
  record turned up several that quoted something they should not, and
  each of those is now narrower. A rejected `Device-Id` is no longer
  echoed into the line that turns the device away, and a refused
  handshake names an unidentified client with a null `device` rather
  than whatever header arrived; the lines that report a failure render
  its class name rather than a dependency's own sentence; and the
  onboarding banner and the key-miss line carry no key material at
  all, with `samtal-server config ota-url` the only route to the URL.
  No event name, level or channel changed, and the only field that did
  is the one that held a value nobody had authenticated.

- **The egress guarantee is one rule, and a provider's marking is
  mandatory** (#136): the `server.local_only` enforcement behind the
  local-first promise lived in two places, the provider registry and
  the MCP build path, each with its own semantics, wording and
  exception type. Both now call `samtal_server/egress.py`, which holds
  the rule and every refusal sentence unchanged, word for word,
  including the provider messages' "off this host" and the MCP ones'
  "off this network"; each call site keeps only the exception type its
  surface promises. The registry no longer defaults an undeclared
  provider type to egress, and the `Provider` base no longer carries
  `egress = True`: every provider class declares its own marking in its
  own class body, inheriting one from a parent does not count, and a
  class that declared nothing or declared something other than true,
  false or null is refused when it is built, in any mode, with a
  message naming the class and not the value. Operator-visible
  behavior is unchanged for every declared provider and MCP entry; the
  new refusals can only be reached by code adding a provider type.

- **Providers share a kit, and a failed request has a type** (#137):
  the five provider types that reach a network re-implemented the same
  plumbing, three of them importing credential resolution from the
  Anthropic provider and two carrying identical copies of the PCM
  alignment loop, while `DEFAULT_TIMEOUT_S` was declared three times.
  `samtal_server/providers/kit.py` now owns credential resolution, the
  timeout and token defaults, the retries-off policy and the alignment
  helper, and no provider imports another. Request failures leave as
  `ProviderCallError`, or `ProviderCallTimeout` when the failure was a
  wait; neither is a `RuntimeError`, so a provider failure can no
  longer be mistaken for a vanished device, and the timeout one is a
  `TimeoutError`, so classifying it needs no substring matching. Both
  LLM providers gained the injectable `client=` seam the other three
  already had, and their SDK clients now carry a 30 s per-operation
  transport timeout with automatic retries off, where they previously
  had no bound at all and the SDK's two retries; no new configuration
  key, and a streaming reply that keeps delivering is not cut off by
  it. **Operator-visible:** a wrapped failure's message and the
  `provider_failed` event's `error` field now report the taxonomy class
  with the SDK's class name and the HTTP status, and no longer the
  vendor's own sentence or response body, which could echo request
  content or a credential into the retained logs. The ElevenLabs
  failure message loses its quoted body detail for the same reason.

### Fixed

- **A session test's synthesis fixtures pass a real failure callback**
  (#135): `test_only_a_sentence_whose_audio_finished_counts_as_spoken`
  built its two `_Synthesis` objects with the session id where the
  constructor takes the callback a failed synthesis is reported
  through. The test itself was earning its verdicts (it speaks one
  sentence to the end and cancels another mid-send, and neither path
  reaches the callback), but the fixture violated the constructor's
  contract, and under a failing voice that argument turns the provider
  failure into `TypeError: 'str' object is not callable` on its way
  out. Both constructions now share a recording callback with the
  signature spelled out, and the test asserts nothing was reported, so
  a failure appearing where the mock voice is supposed to work is a
  failing test rather than an unnoticed one. No production code
  changed.

- **A provider failure during a reply is reported instead of read as a
  vanished device** (#137): the reply body caught `RuntimeError`
  broadly and returned in silence, because that is what a starlette
  socket raises for a send that comes after the close and a
  disconnected device is not worth a word. A provider failure raised as
  a bare `RuntimeError` went out the same door, with the reply ending
  quietly and nothing on the record. Both of the transport's disconnect
  shapes are now translated at the device edge into the `DeviceGone`
  the boundary always promised, the reply body catches that type alone,
  and everything else is logged as "reply failed". A local bug in a
  reply, in the encoder or the resampler or anywhere else that speaks,
  is likewise on the record now instead of being taken for a device
  that went away. **Operator-visible:** that line names the exception's
  class and stops there. It used to carry the traceback, and the arm it
  comes from now catches every provider failure as well, whose message
  and chain of causes can hold whatever a response body held; the
  stage, the provider and the host of anything that failed on the wire
  are still on the `provider_failed` event beside it. Whether a failure
  was a wait is decided by type rather than by looking for "Timeout" in
  a class name; the `provider_failed` event's fields and its sentence
  are unchanged.
  The filler playback keeps its broad catch, whose narrowing is #141's.

## 2026-08-14

### Added

- **An MCP server entry carries its own guidance** (#122): an
  `instructions` field on an `mcp_servers` entry holds what the model
  should know about using that server's tools, and it is injected into
  the system prompt of every agent the entry is granted to, under a
  heading naming the prefix its tools carry (`home__`). Know-how about
  a capability now lives beside the capability instead of being copied
  into every persona that was granted it. The grant is the whole
  condition: it is injected whether or not the server is connected and
  whatever an allow list narrows its tools to, and an agent with
  `mcp: []` sees none of it. The text is stored and injected exactly as
  written, indentation and blank lines included. Editing it does not
  restart the connection, so a reload reports the entry as `unchanged`
  and the tools do not blink; the new text reaches a conversation at
  its next activation, a new session or an agent switch, and never a
  reply of one already running.
- **Agents share blocks of prompt text** (#122): a `prompt_fragments`
  section maps a name to one block of text, and `prompt_includes` on an
  agent or on `agent_defaults` names the fragments that agent's system
  prompt carries. Household facts or a house style are then written once
  instead of being copied into every persona prompt and drifting apart.
  The list follows the `mcp` field's rules exactly: unset inherits the
  `agent_defaults` list, naming a list replaces the inherited one rather
  than extending it, and `prompt_includes: []` opts one agent out of
  what its siblings share. A fragment is injected in the order the layer
  lists it, between the agent's own prompt and any MCP guidance, with
  nothing added around it and nothing trimmed inside it, and it is
  counted under `fragment:<name>` by `samtal-server config prompt` and
  by the `prompt_assembled` event. A name that matches no fragment is
  refused when it is written and reported by layer and list position
  rather than by value, since a name written beside prompt text is
  where a credential gets pasted. Fragments are part of the boot-time snapshot,
  so a write applies at the next server start.
- **A server's own guidance is consumed behind two opt-ins** (#122):
  an MCP server ships guidance about itself through two channels, the
  `instructions` of its handshake and the prompts it publishes, and
  each is injected only where an entry says so.
  `use_server_instructions` (off by default) takes the first;
  `inject_prompts` names published prompts one at a time, by the name
  the server lists them under, and injects them in the order listed. A
  published prompt renders as the text of its messages in order, joined
  by blank lines and with the roles dropped. Names are validated
  against the server's own paginated listing before anything is
  fetched, and a name it does not publish, a prompt declaring required
  arguments, and one that renders anything but text are each skipped
  with a warning naming the entry and the position in the list, never
  the name, since a prompt name is a string the server chose. Both
  channels are capped at 4000 characters per block, skipped whole
  rather than truncated, and nothing a server ships is ever written to
  a log. What is injected is counted under
  `server_instructions:<entry>` and `server_prompt:<entry>:<position>`
  by `samtal-server config prompt`, under a heading in the prompt
  saying the server is the one talking. What a server ships is captured
  on every connect whatever the flag says, so turning
  `use_server_instructions` on applies at the next reload with no
  reconnection; editing `inject_prompts` changes what a connect
  fetches, so that one restarts the connection.
- **An operator can read an agent's assembled prompt** (#122):
  `GET /api/runtime/agents/{name}/prompt`, and `samtal-server config
  prompt <agent>` as its client, answer the system prompt a session
  opening now as that agent would be sent: the ordered blocks, each
  with its provenance (`persona`, `fragment:<name>`,
  `instructions:<entry>`, `server_instructions:<entry>`,
  `server_prompt:<entry>:<position>`, `memory`), its size in characters
  and its text, plus the total to tune a small model's context budget
  against. It is assembled from the loaded agents, the running MCP slice
  and the memory store rather than from the database, so it cannot
  disagree with what a session would get, and it is a preview of a new
  session rather than a readback of a running one. The CLI prints whole
  blocks and truncates nothing, since a concealed tail is exactly what
  an operator came to see. Agent activation also logs a
  `prompt_assembled` event with the same per-source counts.

### Changed

- **The system prompt is assembled in one documented order** (#122):
  the agent's own prompt, then the shared fragments it includes in the
  order its layer lists them, then the guidance of each granted MCP
  entry in grant order, then the remembered facts last under the
  heading they have always had, separated by blank lines. For a
  deployment with no guidance and no fragments configured, the prompt is
  character for character what it was. The persona, the fragments and
  the guidance are assembled when a conversation starts and again when
  it switches agents, and held for the life of
  that activation; the remembered facts keep the clock they had, read
  on every reply, so a fact stored by one conversation is still known
  to a concurrent one on its next reply. That read now happens in a
  worker thread rather than on the event loop every conversation
  shares.

### Fixed

- **A `--local` write says when it really applies** (#134): three
  commands on the break-glass path told an operator to restart the
  server for a change `samtal-server config reload` applies. `config
  --local delete mcp-server`, and `set-secret` and `clear-secret` on an
  MCP entry's slot, now name the reload, which is what the same act
  through the configuration API has always answered. The line every
  `--local` invocation prints first changed with them: it said that a
  running server observes no local change until its next start, device
  bindings excepted, which would have contradicted the corrected notice
  one line later. It now says what the path is and leaves the timing to
  the write, which answers it in the same three cases the API does. A
  test runs each act of the `--local` mutating subset both ways and
  compares what the two printed, so a future notice on one path that
  the other does not follow fails rather than being noticed by
  somebody. The deployment notes and the configuration section of the
  server README carried the same absolute claim in four places and are
  corrected with it.

## 2026-08-13

### Added

- **A running server says what its MCP servers are doing** (#121):
  `GET /api/runtime/mcp-servers`, and `samtal-server config status` as
  its client, answer with one entry per configured `mcp_servers` entry:
  whether it is connected or down, since when, the reason token if it
  is down, the tools it published, and the agents that may reach it. An
  entry no agent references reports `unused`, which is the answer to
  "why does the agent not have that tool" when the entry itself looks
  right and was invisible until now. The read is answered from the
  running server's own managers rather than from the database, so it
  cannot disagree with what is connected, and it carries published tool
  names only: a description or a server's own listed name is bytes that
  server chose, and one holding a credential of the deployment's could
  reflect it there. `/runtime` is a namespace of its own because an
  `mcp_servers` entry may legally be named `status`.
- **An MCP change applies without a restart** (#121):
  `POST /api/runtime/mcp-servers/reload`, and `samtal-server config
  reload` as its client, have a running server re-read the
  `mcp_servers` entries, the secrets stored on them and the agents'
  effective `mcp` grant lists, and apply them: an entry that is new or
  newly referenced is started, one whose fragment or whose stored
  secrets changed is stopped and rebuilt (so a rotated credential
  applies here too), one that is gone or no longer referenced is
  stopped, and an unchanged one keeps the connection it had. No session
  is dropped: a conversation in progress picks the new world up on its
  next utterance. The reply carries the four outcomes by entry name and
  the whole status document, so one round trip both applies and
  verifies. Nothing is stopped or started until every manager the new
  configuration needs has been built, so a refusal (an unset `$VAR`, a
  credential that will not decrypt, an egress declaration
  `server.local_only` forbids, a snapshot that will not validate)
  changes nothing at all; a server that merely will not connect applies
  as `down` with its reason, revivable as ever. One reload runs at a
  time, and a concurrent one is refused with the same retryable 409 a
  contended write answers with.

- **An agent can be granted single tools of an MCP server** (#121): an
  `mcp` list entry is either the entry name on its own, which is the
  whole server as before, or an object naming the server and the tools
  of it that layer may reach (`{server: home, tools: [turn_on_light]}`),
  on agents and on `agent_defaults` alike. Tools are named the way the
  model is given them minus the entry prefix, which is the name
  `samtal-server config status` prints, so what the server called a tool
  before the publishing rule got to it never has to appear. It is an
  allow list and there is no deny list, since a denied set would
  silently grant whatever the server adds next. The grant filters what
  the agent is offered and is checked again when a call arrives, so a
  tool an agent was not offered is refused rather than run. A name the
  server does not publish is logged when its tools come out of the
  publishing rule, compared against what published rather than what was
  listed, and the status surface shows each agent's allowed tools beside
  the published ones.

### Changed

- **Three tool decisions are documented where they are met** (#121):
  that the builtins are outside the grant model, each appearing under a
  structural condition (`switch_agent` when the device is bound to more
  than one agent, `remember` when memory is configured) because
  withholding either per agent would strand a handoff or leave an agent
  recalling and never learning; that an SSE-only server is configured as
  a stdio server behind an `mcp-proxy` bridge, there being no native SSE
  arm now that the specification has deprecated the transport in favour
  of streamable HTTP; and that a tool result is speakable text, with
  content of any other kind rendered as a named placeholder rather than
  dropped, so the model can say what it received, until a display path
  that renders more than speech makes structured results worth carrying
  to the board. The server README's tools section carries all three, the
  two MCP server examples carry the bridge, and the generated reference
  carries a sentence of each on the `mcp`, `transport` and `mcp_servers`
  descriptions.

- **MCP writes name the reload instead of a restart** (#121): the entry
  writes and deletes, and the writes and clears of the secret slots on
  them, answer with a notice naming `samtal-server config reload`.
  Provider writes, provider secrets and agent writes keep the restart
  sentence, the last of them because an agent fragment mixes the
  reloadable `mcp` list with a prompt, providers and filler that are
  not. The CLI's `--local` recovery path keeps it for every entity,
  since it runs where there may be no server to ask.

- **The MCP streamable_http transport left the SDK's deprecated
  client** (#98): the transport now builds its own `httpx.AsyncClient`
  with the configured headers and the HTTP policy the SDK's factory
  used to supply for it (redirects followed, a 30 s timeout with a
  300 s read for a stream a server holds open), and hands that client
  to the SDK's replacement transport. Nothing changes for a running
  deployment; what changes is that the transport is no longer one SDK
  release away from breaking, and that the header-delivery test no
  longer has to ignore a deprecation warning to make its point. The
  `mcp` requirement moves from `>=1.2` to `>=1.24,<2`, since 1.24 is
  the first release carrying the replacement and MCP 2 needs httpx 2
  and yields a transport this client does not unpack, which makes
  migrating to it separate work.

- **The hardware checkpoint's findings are in the notes and the
  AMOLED guide** (#40): the captive portal's Custom OTA URL field is
  vendor-build-dependent rather than a firmware-version threshold
  (present on the Waveshare factory AMOLED-2.16 image at 2.2.4,
  absent from stock 2.4.0 on the Touch-LCD-1.54); the firmware's OTA
  client does not follow redirects, so a slashless OTA URL against a
  redirecting server shows `code=307` and restart-loops, which is why
  a device-facing endpoint must serve the slashless spelling
  directly; no portal re-entry was found on a provisioned factory
  AMOLED, so repointing a provisioned board is the NVS-over-USB
  route, not a portal retype; and the activation ceremony is
  validated end to end on both boards against samtal-server, code to
  connected with no power cycle. The AMOLED guide gains the factory
  launcher's behavior (the assistant is the AIChats app), the three
  labeled buttons, the white-screen quirk, and a portal-first
  onboarding section.

### Fixed

- **The short onboarding path answers with and without its trailing
  slash** (#40): a captive portal saves the typed URL as it likes, and
  the first factory board onboarded over this route saved it without
  the slash, POSTed to `/x/<key>`, and refused the redirect the server
  answered with, showing `code=307` on screen and restarting in a loop.
  Both spellings are now served by the same handlers, so nothing a
  device reaches depends on a redirect being followed. A wrong key
  still answers the same 404 as any unserved path on either spelling.
  Typing the URL with its trailing slash is still what the banner and
  the docs print, but it is no longer load-bearing.

### Security

- **An MCP server can no longer write to the logs** (#98): the SDK's
  HTTP client narrated what the far end said, including the session id
  it chose, the raw body of an initialization result that would not
  parse, and a traceback quoting the bytes that failed, all of which
  reached the collected JSON log of any deployment with an HTTP MCP
  server configured. Those records now stop at the SDK's own logger,
  and this server's unavailability warning prints a reason built from
  exception types instead of the exception's message, which a broken or
  hostile server writes half of. The line an operator greps for is
  unchanged and still names the server and the kind of failure, for
  example "mcp server weather is unavailable, its tools are absent:
  ValidationError". Debug logging is unaffected and still prints
  response headers from every HTTP client in the process, MCP servers
  included.

## 2026-08-12

### Changed

- **`server.websocket_url` refuses a URL carrying credentials** (#40):
  the OTA endpoint renders this value verbatim in the reply to a GET,
  and the short onboarding path serves that same GET, so a
  `user:password@host` written here was readable by anyone holding the
  onboarding URL. It is now refused at load, without the value being
  quoted back. This is a behavior change for a configuration that was
  already leaking, and no other websocket URL is affected.

- **Binding a device no longer needs a restart** (#40): a running
  server now reads the `devices` table and `default_agent` as a device
  asks for them, so binding a board, unbinding it, or changing the
  default agent applies at that device's next OTA check or connection.
  Everything else stays a boot-time snapshot with its restart notice
  unchanged, and the acknowledgement for a device write says which of
  the two happened: it names the restart when the binding points at an
  agent this server has not loaded, which is what a binding written
  after boot to a newly created agent does. A conversation already
  running is never touched, and a delete stops the next token and the
  next connection rather than reaching into one in progress. The read
  runs off the event loop through a connection of its own that never
  migrates and takes no write lock, so an operator's write cannot stall
  a device's check-in, and a database that cannot be read falls back to
  the configuration the server started with, saying so in the log. The
  configuration API's description, its acknowledgement schema, the
  committed OpenAPI document and the server README carry the two
  notices.

- The word "persona" is gone from the server's own voice: model
  docstrings and field descriptions, the generated domain reference
  and OpenAPI document, the example config's memory comments, two
  code comments, both READMEs, and the example fragments now say
  agent, matching the terminology decision recorded in
  `docs/concepts.md` and the glossary. Historical records (old
  changelog entries, plans, issues) keep their original wording, and
  tests keep their internal persona identifiers.

### Added

- **`samtal-server config ota-url` and `samtal-server config doctor`**
  (#40): the two commands the onboarding ceremony is run with.
  `ota-url` prints the URL to type into a board's captive portal and
  contacts nothing at all: no server, no database, no API token. It
  reads the same config file the server reads, takes the device
  authentication secret from the same environment variable, and derives
  the key and the origin with the server's own functions, so the string
  it prints is the string that server answers on; the URL goes to
  stdout alone and the guidance goes to stderr, with an origin nobody
  configured reading as the guess it is. Onboarding turned off names
  `server.ota_path` without quoting the segment, a missing secret names
  the variable, and a key pinned under `server.onboarding.key` prints
  its URL with no secret at all. `doctor` GETs that URL, or one given
  as an argument, and reports what a device would be told: nothing
  answers there, something other than samtal-server answers,
  samtal-server answers but sends devices to a plain `ws://` URL from
  behind TLS (the proxy misconfiguration that fails at the handshake
  with every other line looking right), or it is healthy, naming the
  websocket URL, server version and protocol version it reported. It is
  a GET and never a POST, so it mints no activation code and spends
  none of the issuance budget, and it carries no bearer token, since
  the token issuer cannot require one. Both READMEs and both example
  configs are rewritten around the ceremony, and the deployment
  profile's advice to inject the OTA path segment from a secret store
  is replaced by the derived key, with the legacy `ota_path` kept
  documented for boards already carrying one.

- **Onboarding a board by the code it shows** (#40): a device the
  database holds no binding for, on a deployment with no default agent
  to cover it, is now answered at its configuration check with a
  six-digit activation code instead of an empty token. Stock firmware
  shows that code on screen, speaks it digit by digit, and polls the
  server every three seconds, so an operator reads the code off the
  board in front of them, runs `samtal-server config add-device CODE
  AGENT`, and the board connects seconds later with no power cycle and
  no button press. `samtal-server config pending` lists the boards
  waiting, with the model and firmware version each reported, which is
  what tells two boards on one desk apart; `add-device` sits beside
  `bind-device`, which still takes a MAC you already know. The two
  routes behind them are `GET /api/devices/pending` and
  `POST /api/devices/pending/{code}`, both in the committed OpenAPI
  document, and the claim writes through the same repository call
  binding by MAC uses, so its reference checks and its acknowledgement
  are the same. Nothing changes for a deployment with a default agent
  set: it covers every unknown MAC by design, so its devices keep
  receiving a token and no activation object. Codes last ten minutes,
  are re-issued at the next check-in after that (the board displays
  whatever the fresh reply carries), are forgotten when the server
  restarts, and are bounded both in how many devices may wait at once
  and in how many may be minted per ten minutes, so an unauthenticated
  endpoint cannot be made to mint without limit. A version-2 activation
  poll is answered by the same ceremony: its HMAC is computed with a
  key burned into the device, which this server has no copy of and
  cannot verify, so what is checked instead is that the body parses,
  names a known algorithm, and echoes the challenge this server issued.

- **A short onboarding path and a startup banner** (#40): the OTA
  endpoint is now also served at `/x/<key>/`, where the key is eight
  base32 characters derived from the device authentication secret, so
  onboarding a board means typing eight unambiguous characters into its
  captive portal instead of a long random path segment. Nothing about
  the key is configured or stored: it is stable across restarts and
  rotates only when the secret does, `server.onboarding.key` pins the
  previous one across a rotation, and a wrong key answers the same 404 a
  path that was never served answers while logging the attempted key
  beside the correct one, so a typo diagnoses itself. With device
  authentication off there is no secret, so the route is served keyless
  at `/x/`. `server.onboarding.enabled` (on by default) switches the
  route off, `server.ota_path` accepts null to unmount the legacy route,
  and unmounting both refuses the boot. The server now prints the whole
  URL to type at startup and on a GET of the OTA endpoint, naming the
  origin it came from: the new `server.public_url`, else the origin of
  `server.websocket_url`, else the listen address, which reads as the
  guess it is. Legacy OTA behavior is unchanged; the short route and the
  startup line are what an upgrading deployment sees.

- **The stock activation ceremony, documented** (#40):
  `docs/xiaozhi-notes.md` now records the 6-digit code flow
  reconstructed from the vendored firmware and manager-api sources
  (the optional `activation` object, the `/activate` poll and its two
  `Activation-Version` forms, the challenge that makes polling fast,
  and the loop that makes binding take effect without a power cycle),
  plus why the OTA URL is the only place a stock board can carry a
  secret, and that the OTA response cannot change the device language.
  The captive-portal note is corrected: current factory firmware has a
  Custom OTA URL field on its Advanced tab, which v2.4.0 lacked.

- **Device guides** (#93): `docs/devices/`, one user-facing guide per
  supported board plus a common page for what every board running the
  upstream firmware shares. The Touch-LCD-1.54 guide is written in
  full (the complete control inventory read against the board support
  code, including the gestures the old firmware README never listed
  and the idle-only echo-cancellation toggle that takes barge-in with
  it; the wake word with an exact account of what the server does and
  does not learn; the voice commands the board publishes as MCP tools;
  the display's two power-saving timers with their real conditions);
  the ePaper and AMOLED guides are stubs marked 🚧 whose every section
  names its source, hands-on or read from the firmware. The
  "Using the device" section moves out of `samtal-esp32/README.md`,
  correcting on the way its claim that the listening behavior it
  described was universal, and leaves a pointer behind. Both hardware
  tables link the guides as `guide · wiki`, the root README's Getting
  Started sends the reader to their board's guide for which button to
  press, and `docs/README.md` indexes the new directory.

- `docs/concepts.md`, the domain model from the user's point of view:
  device, agent, binding, conversation, session, and users as a later
  stage, with the decided semantics. The wake word wakes the device
  and the default agent answers; a mid-session switch lasts for the
  session and starts clean by default, carrying context only on
  explicit request; conversations suspend rather than end, can resume
  from another device, and are cross-linked with the sessions that
  touched them, which makes the session transcript its own artifact;
  turns that are only meta requests are recorded as session events,
  not conversation entries; memory stays agent-keyed with a planned
  shared user profile; meta questions are built-in tools in every
  agent with conversation search scoped to the asking agent; a
  built-in help agent explains the device and the system, reading
  observed device facts (#96) and the per-board guides (#93). The
  glossary gains entries for the new nouns and links them to the
  page.

- **Documentation for the configuration API** (#101). The server README
  gains a section on it under Configuration: what mounts at `/api`, the
  bearer token and how to generate one, the routes a noun at a time,
  what a masked read adds to an entity, the acknowledgement that says
  when a write applies, the refusal mapping, and
  `docs/reference/api-openapi.json` as the contract. The
  `samtal-server config` commands are documented as the client they now
  are, with the order they resolve the URL and the token in and exec
  into the running container as the deployment story, and `--local` as
  the break-glass path with its subset and its notice.

  Deployment notes beside the ones for the configuration database cover
  the four decisions a deployment has to make: setting
  `SAMTAL_API_SECRET` before rolling the image rather than meeting the
  boot error, what to do with `/api/` at the edge (do not route it
  outward, route it separately and restrict it, or accept that the token
  is the only thing in front of it), loopback-or-TLS for the whole API
  because the token rides on every request, and the recovery procedure
  in full.

  The generated domain reference now says that those commands are a
  client of the API and points at its document and its schemas; the root
  README's Getting Started gains the from-outside case beside the
  from-inside one it already had.

### Fixed

- Documentation that the API's arrival had made untrue (#101): the
  server README's list of what the server exposes and its "nothing else
  is exposed" paragraph, neither of which mentioned `/api/` or the rule
  that keeps the OTA endpoint out of it; the Stack section's "future
  admin API"; the promise that hot apply is what the admin API will
  bring, when the API answers with the same restart notice; and
  `config.example.yaml`'s comments about who writes the database, one
  of which offered `config list` as an example of a command that opens
  it directly, which it no longer is.

- `docs/concepts.md` claiming that the protocol version, the feature
  map and the device's own MCP tool list all arrive at `hello` (#109):
  the tool list comes from a separate background MCP handshake started
  after the hello, which a first utterance can beat, so the Observed
  facts bullet now names the arrival phases in wire order (OTA
  check-in, hello, background discovery, first listen, `listen`
  `detect`) and defers to the protocol notes for the race.

- The same bullet's "today these are parsed and dropped" (#109), which
  understated retention: board model and firmware version cross the
  OTA-to-session boundary in the bounded in-memory cache, which a
  session reads into its manifest when capture is enabled, and the
  protocol version, the discovered MCP tools and the listening mode
  are retained for the life of the session, the protocol version also
  entering an enabled capture's manifest. Only the wake-word report is
  merely debug-logged, and what none of it enters is a durable,
  queryable per-device record (#96).

- `docs/xiaozhi-notes.md` saying that the server "learns only that a
  session opened" when a wake word fires (#109). The trigger audio is
  genuinely unreachable, since ESP-SR decides on-device, but the
  firmware sends `listen` `detect` with the fired word in `text`,
  which `device/session.py` receives and debug-logs; the entry now
  separates the constraint from the report and gives the report its
  tier.

- The glossary's Manifest entry (#109), which listed "device,
  firmware" and named neither the client ID, nor the board model
  cached from the last OTA check-in, nor the session's protocol
  version, all of which the manifest actually carries.

### Removed

- The pipecat spike code under `spikes/pipecat-alignment/`, 3,714
  lines across 15 files, throwaway by the declared policy of its own
  plan and now that its findings are written down. The record of
  reference is unchanged: `docs/plans/2026-08-11-pipecat-alignment-spike.md`
  and its implementation doc still hold both gate verdicts, the line
  counts, the 23-item seam-obligation map, the offsets and drift
  figures, and the pinned pipecat-ai 1.7.0. The code stays recoverable
  from the annotated tag `spike/pipecat-alignment`, on the last commit
  that contains it, and from PR #90. Removing it costs nothing
  structural (no README, AGENTS.md or workflow referred to the
  directory, and no CI step ran it) and settles a standing ambiguity:
  a second uv project beside `samtal-server/` reads as a component,
  and unlinted code pinned to a release it can no longer be measured
  on only drifts. The pipecat issues stay open on their own terms and
  none of them is v1 work: #84 is a decision record that already
  concludes "not now", #91 promotes the serializer as an external
  showcase that never touches `samtal_server/`, and #92 is a design
  note whose later stages wait for a driving use case.

## 2026-08-11

### Added

- **The configuration API writes** (#101). Every write the
  `samtal-server config` commands can make is now a route under `/api`:
  PUT and DELETE per entity kind, the two write-only secret endpoints,
  the device binding, and the default agent with its idempotent clear.
  A successful write answers with what it did and the sentence saying
  when it applies, because configuration stays a boot-time snapshot.
  Fragment bodies are received as opaque objects and validated only in
  the repository, so the one validator that could echo a rejected body
  back (and with it a pasted credential) is never in front of them; the
  three argument-shaped bodies are parsed to an exact shape and refused
  with a description of what was expected rather than a quote of what
  arrived. The committed OpenAPI document
  (`docs/reference/api-openapi.json`) carries every write, the schema
  each body takes, and the refusals each can answer with.

- **`samtal-server config --local`** (#101), the break-glass recovery
  subset: `show`, `delete`, `clear-secret` and `set-secret` against the
  database directly, for a deployment whose server will not start. Every
  `--local` invocation says on stderr that it bypasses the API and that
  a running server will not observe the change until its next start;
  every other command refuses the flag by naming the four. Its `show`
  and `delete` go by what is stored rather than by what a new write
  would be allowed to create, so a row that predates the addressability
  rule stays readable and removable.

- **The configuration API reads** (#101). Every read the
  `samtal-server config` commands can do is now a GET under `/api`:
  `/config` for the whole masked document with the location of every
  stored secret beside it, an identity-keyed listing per entity kind,
  one route per addressable entity, and `/default-agent`. A read
  answers with the entity's masked body and the slots holding a stored
  secret, each marked with the entity key its value displaces, which is
  the fact a masked read exists to convey and the one the entity itself
  can never carry. Reads are masked and stay masked: no secret value,
  and no plaintext that got into a row another way, appears in a
  response, a header or a log. The committed OpenAPI document
  (`docs/reference/api-openapi.json`) describes the routes, the
  refusals each can answer with, and the entity schemas a client needs
  to write an entity back.

### Changed

- **A secret-shaped key is refused anywhere inside a provider's
  options, not only at the top level** (#101). A provider entry passes
  its options through to the implementation, so an option can be a
  structure, and `connection: {api_key: ...}` was accepted, stored and
  read back verbatim by every display path. It is now refused when the
  fragment is parsed, with a message naming the dotted path and the
  rule rather than quoting the value. Being honest about the behavior
  change: a fragment that nested a secret-shaped key was never usable
  (nothing resolved it, and every read published it), and such a
  stored row now reports that it cannot be read as configuration
  rather than showing what it holds. The display path masks
  secret-shaped keys at every depth too, since it is the last thing
  between a row that got its contents another way and a caller.

- **A number that is not finite is refused in a fragment** (#101).
  YAML spells `.nan` and `.inf`; JSON does not, so such a value would
  be read back over the API as null, quietly turning the option into
  the provider's own default. Every fragment is checked at any depth
  when it is written, and a stored value that is not finite reports
  that the row cannot be read rather than answering with a value
  nobody wrote.

- **A stored row that cannot be read as configuration is reported as a
  server-side failure** (#101), which over the API means 500 rather
  than the 422 that says the caller sent something wrong. A row that
  fails the models is nothing a reader of it can fix, and it is the
  same situation as a column holding the wrong shape, which already
  said so. The message names the row and the fields that failed and
  never their values. Boot is unaffected: the storage refusal is a
  `ConfigError`, so a server meeting an unreadable row still refuses to
  start with the same sentence it printed before.

- **A name or a secret slot must be expressible as one URL path
  segment** (#101). An entity is addressed by putting its identity in
  a path, so a name or a slot holding a slash or a control character
  could never be fetched, replaced or deleted over the API. Both are
  now refused when they are written, naming the rule and the kind of
  character rather than the value; an MCP slot's key half must
  additionally be an environment variable name after `env.` or an HTTP
  header name after `headers.`, which is what the value would have
  referenced. Spaces, percent signs and characters outside ASCII stay
  legal and percent-encode losslessly. The rule runs at write time
  only: a row written before it still boots, still appears in
  `config show`, and is still deletable.

- **`samtal-server config` is a client of the configuration API**
  (#101). The grammar is unchanged and every message is unchanged: the
  API carries the repository's own sentence and the CLI prints it. What
  changed is that a command now needs a running server to talk to. It
  finds it at `--api-url`, then `SAMTAL_API_URL`, then
  `http://127.0.0.1:<server.port>/api`, and authenticates with the value
  of the variable `server.api.secret_env` names, which on a deployment
  is the variable the server itself was started with: exec into the
  running container and the CLI has the address and the token for free.

- **The API client refuses to send the token in clear** (#101). The
  bearer token crosses every request and grants everything the API can
  do, secret writes included, so a plain `http://` connection to a host
  that is not this machine is refused outright, with no flag to override
  it: use TLS, a tunnel that terminates it, or loopback from inside the
  container. A URL carrying a username or password is refused too, and
  no URL is printed with either still in it. A non-2xx response that is
  not this API's own is reported as a status code and never relayed,
  because what a proxy returns is not sanitized output.

- **Getting Started reorders to start, configure, restart** (#101).
  Configuring requires a running server, and an empty database is a
  valid state to run one on: it comes up serving no agents, is
  configured over its own API, and picks the configuration up at the
  restart, which is now a documented step of its own rather than a
  sentence at the end of another one.

- **The smoke lane's seeding scripts manage their own server** (#101).
  `seed.sh`, `seed-slim.sh` and `seed-local-engines.sh` each start a
  server inside the seeding container, wait for `/healthz`, write
  through it over loopback with the image's own CLI, and stop it again,
  printing the server's log if anything fails. The seeding containers
  are given the two secrets a server needs, and no provider credential,
  because an empty domain half builds no provider.
  `config.deploy.example.sh` is rewritten to run against a running
  server, and both sets of script tests move to the integration lane.

- **Every deployment must set `SAMTAL_API_SECRET` before upgrading**
  (#101). The configuration API is always mounted and always behind a
  bearer token, deliberately without an `enabled` flag, so a server
  started with that variable unset or blank refuses to boot. The
  refusal names the variable, prints
  `SAMTAL_API_SECRET=$(openssl rand -hex 32)`, and says that the token
  grants everything the API can do, so it belongs on a loopback
  connection or behind TLS. `server.api.secret_env` renames the
  variable for a deployment that wants another one.

- **`server.auth.secret_env` and `server.api.secret_env` must hold the
  name of an environment variable** (#101), the rule a provider's
  `*_env` keys already follow. Pasting a secret into either key is now
  refused when the configuration is read, with a message that says
  what the key must hold and shows an example rather than quoting what
  was written. This is a behavior change and worth being honest about:
  a pasted value never worked, because the name is looked up in the
  environment and no lookup of a pasted secret succeeds, so such a
  configuration was already failing, only later and while echoing the
  paste.

- The repository's refusals carry types (`UnknownEntityError`,
  `DatabaseBusyError`, `StorageError`, all subclassing `ConfigError`)
  so that the API can map a refusal to a status code without reading
  its message (#101). No message text changes anywhere, and the CLI
  prints exactly what it printed before.

- **The server boots its domain half from the database** (#86). The
  YAML file keeps `server:` and `memory:`; providers, MCP servers,
  agent defaults, agents, devices and the default agent now come from
  the database `samtal-server config` writes, read once at boot and
  composed onto the file half into the same validated shape the server
  has always used. Nothing about a conversation changes; where the
  configuration comes from does.

  **A domain section left in the YAML file, or a `SAMTAL_` override for
  one, refuses the boot** naming the key, the command that writes it
  now, and the reference. A configuration that silently stopped
  applying would be worse than one that will not start, and
  pydantic-settings ignores unmatched prefixed variables, so the
  environment is scanned explicitly for the six moved names.

  `config.example.yaml` shrinks to the server half, with its header
  pointing at the config commands, the generated reference and the
  example fragments; `config.deploy.example.yaml` keeps its profile's
  domain half as the commands that write it, values and reasoning
  intact. The container image sets
  `SAMTAL_SERVER__DATABASE__DIR=/data/db`, so the database lives on the
  data volume beside the model caches and the memory files, and the
  smoke lane seeds each image check's configuration through the CLI
  from the image itself before the container starts.

  Deployment notes for it are in `samtal-server/README.md`: generating
  and escrowing `SAMTAL_MASTER_KEY`, rotation adding a key without
  being able to retire one, WAL-safe backups (`VACUUM INTO` or
  `.backup`, or a plain copy only of a stopped and checkpointed
  database), what a restore needs, and what a copy of the file does and
  does not expose.

### Removed

- The staging notice on every mutating `samtal-server config` command,
  and the staging paragraphs in the generated reference and the
  examples README (#86). The window they existed for closed when the
  server started reading the database. Mutating commands now print that
  the write applies at the next server start, which is the trap a
  boot-time snapshot does have.

### Added

- **A REST API for the domain configuration, mounted at `/api`**
  (#101), as a skeleton: the namespace exists, is gated, and has no
  routes yet. Every request under it must carry
  `Authorization: Bearer <token>` whose value is the environment
  variable `server.api.secret_env` names, and one that does not is
  refused with 401 whether or not the path it asked for exists, since
  only an authenticated caller gets to learn which routes there are.
  Refusals from the repository keep their exact wording and gain a
  status code: 404 for a missing entity, 409 for the retryable busy
  lock, 500 for stored state that cannot be read, 422 for everything
  else. No request body and no traceback is ever quoted back.

  `samtal-server config openapi` prints the API's OpenAPI document,
  generated from its routes and committed at
  `docs/reference/api-openapi.json`, where CI regenerates and diffs it
  the way it already does the domain reference. Like `config schema`
  and `config reference` it is read-only: no configuration file, no
  database, no encryption key, no token.

  Nothing about a device conversation changes, and the device-facing
  endpoints are structurally absent from the API's document: the API
  is a second application mounted on the server's port, not a router
  on the one that serves devices.

- Generated documentation for the domain configuration (#86). Every
  domain model field now carries a description, and three renderings
  come from that one source: `samtal-server config schema [entity]`
  prints JSON Schema, `samtal-server config reference` prints the
  markdown reference (committed at `docs/reference/domain-config.md`),
  and the `config set` commands list their fragment's fields in
  `--help`. Both new commands are read-only: no database, no
  configuration file, no encryption key.

  The long-form narrative that documented these settings in
  `config.example.yaml` (the measured latencies, the field findings,
  the tuning advice) moves to richly commented per-entity fragments
  under `samtal-server/examples/`, one file per entity or provider
  type, each naming the `config set` command that installs it and each
  run through that command by the test suite. `config.example.yaml`
  itself is unchanged in this release and still carries its domain
  sections; the switchover is what removes them.

  CI regenerates the committed reference and fails on any difference,
  and the workflow now also runs on changes under `docs/reference/`, so
  the committed copy cannot drift from the models.

- The repository and the write path for the DB-backed domain
  configuration (#86): `samtal_server/config/store.py` reads the
  database into the existing pydantic models and writes fragments back
  through them, and `samtal-server config <command>` is the CLI in
  front of it. **The server does not read the database yet**: it still
  boots its whole configuration from the YAML file, so every mutating
  command prints a staging notice saying that this write takes effect
  at the switchover and changes nothing about the running server.

  The grammar covers one noun per entity kind: `set` for providers, MCP
  servers, agents and agent-defaults, `delete` for providers, MCP
  servers, agents and devices, `bind-device`, `set-default-agent` and
  `clear-default-agent`, `set-secret` and `clear-secret`, `list`, and
  `show` with every stored secret masked and every environment
  reference it shadows marked. A fragment is the same
  YAML shape the section has in the file today, read from a path or
  from stdin, and `set` replaces the entity without touching its stored
  secrets. A secret never passes through an argument: `set-secret`
  reads stdin, without echo at a terminal, or names the variable
  holding the value with `--from-env`.

  Every write validates the resulting snapshot's references inside one
  `BEGIN IMMEDIATE` transaction, so an agent naming an unknown
  provider, a device bound to an unknown agent, or deleting something
  still referenced is refused, and two concurrent writers cannot each
  validate against the state before the other's change. Completeness
  rules stay at boot, so a deployment can be built up from an empty
  database in the natural order without wedging.

  `server.database.dir` (`SAMTAL_SERVER__DATABASE__DIR`, default
  `/var/lib/samtal`) is where the database lives, read by the CLI
  through the same settings machinery the server reads its own
  configuration with. `verify_secrets` is implemented and tested but
  not yet called: the switchover is what puts it on the boot path.

- The storage foundation for the DB-backed domain configuration (#86):
  `samtal_server/db/` opens, configures and migrates a SQLite database
  holding the domain half of the configuration, and
  `samtal_server/config/secrets.py` defines the envelope its secrets
  are stored in. Nothing reads or writes it yet: no server behavior
  changes, and the YAML file is still the only configuration the
  server boots from.

  `open_database` creates the directory when it can, opens the file in
  WAL mode with a busy timeout, and runs the packaged Alembic
  migrations, so a fresh data volume becomes a current database with no
  init command to forget. Every transaction begins with `BEGIN
  IMMEDIATE`, which is what stops two processes racing the baseline
  migration and, later, two CLI writes interleaving their
  read-validate-write. Opening deliberately does not verify stored
  secrets: a missing or wrong key is exactly when the CLI has to stay
  usable as the recovery tool.

  A stored secret is either an environment reference, unchanged from
  today (`api_key_env`, `$VAR`), or Fernet ciphertext under
  `SAMTAL_MASTER_KEY`. The encrypted payload carries the secret
  together with its canonical location, and decryption refuses a token
  whose location is not the slot being read, so a valid token copied
  into another entity's row does not decrypt there. The key variable
  holds one or more keys, newest first: the newest encrypts, decryption
  tries them in order, and until a re-encrypt command exists every old
  key must stay configured while any token written under it is stored.

  CI now also builds the wheel, installs it into a scratch environment,
  and migrates a fresh database from the installed artifact alone, since
  a test run from the checkout cannot show that the migration scripts
  were packaged.

- A throwaway spike under `spikes/pipecat-alignment/` that measures
  whether pipecat could sit behind samtal's device edge, and answers
  it with numbers (#89). It runs a minimal pipecat 1.7.0 pipeline
  (Silero VAD, a canned reply, no LLM and no cloud) behind a xiaozhi
  frame serializer on pipecat's FastAPI websocket transport, driven by
  the unmodified xiaozhi-sdk device simulator, and runs the
  repository's existing `scripts/echo_leakage.py` analysis unmodified
  over the capture it produces. It is its own uv project so its
  dependencies never touch the server's, and nothing from it lands in
  `samtal_server/`.

  **Gate 1, capture alignment: passed**, at 250 ms and 1500 ms injected
  delay alike. A capture built on pipecat recovers a known echo's delay
  to 1.2 ms and its gain to 0.2 dB, in all 125 candidate windows per
  delay, with a lag IQR of zero and no measurable drift over two
  minutes. Three qualifications come with it. `AudioBufferProcessor`
  offers two bot tracks and only `on_bot_turn_audio_data` works: the
  delivered track, the one whose arrivals can be timestamped, is
  silently corrupted whenever the device streams microphone audio
  during a reply, because the processor pads the bot track up to the
  user track at every delivery, turning 126 s of reply into 150 s with
  13 s of silence inserted inside continuous speech. The track that
  works arrives once per turn, so it serves offline echo measurement
  and not a real-time AEC reference. And the processor has to record at
  the native output rate, because asking it for the capture rate puts
  its never-flushed streaming resampler in the path and truncates each
  turn's tail.

  **Gate 2, adapter size: not passed, and not failed.** The
  adoption-required adapter is 312 lines against 899 for the whole
  bespoke edge, but against the comparable slice of it that this
  exchange actually exercises it is 312 against 222, or 154 against 155
  counting code only: the same size, not smaller, while covering 8 of
  the seam's 23 obligations and speaking xiaozhi protocol v1 only. Its
  shape stayed message translation, which is the half of the answer
  that would have condemned it.

  Consequence: adopting pipecat for the streaming conversation runtime
  (#31) is a genuine tradeoff to be argued with these numbers rather
  than a question settled either way. Along the way the spike
  established that pipecat's websocket transport does pace output at
  real time, and that whether its recording is wire-aligned depends
  entirely on which recording API the adopter picks and at what rate.

  An external review of the spike's own pull request caught two
  measurement errors that had produced an earlier, wrong, "gate 1
  failed" conclusion: tap packets placed at the end rather than the
  start of their playout slot, and two different resamplers where the
  method required one. Both are recorded in the implementation doc
  along with the reversal.

## 2026-08-10

### Changed

- The device edge and the conversation runtime are now separate
  packages with an explicit interface pair between them (#85). What was
  one 2,138-line `session.py` owning both sides is
  `samtal_server/device/` (the xiaozhi handshake, Opus codecs, framing,
  frame pacing, capture, the device MCP transport, session limits and
  the idle watchdog) and `samtal_server/runtime/` (endpointing, the
  barge-in gate ladder, the filler, ASR, the LLM tool loop, sentence
  splitting, speech synthesis and its lookahead, history and agent
  handover). They meet at `device/boundary.py`: a `SessionInput` the
  edge feeds and a `DeviceOutput` the runtime drives, both described in
  device terms, with reply audio crossing as an opaque batch so the
  runtime never learns Opus. The bespoke pipeline is built for each
  connection by a factory assembled at startup, which is the seam a
  second runtime plugs into.

  A pure refactor, with one deliberate exception stated in the
  implementation doc: no event name, field, reason or ordering changes,
  no wire bytes change, and the `logger` field stays
  `samtal_server.session` on every conversation record, which a
  characterization test now pins. The whole integration lane passes
  with a single import line changed.

### Fixed

- A device that disconnects while one of its own MCP tools is being
  called now reaches the conversation runtime as the boundary's
  `DeviceGone` rather than as the websocket transport's exception,
  which is what every other outgoing message already did (#85). The
  runtime's observable behavior is unchanged: `DeviceGone` subclasses
  `RuntimeError`, so the tool loop turns it into the same error result
  and the same `tool_call` event as before.

- The principles page now separates product promises (falsifiable
  commitments to the person running samtal) from the architecture
  principles that keep them, with an explicit precedence rule: when
  the two conflict, the promise wins. Thin device smart server moved
  to the promises, and the declared-egress principle folded into the
  local-first promise as its enforcement mechanism.

### Added

- An ADR recording the device-facing boundary decision
  (`docs/adr/2026-08-10-normalize-the-hardware-edge.md`): interfaces at
  samtal's core describe device capabilities, the xiaozhi edge is
  normalized once, runtimes stay themselves behind it, there is no
  universal `ConversationBackend`, and the decision sites keep their
  reasoned events. The three architecture principles it supports now
  cite it.
- The implementation doc for that extraction
  (`docs/plans/2026-08-10-device-facing-session-boundary-implementation.md`).
- The accepted plan for extracting a device-facing session boundary
  (`docs/plans/2026-08-10-device-facing-session-boundary.md`): the
  interface pair, ownership map, and ten-commit sequence for splitting
  `session.py` into a device edge and a pipeline runtime with no
  behavior change. Reviewed adversarially in three rounds before
  acceptance; the review settled the send-path batch surface, event
  attribution, the pinned logger identity, and the commit ordering.
- Two product promises in `docs/architecture/principles.md`: stock
  xiaozhi firmware is the compatibility floor (a stock board holds a
  conversation without a reflash, bounded to the WebSocket transport,
  versioned by the firmware in the field, and a floor rather than a
  ceiling), and a fully local deployment is first-class (every core
  capability reachable with local providers, enforced by declared
  egress and `local_only`).

- The standing architecture principles as
  `docs/architecture/principles.md`: samtal's identity (own the
  appliance, let conversation runtimes stay themselves), the
  device-facing boundary with its phone-call litmus test, the
  runtimes-are-siblings rule with the universal-abstraction
  anti-goal, thin device smart server, reason-annotated decision
  sites, and the declared-egress guarantee, each principle with an
  example and a counterexample. Pointers added from `AGENTS.md`, the
  docs index, and the ADR README, whose division of labor gains a
  fourth clause: issues hold evidence, ADRs hold decisions, plans
  hold execution, principles hold direction.

## 2026-08-09

### Fixed

- The conversational filler talking over the user. The filler timer
  checked only whether reply audio had started before playing its
  clip, never whether the user was speaking, so when the endpointer
  ended a turn at a mid-sentence thinking pause (common in
  dictation-style turns: "guarda en la memoria que...") the clip
  fired into the user's continuation. Field round 2 measured 4 of 20
  fires landing 1.4 to 1.8 s into speech already underway, one of
  them 52 ms before the barge-in it was firing into cancelled the
  reply it masked. The timer now stands down at fire time, with a
  `filler_skipped` event, when the endpointer holds unresolved speech
  or a barge-in confirmation has the outgoing frames paused
  (`docs/features/2026-08-09-filler-yields-to-live-speech.md`).

### Added

- Five turn-taking entries in the glossary (continuation, gate
  ladder, premature endpoint, structured event, and a sharpened
  refractory period), extracted from the field analysis of the
  latency-masking deployment so the vocabulary the analysis leans on
  is addressable from issues and PRs.
- Three end-of-turn entries in the glossary (end-of-turn detection,
  prosodic cues, semantic completeness) with worked examples, and
  examples added to the continuation, gate ladder, endpointer, and
  trailing silence entries, so the turn-taking discussion in the
  issues reads without a linguistics background.
- The echo leakage measurement as committed tooling
  (`scripts/echo_leakage.py` and `scripts/echo_leakage_control.py`),
  so the analysis that settled #48's headline question can be re-run
  on any future captures instead of living in a session's scratch
  space. The measurement cross-correlates a capture's microphone
  channel against its paced-reply channel over windows where the
  assistant plays and the user is silent, reporting the leakage and
  path delay per session and per voice, and the detectability bound
  where nothing is found; the control injects a known synthetic echo
  and refuses a passing verdict unless the measurement recovers it,
  which is the condition for trusting any null result. Both verified
  to reproduce round 1's figures from the original captures.

## 2026-08-08

### Fixed

- Every cloud ASR call failing at connect time on the build carrying
  the echo retry (#75). The retry work passed `Omit()` as the
  per-request timeout on the ordinary path, matching the idiom of the
  neighbouring form fields; but timeout is a client option, `Omit` is
  not a `NotGiven` to the SDK, and the sentinel instance reached httpx
  as the literal connect timeout, failing every transcription in
  milliseconds. The fix is the SDK's `NOT_GIVEN` sentinel, plus a
  regression test pinning that every timeout value reaching the
  transport layer is a real number, which is the class of check a mock
  transport can actually make (it never connects, which is how the
  suite and a green deploy verification both missed the bug).

### Added

- Latency masking with pre-synthesized conversational fillers (#74).
  When a reply's first audio has not started within a configured
  delay of the utterance being transcribed (default 1800 ms, above
  the roughly 1.2 s a healthy reply takes to its first audio and
  below the 2 to 3 s of dead air field round 1 measured on ordinary
  turns), the session plays a short filled pause ("Hmm, let me
  see...") in the active agent's own voice, and the real reply queues
  behind the clip's tail. The clips are synthesized once at boot and
  cached as PCM, so the mask costs nothing at the moment it masks and
  keeps working when the TTS provider is the thing being slow; a
  synthesis failure logs a warning and leaves the feature off for
  that agent rather than failing the boot. The filler is honest
  assistant speech: it moves the device into its speaking state,
  counts as the turn's `speaking_started`, lands on capture channel
  1, and enters the barge-in gates like any reply audio. One filler
  per turn, logged as a `filler_played` event with the measured delay
  and the phrase index, and composed with the first-token watchdog:
  the filler is the soft early threshold, the watchdog the hard late
  one. Off by default, configured per agent (or in `agent_defaults`)
  as `filler: {enabled, delay_ms, phrases}` with the phrases written
  in each agent's own language.

- Two reference pages in `docs/`, distilled from the first field-test
  rounds (#48, #73). The
  [regression suite for conversational quality](docs/conversational-quality-regression-suite.md)
  explains why field tests exist, the shape of a conversation turn
  with the event each step emits, the preflight and artifacts of a
  test round, and the three layers findings age in: the instrument
  (stack-independent), the interaction layer (survives provider
  swaps), and the calibration (the stack-specific constants worth
  optimizing once a deployment settles). The
  [glossary](docs/glossary.md) gives one short definition per
  concept, technique, and technology the project is built on, with
  pointers for going deeper. Both are indexed from
  [docs/README.md](docs/README.md).

- A first-token watchdog on the LLM round (#68). Nothing used to bound
  the gap between sending a chat request and the first byte of the
  answer, so a provider that stalled there froze the session in
  `replying`, not listening, for as long as it cared to take: a field
  test saw a 17 s stall that only a barge-in rescued. The watchdog
  cancels a round whose first token has not arrived within
  `server.llm_first_token_timeout_s` (default 10 s, chosen against the
  field data: healthy first tokens cluster at 500 to 800 ms, the worst
  spike that still answered was 8.9 s) and retries it once, which the
  same field data says answers quickly; a second stall gives the round
  up as a `provider_failed` event with `error: FirstTokenTimeout` and
  the session returns to listening, so the failure mode is a silent
  turn rather than a wedged session. Only the wait for the stream to
  begin is bounded: a long generation that is already streaming is
  healthy and runs to the end, the provider adapters announce their
  first chunk off the wire so a round that streams nothing but a
  buffered tool call (a handover does) is not mistaken for a stall,
  and barge-in keeps working through the whole watchdog window exactly
  as before. The retry is its own structured event, `llm_retry`,
  documented in the server README's event table.

### Fixed

- An ASR transcript that comes back as the configured prompt is no
  longer taken as proof of silence (#69). The #54 guard discarded the
  echo outright, and the #48 field test showed it swallowing real
  speech: nine echoes in two days, every one on a clip of 0.78 to
  1.92 s, two of them a user answering "yes, please" to an offer and
  being ignored. The guard itself stands, an exact echo of the prompt
  is still never handed to the LLM as if spoken, but the clip is now
  transcribed once more with the prompt withheld: a real short answer
  transcribes fine without the prompt's help and is heard normally,
  while a retry that comes back empty or as the prompt again is
  discarded as before. Only the tripped guard pays for the second
  round trip; the normal path is one request per utterance, exactly as
  it was. The retry lives on what the first request left of the
  provider's `timeout_s` rather than getting a fresh timeout of its
  own, which would have quietly doubled the bounded wait that
  disabling client retries exists to keep: with under a second of
  budget left no retry is sent at all, and a retry the deadline cuts
  off discards the clip rather than surfacing an error. Each trip
  logs one `asr_prompt_echo` event (in the server README's event
  table) whose `outcome` tells `recovered` apart from
  `confirmed_empty`, `confirmed_echo`, `timed_out` and `skipped`, and
  whose `retry_ms` carries what the retry cost, so future field data
  can measure how often the guard was swallowing real speech. The old
  warning line ("treating
  N s of audio as nothing said") now appears only when the retry
  confirms there was nothing, so a log search for it keeps meaning
  what it meant.

## 2026-08-07

### Added

- The four stage acronyms are expanded where the server README first
  uses them. It opened by describing the pipeline as VAD, ASR, LLM and
  TTS without ever saying what any of them stand for, though those four
  names are the vocabulary of the whole configuration file. A table
  gives each in full with what it decides, and notes that they run
  strictly in order, which is why any one of them is latency the user
  hears. Separately, two unrelated options are both called `prompt`: a
  list of words the transcriber should expect, and the persona
  instruction sent to the LLM. The ASR one is a hint about vocabulary
  rather than a request for behaviour, which is exactly what makes an
  echoed prompt becoming an actionable utterance surprising, so both
  the README and `config.example.yaml` now tell them apart where
  somebody editing the value will be looking.

- A survey of the neighbouring projects, `docs/related-projects.md`, in
  two registers. Alternatives, the projects someone could choose instead
  of samtal, each answering the same four questions: what it is, where it
  overlaps, where samtal is deliberately different, and what samtal
  borrows. Rhasspy shares the premise (thin listening endpoints, a server
  you run, nothing leaving the house) and not the goal, matching a
  template grammar and emitting an intent where samtal puts a language
  model. Its successor line matters more than the archived 2.5 most links
  point at, because Wyoming and Home Assistant's Assist pipeline reach
  nearly samtal's picture from the other direction, differing in assuming
  a hub and in owning the wire protocol where samtal implements one it
  does not control. ElatoAI is the closest match to samtal's physical
  shape, same chip family and codec and transport, and the strongest
  argument for the road not taken: one vendor's realtime
  speech-to-speech API and a hosted account, against a staged pipeline
  that can run with no account at all. Hermes Agent is in the document
  because "voice" means voice messaging there, no wake word and no
  hardware, which makes it a candidate brain a layer above samtal rather
  than a rival beside it. The second register is what samtal is built
  from, saying what each dependency is and why samtal touches it, with
  the terms left in `THIRD_PARTY_LICENSES.md`: the upstream pair, the
  pipeline components, the `xiaozhi-sdk` device simulator the tests run
  on, and the wake word models on the device. Writing it turned up that
  samtal depends on the Rhasspy organisation twice over, for Piper voices
  and for `pysilero-vad`, while running none of Rhasspy. A closing list
  names the projects not yet read, so they are not rediscovered from
  scratch, and makes no claims about them.
- Architecture diagrams, and a walkthrough that teaches them.
  `docs/architecture/excalidraw/` holds a high-level overview, now
  embedded in the root README, and a detailed picture of one
  conversation turn across four lanes (human, device, server, external
  services); the editable originals are scenes in the team Excalidraw
  workspace and the committed files are their exports.
  `docs/architecture/README.md` embeds both and walks the detailed one
  step by numbered step, explaining each concept and the problem it
  solves and expanding every acronym (AEC, VAD, ASR, LLM, TTS, MCP)
  before using it, then closes by naming the two things a happy-path
  picture cannot show, interrupting a reply and which stages leave the
  host, pointing at the diagram for each.
- Three PlantUML diagrams under `docs/architecture/plantuml/`, whose
  source is text in the repository rather than an export of a hosted
  scene, so a pipeline change and the picture of it move in the same
  commit and a reviewer reads the diff. `architecture-overview` colours
  every provider by its `egress` class marking, which is the thing
  `server.local_only` is checked against at boot, so the picture
  answers "what leaves this host" from the same source the enforcement
  reads. `conversation-turn` is a sequence diagram, chosen because what
  matters in a turn is ordering and overlap: the tool loop's rounds and
  its final `tool_choice: none`, the next sentence being synthesized
  while the current one plays, and which listening mode re-arms the
  microphone. `barge-in-decision` is an activity diagram of the gates
  an utterance passes before it may cancel a reply, which is branches
  rather than flow and so could not be grafted onto the conversation
  diagram's happy path; it names both thresholds and the structured
  event every branch emits, those being what the thresholds get tuned
  from. Rendering is a local `plantuml` invocation documented beside
  them; the Excalidraw pair stays for the README's front door.
- The research notes record what running stock firmware costs the
  server, as the list to work from when the device side is tackled.
  samtal-server implements the server half of the xiaozhi protocol and
  changes nothing about it, which is the right trade for v1 and is paid
  for in machinery that exists only because the device cannot be asked
  to behave differently: barge-in reaching only realtime mode because
  the device owns the listening mode, `idle_timeout_s` existing because
  nothing in the firmware closes a realtime channel, and the whole
  barge-in gate stack existing because echo cancellation quality is
  invisible from here. One entry turned out to be an unclaimed
  capability rather than a constraint, and has its own section: the
  firmware update channel is fully built on the device, with A/B
  partitions already in the layout, a boot path and a `self.upgrade_firmware`
  MCP path (kept out of the model's tool list), and rollback already
  enabled, so what is missing before anything ships is signing rather
  than plumbing. The v1 plan's device-side line now points at it.

## 2026-08-06

### Added

- Two events that make a provider's behaviour visible, which is one
  gap seen from two sides. `provider_failed` fires where an ASR, LLM
  or TTS call fails, carrying `stage`, `provider`, `type`, `host`,
  `error` and `duration_ms` alongside the `session` and `device` every
  conversation event has. A failing provider was previously a
  traceback under "reply failed" with no `event` to filter on, no
  session to group by, and no host, which is the field an egress
  allowlist is diagnosed from: the reported symptom was a pod that
  boots healthy, answers, and is silent every reply until the
  synthesis timeout expires, with nothing in the logs naming a network
  policy. A timeout is worded as one, because where traffic is dropped
  rather than refused the whole symptom is the wait, and the wait sits
  at the provider's `timeout_s`, which is itself the diagnosis. The
  human sentence and the traceback are unchanged.

  `llm_round` fires per generation call with `duration_ms`,
  `first_token_ms` (of the first spoken token, so a round that only
  asked for a tool carries none), `turns`, `round`, the same fields, and
  `prompt_tokens`/`completion_tokens` where the provider reports them.
  Stage latency was otherwise inferred from the gaps between events,
  and the gap between `heard` and `speaking_started` holds the LLM and
  the TTS time to first byte with nothing separating them. A field
  session lost 19.04 s inside that gap against a session median of
  1.18 s and the logs could not say whether the payload or the vendor
  was responsible; they now can. `round` counts the whole reply rather
  than one agent's leg, so the generation after a handover is a round
  of its own rather than another first round. Token counts are asked
  for only where the endpoint is OpenAI itself, whose dialect needs
  `stream_options`, and read wherever a server volunteers them; the
  Anthropic API reports them unasked. Their absence is a fact about
  the endpoint rather than an error.

- A second published image variant, `slim`, for deployments whose ASR
  and TTS name external providers. It installs no optional extra, so it
  carries neither local engine and no GPL component at all, and it is
  494 MB against the default's 883 MB, a saving of 389 MB. Most of that
  is not piper but `faster-whisper`, which brings its own inference
  stack rather than reusing the onnxruntime `pysilero-vad` already
  pulls in, so the reduction is far larger than the size of the engines
  themselves. Tags follow the Docker convention where the unsuffixed
  name is the batteries-included image: the default variant keeps
  `latest`, the dated tag and `sha-<short>` exactly as before, and slim
  takes `slim`, `<date>-slim` and `sha-<short>-slim`. Nothing changes
  for an existing puller of `latest`. Both are built from one
  Dockerfile, selected with `--build-arg SAMTAL_VARIANT=slim`, so they
  cannot drift, and an unrecognised variant fails the build rather than
  silently producing the smaller image. `silero` VAD is in both, being
  a core dependency rather than an extra. A slim image given a config
  that names `faster_whisper` or `piper` refuses to start and names the
  extra it lacks, which is checked in CI along with the absence of the
  packages themselves; both variants run the same whole-conversation
  smoke test, which is what makes them provably the same server.

- New `server.capture` section: recording a session to disk so a
  real-world one can be analysed offline. Off by default and off until
  `enabled` says otherwise, because this writes room audio to disk and
  that is the opposite of what the rest of the project promises; a
  warning at startup and one line per recorded session say when it is
  on, and a section that is present but off says so once, since a
  configured capture that records nothing is otherwise a silence to
  debug. The flag is the switch rather than the presence of the
  section, so turning capture off after a recording does not mean
  deleting the directory and the budgets with it; `dir` stays required
  even while disabled, so switching on is one word. It exists because acoustic problems
  cannot be reproduced in any test lane: both lanes bypass the
  microphone, the board's echo cancellation, and the room, so how much
  of the assistant's own voice reaches the endpointer is unknown and a
  barge-in fix would be tuned against a guess. Three files per session
  on one timeline: a stereo 16 kHz WAV with the microphone on channel
  0 and what was paced out to the speaker on channel 1, so one sample
  index is one instant in both and echo leakage becomes a measurement;
  a JSONL decision track carrying every structured event with a `t_ms`
  offset into the audio, plus frames dropped before decode aggregated
  per second with their reason and the endpointer's `speech_ms`
  sampled every frame rather than only where it decided something; and
  a JSON manifest recording the server revision, the firmware the
  device reported at OTA check-in, the resolved provider entries
  verbatim, and the barge-in thresholds, because a capture outlives the
  code that made it. The microphone is recorded before the session's
  own guards, so the frames a configuration discards are in the file
  anyway, those being the ones that explain a misfire. Options:
  `enabled` (default false), `dir` (required),
  `max_session_s` (default 900), `max_total_mb` (default 2000, whole
  captures pruned oldest first) and `min_free_mb` (default 1000, below
  which a capture declines to start and says so, since agent memory
  and the model caches share the volume). A capture cut off by a
  restart stays readable: both files are flushed as they are written,
  and the manifest says whether the WAV header was ever patched.

- The server can say which build it is running. `__version__` has read
  `0.1.0` since the package skeleton and answers a different question,
  so a separate `revision` now rides `/healthz`, every `session_open`
  log event, and the OTA reply under a new `server` key. It is resolved
  once at startup: `SAMTAL_REVISION` when set, else `git describe
  --always --dirty` when there is a checkout to describe, else
  `unknown`, which a build with neither reports rather than failing to
  start. The image gained a `SAMTAL_REVISION` build argument that
  becomes an environment variable, since a process cannot read its own
  image's OCI labels, and CI passes the commit its `sha-` tag is
  computed from, so a running container and its image tag agree. The
  `session_open` field is the widest of these: the JSON logs already
  ship to a collector, so every session becomes attributable to a
  build, which is what makes two field recordings of different
  behaviour tellable apart from one code change and two different
  rooms.

- New `server.limits.idle_timeout_s` (default 120): how long a realtime
  session may go without conversing before the server closes it,
  counted from the end of the last utterance or the end of the last
  reply, whichever is later. A realtime device asks to listen once and
  then streams its mic for the rest of the connection, and nothing in
  the firmware ever closes that channel, so until now walking away
  mid-conversation left the mic running until `max_session_s`, an hour
  in a typical deployment: room audio reaching the server, one of
  `max_sessions` held, Opus decode and VAD running over the silence,
  and the board unable to reach the sleep mode `CanEnterSleepMode`
  refuses while an audio channel is open. Arriving audio deliberately
  does not reset the clock, since a realtime session streams silence
  too; a reply still speaking does, so a timer coming due mid-reply
  cannot leave the user without a window to answer. Realtime only: an
  auto-mode device stops listening after each reply and re-arms per
  turn, and `max_session_s` remains its bound. The close is a 1000
  normal closure, which the firmware reads as the end of a conversation
  and answers by reconnecting on the next wake word, and it is logged
  as its own `session_idle` event so an abandoned conversation can be
  told from one that ran out of its hour.

- New `openai` ASR provider type, transcribing an utterance through
  the OpenAI transcription API. Like the `openai` TTS type it needs no
  optional extra and adds no dependency, so one key now serves all
  three network stages. Options: `api_key_env` (required for OpenAI
  itself), `model` (default `gpt-4o-mini-transcribe`), `base_url`
  (default `https://api.openai.com/v1`), `prompt`, `language`,
  `temperature` and `timeout_s`. The stage's PCM goes up as WAV, whose
  header carries whatever rate the pipeline is running at, so nothing
  is re-encoded and no rate is pinned. `base_url` reaches any server
  implementing `/v1/audio/transcriptions`: a keyless self-hosted one
  may leave `api_key_env` out, while a gateway or hosted endpoint that
  authenticates still names its variable there, since only the
  *requirement* for a key is specific to OpenAI's own host. The
  endpoint rather than the type decides egress, so an entry under
  `server.local_only` declares its own. Retries are off, so `timeout_s`
  bounds a turn. Audio under OpenAI's 0.1 s minimum is answered empty
  without a round trip, which is the barge-in path: a snippet of tens
  of milliseconds is transcribed to decide whether an interruption was
  real, and the API's refusal would be logged as a failure rather than
  the non-answer it is. That floor was measured against OpenAI, so it
  applies only there; a compatible endpoint sets its own accepted
  length, as it already does for the model rules and the temperature
  range.
  Unlike the TTS types, this one is usually the faster choice as well
  as the more accurate: measured against local `faster_whisper` small
  on an int8 CPU, 536 to 658 ms per utterance against 1688 to 1781 ms,
  and it still transcribed Swedish exactly under white noise at 0 dB
  where the local model returned an unrelated English sentence. It
  reports no language and asks for no session language lock, because
  the API returns neither a usable language nor a confidence; nothing
  is lost, since the detection pass those exist to skip is free here.
  It does not stream, which the module documents as a decision rather
  than an omission: the stage takes a whole utterance and the LLM
  cannot start on half a sentence.

### Changed

- `tts start` now means "audio is about to play" and nothing else. It
  went out as soon as transcription finished, before the LLM ran, so a
  device entered its speaking state and displayed 说话中… for the whole
  of a slow generation while playing nothing. Confirmed on the board:
  with a generation stalled 20 s, the firmware's own state machine
  logged `listening -> speaking` at the transcript, and a
  conversation-button press 7.1 s into that silence produced
  `Application: Abort speaking` on the device and `device aborted (no
  reason)` on the server. That is the reasonless abort seen in the
  field: a user interrupting a device that was not speaking. Moved,
  the same stall passes with the board still listening, and it enters
  `speaking` when the first sentence does. A reply that speaks nothing
  at all still sends the pair, `start` immediately before `stop`,
  because an auto-mode device re-arms its listening on `tts stop` and
  a `stop` it was never told to expect is the one way this could
  strand one. Recorded as an ADR.

- CI passes the short commit SHA as `SAMTAL_REVISION`, so a running
  container's `revision` equals its image tag's suffix instead of being
  40 characters against the tag's seven. The field meant two different
  things depending on which source produced it, and a deployment was
  caught by it: its post-deploy check compared `/healthz` to the `sha-`
  tag with equality, which is the natural reading, and got a false
  failure. The seven characters come from one expression shared by the
  build arguments, the smoke lane and `docker/metadata-action`'s
  `type=sha,format=short`, so the tag and the reported revision cannot
  drift apart. Deliberately not `git rev-parse --short`, whose length
  git widens as a repository grows: what this has to agree with is the
  tag. A working tree still reports `git describe --always --dirty`,
  keeping the `-dirty` marker that says a build is running code which
  is not any commit, and an image built with no build argument still
  reports `unknown`.

- The rules an `openai` provider derives from its `base_url` (whether
  a key is required, whether the type's model rules apply, the retry
  policy) moved to a shared `providers/openai_endpoint.py`, now that
  two stages decide them the same way. No behaviour changed.

### Fixed

- A provider that fails while constructing itself now says which
  configuration entry it was. `build_provider` named the entry for
  every other failure it raises (an unknown type, a bad option, a
  missing extra, an egress-marked provider under `local_only`), which
  is what makes a bad configuration a five-second fix, but the factory
  call itself was unwrapped: a local engine fetching its weights can
  fail on a blocked host, a full volume, a corrupt cache or a name the
  hub does not have, and each of those arrived as a traceback from
  inside `faster_whisper`, `httpx` or `huggingface_hub` with no
  mention of what was being built. Survivable while a configuration
  had one provider per stage, since there was only one candidate;
  multi-entry configurations are now normal, and a deployment running
  language-locked personas has three ASR entries and three TTS entries
  differing only in a pinned language and a voice. `ProviderError`
  passes through untouched, so every existing message keeps its exact
  wording, and the original exception survives as `__cause__`, so the
  traceback is still there. Not a reachability check and not a retry:
  the boot fails at the same moment for the same reason, and only says
  which entry it was.

- An ASR transcript that comes back as the configured `prompt` is now
  discarded rather than answered. On short or low-content audio the
  transcription model hands the prompt back instead of hearing
  anything: provoked against `gpt-4o-mini-transcribe`, 45 of 45 clips
  of room tone under a second returned the prompt word for word. That
  is not a cosmetic artifact, because the transcript reaches the model
  as something the user said. A field session set `prompt` to the
  assistant's name and its three agent names, precisely so the personas
  would be recognised when spoken, and a 0.9 s utterance came back as
  that string and was read as a request to switch agents: a handover
  nobody asked for. The provider knows what prompt it sent, so a
  transcript equal to it (trimmed, case-insensitive, and ignoring a
  full stop the model added, which one of the 45 carried) is now
  treated as silence, the same as audio under the minimum length, and
  logged as a warning so it is not a silent drop. Equality rather than
  containment: someone can say the words in the prompt. An entry with
  no `prompt` is unaffected. The README and `config.example.yaml` now
  state the failure mode and the rule that follows from it: keep the
  prompt to vocabulary, never to anything the assistant could act on.

- `switch_agent` naming the agent that is already speaking is now
  refused instead of performed. It was reported twice in one field
  session: the user's utterance mentioned a persona by name while that
  persona was already active, the model called `switch_agent` on it,
  and the session ended the leg, re-activated the same agent and ran a
  second LLM round (2.82 s and 1.70 s) whose only product was the
  assistant introducing itself to someone it was already talking to. A
  handover to the current agent is a pure cost with no possible effect,
  so it now comes back as a tool error the current agent phrases in its
  own voice and language, like the other refusals, and the reply
  continues rather than stopping. A string comparison, made before the
  round is committed. Switching to a different bound agent is
  unaffected, and a device bound to one agent still gets no
  `switch_agent` tool at all.

- Documentation gaps a deployment hit in the field, all of them cheap
  to state and expensive to discover. The ElevenLabs stock voices are
  recorded by English speakers, so a stock voice speaking Spanish
  sounds like an American speaking fluent Spanish; the `voice_id`
  comment now says to pin a native voice for a non-English agent, and
  that a professional clone is fine-tuned per model, so one unavailable
  on the configured model fails at synthesis rather than at boot and
  presents as silence on the device. The `memory:` comment now says
  that renaming an agent orphans its memory, since the key is the agent
  name, and that conversation history carrying across a `switch_agent`
  handover is not the same thing as agent memory: a persona that has
  stored nothing can still greet the user by name the moment it takes
  over, from the transcript rather than from its own file. And the
  README's Security section gains a table of which hosts each provider
  type reaches, because a blocked host does not announce itself, and
  because an `openai` ASR shares `api.openai.com` with an
  `openai_compatible` LLM while `elevenlabs` TTS needs a host of its
  own, which is not obvious and is the one most likely to be missed.

- A multi-sentence reply no longer stutters. Frames are paced to
  realtime, so sending a sentence takes about as long as hearing it,
  and each sentence used to be synthesized only once the previous one
  had finished playing. That put the next sentence's whole time to
  first byte on the speaker as silence, once per sentence, for the
  whole reply: measured against the real providers, 884 ms and 478 ms
  between the sentences of a three-sentence reply through
  `gpt-4o-mini-tts`, and 129 ms and 139 ms through
  `eleven_flash_v2_5`. It was reported from a board session as hiccups
  in the assistant's voice. It was also worse than a plain pause,
  because the frame pacer's schedule is absolute from a reply's first
  frame, so the frames after a stall burst out to catch up: a dropout
  followed by a flood. A sentence now starts synthesizing before the
  previous one is spoken rather than after, one sentence of lookahead,
  so that latency is spent against playback that is already happening.
  Measured again, every sentence boundary is a single 60 ms frame,
  which is the cadence rather than a gap, and the catch-up bursts are
  gone. A sentence run ahead and then cancelled by a barge-in is
  neither spoken nor recorded as spoken, the lookahead stops at a tool
  round's boundary, and a synthesis belongs to the agent leg that
  started it, so a handover cannot speak in the wrong voice.

- The server README's log event table was missing `session_idle`, added
  the same day the event was, and its `session_open` row did not list
  the new `revision` field. Both rows now match what the server emits.

- Documentation for the `openai` ASR provider told operators that
  leaving `language` unset "costs nothing", which a device checkpoint
  disproved: on far-field microphone audio through Opus, detection has
  much less to go on than a clean file, and unpinned Swedish came back
  as English-shaped nonsense ("Vad heter Sveriges huvudstad?" heard as
  "Hat hetas verigezogistad."). Pinning fixed it outright, and `prompt`
  does not compensate, since it fixes vocabulary rather than language.
  The README and `config.example.yaml` now tell an operator to set
  `language` for any non-English deployment. The accuracy comparison is
  marked as holding only once the language is pinned, and the latency
  tables gain the figures measured on the board, where the local engine
  came in at 964 ms rather than the desk's 1688 to 1781 ms, so the gap
  to the cloud is much narrower than first published. No code changed.

- The test suite no longer writes or reads bytecode, so a working tree
  can no longer lie about what it is running. A cached `.pyc` records
  the source's size and its mtime in whole seconds, and CPython accepts
  the cache when both are equal to the source's current values, so any
  edit that keeps the byte count and leaves the mtime on the second it
  was compiled on is invisible. Two ordinary operations here do exactly
  that: swapping two statements to check a regression test really fails
  without its fix, and restoring a file from a backup, which carries
  the backup's mtime rather than the current time. The second is how it
  bit while addressing the review on #13, where a correct fix ran as
  its pre-fix version and looked broken. `tests/conftest.py` now sets
  `sys.dont_write_bytecode`, which also covers pytest's
  assertion-rewritten test bytecode, and clears the existing caches
  under `samtal_server/` and `tests/`, since the flag stops writes but
  not reads and a cache left in place would never be refreshed. CI
  exports `PYTHONDONTWRITEBYTECODE` for the steps that are not pytest.
  The container image is deliberately untouched: its sources never
  change after the build, so timestamp validation is correct there and
  `UV_COMPILE_BYTECODE=1` is worth keeping. `AGENTS.md` gains the two
  traps, since neither is guessable.

## 2026-08-05

### Added

- New `openai` TTS provider type, streaming cloud synthesis as raw
  PCM. It needs no optional extra and adds no dependency: the `openai`
  client already ships for the `openai_compatible` LLM type, and
  speech is a method on it, so one key serves both stages. Options:
  `voice` (required), `api_key_env` (required for OpenAI itself),
  `base_url` (default `https://api.openai.com/v1`), `model` (default
  `gpt-4o-mini-tts`), `instructions`, `speed` and `timeout_s`. There
  is no audio format option: the API's `pcm` format is fixed at
  24 kHz, which is the device rate, so nothing is resampled. Naming
  `speed` on a `gpt-4o` model, or `instructions` on a `tts-1` model,
  fails the boot rather than becoming a knob the API silently ignores;
  that check knows OpenAI's models, so it applies only when `base_url`
  names OpenAI's host and a compatible server receives both knobs
  unexamined. The host is what decides, so every spelling of OpenAI's
  endpoint keeps the same startup checks, and a `base_url` that is not
  a URL fails the boot rather than the first synthesis.
  `base_url` reaches any server implementing `/v1/audio/speech`, so a
  local pipeline stays available through this type and no key is
  needed for one; that also means the endpoint rather than the type
  decides egress, and an entry under `server.local_only` declares its
  own, exactly as `openai_compatible` does. Retries are off, so
  `timeout_s` bounds a sentence: the SDK would otherwise attempt a
  failed request three times, leaving the device silent for three
  timeouts plus backoff.
  Documented with a caveat found on the test board: because a reply is
  synthesized sentence by sentence with no lookahead, this provider's
  time to first byte is paid at every sentence boundary as well as at
  the start, measured at 520 to 617 ms per boundary against
  ElevenLabs' 111 to 131 ms, which is audible as stuttering on long
  replies. The provider suits short answers until #37 lands.
- New `elevenlabs` TTS provider type, streaming cloud synthesis as raw
  PCM. It needs no optional extra: the API is one streaming POST, so
  the provider speaks it over `httpx` (now a direct dependency)
  instead of a vendor SDK, and is present in every install. Options:
  `voice_id` and `api_key_env` (both required), plus `model`
  (default `eleven_flash_v2_5`, the low-latency model), `output_format`
  (default `pcm_24000`, which matches the device rate so nothing is
  resampled), `language_code`, `voice_settings` and `timeout_s`. The
  type marks egress, so `server.local_only` refuses it.
- Every provider type now carries a class-level `egress` marking:
  whether it sends session data (audio, transcripts, replies) off the
  host. `anthropic` marks egress; `silero`, `faster_whisper`, `piper`
  and the mocks mark local. `openai_compatible` cannot know its own
  (the `base_url` decides), so it defers to an explicit per-entry
  `egress` declaration in the configuration; the other types reject
  that key, and a type without any marking counts as egress.
- New `server.local_only` flag (default `false`): when on, building
  any egress-marked provider refuses to boot with an error naming the
  stage and provider, and an `openai_compatible` entry is refused
  unless it declares `egress: false`. MCP servers sit inside the same
  boundary, since tool arguments carry conversation-derived data and
  no transport knows its own egress: every referenced `mcp_servers`
  entry needs the same declaration. Boot-time, never runtime: a
  local_only server that starts is a local_only server. The fully
  local promise becomes a property the server checks instead of a
  documentation property of a carefully chosen configuration.
- Barge-in is gated: an utterance the endpointer ends while a reply is
  in flight only cancels it on evidence of user speech. Four gates, in
  order: speech shorter than `server.barge_in_min_speech_ms` (default
  500) is dropped; an interruption landing while the reply is still
  transcribing cancels it but prepends its audio, so one reply answers
  the user's whole sentence instead of losing its head; anything
  within `server.barge_in_refractory_ms` (default 1000) of the reply's
  first audio frame is dropped as playback-onset transient; everything
  else pauses the outgoing frames while ASR transcribes the
  interruption, cancelling only on a non-empty transcript and
  otherwise resuming the reply where it stopped. The gates apply to
  endpointer-driven utterance ends only: a manual `listen stop`
  mid-reply still cancels unconditionally, and `barge_in: false` is
  untouched. The decision is recorded in
  `docs/adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md`.
- The `Endpointer` protocol gained `speech_ms()`: milliseconds
  classified as speech since the last reset, at each implementation's
  own window granularity.
- New structured log events, since events are the observability
  surface: `barge_in_suppressed` (`reason`: `min_speech`,
  `refractory`, or `no_transcript`, plus `speech_ms`) fires when a
  gate drops an interruption, and `barge_in_merged` (`speech_ms`)
  fires on the mid-transcription merge. The `speech_ms` they carry is
  exactly the data the two thresholds should be tuned with from a
  deployment's retained logs.
- The deployment profile documents both keys with noisy-deployment
  guidance: the VAD `threshold` is the companion knob (stricter
  speech classification keeps noise from reaching the gates at all),
  and its `trailing_silence_ms` suggestion now carries a caution,
  since shorter trailing silence makes mid-sentence chopping
  likelier.

### Changed

- The `barge_in` event gained `speech_ms` (the endpointer's
  speech-classified duration for the interrupting utterance) and
  `speaking_ms` (milliseconds from `speaking_started` to the cancel
  decision, absent when the reply had not yet spoken).

### Fixed

- `uv run pytest` failed to collect. `tests/unit` and
  `tests/integration` each hold a `test_ws_auth.py` and a
  `test_drain.py`, named for what they test at their own level, and
  pytest's default import mode registers a test module by its bare
  basename, so each pair collided. Any run collecting both suites
  errored, including the bare command the configured `testpaths`
  implies; only CI's split into two invocations hid it. The suites now
  run under `--import-mode=importlib`, which imports each file by its
  full path, so the descriptive names stay and the obvious command
  works.
- The README's feature list claimed a local voice pipeline of
  "SileroVAD + SenseVoice + EdgeTTS"; SenseVoice and EdgeTTS were
  never implemented. It now names the stack the provider registry
  actually ships: Silero VAD, faster-whisper, Piper.

## 2026-08-04

### Changed

- The README now leads with the project's spirit: a "Conversational
  AI. Sweded." tagline linking to the Be Kind Rewind creators' How To
  Swede video, a "What is samtal?" section that opens with the sweding
  idea and names the two upstream projects it starts from, and a
  Credits note on where the word comes from.

### Added

- `config.deploy.example.yaml`: a deployment profile example for the
  container image behind a TLS-terminating proxy on a small CPU quota,
  holding the values the issue #22 latency measurements validated
  (`cpu_threads` sized to the quota, `vad_filter: true`,
  `condition_on_previous_text: false`, a two-step `temperature`
  ladder, and `language_detect: once` with a confidence floor and
  fallback). Where `config.example.yaml` documents every key, this
  file sets only what a deployment should decide, so operators can
  adapt it instead of re-deriving the tuning from the feature docs.
  Operator review then hardened the profile: no `default_agent`, so
  the `devices` map is an allowlist and unknown devices are refused;
  the secret `ota_path` segment is injected from the environment
  rather than committed; and agent memory sits on the data volume. A
  unit test keeps it parsing and pins the allowlist posture.

- A language surface for multilingual deployments that cannot pin
  `language`: `language_detect: once` detects until one confident
  answer and then reuses it for the rest of the session (saving the
  constant per-utterance detection pass that #22 measured at 3.4 s),
  and `language_fallback` with `language_confidence_floor` uses a
  configured language instead of trusting a low-confidence guess,
  re-invoking before any decoding runs. Under the hood the ASR
  protocol now returns text with language metadata and takes a
  session-scoped hint (`AsrResult`, recorded as an ADR amendment),
  and the `heard` event carries `language` and `language_confidence`
  when an engine detected.
- The `faster_whisper` ASR provider now exposes the decode options the
  live-deployment measurements in #22 identified: `vad_filter` and
  `vad_parameters` (strip non-speech inside the ASR call),
  `condition_on_previous_text` (false is the standard mitigation for
  repetition loops), `temperature` (a short fallback ladder bounds
  worst-case decode latency), and `cpu_threads` (size the inference
  pool to a container CPU quota in config rather than through
  `OMP_NUM_THREADS`). All keep the engine's defaults when unset.
- A `speaking_started` conversation event, logged when the first Opus
  frame of a reply goes out. `replied` fires at the last frame of a
  paced stream, so on its own the logs could not separate synthesis
  cost from speaking time; the pair makes time-to-first-audio directly
  measurable, which the operator measurements in #22 asked for.
- `docs/adr/` holds architecture decision records: one immutable,
  date-prefixed file per decision that is hard to reverse, surprising
  without context, and the result of a real trade-off. The first two
  records backfill decisions from the v1 design whose consequences the
  live-deployment measurements in #22 made visible: that providers are
  startup-built singletons behind payload-only protocols, and that the
  structured JSON log events are the server's observability surface and
  transcript store until v3.
- `samtal-esp32/README.md` documents how a board behaves in daily use.
  It separates what holds for any board running the upstream firmware,
  that the microphone is live locally for the wake word but reaches the
  server only during a conversation, that nothing in the firmware ends
  that conversation once it is open, and the 2.4 GHz limit that governs
  phone hotspots, from what is read out of one board's own
  configuration, which is the controls and the sleep and shutdown
  timings. The hardware tables in both READMEs now link the working
  board's status to it.

### Changed

- `faster_whisper`'s `beam_size` now defaults to 1 (greedy decoding)
  instead of 5. Beam search costs a multiple of the decode time on CPU
  and buys little accuracy on short spoken commands (#19); production
  measurement showed the predicted speedup with no attributable
  accuracy cost (#22). Deployments that want the old behaviour set
  `beam_size: 5` explicitly.

### Fixed

- An utterance handed to ASR no longer carries the whole gap since the
  previous one (#14). A continuously listening realtime session
  buffers the reply's playback time and the user's thinking pause, and
  every utterance after the first dragged up to thirty seconds of that
  silence into transcription: slower on every turn, billed audio on
  hosted ASR, and the source of the garbled transcripts and language
  misdetections measured in #22. The endpointer now reports where
  speech began, and the session trims the utterance to the speech plus
  `server.utterance_pre_roll_ms` (default 300 ms, so the first phoneme
  survives) plus the trailing window. `heard`'s `duration_s` therefore
  means how long the user spoke again, which matters because retained
  logs are the transcript store.

## 2026-08-03

### Added

- samtal-server hardening and release (M7): the server is now something
  you can deploy. It ships as a multi-arch container image
  (`ghcr.io/rafacm/samtal-server`, amd64 and arm64, tagged `latest`, the
  build time, and the commit SHA), built and published by CI only after
  the tests pass, with both local engines baked in so one `docker run`
  with one mounted YAML serves a conversation. Model weights are still
  never baked in: `HOME` points at the mounted volume, where whisper
  models and Piper voices download at first start. A fourth test lane,
  `tests/smoke`, holds a whole conversation with the freshly built
  container in CI, which turns the milestone acceptance into something
  checked rather than remembered.
- Structured logging: `server.log_format` (`text` or `json`, and `json`
  is the image's default) and `server.log_level`. Every conversation
  event now carries structured fields alongside its human sentence
  (`event`, `session`, `device`, plus what the event holds), so retained
  JSON logs filtered on `heard`/`replied`/`agent_said` and grouped by
  session read back as transcripts. That stands in for a conversation
  store until v3 brings a real one.
- Limits and a graceful shutdown: `server.limits.max_sessions` (eight
  concurrent conversations) and `server.limits.max_session_s` (an hour,
  which bounds an idle session too, so there is no separate idle key).
  On SIGTERM the server stops admitting sessions and lets every reply in
  flight finish speaking before closing those sockets, inside
  `server.drain_s`; a second signal forces the exit. Uvicorn cannot do
  this part, since it fail-closes every websocket with 1012 the moment
  its own shutdown begins.

### Changed

- `docs/xiaozhi-notes.md` records three findings from provisioning a
  board against an HTTPS backend: that a device missing from the
  `devices:` allowlist still gets `200 OK` from the OTA check with an
  empty token and is refused only at the WebSocket handshake, that the
  firmware needs no certificate work because the ESP-IDF bundle plus
  cross-signed verification covers the current Let's Encrypt chain, and
  that probing a WebSocket route with `curl` requires `--http1.1` or the
  route answers a misleading `404`. The NVS note now also lists which
  namespaces to carry across a regeneration and which regenerate
  themselves.
- Published images carry the build time (`2026-08-03-1200`, UTC) where
  they carried the build date. A date-only tag was claimed by every
  build that day, so it moved like a second `latest` while reading like
  a release marker: two merges on 2026-08-03 both took `2026-08-03`,
  and the second changed what that tag meant four hours after the
  first. `latest` is now the only tag that moves.
- `default_agent` is now required only when agents are defined and no
  device is bound to one. Omitting it is how a deployment says "only
  these devices": every unknown MAC then resolves to no agent, is issued
  no token, and is turned away, which makes the `devices` map the
  allowlist without a second list to keep in sync.
- WebSocket pings are explicit at 20 seconds, which settles the per-path
  idle timeout question the v1 plan parked: a proxy in front needs only
  a read timeout above that interval, and the two paths need no
  different treatment.

### Fixed

- A realtime-mode session no longer goes deaf after its first utterance.
  It served exactly one exchange: a realtime device sends `listen start`
  once and then streams continuously, and the server stopped listening
  after every utterance waiting for a re-arm that was never coming, so a
  board answered one question per button press. The firmware asks for
  realtime exactly when its echo cancellation is on, which makes this
  the normal case for the hardware this project targets rather than an
  edge case. A realtime session now keeps listening, including while it
  speaks, so an utterance that ends mid-reply cancels that reply and is
  answered instead: talking over the assistant stops it. The new
  `server.barge_in` (default true) turns the interrupting off for a
  board whose echo cancellation leaks the speaker back into the
  microphone, where a reply would otherwise interrupt itself;
  conversations stay multi-turn either way. The listening mode a device
  asks for is now logged at info, and an interruption logs a `barge_in`
  event.
- An interrupted reply now leaves the conversation history holding
  exactly the sentences the user heard. Sentences were counted per
  round, and a reply cut off mid-round lost all of them, so a device
  that spoke for thirteen seconds before being interrupted left no
  trace: the reply answering the interruption was written as though
  none of it had been said. They are counted one at a time now, as
  each sentence's audio goes out, which also keeps the sentence that
  was cut off partway out of the history and out of the retained
  logs.

### Security

- Device authentication is on by default, and a server started with it
  enabled and no secret in the environment refuses to boot rather than
  quietly serving every device that connects. The OTA endpoint issues
  each bound device an HMAC token (upstream's scheme, so stock firmware
  needs no change), and the websocket handshake verifies it before
  accepting the upgrade: a missing, forged, expired, or foreign token is
  refused with HTTP 403 and never reaches a socket. Opting out for a
  trial on a trusted network is one deliberate flag,
  `server.auth.enabled: false`.
- `server.ota_path` makes the endpoint's path configurable, so a public
  deployment can hide the one endpoint that cannot require a token
  behind a long random segment.
- FastAPI's `/docs`, `/redoc`, and `/openapi.json` are no longer served.
  A device needs two paths and a healthcheck a third.

## 2026-08-02

### Added

- samtal-server tools and MCP (M6): the assistant can now do things, not
  only say them. Three sources of tools merge into one list the model
  sees, kept apart by the shape of their names rather than by collision
  handling: MCP servers configured per agent under a new top-level
  `mcp_servers` section (stdio and streamable-http, referenced through
  an `mcp` list that `agent_defaults` can supply, secrets written as
  `$VAR` and resolved at boot), the device's own tools discovered over
  the conversation socket, and two builtins. `switch_agent` moves a
  conversation between the agents its device is bound to, and the new
  agent greets in its own prompt and its own voice with the history
  carried over; `remember` keeps a fact in a per-agent file that is
  injected into that agent's prompt on every reply, configured by an
  optional `memory` section. The session owns the tool loop, so
  providers stay translators and the round after a handover can go to a
  different one; a tool that fails, times out, or does not exist becomes
  an error result the model explains in its own voice rather than a
  broken reply. A server that is unreachable at startup logs a warning
  and reconnects in the background, while configuration mistakes still
  fail the boot. The official `mcp` SDK is now a core dependency.
- samtal-server agents and bindings (M5): distinct personas, enforced. A
  new top-level `agent_defaults` section holds what every agent uses
  unless it names something else, so a typical agent shrinks to a prompt
  and a voice; it deliberately takes no prompt, since a prompt is what
  makes an agent that agent. A device is bound to one agent or to a list
  of them, the first being the agent a conversation starts on and the
  rest the ones M6's spoken switching will reach, and the session now
  holds an explicit active agent whose prompt, providers, and endpointer
  swap together. Two simulated devices in one server run get two
  personas: the reply text comes from each agent's own prompt and the
  audio in each agent's own voice. The opt-in local lane runs the same
  thing on real engines, identifying the voice each device was answered
  in by re-speaking the reply in both configured voices.
- samtal-server conversation pipeline (M4), replacing the M3 echo: while
  the device listens, decoded audio feeds a Silero VAD endpointer; the
  finished utterance is transcribed (announced to the device in an `stt`
  message), the LLM streams a reply that a sentence splitter cuts into
  speakable pieces, and TTS speaks each sentence back as paced Opus frames
  at 24 kHz, the rate the server hello now announces. Conversation history
  accumulates per connection, `abort` still cancels a reply mid-stream,
  and provider failures end the reply but never the session. Every stage
  is a pluggable provider chosen per agent and built at server startup, so
  configuration mistakes fail the boot: `silero` VAD (pysilero-vad, core),
  `faster_whisper` ASR (extra), `anthropic` and `openai_compatible` LLM
  (core), `piper` TTS (extra, GPL-3.0), and deterministic keyless `mock`
  providers that let CI run the whole pipeline. Model weights and voices
  download at startup, never ship in the package.
- samtal-server opt-in local test lane (`SAMTAL_LOCAL_LANE=1 uv run
  pytest tests/local`): one real conversation through the fully local
  pipeline against a local Ollama, with a pre-flight check that fails
  naming whatever is missing. Never runs in CI; skips without the opt-in.

- samtal-server device websocket endpoint (M3) at `/xiaozhi/v1/`: accepted
  upgrade, hello exchange with a 10 second timeout, and an audio loop that
  echoes each utterance back re-encoded (a full Opus decode/encode round
  trip on PyAV), framed by `tts` messages and paced at the frame cadence.
  Utterances end on `listen stop` or through an energy endpointer standing
  in for M4's VAD; `abort` interrupts a reply in flight; binary framing
  covers protocol versions 1 to 3; devices that resolve to no agent are
  closed with policy code 1008. The integration lane now runs the
  xiaozhi-sdk simulator end to end against a live server. Verified on the
  desk: the board that got 403 since M2 now holds the hello exchange and
  echoes speech.
- samtal-server device OTA/config endpoint (M2) at `/xiaozhi/ota/`: a device
  POSTs its system info and receives the WebSocket URL, an (as yet empty)
  token, the binary protocol version to speak, and the wall clock. The
  firmware section always answers "up to date" because samtal-server serves
  no images, and no activation section is ever sent. The `Device-Id` MAC
  resolves to an agent through the config, falling back to `default_agent`.
  A `GET` on the same path reports where devices are being sent. New
  `server` keys: `websocket_url` (defaults to the address the device reached
  the OTA endpoint on), `protocol_version`, and `timezone_offset_minutes`.
- samtal-server configuration layer (M1), built on pydantic-settings:
  models for `server`, `providers`, `agents`, `devices`, and
  `default_agent`, loaded from one YAML file (`--config` or
  `SAMTAL_CONFIG`). Any key is overridable via `SAMTAL_`-prefixed
  environment variables (nested keys joined with `__`, e.g.
  `SAMTAL_SERVER__PORT`), and a `.env` file is read at startup with
  environment variables taking priority. Secrets are referenced by
  environment variable name only, and validation reports every problem
  with its location. A documented `config.example.yaml` ships with the
  server.
- `docs/README.md` as an index of the research notes, plans, and feature
  docs, linked from the root README's project layout.
- samtal-server README sections on transports (WebSocket only for v1, with
  upstream's MQTT+UDP as the additive alternative; WebRTC is not an upstream
  transport) and on ports and topology, covering the single-port choice, its
  tradeoffs, and what a reverse proxy in front of it has to get right.
- Waveshare ESP32-S3-Touch-AMOLED-2.16 (480×480 AMOLED, dual-mic AEC) listed
  as a planned target board.
- samtal-server stack decision: Python 3.12 + FastAPI (uv-managed), with the
  xiaozhi-sdk device simulator for hardware-free integration tests.
- samtal-server v1 plan (`docs/plans/2026-08-02-samtal-server-v1.md`):
  architecture, milestones M0 to M7 with device checkpoints, folder-scoped
  GitHub Actions CI, and instance-config separation.
- Workflow and documentation conventions in `AGENTS.md`: feature branches
  with rebase-only PRs for code work, dated plan files in `docs/plans/`,
  feature docs in `docs/features/`, and `gh` API tips.
- M0 skeleton for samtal-server: uv-managed Python 3.12 package with FastAPI
  app and `/healthz`, unit and integration test lanes, ruff, and the
  folder-scoped GitHub Actions workflow.

### Changed

- samtal-server `devices` values are now one agent name or a list of
  them, always stored as a list, and `Config.agent_for_device` became
  `agents_for_device`, returning the whole list. Existing single-name
  bindings keep working unchanged. `config.example.yaml` gained
  `agent_defaults`, a second voice, a second persona, and a list-valued
  binding.
- samtal-server agents must now name a provider for all four pipeline
  stages (`llm`, `asr`, `tts`, `vad`); the server refuses to start
  otherwise. `config.example.yaml`'s placeholder `sensevoice` entry became
  the real `faster_whisper` type, and its agent prompt now states the
  reply language explicitly.
- README header now shows project status badges for server CI, Python,
  FastAPI, ESP-IDF, and the MIT license.
- Hardware tables (root and samtal-esp32 READMEs) now list the e-paper
  board first, link each board name to its product page, and keep a single
  "wiki" link in the Links column.
- samtal-server now logs its own work: the CLI gives the root logger a
  handler, which uvicorn does not do, so messages from samtal-server no
  longer vanish while uvicorn's request lines appear.
- Updated logo artwork (`assets/samtal-logo.png`), same concept: the person
  and the device sharing one waveform.
- Hardware tables now link each board's product page and technical
  documentation ("doc").
- The logo is a single transparent PNG of the original artwork
  (`assets/samtal-logo.png`); the traced SVG variant is removed.

## 2026-08-01

### Changed

- New logo: a person and the device sharing one waveform, echoing the
  etymology of samtal (together + speech).
- Logo rebuilt as vector art: `assets/samtal-logo.svg` is now the source of
  truth, and `assets/samtal-logo.png` is rendered from it with a transparent
  background (fixes white edge pixels on dark pages).
- README header now shows the project logo (`assets/samtal-logo.png`).
- README rewritten in the style of clew.nvim: etymology header, early-development
  warning with 🚧 markers, feature bullets, hardware table.

### Fixed

- The vector trace of the logo had flattened the original color gradations;
  the SVG now uses real linear gradients on the orange and blue regions so it
  matches the raster original on both light and dark backgrounds.

### Added

- `AGENTS.md` with project conventions for coding agents, and `CLAUDE.md`
  referencing it.

- Project scaffold: `samtal-esp32/` (device firmware) and `samtal-server/`
  (conversation server) subprojects.
- `docs/xiaozhi-notes.md`: research notes on the upstream xiaozhi firmware and
  server, covering architecture, device↔server protocol, configuration, and the
  procedure used for the first working end-to-end demo on a Waveshare
  ESP32-S3-Touch-LCD-1.54.
- MIT license and third-party license notices for the upstream projects
  ([78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32),
  [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)).
