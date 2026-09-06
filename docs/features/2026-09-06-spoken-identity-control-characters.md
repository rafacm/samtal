# What a refusal may call a name it cannot print

**Date:** 2026-09-06

## Problem

`store._check_addressable` refuses a control character in a name and
says why in as many words: it "does not survive a header or a log line
intact". The same docstring says what the rule does not cover, because
the rule is write time only: a row written before it "still boots, still
appears in a whole-configuration read, and is still deletable".

So the byte reached every sentence composed over a stored identity. PR
#412 found this while converging the boot refusal's two vocabularies,
and filed it rather than widening into it, because how a spoken identity
neutralizes a control character is a decision of its own. Reproduced
before anything was written, by planting `bad<ESC>name` beneath the
write checks the way the display suite plants a credential:

```
'invalid config in the domain schema of the vinga database:
  - default_agent is required when agents are defined and no device is
    bound to one; set it to one of: bad\x1bname
  - agents.bad\x1bname.llm: names no llm provider that exists, and the
    name is not quoted back (defined: bad\x1bname)'
```

Three times in one sentence, printed to stderr by the entry point before
logging is configured at all. The same plant reached
`agents.bad\x1bname: the row cannot be read` from the store and
`providers.llm.bad\x1bname: "egress" is decided by type "mock"` from the
provider build.

## The decision: escape, on the sentences only

**Escape, and only the control characters.** `spoken_identity(value)` is
the credential strip #381 put on these surfaces, with every control
character escaped as `\xNN` behind it. It lives beside
`without_url_credential` in `config/models.py`, which is where the strip
lives and where the character class now lives too.

Escaping wins on what the operator holding the sentence has to do next,
and that turns on a property of this rule that the credential rule does
not share. A credential-bearing name holds `://` and therefore a slash,
so no path segment addresses such a row: #381 could say that what a
sanitized display takes away is "a spelling that never worked as a
handle". A control character percent-encodes and decodes losslessly,
which is exactly why `_check_addressable` refuses it for what it does to
a log line rather than for what it does to routing. Measured rather than
assumed: `GET /agents/bad%1Bname` answers 200, the rename answers 200,
and the delete after it answers 200. The row is reachable, so the
sentence naming it is a recipe, and `\x1b` says which byte to encode
where a fixed mark does not.

**Rejected: replace with a fixed mark** (`?`, as `printing.printable`
does). It loses which character it was, so it cannot be turned back into
a path segment, and it prints two different broken names alike. That
door has a different job: it bounds and renders answer text a server
sent, where the reader came for the sentence rather than for the value.

**Rejected: refuse to render the name.** `cli._as_written` does exactly
this, and rightly, because what it builds is a command an operator
pastes, so a location it cannot write down addresses a different entity
or none. A refusal is not a command. Withholding the name there leaves
an operator who can see that a row is broken and not which one, on the
surface whose whole job is to say which one.

**Rejected: escaping the backslash beside them.** It would close the
ambiguity between a name holding the six characters `\x1b` and a name
holding the one character, at the cost of changing how every lawful name
holding a backslash is printed. That ambiguity is a reading of two names
rather than an addressing of either, and byte-identical rendering of
lawful names is the property this whole chain has kept.

### Where the rule stops, and the measurement that put it there

The rule is on the identities this server SAYS, not on the ones it
SHOWS, and that line was drawn by measuring the alternative rather than
by taste.

A view hands an identity back as a document key, and every writer of
that document neutralizes a control character already and losslessly:
JSON writes `\u001b`, YAML writes `\e`, and the CLI's table renderings
go through `printing.printable`. So nothing reaches a stream raw by that
route. What escaping there WOULD change is an outcome, and for the
worse. An export is the whole-configuration document in the shape
`import` takes, and `import` runs the write path:

