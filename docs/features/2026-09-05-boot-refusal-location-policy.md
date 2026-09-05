# The boot refusal's location policy: what a stored world may be called

**Date:** 2026-09-05

## Problem

A boot reads a configuration in two halves, and until now it refused in
two vocabularies.

The file half has been rendered by the shared policy since #376:
`models.validation_problems` walks every segment of a pydantic error's
location against the model, and a segment this repository did not
declare stops the walk. An unrecognized key, a provider option, an
entry of an MCP server's `env`: none of them reaches the sentence, the
pointers or a log line, because a key is as good a place to paste a
credential as a value is and better at hiding there.

The domain half kept a renderer of its own in the loader,
`_format_validation_error`, which printed every location segment as it
found it. The reason
was written beside it as a question rather than as a decision: the
domain half's locations are mapping keys (`agents.<name>.llm`,
`devices.<mac>`, `providers.llm.<entry>`), the shared walk truncates
those to their section, and the store's own refusals over the same
entities print them in full, deliberately and under test. Converging
the halves would make a boot refusal say less about a stored world than
the write that stored it does.

That question is #382, and it is a question about the store's location
vocabulary rather than about the loader. Two facts made it urgent
rather than tidy:

1. **The boot already spoke stored names, in the same refusal.**
   `check_references` composes `agents.<name>.<stage>` and lists every
   name that does resolve; `check_completeness` lists the agents a
   default could be set to. Both run at boot, inside the composition
   the private renderer was rendering. So a single refusal could print
   `agents` truncated on one line and `agents.sam.llm` in full on the
   next.
2. **#381 had just made every identity-speaking surface strip a
   credential**, because a name written before the addressability rule
   can hold a URL carrying one and still boots, still reads and is
   still deletable. #381 covered the DISPLAY surfaces. A refusal says
   an identity rather than showing one, and it says it somewhere no
   display goes: a server's stderr as it fails to start, read by an
   operator, by a container log and by whatever collects one.

## Changes

### The decision: provenance, not a truncation rule

A stored entity's name is repository vocabulary a boot refusal may
speak. A key an operator typed into a file, or into a stored entity's
body, is not.

The two halves are not two policies but one policy asked of two
provenances, so they converge on one renderer that is told which it is
holding. `safe_location` and `validation_problems` take `stored`, and
under it a mapping the models declare the value shape of is a section
of entities whose key is an identity; a mapping of strings to strings
is a body somebody filled in, keys and all. Which mappings those are is
read off the declaration (`_entity_valued`) rather than kept as a
second list of section names, so a new section is one by being one.

`loader._format_validation_error` is deleted. `compose_config` and the
store's own assembly of the domain half call the shared renderer, and
the docstring that held the question now records the decision.

The convergence tightens as well as loosens: a key an operator wrote
inside a stored body, an MCP server's `env` entry or a provider's
option, is no longer printed by a boot either, and an unrecognized key
under `agent_defaults` is answered by the rule it broke, in the words
the write of the same fragment has always used.

### The strip, on every sentence that names an identity

Four sentences composed over a stored name carried a credential
verbatim, and all four now leave through `without_url_credential`, the
door #381 put the displays through:

| Where | Sentence |
| --- | --- |
| `check_references` | the entry whose provider does not resolve |
| `defined` | the names that do resolve, which five refusals list |
| `check_completeness` | the agents a default could be set to |
| `store._location` | the prefix on every per-row refusal |

The lists are sorted on the name as it is STORED and shortened
afterwards, for the reason `views._shown_mapping` gives: sorting after
the strip would let what a name hides decide where it appears. A write
is unaffected, and the strip there is deliberate belt and braces: a
name reaches these sentences only after the addressability check has
passed it, and a name carrying a credential holds a slash.

### Two columns that reached a location before anything checked them

The sol round found the load path composing refusals out of raw
columns.

