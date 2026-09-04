# Exempt max_tokens from the secret-key heuristic: implementation

Companion to
[`2026-09-04-max-tokens-exemption.md`](2026-09-04-max-tokens-exemption.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the exemption, the migration, the suite, and the fragment lines

### What was done

`config/models.py`, at the rule's one home.
`_SECRET_KEY_EXEMPT_NAMES = ("max_tokens",)` is declared in the block
that holds the two fragment tuples, with the comment stating all three
conditions a name has to meet to be there (it contains a fragment as a
substring, it is a declared option a builder reads, and it is not a
credential) and stating why the wider tuple can never have a member:
an MCP env or headers key and a URL query parameter are named by
somebody else, so the second condition cannot hold there. The compare
is the first statement of `secret_option_fragment`, exact and against
the name as it was written, before the `lower()` the fragment scan
does. Nothing else in the module changed, and no second reader of the
exemption exists anywhere.

`db/migrations/versions/3002_drop_max_tokens_secrets.py`, new, on the
domain chain with `3001_postgres_domain` as its down revision. One
statement:

```sql
update domain.providers
   set secrets = (secrets::jsonb - 'max_tokens')::json
 where jsonb_exists(secrets::jsonb, 'max_tokens')
```

Both halves cast because the `secrets` column is `JSON` and neither
`-` nor the key test exists on that type. `jsonb_exists(...)` rather
than the `?` operator, which is the same question spelled so that no
layer between here and the driver can read it as a placeholder. The
`where` keeps the statement off every row that has no such slot, which
is every row on almost every deployment, so an untouched row keeps its
column's bytes rather than being rewritten through a JSON round trip
that would reorder its remaining keys. `downgrade` is empty and says
why: what the upgrade removed is gone, nothing else holds a copy, and
an invented envelope would fail the next boot's verification rather
than restore anything.

The chain's head moved in the two places that pin it:
`tests/unit/test_db_open.py`'s `HEAD`, and the domain-chain assertion
in the CI wheel-migration step, which is the lane that migrates a fresh
database from the built artifact.

`tests/integration/test_domain_upgrade.py`, new. `blank_database`
stamped at the baseline the way `db.upgrade_to_head` drives Alembic,
one provider entry and an `api_key` credential written through the
repository, and the withdrawn `max_tokens` envelope written into the
column directly with `secrets.encrypt`, because the repository this
commit ships refuses the slot: what is reproduced is a row an older
build wrote, and only the row is old. Four cases: the seed really
carries the slot (without which every other assertion would be
vacuous), the upgrade takes exactly that key and leaves the sibling
opening to its own plaintext with `verify_secrets` passing over the
result, the single read and the listing agree, and the export names
only the surviving slot.

`tests/unit/test_config_secret_exemption.py`, new, the containment
suite. Three depths: the predicate over every fragment of the narrow
tuple and every neighbour and case variant of the exemption; the write
path over the same table, flat and nested one key deep, at the
repository and over HTTP; and the wider rule at its own three readers
(`mcp_secret_fragment` through an MCP `env` key and an MCP header,
`is_url_credential_parameter` through `url_credential` and through a
provider's `base_url`). The install side runs on the file, the API and
the CLI, under the open-doors type and under the one that declares the
field, with a value that is not the builders' default. Every refused
value is a sentinel asserted absent from the exception, its chain, its
`problems`, the structured body, the response text and headers, both
log formats and both streams.

The pins that belong beside what they are about, rather than in that
file:

- `tests/unit/test_config_round_trip.py`: the read-back-unmasked round
  trip, the keep-marker pin (numeric store, mask resubmit, typed
  `/max_tokens` refusal, stored integer unchanged), and the export
  round trip through the CLI. The generic
  mask-under-a-non-secret-key control above them is untouched.
- `tests/unit/test_config_store.py`: the slot pin, held differentially
  against the refusal an ordinary non-slot option name meets, with
  `api_key` still filling afterwards.
- `tests/unit/test_config_api_problems.py`: the taxonomy row, a
  mistyped `max_tokens` on `openai_compatible` answered as a declared
  option of a typed type with the pointer `/max_tokens`.
- `tests/unit/test_config_reads.py`: shown and recorded as its value,
  beside a credential-shaped sibling that is still displaced.
- `tests/unit/test_providers_llm.py`: the builder reach. `anthropic`
  factory-built and holding the configured cap, and
  `openai_compatible` factory-built and driven over a recording
  transport so the assertion is on the JSON that left the process.

`examples/llm-anthropic.yaml` and `examples/llm-openai-compatible.yaml`
each gain a commented `# max_tokens: 1024` with the reasoning above it.
The generic uncommenting test carries the typed fragment;
`test_config_examples.py` gains a targeted anthropic case that
uncomments that fragment whole, installs it, and reads the documented
value back, because the generic case selects only the types that
declare an options model.

`CHANGELOG.md` gains the 2026-09-04 Fixed entry: the field writable,
what is still refused, the slot acceptance withdrawn, and the migration
that removes the legacy rows.

### Deviations from the plan

One, and it is a placement rather than a decision. The plan says
`_SECRET_KEY_EXEMPT_NAMES` is "declared beside `_SECRET_KEY_FRAGMENTS`";
it is declared after `_UNDECLARED_SECRET_KEY_FRAGMENTS` rather than
between the two tuples. Putting it between them would have separated
the wider tuple from the comment that derives it ("The same rule where
the name is not one this repository or a provider type declared"),
which would then have read as being about the exemption. The exemption's
own comment names which tuple it narrows and states that the wider one
is deliberately untouched, so the fact is in the same block either way.

Nothing else departed from the plan. The three named behaviour changes
landed as written, the wider tuple and the URL rule are byte-unchanged,
and no second copy of the rule was added anywhere.

### What building it turned up

**The generated references really are byte-stable, and so is the
census manifest, but only the first of those was predicted.** The plan
expected no reference churn and there was none: `domain-config.md`,
`api-openapi.json` and the CLI reference all regenerate identically,
because the `max_tokens` rows already existed in all three and the CLI
recipes are read out of the commands a fragment quotes rather than out
of its option lines. The command-spellings manifest is a different
matter: it records `path:line` for every quoted invocation in the tree,
so a comment added to `config/models.py` and a line added to an example
fragment both stale it. It was regenerated twice, in the commits that
moved those lines.

**A refusal that names one of the repository's own six words cannot
also promise not to name the key.** The containment matrix asserts that
a refused key is never quoted back, which fails for exactly the rows
whose key IS the fragment (`token`, `api_key`, `apikey`): the refusal
names the fragment on purpose, and nobody invented it. The assertion is
skipped for those rows with the reason written beside it, rather than
weakened for all of them.

**The nested exemption is a name rule at every depth, and that is
correct.** `connection: {max_tokens: 1}` is now accepted, since the
walk asks the same predicate of every leaf. It is the right answer for
the same reason the flat one is: what the rule decides is whether a
name says the value is a credential, and the answer does not change
with depth. The matrix runs its whole refusal table flat and nested, so
what is admitted one key deep is exactly what is admitted at the top.

### Verification

- [x] `uv run ruff check .` clean.
- [x] `uv run pytest tests/unit -q -n auto --dist loadfile`: 5463
      passed, 19 skipped.
- [x] `uv run pytest tests/integration -q`: 243 passed.
- [x] `python3 scripts/check_doc_links.py .` clean.
- [x] The four generated documents regenerate byte-identically
      (`config reference`, `config openapi`, the CLI reference region,
      and the recipes inside it), asserted by running the same commands
      the CI drift steps run and diffing.
- [ ] The CI wheel-migration lane against the new chain head. It builds
      and installs a wheel on a runner and cannot be run from here; the
      assertion it makes is the one `tests/unit/test_db_open.py` makes
      from the checkout, which passes.
