# The display sweep: reads fail open, masked

**Date:** 2026-08-19

## Problem

Two open questions about the same surface, filed a day apart from the
same walkthrough, and answered here together.

**#176, what a read shows.** The five body builders in
`samtal-server/samtal_server/config/views.py` (`provider_body`,
`mcp_server_body`, `prompt_fragment_body`, `agent_body`, `layer_body`)
each listed their kind's fields by hand. The descriptor plan's M5 cost
demonstration measured what that costs: a scratch `note` field added to
`PromptFragmentConfig` reached the store, both APIs, the CLI and both
generated references with nobody touching them, and never appeared in
`config show prompt-fragment household` or in the whole-configuration
document, because `prompt_fragment_body` returned `{"text": entry.text}`
and knew nothing about the new field. No test failed for it, which is
the part that makes it a defect rather than a rule: `provider_record`
does the same thing on purpose and says so, and the five said nothing,
so on the display path the same behavior read as an oversight.

**#171, how deep the mask goes.** A provider's options were masked at
every depth, because an option is passed through to the provider
implementation and so can be a structure with a secret-shaped key
inside it. An MCP server's `env` and `headers` went through
`shown_values`, which masked one level down and no further. The two
rules differed by accident rather than by decision, and the difference
would have become visible the first time either mapping held anything
but a flat string, or the first time the model grew a nested section.

The maintainer ratified one policy for both on 2026-08-19, recorded on
each issue: **the display fails open, masked**. A read shows every
field the model declares, because a read is thrown away as soon as it
has been read and an operator debugging with an incomplete answer is
the worse failure; and the walk that finds the new field is the walk
that masks it, at every depth. `provider_record` keeps the opposite
answer for the opposite reason, which its docstring now states as a
split rather than as a local preference: a record is written into a
capture manifest and a conversation's session row, both of which
outlive the conversation, so a field is absent from every record until
somebody decides it belongs there.

## Changes

**One builder, derived from the model.** `views.entity_body(descriptor,
entry)` replaces the five. It walks the entry's own model fields in
declaration order, then a pass-through model's extras (a provider's
options, which are the implementation's and cannot be declared), and it
is registered as the `body` fact of all five kinds through one loop.
What differs between the kinds is the descriptor it is handed, not the
way an entry is shown.

**Two new registry facts**, in
`samtal-server/samtal_server/config/entities.py`:

- `EntityDescriptor.secret_key`, the predicate that decides whether a
  key name holds a credential, asked at every depth. It is the same
  predicate the models refuse an inline value under, so what a write
  rejects and what a read masks cannot come to disagree. The wider
  reading (`is_mcp_secret_key`, which counts `auth`) is the default,
  because a kind that has not thought about the question should mask
  more rather than less; a provider takes the narrower
  `is_secret_option`, deliberately, since its options are passed
  through to an implementation where `auth_type: bearer` is
  configuration an operator reads back rather than a credential.
- `DocumentedShape.always_shown`, the fields a shape shows even when
  they hold their declared default. One shape uses it: a filler's
  `phrases`, because the phrase list is what the section is, and a
  disabled section shows the empty list it has.

