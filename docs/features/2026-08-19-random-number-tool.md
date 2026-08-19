# A builtin that draws a real random number

## Problem

Issue #82. A language model cannot produce randomness. Asked to roll a
die it writes whichever digit its distribution favours, writes the same
one again the next time it is asked, and sounds equally certain either
way. Everything a household actually uses chance for goes through that
same broken step: rolling for a board game, flipping for who goes
first, drawing lots, thinking of a number for a child to guess. The
assistant answers all of them confidently and none of them honestly,
and the failure is invisible until somebody notices the same number
keeps coming up.

The server, unlike the model, has a real entropy source one import
away.

## Changes

A third builtin tool, `random_number`, beside `switch_agent` and
`remember`.

**One tool, two optional bounds.** `minimum` and `maximum`, both whole
numbers, both included in the range. Defaults are 1 and 6, an ordinary
die, so a model that sends no arguments at all gets the most common
case rather than an error. The description tells the model what chance
is for here (dice, coins, lots, who goes first, a number to guess) and
says outright that a number it chooses itself is not random and will
not feel fair, since the tool only helps if it is reached for.

**Honest entropy.** `secrets.randbelow`, which reads the operating
system's pool, rather than a seeded generator shared with whatever else
in the process might be drawing from it. The draw is synchronous and
the dispatch calls it inline: it reads a few bytes and returns, so
there is nothing to await and nothing that could hold the event loop.

**Hard bounds, and refusals in the shape the builtins already use.**
Each bound is held between -1000000 and 1000000. Wide enough for
anything somebody says out loud, narrow enough that the answer stays
speakable, which is the only output this pipeline has: a model asking
for a twenty-digit range is not asking on a user's behalf. A bound that
is not a whole number, a bound outside the limits, and a range that
runs backwards each raise a `ValueError` naming the tool and the
argument, which the runtime turns into the error result the model reads
and calls again from. A bool is refused rather than taken as 0 or 1:
Python says a bool is an int, and a model that sent `true` for a bound
meant something this tool cannot honour.

**Offered unconditionally**, which is what separates it from its two
siblings. `switch_agent` appears under a device-shaped condition and
`remember` under a deployment-shaped one; `random_number` has neither.
It is configured by nothing, reaches nothing outside the process, and
there is no fact about a board or a deployment that would make chance
apply to one agent and not another. No configuration surface was added
and the tool stays outside the `mcp` grant model, exactly as the other
builtins do.

**The name joins the reserved set.** `random_number` is bare, like the
other builtins, so `names.BUILTIN_TOOL_NAMES` carries it and an
`mcp_servers` entry may no longer be called that. Two things follow
from the same line without further work: the turn record and the
`tool_call` event classify the call as `builtin`, and the log line that
names a tool may quote this one, since it is a name this server
authored rather than a peer's bytes.

## Key parameters

| Parameter | Value | Where |
| --- | --- | --- |
| Tool name | `random_number` | `tools/names.py` |
| Default range | 1 to 6 (a die) | `tools/builtin.py` |
| Hard bound on each end | -1000000 to 1000000 | `tools/builtin.py` |
| Entropy source | `secrets.randbelow` | `tools/builtin.py` |
| Timeout | the builtin default (15 s) | unchanged |

## Verification

- `uv run ruff check .` clean.
- `uv run pytest tests/unit -q`: 3031 passed, 16 skipped.
- `uv run pytest tests/integration -q`: 58 passed. The end-to-end grant
  test now also proves the tool reaches a real conversation: the
  restricted agent's reply lists `random_number` beside the two tools
  it was granted.
- All four generated references regenerated (`config reference`,
  `config openapi`, `events reference`, `conversations schema`). Two
  drifted, both from the one sentence in the `mcp` field's description
  that lists the builtins outside the grant model; the event and
  conversation schemas were byte-identical, since a new builtin adds no
  event and no column.
- The new unit file covers the definition, the draw with the entropy
  scripted, the defaults, a single-value range, a negative range, the
  widest allowed range, and every refusal. It asserts range membership
  over many draws and never that two draws differ, which is the one
  property a random source may legitimately fail on any given
  afternoon.

## External review round

codex CLI, model gpt-5.6-sol, 2026-08-19, on the PR #204 diff. Three
findings.

1. **P1, rejected bounds reach the conversation API.** A call is
   recorded before it runs, so a bound the tool threw away is persisted
   and served. *Declined, with the code checked first.* This is the
   content-and-telemetry split
   ([ADR](../adr/2026-08-15-content-and-telemetry-are-separate-surfaces.md))
   working as designed. Verified: the `tool_call` event declares a
   closed field set (source, tool, duration_ms, is_error, the entry for
   an MCP call), the emitter refuses an undeclared field at emit time,
   the refusal path logs nothing of its own, and the store strips `tool`
   from every `tool_call` row on top of that, so no argument value
   reaches any event field or any log record. The `/conversations` reads
   are registered on the gated `/api` sub-application, whose bearer gate
   compares every request against the token resolved from
   `SAMTAL_API_SECRET` at boot, and the rows exist only where the
   deployment left the `text` switch on. An argument redacted because
   the tool refused it would hide the evidence of why it refused, which
   is what that record is for. The decline is pinned by a test
   (`test_a_rejected_tool_argument_is_kept_as_content_and_named_on_no_telemetry`):
   a credential-shaped bound is refused, then asserted present verbatim
   on the `tool_invocations` row and absent from every emission, every
   events row, and every log record in both formats. The pin was checked
   by leaking the value onto the event on purpose.
2. **P2, the entropy call is unpinned.** *Accepted.* The draw is now
   driven with a scripted `randbelow`, asserting the width it is asked
   for (`maximum - minimum + 1`) and turning both ends of what it may
   answer into the two endpoints, which is also the statement that the
   range is inclusive at both ends. Narrowing the width by one fails it.
3. **P2, the grant integration test compared by subtraction.**
   *Accepted.* Subtracting every builtin name made the test blind to a
   builtin offered where its condition does not hold. Both offers are
   now compared as complete sets, with the builtins genuinely due in
   that configuration named in one place.

## Files modified

- `samtal-server/samtal_server/tools/builtin.py`
- `samtal-server/samtal_server/tools/names.py`
- `samtal-server/samtal_server/tools/source.py`
- `samtal-server/samtal_server/config/models.py`
- `samtal-server/tests/unit/test_tools_random.py` (new)
- `samtal-server/tests/unit/test_session_tools.py`
- `samtal-server/tests/unit/test_tool_names.py`
- `samtal-server/tests/unit/test_conversations_session.py`
- `samtal-server/tests/integration/test_tools.py`
- `samtal-server/README.md`
- `samtal-server/examples/mcp-server-stdio.yaml`
- `samtal-server/examples/mcp-server-streamable-http.yaml`
- `docs/reference/api-openapi.json`
- `docs/reference/domain-config.md`
- `docs/features/2026-08-19-random-number-tool.md` (this document)
- `CHANGELOG.md`
