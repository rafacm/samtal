# Enforce the fully-local promise with egress marking

## Problem

Issue #30: running without any cloud dependency is a core samtal
promise, but it was a documentation property of a carefully chosen
configuration, not something the server checks. Nothing distinguished
a provider that keeps audio and text on the machine from one that
sends them to a vendor, so a config edit could silently break the
promise.

## Changes

- Every provider type declares whether it sends session data (audio,
  transcripts, replies) off the host, as a class-level `egress`
  attribute on a new `Provider` base class that all four stage
  interfaces inherit. `anthropic` marks egress; `silero`,
  `faster_whisper`, `piper` and the mocks mark local. The base
  defaults to True: a future type that forgets to declare counts as
  sending data away, so the omission fails a local_only boot instead
  of quietly leaking. Cloud providers under #11 land carrying the
  marking.
- `server.local_only` (default false): when on, building any
  egress-marked provider fails at boot with a `ProviderError` naming
  the stage and provider, the same failure mode as a missing extra.
  Enforcement lives in the registry, which runs at startup, so the
  check is boot-time by construction: a local_only server that starts
  is a local_only server.
- `openai_compatible` marks None because its `base_url` decides
  (Ollama on localhost or a cloud vendor). Under local_only such an
  entry must carry an explicit `egress` declaration, a declared field
  on `ProviderConfig`, with `egress: false` being the operator
  asserting the endpoint stays on the host. Types that know their own
  egress reject the key, so a stray declaration is a boot error
  rather than a silent no-op.
- MCP servers sit inside the same boundary, a review finding on the
  PR: tool arguments carry conversation-derived data, and a
  local_only config could otherwise attach a `streamable_http` server
  at a public URL. No transport knows its own egress (a stdio command
  may proxy anywhere, a url may name localhost), so there is nothing
  class-level to consult: under local_only, every referenced
  `mcp_servers` entry must declare `egress: false`, checked in
  `McpServers.build` at boot. Unreferenced entries are left alone,
  matching how only referenced providers are built.
- The issue's optional sanity check that a declared base_url's host
  is loopback or a private address is not included: the declaration
  is the operator's assertion, and the check can be added later
  without schema changes.

## Key parameters

| Option | Default | Notes |
|---|---|---|
| `server.local_only` | `false` | refuse to boot any provider that sends session data off the host |
| `providers.<stage>.<name>.egress` | unset | the operator's egress assertion; honoured only for `openai_compatible`, rejected on types that know their own |
| `mcp_servers.<name>.egress` | unset | the operator asserting the server's command or URL stays on the local network; required for referenced entries under `local_only` |

## Verification

- Unit tests (`tests/unit/test_providers_egress.py`): the class
  marking of every type (extras skipped when not installed, an
  unmarked type counts as egress), and the enforcement outcomes: an
  egress provider refused naming stage and provider, an undeclared
  `openai_compatible` refused with the declaration to add, a declared
  `egress: false` building, a stray declaration on a fixed type
  rejected, and everything building as before with local_only off.
- MCP unit tests (`tests/unit/test_tools_mcp.py`): a referenced entry
  without the declaration is refused naming the entry and the
  declaration to add, a declared `egress: false` builds, a declared
  `egress: true` is refused, and unreferenced entries are ignored.
- Real-engine boot checks on the development machine with cached
  weights: an all-local config (Silero, faster-whisper `small`,
  Piper, `openai_compatible` with `egress: false`) booted with
  `local_only: true` and served `/healthz`; the same config with an
  `anthropic` LLM was refused naming `providers.llm.cloud`.
- Full unit suite green (506 passed, 2 skipped), integration green
  (27 passed), lint clean.

## Files modified

- `samtal-server/samtal_server/providers/base.py`
- `samtal-server/samtal_server/providers/__init__.py`
- `samtal-server/samtal_server/providers/registry.py`
- `samtal-server/samtal_server/providers/mock.py`
- `samtal-server/samtal_server/providers/silero.py`
- `samtal-server/samtal_server/providers/faster_whisper.py`
- `samtal-server/samtal_server/providers/piper_tts.py`
- `samtal-server/samtal_server/providers/anthropic_llm.py`
- `samtal-server/samtal_server/providers/openai_llm.py`
- `samtal-server/samtal_server/config/models.py`
- `samtal-server/samtal_server/tools/mcp.py`
- `samtal-server/tests/unit/test_providers_egress.py`
- `samtal-server/tests/unit/test_tools_mcp.py`
- `samtal-server/config.example.yaml`
- `samtal-server/README.md`
- `CHANGELOG.md`
