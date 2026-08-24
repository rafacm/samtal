# The configuration CLI

`vinga-server config` reads and writes the domain half of a deployment's
configuration: the providers, the MCP servers, the prompt fragments, the
agent defaults, the agents, the device bindings and the default agent.
It is a client of the configuration API rather than a second way into
the database, so a refusal reads the same whichever way it was reached,
and every command needs a server to be running. An empty database is a
valid state for that server to be running on, which is what makes
configuring a deployment from nothing possible at all.

What each field means is
[`domain-config.md`](domain-config.md), generated from the models. What
the API answers is [`api-openapi.json`](api-openapi.json), generated
from the routes. This page is about the command line in front of both.

It is written in two halves. Everything above the first marker below is
written by hand. Everything between the markers is generated from the
command tree and from the commented fragments in
[`vinga-server/examples/`](../../vinga-server/examples/), and
regenerated and diffed by CI, so no command page and no recipe on this
page can describe a grammar this server does not have.

## Installing it

Three ways in, in the order a deployment meets them.

**Inside the container**, which is the intended one. The image ships the
CLI, and a container that is already serving has the token and the
loopback address in its environment, so there is nothing to arrange:

```bash
docker exec -i vinga vinga-server config list
```

A shell function makes that the shortest of the three:

```bash
vinga() { docker exec -i vinga vinga-server "$@"; }

vinga config list
```

The `-i` is load-bearing rather than habit: `set-secret` reads the
credential from stdin, and `apply -f -` reads a whole document from it.
The rest of this page spells the command out as `vinga-server config`,
which is what its own help pages say and what a checkout runs; read it
as `vinga config` wherever the shim is what you have. One thing does not
carry over: a path is resolved inside the container, which has the CLI
but not `examples/`, so a document that lives on your machine is piped
in with `-f -` rather than named.

**From a checkout**, which is what a development machine does. Run it
from `vinga-server/`, where the example fragments are:

```bash
uv run vinga-server config list
```

**As a tool of its own**, for a workstation that administers a
deployment it does not host. There is no published package yet, so
neither `uvx vinga-server` nor `uv tool install vinga-server` resolves a
release; both take a checkout or a built wheel instead:

```bash
uvx --from ./vinga-server vinga-server config list

uv tool install ./vinga-server
vinga-server config list
```

An installed tool carries no configuration file, so it is the invocation
that most needs the two variables the next section is about.

## Reaching a server

Every command except `ota-url` and the four that render documents is a
request to the configuration API, so two things have to be true: the
client has to know where the API is, and it has to carry the token.

**The address** is resolved in this order:

1. `--api-url`, which is accepted before the command word and after it
2. `VINGA_API_URL`
3. `http://127.0.0.1:<server.port>/api`, the port read from the same
   YAML file the server was started with (`--config`, or
   `VINGA_CONFIG`), so the two cannot disagree about it

**The token** is the value of the environment variable
`server.api.secret_env` names, `VINGA_API_SECRET` by default, read from
that same file. A missing one is a sentence naming the variable, printed
before any request is sent.

**The connection is loopback or TLS, and there is no flag that overrides
it.** The token grants everything the API can do and rides on every
request, so a plain `http://` connection to a host that is not a
loopback address (`127.0.0.1`, `::1` or `localhost`) is not made at all.
Such a flag's only purpose would be sending the token in clear. Reach a
remote deployment over `https://`, or through a tunnel that terminates
TLS, or from inside the container over loopback. A URL carrying a
username or a password is refused outright, and any URL this client
prints has that stripped.

```bash
export VINGA_API_URL=https://voice.example/api
export VINGA_API_SECRET=...

vinga-server config list
```

## The break-glass path

`--local` is for a database whose server will not start, which is the
one situation in which there is nothing to write through. It opens the
database directly, covers four commands and no others, and says on
stderr every time that it is bypassing the API:

```bash
vinga-server config --local show
vinga-server config --local delete agent broken
vinga-server config --local set-secret provider llm claude api_key
vinga-server config --local clear-secret provider llm claude api_key
```

Those four are the recovery subset: look at what is stored, take out
what will not load, and repair a credential. Every other command refuses
the flag by naming them.

