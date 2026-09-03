# Example fragments and presets

Two tiers of file, for the two shapes a document can have.

A **fragment** is the body of a single entity, in the shape
`vinga-server config <noun> set` takes it: one file per entity or
provider type. A **preset**, under [`presets/`](presets/), is a whole deployment
in one document, in the shape `vinga-server config import` takes it:
several kinds at once, written in one transaction. Neither is a
configuration the server reads. The domain half of the configuration
lives in the database, and these are what get written into it.

Either way it needs a running server: `vinga-server config` writes
through the configuration API the server mounts, which
[the server README](../README.md#the-configuration-api) describes. An
empty database is a valid state for that server to be running on.

Every file names its own command in its header, so using one is copy,
edit, run:

```bash
vinga-server config import -f examples/presets/local-stack.yaml
vinga-server config apply
vinga-server config provider set llm claude -f examples/llm-anthropic.yaml
```

Those headers are also where the recipes in
[`docs/reference/cli.md`](../../docs/reference/cli.md) come from: they
are read out of these files rather than written beside them, and the
whole list is run against a live server on every build.

The entity's name is an argument, not part of the fragment, which is why
the same `agent.yaml` can be installed twice under two names. A preset
carries its entities' names in the document instead, because a document
says where each of its entries goes.

Importing is additive and never deletes: a section a document does not
name is left alone, and the same document twice changes nothing.

## What is documented where

- The **field descriptions** live on the models and are rendered into
  [`docs/reference/domain-config.md`](../../docs/reference/domain-config.md)
  and into `vinga-server config schema`. That is the contract: which
  fields exist, what type each one is, what it defaults to.
- The **provider-type options** (everything a provider entry carries
  beyond `type`, `api_key_env` and `egress`) are declared type by type.
  A type with an option model has its options checked when the entry is
  written and refused by name, and they are documented everywhere the
  fields are: a table per type in
  [`docs/reference/domain-config.md`](../../docs/reference/domain-config.md),
  a component in the API document, the epilog of `config provider set`,
  and `vinga-server config schema provider <stage> <type>`. The types
  declared that way, as the stage and type that address one, are
  `llm openai_compatible`, `asr faster_whisper` and `tts elevenlabs`.
  The first of them keeps its door open on purpose, because it exists to
  reach a server this repository has never seen: a key its model does
  not declare is not refused, it is sent to the endpoint as part of the
  request. Every other type still passes its options through to the
  implementation, so no schema can describe those. Until the rest are
  typed (#88), these files are where they are documented, and either way
  these files are where the measured numbers and the field findings
  behind each default are kept.

## Secrets

A fragment never holds a credential. A secret-bearing key names the
environment variable holding the value (`api_key_env: ANTHROPIC_API_KEY`
on a provider, `$NAME` in an MCP server's `env` or `headers`), and the
models refuse anything else, exactly as they do for the configuration
file.

The other way to hold a credential is encrypted in the database, which
never passes through a file at all:

```bash
vinga-server config provider secret set llm claude api_key
```

The value is read from stdin (not echoed at a terminal) or from a named
variable with `--from-env`. A stored secret takes precedence over an
environment reference written for the same slot, and `config show` marks
the reference it displaces.

## The files

| File | Applies |
| --- | --- |
| `presets/local-stack.yaml` | a whole deployment that reaches no vendor |
| `presets/cloud-stack.yaml` | a whole deployment on vendor APIs |

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
| `prompt-fragment.yaml` | `prompt_fragments`, one shared block of prompt text |
| `agent-defaults.yaml` | `agent_defaults`, the singleton |
| `agent.yaml` | `agents`, one agent |

Devices and the default agent have no fragments: they are written with
`config device bind` and `config default-agent set`, which take
arguments rather than a document. A preset carries neither, because
which board reaches which agent is the one thing a preset cannot know.
