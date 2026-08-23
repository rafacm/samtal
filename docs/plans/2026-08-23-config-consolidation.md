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

1. **The merge is a move plus a subclass, never a collapse.**
   `DomainConfig` (the 7 fields and 3 FIELD validators, no model
   validator) is declared once in `models.py`, and `Config`
   subclasses it, adding `server`, `memory`, the accessors, and
   the boot-only `_check_domain` model validator. `store.py`
   imports `DomainConfig` from `models`. The subclass shape is
   what keeps two contracts intact: write-time validation stays
   reference-half-only (validating the store against `Config`
   would run `check_completeness` at write time and refuse the
   first `set agent` into an empty database, the exact wedge the
   store's docstring exists to prevent), and the 7-field model
   survives BY NAME, so `config schema` and the reference's
   whole-domain table render exactly what they render today.
   Consequences stated: `Config.model_fields` order changes
   (inherited domain fields first, `server` and `memory` last),
   and the milestone verifies nothing reads that order before
   relying on it; `docgen.py` stops importing `store` (it
   imported SQLAlchemy and cryptography for this one class), an
   incidental win recorded in the implementation doc;
   `DomainSnapshot` SURVIVES with its docstring's reason updated
   (the checks still run against two objects, now related by
   inheritance); the `DOMAIN_DESCRIPTIONS` comment naming "two
   models carry these fields" is rewritten in the same commit;
   and the store's model stays FREE of after-validators
   permanently, because `_read_domain` assigns `agent_defaults`
   and `default_agent` after construction, a constraint now
   stated beside the class. The pins for this decision are the
   reference document byte-identical, `config schema` output
   byte-identical, and the store's write-order suite; the OpenAPI
   document cannot see either model and pins nothing here.
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

## Plan review round

External review of commit `c37d5516`, 2026-08-23. Backend:
claude CLI 2.1.239, model `claude-opus-5`, read-only tool set
(interim fallback tier). Verdict as received: NOT READY; findings 1
to 5 each block M1 as written and finding 4 blocks M2's central
move; none of it touches the issue's five decisions, which are all
implementable; what was missing is the how, against this code.
Findings condensed but faithful:

1. **P1: the merge direction collides with `Config`'s boot-only
   model validator.** Validating the store against `Config` runs
   `check_completeness` at write time and the first `set agent`
   into an empty database is refused. The shape is a move plus a
   subclass: `DomainConfig` declared in `models.py`,
   `Config(DomainConfig)` adding `server`, `memory`,
   `_check_domain` and the accessors; field-order consequence
   stated.
2. **P1: the merge changes two committed artifacts** (`config
   schema` and the reference's whole-domain table render
   `DomainConfig`) unless the 7-field model survives by name; the
   subclass shape keeps them still, and the move removes docgen's
   store import edge, an incidental win to record.
3. **P1: `api-openapi.json` cannot pin decision 1** (neither model
   is a component of the document). The real pins: the reference
   byte-identical, `config schema` byte-identical, and the store's
   write-order suite.
4. **P1: the OpenAPI prose is mostly route DOCSTRINGS FastAPI
   reads, not literals**; `API_DESCRIPTION` interpolates `MASK`
   and `API_OPTIONS_NOTE`, carries literal `{code}`-style braces,
   and its line wrapping is pinned. Scope decision 3 to the
   module-level description constants, leave docstrings in place,
   and state the loader's interpolation contract.
5. **P1: `responses.py` has no tests-only parts** (every public
   name has a production reader); the stale premise is recorded,
   and the real question is whether `outcomes`/`flags`/
   `RELOAD_SECTIONS` belong in `cli.py`, their only caller.
6. **P2: decision 2 names no field dispositions.** 31 store reads
   and 21 api reads of descriptor fields are load-bearing; the
   registry has no generative machinery left post-#210; the one
   real duplication is `route`+`addressing` versus api.py's path
   literals, which the plan never names.
7. **P2: the honest-seams pre-answer is wrong**: `secret_key` is
   an injected predicate and the masking rule for every displayed
   value; it stays, and the masking path unchanged is the claim to
   make.
8. **P2: the underscore rule collides with the test-surface
   rule.** One rule with no residue: after the retreat no test
   imports a wording constant; survivors keep public names and
   are listed (`PROBLEM_TITLES` at minimum, plus the constants
   non-config suites import).
9. **P2: M1 edits eight test files, five outside the config
   family** (the notice-constant import redirect from writes to
   entities); mechanical, no assertion change, and it is what
   makes the milestone split work.
10. **P2: seven folded sentences have two call sites**, and the
    pin that replaces `writes.py`'s single-source guarantee is
    `test_a_local_write_acknowledges_what_the_api_acknowledges`,
    a differential assertion explicitly exempt from the retreat;
    the module docstring's rationale needs a surviving home.
11. **P2: document prose and runtime refusal bodies are two
    kinds**, and `PROBLEM_DESCRIPTIONS` is both; decision 3 moves
    document prose only and `PROBLEM_DESCRIPTIONS` stays in code.
12. **P2: nothing proves a wheel carries the data files.** Extend
    the wheel step to render the document from the installed
    wheel and diff it; the loader raises a named error at import
    on a missing file.
13. **P3: two recorded facts go stale** (the DOMAIN_DESCRIPTIONS
    comment; `DomainSnapshot`'s reason changes under the subclass
    shape).
14. **P3: `_read_domain` assigns two fields after construction**,
    which is why the store's model must stay free of
    after-validators; record the constraint.
15. **P3: the inventory commands will not run as written** (paths,
    import forms), and the writes.py census is 13 f-string
    factories, one constant, two decisions.