It does not check whether a server is running against the same file.
There is no reliable way to (a pid file lies after a crash and a lock
probe races the answer), and a wrong refusal would wedge the recovery
path in exactly the situation it exists for. What makes that safe is
that a write is one transaction either way, so two writers serialize:
the second waits for the lock and commits well inside the database's ten
second busy timeout, and only a writer still waiting when that runs out
is refused, told nothing was changed and to run the command again.

Its `show` and `delete` go by what is stored rather than by what a new
write would be allowed to create, which is what lets it reach a row
written before a rule a later release added. Each of its writes says
when it takes effect, and says the same thing the API says for the same
act.

## Writing a whole deployment at once

`apply` takes one document holding any number of entities and settings,
and writes all of it in one transaction:

```bash
vinga-server config apply -f examples/presets/local-stack.yaml
```

The document's top-level keys are the sections of the domain
configuration, the entity bodies are exactly the fragments `set` takes,
and the two settings are written in the shape the configuration document
holds them in rather than the shape their own routes take: `devices` as
a MAC mapped to its list of agents, and `default_agent` as a name or an
explicit `null`.

Four promises come with it, and they are what make a document the
shortest path from an empty database to a working deployment.

**It orders the writes.** A write whose references do not resolve is
refused, which is what forces the creation order when entities are
written one at a time. A document is validated against the state it
would leave, so the providers, the defaults naming them and the agent
inheriting them all arrive together and no intermediate state is ever
checked.

**It is refused whole.** Anything in the document that will not resolve
rolls the whole transaction back, with the same sentences a single write
of the same entity would have earned. There is no half-applied document.

**It is additive, and it never deletes.** A section the document does
not mention is left alone, an empty mapping adds nothing, and an
explicit `default_agent: null` clears that setting while its absence
leaves it as it was. Pruning a store down to a document is a different
verb with different stakes, and it is deliberately not this one.

**The same document twice changes nothing.** Each entity's incoming body
is compared against the stored one before anything is written, and an
equal body is reported `unchanged`, with no write and no notice.

Two bounds sit in front of all of that, and both are refused before
anything is mutated: the number of entities one document may carry, and
the size of the request body. What has no bound is how long the client
waits. An apply loads the whole existing configuration and validates the
whole resulting one, and nothing about the request limits how large
either is, so no finite timeout can be derived that would not sometimes
expire on a transaction the server goes on to commit. The client
therefore waits for the answer however long it takes. The connect
timeout stays bounded, because a server that is not there must still say
so quickly. What remains is the connection dying mid-wait, which is the
exposure every write already has, and the recovery is the same one: read
the store back with `export` or `show`.

## Reading it back out

`show` and `export` are two projections of the same read. `show` is the
display one: the configuration as a person reads it, with every stored
credential listed underneath as a masked slot. `export` is the writable
one: the same content in the shape `apply` takes it, with a header
saying how to reproduce the deployment.

```bash
vinga-server config export > deployment.yaml
vinga-server config export agent assistant > assistant.yaml
```

A credential never travels in a read, so an exported document does not
carry one. What it carries instead is the `set-secret` command that
enters each stored credential, as comment lines at the foot of the file.
Reproducing a deployment is therefore two steps, in this order:

```bash
vinga-server config apply -f deployment.yaml
# then the set-secret commands the export listed, one per stored slot
```

That order is not a nicety. A masked value is not something a creating
write would accept, so an export that injected masks into the bodies
would fail to apply onto an empty store, which is the one place an
export most has to work; and `set-secret` addresses an entity, so it
cannot run before the entity exists.

<!-- generated: cli reference -->

Generated by `vinga-server config cli-reference`. Do not edit anything between
the two markers around it by hand: CI regenerates this region and fails on any
difference, so an edit here is reverted by the next run. Everything outside
them is written by hand and generated by nothing.

## Recipes

One topic at a time, in the order the whole list runs in against an empty
database. Every line below is read out of the example file it names, so a
recipe cannot come to name a file that moved or an entity name a fragment no
longer uses, and the whole of it is run against a live server on every build.

### A whole deployment

A whole deployment in one document: every entity it names, in one transaction,
refused whole if anything in it will not resolve. This is the shortest path
from an empty database to a server with something to say.