| The document says | The import does |
| --- | --- |
| `"bad\ename"` (today) | refuses: "the name contains a control character" |
| `"bad\\x1bname"` (escaped) | writes `agents.bad\x1bname`, a lawful eleven-character agent |

Today's answer is the honest one and it names the rule. The escaped one
silently creates a row nobody meant. Both halves of that table are a
case in the suite.

## Changes

### One home for the rule, and one for the class

`spoken_identity` is the composition, and `_CONTROL_RE` moves from
`store.py` to sit beside it, with two readers that have to agree:
`holds_control_character`, which the write asks, and the escape, which a
refusal asks of a row the write never saw. A write refusing a set a
refusal did not escape would be one rule written down twice, so the
suite pins the two against each other character by character across both
ranges.

The order inside the composition is the rule rather than a preference:
strip first, escape second. `urlsplit` deletes every tab, carriage
return and newline before it reads anything, so escaping first makes
that break permanent and the credential rule then answers None to a URL
that still carries one. Both shapes a carriage return makes are pinned,
in both orders.

### The surfaces that read it

The census below is the whole of it. Everything in the first table was
already reading `without_url_credential` and now reads the composition;
everything in the second was composing a location by hand.

| Where | What it says |
| --- | --- |
| `entities.entity_location` | every per-row store refusal, and `provider_label`, which is what every provider build refusal and the build's own warning name an entry by |
| `models.safe_location` | every segment of a validation error's location, on the stored half |
| `models.defined` | the names that do resolve, which five refusals list |
| `models.check_references` | `agents.<name>`, the location of a reference that does not resolve |
| `models.check_completeness` | the agents a default could be set to |
| `Config.provider_for_agent` | the layer a stage resolved through |
| `providers/world.py` | `agents.<agent>: no <stage> provider is named` |
| `providers/world.py` | the identity the build stamps on every provider |
| `providers/registry.py` | the option names a type never asked about |
| `config/api.py` | the agent rename acknowledgement |

| Where a location was spelled by hand | Now |
| --- | --- |
| `store._check_slot`, the provider slot's addressability refusal | `entity_location` |
| `store._check_slot`, the two MCP slot key refusals | `entity_location` |
| `secrets.resolve_mcp_values`, an unresolved environment reference | `entity_location` |

### Two things the round changed after they were first written down

**The stamped `ProviderIdentity` was going to be left on the credential
strip alone**, on the reasoning that its five parts are FIELDS and every
writer of a field escapes a control character already, while the text
log format carries no fields at all. Measuring the loopback warning
refuted it: the events package composes that warning's SENTENCE out of
the identity's own values, so a planted name reached a text log line
whole while the label beside it was escaped. The stamp has never been
the name as stored, which is what makes escaping it the same move #413
made when it put the strip there: it is what every event about the entry
calls it, so it has to be what every sentence about the entry calls it.
`provider.model`, which is what goes into the request, is untouched and
pinned that way.

**`url_credential` had a gap of its own**, found while measuring what a
control character does to the rules that read a stored value. The
function tested for a literal `://` before parsing, and the parser
deletes tabs, carriage returns and newlines before it reads anything, so
`https:/<CR>/user:password@host/v1` was a URL carrying a credential to
the library and to every client that opens one, and was not one to this
rule: it passed the strip unchanged and was displayed, recorded and
spoken with the credential in it. The test now runs on the value the
parse will see. Only that one test had the gap, which is what says it is
a gap rather than a policy: every rule under it reads what `urlsplit`
returns, so a parameter spelled `?to<CR>ken=` already arrived as `token`
and was already taken out.

## Key parameters

- `models.spoken_identity(value)`: one stored identity as a sentence
  about it may say it. The one door for a SAID identity, and the
  composition of the two rules that apply to one, in the order they
  apply.
- `models.holds_control_character(value)`: the other reader of the same
  class, which is what `store._check_addressable` asks at a write.
