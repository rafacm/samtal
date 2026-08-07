# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
using dates (`## YYYY-MM-DD`) as section headers instead of version numbers.

## 2026-08-07

### Added

- A survey of the neighbouring projects, `docs/related-projects.md`, in
  two registers. Alternatives, the projects someone could choose instead
  of samtal, each answering the same four questions: what it is, where it
  overlaps, where samtal is deliberately different, and what samtal
  borrows. Rhasspy shares the premise (thin listening endpoints, a server
  you run, nothing leaving the house) and not the goal, matching a
  template grammar and emitting an intent where samtal puts a language
  model. Its successor line matters more than the archived 2.5 most links
  point at, because Wyoming and Home Assistant's Assist pipeline reach
  nearly samtal's picture from the other direction, differing in assuming
  a hub and in owning the wire protocol where samtal implements one it
  does not control. ElatoAI is the closest match to samtal's physical
  shape, same chip family and codec and transport, and the strongest
  argument for the road not taken: one vendor's realtime
  speech-to-speech API and a hosted account, against a staged pipeline
  that can run with no account at all. Hermes Agent is in the document
  because "voice" means voice messaging there, no wake word and no
  hardware, which makes it a candidate brain a layer above samtal rather
  than a rival beside it. The second register is what samtal is built
  from, saying what each dependency is and why samtal touches it, with
  the terms left in `THIRD_PARTY_LICENSES.md`: the upstream pair, the
  pipeline components, the `xiaozhi-sdk` device simulator the tests run
  on, and the wake word models on the device. Writing it turned up that
  samtal depends on the Rhasspy organisation twice over, for Piper voices
  and for `pysilero-vad`, while running none of Rhasspy. A closing list
  names the projects not yet read, so they are not rediscovered from
  scratch, and makes no claims about them.