```bash
vinga-server config apply -f examples/presets/cloud-stack.yaml
vinga-server config apply -f examples/presets/local-stack.yaml
```

### Provider

`providers.<stage>.<name>`

One engine, named so agents can reference it.

```bash
vinga-server config set provider llm claude -f examples/llm-anthropic.yaml
vinga-server config set provider llm local -f examples/llm-openai-compatible.yaml
vinga-server config set provider asr whisper -f examples/asr-faster-whisper.yaml
vinga-server config set provider asr ears -f examples/asr-openai.yaml
vinga-server config set provider tts piper -f examples/tts-piper.yaml
vinga-server config set provider tts eleven -f examples/tts-elevenlabs.yaml
vinga-server config set provider tts openai_voice -f examples/tts-openai.yaml
vinga-server config set provider vad silero -f examples/vad-silero.yaml
```

### MCP server

`mcp_servers.<name>`

One MCP server, named so agents can reference it.

```bash
vinga-server config set mcp-server home -f examples/mcp-server-stdio.yaml
vinga-server config set mcp-server weather -f examples/mcp-server-streamable-http.yaml
```

### Prompt fragment

`prompt_fragments.<name>`

One named block of prompt text, shared by the agents that include it.

```bash
vinga-server config set prompt-fragment household -f examples/prompt-fragment.yaml
```

### Agent

`agents.<name>`

One agent: a prompt, plus whichever stages it overrides.

```bash
vinga-server config set agent assistant -f examples/agent.yaml
```

### Agent defaults

`agent_defaults`

What every agent uses unless it names something else.

```bash
vinga-server config set agent-defaults -f examples/agent-defaults.yaml
```

### Devices and the default agent

`devices, default_agent`

Which board reaches which agent, which is the one thing a preset cannot know.
A binding applies at that device's next check-in rather than at a reload.

```bash
vinga-server config bind-device aa:bb:cc:dd:ee:ff assistant
vinga-server config set-default-agent assistant
```

### Stored credentials

A credential encrypted in the database, which never puts it in a file at all.
The value is read from stdin, or from the variable --from-env names, and never
from an argument. A stored secret wins over an environment reference written
for the same slot.

```bash
vinga-server config set-secret provider llm brain api_key
vinga-server config set-secret provider llm claude api_key
vinga-server config set-secret mcp-server home env.API_ACCESS_TOKEN
vinga-server config set-secret mcp-server weather headers.Authorization
```

## Every command

Every command of the group, with the page its own `--help` prints. A command
takes `--config`, `--api-url` and `--local` before the command word as well as
after it, and a value given before it survives a command that was not given
one.

### `vinga-server config`

```
Usage: vinga-server config [OPTIONS] COMMAND [ARGS]...

  Read and write the domain half of the configuration: providers, MCP servers,
  agents, devices and their secrets. Commands go through the configuration API
  on the running server; --local is the recovery path.

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.

Commands:
  set                  create or replace one entity, from a YAML fragment or
                       from key=value arguments
  delete               delete one entity
  bind-device          bind a device by the MAC you already know, to one or more
                       agents
  add-device           bind the device showing this activation code, which is
                       the six digits on its screen; use bind-device when you
                       know the MAC instead
  apply                write a whole document: every entity, binding and setting
                       it names, in one transaction, refused whole if anything
                       in it will not resolve. Applying is additive and never
                       deletes, and the same document twice changes nothing.
                       This waits for the server's answer however long the
                       transaction takes
  pending              the devices showing an activation code, and the code each
                       is showing
  status               what each configured MCP server is doing on the running
                       server: connected, down, or unused because no agent
                       references it, since when, and which tools it published
  prompt               the system prompt a new session as this agent would be
                       sent, block by block with the size of each and the total;
                       a conversation already running holds what it assembled
                       when it started
  reload               apply the stored configuration to the running server,
                       without a restart and without dropping a conversation
  ota-url              the URL to type into a device's captive portal; derived
                       from this configuration and the device-auth secret, and
                       it contacts nothing
  set-default-agent    the agent an unbound device reaches
  clear-default-agent  unset it, leaving the devices map as the allowlist
  set-secret           store one credential, encrypted, read from stdin or a
                       variable
  clear-secret         remove one stored credential
  list                 a summary tree
  schema               the JSON Schema of one entity, or of the whole domain
                       half
  reference            the markdown reference, generated from the models
  openapi              the configuration API's OpenAPI document, generated from
                       its routes
  cli-reference        the generated half of the CLI reference: the recipes read
                       out of the example fragments, and every command's own
                       help page
  show                 everything, or one entity
  export               the stored configuration as a document apply takes, or
                       one entity's fragment
```

