# M5 Agents and bindings: implementation plan

This file details milestone M5 of the
[samtal-server v1 plan](2026-08-02-samtal-server-v1.md). It was agreed in
conversation on 2026-08-02 and is written so a fresh session can implement
it from the repository alone. Implementation notes do not go in a companion
to this file: they go in the v1 plan's own
[implementation doc](2026-08-02-samtal-server-v1-implementation.md), as a
new M5 section appended in the same change that ticks the milestone in the
v1 plan's checklist.

## State at the start of M5

M0 to M4 are merged (PRs #1 to #4 and #6), `main` is clean, no open PRs.
The full conversation pipeline works: per-connection sessions
(`samtal-server/samtal_server/session.py`) run Silero VAD, ASR, a
streaming LLM, and sentence-by-sentence TTS behind pluggable providers
(`samtal-server/samtal_server/providers/`) that are built and validated at
server startup (`registry.py`). Device-to-agent resolution by MAC
(`Config.agent_for_device`) and per-agent prompt/provider references exist
in the config model (`samtal-server/samtal_server/config/models.py`).
What M5 owns is making distinct personas real and enforced.

## Decisions already made (do not reopen)

Agreed with Rafael before this plan was written:

- **A voice stays a TTS provider entry.** Two personas with different
  voices are two `providers.tts` entries; agents reference the one they
  want. No agent-level option overrides: for Piper the voice is the loaded
  model, so two voices cost two engine instances either way, and an
  override mechanism would add a second configuration path for the same
  thing.
- **Multi-agent-per-device groundwork lands in M5, switching lands in
  M6.** The config models a device bound to a *set* of agents and the
  session gains an explicit "currently active agent"; the spoken
  `switch_agent` tool arrives with M6's tool calling. Routing rule: the
  first bound agent is activated at connect, and a device with no binding
  falls back to `default_agent`, the agent any conversation lands on when
  nothing more specific matches.
- **No memory work in M5.** Persistent per-agent memory arrives as
  file-backed remember/recall tools in M6; the keying decision (per agent
  vs per agent and device) is made there. Session history stays
  per-connection.
- **Deferred items stay deferred**: realtime listening mode (still treated
  as auto) and OpenAI-compatible cloud ASR/TTS providers. Touch only if
  they fall out naturally, which nothing below needs.

## Design

### 1. Agent-level defaulting: `agent_defaults`

New top-level config section `agent_defaults` with the four stage fields
(`llm`, `asr`, `tts`, `vad`) and nothing else. No prompt: a persona's
prompt is its identity and must never be inherited silently. An agent's
effective pipeline is `agent_defaults` overlaid with the agent's own
entries, so a typical agent shrinks to `prompt` plus `tts`.

Cross-reference validation and the boot-time completeness check
(`build_agent_providers`) move to the effective view. Error messages name
the layer the bad or missing reference came from: `agent_defaults.llm`
when the default is wrong, `agents.tutor.llm` when the override is. The
M4 behaviour is preserved: an agent whose effective pipeline is missing
any of the four stages fails the boot with the agent named, never the
first conversation.

### 2. Device bindings: a set with an active default

`devices` values accept a string (unchanged, one agent) or a non-empty
list of agent names; internally always normalized to a list. The first
entry is the agent activated at connect. Duplicates within a list and
unknown agent names are validation errors, phrased like the existing
ones. `Config.agent_for_device` becomes `agents_for_device(mac) ->
list[str]`; unknown devices still fall back to `[default_agent]`, and a
device that resolves to nothing (no binding, no `default_agent`) still
resolves to nothing.

### 3. Session: the currently active agent

The session stores its allowed agent list and an explicit active agent
name, with a small `_activate_agent(name)` method that swaps prompt,
providers, and endpointer. M5 calls it exactly once at connect; M6's
`switch_agent` tool will call it again mid-session. Rejection paths stay
as M3 built them (a device that resolves to no agent is accepted and then
closed with 1008 and a short reason), now with integration coverage.
Conversation history (`_turns`) stays per-connection; what happens to it
on a mid-session switch is M6's decision, not M5's.

### 4. Acceptance: two devices, two personas, one server, real assertions

Mock provider extensions (`providers/mock.py`), backward compatible:

- `MockLlm` learns a `{system}` placeholder in its reply template, so a
  reply provably derives from the agent's own prompt.
- `MockTts` learns a `tone_hz` option, so two "voices" are
  distinguishable in received audio by dominant frequency.

The integration test runs one server whose config binds MAC A to agent
`poet` and MAC B to agent `tutor` (different prompts, different mock LLM
entries, different mock TTS voices), connects two xiaozhi-sdk simulators
**concurrently**, and asserts per device:

- the reply text contains that agent's own prompt marker, proving no
  shared prompt state between sessions;
- the received audio's dominant frequency matches that agent's voice,
  proving per-agent TTS.

Also covered: a third simulator with an unbound MAC gets the default
agent; with a config lacking `default_agent`, an unbound MAC gets the
1008 close. Unit tests additionally assert that two sessions of
different agents share no mutable provider state they should not.

### 5. Local lane and documentation

- `tests/local` gains an opt-in two-persona run (same
  `SAMTAL_LOCAL_LANE=1` gate and pre-flight style as M4's): two Piper
  voices, two prompts, real Silero and faster-whisper, asserting the two
  replies differ and the voices are distinct.
- `config.example.yaml` gains `agent_defaults`, a second persona with its
  own voice, and a list-valued device binding, in the same change as the
  schema change (repository rule).
- `CHANGELOG.md` updated (date-based Keep a Changelog format).
- README only if the config shape warrants it.
- No device checkpoint is required for M5. The board is on the desk if
  hearing two voices for real is useful; that is a config file away, not
  a milestone requirement.

## Commit breakdown

Small commits, one logical change each, imperative titles of roughly 50
characters with bodies explaining what and why:

1. Mock provider extensions (`{system}`, `tone_hz`) with unit tests
2. `agent_defaults` config model, effective-view validation, unit tests
3. Registry builds from the effective view, unit tests
4. List-valued `devices` and `agents_for_device`, unit tests
5. Session active-agent concept, unit tests
6. Two-persona integration test (the milestone acceptance)
7. Local lane two-persona test
8. `config.example.yaml` and `CHANGELOG.md`
9. Plan checklist tick and implementation doc M5 section (rides in the
   PR that completes the milestone)

## Process constraints

- Branch `feature/agents-and-bindings` off `main`; verify `main` is
  current first (`git pull --rebase`). Never commit code to `main`.
- Run from `samtal-server/`: `uv run pytest tests/unit -q`,
  `uv run pytest tests/integration -q`, `uv run ruff check .` before
  every push. CI runs the same.
- Open the PR when done (title per the repository convention: imperative
  verb plus deliverables, never a bare "M5:" prefix; body with a
  Verification task list, boxes checked only for steps actually carried
  out, and no hard-wrapped lines in the PR body). **Do not merge it.**
- The implementation doc section records deviations from this plan,
  or states explicitly that there were none.
- No em-dashes anywhere: docs, commit messages, code comments.
