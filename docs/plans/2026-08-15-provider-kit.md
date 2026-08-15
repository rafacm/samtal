# Give providers a shared kit and a request-time error taxonomy

## Goal

Implement issue #137: concrete providers re-implement shared plumbing
(credential resolution imported from `anthropic_llm` by three other
modules, the PCM byte-alignment loop duplicated verbatim with its
docstring, `DEFAULT_TIMEOUT_S` declared three times) and raise
unclassifiable errors at request time, so the pipeline classifies
timeouts by class-name substring (`is_timeout`,
runtime/pipeline.py:125) and the reply body's broad
`except (DeviceGone, RuntimeError)` (pipeline.py:683) can mistake an
ElevenLabs HTTP failure raised as bare `RuntimeError` for a vanished
device. Give the plumbing one home, give request failures a taxonomy
the pipeline classifies by type, and route provider failures to the
provider-failed path, never the vanished-device path.

The companion implementation doc,
[`2026-08-15-provider-kit-implementation.md`](2026-08-15-provider-kit-implementation.md),
records what each milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated

Settled by issue #137 and not re-litigated here:

1. **A shared provider-kit module owns credential resolution,
   timeout defaults, retry policy, and the byte-alignment stream
   helper; providers consume it.**
2. **A request-time exception taxonomy** (a provider-call error
   carrying at least a timeout distinction) raised by every provider
   on request failure; the pipeline classifies by type and
   `is_timeout` substring matching is deleted.
3. **The broad reply-body catch narrows** so provider failures take
   the provider-failed path (with its structured event), never the
   vanished-device path.
4. **Both LLM providers gain the injectable `client=` seam** the
   other three cloud providers already have.
5. **Provider protocols in `providers/base.py` do not widen**; this
   touches implementations and the error contract only.

Evidence re-verified at main@08e07c6: `resolve_api_key` at
anthropic_llm.py:150-175, imported by `elevenlabs_tts.py:22`,
`openai_endpoint.py:21`, and `openai_llm.py:17` (which also imports
`DEFAULT_MAX_TOKENS`); the alignment loop duplicated between
openai_tts.py:117-136 and elevenlabs_tts.py:148-163;
`DEFAULT_TIMEOUT_S = 30.0` in elevenlabs_tts.py:44,
openai_asr.py:74, and openai_tts.py:55; `AnthropicLlm.__init__`
(anthropic_llm.py:97-98) and `OpenAiCompatibleLlm.__init__`
(openai_llm.py:114-115) build SDK clients with no timeout, the
SDK's default retries, and no `client=` parameter, and
`test_providers_llm_tools.py:176,362` assigns over `_client`;
`is_timeout` at pipeline.py:125-134; the reply-body catch at
pipeline.py:683 and the elevenlabs bare `RuntimeError` at
elevenlabs_tts.py:168-176.

## Decisions this plan makes

### The kit is `providers/kit.py`; the taxonomy lives in `providers/base.py`

Two homes because they are two different things. The taxonomy is
contract: the pipeline imports it to classify, providers raise it,
and `base.py` is where the provider contract (`ProviderError`, the
protocols) already lives; adding exception classes widens no
protocol, which decision 5 permits. The kit is implementation-side
plumbing only providers consume: `resolve_api_key` (moved verbatim
from `anthropic_llm`, with `API_KEY_SLOT` and its
`stored_provider_secret` read), `DEFAULT_TIMEOUT_S = 30.0` declared
once, the SDK retry policy (retries disabled so `timeout_s` stays
the bound the operator set, the reason openai_asr.py already
documents), and the byte-alignment helper. `anthropic_llm`,
`elevenlabs_tts`, `openai_llm`, `openai_asr`, `openai_tts`, and
`openai_endpoint` import from the kit; no provider imports another
provider afterward, which the implementation doc proves by grep.

### The taxonomy is two classes, and the timeout one is also a `TimeoutError`

```python
class ProviderCallError(Exception):
    """A provider's request failed after the provider was built."""

class ProviderCallTimeout(ProviderCallError, TimeoutError):
    """The failure was a wait rather than an answer."""
```

