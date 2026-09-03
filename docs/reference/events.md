# Event schema reference

Generated from the declarations by `vinga-server events reference`. Do
not edit this file by hand: CI regenerates it and fails on any difference,
so an edit here is reverted by the next run. The declarations live in
`vinga-server/src/vinga_server/events/catalog.py`.

The structured events are this server's observability surface
([ADR](../adr/2026-08-04-json-logs-are-the-observability-surface.md)), and
they carry metadata and nothing else
([ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md)).
This document is that surface written down: 61 events in 86 variants. What was
said in a conversation is in the conversation store instead, keyed by the same
`session` ([its reference](conversations-schema.md)).

A site does not describe an emission; it constructs one. Every variant below
is a type, its values are types, and its sentence and argument order are
derived from its own fields, so an emission that is not one of these shapes
cannot be built at all. What is left at runtime is the construction itself,
which happens inside the emitter's guard: a construction that refuses is said
once on the emitter's own channel and dropped, because a telemetry bug must
never cost a reply.

The [README's Logging section](../../vinga-server/README.md#logging) is the
human overview, with one line per event saying when it fires.

## How to read it

A variant is one whole emission shape: where it rides, how loud it is, the
sentence it renders, the arguments that sentence takes, and the payload it
carries. Events have more than one because the surface does:
`session_rejected` is emitted with three arities across four templates on two
channels, `mcp_reload`'s applied and refused answers carry mutually exclusive
fields, and several events change level with shape. A site constructs exactly
one of them.

The template is byte-exact, and the argument table's rows are its `%`
positions in order, each naming the declared field whose value that position
renders. A message that is not one of the declared templates fails even when
every field is lawful, because the rendered sentence reaches the same taps the
payload does.

A variant's field table is the WHOLE payload a tap receives, base fields
included: `event` everywhere, and `session` and `device` on the session
channel, where the emitter owns them and a variant declaring one is refused at
import. On a server channel `session` and `device` are ordinary fields,
declared where they are carried. Required says the field is always present in
that variant; nullable says it may be present and null. An argument position
carries the same nullability column, for the positions whose value the
sentence may have to render as nothing.

This catalog is also a LIVE surface. The same emissions are streamed to an
authenticated operator over `GET /api/runtime/events`, one JSON object per
event, and what rides there is what is documented here plus two fields the
stream owns: `ts`, the wall-clock instant the event was emitted at, and
`level`, the name of the level in that variant's row. Nothing is kept behind
that stream, which is what makes it a second transport over this surface
rather than a store: a reader joins the present, and what happened before is
the conversation record's to answer. A reader who needs the exact object reads
the JSON log, which is the retained copy of the same records.

Two per-frame samples are outside all of this on purpose. The endpointer track
and the dropped-frame counts are capture side channels rather than events, so
they are outside the tap contract and outside this registry, which is what
keeps validation a cost paid per decision rather than per frame.

## The channels

The channel is the scope. One session channel, `vinga_server.session`, carries
everything a conversation says about itself; the 14 server channels are each a
subsystem's own module name. An event declared on one channel and emitted from
another is a violation even when its fields are lawful.

- `vinga_server.session`
- `vinga_server.app`
- `vinga_server.capture`
- `vinga_server.config.api`
- `vinga_server.conversations.store`
- `vinga_server.device.bindings`
- `vinga_server.filler`
- `vinga_server.memory.store`
- `vinga_server.onboarding`
- `vinga_server.ota`
- `vinga_server.providers.openai_asr`
- `vinga_server.providers.world`
- `vinga_server.registry`
- `vinga_server.tools.mcp`
- `vinga_server.ws`

## What a value may be

There is no free-text kind, which is the property the whole vocabulary exists
to keep. Every string field is one of these, and a field that would need prose
is a design error the taxonomy refuses to encode. A trusted identifier's
domain is a non-empty string once stripped, as NonBlankStr defines it: what
the configuration itself guarantees, since a value type claiming more would
refuse a lawful deployment's traffic.

| Kind | What it is |
| --- | --- |
| `IDENTIFIER` | A trusted name the operator or this server chose: an agent, a configured entry, a pipeline stage, a path, an origin. Trusted is about provenance rather than shape, so its domain is the configuration's own and no tighter. |
| `TOKEN` | One value out of the field's declared closed set, listed in full below. |
| `CLASS_NAME` | An exception or type name. Never a message: a type name says what went wrong, a message says what a stranger wrote. |
| `ID` | A bounded machine form this server minted or normalized, held to a named syntax rather than to a generic length. |
| `DESCRIPTOR` | A far-side string retained deliberately: what a device says about itself at check-in, bounded and stripped of unprintables at its decision site and bounded again at emit. |
| `INT` | A whole number. Booleans are refused, since `True` is an `int` to Python. |
| `FLOAT` | A finite number, whole or fractional. Infinities and NaN are refused: they are not measurements and JSON cannot carry them. |
| `BOOL` | `True` or `False`. |
| `COUNT` | A whole number of zero or more, for the fields whose meaning is how many. |
| `IDENTIFIER_LIST` | A list whose every element is an `IDENTIFIER`. |
| `ID_LIST` | A list whose every element is an `ID` of the field's declared syntax. |
| `SOURCES` | The one structured kind: a mapping from prompt provenance to character counts, keyed by the grammar below. |

A `TOKEN` field's constraint column lists its whole set. A value that is
empty, or that begins or ends with a space, is printed quoted there, for the
reason the patterns below are: a bare code span shows neither, and a set whose
members cannot be read exactly is not a closed set.

## What an argument may be

The sentence's `%` positions have their own taxonomy beside the field kinds,
because a rendered sentence carries shapes no payload field does.

| Kind | What it is |
| --- | --- |
| `IDENTIFIER` | As the field kind. |
| `TOKEN` | As the field kind. |
| `CLASS_NAME` | As the field kind. |
| `ID` | As the field kind. |
| `DESCRIPTOR` | As the field kind, reusing the bounds of the field this position renders: a lawful descriptor necessarily reaches the sentence that shows it. |
| `INT` | As the field kind. |
| `FLOAT` | As the field kind. |
| `BOOL` | As the field kind. |
| `COUNT` | As the field kind. |
| `PATHLIKE` | A trusted configured path, `Path` or `str`. Argument-only: the payload field beside it carries the same path as an `IDENTIFIER`. |
| `COMPOSED` | A formatted fragment of identifiers, held to the named grammar below rather than to a string type. Argument-only, and the reason `IDENTIFIER` was not widened to cover punctuation. |

## The id syntaxes

What an `ID` field is held to. Each is anchored at both ends when it is
matched, so a pattern cannot admit a prefix. Patterns are printed quoted, here
and below, because a leading or trailing space is part of several of them and
a bare code span would hide it.

| Syntax | Pattern | Longest | What it is |
| --- | --- | --- | --- |
| `mac` | `'[0-9a-f]{2}(?::[0-9a-f]{2}){5}'` | 17 | The canonical form `normalize_mac` answers with. |
| `reported_mac` | `'[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}'` | 17 | The Device-Id header as the firmware sent it, which the OTA sentence renders beside the normalized form the field carries. Only a header `normalize_mac` accepted ever reaches that sentence, so the looser separator and case are the whole of the difference. |
| `session_id` | `'[0-9A-Za-z_-]{1,64}'` | 64 | A token this server minted. Production ids are `uuid4().hex`; the syntax is the bounded machine form rather than that one spelling, because the capture and store suites drive sessions of their own naming and a session id is never far-side bytes whoever chose it. |
| `conversation_id` | `'[0-9A-Za-z_-]{1,64}'` | 64 | A token this server minted for one conversation thread. Production ids are `uuid4().hex`, the same shape and the same bounded machine form a session id takes, and for the same reason: the store suites drive threads of their own naming, and a thread id is never far-side bytes whoever chose it. |
| `activation_code` | `'[0-9]{6}'` | 6 | A claim ticket read off a screen, not a credential. |
| `event_name` | `'[a-z][a-z0-9_]{0,63}'` | 64 | The registry's own key, carried in the payload as `event`. |
| `language` | `'[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8})*'` | 16 | A language code as an ASR engine reports it: the bare ISO 639 code or a tagged form such as `en-US`. |

## The composed grammars

What a `COMPOSED` argument is held to, with the code that builds it. Naming
the builder is what keeps a grammar honest: a fragment nobody assembles is a
pattern somebody guessed. They are bounded by structure rather than by
character class or length, since what a fragment promises is its shape and
never what an operator may have called something.

| Grammar | Pattern | Built by | What it is |
| --- | --- | --- | --- |
| `empty_fragment` | `''` | `vinga_server.events.values:Nothing` | The nothing a site renders where it has nothing to add. Declared rather than left untyped, so a variant that may only say nothing says exactly that. |
| `also_bound_to` | `'(?: \(also bound to [\s\S]+\))?'` | `vinga_server.events.values:AlsoBoundTo.of` | The tail naming the agents a device is bound to beside the one that answered, empty for a device bound to exactly one. The names inside it are comma joined, and the grammar does not say so: a configured name may itself hold a comma, so the joined fragment cannot be parsed back into the names that made it, and a pattern claiming otherwise would refuse a lawful deployment. |
| `agent_list` | `'[\s\S]+'` | `vinga_server.events.values:AgentList.of` | The configured agent names a device is bound to, comma-joined. Non-empty, and nothing further: see the tail grammar above for why the joining is not part of the claim. |
| `session_list` | `'[0-9A-Za-z_-]{1,64}(?:, [0-9A-Za-z_-]{1,64})*'` | `vinga_server.events.values:SessionList.of` | The session ids a prune removed, comma-joined. |
| `quoted_tool_name` | `' "[\s\S]+"'` | `vinga_server.events.values:QuotedToolName.of` | A builtin's name, which is this server's own word, bounded here by the quoting alone. A device tool's name is the board's vocabulary and an unknown one is whatever the model invented, so neither is ever rendered here. |
| `from_entry` | `' from entry "[\s\S]+"'` | `vinga_server.events.values:FromEntry.of` | The configured MCP entry a call reached, never the far side's own tool name. Entry names are separately held to `[A-Za-z0-9_-]+` by the configuration, which makes this grammar a floor rather than the whole truth; the floor is what this surface may claim, since the tighter rule is configuration's to keep and to change. |
| `quoted_provider` | `'(?: "[\s\S]+")?'` | `vinga_server.events.values:QuotedProvider.of` | The configuration entry the failing provider is, bounded by the quoting alone, and empty for a provider the registry never built. Optional for the reason the host tail below is: one variant says both, and a rendered position cannot be absent. |
| `reaching_host` | `'(?: reaching [\s\S]+)?'` | `vinga_server.events.values:ReachingHost.of` | Where the call was going, empty for an engine that runs in this process. |
| `origin_provenance` | `'(?:from\|guessed from) [\s\S]+'` | `vinga_server.onboarding.origin:Origin.provenance`, `vinga_server.events.values:OriginProvenance` | Which configuration key the banner's origin came out of, and whether it was read or inferred. |
| `device_or_unidentified` | `'[0-9a-f]{2}(?::[0-9a-f]{2}){5}\|an unidentified device'` | `vinga_server.events.values:DeviceOrUnidentified.of` | The MAC behind a Device-Id header this server recognizes, or the fixed phrase. Nothing else: with device auth off nothing has verified that header, so an unrecognized one names no device at all. |

## The prompt provenance grammar

`prompt_assembled.sources` is the one structured field: a mapping from where a
block of the prompt came from to how many characters it contributed, never any
of the prompt itself. Its keys take these forms, with `<name>` and `<entry>`
configured names and `<position>` a positive integer.

- `persona`
- `fragment:<name>`
- `instructions:<entry>`
- `server_instructions:<entry>`
- `server_prompt:<entry>:<position>`

`memory` is deliberately not among them. `prompt_assembled` reports the cached
know-how half of the prompt and excludes the per-round memory read, so a
`memory` key is a violation like any unknown prefix, even though it is a
provenance token elsewhere in the prompt assembly.

```text
persona|fragment:[A-Za-z0-9_-]+|instructions:[A-Za-z0-9_-]+|server_instructions:[A-Za-z0-9_-]+|server_prompt:[A-Za-z0-9_-]+:[1-9][0-9]*
```

## The events

One row per event, in the order the sections below run: the order a request
meets them, from a device's check-in to the server's own lifecycle surfaces.

| Event | Channels | Levels | Variants |
| --- | --- | --- | --- |
| `conversations_enabled` | `vinga_server.conversations.store` | WARNING | 1 |
| `conversations_dropped` | `vinga_server.conversations.store` | WARNING | 1 |
| `conversations_failed` | `vinga_server.conversations.store` | WARNING | 2 |
| `conversations_pruned` | `vinga_server.conversations.store` | INFO | 1 |
| `session_rejected` | `vinga_server.session`, `vinga_server.ws` | WARNING | 4 |
| `session_open` | `vinga_server.session` | INFO | 1 |
| `session_limit` | `vinga_server.session` | INFO | 1 |
| `session_idle` | `vinga_server.session` | INFO | 1 |
| `session_closed` | `vinga_server.session` | INFO | 1 |
| `speaking_started` | `vinga_server.session` | INFO | 1 |
| `heard` | `vinga_server.session` | INFO | 1 |
| `replied` | `vinga_server.session` | INFO | 1 |
| `agent_said` | `vinga_server.session` | INFO | 1 |
| `handover` | `vinga_server.session` | INFO | 1 |
| `conversation_resumed` | `vinga_server.session` | INFO | 1 |
| `milestone_recorded` | `vinga_server.session` | INFO | 1 |
| `prompt_assembled` | `vinga_server.session` | INFO | 1 |
| `llm_retry` | `vinga_server.session` | WARNING | 1 |
| `llm_round` | `vinga_server.session` | INFO | 1 |
| `provider_failed` | `vinga_server.session` | WARNING | 1 |
| `tool_call` | `vinga_server.session` | INFO | 3 |
| `barge_in` | `vinga_server.session` | INFO | 1 |
| `barge_in_suppressed` | `vinga_server.session` | INFO | 3 |
| `barge_in_merged` | `vinga_server.session` | INFO | 1 |
| `filler_skipped` | `vinga_server.session` | INFO | 2 |
| `filler_played` | `vinga_server.session` | INFO | 1 |
| `ota_check` | `vinga_server.ota` | INFO, WARNING | 4 |
| `activation_not_offered` | `vinga_server.ota` | WARNING | 2 |
| `activation_complete` | `vinga_server.ota` | INFO | 1 |
| `activation_pending` | `vinga_server.ota` | DEBUG | 1 |
| `activation_refused` | `vinga_server.ota` | WARNING | 3 |
| `ota_request_rejected` | `vinga_server.ota` | WARNING | 1 |
| `onboarding_banner` | `vinga_server.onboarding` | INFO | 2 |
| `onboarding_key_mismatch` | `vinga_server.onboarding` | WARNING | 1 |
| `onboarding_key_unshaped` | `vinga_server.onboarding` | WARNING | 1 |
| `auth_rejected` | `vinga_server.ws` | WARNING | 1 |
| `asr_prompt_echo` | `vinga_server.providers.openai_asr` | INFO, WARNING | 5 |
| `mcp_connected` | `vinga_server.tools.mcp` | INFO | 1 |
| `mcp_down` | `vinga_server.tools.mcp` | INFO, WARNING | 3 |
| `mcp_call_dropped` | `vinga_server.tools.mcp` | WARNING | 1 |
| `mcp_tool_shadowed` | `vinga_server.tools.mcp` | WARNING | 1 |
| `mcp_reload` | `vinga_server.tools.mcp` | INFO, WARNING | 2 |
| `provider_reaches_loopback` | `vinga_server.providers.world` | WARNING | 1 |
| `memory_unreadable` | `vinga_server.memory.store` | WARNING | 1 |
| `memory_unwritable` | `vinga_server.memory.store` | WARNING | 1 |
| `memory_cleanup_failed` | `vinga_server.memory.store` | WARNING | 1 |
| `filler_disabled` | `vinga_server.filler` | WARNING | 1 |
| `capture_started` | `vinga_server.capture` | INFO | 1 |
| `capture_declined` | `vinga_server.capture` | WARNING | 3 |
| `capture_limit` | `vinga_server.capture` | INFO | 1 |
| `capture_failed` | `vinga_server.capture` | WARNING | 1 |
| `capture_pruned` | `vinga_server.capture` | INFO | 1 |
| `capture_over_budget` | `vinga_server.capture` | WARNING | 1 |
| `capture_enabled` | `vinga_server.app` | WARNING | 1 |
| `capture_disabled` | `vinga_server.app` | INFO | 1 |
| `drain_started` | `vinga_server.registry` | INFO | 1 |
| `drain_finished` | `vinga_server.registry` | INFO | 1 |
| `drain_incomplete` | `vinga_server.registry` | WARNING | 1 |
| `device_bindings_unreadable` | `vinga_server.device.bindings` | WARNING | 1 |
| `api_error` | `vinga_server.config.api` | ERROR | 1 |
| `api_storage_error` | `vinga_server.config.api` | ERROR | 1 |

### `conversations_enabled`

The store opens at startup, which means this server is recording what is said
to it. Said once, before anything connects, and at WARNING for the reason
`capture_enabled` is.

#### Variant 1: `vinga_server.conversations.store` at WARNING

```text
recording conversations
```

No arguments: the sentence is fixed.

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |

### `conversations_dropped`

The store is behind and events for one session are being dropped. Said once
per session at its first drop; the total lands on that session's row.

#### Variant 1: `vinga_server.conversations.store` at WARNING

```text
session %s: the conversation store is behind, dropping events
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |

### `conversations_failed`

A write to the store failed and its batch was dropped, or a prune could not
run.

#### Variant 1: `vinga_server.conversations.store` at WARNING

```text
the conversation store dropped a batch after a write failed (%s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `failure` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `failure` | `CLASS_NAME` | yes | no |  | The exception's class name, never its message. |

#### Variant 2: `vinga_server.conversations.store` at WARNING

```text
the conversation store could not prune (%s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `failure` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `failure` | `CLASS_NAME` | yes | no |  |  |

### `conversations_pruned`

Retention deleted the conversations that had aged out of the window, and the
session records nothing points at any more. At INFO: a policy doing its job.

#### Variant 1: `vinga_server.conversations.store` at INFO

```text
conversations: pruned %d conversation(s) and %d session record(s) older than %d days
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `conversations` (`COUNT`) | no |  |  |
| 2 | `sessions` (`COUNT`) | no |  |  |
| 3 | `days` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `conversations` | `COUNT` | yes | no |  | Threads taken whole, with their turns. The unit retention prunes by, so this is the count that says how much dialogue left. |
| `sessions` | `COUNT` | yes | no |  | Session records taken, which is only the aged ones no surviving turn still names. A count, not a list. |

### `session_rejected`

A device turned away. Emitted on both scopes: the session channel for the
refusals a session makes after the accept, and `vinga_server.ws` for the one
the endpoint makes before a session can run at all.

#### Variant 1: `vinga_server.session` at WARNING

```text
session %s rejected: the Device-Id header is not a device MAC (six colon-separated hex pairs)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `bad_device_id` |  |

#### Variant 2: `vinga_server.session` at WARNING

```text
session %s rejected: device %s is bound to agent %s, which this server is not serving; install it with: vinga-server config apply
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `mac` (`ID`) | no | the `mac` syntax |  |
| 3 | `unloaded` (`COMPOSED`) | no | the `agent_list` grammar |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `agent_not_loaded` |  |

#### Variant 3: `vinga_server.session` at WARNING

```text
session %s rejected: device %s has no agent: bind it under devices or set default_agent
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `mac` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `no_agent` |  |

#### Variant 4: `vinga_server.ws` at WARNING

```text
refused a websocket handshake from %s: the server is at capacity
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `shown` (`COMPOSED`) | no | the `device_or_unidentified` grammar |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `capacity` |  |

### `session_open`

A conversation starts.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s open: device %s (client %s) agent %s%s, protocol v%d, %d Hz %d ms frames in
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `mac` (`ID`) | no | the `mac` syntax |  |
| 3 | `said_client` (`DESCRIPTOR`) | no | at most 64 characters, every one printable |  |
| 4 | `agent` (`IDENTIFIER`) | no |  |  |
| 5 | `bound_tail` (`COMPOSED`) | no | the `also_bound_to` grammar |  |
| 6 | `protocol` (`INT`) | no |  |  |
| 7 | `sample_rate` (`INT`) | no |  |  |
| 8 | `frame_ms` (`INT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `client` | `DESCRIPTOR` | yes | yes | at most 64 characters, every one printable | The device UUID, bounded for the event only: the capture manifest and the conversation store keep the header as it arrived. |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `agents` | `IDENTIFIER_LIST` | yes | no |  |  |
| `protocol` | `INT` | yes | no |  |  |
| `revision` | `IDENTIFIER` | yes | no |  | Which build this server is, so every session from here on is attributable to one. |

### `session_limit`

The duration cap fires.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s reached the %.0f s time limit
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `limit_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `duration_s` | `FLOAT` | yes | no |  |  |

### `session_idle`

The idle timeout hangs up on a realtime session.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s idle for %.0f s, hanging up
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `idle_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `idle_s` | `FLOAT` | yes | no |  |  |
| `duration_s` | `FLOAT` | yes | no |  |  |

### `session_closed`

A conversation ends.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s closed (device %s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `mac` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `duration_s` | `FLOAT` | yes | no |  |  |
| `reason` | `TOKEN` | yes | no | one of: `client`, `drain`, `error`, `idle`, `limit` | The first cause to fire, so a drain closing a session an idle timer was about to hang up on reads `drain`. |

### `speaking_started`

The reply's first audio frame goes out.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: speaking started
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |

### `heard`

An utterance is transcribed. No transcript: what was said is the conversation
store's, and what an operator measures with is how long the user spoke.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: heard %.2f s of speech
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `duration_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `duration_s` | `FLOAT` | yes | no |  |  |
| `language` | `ID` | no | no | the `language` syntax | Only engines that detected carry this. |
| `language_confidence` | `FLOAT` | no | no |  |  |

### `replied`

A reply finishes.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: %s replied in %d sentences
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `agent` (`IDENTIFIER`) | no |  |  |
| 3 | `sentences` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `sentences` | `COUNT` | yes | no |  | How many of them the user heard, so a reply a barge-in cut short reports what went out. |

### `agent_said`

One agent's part of a reply.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: %s said %d sentences
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `agent` (`IDENTIFIER`) | no |  |  |
| 3 | `sentences` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `sentences` | `COUNT` | yes | no |  |  |

### `handover`

`switch_agent` succeeds.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: handed over from agent %s to %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `from_agent` (`IDENTIFIER`) | no |  |  |
| 3 | `to_agent` (`IDENTIFIER`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `from_agent` | `IDENTIFIER` | yes | no |  |  |
| `to_agent` | `IDENTIFIER` | yes | no |  |  |
| `from_conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the outgoing agent was on, which the handover turn belongs to. |
| `to_conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the incoming agent is on: its own, minted at its first activation in this session and continued at every later one. |

### `conversation_resumed`

A stored thread is picked up again, at the boundary the selection ended a
reply on. Counts and a flag: which thread, how much of it was rebuilt, how
much of it could not be, and whether there was more of it than the budget had
room for.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: resumed conversation %s from %d stored turns
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `conversation` (`ID`) | no | the `conversation_id` syntax |  |
| 3 | `turns` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the session moved onto, which the agent that asked for it is bound to from here. |
| `turns` | `COUNT` | yes | no |  | How many stored turns were rebuilt into the model's context. A count and never the dialogue: what was said is the store's. |
| `skipped` | `COUNT` | yes | no |  | How many stored turns could not be rebuilt because no text was kept for them, which is what a thread recorded under the stricter storage setting leaves behind. |
| `over_budget` | `BOOL` | yes | no |  | Whether the thread held more than the hydration budget had room for, so what was rebuilt is its recent tail. |

### `milestone_recorded`

A consented recap is stored as a checkpoint on its thread, after the user has
heard it. The thread's identity and nothing else: what the recap says is what
a room said, so it is conversation content and belongs to the store.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: recorded a recap milestone on conversation %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `conversation` (`ID`) | no | the `conversation_id` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the checkpoint was recorded on. The identity and nothing else: a recap is a summary of what a room said, so it is conversation content and lives in the store under the text switch, never on a decision track. |

### `prompt_assembled`

The know-how half of a prompt is assembled and cached. The per-round memory
read is deliberately not part of it, which is why `memory` is not one of the
provenance forms.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: assembled %d characters of prompt for %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `characters` (`COUNT`) | no |  |  |
| 3 | `agent` (`IDENTIFIER`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `characters` | `COUNT` | yes | no |  |  |
| `sources` | `SOURCES` | yes | no | keyed by the prompt provenance grammar, with counts for values | Each block's size by provenance: how much of the prompt came from where, never any of the prompt itself. |

### `llm_retry`

The first-token watchdog cancels a stalled generation and retries the round
once.

#### Variant 1: `vinga_server.session` at WARNING

`provider` and `type` are atomic: a provider with an identity carries both,
and one the registry never built carries neither. `host` is absent for an
engine that runs in this process and `model` for a type that has none to name.

```text
session %s: no first token after %.1f s, retrying round %d
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `duration_s` (`FLOAT`) | no |  |  |
| 3 | `round` (`INT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `round` | `INT` | yes | no |  |  |
| `duration_ms` | `INT` | yes | no |  |  |
| `stage` | `IDENTIFIER` | yes | no |  |  |
| `provider` | `IDENTIFIER` | no | no |  |  |
| `type` | `IDENTIFIER` | no | no |  |  |
| `host` | `IDENTIFIER` | no | no |  |  |
| `model` | `IDENTIFIER` | no | no |  | The GenAI conventions' `gen_ai.request.model`. |

### `llm_round`

A generation call finishes.

#### Variant 1: `vinga_server.session` at INFO

`provider` and `type` are atomic: a provider with an identity carries both,
and one the registry never built carries neither. `host` is absent for an
engine that runs in this process and `model` for a type that has none to name.

```text
session %s: %s round %d took %.2f s over %d turns
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `agent` (`IDENTIFIER`) | no |  |  |
| 3 | `round` (`INT`) | no |  |  |
| 4 | `duration_s` (`FLOAT`) | no |  |  |
| 5 | `turns` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `round` | `INT` | yes | no |  | Counts the whole reply rather than one agent's leg, so the generation after a handover is a round of its own. |
| `turns` | `COUNT` | yes | no |  | The cheap proxy for payload size. |
| `duration_ms` | `INT` | yes | no |  |  |
| `stage` | `IDENTIFIER` | yes | no |  |  |
| `provider` | `IDENTIFIER` | no | no |  |  |
| `type` | `IDENTIFIER` | no | no |  |  |
| `host` | `IDENTIFIER` | no | no |  |  |
| `model` | `IDENTIFIER` | no | no |  | Present where the configured entry names one. The GenAI conventions' `gen_ai.request.model`. |
| `input_tokens` | `COUNT` | no | no |  | Present where the provider reported usage; their absence is a fact about the endpoint. |
| `output_tokens` | `COUNT` | no | no |  |  |
| `first_token_ms` | `INT` | no | no |  | Times the first spoken token, so a round that only asked for a tool carries none. |

### `provider_failed`

An ASR, LLM or TTS call fails. The class name is reported and the exception's
message is not: a type name says what went wrong, a message says what a
stranger wrote.

#### Variant 1: `vinga_server.session` at WARNING

`provider` and `type` are atomic: a provider with an identity carries both,
and one the registry never built carries neither and names no entry and no
host in the sentence either. `host` is absent for an engine that runs in this
process and `model` for a type that has none to name.

```text
session %s: %s provider%s %s after %.2f s%s: %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `stage` (`IDENTIFIER`) | no |  |  |
| 3 | `named` (`COMPOSED`) | no | the `quoted_provider` grammar |  |
| 4 | `outcome` (`TOKEN`) | no | one of: `failed`, `timed out` |  |
| 5 | `duration_s` (`FLOAT`) | no |  |  |
| 6 | `where` (`COMPOSED`) | no | the `reaching_host` grammar |  |
| 7 | `error` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `error` | `CLASS_NAME` | yes | no |  | A round whose retry also stalled carries `FirstTokenTimeout`. |
| `duration_ms` | `INT` | yes | no |  |  |
| `stage` | `IDENTIFIER` | yes | no |  |  |
| `provider` | `IDENTIFIER` | no | no |  |  |
| `type` | `IDENTIFIER` | no | no |  |  |
| `host` | `IDENTIFIER` | no | no |  |  |
| `model` | `IDENTIFIER` | no | no |  |  |

### `tool_call`

A tool returns. `source` says which namespace the model reached into; the name
itself is only ever this server's own word for it.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: %s tool%s took %.2f s%s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `source` (`TOKEN`) | no | one of: `builtin` |  |
| 3 | `named` (`COMPOSED`) | no | the `quoted_tool_name` grammar |  |
| 4 | `duration_s` (`FLOAT`) | no |  |  |
| 5 | `outcome` (`TOKEN`) | no | one of: `''`, `' and failed'` |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `source` | `TOKEN` | yes | no | one of: `builtin` |  |
| `tool` | `IDENTIFIER` | yes | no |  | The only tool names this server authors. |
| `duration_ms` | `INT` | yes | no |  |  |
| `is_error` | `BOOL` | yes | no |  |  |

#### Variant 2: `vinga_server.session` at INFO

```text
session %s: %s tool%s took %.2f s%s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `source` (`TOKEN`) | no | one of: `mcp` |  |
| 3 | `named` (`COMPOSED`) | no | the `from_entry` grammar |  |
| 4 | `duration_s` (`FLOAT`) | no |  |  |
| 5 | `outcome` (`TOKEN`) | no | one of: `''`, `' and failed'` |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `source` | `TOKEN` | yes | no | one of: `mcp` |  |
| `entry` | `IDENTIFIER` | yes | no |  | The configured entry, never the far side's tool name. |
| `duration_ms` | `INT` | yes | no |  |  |
| `is_error` | `BOOL` | yes | no |  |  |

#### Variant 3: `vinga_server.session` at INFO

A device tool's name is the board's vocabulary and an unknown one is whatever
the model invented, so neither is named.

```text
session %s: %s tool%s took %.2f s%s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `source` (`TOKEN`) | no | one of: `device`, `unknown` |  |
| 3 | `named` (`COMPOSED`) | no | the `empty_fragment` grammar |  |
| 4 | `duration_s` (`FLOAT`) | no |  |  |
| 5 | `outcome` (`TOKEN`) | no | one of: `''`, `' and failed'` |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `source` | `TOKEN` | yes | no | one of: `device`, `unknown` |  |
| `duration_ms` | `INT` | yes | no |  |  |
| `is_error` | `BOOL` | yes | no |  |  |

### `barge_in`

Speech cuts a reply short.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: barge-in, cancelling the reply in flight
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `speech_ms` | `INT` | yes | no |  |  |
| `speaking_ms` | `INT` | no | no |  | Milliseconds from `speaking_started` to the cancel decision, absent when the reply had not yet spoken. |

### `barge_in_suppressed`

An interruption is dropped and the reply lives.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: barge-in suppressed, %d ms of speech is under the %.0f ms floor
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `speech_ms` (`INT`) | no |  |  |
| 3 | `floor_ms` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `min_speech` |  |
| `speech_ms` | `INT` | yes | no |  |  |

#### Variant 2: `vinga_server.session` at INFO

```text
session %s: barge-in suppressed inside the refractory window
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `refractory` |  |
| `speech_ms` | `INT` | yes | no |  |  |

#### Variant 3: `vinga_server.session` at INFO

```text
session %s: barge-in suppressed, nothing transcribed
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `no_transcript` |  |
| `speech_ms` | `INT` | yes | no |  |  |

### `barge_in_merged`

An interruption merges with the utterance the reply was transcribing.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: barge-in mid-transcription, merging the utterances
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `speech_ms` | `INT` | yes | no |  |  |

### `filler_skipped`

The filler timer fired but the user was there first, so no clip played.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: filler skipped, the user is speaking (%d ms heard)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `speech_ms` (`INT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `reason` | `TOKEN` | yes | no | one of: `user_speaking` |  |
| `speech_ms` | `INT` | yes | no |  |  |

#### Variant 2: `vinga_server.session` at INFO

```text
session %s: filler skipped, a barge-in is being confirmed
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `reason` | `TOKEN` | yes | no | one of: `barge_in_pending` |  |

### `filler_played`

The reply was slow, so a pre-synthesized clip masked the wait. Its first frame
is the turn's `speaking_started`.

#### Variant 1: `vinga_server.session` at INFO

```text
session %s: no reply audio after %d ms, playing filler %d
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `delay_ms` (`INT`) | no |  |  |
| 3 | `phrase_index` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `conversation` | `ID` | yes | no | the `conversation_id` syntax | The thread the agent was talking on, stamped by the same activation that stamped the agent. A server-minted id and therefore metadata; what was said on the thread is the store's. |
| `delay_ms` | `INT` | yes | no |  | Measured, from the transcription to the fire. |
| `phrase_index` | `COUNT` | yes | no |  |  |

### `ota_check`

What a device said about itself at its configuration check, and what this
server resolved it to. No session exists yet, so the record names the device
instead.

#### Variant 1: `vinga_server.ota` at WARNING

```text
device %s (%s, firmware %s) has no agent and is showing activation code %s; bind it with: vinga-server config device pending claim %s <agent>
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `said_device` (`ID`) | no | the `reported_mac` syntax |  |
| 2 | `board` (`DESCRIPTOR`) | no | at most 64 characters, every one printable |  |
| 3 | `firmware` (`DESCRIPTOR`) | no | at most 32 characters, every one printable |  |
| 4 | `code` (`ID`) | no | the `activation_code` syntax |  |
| 5 | `code` (`ID`) | no | the `activation_code` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `client` | `DESCRIPTOR` | yes | yes | at most 64 characters, every one printable | The device UUID, bounded for the event only: the token the reply issues is still signed for the header exactly as it arrived. |
| `board` | `DESCRIPTOR` | yes | no | at most 64 characters, every one printable | What the device calls itself. `unknown` when it said nothing usable. |
| `firmware` | `DESCRIPTOR` | yes | no | at most 32 characters, every one printable | The only moment a device ever states its firmware version: the websocket handshake does not carry it. |
| `agents` | `IDENTIFIER_LIST` | yes | no |  |  |
| `unloaded` | `IDENTIFIER_LIST` | yes | no |  | Agents this device is bound to that the world this server is serving does not hold. Named on every record rather than only on the one that complains, so a query for devices waiting on a reload is one field. |
| `code` | `ID` | yes | no | the `activation_code` syntax |  |

#### Variant 2: `vinga_server.ota` at WARNING

```text
device %s (%s, firmware %s) is bound to agent %s, which this server is not serving; install it with: vinga-server config apply
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `said_device` (`ID`) | no | the `reported_mac` syntax |  |
| 2 | `board` (`DESCRIPTOR`) | no | at most 64 characters, every one printable |  |
| 3 | `firmware` (`DESCRIPTOR`) | no | at most 32 characters, every one printable |  |
| 4 | `named` (`COMPOSED`) | no | the `agent_list` grammar |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `client` | `DESCRIPTOR` | yes | yes | at most 64 characters, every one printable |  |
| `board` | `DESCRIPTOR` | yes | no | at most 64 characters, every one printable |  |
| `firmware` | `DESCRIPTOR` | yes | no | at most 32 characters, every one printable |  |
| `agents` | `IDENTIFIER_LIST` | yes | no |  |  |
| `unloaded` | `IDENTIFIER_LIST` | yes | no |  |  |

#### Variant 3: `vinga_server.ota` at WARNING

```text
device %s (%s, firmware %s) has no agent: bind it under devices or set default_agent
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `said_device` (`ID`) | no | the `reported_mac` syntax |  |
| 2 | `board` (`DESCRIPTOR`) | no | at most 64 characters, every one printable |  |
| 3 | `firmware` (`DESCRIPTOR`) | no | at most 32 characters, every one printable |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `client` | `DESCRIPTOR` | yes | yes | at most 64 characters, every one printable |  |
| `board` | `DESCRIPTOR` | yes | no | at most 64 characters, every one printable |  |
| `firmware` | `DESCRIPTOR` | yes | no | at most 32 characters, every one printable |  |
| `agents` | `IDENTIFIER_LIST` | yes | no |  |  |
| `unloaded` | `IDENTIFIER_LIST` | yes | no |  |  |

#### Variant 4: `vinga_server.ota` at INFO

```text
device %s (%s, firmware %s) resolved to agent %s%s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `said_device` (`ID`) | no | the `reported_mac` syntax |  |
| 2 | `board` (`DESCRIPTOR`) | no | at most 64 characters, every one printable |  |
| 3 | `firmware` (`DESCRIPTOR`) | no | at most 32 characters, every one printable |  |
| 4 | `agent` (`IDENTIFIER`) | no |  |  |
| 5 | `bound_tail` (`COMPOSED`) | no | the `also_bound_to` grammar |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `client` | `DESCRIPTOR` | yes | yes | at most 64 characters, every one printable |  |
| `board` | `DESCRIPTOR` | yes | no | at most 64 characters, every one printable |  |
| `firmware` | `DESCRIPTOR` | yes | no | at most 32 characters, every one printable |  |
| `agents` | `IDENTIFIER_LIST` | yes | no |  |  |
| `unloaded` | `IDENTIFIER_LIST` | yes | no |  |  |

### `activation_not_offered`

An unbound device that was answered with no activation code, and why.

#### Variant 1: `vinga_server.ota` at WARNING

```text
device %s is unbound in the configuration this server started with, but the database could not be read, so no activation code was issued: this device may already be bound. Fix the database and it is offered one at its next check
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `device` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `unreadable` |  |

#### Variant 2: `vinga_server.ota` at WARNING

```text
device %s is unbound but was offered no activation code: %s. It is answered exactly as it was before onboarding existed, with no token; bind it by its MAC with: vinga-server config device bind %s <agent>
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `device` (`ID`) | no | the `mac` syntax |  |
| 2 | `reason` (`TOKEN`) | no | one of: `128 devices are already waiting to be claimed, which is the cap`, `30 activation codes have been issued in the last 10 minutes, which is the limit` |  |
| 3 | `device` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `128 devices are already waiting to be claimed, which is the cap`, `30 activation codes have been issued in the last 10 minutes, which is the limit` |  |

### `activation_complete`

A waiting device has been claimed; its next check hands it a token.

#### Variant 1: `vinga_server.ota` at INFO

```text
device %s is activated: its next configuration check hands it a token
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `device` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `agents` | `IDENTIFIER_LIST` | yes | no |  |  |

### `activation_pending`

A waiting device polled and is still waiting.

#### Variant 1: `vinga_server.ota` at DEBUG

```text
device %s is still waiting to be claimed
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `device` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `code` | `ID` | yes | yes | the `activation_code` syntax | Null for a MAC this server holds no pending entry for. |
| `unloaded` | `IDENTIFIER_LIST` | yes | no |  |  |

### `activation_refused`

A version-2 activation poll failed one of the checks this server can hold it
to. Nothing of the body is ever quoted: the checks name which one failed and
stop there.

#### Variant 1: `vinga_server.ota` at WARNING

```text
device %s sent a version-2 activation body that is not a JSON object; it is answered as still waiting. Nothing of the body is quoted here
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `device` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `code` | `ID` | yes | no | the `activation_code` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `unreadable_body` |  |

#### Variant 2: `vinga_server.ota` at WARNING

```text
device %s sent a version-2 activation body naming an algorithm this server does not know; it is answered as still waiting. The value is not quoted here, since it is whatever the request carried
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `device` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `code` | `ID` | yes | no | the `activation_code` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `unknown_algorithm` |  |

#### Variant 3: `vinga_server.ota` at WARNING

```text
device %s sent a version-2 activation body answering a challenge this server did not issue for it; it is answered as still waiting
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `device` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `code` | `ID` | yes | no | the `activation_code` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `challenge_mismatch` |  |

### `ota_request_rejected`

A request this endpoint could not read. The sentence is one of three fixed
refusals, so nothing a request carried is interpolated into the retained log.

#### Variant 1: `vinga_server.ota` at WARNING

```text
rejected OTA request: %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `refusal` (`TOKEN`) | no | one of: `the Client-Id header is required and holds the device UUID`, `the Device-Id header does not hold a MAC address; it has to be six colon-separated hex pairs, for example aa:bb:cc:dd:ee:ff. What was sent is not quoted back, since a header that missed the MAC may hold anything at all`, `the Device-Id header is required and holds the device MAC` |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |

### `onboarding_banner`

Where devices are configured, said once at startup.

#### Variant 1: `vinga_server.onboarding` at INFO

```text
device onboarding is off: devices are configured at the server.ota_path path on %s (%s), which is not printed here, since that segment is this deployment's secret
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `origin` (`IDENTIFIER`) | no |  |  |
| 2 | `provenance` (`COMPOSED`) | no | the `origin_provenance` grammar |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `origin` | `IDENTIFIER` | yes | no |  |  |
| `origin_source` | `TOKEN` | yes | no | one of: `server.public_url`, `server.websocket_url`, `the listen address (server.host and server.port)` |  |
| `onboarding` | `BOOL` | yes | no |  |  |

#### Variant 2: `vinga_server.onboarding` at INFO

```text
device onboarding is on: devices are configured on %s (%s), at the short path vinga-server config ota-url prints. The path is not repeated here, since its key stands in front of the endpoint that issues device tokens
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `origin` (`IDENTIFIER`) | no |  |  |
| 2 | `provenance` (`COMPOSED`) | no | the `origin_provenance` grammar |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `origin` | `IDENTIFIER` | yes | no |  |  |
| `origin_source` | `TOKEN` | yes | no | one of: `server.public_url`, `server.websocket_url`, `the listen address (server.host and server.port)` |  |
| `onboarding` | `BOOL` | yes | no |  |  |
| `keyed` | `BOOL` | yes | no |  | Whether anything stands in front of the short route at all. A fact about the deployment rather than about the key, which is what makes it safe to say. |

### `onboarding_key_mismatch`

A request carried a key-shaped segment, and not this server's. Neither is
repeated.

#### Variant 1: `vinga_server.onboarding` at WARNING

```text
a request reached the onboarding path carrying %d characters shaped like a key, and not this server's; neither is repeated here. Check the URL typed into the device's captive portal against the one printed by vinga-server config ota-url
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `attempted_length` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `attempted_length` | `COUNT` | yes | no |  |  |

### `onboarding_key_unshaped`

A request carried something that is not key-shaped at all.

#### Variant 1: `vinga_server.onboarding` at WARNING

```text
a request reached the onboarding path carrying %d characters that are not shaped like a key at all, so they are not repeated here; the URL to type comes from vinga-server config ota-url
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `attempted_length` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `attempted_length` | `COUNT` | yes | no |  |  |

### `auth_rejected`

A handshake refused before the accept. No device: nothing is authenticated at
this point, so the Device-Id header is a string whoever opened the socket
chose.

#### Variant 1: `vinga_server.ws` at WARNING

```text
refused a websocket handshake from an unidentified client: %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `reason` (`TOKEN`) | no | one of: `bad_token`, `no_token` |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | yes | the `mac` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `bad_token`, `no_token` |  |

### `asr_prompt_echo`

A transcript came back as the ASR prompt and the clip was retried once without
it, on what the first request left of `timeout_s`. No session or device:
providers are shared singletons that serve every conversation, so the event
names the host instead.

#### Variant 1: `vinga_server.providers.openai_asr` at WARNING

```text
openai asr: the transcript came back as the configured prompt with %.1f s of the timeout left, too little to retry, treating %.2f s of audio as nothing said
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `remaining_s` (`FLOAT`) | no |  |  |
| 2 | `duration_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `outcome` | `TOKEN` | yes | no | one of: `skipped` | Under a second of budget remained, so no retry was sent. |
| `duration_s` | `FLOAT` | yes | no |  |  |
| `host` | `IDENTIFIER` | yes | no |  |  |

#### Variant 2: `vinga_server.providers.openai_asr` at WARNING

```text
openai asr: the retry outran the timeout's remaining %.1f s, treating %.2f s of audio as nothing said
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `remaining_s` (`FLOAT`) | no |  |  |
| 2 | `duration_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `outcome` | `TOKEN` | yes | no | one of: `timed_out` | The retry outran what the first request left of the budget. |
| `duration_s` | `FLOAT` | yes | no |  |  |
| `host` | `IDENTIFIER` | yes | no |  |  |
| `retry_ms` | `INT` | yes | no |  |  |

#### Variant 3: `vinga_server.providers.openai_asr` at WARNING

```text
openai asr: the retry came back as the prompt again, treating %.2f s of audio as nothing said
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `duration_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `outcome` | `TOKEN` | yes | no | one of: `confirmed_echo` | The retry came back as the configured prompt again. |
| `duration_s` | `FLOAT` | yes | no |  |  |
| `host` | `IDENTIFIER` | yes | no |  |  |
| `retry_ms` | `INT` | yes | no |  |  |

#### Variant 4: `vinga_server.providers.openai_asr` at WARNING

```text
openai asr: the retry came back empty, treating %.2f s of audio as nothing said
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `duration_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `outcome` | `TOKEN` | yes | no | one of: `confirmed_empty` | The retry heard nothing. |
| `duration_s` | `FLOAT` | yes | no |  |  |
| `host` | `IDENTIFIER` | yes | no |  |  |
| `retry_ms` | `INT` | yes | no |  |  |

#### Variant 5: `vinga_server.providers.openai_asr` at INFO

```text
openai asr: the retry recovered %.2f s of audio the echo guard would have discarded
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `duration_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `outcome` | `TOKEN` | yes | no | one of: `recovered` | The retry's transcript is heard. What was recovered is not in the sentence: conversation-derived text is banned on the events however it was recovered (#165). |
| `duration_s` | `FLOAT` | yes | no |  |  |
| `host` | `IDENTIFIER` | yes | no |  |  |
| `retry_ms` | `INT` | yes | no |  |  |

### `mcp_connected`

An entry's connect finishes and its tools are published. No session or device:
one entry serves every conversation, and the rest of this block is the same.

#### Variant 1: `vinga_server.tools.mcp` at INFO

```text
mcp server %s connected with %d tool(s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `entry` (`IDENTIFIER`) | no |  |  |
| 2 | `tools` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `entry` | `IDENTIFIER` | yes | no |  |  |
| `transport` | `TOKEN` | yes | no | one of: `stdio`, `streamable_http` |  |
| `tools` | `COUNT` | yes | no |  | A count, never a list. |
| `duration_ms` | `INT` | yes | no |  |  |

### `mcp_down`

An entry fails to come up, or its connection is given up.

#### Variant 1: `vinga_server.tools.mcp` at WARNING

```text
mcp server %s is unavailable, its tools are absent: %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `entry` (`IDENTIFIER`) | no |  |  |
| 2 | `failure` (`CLASS_NAME`) | no | one name, or several joined with `, ` |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `entry` | `IDENTIFIER` | yes | no |  |  |
| `reason` | `TOKEN` | yes | no | one of: `connect_timeout`, `discovery_failed`, `initialize_failed`, `transport_failed` |  |
| `duration_ms` | `INT` | yes | no |  | How long the connect ran before it failed. |

#### Variant 2: `vinga_server.tools.mcp` at INFO

The intentional one, a shutdown or a reload, and the only `mcp_down` at INFO.
No duration: how long a working connection lasted is a different number under
the same name.

```text
mcp server %s is stopped and its tools are gone
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `entry` (`IDENTIFIER`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `entry` | `IDENTIFIER` | yes | no |  |  |
| `reason` | `TOKEN` | yes | no | one of: `stopped` |  |

#### Variant 3: `vinga_server.tools.mcp` at WARNING

Always beside an `mcp_call_dropped`, in that order.

```text
mcp server %s: dropping the connection after a failed call
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `entry` (`IDENTIFIER`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `entry` | `IDENTIFIER` | yes | no |  |  |
| `reason` | `TOKEN` | yes | no | one of: `call_failed` |  |

### `mcp_call_dropped`

A tool call failed and the connection was dropped because of it. The tool is
said by its position in the far side's listing and never by its name: half a
published name is what the far side called its tool.

#### Variant 1: `vinga_server.tools.mcp` at WARNING

```text
mcp server %s: the call to published tool %s failed (%s), so its answer is lost
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `entry` (`IDENTIFIER`) | no |  |  |
| 2 | `position` (`COUNT`) | yes |  |  |
| 3 | `error` (`CLASS_NAME`) | no | one name, or several joined with `, ` |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `entry` | `IDENTIFIER` | yes | no |  |  |
| `position` | `COUNT` | yes | yes |  | The tool's place in the far side's listing, counted from one. Null for a name this connection no longer knows. |
| `error` | `CLASS_NAME` | yes | no | one name, or several joined with `, ` | The failure's class name, and for a group of them the sorted names joined with a comma. Never a message. |

### `mcp_tool_shadowed`

A published tool is dropped because a more specific entry owns its name.

#### Variant 1: `vinga_server.tools.mcp` at WARNING

```text
mcp server %s: dropping published tool %d, its name is inside the namespace of the entry %s, which owns it
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `entry` (`IDENTIFIER`) | no |  |  |
| 2 | `position` (`COUNT`) | no |  |  |
| 3 | `owner` (`IDENTIFIER`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `entry` | `IDENTIFIER` | yes | no |  |  |
| `position` | `COUNT` | yes | no |  | The tool's place in the far side's listing. |
| `owner` | `IDENTIFIER` | yes | no |  |  |

### `mcp_reload`

A reload of the MCP servers finishes, whether or not the caller is still
connected. Exactly one per reload, at whichever of the two phases ended it.

#### Variant 1: `vinga_server.tools.mcp` at WARNING

```text
mcp servers were not reloaded and nothing was changed (%s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `reason` (`TOKEN`) | no | one of: `database_busy`, `in_progress`, `invalid`, `unexpected`, `unreadable` |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `outcome` | `TOKEN` | yes | no | one of: `refused` |  |
| `reason` | `TOKEN` | yes | no | one of: `database_busy`, `in_progress`, `invalid`, `unexpected`, `unreadable` | Chosen where the exception is classified and never built out of its message. |

#### Variant 2: `vinga_server.tools.mcp` at INFO

```text
mcp servers reloaded: %d started, %d restarted, %d stopped, %d unchanged
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `started` (`COUNT`) | no |  |  |
| 2 | `restarted` (`COUNT`) | no |  |  |
| 3 | `stopped` (`COUNT`) | no |  |  |
| 4 | `unchanged` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `outcome` | `TOKEN` | yes | no | one of: `applied` |  |
| `started` | `COUNT` | yes | no |  |  |
| `restarted` | `COUNT` | yes | no |  |  |
| `stopped` | `COUNT` | yes | no |  |  |
| `unchanged` | `COUNT` | yes | no |  |  |
| `duration_ms` | `INT` | yes | no |  | Measured from when the request was accepted, so it covers the re-read as well as the apply. |

### `provider_reaches_loopback`

A provider entry built inside a container names this machine in its endpoint,
which inside a container is the container. At WARNING and once per build of
the entry, on the boot and on every apply that rebuilds it. Never a refusal:
the same configuration is correct where the endpoint shares the container or
its network namespace.

#### Variant 1: `vinga_server.providers.world` at WARNING

A warning and never a refusal: localhost is right for an endpoint sharing this
container or its network namespace, and only the deployment knows which it is.

```text
providers.%s.%s reaches %s, which inside a container is the container itself rather than the machine running it; if that endpoint is on the machine, name it instead (host.docker.internal, which the compose file resolves on every platform) and apply
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `stage` (`IDENTIFIER`) | no |  |  |
| 2 | `provider` (`IDENTIFIER`) | no |  |  |
| 3 | `host` (`TOKEN`) | no | one of: `127.0.0.1`, `::1`, `localhost` |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `stage` | `IDENTIFIER` | yes | no |  |  |
| `provider` | `IDENTIFIER` | yes | no |  | The configuration entry, by name. |
| `type` | `IDENTIFIER` | yes | no |  | The entry's provider type. |
| `host` | `TOKEN` | yes | no | one of: `127.0.0.1`, `::1`, `localhost` | Which spelling of this machine the entry's endpoint named. The whole of what this event says about a base_url, and a closed set of three, so nothing an operator wrote can ride it. |

### `memory_unreadable`

One scope of an agent's memory could not be read, so the agent remembers
nothing of it this round. Said once per scope the read could not answer, since
a read that serves a whole prompt answers three of them.

#### Variant 1: `vinga_server.memory.store` at WARNING

```text
could not read %s memory for agent %s (%s); it remembers nothing of it this round
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `scope` (`TOKEN`) | no | one of: `agent`, `conversation`, `device` |  |
| 2 | `agent` (`IDENTIFIER`) | no |  |  |
| 3 | `error` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  | The agent whose reply the read was for, which is the acting agent rather than the owner of the rows: a device scope is read on behalf of whichever agent is speaking on that device. |
| `scope` | `TOKEN` | yes | no | one of: `agent`, `conversation`, `device` | Which memory could not be read. A read that answers several scopes at once says this once per scope it could not answer, so nothing renders empty unreported. |
| `error` | `CLASS_NAME` | yes | no |  |  |

### `memory_unwritable`

A change an agent asked for could not be stored, so nothing was changed. The
write path's own event, beside the read path's above: the two fail differently
and answer differently. A read that fails is contained (the agent remembers
nothing of that scope this round and the reply happens), while a write that
fails is a sanitized refusal the model reads out, so this is where an operator
learns what the database actually said, by class name and never by message.

#### Variant 1: `vinga_server.memory.store` at WARNING

```text
could not write %s memory for agent %s (%s); nothing was changed
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `scope` (`TOKEN`) | no | one of: `agent`, `conversation`, `device` |  |
| 2 | `agent` (`IDENTIFIER`) | no |  |  |
| 3 | `error` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  | The agent that was acting, by its configured name. |
| `scope` | `TOKEN` | yes | no | one of: `agent`, `conversation`, `device` | Which memory the refused write was for. |
| `error` | `CLASS_NAME` | yes | no |  |  |

### `memory_cleanup_failed`

State and held facts belonging to conversations that are gone could not be
removed. Agent-free on purpose, beside the two events above: the boot sweep
and a session's own teardown act for no agent and on no scope, so neither of
the other two could report this honestly. Nothing is lost by a failure here
except the space, and the next sweep takes what this one did not.

#### Variant 1: `vinga_server.memory.store` at WARNING

```text
could not remove the memory of conversations that are gone (%s); the next sweep takes what this one did not
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `error` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `error` | `CLASS_NAME` | yes | no |  | The class of the failure, and the whole of what this event carries. There is no agent and no scope, because what this answers for is the memory of threads nobody owns any more. |

### `filler_disabled`

Filler synthesis failed for one agent, so latency masking is off for it.

#### Variant 1: `vinga_server.filler` at WARNING

```text
agent %s: filler synthesis failed, latency masking is off for this agent (%s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `agent` (`IDENTIFIER`) | no |  |  |
| 2 | `error` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `agent` | `IDENTIFIER` | yes | no |  |  |
| `error` | `CLASS_NAME` | yes | no |  |  |

### `capture_started`

A session is being recorded.

#### Variant 1: `vinga_server.capture` at INFO

```text
session %s: capturing to %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `path` (`PATHLIKE`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `path` | `IDENTIFIER` | yes | no |  |  |

### `capture_declined`

A session is not being recorded, and why.

#### Variant 1: `vinga_server.capture` at WARNING

```text
session %s: not capturing, %s is unusable (%s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `directory` (`PATHLIKE`) | no |  |  |
| 3 | `failure` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `unusable` |  |
| `failure` | `CLASS_NAME` | yes | no |  |  |

#### Variant 2: `vinga_server.capture` at WARNING

```text
session %s: not capturing, %.0f MB free is below the %.0f MB floor
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `free` (`FLOAT`) | no |  |  |
| 3 | `floor_mb` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `min_free_mb` |  |
| `free_mb` | `COUNT` | yes | no |  |  |

#### Variant 3: `vinga_server.capture` at WARNING

```text
session %s: not capturing, could not open the files (%s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `failure` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `open` |  |
| `failure` | `CLASS_NAME` | yes | no |  |  |

### `capture_limit`

A recording reached its per-session ceiling.

#### Variant 1: `vinga_server.capture` at INFO

```text
session %s: capture reached its %.0f s limit
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `limit_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |

### `capture_failed`

A recording stopped after a write failed.

#### Variant 1: `vinga_server.capture` at WARNING

```text
session %s: capture stopped after failing to %s (%s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `session` (`ID`) | no | the `session_id` syntax |  |
| 2 | `reason` (`TOKEN`) | no | one of: `write an event`, `write audio` |  |
| 3 | `failure` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `session` | `ID` | yes | no | the `session_id` syntax |  |
| `reason` | `TOKEN` | yes | no | one of: `write an event`, `write audio` | Which of the recording's two tracks the write was for. |
| `failure` | `CLASS_NAME` | yes | no |  |  |

### `capture_pruned`

Old recordings were removed to stay inside the disk budget.

#### Variant 1: `vinga_server.capture` at INFO

```text
capture: pruned %d session(s) to stay under %.0f MB: %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `removed` (`COUNT`) | no |  |  |
| 2 | `budget_mb` (`FLOAT`) | no |  |  |
| 3 | `listed` (`COMPOSED`) | no | the `session_list` grammar |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `sessions` | `ID_LIST` | yes | no | each element: the `session_id` syntax | The ids themselves, not a count. |

### `capture_over_budget`

The disk budget is exceeded and nothing more can be pruned.

#### Variant 1: `vinga_server.capture` at WARNING

```text
capture: %.0f MB on disk is over the %.0f MB budget and nothing more can be pruned; raise max_total_mb or lower max_session_s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `used` (`FLOAT`) | no |  |  |
| 2 | `budget_mb` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `total_mb` | `COUNT` | yes | no |  |  |

### `capture_enabled`

Said once at startup, at WARNING: recording room audio is a thing an operator
should not discover by accident.

#### Variant 1: `vinga_server.app` at WARNING

```text
session capture is on: room audio and a track of the session's events are being written to %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `path` (`PATHLIKE`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `path` | `IDENTIFIER` | yes | no |  |  |

### `capture_disabled`

Capture is configured but off.

#### Variant 1: `vinga_server.app` at INFO

```text
session capture is configured but off; set server.capture.enabled to record to %s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `path` (`PATHLIKE`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `path` | `IDENTIFIER` | yes | no |  |  |

### `drain_started`

A shutdown begins draining.

#### Variant 1: `vinga_server.registry` at INFO

```text
draining %d session(s), up to %.0f s
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `sessions` (`COUNT`) | no |  |  |
| 2 | `timeout_s` (`FLOAT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `sessions` | `COUNT` | yes | no |  |  |
| `timeout_s` | `FLOAT` | yes | no |  |  |

### `drain_finished`

Every reply finished speaking.

#### Variant 1: `vinga_server.registry` at INFO

```text
every session drained
```

No arguments: the sentence is fixed.

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `sessions` | `COUNT` | yes | no |  |  |

### `drain_incomplete`

A reply was cut, or a session hung.

#### Variant 1: `vinga_server.registry` at WARNING

```text
drained with %d session(s) cut mid-reply and %d that did not finish
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `cut_mid_reply` (`COUNT`) | no |  |  |
| 2 | `unfinished` (`COUNT`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `sessions` | `COUNT` | yes | no |  |  |
| `cut_mid_reply` | `COUNT` | yes | no |  |  |
| `unfinished` | `COUNT` | yes | no |  |  |
| `timeout_s` | `FLOAT` | yes | no |  |  |

### `device_bindings_unreadable`

The database could not be read, so the answer is the served world's.

#### Variant 1: `vinga_server.device.bindings` at WARNING

```text
cannot read the device bindings for %s; answering from the configuration this server started with, which may be older than the database. The failure's kind is recorded beside this line
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `device` (`ID`) | no | the `mac` syntax |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
| `device` | `ID` | yes | no | the `mac` syntax |  |
| `failure` | `CLASS_NAME` | yes | no |  |  |

### `api_error`

The configuration API failed to handle a request. The class name and nothing
else.

#### Variant 1: `vinga_server.config.api` at ERROR

```text
the configuration API failed to handle a request (%s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `failure` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |

### `api_storage_error`

The configuration API met unreadable stored state.

#### Variant 1: `vinga_server.config.api` at ERROR

```text
the configuration API met unreadable stored state (%s)
```

| # | Argument | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- |
| 1 | `failure` (`CLASS_NAME`) | no |  |  |

| Field | Kind | Required | Nullable | Constraint | Note |
| --- | --- | --- | --- | --- | --- |
| `event` | `ID` | yes | no | the `event_name` syntax |  |
