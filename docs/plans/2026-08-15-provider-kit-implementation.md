# Give providers a shared kit and a request-time error taxonomy

Companion to
[`2026-08-15-provider-kit.md`](2026-08-15-provider-kit.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out.

## Milestone 1: the kit, the taxonomy, and the five providers

`samtal_server/providers/kit.py` now holds the plumbing the five
request-making providers used to each carry a copy of;
`ProviderCallError` and `ProviderCallTimeout` land in `base.py` beside
the rest of the provider contract; all five providers consume both, the
two LLM providers gained the `timeout_s`/`client=` seam the other three
already had, and no provider imports another. Thirty-eight tests were
written or rewritten, twenty-seven of them failing against the pre-kit
providers and passing after.

### What landed

**`samtal-server/samtal_server/providers/kit.py`** (new, 172 lines). A
module docstring in the house voice explaining why one module owns this
plumbing (five providers answering the same handful of questions, three
of them importing the credential resolver from whichever provider
happened to be written first) and why the taxonomy is deliberately not
here: it is contract, the pipeline classifies by it, and it belongs
with the protocols in `base.py`.

- `API_KEY_SLOT` and `resolve_api_key`, moved verbatim from
  `anthropic_llm` with their docstring and their
  `stored_provider_secret` read.
- `DEFAULT_TIMEOUT_S = 30.0`, declared once, described as a
  per-operation transport timeout rather than a wall-clock deadline
  (the plan's finding 8).
- `DEFAULT_MAX_TOKENS = 1024`, moved from `anthropic_llm` (finding 6).
- `MAX_RETRIES = 0`, moved from `openai_endpoint` with its reasoning
  intact, since retries-off is a fact about every SDK here rather than
  about the OpenAI dialect.
- `REQUEST_FAILURES` and `REQUEST_TIMEOUTS`: the exception families a
  provider catches, and the subset that is a wait. `anthropic.APIError`,
  `openai.APIError` and `httpx.HTTPError`, plus the two SDKs'
  `APITimeoutError`, `httpx.TimeoutException` and `TimeoutError`.
- `call_failure(label, exc)`: the one place that turns an SDK exception
  into the taxonomy, carrying the provider label, the SDK class name and
  the HTTP status code when there is one, and nothing else.
  `_status_code` reads `status_code` off the exception, falls back to
  `response.status_code`, and returns it only if it is an `int`.
- `aligned_pcm(label, chunks)`: the carry-the-odd-byte loop the two TTS
  providers duplicated, with the docstring's reasoning kept and the
  dropped-byte warning under the caller's label.

**`samtal-server/samtal_server/providers/base.py`.** Two exception
classes and nothing else; no protocol changed. `ProviderCallError`'s
docstring says why it is not a `RuntimeError` (the device edge's
vanished-device catch) and that its message carries trusted metadata
only; `ProviderCallTimeout`'s says why it is also a `TimeoutError` (one
`isinstance` replaces the substring match milestone 2 deletes). Both
are exported from `providers/__init__.py` beside `ProviderError`, which
is where milestone 2's pipeline import will reach for them.

**The five providers.** Each catches `REQUEST_FAILURES` around every
place it waits on the wire and raises `call_failure`'s result:

- `anthropic_llm`: the request, the event iteration, and
  `get_final_message()`, all three inside one `try` around the
  `async with`.
- `openai_llm`: the `create` call and the chunk iteration.
- `openai_tts`: the streaming request and the byte iterator.
- `elevenlabs_tts`: the send, the status check, and the byte iterator.
  `_api_error` is now a plain function returning `ProviderCallError`
  with the status only, and no longer reads the response body at all.
- `openai_asr`: `transcribe` as a whole, which is what finding 3 asks
  for. The echo retry's own `except (TimeoutError, APITimeoutError)`
  was left exactly as it was, and still eats what it ate.

`openai_endpoint` imports `resolve_api_key` from the kit and no longer
declares `MAX_RETRIES`. `LABEL` constants (`"anthropic"`,
`"openai compatible"`, `"openai asr"`, `"openai tts"`, `"elevenlabs"`)
name each provider where the kit speaks on its behalf.

**Both LLM constructors** gained `timeout_s: float = DEFAULT_TIMEOUT_S`
and `client: ... | None = None`, mirroring `openai_asr`'s shape, with a
comment saying what the timeout is and is not and why `build` grows no
option for it. `AnthropicLlm` also lost its
`AsyncAnthropic(api_key=key) if key else AsyncAnthropic()` conditional:
the SDK treats an explicit `api_key=None` exactly as an absent argument,
so one construction covers both and the timeout and retry arguments are
written once.

**The tests.** `test_providers_llm_tools.py`'s two `_client`
assignments now pass the same fakes to `client=`; its fakes grew
`opening`, `mid_stream` and `final` failure points, and its module
docstring says the file also covers what a stream does when the wire
fails. Sixteen tests there cover both LLM providers. The three
remaining provider test files gained the same shapes through their mock
transports, and `test_providers_llm.py` gained the four
client-construction pins finding 7 asks for.

**`CHANGELOG.md`.** One entry appended to the existing
`## 2026-08-15` / `### Changed` section, naming the operator-visible
change explicitly.

### The two pins that moved beyond the plan's list

The plan names the elevenlabs `RuntimeError` pins
(test_providers_elevenlabs.py:225,233) as the tests the error-contract
change moves. Two more had the same shape and had to move with it, in
both cases from an SDK exception class to the taxonomy:

- `test_providers_openai_asr.py::test_an_api_error_raises_with_the_reason`
  and `::test_a_failing_utterance_is_attempted_once`, which pinned
  `APIStatusError` and, in the first case, matched on the vendor's own
  text `"invalid api key"`.
- `test_providers_openai_tts.py::test_an_api_error_raises_with_the_reason`
  and `::test_a_failing_sentence_is_attempted_once`, identically.

They are not weakened: each now asserts the taxonomy type, the HTTP
status, the SDK class name, and (the new part) that a sentinel planted
in the response body is absent. The `attempts == 1` assertion behind
the retry pins is untouched, which is the load-bearing half of those
two.

### Red to green

Two runs, each with the new tests present and the providers not yet
adopting the taxonomy. The kit and the taxonomy classes were committed
first, so the tests import cleanly and fail for the reason they exist
rather than at collection.

The LLM half, from `samtal-server/`:

```
uv run pytest tests/unit/test_providers_llm_tools.py -q
```

```
FAILED test_anthropic_wraps_a_request_that_timed_out
FAILED test_anthropic_wraps_an_api_error_and_keeps_the_vendors_text_out
FAILED test_anthropic_wraps_a_timeout_after_the_first_chunk
FAILED test_anthropic_wraps_a_raw_httpx_failure_after_the_response_opened
FAILED test_anthropic_wraps_a_final_message_that_never_assembled
FAILED test_a_failed_anthropic_request_leaks_nothing_into_the_logs
FAILED test_openai_wraps_a_request_that_timed_out
FAILED test_openai_wraps_an_api_error_and_keeps_the_vendors_text_out
FAILED test_openai_wraps_a_failure_after_the_first_chunk
FAILED test_openai_wraps_a_raw_httpx_failure_after_the_response_opened
FAILED test_a_failed_openai_request_leaks_nothing_into_the_logs
11 failed, 22 passed in 0.90s
```

The ASR and TTS half:

```
uv run pytest tests/unit/test_providers_openai_asr.py \
  tests/unit/test_providers_openai_tts.py \
  tests/unit/test_providers_elevenlabs.py -q
```

```
FAILED test_providers_openai_asr.py::test_an_api_error_raises_the_taxonomy_with_the_status_and_no_body
FAILED test_providers_openai_asr.py::test_a_first_request_that_timed_out_surfaces_as_a_timeout
FAILED test_providers_openai_asr.py::test_a_failed_request_leaks_nothing_into_the_logs
FAILED test_providers_openai_asr.py::test_a_failing_utterance_is_attempted_once
FAILED test_providers_openai_tts.py::test_an_api_error_raises_the_taxonomy_with_the_status_and_no_body
FAILED test_providers_openai_tts.py::test_a_request_that_timed_out_raises_the_timeout_half
FAILED test_providers_openai_tts.py::test_a_failure_after_the_first_chunk_is_wrapped_too
FAILED test_providers_openai_tts.py::test_a_timeout_after_the_first_chunk_is_still_a_timeout
FAILED test_providers_openai_tts.py::test_a_failed_request_leaks_nothing_into_the_logs
FAILED test_providers_openai_tts.py::test_a_failing_sentence_is_attempted_once
FAILED test_providers_elevenlabs.py::test_an_api_error_raises_the_taxonomy_with_the_status_and_no_body
FAILED test_providers_elevenlabs.py::test_an_error_body_reaches_neither_the_message_nor_the_logs
FAILED test_providers_elevenlabs.py::test_a_request_that_timed_out_raises_the_timeout_half
FAILED test_providers_elevenlabs.py::test_a_transport_failure_raises_the_error_half
FAILED test_providers_elevenlabs.py::test_a_failure_after_the_first_chunk_is_wrapped_too
FAILED test_providers_elevenlabs.py::test_a_timeout_after_the_first_chunk_is_still_a_timeout
16 failed, 91 passed in 1.98s
```

Twenty-seven red: twenty-five of the thirty-eight tests written or
rewritten here, plus the two "attempted once" tests whose expected
exception type moved with the contract. After the providers adopted the
kit's `call_failure`, the same two commands report `33 passed` and
`107 passed`.

The thirteen new tests that were green throughout are green on purpose.
Nine are the pass-through and cancellation guards: a non-SDK exception
and a mid-stream `CancelledError` already escaped unwrapped, and these
pin that the wrapping did not start catching them. The other four are
the client-construction pins in `test_providers_llm.py`, written in the
commit that gave the constructors their arguments.

### The greps the plan asks for

From the repository root:

```
grep -rn "from samtal_server.providers.anthropic_llm import" samtal-server/samtal_server
                                                                  (no matches)
grep -rn "^DEFAULT_TIMEOUT_S" samtal-server/samtal_server
                              providers/kit.py:47:DEFAULT_TIMEOUT_S = 30.0
grep -rn "len(chunk) % 2" samtal-server/samtal_server
                          providers/kit.py:165
grep -rn "^MAX_RETRIES" samtal-server/samtal_server
                        providers/kit.py:60:MAX_RETRIES = 0
```

No module under `providers/` imports another provider implementation:
the only intra-package imports left are `base`, `kit`, `registry` and
`openai_endpoint`, which are shared modules rather than providers. The
four test files that still import `anthropic_llm` import the
`AnthropicLlm` class, which is the module's own subject.

`git diff --stat main -- samtal-server/samtal_server/runtime
samtal-server/samtal_server/device` is empty: milestone 2's territory
was not touched. No event-assertion test file appears in
`git diff --stat main -- samtal-server/tests` either.

### Deviations from the plan

Three, none of them a departure from a decision.

**`MAX_RETRIES` moved as well.** The plan's kit contents name "the SDK
retry policy" without saying where the existing constant lives. It
lived in `openai_endpoint`, which is the module about the OpenAI
dialect, and two of its three readers are not about that dialect at
all. It moved to the kit with its comment; `openai_endpoint` keeps the
three answers that really are dialect questions.

**Two more test pins moved with the contract**, described in their own
section above: the ASR and TTS "api error raises with the reason" pins
had the same shape as the elevenlabs ones the plan lists.

**The taxonomy is raised outside the `except` arm.** The plan says
`raise ... from None`, and that alone is not enough: `from None`
suppresses the SDK exception's *rendering* but still leaves it on the
new error as `__context__`, reachable by anything that walks the chain,
which is exactly what the sentinel tests do. Every site therefore
assigns the taxonomy error in the `except` arm and raises it after the
block, where there is no in-flight exception to become the context.
Each site carries a comment saying so, since the shorter spelling is
the obvious "simplification" for a later reader.

### Discoveries

**`ProviderCallTimeout` is an `OSError`.** `TimeoutError` has been an
`OSError` subclass since Python 3.3, so inheriting it (which the plan
requires, and which is what makes milestone 2's classification one
`isinstance`) makes every wrapped timeout an `OSError` too. `grep -rn
"except .*OSError" samtal_server` finds seven sites: file writes in
`capture.py`, `build_info.py`, `tools/memory.py`, `config/cli.py`,
`config/loader.py` and `db/__init__.py`. None of them wraps a provider
call, so nothing catches a provider timeout by accident today. It is
worth knowing before someone adds an `except OSError` near the
pipeline.

**The OpenAI SDK converts everything the transport raises.** A
non-SDK exception cannot escape a request through the SDK: anything the
transport raises that is not already an `OpenAIError` comes back as
`APIConnectionError`. The pass-through case therefore has to be built
somewhere the SDK is not in the way, and it is built differently per
provider: from the open byte stream for the TTS provider (which the SDK
hands through untouched), and from a client double for the ASR provider
(which reads its whole response inside the SDK's request path, so there
is no "after" at all). For ElevenLabs, which speaks httpx directly, the
handler can simply raise.

**A 401 is not an `APIStatusError` by name.** The SDK raises the
specific subclass, `AuthenticationError`, so the message reads
`... HTTP 401 (AuthenticationError)`. The two tests that assert the SDK
class name assert that one; the tests that construct an
`APIStatusError` by hand still see `APIStatusError`. Either way the
class an operator sees is the SDK's own, which is the point.

**The retry's soft timeout and the taxonomy do not collide.** Because
the wrapping sits at `transcribe` rather than at `_request`, the echo
retry's `except (TimeoutError, APITimeoutError)` still sees the SDK's
exception and still returns the empty transcript with its
`asr_prompt_echo(outcome="timed_out")` event. Its two tests
(`test_the_deadline_is_absolute_rather_than_per_connection_phase` and
`test_a_retry_cut_off_by_the_deadline_discards_rather_than_fails`)
passed unmodified throughout. Worth noting for anyone who later moves
the wrapping inward: `ProviderCallTimeout` *is* a `TimeoutError`, so
that `except` would keep working, which is the plan's fallback and it
was not needed.

### Verification

From `samtal-server/`, on `refactor/provider-kit` with every commit of
this milestone in place:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **1888 passed, 15 skipped** in 177 s.
  Thirty-four more than the 1854 the previous milestone recorded, which
  is exactly the count of tests added here (of the thirty-eight
  written, four are rewrites of existing tests and two elevenlabs pins
  were replaced rather than added).
- `uv run pytest tests/integration -q`: **53 passed** in 154 s.
- `git diff --stat main -- samtal-server/samtal_server/runtime
  samtal-server/samtal_server/device`: empty.

Everything outside pytest ran with `PYTHONDONTWRITEBYTECODE=1`, and no
file was restored mid-run, so the bytecode trap in `AGENTS.md` did not
apply.

### PR #150 review round

One external review of the PR diff (codex CLI, model gpt-5.6-sol,
read-only, 2026-08-15). Verdict: mergeable after the fixes. Six
findings, as received and condensed, each with the commit that
answered it.

1. **P1: the SDK and transport logs bypass the sanitizer.** The
   taxonomy sanitizes what a provider raises, but the openai,
   anthropic, httpx and httpcore libraries log for themselves, and
   `logs.py` propagates whatever level the operator configured. The
   reviewer reproduced a sentinel surviving in an SDK traceback record
   and a response header logged verbatim against the locked openai
   2.48.0. Hold those libraries' records below a safe level, and prove
   it with mock-transport tests through the real SDK client.
   *Resolution*: adopted, `53ae3e8`. `logs.quiet_vendor_libraries`
   holds the four at INFO, or at the server's own level when that is
   higher, so it only ever quietens; `configure` calls it. INFO rather
   than WARNING keeps httpx's one line per request, which carries the
   method, the URL and the status and no header or body. Reproduced
   before fixing: at DEBUG the SDK logs `HTTP Response: ... Headers({'x-echo': '<sentinel>', ...})`
   and an `Encountered an HTTP status error` record with `exc_info`.
2. **P1: ElevenLabs close failures escape raw.** `response.aclose()`
   ran in a `finally` outside the catch, and an exception raised in a
   `finally` replaces the one in flight, so a connection reset while
   closing replaced the sanitized error and could replace a
   cancellation.
   *Resolution*: adopted, `6f41192`. `_released` answers with a
   taxonomy error rather than raising one; the first failure wins, a
   release failure with nothing else wrong is reported as the taxonomy,
   and on the cancellation-or-bug arm the response is still released
   while a failure there is dropped.
3. **P1: the relocated credential resolver prints rejected input.**
   The refusal for an unset `api_key_env` interpolated that field's
   value into a message `main` prints to stderr and the logs keep.
   *Resolution*: adopted, `427a593`. Fixed wording, entry name only.
   Five existing pins moved with it, and a new test plants a
   variable-shaped sentinel and checks the chain and the stderr line.
4. **P2: every provider caught every vendor's SDK errors.** One
   combined tuple would dress a miswired client as an ordinary request
   failure.
   *Resolution*: adopted, `545b760`. Three tuples in the kit
   (`HTTPX_FAILURES` and the two vendor ones built on it), one per
   client a provider can hold, with a wrong-SDK pass-through test in
   each direction.
5. **P2: `client or ...` discards a falsey client double.**
   *Resolution*: adopted, `f8114b9`. `client if client is not None else
   ...`, in all five providers rather than only the two the review
   named, with a test each.
6. **P3: the plan still claimed the SDK exception rides as
   `__cause__`,** contradicting its own adopted finding 1.
   *Resolution*: adopted, `5202066`. The sentence now says the SDK
   class name survives as message metadata and nothing else does.

Each fix commit carries its own red-to-green: the neutered guard makes
four of the seven log tests fail, the old `finally` shape fails three of
the four release tests (the fourth passes either way, because httpx
closes a body read to completion from inside the iterator, where the
catch already saw it), the old wording fails three credential tests, a
combined catch tuple fails both wrong-SDK tests, and `or` fails all five
falsey-client tests.

Two things the round turned up that were not findings:

- **The config model already refuses a pasted credential in
  `api_key_env`.** Its field validator rejects anything that does not
  look like a variable name, which is why finding 3's test has to use a
  variable-shaped sentinel to reach the resolver at all.
- **Pydantic's own `ValidationError` renders the offending input.**
  Hitting that validator with a real credential would print it, which
  is a different surface from the one this issue owns (config
  validation, not provider construction) and is left alone here.

### Verification after the review round

From `samtal-server/`, with all six fix commits in place:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **1906 passed, 15 skipped** in 173 s.
  Eighteen more than before the round: six log tests, four release
  tests, one credential test, two wrong-SDK tests and five
  falsey-client tests.
- `uv run pytest tests/integration -q`: **53 passed** in 150 s.
- `git diff --stat main -- samtal-server/samtal_server/runtime
  samtal-server/samtal_server/device`: still empty. The one file
  touched outside `providers/` is `samtal_server/logs.py`, which
  finding 1 required.
## Milestone 2: the pipeline classifies by type

The reply body no longer reads a bare `RuntimeError` as a device that
went away, because the device edge now makes sure a device that went
away never arrives as one. `is_timeout` is gone, the two sites that stay
broad say what they cover, and four tests hold both sides of the catch.

### What landed

**`samtal-server/samtal_server/device/session.py`.** `_send_text` and
`_send_frame` translate `(WebSocketDisconnect, RuntimeError)` into
`DeviceGone` instead of `WebSocketDisconnect` alone. One `except` arm
for both, since starlette's `WebSocketDisconnect` inherits from
`Exception` rather than from `RuntimeError`. `_send_text`'s docstring
carries the reasoning for the pair, including why this is a translation
rather than a blanket: the `try` covers the socket call and nothing
else, so the only `RuntimeError` it can catch is one the socket raised.

**`samtal-server/samtal_server/runtime/pipeline.py`.**

- `is_timeout` deleted. `_provider_failed` words a failure with
  `isinstance(exc, TimeoutError)`, and its docstring gained the
  paragraph explaining what makes one `isinstance` enough now and what
  the substring match was working around.
- The reply body's `except (DeviceGone, RuntimeError)` became
  `except DeviceGone`, with a comment saying that a bare `RuntimeError`
  arriving there is a bug in this process.
- The `contextlib.suppress(DeviceGone, RuntimeError)` around
  `finish_speaking()` keeps its breadth and gained a comment: the one
  statement inside is a device send, so the `RuntimeError` half can only
  be the transport's.
- The filler playback catch keeps its breadth and gained a comment
  saying accurately what it covers (resampling, encoding and the encoder
  flush as well as the send), that the breadth can therefore still
  swallow a local bug, and that narrowing it is #141's question rather
  than this issue's.

**`samtal-server/samtal_server/device/boundary.py`.** `DeviceGone`'s
docstring no longer claims "every site that must swallow a vanished
device already catches `RuntimeError` broadly". It now says the edge
translates both of the transport's shapes so a runtime catching this
type alone catches every vanished device, that the reply body does
exactly that, and that the subclassing still matters for the two sites
that stayed broad, which are both device sends.

**`samtal-server/tests/unit/test_session_reply_failures.py`** (new, 118
lines). A new module rather than an addition to an existing one:
`test_session_events.py` and `test_session_characterization.py` are both
pinned by this milestone's acceptance criteria, and the other session
suites are each about one subject. Four tests, none of them touching a
provider implementation:

- The taxonomy half, through `test_session_events`'s own `reply_with`
  helper: a TTS raising `ProviderCallError` mid-reply produces a
  `provider_failed` record with `stage == "tts"` and
  `error == "ProviderCallError"`, and the reply ends in the arm that
  reports rather than the one that returns.
- A `ProviderCallTimeout` from the same place is worded as a wait, which
  is the classification change seen from outside.
- The catch half: a bare `RuntimeError` raised while speaking is logged
  under "reply failed" (naming its class, and nothing more, after the
  review round's finding 1).
- Its other side: a `DeviceGone` raised in the same place still says
  nothing.

The two catch-half tests share a helper that replaces `runtime._speak`,
the same seam `run_reply` in `test_session_tools.py` already uses, and
drive a whole reply through `drive_reply` against a socket that accepts
everything. Speaking is where a local bug in a reply actually lives, it
sits inside the reply body's `try`, and it is past every provider, so
nothing reports the failure before the outer catch decides what to make
of it.

**`CHANGELOG.md`.** One entry under `## 2026-08-15` / `### Fixed`. Fixed
rather than Changed because the load-bearing half is a defect: a
provider failure during a reply left no record at all. The commit body
says so.

### Red to green

The catch-half test, run with the new module present and the pipeline
still catching `(DeviceGone, RuntimeError)`, from `samtal-server/`:

```
uv run pytest tests/unit/test_session_reply_failures.py -q
```

```
FAILED test_a_bug_while_speaking_is_reported_rather_than_swallowed
E       assert 'reply failed' in 'INFO ... heard "hello"\nINFO ... poet round 1 took 0.00 s over 1 turns\n'
1 failed, 3 passed in 1.11s
```

The captured log is the evidence rather than the assertion: the reply
ended with `heard` and one LLM round on the record and nothing else, so
the `RuntimeError` really was swallowed as a vanished device. After the
catch narrowed, the same command reports `4 passed`.

The other three are green under both implementations, which is the
plan's finding 4 exactly: a `ProviderCallError` is not a `RuntimeError`
and already missed the broad catch, and a `DeviceGone` is caught either
way. They are worth having anyway, since between them they pin that the
narrowing did not stop reporting provider failures and did not start
reporting disconnects.

### The greps the plan asks for

From `samtal-server/`:

```
grep -rn "is_timeout" samtal_server            (no matches, exit 1)
```

`git diff refactor/provider-kit -- samtal-server/tests/unit/test_session_events.py
samtal-server/tests/unit/test_session_characterization.py` is empty: the
event assertions and the characterization pins pass unmodified, and the
whole milestone's diff is five files (the two production modules, the
boundary docstring, the new test module, and the changelog).

### Deviations from the plan

None. Every decision the plan makes for this milestone landed as
written, including the two-step order finding 2 asks for: the edge
translation is its own commit and its own green test run before the
catch narrows in the next one.

### Discoveries

**The close path had already made this decision.** `_close` suppresses
`RuntimeError` around `websocket.close()`, and has since before this
issue: a socket that raises on close was already being read as a device
that is gone. The translation in the two send helpers makes the send
path agree with the close path rather than introducing a new reading.

**Nothing latent surfaced.** The plan's third risk was that narrowing
would expose some device-edge path raising bare `RuntimeError` where it
should raise `DeviceGone`, which would now log "reply failed". Both
lanes are green and no new "reply failed" appears
anywhere in them, so there is no such site in what the suites reach. The
milestone-1 discovery about `ProviderCallTimeout` being an `OSError`
stayed harmless for the same reason: none of the seven `except OSError`
sites is anywhere near a reply.

**One characterization docstring is now half historical, deliberately
left alone.** `test_a_reply_ends_quietly_when_the_send_path_raises`
explains its `RuntimeError` parameter as covering "an encoder or a
resampler failing while speaking" as well as the socket, and that half
is exactly what stopped being covered. Its assertions still hold, and
hold for the right reason, because the failure in the test comes from
the socket; only the sentence about the other case is stale. The file is
pinned by this milestone's acceptance criteria and was not touched.
Whoever revisits the characterization suite for #85 should correct the
sentence then.

### Verification

From `samtal-server/`, on `refactor/pipeline-provider-errors` with every
commit of this milestone in place:

- `uv run ruff check .`: **All checks passed!**
- `uv run pytest tests/unit -q`: **1892 passed, 15 skipped** in 175 s.
  Four more than milestone 1's 1888, which is the four tests added here.
- `uv run pytest tests/integration -q`: **53 passed** in 154 s.
- `git diff refactor/provider-kit` over the two pinned test files:
  empty.

Nothing was restored mid-run, so the bytecode trap in `AGENTS.md` did
not apply; the red run and the green run of the catch-half test are both
pytest, which writes no bytecode.
