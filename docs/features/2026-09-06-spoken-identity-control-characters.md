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
assumed: `GET /agents/bad%1Bname` answers 200, `DELETE` of that same
spelling answers 200, and so does the rename. The row is reachable, so
the sentence naming it is a recipe, and `\x1b` says which byte to encode
where a fixed mark does not. It is also why the acknowledgements are
part of this rule: what a delete answers with is that name, said back.

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
| `config/api.py` | every write acknowledgement, the rename included |
| `secrets.SecretLocation.describe` | thirteen encryption and decryption refusals, and the four secret acknowledgements |

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
- `entities.SECRET_HOLDERS`: which kind holds a stored secret of each
  kind a stored location may name, derived once from the registry and
  read by the store, by the CLI's export and closed-set gate, and by a
  location saying itself.
- `secrets.SecretLocation.describe()`: how a location reads in a
  sentence, which is thirteen refusals and four acknowledgements. Its
  fields are the lookup and are untouched.
- `api._acknowledge(what, notice)`: `what` is a sentence, and every
  identity in one goes through the door at the site that composes it.
  Not stripped inside the function, because what arrives is already a
  sentence and `url_credential` would read one holding an address as
  prose.

No configuration key, event field or event sentence changed, and every
committed reference regenerates byte-identical.

## Review rounds

**The sol round on PR #423, 2 P1s, both adopted.** Both were the same
shape as each other and the same shape as the first pass's own gap: a
surface that says a stored identity and was not on the census.

- **A secret's location said both halves verbatim.**
  `SecretLocation.describe` is one string and it is what thirteen
  encryption and decryption refusals and four acknowledgements are built
  from. `verify_secrets` opens every stored envelope at startup, so a
  planted provider name and a planted slot reached a boot's stderr
  whole. Both halves now leave through the door, and the FIELDS keep
  what they are: `identity` and `slot` are what a lookup is made from
  and are untouched, which is the line `entity_location` already draws.
  The identity is split into the parameters that address the entity
  before each is said, so the URL rule is asked of a name rather than of
  a dotted join it would read as a scheme of its own. That needed the
  kind-to-descriptor mapping, which was derived three times (the store,
  the CLI's export and its closed-set gate, and now this);
  `entities.SECRET_HOLDERS` is the one derivation and all three read it.
- **Ten successful acknowledgements joined a stored name in raw**, and
  two of them are reachable. A delete goes by membership, so a legacy
  row is deletable and said its own name on the way out; and a device
  binding and the default agent REFERENCE an agent rather than creating
  one, carrying the name in a JSON body where a slash is no obstacle, so
  `device aa:bb:cc:dd:ee:ff bound to https://user:<credential>@host/named`
  came back with a 200. The first pass's reachability case renamed
  before deleting, which is what hid the delete: renaming ahead of it is
  the one route that never asks the delete to say the name.

The design check the round asked for, answered by grep rather than by
assertion: the only consumer of the `wrote` sentence anywhere is
`cli._acknowledged`, which prints the line and reads nothing out of it.
Nothing parses a name back out of one, so these are operator-facing
sentences and the strip belongs in them.

**The terra delta on PR #423, 1 P1, adopted, and it is a defect the
first of those two fixes introduced.** `describe` reads the kind against
the registry now, and `_unwrap` builds a `SecretLocation` out of a
decrypted payload whose three fields have been held to
`isinstance(str)` and to nothing else. A valid envelope naming a kind
that is not one of the two therefore left as a `KeyError` whose single
argument is the payload's own word, past the bounded handler in
`serving.run`, so a boot printed a traceback carrying decrypted bytes,
control characters included.

The check goes in front of the construction rather than inside
`describe`, because that is where the invariant is: `SecretLocation.kind`
is a closed set of two, and a location built from a word that is not one
of them is a value lying about its own type. The refusal names the two
kinds and never the word, which is the shape `store._NOT_A_STAGE` has
for the same situation, and it quotes the REQUESTED location, which is
the one an operator has to fix. `_unwrap` is the only place a
`SecretLocation` is built from anything but repository vocabulary: the
two classmethods pass literal kinds, the store reads `secret_slots` off
a descriptor, and the CLI's gate already checks a stored answer's kind
against the closed set.

### The re-census

Run again over `api.py` and `secrets.py`, and then over the tree, since
every round on this chain has under-counted:

```
grep -rnE '(agents|providers|mcp_servers|prompt_fragments|agent_defaults|devices)\.\{' src/vinga_server/config/api.py src/vinga_server/config/secrets.py
grep -nE '\{(name|agent|server|identity|slot|stage|old|new)[]!:}]|\{self\.|\{bound\.|\{stored\.|\{location\.|\{renamed\.' src/vinga_server/config/api.py src/vinga_server/config/secrets.py
grep -rn 'describe()' src/
grep -rnE 'raise [A-Za-z]*Error\(\s*f"' -A 2 src/
```

No third surface. The first returns nothing. The second returns only
sites that now read the door, plus this repository's own vocabulary (a
stage and a type in an OpenAPI description, a description filename, an
environment variable name). The third returns sixteen callers, all of
them the sentences named above. The fourth, over the whole tree, returns
one stored identity inside a raised sentence that is not already through
the door: `tools/mcp/manager.py`, which is #420's and was already named
below; everything else it finds is a module attribute or an event field
name this repository declares.

Two neighbours were checked and are not surfaces. `_binding_notice`
takes the unloaded names but reads them only for truthiness, so no name
reaches that sentence. And `_applied`, the apply answer, carries
`section` and `identity` as structured fields rather than in a sentence,
which is exactly the shape #421 owns.

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
  loadfile`, 5919 passed and 19 skipped, 99 of them the cases added here
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
  - `SecretLocation.describe`, reverted to rendering both halves as
    stored: three cases failed, two of them boots that open every
    envelope, one in each suite.
  - The acknowledgements, reverted (the three deletes and the
    bound-agent list): four cases failed, including the device binding
    in each suite, which is the one route a credential-bearing name
    reaches a 200 by.
  - The payload's kind check, reverted: the boot left as a `KeyError`
    carrying the rogue word, which is the traceback the finding is
    about.
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
- `vinga-server/src/vinga_server/config/cli.py`
- `vinga-server/src/vinga_server/providers/world.py`
- `vinga-server/src/vinga_server/providers/registry.py`
- `vinga-server/tests/unit/test_config_control_character_identities.py`
- `vinga-server/tests/unit/test_config_url_credential_display.py`
- `vinga-server/tests/unit/command-spellings.txt`
- `CHANGELOG.md`