### `vinga-server config set`

```
Usage: vinga-server config set [OPTIONS] COMMAND [ARGS]...

  create or replace one entity, from a YAML fragment or from key=value arguments

Options:
  --help  Show this message and exit.

Commands:
  provider         create or replace providers.<stage>.<name>
  mcp-server       create or replace mcp_servers.<name>
  prompt-fragment  create or replace prompt_fragments.<name>
  agent            create or replace agents.<name>
  agent-defaults   create or replace agent_defaults
```

### `vinga-server config set provider`

```
Usage: vinga-server config set provider [OPTIONS] {STAGE} {NAME} [KEY=VALUE]

  create or replace providers.<stage>.<name>

Arguments:
  STAGE      llm, asr, tts, vad  [required]
  NAME       [required]
  KEY=VALUE  the entity written inline, one key=value per field; a dotted key
             nests (filler.enabled=true) and a value reads as one YAML scalar.
             The alternative to -f, and never both

Options:
  -f, --file PATH  YAML fragment for this entity, or - to read it from stdin;
                   the alternative to key=value arguments, and never both
                   (default: none, and one of the two forms must be given)
  --config PATH    path to the YAML config file naming server.port and
                   server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL    base URL of the configuration API (default: $VINGA_API_URL,
                   then http://127.0.0.1:<server.port>/api)
  --local          read and write the database directly instead of the API: the
                   recovery subset (show, delete, clear-secret, set-secret), for
                   when the server will not start
  --help           Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga-server config set-secret`,
which reads it from stdin or from the variable --from-env names, and never
echoes it.

fragment fields for provider (providers.<stage>.<name>):

  type: str  (required)
    The provider implementation this entry configures, such as anthropic,
    openai_compatible, faster_whisper, openai, piper, elevenlabs or silero.
  api_key_env: str | null  (default: null)
    The name of the environment variable holding this provider's credential,
    never the credential itself.
  egress: bool | null  (default: null)
    Whether this entry sends session data off the host, asserted by the
    operator for the types whose configuration decides it rather than their
    name (openai_compatible, and the openai ASR and TTS types, whose base_url
    may be local or a vendor).

Any other key is an option for the provider implementation; see
vinga-server/examples/ for each type's options.

Full descriptions: vinga-server config schema provider
```

### `vinga-server config set mcp-server`

```
Usage: vinga-server config set mcp-server [OPTIONS] {NAME} [KEY=VALUE]

  create or replace mcp_servers.<name>

Arguments:
  NAME       [required]
  KEY=VALUE  the entity written inline, one key=value per field; a dotted key
             nests (filler.enabled=true) and a value reads as one YAML scalar.
             The alternative to -f, and never both