Deliberately not `RuntimeError` subclasses: not matching the
device-edge catches is the whole point of decision 3. The timeout
class inherits `TimeoutError` as well, so the pipeline's
classification becomes plain `isinstance(exc, TimeoutError)`: it
covers `asyncio.TimeoutError`, the watchdog's own
`FirstTokenTimeout` (already a `TimeoutError` subclass), and every
wrapped SDK timeout, with no substring anywhere.

The message carries trusted metadata only (the review round's
finding 1): the SDK exception's class name, and the HTTP status
code when there is one, never the vendor's message text. An SDK
exception's string can embed the response body, and elevenlabs'
current error deliberately does; a compatible endpoint can echo
request content or credentials there, `_provider_failed` renders
`str(exc)` into the log line, and the observability ADR makes that
log the retained surface. For the same reason the taxonomy error
is raised `from None`: the SDK exception must not ride into
`logger.exception`'s rendered chain either. What an operator loses
(the vendor's prose) they recover by re-running the request by
hand; what the logs keep (taxonomy class, SDK class, status code,
stage, entry, host, elapsed) is the diagnosable part. Sentinel
tests prove it: a secret planted in the SDK exception's message,
in a response body, and in a cause is absent from the raised
chain, from `caplog.text`, and from every log record's fields.

The `provider_failed` event's `error` field, which reports
`type(exc).__name__`, now reads
`ProviderCallTimeout`/`ProviderCallError` for wrapped failures.

### Who raises it: the five request-making providers

`AnthropicLlm`, `OpenAiCompatibleLlm`, `OpenAiAsr`, `OpenAiTts`,
and `ElevenLabsTts` wrap request-time failures: each catches its
own SDK's exceptions at its request sites (including failures
raised mid-stream by the response iterator) and raises
`ProviderCallTimeout` for the SDK's timeout classes
(`anthropic.APITimeoutError`, `openai.APITimeoutError`,
`httpx.TimeoutException`, `asyncio.TimeoutError` where the provider
runs its own `asyncio.timeout`, as openai_asr does) and
`ProviderCallError` for the rest of the SDK's request-failure
surface (`anthropic.APIError`, `openai.APIError`,
`httpx.HTTPError`), from the original. `_api_error` in
elevenlabs_tts returns `ProviderCallError` instead of
`RuntimeError`. Exceptions outside the SDK families
(`CancelledError` above all, and genuine bugs) pass through
untouched: the taxonomy claims request failures, not all failures.

For openai_asr the taxonomy applies to the failure of `transcribe`
as a whole, not to every internal request (the review round's
finding 3): the prompt-echo retry deliberately converts its own
retry's timeout into an empty transcript and an
`asr_prompt_echo(outcome="timed_out")` event, and that
timeout-as-discard policy is behavior the existing tests pin. The
wrapping therefore sits where `transcribe`'s failure leaves the
provider, and the retry's internal policy keeps eating what it
already eats (catching the taxonomy timeout there if the internal
call sites are what get wrapped). Two tests split the cases: an
initial request timeout surfaces `ProviderCallTimeout`; a
retry-phase timeout still yields the empty transcript and the
echo event, never a raised taxonomy error.

The local engines (`SileroVad`, `FasterWhisperAsr`, `PiperTts`) and
the mocks stay outside the taxonomy: they make no requests, their
failures are bugs in this process rather than answers a network did
not deliver, and dressing a bug as a provider-call error would hide
it from `logger.exception`. The issue's per-provider test criterion
names the `client=` seam, which only the request-making five have,
so this scoping reads the criterion as written.

### Both LLM providers get `timeout_s`, retries off, and `client=`, with no new config surface

`AnthropicLlm.__init__` and `OpenAiCompatibleLlm.__init__` gain
`timeout_s: float = DEFAULT_TIMEOUT_S` and
`client: ... | None = None` parameters, mirroring the shape
openai_asr.py:148-182 already has: the default client is built with
the kit's timeout and retries-off policy, and a passed client is
used as given. The `build` functions do NOT grow a `timeout_s`
option: exposing a new configuration key is a schema change with
its own documentation ripple (config.example.yaml, the generated
reference), and the issue's evidence is "no timeout", not "no
timeout knob". A deployment that needs a nonstandard LLM timeout is
a follow-up with its own issue; this plan bounds every LLM request
at the kit default where today it is unbounded.

