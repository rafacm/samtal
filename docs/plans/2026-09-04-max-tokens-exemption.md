# Exempt max_tokens from the secret-key heuristic

Plan for [#277](https://github.com/rafacm/vinga/issues/277).
Implementation notes land in the companion
`2026-09-04-max-tokens-exemption-implementation.md`, one section
per milestone, appended in the change that ticks the milestone
here.

## Goal

`secret_option_fragment` matches the fragment `token` anywhere in
an option key, so `max_tokens` is refused as an inline secret on
every surface, for every provider type, and always has been: the
`anthropic` and `openai_compatible` builders read an option no
fragment could ever install, the default has silently always won,
and the generic refusal even advises writing `max_tokens_env`,
which nothing would read. Meanwhile every operator-facing
reference documents the field as writable with no caveat. This
plan adds a bounded exact-name exemption at the heuristic's one
home, making the field writable, shown rather than masked, and no
longer a nonsense secret slot, with a regression suite proving the
loosening admits exactly the exempted name and nothing else.

## The issue's decisions, restated

- The fix is a design decision on the shared heuristic, not a
  patch at one site; the issue names the three candidates.
- Whichever wins, the write path, the unchanged-value masking
  predicate (`is_secret_option` drives both) and the docs move
  together.
- A planted-credential regression suite must show the loosening
  admits exactly the exempted names and nothing else.

## Where the facts already live

The rule has one home and six readers. `_SECRET_KEY_FRAGMENTS`
feeds `secret_option_fragment` (`config/models.py:1056`), which
feeds `is_secret_option`, whose own docstring states the
three-readers rule: the inline-value refusal
(`check_no_inline_secrets`, reached by
`ProviderConfig._reject_inline_secrets` on every construction
through every door), the secret-slot check
(`store.py:1955`, which today accepts `max_tokens` as a slot), and
the display mask (the provider descriptor's
`secret_key=is_secret_option` at `entities.py:401`, driving both
`views.entity_body` and the unchanged-value marks in
`store._prepare`, whose comment states that what a read hides and
what a write restores are one rule); plus two direct record-path
calls (`views.py:362`, `views.py:375`). The MCP entries and URL
parameters use the deliberately wider
`_UNDECLARED_SECRET_KEY_FRAGMENTS` through `mcp_secret_fragment`
and `is_url_credential_parameter` (#279). The typed field lives at
`provider_options.py:736` (`StrictInt`, default 1024, restating
the builders' `DEFAULT_MAX_TOKENS`); the #88 M3 implementation doc
records the full analysis and deliberately changed nothing else.

## Open questions, resolved

**Option (a), the exact-name exemption, and here is the census
that buries option (b).** Word-boundary matching cannot be applied
to the shared rule: `\b` treats `_` as a word character, so
`\btoken\b` fails to match `session_token`, `auth_token`,
`access_token`, `API_ACCESS_TOKEN` and `client_secret`, which is
most of the real secret names in use; a token-split comparison
(split on non-alphanumerics, compare parts) keeps those but stops
matching `Authorization` against `auth`, which the wider tuple
must keep matching for #279, so the two tuples would stop being
"one tuple and not two" as `models.py:58-65` requires. And the
census shows the whole benefit of (b) is one name: `max_tokens` is
the only provider-option key in the entire codebase containing a
fragment as a substring but not as a word (enumerated across every
untyped builder read and every typed model field). Option (c)
renames a vendor's own vocabulary and rewrites three generated
references for no schema gain. So: a one-entry exact-name tuple,
`_SECRET_KEY_EXEMPT_NAMES = ("max_tokens",)`, declared beside
`_SECRET_KEY_FRAGMENTS` with the comment saying what earns a name
a place there (it contains a fragment, it is a declared option a
builder reads, and it is not a credential).

**The exemption lives inside `secret_option_fragment`, so every
reader agrees by construction.** The compare is exact and
case-sensitive, on the original name against the one-entry set,
before the lowering the fragment scan does: option names are
case-sensitive everywhere they are declared and read, so
`MAX_TOKENS` and `Max_Tokens` are spellings nothing declares, and
exempting them would hand the open-doors type a passthrough field
the fix never meant to admit. The case variants join the refusal
matrix. Because all six consumers derive
from this one function, the write refusal, the slot check, the
display mask, the unchanged-value marks and the record path move
together automatically, which is the issue's move-together
requirement satisfied by locality rather than by coordination.
Placing the exemption anywhere narrower would wedge the round
trip: writable but masked means the resubmitted mask becomes a
keep-marker with nothing stored, which `_keep` refuses.

**The wider tuple is untouched.** `mcp_secret_fragment`,
`_UNDECLARED_SECRET_KEY_FRAGMENTS` and
`is_url_credential_parameter` keep their reach: an MCP `env` or
`headers` key or a URL parameter has no declared reader, so
nothing earns an exemption there, and the #279 sweep in
`test_config_bodies.py` stays byte-green.

**Three behavior changes, each named and pinned.** Writable:
`max_tokens` installs from the file, the API and the CLI, and the
typed `StrictInt` keeps refusing `"1024"`, a bool and a float
exactly as the parity rows pin. Shown: the value renders unmasked
in every display and export, which is correct because it is a
reply-length cap, not a credential. Not a slot:
`provider secret set <stage> <entry> max_tokens` now refuses with
the existing not-a-slot sentence, where before it accepted a slot
no read or build would ever consult. The first cut of this plan
claimed no migration concern; that was wrong in one direction and
the review round caught it. No deployment can hold a stored
`max_tokens` option value (the write refusal predates every
store), but the slot check accepted `max_tokens` as a secret slot
all along, so a deployment can hold a stored, never-consumed
`secrets.max_tokens` row, which boots, lists as stored-secret
metadata, and renders into the export's foot as a
`provider secret set ... max_tokens` command the post-change
import path would refuse, breaking the documented
export-and-reapply recovery. So the change ships with a forward
data migration on the domain chain that deletes exactly the
provider `secrets.max_tokens` rows and nothing else (deleting
loses nothing, since no reader ever consumed the row), exercised
by the CI wheel-migration step like every schema change, with
boot, single reads, listings and a full export verified over a
database seeded with such a row beside a sibling secret that must
survive, and the withdrawal named in the CHANGELOG.

**Both LLM fragments document the field, decided here.** The
`openai_compatible` example fragment regains the commented
`# max_tokens: 1024` line the M3 round had to remove, with the
generic uncommenting test
(`test_every_documented_option_of_a_typed_type_installs`) as the
standing proof it installs. The `anthropic` fragment documents the
same line: its builder reads the option identically, and there is
no product reason for the asymmetry; since the generic test covers
only typed types and anthropic declares no options model, a
targeted anthropic uncommenting-install case is added beside the
generic one so the documented line is held to installing the same
way.

## Module layout

No new module. `config/models.py` deepens at the rule's one home;
nothing else learns anything.

## Tests

- **The regression suite, built to prove exact containment rather
  than sampled**: `max_tokens: 1024` installs and survives into
  `.options` on the file, API and CLI surfaces. The refusal matrix
  enumerates every fragment of the narrow tuple (`secret`,
  `token`, `password`, `api_key`, `apikey`, `credential`, each
  with a representative planted name), the exact-name neighbors
  and probes (`max_token`, `tokens`, `token`, `max_tokens_backup`,
  `tokens_max`, `session_token`, `auth_token`, `client_secret`),
  the case variants (`MAX_TOKENS`, `Max_Tokens`), and nested
  provider keys; `max_tokens_env` keeps its env-reference
  validation (noted as not a probe, since `_env` names are handled
  before fragment matching). The planted values are sentinels in
  the `PLANTED_KEYS` style of `test_config_api_problems.py`,
  asserted absent from exception chains, structured API bodies,
  logs in both formats, stdout and stderr.
- **Display and round trip**: `max_tokens` renders unmasked in
  `entity_body` and the record path (`provider_record`), exports
  as its value, and re-imports. The reshaped keep-marker behavior
  gets its own direct pin: store a numeric `max_tokens`, resubmit
  `max_tokens: "********"`, and assert the typed `/max_tokens`
  refusal with the stored integer unchanged, a case that under the
  old predicate would have read the mask as a keep marker and
  succeeded; the generic mask-under-a-non-secret-key control stays
  untouched beside it.
- **The slot check**: `provider secret set` on `max_tokens`
  refuses with the not-a-slot sentence (a new pin, since the old
  acceptance was never pinned), and `api_key` keeps working as the
  slot example.
- **The wider rule**: the committed-fixture sweep and the #279
  URL cases stay green untouched; containment cases plant
  `max_tokens` and `MAX_TOKENS` as an MCP `env` key, as an MCP
  header, and as a URL query parameter, asserting each is still
  refused or stripped by its own reader
  (`mcp_secret_fragment`, `is_url_credential_parameter`), pinning
  that the exemption did not leak into the wider tuple or the URL
  rule.
- **The problem taxonomy**: the API answers a written
  `max_tokens` as a declared typed option (the `/beam_size`-style
  pointer shape in `test_config_api_problems.py`), not as an
  inline secret.
- **The value reaches the builders**, which is the defect's own
  shape (the default always won): factory-level tests build both
  the `anthropic` and `openai_compatible` providers from
  configurations carrying a non-default `max_tokens` and assert
  the built provider holds it, and one case continues through the
  request seam and asserts the outgoing request carries the
  configured value.
- **Docs coupling**: the uncommenting test carries the fragment
  line; the generated references regenerate only if a description
  changes, which this plan does not do.

## Risks

- **Loosening a security rule.** Bounded by construction: an
  exact lowercase name compare, one entry, at one site, with the
  suite asserting the near-misses still refuse and the MCP case
  proving containment. The no-leak sentinel style already in the
  suite (values never echoed in refusals) is unchanged.
- **A second exemption reader drifting.** There is none to drift:
  the exemption is inside the function every consumer calls, and
  the plan adds no other copy.
- **Generated-reference churn.** None expected (the `max_tokens`
  rows already exist in `domain-config.md`, `cli.md` and the
  OpenAPI document); the freshness pins catch any surprise.

## Milestones

- [x] **[M1: the exemption, the migration, the suite, and the
  fragment lines](2026-09-04-max-tokens-exemption-implementation.md#m1-the-exemption-the-migration-the-suite-and-the-fragment-lines)**
  (PR TBD). `_SECRET_KEY_EXEMPT_NAMES` inside
  `secret_option_fragment`, exact and case-sensitive, with the
  earning-a-place comment; the forward domain-chain migration
  deleting exactly the provider `secrets.max_tokens` rows, with
  the seeded-row verification (boot, reads, listings, export, a
  sibling secret surviving) and the wheel lane exercising it; the
  containment suite, keep-marker, builder-reach, display,
  round-trip, slot and taxonomy pins above; both LLM fragments'
  `# max_tokens: 1024` lines with the targeted anthropic
  uncommenting-install case; a CHANGELOG entry naming the field
  writable, the slot acceptance withdrawn and the legacy-row
  removal; the implementation-doc section. Design footprint:
  deepens the heuristic at its one home; no new module, no second
  copy of the rule anywhere. Documentation footprint: the two
  example fragments and `CHANGELOG.md`; generated references
  expected byte-stable and asserted so by their freshness pins.

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, sandbox
read-only, 2026-09-04, against commit HEAD^ of this section; the
reviewer ran about 8 minutes. Verdict: ready after the P1/P2
amendments.

1. **P1: the case-folded exemption admits undeclared spellings.**
   Option names are case-sensitive (`OpenaiCompatibleOptions`
   declares only `max_tokens`, the anthropic reader reads only
   that spelling, and passthrough checks are case-sensitive), so
   exempting the lowered name would admit `MAX_TOKENS` and
   `Max_Tokens`, which nothing declares or reads and which the
   open-doors type could forward. Compare the original name
   exactly against `{"max_tokens"}` before lowering for the
   fragment scan, and add the case variants to the refusals.

   *Resolution*: accepted in full; the compare is exact and
   case-sensitive on the original name, with the case variants in
   the refusal matrix and the passthrough consequence stated.

2. **P1: the no-migration claim is false and creates an
   unreplayable export.** `_check_slot` accepts `max_tokens` today
   and `set_secret` stores it; stored rows are loaded and verified
   at boot, listed as metadata, and rendered into export commands,
   so after the change an exported
   `provider secret set ... max_tokens` line would be refused,
   breaking the documented export-and-reapply recovery. Handle
   legacy rows explicitly: prefer a forward data migration
   removing only provider `secrets.max_tokens` rows, exercised by
   the wheel-migration lane, with boot, reads, listings and
   exports verified after, and the withdrawal documented.

   *Resolution*: accepted in full; the false claim is corrected in
   place with the review round credited, and the plan now ships
   the forward migration deleting exactly the provider
   `secrets.max_tokens` rows, wheel-exercised, with the
   seeded-row verification and the CHANGELOG withdrawal note.

3. **P1: the regression census does not prove containment or
   no-leak behavior.** The narrow tuple has six fragments and the
   planned refusals omit `apikey` and `credential`; there are no
   prefix/suffix probes (`max_tokens_backup`); `max_tokens_env` is
   not a probe because `_env` names are handled before fragment
   matching; the existing table test drives one surface with a
   dummy value and checks only the exception string; and the MCP
   case does not exercise `is_url_credential_parameter`. Enumerate
   every fragment, exact-name neighbors, case variants and nested
   keys; add `max_tokens` containment for MCP env, headers and URL
   query parameters; plant sentinels and assert absence from
   exception chains, API bodies, logs, stdout and stderr in the
   `PLANTED_KEYS` style.

   *Resolution*: accepted in full; the suite section is rebuilt to
   enumerate every fragment, the probes and case variants, nested
   keys, the three wider-rule containment surfaces including the
   URL reader, and the sentinel-across-surfaces discipline.

4. **P2: the unchanged-value marker behavior is not pinned.** The
   generic `note: MASK` control stays green even if `max_tokens`
   is still secret-shaped to `_masked_paths`. Store a numeric
   `max_tokens`, resubmit `max_tokens: "********"`, and assert a
   typed `/max_tokens` refusal with the stored integer unchanged;
   under the old predicate the mask would read as a keep marker
   and succeed.

   *Resolution*: accepted in full; the display bullet now carries
   the direct keep-marker pin (numeric store, mask resubmit, typed
   refusal, stored integer unchanged) beside the untouched generic
   control.

5. **P2: no test proves a configured value reaches either
   builder.** The defect was the default always winning, yet the
   planned tests stop at `.options`. Add factory-level tests for
   both builders with a non-default value, one continuing through
   the request seam to the outgoing `max_tokens`.

   *Resolution*: accepted in full; the test section gains the
   factory-level cases for both builders with a non-default value
   and the request-seam assertion.

6. **P2: the anthropic documentation decision is improperly
   deferred.** The generic uncommenting test covers only typed
   types, so untyped anthropic is excluded from it; decide now:
   document `# max_tokens: 1024` in both LLM fragments and add a
   targeted anthropic uncommenting-install test.

   *Resolution*: accepted in full; both fragments document the
   line, decided in the plan, with the targeted anthropic
   uncommenting-install case beside the generic test.
