# Enforce the egress guarantee through one rule

Companion to
[`2026-08-15-one-egress-rule.md`](2026-08-15-one-egress-rule.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: move both egress checks into one module and make the marking mandatory

`samtal_server/egress.py` now holds the rule and every refusal
sentence; the provider registry and the MCP build path call it and keep
only their own exception types; the `Provider` base carries the
annotation and no value; and four tests in
`tests/unit/test_providers_egress.py` pin the mandatory marking at the
build path, all four of them failing against the old enforcement and
passing against the new.

### What landed

**`samtal-server/samtal_server/egress.py`** (new, 137 lines). A module
docstring in the house voice explaining why one module owns the rule
(the principles page's enforced-not-documented promise, and a guarantee
with two implementations and a default being one nobody can read in a
sitting), citing #30 and #136 and stating the two conventions the
sentences follow: value-free, and "off this host" for providers against
"off this network" for MCP entries, which is a distinction rather than
drift.

- `EgressRefusal(Exception)` carries the finished sentence and nothing
  else.
- `check_provider(label, config, provider, local_only)` is the old
  registry check with the marking read differently and the exception
  type changed.
- `_marking(label, config, provider)` is the new part: it reads
  `vars(type(provider)).get("egress", _UNDECLARED)` and refuses an
  absent marking, then validates by identity
  (`marking is True or marking is False or marking is None`) and
  refuses anything else. `_UNDECLARED` is a module-level sentinel
  object, needed because `None` is itself a marking. Both refusals fire
  whatever the mode.
- `check_mcp_server(name, entry)` is the old MCP check verbatim, with
  the `local_only` guard deliberately left at its call site.

Neither `ProviderError` nor `McpConfigError` is imported; the only
import is `McpServerConfig` and `ProviderConfig` from
`samtal_server.config.models`. That module does import from the
`tools` package, but only the leaf `samtal_server.tools.names`
(naming rules with no imports of its own back into `tools.mcp`),
and `tools/__init__.py` is deliberately inert, so the path from
`egress.py` never reaches `tools.mcp` or `providers` and no cycle
is possible.

**`samtal-server/samtal_server/providers/base.py`.** `egress:
ClassVar[bool | None]` keeps the annotation and loses `= True`. The
docstring paragraph that blessed the default is replaced by one saying
there is no default, that every concrete type declares in its own class
body, that inheriting a parent's marking does not count, and that the
abstract stage bases stay undeclared because nothing builds them.

**`samtal-server/samtal_server/providers/registry.py`.** `_check_egress`
is gone. `build_provider` calls `check_provider` inside a `try` and
re-raises `EgressRefusal` as `ProviderError(str(exc)) from exc`, under a
comment carrying the #30 and #136 trail. Its docstring's list of what
raises `ProviderError` gains the missing-marking case.

**`samtal-server/samtal_server/tools/mcp.py`.** `_check_egress` is gone.
`_managers_for` calls `check_mcp_server` under the `if
config.server.local_only:` guard it already had, wrapping
`EgressRefusal` as `McpConfigError`. `McpServerConfig` is still imported
there for other uses, so the import list did not change apart from the
new `samtal_server.egress` line.

**`samtal-server/tests/unit/test_providers_egress.py`.** One helper,
one rewrite, three additions, and the module docstring updated to say
what the file now pins. The helper,
`build_a_throwaway_llm(monkeypatch, make)`, replaces
`registry._factories` with a one-entry table and calls
`build_provider("llm", "brain", ...)` with `local_only` off; the table
is rebuilt on every call inside `build_provider`, which is why the
function is patched rather than a dict mutated.

- `test_a_type_that_forgot_to_declare_counts_as_egress` became
  `test_a_type_that_forgot_to_declare_is_refused_at_construction`: a
  bare `Provider` subclass built through `build_provider` raises
  `ProviderError` naming the entry and the class.
- `test_the_provider_bases_declare_no_egress_at_runtime`: `Provider`
  and the four stage bases answer False to `hasattr(base, "egress")`.
- `test_an_unmarked_subclass_does_not_ride_its_parents_marking`: a bare
  subclass of `MockLlm` (marked `False`) is refused, named.
- `test_a_marking_that_is_not_one_of_the_three_is_refused`: a class
  declaring `egress = 0` is refused, named, and the message is asserted
  not to contain `"0"`, which is the value-free convention made
  checkable.

Every other test in the file is untouched, and no other test file
changed.

**`CHANGELOG.md`.** A `### Changed` block added above the existing
`### Fixed` in the `## 2026-08-15` section, in Keep a Changelog order.

### The refusal sentences, old against new

The five sentences that existed before are byte-identical after the
move. Extracting every f-string literal line from the two old checks
(`registry.py` at `main`, lines 284-303, and `tools/mcp.py` at `main`,
lines 1255-1267) and from the new module, stripping indentation and
sorting both:

```
diff old.txt new.txt
4a5,6
> f'{label}: type "{config.type}" builds {kind.__name__}, which declares '
> f'{label}: type "{config.type}" builds {kind.__name__}, whose "egress" '
5a8
> f'no "egress" of its own; every provider class states whether it sends '
6a10
> f"is none of true, false or null; correct the declaration on the class"
11a16
> f"session data off this host"
```

Additions only, no deletions and no modifications: 13 old lines, 18 new.
Four of the five added lines are the two new refusals; the fifth,
`f"session data off this host"`, is a duplicate of a line the old set
already had, because the undeclared-marking sentence happens to end the
same way as the egress-under-local_only one. Declared behavior therefore
reads exactly as it did, which is what the fragment assertions in
`test_providers_egress.py` and `test_tools_mcp.py` (`'"egress: false"'`,
`"off this host"`, `"off this network"`, `'decided by type "mock"'`)
continue to hold unmodified.

The two new sentences:

```
providers.llm.brain: type "throwaway" builds Forgetful, which declares no
"egress" of its own; every provider class states whether it sends session
data off this host

providers.llm.brain: type "throwaway" builds Sloppy, whose "egress" is none
of true, false or null; correct the declaration on the class
```

### Is `build_provider` the only factory call site?

Yes, in production code. `_factories()` is referenced at
`registry.py:186` (its definition), `:231` and `:233` (both inside
`build_provider`), and the factory is called once, at `:258`, also
inside `build_provider`. `grep -rn "_factories\|factory(" samtal_server`
finds nothing else that is a provider factory: the other three hits are
`app.py`'s `runtime_factory`, `config/docgen.py`'s
`info.default_factory()` and `pipeline.py`'s `bespoke_runtime_factory`,
none of which build providers. `build_provider` itself is called from
exactly one place, `build_agent_providers` (`registry.py:347`), and no
module outside `samtal_server/providers/` imports a provider
implementation module at all (`grep -rn "from samtal_server.providers"
samtal_server` outside that package returns only `AgentProviders`,
`build_agent_providers`, `ProviderError`, `ToolDef`, `TtsProvider` and
the pipeline's type imports). So every provider a running server holds
passes the mandatory-marking check.

Tests do construct provider classes directly, which is exactly the
freedom the plan's build-time-not-definition-time decision preserves: a
hand-built double is not a provider the server holds.

### Readers of `provider.egress` outside the module

`grep -rn '\.egress' samtal_server tests`, minus the configuration
fields (`config.egress`, `entry.egress`, `row.egress`, `self.egress`),
finds no production reader outside `egress.py` at all. The remaining
hits are all tests asserting a concrete class's own declaration:

```
tests/unit/test_providers_elevenlabs.py:131  ElevenLabsTts.egress is True
tests/unit/test_providers_egress.py:55,57,64,71,75,76  SileroVad, the mocks,
                                             FasterWhisperAsr, PiperTts,
                                             AnthropicLlm, OpenAiCompatibleLlm
tests/unit/test_providers_openai_asr.py:161  OpenAiAsr.egress is None
tests/unit/test_providers_openai_tts.py:187  OpenAiTts.egress is None
tests/unit/test_config_store.py:113          mcp_servers["home"].egress (config)
```

Every one of them reads a class that declares in its own body, so the
missing base value reaches none of them, and the plan's `AttributeError`
risk did not materialise anywhere in the tree. The one reader that would
have been exposed was the rewritten `Forgetful.egress is True`
assertion, which is the assertion this issue exists to delete.

`grep -rn "_check_egress" samtal_server tests` finds nothing: both
functions are gone and the new ones are named `check_provider` and
`check_mcp_server`.

### Red to green

The four tests were written first, against the tree with the old
enforcement still in place (`main` plus nothing but the test file), and
run from `samtal-server/`:

```
uv run pytest tests/unit/test_providers_egress.py -q
```

Before, with the old registry check and the base-class default:

```
FAILED test_a_type_that_forgot_to_declare_is_refused_at_construction
FAILED test_the_provider_bases_declare_no_egress_at_runtime
FAILED test_an_unmarked_subclass_does_not_ride_its_parents_marking
FAILED test_a_marking_that_is_not_one_of_the_three_is_refused
4 failed, 8 passed, 2 skipped
```

The three build-path failures read `Failed: DID NOT RAISE
ProviderError`, and the fourth `assert not True, where True =
hasattr(<class 'samtal_server.providers.base.Provider'>, 'egress')`:
each fails for the reason it exists, not because the helper is broken.
The eight other tests in the file passed at that point and pass now.

After the module and the call sites landed, the same command, together
with the MCP egress tests:

```
uv run pytest tests/unit/test_providers_egress.py tests/unit/test_tools_mcp.py -q
64 passed, 2 skipped in 9.71s
```

### Deviations from the plan

None in behavior. Two things worth naming, neither a departure from a
decision:

**Function names.** The plan's verification line expects `grep -rn
"_check_egress" samtal_server` to find "only the new module's
internals". The moved functions are named `check_provider` and
`check_mcp_server` instead, so the grep finds nothing at all, which
satisfies the check it was written for (none in `providers/`, none in
`tools/`) more strictly than the wording anticipated. A leading
underscore would have been wrong on a function two other modules call.

**Order of the two provider refusals.** The mandatory-marking check runs
before the config-key conflict check, where the old code read the
marking with `getattr` and went straight to the conflict. No configured
entry can see the difference: reaching the conflict check at all
requires a class that declares, and a class that declares reaches it
unchanged.

### Discoveries

**The base-class default was load-bearing for exactly one assertion.**
The plan's risk about `AttributeError` in other readers came to nothing,
and the grep above says why: every reader in the tree names a concrete
provider class, and every concrete provider class already declared. The
hole was real but nobody was standing in it, which is the difference
between this refactor and a bug fix.

**The factory table's freshness is what made the new tests easy.**
`_factories()` rebuilds its dict on every call, so a test registers a
throwaway type by monkeypatching one function for the duration of one
`build_provider` call, with nothing to restore and no chance of a
registration leaking into another test.

### Verification

From `samtal-server/`, on `refactor/one-egress-rule` with every commit
of this milestone in place:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **1854 passed, 15 skipped** in 177 s.
  Three more than the 1851 recorded for the previous milestone, which
  is the three added tests; the fourth is a rewrite.
- `uv run pytest tests/integration -q`: **53 passed** in 154 s.
- `git diff --stat main -- tests/`: one file,
  `samtal-server/tests/unit/test_providers_egress.py`, 75 insertions
  and 7 deletions.
- `grep -rn "_check_egress" samtal_server tests`: no matches.

The bytecode trap in `AGENTS.md` did not apply: no file was restored
mid-run, and everything outside pytest ran with
`PYTHONDONTWRITEBYTECODE=1`.
