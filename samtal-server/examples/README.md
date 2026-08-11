# Example fragments

One file per entity or provider type, each the body of a single entity
in the shape `samtal-server config set` takes it. These are examples to
copy and edit, not a configuration the server reads: the domain half of
the configuration lives in the database, and a fragment is how one
entity gets written into it.

Every file names its own command in its header, so installing one is
copy, edit, run:

```bash
samtal-server config set provider llm claude -f examples/llm-anthropic.yaml
```

The entity's name is an argument, not part of the fragment, which is why
the same `agent.yaml` can be installed twice under two names.

## What is documented where

- The **field descriptions** live on the models and are rendered into
  [`docs/reference/domain-config.md`](../../docs/reference/domain-config.md)
  and into `samtal-server config schema`. That is the contract: which
  fields exist, what type each one is, what it defaults to.
- The **provider-type options** (everything a provider entry carries
  beyond `type`, `api_key_env` and `egress`) are passed through to the
  provider implementation, so no schema can describe them. Until typed
  option models land (#88), these files are where they are documented,
  and where the measured numbers and the field findings behind each
  default are kept.

## Secrets

A fragment never holds a credential. A secret-bearing key names the
environment variable holding the value (`api_key_env: ANTHROPIC_API_KEY`
on a provider, `$NAME` in an MCP server's `env` or `headers`), and the
models refuse anything else, exactly as they do for the configuration
file.

The other way to hold a credential is encrypted in the database, which
never passes through a file at all:

```bash
samtal-server config set-secret provider llm claude api_key
```

The value is read from stdin (not echoed at a terminal) or from a named
variable with `--from-env`. A stored secret takes precedence over an
environment reference written for the same slot, and `config show` marks
the reference it displaces.

## The files

| File | Installs |
| --- | --- |
| `llm-anthropic.yaml` | `providers.llm`, Claude over the vendor API |
| `llm-openai-compatible.yaml` | `providers.llm`, any OpenAI-shaped endpoint |
| `asr-faster-whisper.yaml` | `providers.asr`, local Whisper on the CPU |
| `asr-openai.yaml` | `providers.asr`, cloud transcription |
| `tts-piper.yaml` | `providers.tts`, a local voice |
| `tts-elevenlabs.yaml` | `providers.tts`, a cloud voice |
| `tts-openai.yaml` | `providers.tts`, the other cloud voice |
| `vad-silero.yaml` | `providers.vad`, the endpointer |
| `mcp-server-stdio.yaml` | `mcp_servers`, a spawned command |
| `mcp-server-streamable-http.yaml` | `mcp_servers`, an HTTP endpoint |
| `agent-defaults.yaml` | `agent_defaults`, the singleton |
| `agent.yaml` | `agents`, one persona |

Devices and the default agent have no fragments: they are written with
`config bind-device` and `config set-default-agent`, which take
arguments rather than a document.