Options:
  -f, --file PATH  YAML fragment for this entity, or - to read it from stdin;
                   the alternative to key=value arguments, and never both
                   (default: none, and one of the two forms must be given)
  --config PATH    path to the YAML config file naming server.port and
                   server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL    base URL of the configuration API (default: $VINGA_API_URL,
                   then http://127.0.0.1:<server.port>/api)
  --local          read and write the database directly instead of the API: the
                   recovery subset (show, delete, clear-secret, set-secret), for
                   when the server will not start
  --help           Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga-server config set-secret`,
which reads it from stdin or from the variable --from-env names, and never
echoes it.

fragment fields for mcp server (mcp_servers.<name>):

  transport: "stdio" | "streamable_http"  (required)
    Which field group applies: stdio spawns `command` as a subprocess,
    streamable_http connects to `url`.
  command: str | null  (default: null)
    The executable a stdio server is spawned as.
  args: list[str]  (default: [])
    The arguments the stdio command is spawned with, one per entry.
  env: dict[str, str]  (default: {})
    Environment variables for the spawned stdio command.
  url: str | null  (default: null)
    The endpoint a streamable_http server is reached at.
  headers: dict[str, str]  (default: {})
    Headers sent with every streamable_http request.
  egress: bool | null  (default: null)
    Whether this server sends session data off the local network.
  tool_timeout_s: float  (default: 15.0)
    How long one tool call on this server may take, in seconds, before the
    model is told it timed out.
  instructions: str | null  (default: null)
    Guidance for the model about using this server's tools, injected into the
    system prompt of every agent this entry is granted to, under a heading
    naming the prefix its tools carry.
  use_server_instructions: bool  (default: false)
    Whether to inject the guidance this server ships about itself, the
    `instructions` field of its initialize result, into the system prompt of
    every agent this entry is granted to.
  inject_prompts: list[str] | null  (default: null)
    The prompts this server publishes that are injected into the system prompt
    of every agent this entry is granted to, each by the name the server lists
    it under and in the order listed here.

Full descriptions: vinga-server config schema mcp-server
```

### `vinga-server config set prompt-fragment`

```
Usage: vinga-server config set prompt-fragment [OPTIONS] {NAME} [KEY=VALUE]

  create or replace prompt_fragments.<name>

Arguments:
  NAME       [required]
  KEY=VALUE  the entity written inline, one key=value per field; a dotted key
             nests (filler.enabled=true) and a value reads as one YAML scalar.
             The alternative to -f, and never both

Options:
  -f, --file PATH  YAML fragment for this entity, or - to read it from stdin;
                   the alternative to key=value arguments, and never both
                   (default: none, and one of the two forms must be given)
  --config PATH    path to the YAML config file naming server.port and
                   server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL    base URL of the configuration API (default: $VINGA_API_URL,
                   then http://127.0.0.1:<server.port>/api)
  --local          read and write the database directly instead of the API: the
                   recovery subset (show, delete, clear-secret, set-secret), for
                   when the server will not start
  --help           Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga-server config set-secret`,
which reads it from stdin or from the variable --from-env names, and never
echoes it.

fragment fields for prompt fragment (prompt_fragments.<name>):

  text: str  (required)
    The text injected into the system prompt of every agent whose
    prompt_includes names this fragment, as written: its indentation and its
    own blank lines are part of it, and nothing is added around it, not even a
    heading, since this is prompt text the operator wrote and a heading would
    editorialize.

Full descriptions: vinga-server config schema prompt-fragment
```

### `vinga-server config set agent`

```
Usage: vinga-server config set agent [OPTIONS] {NAME} [KEY=VALUE]

  create or replace agents.<name>

Arguments:
  NAME       [required]
  KEY=VALUE  the entity written inline, one key=value per field; a dotted key
             nests (filler.enabled=true) and a value reads as one YAML scalar.
             The alternative to -f, and never both

Options:
  -f, --file PATH  YAML fragment for this entity, or - to read it from stdin;
                   the alternative to key=value arguments, and never both
                   (default: none, and one of the two forms must be given)
  --config PATH    path to the YAML config file naming server.port and
                   server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL    base URL of the configuration API (default: $VINGA_API_URL,
                   then http://127.0.0.1:<server.port>/api)
  --local          read and write the database directly instead of the API: the
                   recovery subset (show, delete, clear-secret, set-secret), for
                   when the server will not start
  --help           Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga-server config set-secret`,
which reads it from stdin or from the variable --from-env names, and never
echoes it.

fragment fields for agent (agents.<name>):

  llm: str | null  (default: null)
    The language model, by the name it is defined under in providers.llm.
  asr: str | null  (default: null)
    The speech recognizer, by the name it is defined under in providers.asr.
  tts: str | null  (default: null)
    The voice, by the name it is defined under in providers.tts.
  vad: str | null  (default: null)
    The voice activity detector, by the name it is defined under in
    providers.vad.
  mcp: list[str | McpGrant] | null  (default: null)
    The MCP servers whose tools this layer offers the model.
  filler: FillerConfig | null  (default: null)
    Latency masking with a pre-synthesized filled pause.
  prompt_includes: list[str] | null  (default: null)
    The shared prompt fragments this agent's system prompt carries, each by
    the name it is defined under in prompt_fragments, injected in the order
    listed and directly after the agent's own prompt.
  prompt: str  (default: "")
    The instruction this agent replies under, sent as the system prompt on
    every turn.

Full descriptions: vinga-server config schema agent
```

### `vinga-server config set agent-defaults`

```
Usage: vinga-server config set agent-defaults [OPTIONS] [KEY=VALUE]

  create or replace agent_defaults

Arguments:
  KEY=VALUE  the entity written inline, one key=value per field; a dotted key
             nests (filler.enabled=true) and a value reads as one YAML scalar.
             The alternative to -f, and never both

Options:
  -f, --file PATH  YAML fragment for this entity, or - to read it from stdin;
                   the alternative to key=value arguments, and never both
                   (default: none, and one of the two forms must be given)
  --config PATH    path to the YAML config file naming server.port and
                   server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL    base URL of the configuration API (default: $VINGA_API_URL,
                   then http://127.0.0.1:<server.port>/api)
  --local          read and write the database directly instead of the API: the
                   recovery subset (show, delete, clear-secret, set-secret), for
                   when the server will not start
  --help           Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga-server config set-secret`,
which reads it from stdin or from the variable --from-env names, and never
echoes it.

fragment fields for agent defaults (agent_defaults):

  llm: str | null  (default: null)
    The language model, by the name it is defined under in providers.llm.
  asr: str | null  (default: null)
    The speech recognizer, by the name it is defined under in providers.asr.
  tts: str | null  (default: null)
    The voice, by the name it is defined under in providers.tts.
  vad: str | null  (default: null)
    The voice activity detector, by the name it is defined under in
    providers.vad.
  mcp: list[str | McpGrant] | null  (default: null)
    The MCP servers whose tools this layer offers the model.
  filler: FillerConfig | null  (default: null)
    Latency masking with a pre-synthesized filled pause.
  prompt_includes: list[str] | null  (default: null)
    The shared prompt fragments every agent's system prompt carries unless the
    agent names a list of its own, each by the name it is defined under in
    prompt_fragments, injected in the order listed and directly after the
    agent's own prompt.

Full descriptions: vinga-server config schema agent-defaults
```

### `vinga-server config delete`

```
Usage: vinga-server config delete [OPTIONS] COMMAND [ARGS]...

  delete one entity

Options:
  --help  Show this message and exit.

Commands:
  provider         delete providers.<stage>.<name>
  mcp-server       delete mcp_servers.<name>
  prompt-fragment  delete prompt_fragments.<name>
  agent            delete agents.<name>
  device           delete devices.<mac>, so the board it names reaches the
                   default agent
```

### `vinga-server config delete provider`

```
Usage: vinga-server config delete provider [OPTIONS] {STAGE} {NAME}

  delete providers.<stage>.<name>

Arguments:
  STAGE  llm, asr, tts, vad  [required]
  NAME   [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config delete mcp-server`

```
Usage: vinga-server config delete mcp-server [OPTIONS] {NAME}

  delete mcp_servers.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config delete prompt-fragment`

```
Usage: vinga-server config delete prompt-fragment [OPTIONS] {NAME}

  delete prompt_fragments.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config delete agent`

```
Usage: vinga-server config delete agent [OPTIONS] {NAME}

  delete agents.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config delete device`

```
Usage: vinga-server config delete device [OPTIONS] {MAC}

  delete devices.<mac>, so the board it names reaches the default agent

Arguments:
  MAC  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config bind-device`

```
Usage: vinga-server config bind-device [OPTIONS] {MAC} {AGENT}

  bind a device by the MAC you already know, to one or more agents

Arguments:
  MAC    [required]
  AGENT  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config add-device`

```
Usage: vinga-server config add-device [OPTIONS] {CODE} {AGENT}

  bind the device showing this activation code, which is the six digits on its
  screen; use bind-device when you know the MAC instead

Arguments:
  CODE   the six digits the device is showing and speaking  [required]
  AGENT  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config apply`

```
Usage: vinga-server config apply [OPTIONS]

  write a whole document: every entity, binding and setting it names, in one
  transaction, refused whole if anything in it will not resolve. Applying is
  additive and never deletes, and the same document twice changes nothing. This
  waits for the server's answer however long the transaction takes

Options:
  -f, --file PATH  YAML document to apply, or - to read it from stdin: the
                   sections of the domain configuration, with the entities in
                   each written as they are for set  [required]
  --config PATH    path to the YAML config file naming server.port and
                   server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL    base URL of the configuration API (default: $VINGA_API_URL,
                   then http://127.0.0.1:<server.port>/api)
  --local          read and write the database directly instead of the API: the
                   recovery subset (show, delete, clear-secret, set-secret), for
                   when the server will not start
  --help           Show this message and exit.
```

### `vinga-server config pending`

```
Usage: vinga-server config pending [OPTIONS]

  the devices showing an activation code, and the code each is showing

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config status`

```
Usage: vinga-server config status [OPTIONS]

  what each configured MCP server is doing on the running server: connected,
  down, or unused because no agent references it, since when, and which tools it
  published

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config prompt`

```
Usage: vinga-server config prompt [OPTIONS] {NAME}

  the system prompt a new session as this agent would be sent, block by block
  with the size of each and the total; a conversation already running holds what
  it assembled when it started

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config reload`

```
Usage: vinga-server config reload [OPTIONS]

  apply the stored configuration to the running server, without a restart and
  without dropping a conversation

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config ota-url`

```
Usage: vinga-server config ota-url [OPTIONS]

  the URL to type into a device's captive portal; derived from this
  configuration and the device-auth secret, and it contacts nothing

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --help         Show this message and exit.
```

### `vinga-server config set-default-agent`

```
Usage: vinga-server config set-default-agent [OPTIONS] {NAME}

  the agent an unbound device reaches

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config clear-default-agent`

```
Usage: vinga-server config clear-default-agent [OPTIONS]

  unset it, leaving the devices map as the allowlist

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config set-secret`

```
Usage: vinga-server config set-secret [OPTIONS] COMMAND [ARGS]...

  store one credential, encrypted, read from stdin or a variable

Options:
  --help  Show this message and exit.

Commands:
  provider    store a credential on providers.<stage>.<name>
  mcp-server  store a credential on mcp_servers.<name>
```

### `vinga-server config set-secret provider`

```
Usage: vinga-server config set-secret provider [OPTIONS] {STAGE} {NAME} {SLOT}

  store a credential on providers.<stage>.<name>

Arguments:
  STAGE  llm, asr, tts, vad  [required]
  NAME   [required]
  SLOT   the option it fills, such as api_key  [required]

Options:
  --from-env VAR  read the value from this variable (default: stdin, read
                  without echo at a terminal)
  --config PATH   path to the YAML config file naming server.port and
                  server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL   base URL of the configuration API (default: $VINGA_API_URL,
                  then http://127.0.0.1:<server.port>/api)
  --local         read and write the database directly instead of the API: the
                  recovery subset (show, delete, clear-secret, set-secret), for
                  when the server will not start
  --help          Show this message and exit.
```

### `vinga-server config set-secret mcp-server`

```
Usage: vinga-server config set-secret mcp-server [OPTIONS] {NAME} {SLOT}

  store a credential on mcp_servers.<name>

Arguments:
  NAME  [required]
  SLOT  env.<KEY> or headers.<KEY>  [required]

Options:
  --from-env VAR  read the value from this variable (default: stdin, read
                  without echo at a terminal)
  --config PATH   path to the YAML config file naming server.port and
                  server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL   base URL of the configuration API (default: $VINGA_API_URL,
                  then http://127.0.0.1:<server.port>/api)
  --local         read and write the database directly instead of the API: the
                  recovery subset (show, delete, clear-secret, set-secret), for
                  when the server will not start
  --help          Show this message and exit.
```

### `vinga-server config clear-secret`

```
Usage: vinga-server config clear-secret [OPTIONS] COMMAND [ARGS]...

  remove one stored credential

Options:
  --help  Show this message and exit.

Commands:
  provider    remove a stored credential from providers.<stage>.<name>
  mcp-server  remove a stored credential from mcp_servers.<name>
```

### `vinga-server config clear-secret provider`

```
Usage: vinga-server config clear-secret provider [OPTIONS] {STAGE} {NAME} {SLOT}

  remove a stored credential from providers.<stage>.<name>

Arguments:
  STAGE  llm, asr, tts, vad  [required]
  NAME   [required]
  SLOT   the option it fills, such as api_key  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config clear-secret mcp-server`

```
Usage: vinga-server config clear-secret mcp-server [OPTIONS] {NAME} {SLOT}

  remove a stored credential from mcp_servers.<name>

Arguments:
  NAME  [required]
  SLOT  env.<KEY> or headers.<KEY>  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config list`

```
Usage: vinga-server config list [OPTIONS]

  a summary tree

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config schema`

```
Usage: vinga-server config schema [OPTIONS] [ENTITY]

  the JSON Schema of one entity, or of the whole domain half

Arguments:
  ENTITY  provider, mcp-server, prompt-fragment, agent, agent-defaults, mcp-
          grant, filler, domain (default: domain)

Options:
  --help  Show this message and exit.
```

### `vinga-server config reference`

```
Usage: vinga-server config reference [OPTIONS]

  the markdown reference, generated from the models

Options:
  --help  Show this message and exit.
```

### `vinga-server config openapi`

```
Usage: vinga-server config openapi [OPTIONS]

  the configuration API's OpenAPI document, generated from its routes

Options:
  --help  Show this message and exit.
```

### `vinga-server config cli-reference`

```
Usage: vinga-server config cli-reference [OPTIONS]

  the generated half of the CLI reference: the recipes read out of the example
  fragments, and every command's own help page

Options:
  --help  Show this message and exit.
```

### `vinga-server config show`

```
Usage: vinga-server config show [OPTIONS] COMMAND [ARGS]...

  everything, or one entity

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.

Commands:
  provider         print providers.<stage>.<name>
  mcp-server       print mcp_servers.<name>
  prompt-fragment  print prompt_fragments.<name>
  agent            print agents.<name>
  agent-defaults   print agent_defaults
  device           print devices.<mac>: the agents that board is bound to
```

### `vinga-server config show provider`

```
Usage: vinga-server config show provider [OPTIONS] {STAGE} {NAME}

  print providers.<stage>.<name>

Arguments:
  STAGE  llm, asr, tts, vad  [required]
  NAME   [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config show mcp-server`

```
Usage: vinga-server config show mcp-server [OPTIONS] {NAME}

  print mcp_servers.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config show prompt-fragment`

```
Usage: vinga-server config show prompt-fragment [OPTIONS] {NAME}

  print prompt_fragments.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config show agent`

```
Usage: vinga-server config show agent [OPTIONS] {NAME}

  print agents.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config show agent-defaults`

```
Usage: vinga-server config show agent-defaults [OPTIONS]

  print agent_defaults

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config show device`

```
Usage: vinga-server config show device [OPTIONS] {MAC}

  print devices.<mac>: the agents that board is bound to

Arguments:
  MAC  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config export`

```
Usage: vinga-server config export [OPTIONS] COMMAND [ARGS]...

  the stored configuration as a document apply takes, or one entity's fragment

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.

Commands:
  provider         export providers.<stage>.<name>
  mcp-server       export mcp_servers.<name>
  prompt-fragment  export prompt_fragments.<name>
  agent            export agents.<name>
  agent-defaults   export agent_defaults
```

### `vinga-server config export provider`

```
Usage: vinga-server config export provider [OPTIONS] {STAGE} {NAME}

  export providers.<stage>.<name>

Arguments:
  STAGE  llm, asr, tts, vad  [required]
  NAME   [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config export mcp-server`

```
Usage: vinga-server config export mcp-server [OPTIONS] {NAME}

  export mcp_servers.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config export prompt-fragment`

```
Usage: vinga-server config export prompt-fragment [OPTIONS] {NAME}

  export prompt_fragments.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config export agent`

```
Usage: vinga-server config export agent [OPTIONS] {NAME}

  export agents.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```

### `vinga-server config export agent-defaults`

```
Usage: vinga-server config export agent-defaults [OPTIONS]

  export agent_defaults

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --local        read and write the database directly instead of the API: the
                 recovery subset (show, delete, clear-secret, set-secret), for
                 when the server will not start
  --help         Show this message and exit.
```
<!-- end generated: cli reference -->