- `models._URL_DELETES`: what `urllib.parse` removes before it parses,
  spelled once so the test in front of the parse reads the string the
  parse will.
- `entities.entity_location(descriptor, *identity)`: unchanged in shape
  and now reading the composition, which is what carries the rule to
  every store refusal and to the whole provider build in one place.

No configuration key, event field or event sentence changed, and every
committed reference regenerates byte-identical.

## What was checked and deliberately left

- **The MCP build path composes `mcp_servers.<name>` twice**, in
  `egress.check_mcp_server` and in `tools/mcp/manager.py`. Those are
  **#420**'s and are not touched here. When they land, the rule serves
  them the way it serves everything else in the second table above: they
  become correct by calling `entity_location` instead of joining a
  section to a name.
- **Events, the session manifest and an apply's answer** name identities
  in structured fields, and are **#421**'s. That issue is a policy
  decision about the events package's `Identifier`, and the one home for
  its answer is the same function this issue built: an event's sentence
  is composed from those values, which is exactly what made the provider
  stamp part of this change.
- **Three of the four consolidated locations cannot carry either rule's
  shape**, measured rather than assumed. An MCP entry name becomes a
  tool-name prefix and is held to `[A-Za-z0-9_-]+` on the way OUT as
  well as on the way in, so a planted one is refused by the load and
  reaches neither MCP slot refusal nor the environment-reference
  location, exactly as a planted device MAC is (#382). They read
  `entity_location` for locality rather than for the escape, and the
  suite pins the unreachability so that the reason is not lost.
- **The display surfaces keep no rule of their own**, for the reason and
  by the measurement above. `views._shown_identity` and
  `views._shown_mapping` still read `without_url_credential`.

## Verification

- Lint: `uv run ruff check .` clean.
- Unit, the shape CI runs: `uv run pytest tests/unit -q -n auto --dist
  loadfile`, 5912 passed and 19 skipped, 92 of them the cases added here
  (5820 on the branch point). Not one existing pin moved, which is the
  byte-identical claim asserted from the other end.
- Integration: `uv run pytest tests/integration -q`, 245 passed.
- All six committed-reference drift checks (domain config, server
  config, conversations schema, events, OpenAPI, CLI reference) diff
  empty; `uv run mypy` over the events package clean;
  `scripts/check_doc_links.py .` checked 207 files with 0 failures.
- Every leak was reproduced before it was fixed, by planting the row and
  watching the byte appear in the sentence, and every fix was then
  proven to bite by reverting it in place, watching the cases fail for
  the right reason, and restoring the file from a copy and touching it
  (never `git checkout`, per `AGENTS.md`).
  - The escape inside `spoken_identity`, reverted: 79 of the 92 cases
    failed.
  - The door inside `entity_location`, reverted: four of this suite's
    cases failed and so did five of #381's, which is what says the two
    rules share one home.
  - The stamped identity, reverted: the loopback warning's sentence
    carried the byte.
  - The credential rule's pre-parse test, reverted: the broken-scheme
    URL was reported as carrying no credential.
- The reachability the decision turns on is driven end to end rather
  than argued: the planted row is fetched, renamed and deleted over the
  API by percent-encoding the byte, and the rename acknowledgement is
  read off the parsed body rather than the text, because JSON escapes a
  raw byte on its own and an assertion over the text would pass with the
  escape gone.

## Files modified

- `vinga-server/src/vinga_server/config/models.py`
- `vinga-server/src/vinga_server/config/entities.py`
- `vinga-server/src/vinga_server/config/store.py`
- `vinga-server/src/vinga_server/config/secrets.py`
- `vinga-server/src/vinga_server/config/api.py`
- `vinga-server/src/vinga_server/providers/world.py`
- `vinga-server/src/vinga_server/providers/registry.py`
- `vinga-server/tests/unit/test_config_control_character_identities.py`
- `vinga-server/tests/unit/command-spellings.txt`
- `CHANGELOG.md`