`test_providers_llm_tools.py`'s two `_client` assignments migrate
to the constructor seam, which is an interface-strengthening test
edit, not a weakening: the same fakes arrive through the front
door.

### The alignment helper takes the iterator and the label

```python
async def aligned_pcm(label: str, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
```

It carries the odd byte exactly as the two duplicated loops do,
logs the dropped trailing byte with the caller's label, and keeps
the existing docstring's reasoning (HTTP chunk boundaries fall
wherever the network puts them). Both TTS providers wrap their
byte iterators with it; the behavior is pinned by the existing
sample-alignment tests, which pass unmodified.

### The pipeline classifies by type, and the reply body stops eating provider failures

`is_timeout` is deleted; `_provider_failed`'s wording check becomes
`isinstance(exc, TimeoutError)`. The events behave identically:
`error` still carries `type(exc).__name__`, the fields do not
change, and the existing event assertions (test_session_events and
friends drive failures through fakes whose exceptions reach the
pipeline unwrapped) pass unmodified.

Narrowing the reply-body catch is a two-step change, because the
device edge does not yet earn it (the review round's finding 2):
`_send_text` and `_send_frame` in `device/session.py` translate
only `WebSocketDisconnect` into `DeviceGone`, while Starlette's
post-close send raises a bare `RuntimeError` that today reaches
the reply body untranslated, and
`test_session_characterization.py` pins both shapes as quiet
disconnects. So milestone 2 first finishes the edge's own promise:
the two send helpers translate the socket's post-close
`RuntimeError` into `DeviceGone` alongside `WebSocketDisconnect`,
which keeps the characterization test's observable behavior (a
disconnect stays quiet) while changing only which type carries it.
Only then does the reply-body catch at pipeline.py:683 narrow from
`(DeviceGone, RuntimeError)` to `DeviceGone`. After both, a bare
`RuntimeError` in the reply body can only be a local bug, lands in
the existing `except Exception` arm, and is logged as "reply
failed" instead of being silently swallowed as a vanished device.