- **A provider row filed under no stage.** The refusal pasted both the
  stage and the entry name into a sentence, so a planted row spelled a
  credential into a boot's stderr three times over. It is now
  `_NOT_A_STAGE`, the sentence a caller's own typo gets since #132's
  review round, with the storage tail on it. This is the converged
  policy applied without a new rule: a word that is not one of the four
  stages is not this repository's vocabulary, so it is answered by the
  rule it broke, and the entry under it is addressed relative to that
  word, so the honest location is the section, exactly as
  `safe_location` truncates to the nearest parent it may name.
- **A device row read before its MAC was.** A MAC is checked on the way
  out as well as on the way in, which is what lets the display suite
  say a device key cannot carry what an entry name can. That check
  lives on the model, and the model ran after the `agents` column
  beside it had been read at a location built by pasting the raw MAC
  into a string. A row getting both wrong at once answered with the
  column's refusal carrying the MAC. `_device` now reads the row in the
  order the guard needs.

## Key parameters

- `safe_location(model, location, *, stored=False)` and
  `validation_problems(headline, model, exc, *, stored=False)`
  (`vinga-server/src/vinga_server/config/models.py`): the one renderer
  of a validation refusal in this package, and the one word that says
  which half it is holding. Passed `stored=True` by
  `loader.compose_config` and by `store._read_domain`; every other
  caller (the file half, the write path, a provider type's options) is
  caller text and keeps today's answer.
- `_entity_valued` (same module): what tells a section of entities from
  a body, read off the mapping's declared value type. A model or a
  list of bindings is a section; a mapping of strings is a body.
- `without_url_credential` (same module): unchanged, and now the one
  door for identities said by a refusal as well as shown by a view.
- `_NOT_A_STAGE` (`vinga-server/src/vinga_server/config/store.py`):
  `providers: the stage has to be one of asr, llm, tts, vad`, now also
  what a stored row filed under no stage answers with.
- `_device` (same module): one stored device row, MAC first. The key it
  returns is the column as stored and only the location is canonical,
  deliberately: keying the composed mapping by the canonical form
  swallows the duplicate-MAC rule, since two rows spelling one MAC two
  ways are two keys until the model normalizes them.

No configuration key, event field or event sentence changed, and every
committed reference regenerates byte-identical.

## Review rounds

**The sol round on PR #412, 2 P1s, both adopted.** Both were the same
shape as each other and unrelated to the renderer: a raw row column
reaching a location before anything had checked it, the provider stage
and the device MAC. Each was reproduced before it was fixed, by writing
the test first and watching the planted sentinel appear in the refusal,
and each has its own commit. The sweep sol asked for, over every place a
row's own column reaches a location in `store.py`, finds three and no
fourth: those two, and `_read_secrets`, which is safe by ordering rather
than by a check of its own, since `load` reads the domain half first.
That property is now written beside it, because nothing was saying so
and a reordering would reopen it.

**The terra delta round on PR #412, 1 P1 refuted and 1 P2 adopted.**

The P1 said a non-string stored MAC would leave `normalize_mac` as an
AttributeError, past the storage refusal and past the bounded handler
in `serving.run`. Its premise is a SQLite column, and this build has
not had one since #283: the stores are Postgres only, the migration
that replaced the SQLite chain cannot open a SQLite file at all, and
`devices.mac` is `Text`, a Postgres `text` primary key. Measured rather
than argued: writing an int, a float and a bytes value into that column
through the driver is accepted, and each reads back as `str` ('12345',
'1.5', '\xaabb'). There is no path on the real backend where `row.mac`
is not a string, so the traceback is not reachable and no test can
honestly drive it. What was taken from the finding is the call shape:
`normalize_device_bindings` reads the same column as
`normalize_mac(str(mac))` and this did not, so it does now. That is
defence in depth and consistency rather than a fix, and it adds no
unreachable arm: an isinstance guard with a sentence of its own was
refused for the reason `views.py` refuses a strip on this same column,
that it "would be code nothing can run".

The P2 is this document.

## What was checked and deliberately left

Two surfaces were found while resolving this and are filed rather than
widened into it, because each is a decision of its own rather than a
consequence of this one. Both are named here so that leaving them named
is what carries them.

- **The provider-build surface still speaks an identity unstripped**,
  taken up as **#413**. `providers/world.py` labels an entry
  `providers.<stage>.<name>` and refuses an agent by
  `agents.<agent>: no <stage> provider is named`;
  `providers/registry.py` builds the same label; and
  `Config.provider_for_agent` composes `agents.<agent>.<stage>`, which
  is what feeds them. These are boot refusals too, so they are the same
  hole; they are not the split this issue owns, they are composed by a
  different renderer, and they happen after the composition rather than
  inside it.
- **A stored name holding a control character is spoken verbatim**,
  taken up as **#414**. `_check_addressable` refuses one at write time
  precisely because a control character "does not survive a header or a
  log line intact", and a boot refusal is a log line; but that rule is
  write-time only, so a row that predates it is named in full by
  `check_references` today and by the converged walk here. This change
  neither widens nor narrows that: the domain half's old renderer
  printed every segment as it found it, so what is spoken is a subset
  of what was spoken before. Gating the walk on addressability was
  considered and refused as a separate decision: the rule lives in
  `store.py` and would have to move to reach the walk, and
  `check_references` would need wording of its own for an entry it may
  not name.

## Verification

- Lint: `uv run ruff check .` clean.
- Unit, the shape CI runs: `uv run pytest tests/unit -q -n auto --dist
  loadfile`, 5726 passed and 19 skipped (5718 on main before this
  change).
- Integration: `uv run pytest tests/integration -q`, 243 passed,
  including `test_cli_live`'s pin that a write refusal names
  `agents.refused-agent.llm` in full, which is the store's stance this
  change adopted for the boot.
- All six committed-reference drift checks (domain config, server
  config, conversations schema, events, OpenAPI, CLI reference) diff
  empty; `uv run mypy` over the events package clean;
  `scripts/check_doc_links.py .` checked 204 files with 0 failures.
- The resolved policy was measured against the alternative rather than
  argued: composing the domain half with `stored=False`, which is the
  truncate-everything convergence #376 tried and abandoned, fails
  exactly 9 pins, the number that issue recorded. Seven of them assert
  that a stored identity IS named (`agents.assistant.llm`,
  `devices.aa:bb:cc:dd:ee:ff`, `providers.llm.claude.<option>`) and
  stay byte-identical under the policy adopted here.
- Two pins moved deliberately: `agent_defaults.prompt` became
  `agent_defaults: an unrecognized key is not permitted`, and
  `providers.llm..[key]` became `providers.llm.`, pydantic's key marker
  being neither this repository's vocabulary nor a stored identity.
- Every sentinel was proven to bite, by reverting the fix in place,
  watching the test fail for the right reason, and restoring the file
  from a copy and touching it (never `git checkout`, per `AGENTS.md`).
  - The four strips, reverted together: all five boot-refusal cases
    failed with the planted name in full, credential and all, in the
    exception chain and on stderr.
  - The stage refusal, reverted: the planted credential appeared three
    times in one sentence.
  - The MAC order, reverted: the column refusal carried the raw MAC.
  - The device key's spelling, changed to the canonical form: the
    duplicate-MAC pair loaded clean, which is what the new pin in
    `test_config_refusals.py` exists to catch.

## Files modified

- `vinga-server/src/vinga_server/config/models.py`
- `vinga-server/src/vinga_server/config/loader.py`
- `vinga-server/src/vinga_server/config/store.py`
- `vinga-server/tests/unit/test_config.py`
- `vinga-server/tests/unit/test_config_refusals.py`
- `vinga-server/tests/unit/test_config_url_credential_display.py`
- `vinga-server/tests/unit/command-spellings.txt`
- `CHANGELOG.md`
