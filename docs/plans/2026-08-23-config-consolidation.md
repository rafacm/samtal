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
2. **`writes.py` folds into its call sites** (13 f-string
   factories, one constant, two decisions), and `responses.py`
   moves what its real readers argue for (decision 5; the issue's
   tests-only premise did not survive the review's grep);
   response models defining real wire shapes stay.
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
2. **The fan-out is kept, and the registry shrink is a field-by-
   field disposition, not a slogan.** Post-#210 the registry holds
   no generative machinery (the route factory died in that batch);
   what decision 2 actually does is drop the two speculative
   fields and state every other field's reader. Goes:
   `leads_with` and `always_shown` (one non-empty use each; the
   fact moves to its one consumer as a literal with a comment
   naming the entity). Stays, with named readers recorded in the
   implementation doc: `table`, `secret_slots`, `moved_key`,
   `missing` (the store's 31 reads), `notice`, `has_delete`
   (api and cli fan-out and row construction), `route` and
   `addressing` (the CLI's URL building), the docgen strings, and
   `secret_key`, the ONE non-string descriptor fact: an injected
   predicate that is the masking rule for every displayed value
   (`views.entity_body`'s path is unchanged, which is the no-leak
   statement this decision owes). The one REAL duplication in this
   territory is named and priced rather than hidden: `route` +
   `addressing` on descriptors versus `api.py`'s literal path
   strings are two spellings of the same paths, held together by
   the committed OpenAPI document (which renders the literals) and
   the CLI integration tests (which drive the descriptors); this
   plan leaves both, because collapsing either direction re-opens
   the de-abstraction decision the audit affirmed, and the pin
   pair above is what keeps them agreeing.
3. **Decision 3 moves DOCUMENT PROSE CONSTANTS only, and the
   boundary is drawn three ways.** First: most of the document's
   prose is route DOCSTRINGS, which FastAPI reads as the operation
   descriptions; those stay exactly where they are, because the
   docstring IS the description and a route whose prose lives in
   another file is harder to read (the deletion test cuts against
   moving them). What moves is the module-level document-prose
   constants (`API_DESCRIPTION` and the `*_DESCRIPTION` family).
   Second: runtime refusal bodies (`UNAUTHORIZED`,
   `MALFORMED_REQUEST`, `UNEXPECTED`, the `*_BODY` trio, and the
   per-route refusal constants) are BEHAVIOR an operator reads and
   stay in code; `PROBLEM_DESCRIPTIONS`, which is both document
   prose and a raised detail, stays in code with that dual role
   stated in its comment. Third: the loader's interpolation
   contract is explicit, because `API_DESCRIPTION` interpolates
   `MASK` and `API_OPTIONS_NOTE` and carries literal `{code}` and
   `{name}` path braces, and its line wrapping is pinned by
   tests: the data file uses a placeholder syntax that cannot
   collide with path braces (`$MASK$`-style sigils substituted by
   the loader from the same constants), the loader raises a named
   error at import when a file or placeholder is missing (a
   packaging mistake is a refusal with a sentence, never a
   contract that silently lost its prose), and
   `test_the_document_states_the_unchanged_value_marker` is
   unchanged and keeps closing the drift it was written for.
   `api-openapi.json` byte-identical proves the move.
4. **The wording-pin retreat has one rule, applied per test.** A
   test that asserts an exact sentence today asserts after this:
   the status code, the problem `type`/`title` field structure,
   the presence and type of each documented field, and any
   SEMANTIC token the sentence carries (an entity name, a count, a
   field path), never the sentence. The privatization rule has NO
   residue, per the repository's interface-is-the-test-surface
   rule: after the retreat, no test imports a wording constant at
   all; a constant a test still needs is by definition one whose
   test did not retreat, and it keeps its public name and is
   listed as a survivor in the implementation doc
   (`PROBLEM_TITLES` at minimum, which `tests/support/problems.py`
   maps status to reason phrase with and `problem_response` reads
   at runtime; plus whatever the non-config suites that import
   `UNAUTHORIZED`/`UNEXPECTED` retain after their own retreat).
   An underscored constant with a test reader would be exactly
   the reach-in AGENTS.md flags, and none is created. The no-leak
   sentinels are NOT wording pins and do not change; neither is
   the differential acknowledgement pin of decision 6.
5. **The issue's `responses.py` premise is stale, and the plan
   records it.** Post-#210, every public name in the module has a
   production reader (verified by the review round against
   `cli.py` and `api.py` line by line); nothing is tests-only.
   The real moves the file supports: the three `*_DESCRIPTION`
   constants whose only readers are the `Field(description=...)`
   calls three lines below inline into those calls, and
   `outcomes`, `flags`, and `RELOAD_SECTIONS` move to `cli.py`,
   their only caller, per the deletion test. The wire-shape
   models stay, being the contract. Each name's disposition is
   enumerated in the implementation doc.
6. **The `writes.py` fold keeps its guarantee through the
   differential pin, and its census is stated honestly.** The
   module is 13 f-string factories, one constant-returning
   function, and two branching decisions (`binding_notice`,
   `secret_notice`, whose docstrings move with their logic to the
   one place each lands). Seven sentences have two call sites
   (api and cli-local); the guarantee the module's docstring
   claims (the break-glass path and the ordinary one cannot
   describe the same act differently) is ALREADY pinned by
   `test_a_local_write_acknowledges_what_the_api_acknowledges`,
   which runs each mutating act both ways and asserts equal
   output; that test is a differential assertion, not a wording
   pin, is explicitly exempt from decision 4's retreat, and is
   named in the module-deletion commit as the surviving home of
   the rationale. M1 also includes the mechanical import redirect
   the fold forces: the five notice constants `writes.py`
   re-exports from `entities.py` are imported by eight test
   files, five outside the config family
   (`test_app_lifespan.py`, `test_device_bindings.py` unit and
   integration, `test_activation.py`, `test_mcp_reload.py`);
   redirecting those imports to `entities` carries no assertion
   change, which is what lets M1 land byte-identical with none of
   M2's wording work.
8. **The proof surface.** `uv run vinga-server config openapi`
   byte-identical against the committed document at every commit
   of the milestone; `config reference` and `config schema`
   byte-identical (decision 1's pins) unless decision 2's
   descriptor shrink removes a string the reference printed, in
   which case the diff is shown and justified in the PR; the
   whole config test family green; the public-name count of the
   package before and after recorded in the implementation doc.
   And because decision 3 turns committed contract into package
   data loaded at import, the CI wheel step is extended to render
   the OpenAPI document FROM THE INSTALLED WHEEL with the source
   tree off sys.path and diff it against the committed JSON,
   which is the same discipline the Alembic scripts already get
   and the only proof a checkout run cannot fake.

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
  from `vinga-server/`, `grep -rnE "config\.writes|config import
  writes" src tests` returning nothing after the fold (all import
  forms, not one spelling); `wc -l src/vinga_server/config/*.py`
  before and after in the implementation doc.

## Module layout

- `config/models.py`: gains the merged domain model declaration.
- `config/store.py`: loses `DomainConfig`, imports from models.
- `config/writes.py`: deleted; its factories inlined at their
  call sites, the two decisions moved whole with their docstrings,
  and the eight-file notice-import redirect applied.
- `config/responses.py`: shrunk per decision 5.
- `config/entities.py`: shrunk per decision 2.
- `config/api.py`: loses the description literals; gains the small
  loader; `config/api_descriptions/` gains the data files.
- Tests: M1 carries only the mechanical notice-import redirect
  (eight files, five outside the config family, no assertion
  change); M2 carries the wording-pin retreat per decision 4.
- `CHANGELOG.md` under `## 2026-08-23`.

## Milestones

- [x] **[M1: merge, fold, and shrink the structures.](2026-08-23-config-consolidation-implementation.md#m1-merge-fold-and-shrink-the-structures)** (PR TBD)
  Decisions 1, 2, 5, and 6: the DomainConfig merge, the writes.py
  fold with its differential pin, the responses moves, the
  entities shrink. M1's REAL test census, per the delta review:
  nine notice-import files (four config, five outside),
  `test_config_entities.py` losing its writes import and the
  whole of the display-facts test whose only subject is the two
  deleted fields, the two importers of the relocating responses
  helpers (`test_config_cli_transport.py`,
  `test_config_cli_rendering.py`), and the `CLEARED_DEFAULT_AGENT`
  constant joining the census. `DomainSnapshot` may be DELETED in
  M1 if the subclass makes the Protocol redundant, the deletion
  test governing, with the choice recorded. Byte-identical
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
   *Resolution* (`c640076a`): the subclass shape, its stated
   consequences, the after-validator constraint, and the real
   pins.

2. **P1: the merge changes two committed artifacts** (`config
   schema` and the reference's whole-domain table render
   `DomainConfig`) unless the 7-field model survives by name; the
   subclass shape keeps them still, and the move removes docgen's
   store import edge, an incidental win to record.
   *Resolution* (`c640076a`): the 7-field model survives by name;
   both artifacts stay still; docgen loses its store edge.

3. **P1: `api-openapi.json` cannot pin decision 1** (neither model
   is a component of the document). The real pins: the reference
   byte-identical, `config schema` byte-identical, and the store's
   write-order suite.
   *Resolution* (`c640076a`): the reference, config schema, and
   the write-order suite are named as the pins.

4. **P1: the OpenAPI prose is mostly route DOCSTRINGS FastAPI
   reads, not literals**; `API_DESCRIPTION` interpolates `MASK`
   and `API_OPTIONS_NOTE`, carries literal `{code}`-style braces,
   and its line wrapping is pinned. Scope decision 3 to the
   module-level description constants, leave docstrings in place,
   and state the loader's interpolation contract.
   *Resolution* (`19b1f77f`): decision 3 scoped to document-prose
   constants with the three fences and the loud loader.

5. **P1: `responses.py` has no tests-only parts** (every public
   name has a production reader); the stale premise is recorded,
   and the real question is whether `outcomes`/`flags`/
   `RELOAD_SECTIONS` belong in `cli.py`, their only caller.
   *Resolution* (`a2505396`): the stale premise recorded; the
   honest moves are the Field-description inlines and the
   cli-only helpers relocating.

6. **P2: decision 2 names no field dispositions.** 31 store reads
   and 21 api reads of descriptor fields are load-bearing; the
   registry has no generative machinery left post-#210; the one
   real duplication is `route`+`addressing` versus api.py's path
   literals, which the plan never names.
   *Resolution* (`01f7a7a0`): field-by-field dispositions and the
   route-versus-literal duplication named and priced.

7. **P2: the honest-seams pre-answer is wrong**: `secret_key` is
   an injected predicate and the masking rule for every displayed
   value; it stays, and the masking path unchanged is the claim to
   make.
   *Resolution* (`01f7a7a0`): secret_key stated as the injected
   masking predicate that stays, the masking path unchanged.

8. **P2: the underscore rule collides with the test-surface
   rule.** One rule with no residue: after the retreat no test
   imports a wording constant; survivors keep public names and
   are listed (`PROBLEM_TITLES` at minimum, plus the constants
   non-config suites import).
   *Resolution* (the finding-8 commit): no-residue privatization
   with the survivor list.

9. **P2: M1 edits eight test files, five outside the config
   family** (the notice-constant import redirect from writes to
   entities); mechanical, no assertion change, and it is what
   makes the milestone split work.
   *Resolution* (`c6a84837`): the redirect is named M1 work,
   mechanical, five files outside the family.

10. **P2: seven folded sentences have two call sites**, and the
    pin that replaces `writes.py`'s single-source guarantee is
    `test_a_local_write_acknowledges_what_the_api_acknowledges`,
    a differential assertion explicitly exempt from the retreat;
    the module docstring's rationale needs a surviving home.
   *Resolution* (`c6a84837`): the differential test is the
   surviving guarantee, exempt from the retreat, the census
   honest.

11. **P2: document prose and runtime refusal bodies are two
    kinds**, and `PROBLEM_DESCRIPTIONS` is both; decision 3 moves
    document prose only and `PROBLEM_DESCRIPTIONS` stays in code.
   *Resolution* (`19b1f77f`): prose-only movement with
   PROBLEM_DESCRIPTIONS staying in code.

12. **P2: nothing proves a wheel carries the data files.** Extend
    the wheel step to render the document from the installed
    wheel and diff it; the loader raises a named error at import
    on a missing file.
   *Resolution* (`3b505b54`): the wheel step renders the document
   from the installed wheel and diffs it.

13. **P3: two recorded facts go stale** (the DOMAIN_DESCRIPTIONS
    comment; `DomainSnapshot`'s reason changes under the subclass
    shape).
   *Resolution* (`c640076a`): DomainSnapshot survives with its
   reason updated; the comment is rewritten in the same commit.

14. **P3: `_read_domain` assigns two fields after construction**,
    which is why the store's model must stay free of
    after-validators; record the constraint.
   *Resolution* (`c640076a`): the constraint recorded beside the
   class.

15. **P3: the inventory commands will not run as written** (paths,
    import forms), and the writes.py census is 13 f-string
    factories, one constant, two decisions.
    *Resolution* (`5b84c09c`): runnable greps covering every
    import form, and the honest census everywhere.

## Delta review round

A focused re-review of the amended plan (same backend and model)
verified all fifteen resolutions against the code, found NO new
P1, and returned two residues folded in above: the decision
numbering slip (the fold is decision 6; the milestones now say
so) and M1's understated test census (nine notice-import files,
the entities display-facts test dying with its two fields, the
two responses-helper importers, `CLEARED_DEFAULT_AGENT` in the
census). It also noted `DomainSnapshot`'s reason weakens under
the subclass shape; M1 may delete it under the deletion test.
Verdict as received: "Ready to implement: every P1 resolution
holds against the code it claims to fix."
