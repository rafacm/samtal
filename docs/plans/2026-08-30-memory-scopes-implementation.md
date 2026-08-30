# Memory scopes and editing: implementation

The companion to [`2026-08-30-memory-scopes.md`](2026-08-30-memory-scopes.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations
says so explicitly.

## M1: the scoped schema and the store's new sentences

PR TBD.

### What landed

In the order the commits tell it: the package's import graph, the
schema and its migration, the migration proved on rows, the events'
scope, the store's seams and its new sentences one family at a time,
the sentinels, and the documents.

- **`memory/scopes.py`,** a module of its own holding `MemoryScope`
  (`conversation`, `agent`, `device`) and `FACT_SCOPES`, its
  two-member subset. Everything derives from it: the facts table's
  check constraint, the store's caps and rendering, the events' `scope`
  field, and M3's tool enum. It imports nothing but `enum`, which is a
  tier constraint rather than a preference (see the deviations).
- **`memory/schema.py`,** grown to the shape #83 needs. `facts` now
  carries `scope` and `owner` where it carried `agent`, plus
  `forgotten_at` and `forgotten_in`, the held pair. Two check
  constraints state what the store relies on: `ck_facts_scope`, built
  from `FACT_SCOPES` so the constraint and the vocabulary cannot
  disagree, and `ck_facts_forgotten`, which keeps the pair null
  together or set together. The index becomes `ix_facts_scope` on
  `(scope, owner, id)`, the access path the ordered read, the prune and
  the lookup filter all walk, with a partial `ix_facts_forgotten` on
  `(forgotten_in, id) WHERE forgotten_in IS NOT NULL` for the paths
  that address held rows by their thread. `state` is new:
  `(conversation, key, value, updated_at)`, primary key
  `(conversation, key)`, every column with its `comment=`.
- **`migrations/versions/2002_memory_scopes.py`,** autogen-produced and
  then reviewed. Three things were written by hand, and each is stated
  in its docstring: the column rename (autogenerate proposes a drop and
  an add, and a database that took that pair would have lost every
  row); the two check constraints, which autogenerate cannot see; and
  the order, so that the `scope` backfill runs under a server-side
  default which is then dropped, since the tables declare none and
  every insert names its scope.
- **`tests/integration/test_memory_upgrade.py`,** the seeded
  preservation case the plan's finding 12 asks for: a database stamped
  at `2001_agent_memory`, three facts written through the baseline's
  own columns, brought to head the way a boot does, and read back for
  the bytes, the scope, the null held pair, the empty ledger and
  identity continuing past the seeded maximum.
- **The events.** `memory_unreadable` and `memory_unwritable` keep
  their names, channel and level and each gains `scope`, which their
  sentences now name. `memory_cleanup_failed` is new, WARNING, carrying
  the failure's class name and nothing else: the sweep and the
  session-close purge act for no agent and on no scope, so neither of
  the other two could report them honestly. `docs/reference/events.md`
  is regenerated, the README index gains its row, and the driver
  inventory grows to 85.
- **`memory/store.py`,** deepened in place around two guarded seams and
  a third for cleanup. `_read` holds one connection on the read engine
  and says `memory_unreadable` once per scope the read could not
  answer; `_written` holds one transaction on the write engine and
  classifies what leaves it, this module's own `_Refused` becoming the
  `ValueError` the tool layer rephrases and everything else being the
  database, by class and never by message; `_cleaned` contains a
  cleanup and says `memory_cleanup_failed`. On top of them:
  - `add(scope, owner, fact, agent=)` answering the row id, with
    `remember` as that call on the agent scope with the id dropped;
  - `update`, `forget` and `restore`, each addressed by `_addressed`,
    the id AND the pair that owns it, with one fixed refusal per
    operation shared by a missing id and an inaccessible one;
  - `recall(agent, device, query)`, a bounded case-insensitive
    substring lookup over both scopes' active facts, newest first, with
    ids, the pattern language escaped out of the query, and a fixed
    continuation sentence;
  - `set_state` and `clear_state`, the conversation ledger, with three
    refusals naming the three bounds a write can cross;
  - `read_for_prompt(agent, device, conversation)`, a frozen
    `PromptMemory` of three rendered blocks in one connection;
  - `purge(connection, threads)`, the seam that lets a caller's
    transaction own the deletes, `purge_threads(threads)` for a caller
    that holds none, and `sweep(grace)`, whose anti-join reads
    `record.conversations`.
- **The caps, per scope and read at call time.** `MAX_LINES` and
  `MAX_BYTES` stay 200 and 8192 (see the deviations), `DEVICE_LINES`
  and `DEVICE_BYTES` are 30 and 2048, `CORE_LINES` and `CORE_BYTES` 40
  and 4096, `RECALL_LINES` and `RECALL_BYTES` 20 and 2048,
  `STATE_KEYS` and `STATE_BYTES` 50 and 4096. Held rows are outside all
  of the arithmetic and are never pruned; every mutating transaction
  re-prunes and never takes the row it just wrote.
- **The lane and CI plumbing.** `tests/conftest.py` needed nothing: it
  reads the tables off the metadata, so `state` is truncated between
  tests by construction. The wheel step's memory block moves its head
  literal to `2002_memory_scopes`, expects both tables, and now checks
  the columns of each, so a wheel carrying the migration without the
  rename fails there too.
- **The documents.** `CHANGELOG.md` gains the dated entry with the
  stop-then-start order, and `vinga-server/README.md`'s upgrade section
  states it where an operator is already reading about upgrading. The
  command-spellings census was regenerated after both README edits, as
  the standing rule requires. Nothing else was stale: the concepts page
  and the glossary describe behavior this milestone does not change.

### Deviations from the plan

Four, and one of them is a sequencing decision the plan left to be
made here.

- **The storage cap stays at 200 lines and 8192 bytes.** The plan grows
  the agent scope to `MAX_LINES = 1000` and `MAX_BYTES = 65536`
  alongside the core/remainder split. Raising it here would balloon
  every prompt in the interim, because `read()` still injects the whole
  scope and nothing yet reads the core; the two numbers therefore move
  in M3, in the same change that makes the injected block the core.
  The names are unchanged, so the cap suites keep their monkeypatch
  shape and the growth is a two-line diff when the split lands.
- **The scope vocabulary lives in `memory/scopes.py`, not in
  `memory/schema.py`.** The plan says the schema module, and the
  difference is a tier rather than a preference: the events package is
  client-half, and `vinga-server events reference` renders from it on
  an install with no database driver at all. A `scope` field whose
  closed set came from the tables would have carried SQLAlchemy into
  that install, which `test_tier_closure.py` caught as an ImportError
  traceback on the one entry point whose every other answer is a
  sentence. The vocabulary is still declared exactly once and is still
  a fact of memory; only its module moved, and that module says why.
- **`vinga_server.memory` no longer re-exports the store.** The
  package's `__init__` imported `store.py`, which imports the events
  package, so anything the events package imported from inside
  `vinga_server.memory` was a circular import (`events/__init__` is
  half-built when `store.py` asks it for `ServerEvents`). The package
  is now docstring-only and callers name `vinga_server.memory.store`,
  which is the discipline `vinga_server.config` already keeps about its
  boot path: importing the package pulls in neither the driver nor the
  migrations. Seventeen import lines moved; nothing else did.
- **Every mutating call takes the acting agent as a keyword.** The
  plan's operations are addressed by `(scope, owner)`, which under
  device scope makes the owner a MAC while the events' `agent` field
  means the agent that was speaking. Rather than let that field carry a
  MAC, or make the field optional, the store's mutations and its
  lookups take `agent=` beside the address. Under agent scope the two
  are the same string, and the call site says so twice, which is honest:
  they are two different facts that happen to be equal.

### Discoveries

- **A prune can undo the mutation that provoked it.** A restored fact
  keeps the id it always had, so it is usually the oldest active row in
  its scope; the ordinary oldest-first prune, running in the same
  transaction, would have deleted it and answered success. The same
  holds for a correction that grows the oldest fact. `_prune` therefore
  takes the id the transaction just wrote and never chooses it, which
  is the smallest rule that makes "every mutation re-prunes" safe. The
  mutation that protects nothing fails two cases.
- **A lookup's query is pattern syntax unless it is escaped.** The
  query is the model's own text, so a lookup for `%` would have
  answered with every fact the agent has. `_containing` escapes the
  backslash, the per cent and the underscore, and the case is pinned.
- **One connection makes per-scope containment moot.** A failed
  statement poisons the transaction the other two scopes would have run
  in, so `read_for_prompt` loses all three together whatever the
  containment is written as. That is why `_read` takes the scopes it
  reached for and reports every one of them, rather than guarding each
  block separately: the honest report is per lost scope, and the honest
  structure is one guard.
- **uv's build cache can serve a stale wheel to the tier lane.** The
  tier environments are built with `uv sync --no-editable`, and after
  an edit to `events/values.py` the lane kept installing the previous
  build; `uv cache clean vinga-server` is what makes a local run of
  that lane mean anything. CI starts from a cold cache and never sees
  it.

### Verification

- `uv run ruff check .`: clean.
- `uv run mypy`: clean (5 source files, the events package).
- `uv run pytest tests/unit -q`: 4645 passed, 19 skipped.
- `uv run pytest tests/unit -q -n auto --dist loadfile`, the shape CI
  runs: same.
- `uv run pytest tests/integration -q`: 226 passed.
- `uv run vinga-server events reference` regenerated
  `docs/reference/events.md`; `uv run python -m
  tests.unit.test_command_spellings` regenerated the census after the
  README edits; `python scripts/check_doc_links.py .` checked 172 files
  with no failures. The five generated documents (`domain-config.md`,
  `conversations-schema.md`, `events.md`, `api-openapi.json`, `cli.md`)
  each regenerate to their committed bytes.
- Each guard was run against the mutation it exists for before being
  trusted: the migration's rename against the autogen candidate's
  drop-and-add; the ownership predicate without its `(scope, owner)`
  half; the prune protecting nothing; an active read that counted held
  rows; per-scope caps collapsed to one pair; the ledger's byte bound
  unchecked; the query's wildcards unescaped; the sweep without its
  grace period and without its anti-join; and `read_for_prompt` reading
  each scope on a connection of its own.
- Not verified: the `image` job, which builds and smokes the container.