Two broad sites keep their breadth, described accurately: the
`contextlib.suppress(DeviceGone, RuntimeError)` around
`finish_speaking()` (pipeline.py:714) wraps only a device send;
the filler playback catch (pipeline.py:1446) wraps resampling,
encoding, and the send together, so its breadth can still swallow
a local bug, and narrowing it is deliberately out of this issue's
scope (the filler path is #141's territory). Both get a comment
saying what they cover. `DeviceGone`'s docstring is updated where
it cites "every site": the reply body no longer catches
`RuntimeError` broadly, and the docstring should not claim it
does.

### Tests

Per provider, request-failure coverage through the constructor
seam, no `_client` assignment and no monkeypatching internals: a
fake client whose request raises the SDK's timeout class must
surface `ProviderCallTimeout`, and one raising the SDK's error
class must surface `ProviderCallError` with the SDK class named in
the message.

The streaming adapters are additionally covered after the first
chunk (the review round's finding 5), because every one of them
has failure points past the request: Anthropic's event iteration
and its final-message assembly, the OpenAI LLM's iteration, and
both TTS byte iterators. For each streaming provider the fake
client yields at least one chunk and then raises, once with the
SDK's timeout class and once with its error class, surfacing the
matching taxonomy type; a raw `httpx` transport error raised from
an SDK-backed stream after the response has opened is covered for
the providers whose SDKs ride httpx; Anthropic's final-message
assembly failure is its own case; and a mid-stream
`asyncio.CancelledError` propagates unwrapped with no
provider-failure report, which is the barge-in path's guarantee. The elevenlabs failure tests that pin `RuntimeError`
(test_providers_elevenlabs.py:225,233) move to the taxonomy type
with their message assertions kept; that is the error-contract
change the issue orders, not a weakening.

The reply path gets two tests, because one cannot prove the catch
changed (the review round's finding 4: a taxonomy error already
misses the `RuntimeError` arm before milestone 2, and an old-shaped
TTS `RuntimeError` already produces `provider_failed` through
`_Synthesis`'s report before the outer catch sees anything, so a
single event assertion is green under both implementations):

- The taxonomy half: a session whose TTS raises
  `ProviderCallError` mid-reply produces a `provider_failed` event,
  asserted through the event stream the way test_session_events
  already asserts provider failures.
- The catch half, the one that is red before milestone 2 and green
  after: a bare non-device `RuntimeError` raised inside the reply
  body reaches the generic failure arm (the "reply failed" log
  record is present) instead of the silent vanished-device return,
  while a `DeviceGone` in the same place stays quiet.

Red-to-green for milestone 2 is the catch-half test, recorded in
the implementation doc.

### Two milestones, two PRs, second stacked on the first

- Milestone 1 touches `providers/` only: the kit, the taxonomy
  classes in base.py, the five providers adopting both, the seam
  migration in the LLM tests, and the per-provider failure tests.
  `main` stays releasable at its merge: the pipeline's substring
  check classifies `ProviderCallTimeout` correctly by its very
  name, and provider failures already stop matching the
  `RuntimeError` catch, so behavior only improves.
- Milestone 2 touches the pipeline's classification and catch
  sites: `is_timeout` deleted, `_provider_failed` on
  `isinstance(exc, TimeoutError)`, the reply-body catch narrowed,
  the `DeviceGone` docstring updated, and the reply-path test.

The split keeps the provider-facing diff and the pipeline-facing
diff separately reviewable, and the issue's own coordination note
(never concurrent with the pipeline extraction) applies to
milestone 2's files.

## Files touched

```
samtal-server/samtal_server/providers/kit.py       new: resolve_api_key, DEFAULT_TIMEOUT_S, retry policy, aligned_pcm
samtal-server/samtal_server/providers/base.py      ProviderCallError, ProviderCallTimeout (no protocol widens)
samtal-server/samtal_server/providers/anthropic_llm.py   consumes kit; timeout, retries off, client=; raises taxonomy
samtal-server/samtal_server/providers/openai_llm.py      same
samtal-server/samtal_server/providers/openai_asr.py      consumes kit; raises taxonomy
samtal-server/samtal_server/providers/openai_tts.py      consumes kit; raises taxonomy; aligned_pcm
samtal-server/samtal_server/providers/elevenlabs_tts.py  consumes kit; raises taxonomy; aligned_pcm
samtal-server/samtal_server/providers/openai_endpoint.py imports kit, not anthropic_llm
samtal-server/samtal_server/runtime/pipeline.py    milestone 2: is_timeout gone, catch narrowed
samtal-server/samtal_server/device/boundary.py     milestone 2: DeviceGone docstring claim corrected
samtal-server/tests/unit/test_providers_llm_tools.py   _client assignments become the seam
samtal-server/tests/unit/test_providers_elevenlabs.py  RuntimeError pins become the taxonomy
samtal-server/tests/unit/... (per-provider failure tests, new; reply-path test, milestone 2)
CHANGELOG.md                                       one entry per milestone under 2026-08-15
docs/plans/2026-08-15-provider-kit.md
docs/plans/2026-08-15-provider-kit-implementation.md
```

`config.example.yaml` is untouched by decision (no new
configuration surface). `providers/base.py`'s protocols are
untouched; only module-level exception classes are added.

## Verification

- `uv run ruff check .`, `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q`, from `samtal-server/`, per
  milestone.
- Milestone 1: `grep -rn "from samtal_server.providers.anthropic_llm import"`
  finds no cross-provider import; `DEFAULT_TIMEOUT_S` defined once;
  the alignment loop exists once; the per-provider failure tests
  red against the pre-kit providers and green after.
- Milestone 2: `grep -n "is_timeout" samtal_server` finds nothing;
  the reply-path test red against the broad catch (with the old
  RuntimeError shape) and green after; existing event assertions
  pass unmodified (`git diff --stat` over the event test files is
  empty).

## Risks and mitigations

- **Wrapping catches too much.** A careless `except Exception`
  around a request site would dress cancellation or a genuine bug
  as a provider-call error, and the pipeline treats those
  differently (`CancelledError` must propagate for barge-in).
  Mitigation: each provider catches only its SDK's exception
  families, `CancelledError` is explicitly outside them
  (`asyncio.CancelledError` is not an `Exception` subclass in
  modern Python, and the plan states it anyway), and the
  per-provider tests include a pass-through case asserting a
  non-SDK exception surfaces unwrapped.
- **The event `error` field changes for real deployments.** Wrapped
  SDK failures now report the taxonomy class where they reported
  `APITimeoutError` before. This is the issue's stated intent
  (classify by type), the SDK class stays in the message and
  `__cause__`, and no committed assertion pins the old names for
  wrapped paths; the CHANGELOG entry says it out loud for anyone
  querying retained logs.
- **Narrowing the catch surfaces latent bare RuntimeErrors.** If
  some device-edge path raises bare `RuntimeError` where the edge
  should raise `DeviceGone`, it now logs "reply failed" with a
  traceback instead of returning silently. That is detection, not
  breakage; the integration lane and the existing device tests
  would show it before merge, and the implementation doc records
  any such site found.
- **The stacked second milestone races other pipeline work.** The
  runbook forbids running this issue concurrently with #141;
  milestone 2 is the reason. Mitigation: sequencing, not code.

## Plan review round

One external review of the plan as first committed (ed55317): codex
CLI, model gpt-5.6-sol, read-only against this repository with the
issue #137 body supplied, 2026-08-15. Verdict: ready after the
P1/P2 amendments. Findings as received, condensed; each carries its
resolution once the amendment addressing it lands.

1. **P1: provider error messages would violate the no-leak
   contract.** The plan preserves SDK messages and causes;
   `_provider_failed` renders `str(exc)` and the reply body logs
   the full chain via `logger.exception`; OpenAI SDK exceptions
   carry response-body text and elevenlabs deliberately includes
   arbitrary response bodies, and a compatible endpoint can echo
   credentials or request content there. Taxonomy messages and
   logs must carry only trusted metadata (taxonomy class, SDK
   class, sanitized status code), no vendor message text and no
   unsafe cause chain; add sentinel tests proving secrets in SDK
   messages, response bodies, and causes are absent from the
   exception chain, `caplog.text`, and every log record.
   *Resolution*: adopted. The taxonomy decision now specifies
   metadata-only messages (SDK class name, status code when known,
   never vendor text), `raise ... from None` so the SDK exception
   stays out of rendered chains, the diagnosability tradeoff
   stated, and the sentinel tests named; the elevenlabs message
   consequently loses its response-body detail, which its updated
   tests reflect.
2. **P1: narrowing the reply catch breaks characterized
   device-disconnect behavior.** `_send_text`/`_send_frame`
   (device/session.py:~907) translate only `WebSocketDisconnect`
   to `DeviceGone`; Starlette's post-close `RuntimeError` passes
   untranslated, and `test_session_characterization.py:482` pins
   both as quiet disconnects. Milestone 2 as written fails that
   test and turns a normal disconnect into "reply failed". First
   translate socket-originated `RuntimeError` to `DeviceGone`
   inside the two send helpers, add `device/session.py` to
   milestone 2, preserve the characterization test, then narrow
   the reply-body catch.
   *Resolution*: adopted. The catch decision is now a two-step
   change: the send helpers translate the socket's post-close
   `RuntimeError` into `DeviceGone` first (keeping the
   characterization test's quiet-disconnect behavior), and only
   then does the reply-body catch narrow; `device/session.py`
   joins milestone 2's files.
3. **P2: blanket ASR wrapping conflicts with the prompt-echo
   retry's soft-timeout contract.** openai_asr deliberately
   converts retry failures into an empty transcript and
   `asr_prompt_echo(outcome="timed_out")`, pinned by tests.
   Taxonomy applies to failure of `transcribe` as a whole; the
   echo retry's timeout-as-discard behavior is preserved, with
   separate tests for an initial request timeout and a retry
   timeout.
   *Resolution*: adopted. The provider-rules decision now scopes
   the ASR taxonomy to `transcribe` as a whole, preserves the
   retry's discard policy explicitly, and names the two split
   tests.
4. **P2: the proposed reply-path test cannot prove the catch was
   narrowed.** A `ProviderCallError` already reaches the generic
   arm before milestone 2, and an old-shaped TTS `RuntimeError`
   already emits `provider_failed` via `_Synthesis` before the
   outer catch, so the single test is green under both
   implementations. Use two tests: taxonomy TTS failure emits
   `provider_failed`; a bare non-device `RuntimeError` reaches the
   generic failure handler while `DeviceGone` stays quiet, and
   only the second demonstrates the catch change.
   *Resolution*: adopted. The reply-path test design is now the
   two named tests, with the catch half carrying milestone 2's
   red-to-green.
5. **P2: the tests do not exercise the mid-stream failures the
   plan claims to handle.** All four streaming adapters have
   failure points after the first chunk (Anthropic iteration and
   final-message assembly, OpenAI LLM iteration, both TTS byte
   iterators), and raw httpx errors can escape SDK iterators after
   the response opens. For every streaming provider, test a
   timeout and a non-timeout raised after at least one yielded
   chunk, cover raw httpx failures from SDK-backed streams and
   Anthropic final-message failure, and prove mid-stream
   cancellation propagates unwrapped.
   *Resolution*: adopted. The tests decision gains the mid-stream
   paragraph naming all of these cases per streaming provider.
6. **P2: `DEFAULT_MAX_TOKENS` has no destination.** openai_llm
   imports it from anthropic_llm, and the plan promises no
   cross-provider imports without assigning the constant a home.
   Move it into the kit (or deliberately localize it) and verify
   both symbols.
7. **P2: nothing tests that the LLM clients actually receive the
   timeout and retry policy.** Injected-client tests pass even if
   the production constructors omit both arguments; existing ASR
   and TTS tests pin these properties. Add one default-client
   construction test per LLM checking timeout and retry settings,
   plus a test that an injected client is used unchanged.
8. **P3: the plan overstates SDK timeouts as whole-request
   bounds.** The SDK/httpx timeout is per phase, not an
   end-to-end deadline; a streaming response may run longer while
   delivering. Say the kit supplies a per-operation transport
   timeout with retries disabled, not a wall-clock deadline.
9. **P3: the filler catch is not device-send-only as asserted.**
   Its `try` also covers resampling, encoding, and encoder
   flushing. Describe the breadth accurately and preserve it
   explicitly as out of scope.

## Milestones

- [ ] **Kit, taxonomy, and the five providers** (PR TBD):
  `providers/kit.py` lands with `resolve_api_key`,
  `DEFAULT_TIMEOUT_S`, the retries-off policy and `aligned_pcm`;
  `ProviderCallError`/`ProviderCallTimeout` land in base.py; the
  five request-making providers consume the kit, gain the missing
  `timeout_s`/`client=` seams (LLMs), and raise the taxonomy at
  request time; cross-provider imports are gone; the `_client`
  assignments in test_providers_llm_tools.py move to the seam; the
  elevenlabs RuntimeError pins move to the taxonomy; per-provider
  failure tests (timeout, error, and pass-through) run through the
  seam; CHANGELOG entry; implementation-doc section in the change
  that ticks this box. Accept: lint and both lanes green; the
  greps above; red-to-green recorded.
- [ ] **Pipeline classifies by type and stops eating provider
  failures** (PR TBD): `is_timeout` deleted and `_provider_failed`
  classifies with `isinstance(exc, TimeoutError)`; the reply-body
  catch narrows to `DeviceGone` with the two device-edge sites
  commented and `DeviceGone`'s docstring corrected; the reply-path
  test proves a TTS `ProviderCallError` produces `provider_failed`
  and no silent swallow; CHANGELOG entry; implementation-doc
  section in the change that ticks this box. Accept: lint and both
  lanes green; no `is_timeout` anywhere; existing event assertions
  untouched by diff.