- The research notes record what running stock firmware costs the
  server, as the list to work from when the device side is tackled.
  samtal-server implements the server half of the xiaozhi protocol and
  changes nothing about it, which is the right trade for v1 and is paid
  for in machinery that exists only because the device cannot be asked
  to behave differently: barge-in reaching only realtime mode because
  the device owns the listening mode, `idle_timeout_s` existing because
  nothing in the firmware closes a realtime channel, and the whole
  barge-in gate stack existing because echo cancellation quality is
  invisible from here. One entry turned out to be an unclaimed
  capability rather than a constraint, and has its own section: the
  firmware update channel is fully built on the device, with A/B
  partitions already in the layout, a boot path and a `self.upgrade_firmware`
  MCP path (kept out of the model's tool list), and rollback already
  enabled, so what is missing before anything ships is signing rather
  than plumbing. The v1 plan's device-side line now points at it.

## 2026-08-06

### Added

- Two events that make a provider's behaviour visible, which is one
  gap seen from two sides. `provider_failed` fires where an ASR, LLM
  or TTS call fails, carrying `stage`, `provider`, `type`, `host`,
  `error` and `duration_ms` alongside the `session` and `device` every
  conversation event has. A failing provider was previously a
  traceback under "reply failed" with no `event` to filter on, no
  session to group by, and no host, which is the field an egress
  allowlist is diagnosed from: the reported symptom was a pod that
  boots healthy, answers, and is silent every reply until the
  synthesis timeout expires, with nothing in the logs naming a network
  policy. A timeout is worded as one, because where traffic is dropped
  rather than refused the whole symptom is the wait, and the wait sits
  at the provider's `timeout_s`, which is itself the diagnosis. The
  human sentence and the traceback are unchanged.

  `llm_round` fires per generation call with `duration_ms`,
  `first_token_ms` (of the first spoken token, so a round that only
  asked for a tool carries none), `turns`, `round`, the same fields, and
  `prompt_tokens`/`completion_tokens` where the provider reports them.
  Stage latency was otherwise inferred from the gaps between events,
  and the gap between `heard` and `speaking_started` holds the LLM and
  the TTS time to first byte with nothing separating them. A field
  session lost 19.04 s inside that gap against a session median of
  1.18 s and the logs could not say whether the payload or the vendor
  was responsible; they now can. `round` counts the whole reply rather
  than one agent's leg, so the generation after a handover is a round
  of its own rather than another first round. Token counts are asked
  for only where the endpoint is OpenAI itself, whose dialect needs
  `stream_options`, and read wherever a server volunteers them; the
  Anthropic API reports them unasked. Their absence is a fact about
  the endpoint rather than an error.

- A second published image variant, `slim`, for deployments whose ASR
  and TTS name external providers. It installs no optional extra, so it
  carries neither local engine and no GPL component at all, and it is
  494 MB against the default's 883 MB, a saving of 389 MB. Most of that
  is not piper but `faster-whisper`, which brings its own inference
  stack rather than reusing the onnxruntime `pysilero-vad` already
  pulls in, so the reduction is far larger than the size of the engines
  themselves. Tags follow the Docker convention where the unsuffixed
  name is the batteries-included image: the default variant keeps
  `latest`, the dated tag and `sha-<short>` exactly as before, and slim
  takes `slim`, `<date>-slim` and `sha-<short>-slim`. Nothing changes
  for an existing puller of `latest`. Both are built from one
  Dockerfile, selected with `--build-arg SAMTAL_VARIANT=slim`, so they
  cannot drift, and an unrecognised variant fails the build rather than
  silently producing the smaller image. `silero` VAD is in both, being
  a core dependency rather than an extra. A slim image given a config
  that names `faster_whisper` or `piper` refuses to start and names the
  extra it lacks, which is checked in CI along with the absence of the
  packages themselves; both variants run the same whole-conversation
  smoke test, which is what makes them provably the same server.

- New `server.capture` section: recording a session to disk so a
  real-world one can be analysed offline. Off by default and off until
  `enabled` says otherwise, because this writes room audio to disk and
  that is the opposite of what the rest of the project promises; a
  warning at startup and one line per recorded session say when it is
  on, and a section that is present but off says so once, since a
  configured capture that records nothing is otherwise a silence to
  debug. The flag is the switch rather than the presence of the
  section, so turning capture off after a recording does not mean
  deleting the directory and the budgets with it; `dir` stays required
  even while disabled, so switching on is one word. It exists because acoustic problems
  cannot be reproduced in any test lane: both lanes bypass the
  microphone, the board's echo cancellation, and the room, so how much
  of the assistant's own voice reaches the endpointer is unknown and a
  barge-in fix would be tuned against a guess. Three files per session
  on one timeline: a stereo 16 kHz WAV with the microphone on channel
  0 and what was paced out to the speaker on channel 1, so one sample
  index is one instant in both and echo leakage becomes a measurement;
  a JSONL decision track carrying every structured event with a `t_ms`
  offset into the audio, plus frames dropped before decode aggregated
  per second with their reason and the endpointer's `speech_ms`
  sampled every frame rather than only where it decided something; and
  a JSON manifest recording the server revision, the firmware the
  device reported at OTA check-in, the resolved provider entries
  verbatim, and the barge-in thresholds, because a capture outlives the
  code that made it. The microphone is recorded before the session's
  own guards, so the frames a configuration discards are in the file
  anyway, those being the ones that explain a misfire. Options:
  `enabled` (default false), `dir` (required),
  `max_session_s` (default 900), `max_total_mb` (default 2000, whole
  captures pruned oldest first) and `min_free_mb` (default 1000, below
  which a capture declines to start and says so, since agent memory
  and the model caches share the volume). A capture cut off by a
  restart stays readable: both files are flushed as they are written,
  and the manifest says whether the WAV header was ever patched.

- The server can say which build it is running. `__version__` has read
  `0.1.0` since the package skeleton and answers a different question,
  so a separate `revision` now rides `/healthz`, every `session_open`
  log event, and the OTA reply under a new `server` key. It is resolved
  once at startup: `SAMTAL_REVISION` when set, else `git describe
  --always --dirty` when there is a checkout to describe, else
  `unknown`, which a build with neither reports rather than failing to
  start. The image gained a `SAMTAL_REVISION` build argument that
  becomes an environment variable, since a process cannot read its own
  image's OCI labels, and CI passes the commit its `sha-` tag is
  computed from, so a running container and its image tag agree. The
  `session_open` field is the widest of these: the JSON logs already
  ship to a collector, so every session becomes attributable to a
  build, which is what makes two field recordings of different
  behaviour tellable apart from one code change and two different
  rooms.

- New `server.limits.idle_timeout_s` (default 120): how long a realtime
  session may go without conversing before the server closes it,
  counted from the end of the last utterance or the end of the last
  reply, whichever is later. A realtime device asks to listen once and
  then streams its mic for the rest of the connection, and nothing in
  the firmware ever closes that channel, so until now walking away
  mid-conversation left the mic running until `max_session_s`, an hour
  in a typical deployment: room audio reaching the server, one of
  `max_sessions` held, Opus decode and VAD running over the silence,
  and the board unable to reach the sleep mode `CanEnterSleepMode`
  refuses while an audio channel is open. Arriving audio deliberately
  does not reset the clock, since a realtime session streams silence
  too; a reply still speaking does, so a timer coming due mid-reply
  cannot leave the user without a window to answer. Realtime only: an
  auto-mode device stops listening after each reply and re-arms per
  turn, and `max_session_s` remains its bound. The close is a 1000
  normal closure, which the firmware reads as the end of a conversation
  and answers by reconnecting on the next wake word, and it is logged
  as its own `session_idle` event so an abandoned conversation can be
  told from one that ran out of its hour.

- New `openai` ASR provider type, transcribing an utterance through
  the OpenAI transcription API. Like the `openai` TTS type it needs no
  optional extra and adds no dependency, so one key now serves all
  three network stages. Options: `api_key_env` (required for OpenAI
  itself), `model` (default `gpt-4o-mini-transcribe`), `base_url`
  (default `https://api.openai.com/v1`), `prompt`, `language`,
  `temperature` and `timeout_s`. The stage's PCM goes up as WAV, whose
  header carries whatever rate the pipeline is running at, so nothing
  is re-encoded and no rate is pinned. `base_url` reaches any server
  implementing `/v1/audio/transcriptions`: a keyless self-hosted one
  may leave `api_key_env` out, while a gateway or hosted endpoint that
  authenticates still names its variable there, since only the
  *requirement* for a key is specific to OpenAI's own host. The
  endpoint rather than the type decides egress, so an entry under
  `server.local_only` declares its own. Retries are off, so `timeout_s`
  bounds a turn. Audio under OpenAI's 0.1 s minimum is answered empty
  without a round trip, which is the barge-in path: a snippet of tens
  of milliseconds is transcribed to decide whether an interruption was
  real, and the API's refusal would be logged as a failure rather than
  the non-answer it is. That floor was measured against OpenAI, so it
  applies only there; a compatible endpoint sets its own accepted
  length, as it already does for the model rules and the temperature
  range.
  Unlike the TTS types, this one is usually the faster choice as well
  as the more accurate: measured against local `faster_whisper` small
  on an int8 CPU, 536 to 658 ms per utterance against 1688 to 1781 ms,
  and it still transcribed Swedish exactly under white noise at 0 dB
  where the local model returned an unrelated English sentence. It
  reports no language and asks for no session language lock, because
  the API returns neither a usable language nor a confidence; nothing
  is lost, since the detection pass those exist to skip is free here.
  It does not stream, which the module documents as a decision rather
  than an omission: the stage takes a whole utterance and the LLM
  cannot start on half a sentence.

### Changed

- `tts start` now means "audio is about to play" and nothing else. It
  went out as soon as transcription finished, before the LLM ran, so a
  device entered its speaking state and displayed 说话中… for the whole
  of a slow generation while playing nothing. Confirmed on the board:
  with a generation stalled 20 s, the firmware's own state machine
  logged `listening -> speaking` at the transcript, and a
  conversation-button press 7.1 s into that silence produced
  `Application: Abort speaking` on the device and `device aborted (no
  reason)` on the server. That is the reasonless abort seen in the
  field: a user interrupting a device that was not speaking. Moved,
  the same stall passes with the board still listening, and it enters
  `speaking` when the first sentence does. A reply that speaks nothing
  at all still sends the pair, `start` immediately before `stop`,
  because an auto-mode device re-arms its listening on `tts stop` and
  a `stop` it was never told to expect is the one way this could
  strand one. Recorded as an ADR.

- CI passes the short commit SHA as `SAMTAL_REVISION`, so a running
  container's `revision` equals its image tag's suffix instead of being
  40 characters against the tag's seven. The field meant two different
  things depending on which source produced it, and a deployment was
  caught by it: its post-deploy check compared `/healthz` to the `sha-`
  tag with equality, which is the natural reading, and got a false
  failure. The seven characters come from one expression shared by the
  build arguments, the smoke lane and `docker/metadata-action`'s
  `type=sha,format=short`, so the tag and the reported revision cannot
  drift apart. Deliberately not `git rev-parse --short`, whose length
  git widens as a repository grows: what this has to agree with is the
  tag. A working tree still reports `git describe --always --dirty`,
  keeping the `-dirty` marker that says a build is running code which
  is not any commit, and an image built with no build argument still
  reports `unknown`.

- The rules an `openai` provider derives from its `base_url` (whether
  a key is required, whether the type's model rules apply, the retry
  policy) moved to a shared `providers/openai_endpoint.py`, now that
  two stages decide them the same way. No behaviour changed.

### Fixed

- A provider that fails while constructing itself now says which
  configuration entry it was. `build_provider` named the entry for
  every other failure it raises (an unknown type, a bad option, a
  missing extra, an egress-marked provider under `local_only`), which
  is what makes a bad configuration a five-second fix, but the factory
  call itself was unwrapped: a local engine fetching its weights can
  fail on a blocked host, a full volume, a corrupt cache or a name the
  hub does not have, and each of those arrived as a traceback from
  inside `faster_whisper`, `httpx` or `huggingface_hub` with no
  mention of what was being built. Survivable while a configuration
  had one provider per stage, since there was only one candidate;
  multi-entry configurations are now normal, and a deployment running
  language-locked personas has three ASR entries and three TTS entries
  differing only in a pinned language and a voice. `ProviderError`
  passes through untouched, so every existing message keeps its exact
  wording, and the original exception survives as `__cause__`, so the
  traceback is still there. Not a reachability check and not a retry:
  the boot fails at the same moment for the same reason, and only says
  which entry it was.

- An ASR transcript that comes back as the configured `prompt` is now
  discarded rather than answered. On short or low-content audio the
  transcription model hands the prompt back instead of hearing
  anything: provoked against `gpt-4o-mini-transcribe`, 45 of 45 clips
  of room tone under a second returned the prompt word for word. That
  is not a cosmetic artifact, because the transcript reaches the model
  as something the user said. A field session set `prompt` to the
  assistant's name and its three agent names, precisely so the personas
  would be recognised when spoken, and a 0.9 s utterance came back as
  that string and was read as a request to switch agents: a handover
  nobody asked for. The provider knows what prompt it sent, so a
  transcript equal to it (trimmed, case-insensitive, and ignoring a
  full stop the model added, which one of the 45 carried) is now
  treated as silence, the same as audio under the minimum length, and
  logged as a warning so it is not a silent drop. Equality rather than
  containment: someone can say the words in the prompt. An entry with
  no `prompt` is unaffected. The README and `config.example.yaml` now
  state the failure mode and the rule that follows from it: keep the
  prompt to vocabulary, never to anything the assistant could act on.

- `switch_agent` naming the agent that is already speaking is now
  refused instead of performed. It was reported twice in one field
  session: the user's utterance mentioned a persona by name while that
  persona was already active, the model called `switch_agent` on it,
  and the session ended the leg, re-activated the same agent and ran a
  second LLM round (2.82 s and 1.70 s) whose only product was the
  assistant introducing itself to someone it was already talking to. A
  handover to the current agent is a pure cost with no possible effect,
  so it now comes back as a tool error the current agent phrases in its
  own voice and language, like the other refusals, and the reply
  continues rather than stopping. A string comparison, made before the
  round is committed. Switching to a different bound agent is
  unaffected, and a device bound to one agent still gets no
  `switch_agent` tool at all.

- Documentation gaps a deployment hit in the field, all of them cheap
  to state and expensive to discover. The ElevenLabs stock voices are
  recorded by English speakers, so a stock voice speaking Spanish
  sounds like an American speaking fluent Spanish; the `voice_id`
  comment now says to pin a native voice for a non-English agent, and
  that a professional clone is fine-tuned per model, so one unavailable
  on the configured model fails at synthesis rather than at boot and
  presents as silence on the device. The `memory:` comment now says
  that renaming an agent orphans its memory, since the key is the agent
  name, and that conversation history carrying across a `switch_agent`
  handover is not the same thing as agent memory: a persona that has
  stored nothing can still greet the user by name the moment it takes
  over, from the transcript rather than from its own file. And the
  README's Security section gains a table of which hosts each provider
  type reaches, because a blocked host does not announce itself, and
  because an `openai` ASR shares `api.openai.com` with an
  `openai_compatible` LLM while `elevenlabs` TTS needs a host of its
  own, which is not obvious and is the one most likely to be missed.

- A multi-sentence reply no longer stutters. Frames are paced to
  realtime, so sending a sentence takes about as long as hearing it,
  and each sentence used to be synthesized only once the previous one
  had finished playing. That put the next sentence's whole time to
  first byte on the speaker as silence, once per sentence, for the
  whole reply: measured against the real providers, 884 ms and 478 ms
  between the sentences of a three-sentence reply through
  `gpt-4o-mini-tts`, and 129 ms and 139 ms through
  `eleven_flash_v2_5`. It was reported from a board session as hiccups
  in the assistant's voice. It was also worse than a plain pause,
  because the frame pacer's schedule is absolute from a reply's first
  frame, so the frames after a stall burst out to catch up: a dropout
  followed by a flood. A sentence now starts synthesizing before the
  previous one is spoken rather than after, one sentence of lookahead,
  so that latency is spent against playback that is already happening.
  Measured again, every sentence boundary is a single 60 ms frame,
  which is the cadence rather than a gap, and the catch-up bursts are
  gone. A sentence run ahead and then cancelled by a barge-in is
  neither spoken nor recorded as spoken, the lookahead stops at a tool
  round's boundary, and a synthesis belongs to the agent leg that
  started it, so a handover cannot speak in the wrong voice.

- The server README's log event table was missing `session_idle`, added
  the same day the event was, and its `session_open` row did not list
  the new `revision` field. Both rows now match what the server emits.

- Documentation for the `openai` ASR provider told operators that
  leaving `language` unset "costs nothing", which a device checkpoint
  disproved: on far-field microphone audio through Opus, detection has
  much less to go on than a clean file, and unpinned Swedish came back
  as English-shaped nonsense ("Vad heter Sveriges huvudstad?" heard as
  "Hat hetas verigezogistad."). Pinning fixed it outright, and `prompt`
  does not compensate, since it fixes vocabulary rather than language.
  The README and `config.example.yaml` now tell an operator to set
  `language` for any non-English deployment. The accuracy comparison is
  marked as holding only once the language is pinned, and the latency
  tables gain the figures measured on the board, where the local engine
  came in at 964 ms rather than the desk's 1688 to 1781 ms, so the gap
  to the cloud is much narrower than first published. No code changed.

- The test suite no longer writes or reads bytecode, so a working tree
  can no longer lie about what it is running. A cached `.pyc` records
  the source's size and its mtime in whole seconds, and CPython accepts
  the cache when both are equal to the source's current values, so any
  edit that keeps the byte count and leaves the mtime on the second it
  was compiled on is invisible. Two ordinary operations here do exactly
  that: swapping two statements to check a regression test really fails
  without its fix, and restoring a file from a backup, which carries
  the backup's mtime rather than the current time. The second is how it
  bit while addressing the review on #13, where a correct fix ran as
  its pre-fix version and looked broken. `tests/conftest.py` now sets
  `sys.dont_write_bytecode`, which also covers pytest's
  assertion-rewritten test bytecode, and clears the existing caches
  under `samtal_server/` and `tests/`, since the flag stops writes but
  not reads and a cache left in place would never be refreshed. CI
  exports `PYTHONDONTWRITEBYTECODE` for the steps that are not pytest.
  The container image is deliberately untouched: its sources never
  change after the build, so timestamp validation is correct there and
  `UV_COMPILE_BYTECODE=1` is worth keeping. `AGENTS.md` gains the two
  traps, since neither is guessable.

## 2026-08-05

### Added

- New `openai` TTS provider type, streaming cloud synthesis as raw
  PCM. It needs no optional extra and adds no dependency: the `openai`
  client already ships for the `openai_compatible` LLM type, and
  speech is a method on it, so one key serves both stages. Options:
  `voice` (required), `api_key_env` (required for OpenAI itself),
  `base_url` (default `https://api.openai.com/v1`), `model` (default
  `gpt-4o-mini-tts`), `instructions`, `speed` and `timeout_s`. There
  is no audio format option: the API's `pcm` format is fixed at
  24 kHz, which is the device rate, so nothing is resampled. Naming
  `speed` on a `gpt-4o` model, or `instructions` on a `tts-1` model,
  fails the boot rather than becoming a knob the API silently ignores;
  that check knows OpenAI's models, so it applies only when `base_url`
  names OpenAI's host and a compatible server receives both knobs
  unexamined. The host is what decides, so every spelling of OpenAI's
  endpoint keeps the same startup checks, and a `base_url` that is not
  a URL fails the boot rather than the first synthesis.
  `base_url` reaches any server implementing `/v1/audio/speech`, so a
  local pipeline stays available through this type and no key is
  needed for one; that also means the endpoint rather than the type
  decides egress, and an entry under `server.local_only` declares its
  own, exactly as `openai_compatible` does. Retries are off, so
  `timeout_s` bounds a sentence: the SDK would otherwise attempt a
  failed request three times, leaving the device silent for three
  timeouts plus backoff.
  Documented with a caveat found on the test board: because a reply is
  synthesized sentence by sentence with no lookahead, this provider's
  time to first byte is paid at every sentence boundary as well as at
  the start, measured at 520 to 617 ms per boundary against
  ElevenLabs' 111 to 131 ms, which is audible as stuttering on long
  replies. The provider suits short answers until #37 lands.
- New `elevenlabs` TTS provider type, streaming cloud synthesis as raw
  PCM. It needs no optional extra: the API is one streaming POST, so
  the provider speaks it over `httpx` (now a direct dependency)
  instead of a vendor SDK, and is present in every install. Options:
  `voice_id` and `api_key_env` (both required), plus `model`
  (default `eleven_flash_v2_5`, the low-latency model), `output_format`
  (default `pcm_24000`, which matches the device rate so nothing is
  resampled), `language_code`, `voice_settings` and `timeout_s`. The
  type marks egress, so `server.local_only` refuses it.
- Every provider type now carries a class-level `egress` marking:
  whether it sends session data (audio, transcripts, replies) off the
  host. `anthropic` marks egress; `silero`, `faster_whisper`, `piper`
  and the mocks mark local. `openai_compatible` cannot know its own
  (the `base_url` decides), so it defers to an explicit per-entry
  `egress` declaration in the configuration; the other types reject
  that key, and a type without any marking counts as egress.
- New `server.local_only` flag (default `false`): when on, building
  any egress-marked provider refuses to boot with an error naming the
  stage and provider, and an `openai_compatible` entry is refused
  unless it declares `egress: false`. MCP servers sit inside the same
  boundary, since tool arguments carry conversation-derived data and
  no transport knows its own egress: every referenced `mcp_servers`
  entry needs the same declaration. Boot-time, never runtime: a
  local_only server that starts is a local_only server. The fully
  local promise becomes a property the server checks instead of a
  documentation property of a carefully chosen configuration.
- Barge-in is gated: an utterance the endpointer ends while a reply is
  in flight only cancels it on evidence of user speech. Four gates, in
  order: speech shorter than `server.barge_in_min_speech_ms` (default
  500) is dropped; an interruption landing while the reply is still
  transcribing cancels it but prepends its audio, so one reply answers
  the user's whole sentence instead of losing its head; anything
  within `server.barge_in_refractory_ms` (default 1000) of the reply's
  first audio frame is dropped as playback-onset transient; everything
  else pauses the outgoing frames while ASR transcribes the
  interruption, cancelling only on a non-empty transcript and
  otherwise resuming the reply where it stopped. The gates apply to
  endpointer-driven utterance ends only: a manual `listen stop`
  mid-reply still cancels unconditionally, and `barge_in: false` is
  untouched. The decision is recorded in
  `docs/adr/2026-08-05-replies-cancel-only-on-evidence-of-speech.md`.
- The `Endpointer` protocol gained `speech_ms()`: milliseconds
  classified as speech since the last reset, at each implementation's
  own window granularity.
- New structured log events, since events are the observability
  surface: `barge_in_suppressed` (`reason`: `min_speech`,
  `refractory`, or `no_transcript`, plus `speech_ms`) fires when a
  gate drops an interruption, and `barge_in_merged` (`speech_ms`)
  fires on the mid-transcription merge. The `speech_ms` they carry is
  exactly the data the two thresholds should be tuned with from a
  deployment's retained logs.
- The deployment profile documents both keys with noisy-deployment
  guidance: the VAD `threshold` is the companion knob (stricter
  speech classification keeps noise from reaching the gates at all),
  and its `trailing_silence_ms` suggestion now carries a caution,
  since shorter trailing silence makes mid-sentence chopping
  likelier.

### Changed

- The `barge_in` event gained `speech_ms` (the endpointer's
  speech-classified duration for the interrupting utterance) and
  `speaking_ms` (milliseconds from `speaking_started` to the cancel
  decision, absent when the reply had not yet spoken).

### Fixed

- `uv run pytest` failed to collect. `tests/unit` and
  `tests/integration` each hold a `test_ws_auth.py` and a
  `test_drain.py`, named for what they test at their own level, and
  pytest's default import mode registers a test module by its bare
  basename, so each pair collided. Any run collecting both suites
  errored, including the bare command the configured `testpaths`
  implies; only CI's split into two invocations hid it. The suites now
  run under `--import-mode=importlib`, which imports each file by its
  full path, so the descriptive names stay and the obvious command
  works.
- The README's feature list claimed a local voice pipeline of
  "SileroVAD + SenseVoice + EdgeTTS"; SenseVoice and EdgeTTS were
  never implemented. It now names the stack the provider registry
  actually ships: Silero VAD, faster-whisper, Piper.

## 2026-08-04

### Changed

- The README now leads with the project's spirit: a "Conversational
  AI. Sweded." tagline linking to the Be Kind Rewind creators' How To
  Swede video, a "What is samtal?" section that opens with the sweding
  idea and names the two upstream projects it starts from, and a
  Credits note on where the word comes from.

### Added

- `config.deploy.example.yaml`: a deployment profile example for the
  container image behind a TLS-terminating proxy on a small CPU quota,
  holding the values the issue #22 latency measurements validated
  (`cpu_threads` sized to the quota, `vad_filter: true`,
  `condition_on_previous_text: false`, a two-step `temperature`
  ladder, and `language_detect: once` with a confidence floor and
  fallback). Where `config.example.yaml` documents every key, this
  file sets only what a deployment should decide, so operators can
  adapt it instead of re-deriving the tuning from the feature docs.
  Operator review then hardened the profile: no `default_agent`, so
  the `devices` map is an allowlist and unknown devices are refused;
  the secret `ota_path` segment is injected from the environment
  rather than committed; and agent memory sits on the data volume. A
  unit test keeps it parsing and pins the allowlist posture.
- A language surface for multilingual deployments that cannot pin
  `language`: `language_detect: once` detects until one confident
  answer and then reuses it for the rest of the session (saving the
  constant per-utterance detection pass that #22 measured at 3.4 s),
  and `language_fallback` with `language_confidence_floor` uses a
  configured language instead of trusting a low-confidence guess,
  re-invoking before any decoding runs. Under the hood the ASR
  protocol now returns text with language metadata and takes a
  session-scoped hint (`AsrResult`, recorded as an ADR amendment),
  and the `heard` event carries `language` and `language_confidence`
  when an engine detected.
- The `faster_whisper` ASR provider now exposes the decode options the
  live-deployment measurements in #22 identified: `vad_filter` and
  `vad_parameters` (strip non-speech inside the ASR call),
  `condition_on_previous_text` (false is the standard mitigation for
  repetition loops), `temperature` (a short fallback ladder bounds
  worst-case decode latency), and `cpu_threads` (size the inference
  pool to a container CPU quota in config rather than through
  `OMP_NUM_THREADS`). All keep the engine's defaults when unset.
- A `speaking_started` conversation event, logged when the first Opus
  frame of a reply goes out. `replied` fires at the last frame of a
  paced stream, so on its own the logs could not separate synthesis
  cost from speaking time; the pair makes time-to-first-audio directly
  measurable, which the operator measurements in #22 asked for.
- `docs/adr/` holds architecture decision records: one immutable,
  date-prefixed file per decision that is hard to reverse, surprising
  without context, and the result of a real trade-off. The first two
  records backfill decisions from the v1 design whose consequences the
  live-deployment measurements in #22 made visible: that providers are
  startup-built singletons behind payload-only protocols, and that the
  structured JSON log events are the server's observability surface and
  transcript store until v3.
- `samtal-esp32/README.md` documents how a board behaves in daily use.
  It separates what holds for any board running the upstream firmware,
  that the microphone is live locally for the wake word but reaches the
  server only during a conversation, that nothing in the firmware ends
  that conversation once it is open, and the 2.4 GHz limit that governs
  phone hotspots, from what is read out of one board's own
  configuration, which is the controls and the sleep and shutdown
  timings. The hardware tables in both READMEs now link the working
  board's status to it.

### Changed

- `faster_whisper`'s `beam_size` now defaults to 1 (greedy decoding)
  instead of 5. Beam search costs a multiple of the decode time on CPU
  and buys little accuracy on short spoken commands (#19); production
  measurement showed the predicted speedup with no attributable
  accuracy cost (#22). Deployments that want the old behaviour set
  `beam_size: 5` explicitly.

### Fixed

- An utterance handed to ASR no longer carries the whole gap since the
  previous one (#14). A continuously listening realtime session
  buffers the reply's playback time and the user's thinking pause, and
  every utterance after the first dragged up to thirty seconds of that
  silence into transcription: slower on every turn, billed audio on
  hosted ASR, and the source of the garbled transcripts and language
  misdetections measured in #22. The endpointer now reports where
  speech began, and the session trims the utterance to the speech plus
  `server.utterance_pre_roll_ms` (default 300 ms, so the first phoneme
  survives) plus the trailing window. `heard`'s `duration_s` therefore
  means how long the user spoke again, which matters because retained
  logs are the transcript store.

## 2026-08-03

### Added

- samtal-server hardening and release (M7): the server is now something
  you can deploy. It ships as a multi-arch container image
  (`ghcr.io/rafacm/samtal-server`, amd64 and arm64, tagged `latest`, the
  build time, and the commit SHA), built and published by CI only after
  the tests pass, with both local engines baked in so one `docker run`
  with one mounted YAML serves a conversation. Model weights are still
  never baked in: `HOME` points at the mounted volume, where whisper
  models and Piper voices download at first start. A fourth test lane,
  `tests/smoke`, holds a whole conversation with the freshly built
  container in CI, which turns the milestone acceptance into something
  checked rather than remembered.
- Structured logging: `server.log_format` (`text` or `json`, and `json`
  is the image's default) and `server.log_level`. Every conversation
  event now carries structured fields alongside its human sentence
  (`event`, `session`, `device`, plus what the event holds), so retained
  JSON logs filtered on `heard`/`replied`/`agent_said` and grouped by
  session read back as transcripts. That stands in for a conversation
  store until v3 brings a real one.
- Limits and a graceful shutdown: `server.limits.max_sessions` (eight
  concurrent conversations) and `server.limits.max_session_s` (an hour,
  which bounds an idle session too, so there is no separate idle key).
  On SIGTERM the server stops admitting sessions and lets every reply in
  flight finish speaking before closing those sockets, inside
  `server.drain_s`; a second signal forces the exit. Uvicorn cannot do
  this part, since it fail-closes every websocket with 1012 the moment
  its own shutdown begins.

### Changed

- `docs/xiaozhi-notes.md` records three findings from provisioning a
  board against an HTTPS backend: that a device missing from the
  `devices:` allowlist still gets `200 OK` from the OTA check with an
  empty token and is refused only at the WebSocket handshake, that the
  firmware needs no certificate work because the ESP-IDF bundle plus
  cross-signed verification covers the current Let's Encrypt chain, and
  that probing a WebSocket route with `curl` requires `--http1.1` or the
  route answers a misleading `404`. The NVS note now also lists which
  namespaces to carry across a regeneration and which regenerate
  themselves.
- Published images carry the build time (`2026-08-03-1200`, UTC) where
  they carried the build date. A date-only tag was claimed by every
  build that day, so it moved like a second `latest` while reading like
  a release marker: two merges on 2026-08-03 both took `2026-08-03`,
  and the second changed what that tag meant four hours after the
  first. `latest` is now the only tag that moves.
- `default_agent` is now required only when agents are defined and no
  device is bound to one. Omitting it is how a deployment says "only
  these devices": every unknown MAC then resolves to no agent, is issued
  no token, and is turned away, which makes the `devices` map the
  allowlist without a second list to keep in sync.
- WebSocket pings are explicit at 20 seconds, which settles the per-path
  idle timeout question the v1 plan parked: a proxy in front needs only
  a read timeout above that interval, and the two paths need no
  different treatment.

### Fixed

- A realtime-mode session no longer goes deaf after its first utterance.
  It served exactly one exchange: a realtime device sends `listen start`
  once and then streams continuously, and the server stopped listening
  after every utterance waiting for a re-arm that was never coming, so a
  board answered one question per button press. The firmware asks for
  realtime exactly when its echo cancellation is on, which makes this
  the normal case for the hardware this project targets rather than an
  edge case. A realtime session now keeps listening, including while it
  speaks, so an utterance that ends mid-reply cancels that reply and is
  answered instead: talking over the assistant stops it. The new
  `server.barge_in` (default true) turns the interrupting off for a
  board whose echo cancellation leaks the speaker back into the
  microphone, where a reply would otherwise interrupt itself;
  conversations stay multi-turn either way. The listening mode a device
  asks for is now logged at info, and an interruption logs a `barge_in`
  event.
- An interrupted reply now leaves the conversation history holding
  exactly the sentences the user heard. Sentences were counted per
  round, and a reply cut off mid-round lost all of them, so a device
  that spoke for thirteen seconds before being interrupted left no
  trace: the reply answering the interruption was written as though
  none of it had been said. They are counted one at a time now, as
  each sentence's audio goes out, which also keeps the sentence that
  was cut off partway out of the history and out of the retained
  logs.

### Security

- Device authentication is on by default, and a server started with it
  enabled and no secret in the environment refuses to boot rather than
  quietly serving every device that connects. The OTA endpoint issues
  each bound device an HMAC token (upstream's scheme, so stock firmware
  needs no change), and the websocket handshake verifies it before
  accepting the upgrade: a missing, forged, expired, or foreign token is
  refused with HTTP 403 and never reaches a socket. Opting out for a
  trial on a trusted network is one deliberate flag,
  `server.auth.enabled: false`.
- `server.ota_path` makes the endpoint's path configurable, so a public
  deployment can hide the one endpoint that cannot require a token
  behind a long random segment.
- FastAPI's `/docs`, `/redoc`, and `/openapi.json` are no longer served.
  A device needs two paths and a healthcheck a third.

## 2026-08-02

### Added

- samtal-server tools and MCP (M6): the assistant can now do things, not
  only say them. Three sources of tools merge into one list the model
  sees, kept apart by the shape of their names rather than by collision
  handling: MCP servers configured per agent under a new top-level
  `mcp_servers` section (stdio and streamable-http, referenced through
  an `mcp` list that `agent_defaults` can supply, secrets written as
  `$VAR` and resolved at boot), the device's own tools discovered over
  the conversation socket, and two builtins. `switch_agent` moves a
  conversation between the agents its device is bound to, and the new
  agent greets in its own prompt and its own voice with the history
  carried over; `remember` keeps a fact in a per-agent file that is
  injected into that agent's prompt on every reply, configured by an
  optional `memory` section. The session owns the tool loop, so
  providers stay translators and the round after a handover can go to a
  different one; a tool that fails, times out, or does not exist becomes
  an error result the model explains in its own voice rather than a
  broken reply. A server that is unreachable at startup logs a warning
  and reconnects in the background, while configuration mistakes still
  fail the boot. The official `mcp` SDK is now a core dependency.
- samtal-server agents and bindings (M5): distinct personas, enforced. A
  new top-level `agent_defaults` section holds what every agent uses
  unless it names something else, so a typical agent shrinks to a prompt
  and a voice; it deliberately takes no prompt, since a prompt is what
  makes an agent that agent. A device is bound to one agent or to a list
  of them, the first being the agent a conversation starts on and the
  rest the ones M6's spoken switching will reach, and the session now
  holds an explicit active agent whose prompt, providers, and endpointer
  swap together. Two simulated devices in one server run get two
  personas: the reply text comes from each agent's own prompt and the
  audio in each agent's own voice. The opt-in local lane runs the same
  thing on real engines, identifying the voice each device was answered
  in by re-speaking the reply in both configured voices.
- samtal-server conversation pipeline (M4), replacing the M3 echo: while
  the device listens, decoded audio feeds a Silero VAD endpointer; the
  finished utterance is transcribed (announced to the device in an `stt`
  message), the LLM streams a reply that a sentence splitter cuts into
  speakable pieces, and TTS speaks each sentence back as paced Opus frames
  at 24 kHz, the rate the server hello now announces. Conversation history
  accumulates per connection, `abort` still cancels a reply mid-stream,
  and provider failures end the reply but never the session. Every stage
  is a pluggable provider chosen per agent and built at server startup, so
  configuration mistakes fail the boot: `silero` VAD (pysilero-vad, core),
  `faster_whisper` ASR (extra), `anthropic` and `openai_compatible` LLM
  (core), `piper` TTS (extra, GPL-3.0), and deterministic keyless `mock`
  providers that let CI run the whole pipeline. Model weights and voices
  download at startup, never ship in the package.
- samtal-server opt-in local test lane (`SAMTAL_LOCAL_LANE=1 uv run
  pytest tests/local`): one real conversation through the fully local
  pipeline against a local Ollama, with a pre-flight check that fails
  naming whatever is missing. Never runs in CI; skips without the opt-in.

- samtal-server device websocket endpoint (M3) at `/xiaozhi/v1/`: accepted
  upgrade, hello exchange with a 10 second timeout, and an audio loop that
  echoes each utterance back re-encoded (a full Opus decode/encode round
  trip on PyAV), framed by `tts` messages and paced at the frame cadence.
  Utterances end on `listen stop` or through an energy endpointer standing
  in for M4's VAD; `abort` interrupts a reply in flight; binary framing
  covers protocol versions 1 to 3; devices that resolve to no agent are
  closed with policy code 1008. The integration lane now runs the
  xiaozhi-sdk simulator end to end against a live server. Verified on the
  desk: the board that got 403 since M2 now holds the hello exchange and
  echoes speech.
- samtal-server device OTA/config endpoint (M2) at `/xiaozhi/ota/`: a device
  POSTs its system info and receives the WebSocket URL, an (as yet empty)
  token, the binary protocol version to speak, and the wall clock. The
  firmware section always answers "up to date" because samtal-server serves
  no images, and no activation section is ever sent. The `Device-Id` MAC
  resolves to an agent through the config, falling back to `default_agent`.
  A `GET` on the same path reports where devices are being sent. New
  `server` keys: `websocket_url` (defaults to the address the device reached
  the OTA endpoint on), `protocol_version`, and `timezone_offset_minutes`.
- samtal-server configuration layer (M1), built on pydantic-settings:
  models for `server`, `providers`, `agents`, `devices`, and
  `default_agent`, loaded from one YAML file (`--config` or
  `SAMTAL_CONFIG`). Any key is overridable via `SAMTAL_`-prefixed
  environment variables (nested keys joined with `__`, e.g.
  `SAMTAL_SERVER__PORT`), and a `.env` file is read at startup with
  environment variables taking priority. Secrets are referenced by
  environment variable name only, and validation reports every problem
  with its location. A documented `config.example.yaml` ships with the
  server.
- `docs/README.md` as an index of the research notes, plans, and feature
  docs, linked from the root README's project layout.
- samtal-server README sections on transports (WebSocket only for v1, with
  upstream's MQTT+UDP as the additive alternative; WebRTC is not an upstream
  transport) and on ports and topology, covering the single-port choice, its
  tradeoffs, and what a reverse proxy in front of it has to get right.
- Waveshare ESP32-S3-Touch-AMOLED-2.16 (480×480 AMOLED, dual-mic AEC) listed
  as a planned target board.
- samtal-server stack decision: Python 3.12 + FastAPI (uv-managed), with the
  xiaozhi-sdk device simulator for hardware-free integration tests.
- samtal-server v1 plan (`docs/plans/2026-08-02-samtal-server-v1.md`):
  architecture, milestones M0 to M7 with device checkpoints, folder-scoped
  GitHub Actions CI, and instance-config separation.
- Workflow and documentation conventions in `AGENTS.md`: feature branches
  with rebase-only PRs for code work, dated plan files in `docs/plans/`,
  feature docs in `docs/features/`, and `gh` API tips.
- M0 skeleton for samtal-server: uv-managed Python 3.12 package with FastAPI
  app and `/healthz`, unit and integration test lanes, ruff, and the
  folder-scoped GitHub Actions workflow.

### Changed

- samtal-server `devices` values are now one agent name or a list of
  them, always stored as a list, and `Config.agent_for_device` became
  `agents_for_device`, returning the whole list. Existing single-name
  bindings keep working unchanged. `config.example.yaml` gained
  `agent_defaults`, a second voice, a second persona, and a list-valued
  binding.
- samtal-server agents must now name a provider for all four pipeline
  stages (`llm`, `asr`, `tts`, `vad`); the server refuses to start
  otherwise. `config.example.yaml`'s placeholder `sensevoice` entry became
  the real `faster_whisper` type, and its agent prompt now states the
  reply language explicitly.
- README header now shows project status badges for server CI, Python,
  FastAPI, ESP-IDF, and the MIT license.
- Hardware tables (root and samtal-esp32 READMEs) now list the e-paper
  board first, link each board name to its product page, and keep a single
  "wiki" link in the Links column.
- samtal-server now logs its own work: the CLI gives the root logger a
  handler, which uvicorn does not do, so messages from samtal-server no
  longer vanish while uvicorn's request lines appear.
- Updated logo artwork (`assets/samtal-logo.png`), same concept: the person
  and the device sharing one waveform.
- Hardware tables now link each board's product page and technical
  documentation ("doc").
- The logo is a single transparent PNG of the original artwork
  (`assets/samtal-logo.png`); the traced SVG variant is removed.

## 2026-08-01

### Changed

- New logo: a person and the device sharing one waveform, echoing the
  etymology of samtal (together + speech).
- Logo rebuilt as vector art: `assets/samtal-logo.svg` is now the source of
  truth, and `assets/samtal-logo.png` is rendered from it with a transparent
  background (fixes white edge pixels on dark pages).
- README header now shows the project logo (`assets/samtal-logo.png`).
- README rewritten in the style of clew.nvim: etymology header, early-development
  warning with 🚧 markers, feature bullets, hardware table.

### Fixed

- The vector trace of the logo had flattened the original color gradations;
  the SVG now uses real linear gradients on the orange and blue regions so it
  matches the raster original on both light and dark backgrounds.

### Added

- `AGENTS.md` with project conventions for coding agents, and `CLAUDE.md`
  referencing it.

- Project scaffold: `samtal-esp32/` (device firmware) and `samtal-server/`
  (conversation server) subprojects.
- `docs/xiaozhi-notes.md`: research notes on the upstream xiaozhi firmware and
  server, covering architecture, device↔server protocol, configuration, and the
  procedure used for the first working end-to-end demo on a Waveshare
  ESP32-S3-Touch-LCD-1.54.
- MIT license and third-party license notices for the upstream projects
  ([78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32),
  [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)).