**One masking walk, at every depth.** `_masked` and `_shown` mask
whatever a secret-shaped key holds, structures included, and otherwise
walk into mappings, lists and nested models. `masked_option` and
`shown_values` are gone, replaced by it. `recorded_option` stays, since
the record path also strips a credential a URL carries, which the
display path does not do (a display shows what is configured; that
neighbouring question is not this change's).

**The absence rule.** A field is shown at whatever it holds, its
default included, and is left out only when it holds a default that
means absence: null, an empty list, or an empty mapping declared as the
field's own default. That is not decoration. A read is a fragment a
write of it accepts back, and `McpServerConfig` refuses an entry for
naming a field of the other transport at all, so a stdio entry that
showed `url: null` and `headers: {}` could not be written back. An
empty list the operator wrote is not that absence and is shown:
`prompt_includes: []` opts a layer out where an unset one inherits, and
the two must not read alike.

### The newly-visible-fields inventory

**None.** Every field each of the five builders printed is exactly a
field its model declares, and the derived rule reproduces each builder's
omission condition:

| Builder | Fields printed today | Under the derived rule |
| --- | --- | --- |
| `provider_body` | `type`, `api_key_env` (unless null), `egress` (unless null), options | identical; both omissions are null defaults |
| `mcp_server_body` | all 11 declared fields, with `command`/`url`/`egress`/`instructions`/`inject_prompts` omitted when null and `args`/`env`/`headers` omitted when empty | identical; `tool_timeout_s` and `use_server_instructions` are shown at their defaults because those defaults are real values, which is what the two hand-written "always shown" lines said |
| `prompt_fragment_body` | `text` | identical; a required field is always shown |
| `agent_body` | `prompt` plus the layer half | identical; `prompt`'s default is the empty string, which is a value rather than an absence |
| `layer_body` | the four stages, `mcp`, `filler`, `prompt_includes`, each when written | identical; `filler` keeps all three of its fields, which is what `always_shown` is declared for |

So no read prints a field it did not print before, and no existing test
needed its pinned body updated. The whole unit suite passed unchanged
on the commit that derived the bodies, which is the empirical form of
this table. What changes is tomorrow: the next field added to any of
these models appears on every read with nothing in `views.py` to edit.

The one behavior change is the masking depth. It is not reachable
through the store today, because `env` and `headers` are typed
`dict[str, str]` and a row holding anything else is refused when it is
parsed, which is why the sentinel for it is built with
`model_construct`, exactly as the record path's fail-closed sentinel
already was. It is defence in depth for the row that got its contents
another way, and it is the rule that will hold when either mapping
grows a nested shape.

## Key parameters

- `EntityDescriptor.secret_key`: `models.is_secret_option` for
  providers, `models.is_mcp_secret_key` (the default) for the other
  four kinds.
- `DocumentedShape.always_shown`: `("phrases",)` on the filler shape,
  empty everywhere else.
- `DocumentedShape.leads_with`: `("prompt",)` on the agent, empty
  everywhere else.
- The absence set: `None`, `[]`, `{}`, compared against the field's own
  declared default.
- `secrets.mask` is unchanged: only a syntactically valid environment
  reference passes through, and everything else becomes `********`.

## Verification

From `samtal-server/`, with `PYTHONDONTWRITEBYTECODE=1` outside pytest:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `3073 passed, 16 skipped`. The lane
  held 3061 before this change and the twelve added are this change's
  own (nine, and three more from the review round), which is also the
  shape of the claim: deriving the bodies moved the count by nothing at
  all, because no existing pin had to change.
- `uv run pytest tests/integration -q`: `60 passed`, unchanged.
- All four generated references regenerate byte-identical, run exactly
  as the CI drift steps run them: `config reference`,
  `conversations schema`, `events reference` and `config openapi`.
  Nothing drifted and nothing was regenerated into the tree. The
  OpenAPI document does not move because `Envelope.entity` is declared
  as an open mapping, which the response models chose deliberately: a
  masked value is not one the entity model would accept back.

Each mechanism was bitten: reverted, watched fail, restored with a
copy-aside and a `touch`.

| Reverted | Failures |
| --- | --- |
| the masking walk's depth | the nested MCP sentinel, the nested provider option on the views path, the same option over HTTP, and the same option rendered by the CLI |
| deriving the body from the entry's model | the new-field coherence test |
| the declared `always_shown` | the filler-shown-whole test |
| the absence rule | twelve tests, including every write-shaped read and the whole-configuration document |

## The review round

One finding on PR #207, accepted.

**The agent body's field order drifted (P2).** `AgentConfig` inherits
the layer fields and declares `prompt` after them, so a builder that
walks `model_fields` renders the overrides first and the prompt last,
where the retired `agent_body` put the prompt first. JSON and YAML both
keep the order a mapping was built in, so this is drift in the bytes an
API response and a printed document are made of, and every assertion in
this change compared mappings, which cannot see it.

The fix is a third registry fact rather than a special case in the
walk: `DocumentedShape.leads_with`, the fields a display puts before the
rest, empty everywhere except the agent, which leads with its prompt.
Declaration order stays the display order; what the fact says is the one
thing declaration order cannot, that a field declared last is read
first, because a subclass declares its own fields after the ones it
inherits and an agent's prompt is what makes it that agent.

The other four kinds were checked the same way, by diffing each retired
builder's key order against its model's declaration order:

| Kind | Retired builder's order | Model order | |
| --- | --- | --- | --- |
| provider | `type`, `api_key_env`, `egress`, options | same | same |
| mcp-server | all 11 declared fields | same | same |
| prompt-fragment | `text` | same | same |
| agent | `prompt` first, then the layer half | layer half first, `prompt` last | **drift** |
| agent-defaults | the layer half | same | same |
| filler (nested) | `enabled`, `delay_ms`, `phrases` | same | same |
| mcp-grant (nested) | `server`, `tools` | same | same |

Order is now pinned in bytes rather than in mappings: an agent carrying
both a prompt and an override is asserted as the exact JSON text of the
API response and as the exact YAML text the CLI prints. A registry
coherence test also holds `leads_with` and `always_shown` to naming
fields their shape's model actually declares, since a lead field that
does not exist would raise and an always-shown field renamed would
silently stop being shown.

Bitten: with the agent's `leads_with` removed, the two byte-exact pins
fail and nothing else does, which is also the measure of how invisible
the drift was.

## Files modified

- `samtal-server/samtal_server/config/views.py`: one derived builder and
  one masking walk in place of five builders, `masked_option` and
  `shown_values`; the module docstring and `provider_record`'s state the
  split policy.
- `samtal-server/samtal_server/config/entities.py`: the `secret_key` and
  `always_shown` facts, the provider's narrower predicate, the filler's
  declared exception, and `always_shown(model)` for the walk.
- `samtal-server/tests/unit/test_config_reads.py`: the coherence tests
  for both halves of the split, and the depth sentinels.
- `samtal-server/tests/unit/test_config_api_reads.py`: an agent's exact
  response bytes.
- `samtal-server/tests/unit/test_config_cli_local.py`: the nested
  credential as the CLI renders it, and an agent's exact printed bytes.
- `samtal-server/tests/unit/test_config_entities.py`: the display facts
  held to the fields their models declare.
- `docs/plans/2026-08-17-config-descriptors-implementation.md`: the two
  findings it recorded as open questions now point at the answer.
- `CHANGELOG.md`.
