# Consolidate the config admin surface behind the OpenAPI contract

## Goal

Implement issue #242, the second issue of the config phase of #246.
The five moves the issue settles shrink the admin surface around a
config the server itself consumes: the twice-declared domain model
becomes one declaration, the f-string factory and the tests-only
response structures fold into their call sites, the descriptor
registry sheds its speculative fields and its duplication with the
hand-written fan-out, the ~550 lines of OpenAPI description
literals leave `api.py` for data files, and the test suites stop
pinning exact wording. The one contract is explicitly kept: the
generated, committed, CI-diffed `docs/reference/api-openapi.json`
does not change by a byte, which is the proof that every move
changed how the document is produced and nothing it promises.

The companion implementation doc,
[`2026-08-23-config-consolidation-implementation.md`](2026-08-23-config-consolidation-implementation.md),
records what each milestone actually did, deviations from this
plan, and discoveries; a milestone with no deviations says so
explicitly.

## The issue's decisions, restated

1. **`store.DomainConfig` merges into `models.Config`** (the same 7
   domain fields and 3 validators declared verbatim in both).
2. **`writes.py` folds into its call sites** (16 functions each
   returning one f-string), and so do the tests-only parts of
   `responses.py`; response models defining real wire shapes stay.
3. **`entities.py` shrinks to the descriptor strings actually
   read**: `leads_with` and `always_shown` (one non-empty use each)
   go, and ONE side of the registry-versus-handwritten-fan-out
   duplication is picked.
4. **The OpenAPI description literals move out of `api.py` into
   data files the app loads.**
5. **Tests stop pinning exact response and acknowledgement
   wording**; they assert structure and semantics, and the ~40
   wording constants return to module-private.
6. **Kept**: `api-openapi.json` stays the committed, CI-diffed
   contract; #194 and #223 build on the consolidated surface.

## Design decisions this plan makes

1. **The merge direction is into `models.Config`, and `store.py`
   imports it.** `DomainConfig` in `store.py` (line ~162) and the
   domain half of `Config` in `models.py` carry the same fields
   and validators; the one declaration lives in `models.py`, where
   every other entity model lives, and the store validates
   against it. Public name count drops by one model and its
   validators; nothing observable moves (the same pydantic
   metadata generates the same schema output, proved by the
   byte-identical artifacts).
2. **The fan-out is kept; the registry's generative half goes.**
   The audit's finding was that the registry's 692 lines now buy
   strings, not structure, after the conscious de-abstraction kept
   the hand-written five-kind fan-out. Picking the registry side
   would mean re-deriving the fan-out from it, reversing a
   recorded decision; picking the fan-out side means the registry
   shrinks to the descriptor strings the CLI help, docgen, and
   API descriptions actually read (names, addressing, kind words),
   and stops carrying machinery that could have generated code
   nobody generates. `leads_with` and `always_shown` go with it:
   each has one non-empty use, and the one consumer each serves
   reads the fact from the descriptor today only to apply it in
   one place, so the fact moves to that place as a literal with a
   comment naming the entity.
3. **The OpenAPI prose becomes data beside the code, loaded once.**
   The ~550 lines of description string literals move to
   `config/api_descriptions/` as one Markdown-ish text file per
   route group (or one TOML map; the implementer picks whichever
   the loader keeps simplest and says why), loaded at import into
   the same structures `api.py` passes FastAPI today.
   `api-openapi.json` is byte-identical by construction, and the
   CI diff plus the committed-artifact test prove it. The loader
   is a function in `api.py`, not a new module, unless the file
   count argues otherwise; the deletion test governs.
4. **The wording-pin retreat has one rule, applied per test.** A
   test that asserts an exact sentence today asserts after this:
   the status code, the problem `type`/`title` field structure,
   the presence and type of each documented field, and any
   SEMANTIC token the sentence carries (an entity name, a count, a
   field path), never the sentence. The ~40 wording constants
   (`REFUSED_*`-style acknowledgement strings and friends in the
   config package) return to module-private with a leading
   underscore where only the module and its tests read them; a
   constant something outside the package genuinely reads keeps
   its name and is listed in the implementation doc. The no-leak
   sentinels are NOT wording pins and do not change: planting a
   credential and asserting its absence is structure, not wording.
5. **`responses.py` splits by consumer.** Models that define wire
   shapes the OpenAPI document renders stay (they are the
   contract); helpers and structures whose only readers are tests
   fold into those tests or die; anything `cli.py` reads keeps a
   public name. The split is enumerated in the implementation doc
   with each name's disposition.
6. **The proof surface.** `uv run vinga-server config openapi`
   byte-identical against the committed document at every commit
   of the milestone; `config reference` (domain docgen)
   byte-identical unless decision 2's descriptor shrink removes a
   string the reference printed, in which case the diff is shown
   and justified in the PR; the whole config test family green;
   the public-name count of the package before and after recorded
   by `python -c` over `__all__`s in the implementation doc.

## The standing review lenses, pre-answered

- **No-leak.** No observability or refusal surface changes
  semantically; the wording retreat makes tests LESS coupled to
  sentences while the sentinels that hold refusals value-free are
  untouched. The OpenAPI prose files carry only text already in
  the committed public document.
- **Pin before reshaping.** The byte-identical `api-openapi.json`
  at every commit is the pin for decisions 1, 3, and 5; the
  domain reference document pins decision 2's visible half; the
  config family's structural assertions survive the wording
  retreat and keep pinning behavior.
- **Closed sets.** Not this issue's territory.
- **Honest seams.** The descriptor registry keeps only read
  strings; no injectable changes.
- **Inventories by tooling.** Before/after public-name counts;
  `grep -rn "from vinga_server.config.writes import" src tests`
  returning nothing after; `wc -l` of the package before and
  after in the implementation doc.

## Module layout

- `config/models.py`: gains the merged domain model declaration.
- `config/store.py`: loses `DomainConfig`, imports from models.
- `config/writes.py`: deleted; its 16 f-strings inlined at their
  call sites.
- `config/responses.py`: shrunk per decision 5.
- `config/entities.py`: shrunk per decision 2.
- `config/api.py`: loses the description literals; gains the small
  loader; `config/api_descriptions/` gains the data files.
- Tests: the wording-pin retreat per decision 4 across the config
  family; everything else untouched.
- `CHANGELOG.md` under `## 2026-08-23`.

## Milestones

- [ ] **M1: merge, fold, and shrink the structures.** (PR TBD)
  Decisions 1, 2, and 5: the DomainConfig merge, the writes.py
  fold, the responses split, the entities shrink. Byte-identical
  artifacts prove nothing observable moved. Deepens
  `config/models.py` and `config/store.py`: the store's readers
  stop knowing a second domain model exists.
- [ ] **M2: the prose to data and the wording retreat.** (PR TBD)
  Decisions 3 and 4, stacked on M1: the description files, the
  loader, the per-test retreat, the constants re-privatized.
  Byte-identical OpenAPI again; the test diff is the bulk and is
  mechanical under decision 4's one rule.
