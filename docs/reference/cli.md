# The configuration CLI

`vinga` configures a running deployment: the providers, the MCP servers,
the prompt fragments, the agent defaults, the agents, the device
bindings and the default agent. Those are the domain half of a
deployment's configuration, the half a server serves; the file half it
boots from is read here and never written. It is a client of the
configuration API rather than a second way into the database, so a
refusal reads the same whichever way it was reached.
That is the normal path and almost every command is on it, which means
almost every command needs a server to be running. An empty database is
a valid state for that server to be running on, which is what makes
configuring a deployment from nothing possible at all.

Five commands are the exception. `schema`, `reference`, `openapi` and
`cli-reference` render documents out of the models, the routes and the
command tree, and `ota-url` derives a URL from the file half. Those five
open no database, need no key and contact nothing at all.

Two of them do need the server half of this package to be installed,
because what they read is the server's own code: `openapi` builds the
configuration application in order to describe it, and `ota-url` derives
its URL through the onboarding package. Run inside the image or from a
checkout they behave as they always have; on a workstation that
installed the CLI alone they answer one sentence saying which half is
missing, and the committed [`api-openapi.json`](api-openapi.json) is
where that workstation reads the contract instead. What to do when there
is no server to ask has a section of its own below.

One more command asks for something a bare install does not carry, and
it is a different kind of asking: `simulator run` needs an extra rather
than the server half, because it speaks a websocket and the
configuration client has no library for one. It says which extra, and
the installation section below names it.

## The two spellings

The same command has two spellings, and both resolve.

```bash
vinga provider set llm local                 # the console script
vinga-server config provider set llm local   # inside the image
```

`vinga-server` is the server's own entry point, and `config` is the word
that dispatches away from serving to configuring; it has three siblings
(`conversations`, `events` and `doctor`). `vinga` is the CLI as a tool of
its own, and it has no server to dispatch away from, so it drops the
`config` word. Everything after that word is identical, which is what
makes the two one grammar rather than two, and both reach the same entry
function, read the same `.env` file and answer the same sentences.

Everything generated on this page is rendered in the short spelling,
whichever invocation rendered it. That is deliberate: a generated
document may no more vary with the invocation than with the terminal,
and the recipes below are matched against the example fragments by that
one prefix, so a name that changed with the entry point would publish an
empty recipes region through one of them. What varies is a live `--help`
page, which prints the spelling it was actually reached by.

The docker shim below is a third way to type the same thing: the shell
function it defines is named `vinga` and runs `vinga-server` inside the
container, so `vinga config list` there is the long spelling with the
program word supplied by the function.

What each field means is
[`domain-config.md`](domain-config.md), generated from the models. What
the API answers is [`api-openapi.json`](api-openapi.json), generated
from the routes. This page is about the command line in front of both.
Why the grammar has the shape it has, and what a new command is held to,
is [`../architecture/cli-guide.md`](../architecture/cli-guide.md).

It is written in two halves. Everything above the `cli reference` marker
below is written by hand. Everything inside that marker pair is
generated from the command tree and from the commented fragments in
[`vinga-server/examples/`](../../vinga-server/examples/), and
regenerated and diffed by CI, so no command page and no recipe on this
page can describe a grammar this server does not have. The recipes
carry a marker pair of their own inside it, checked against the
fragments separately.

## Asking the grammar what it has

Two ways, and they differ in one thing: whether you arrived with a
command or not.

```bash
vinga --help                  # the root's page, on stdout, exit 0
vinga provider secret --help  # any page of the tree, the same way
vinga                         # the same page, on stderr, exit 1
vinga device pending          # the page of the noun you stopped at
```

`--help` (and `-h`, on every page) is a question, and it is answered on
stdout with exit 0, because asking is not failing. It works at every
level: the root, a noun, a sub-noun and a command.

A bare invocation is not a question but it is not a typo either, so it
is answered with the same page rather than with a sentence telling you
to ask again. `vinga` on its own prints the root's page, and a noun with
no verb after it prints that noun's page: `vinga device pending` lists
what `pending` has, not what the root has. The difference from `--help`
is where it goes and what it exits with. The page goes to **stderr**,
because stdout carries data and an invocation that ran no command
produced none, so `vinga > commands.txt` writes an empty file rather
than a help page. And the exit code is **1**, because no command ran;
scripts that branch on it keep reading a bare invocation as a failure.

Both work before anything else does. Neither reads the `.env` file, and
neither needs a server, a database or a key, so they answer in a
directory and on a machine where nothing is configured yet.

A word this grammar does not have is a different thing again, and gets
the fixed refusal every mistake gets: `vinga sttatus` says that is not a
command and points at `--help`, and prints no page.

## Installing it

Three doors. They are three different things rather than three
spellings of one, and the first is the one this documentation leads
with: a client on the machine an operator administers from, talking to
the API the deployment already serves.

**A workstation installs the CLI**, to administer a deployment whether
or not it hosts it. The default install of this package is the client
half and nothing else: the grammar, the models and the HTTP transport,
and none of the web framework, the database, the encryption, the model
SDKs or the audio stack. There is still no published name, and there
does not need to be, because a git reference is one:

```bash
uv tool install "git+https://github.com/rafacm/vinga#subdirectory=vinga-server"

vinga --version
```

That leaves `vinga` on the PATH, which is what the
[quick start](../../README.md#getting-started) types from its second
step onward. `--version` is what proves the install, because it needs
nothing arranged: every command that reads or writes configuration is a
request, and [Reaching a server](#reaching-a-server) below is the
address and the token each of those needs before it can answer. For one
command and nothing installed, `uvx` runs the same client from the same
reference:

```bash
uvx --from "git+https://github.com/rafacm/vinga#subdirectory=vinga-server" \
  vinga --version
```

Two things about that door are worth knowing before you use it. It
resolves without this repository's lockfile, so it takes the newest
dependencies its constraints allow rather than the tested ones. And it
carries no configuration file, which is why it is the invocation that
most needs the two variables the next section is about. The two
server-half commands named above are not on this door: run those in the
container or from a checkout.

One command on that door asks for more than the client half, and it asks
by name rather than by needing the server. `simulator run` holds a
conversation over a websocket, and the configuration client has no
websocket library, so it lives behind a `sim` extra carrying exactly one
distribution:

```bash
uvx --from "vinga-server[sim] @ git+https://github.com/rafacm/vinga#subdirectory=vinga-server" \
  vinga simulator run https://voice.example/xiaozhi/ota/
```

Who it is for: anybody who wants to hear a deployment answer without
owning a board. `simulator check-in` needs none of it and works from the
plain door above; asked for the conversation without the extra, `run`
says which extra to install and stops before it sends anything.

What that door resolves to is exercised on every run of the test suite,
and it is worth saying what the exercise covers, because it is stronger
than a smoke test and weaker than a promise about the network. The wheel
is built, installed into a clean environment with no extras, and the
`vinga` binary it puts there is run as a program, from a directory
outside the checkout, against a running server. Every command of the
grammar is run that way rather than imported: the ones that reach the
API are asserted to answer, and the three that need a half a bare
install does not have are asserted to print the sentence naming it. So a
wheel missing a file it needs, an entry point that stopped being
written, a heavy import that crept back into a command's own arm, and a
command that quietly left or joined that set are each a failing test. What it does not
cover is the `git+` resolution itself, which needs the network and the
published branch.

**A checkout runs it from the source**, which is what a development
machine does. Run it from `vinga-server/`, where the example fragments
are:

```bash
uv sync
uv run vinga-server config list
```

That sync gives the checkout the whole server rather than the client
half, so the same environment also serves, and every command answers.

**The image already carries it**, which is the advanced door and the
one that installs nothing anywhere. The published container image is the
server, and it ships this CLI under the server's own entry point, so a
shell inside a container that is already serving finds the token and the
loopback address in its environment and there is nothing to arrange:

```bash
docker exec -i vinga vinga-server config list
```

A shell function makes that the shortest way to type it:

```bash
vinga() { docker exec -i vinga vinga-server "$@"; }

vinga config list
```

**That function shadows an installed `vinga` for as long as the shell
defining it lives**, and every command typed in it goes to the container
rather than to the client on the PATH. It is for a shell that
administers a deployment from inside, so a workstation using the client
above is not where to define it; `unset -f vinga` puts the binary back
in a shell that already has.

Nothing about this door goes around the API. It is the same client
making the same requests, running where the token already is, which is
what makes it the answer for a deployment that deliberately does not
route `/api/` outward, the way the smoke lane seeds its own container,
and the place the two server-half commands run. What it is not is the
ordinary way to configure a deployment: that is the workstation client,
and this is the door for when the token should not travel to reach one.

The `-i` is load-bearing rather than habit: a secret set reads the
credential from stdin, and `import -f -` reads a whole document from it.
One thing does not carry over: a path is resolved inside the container,
which has the CLI but not `examples/`, so a document that lives on your
machine is piped in with `-f -` rather than named.

## Versions, and the two halves disagreeing

Two of the three doors above install the CLI separately from the server
it talks to, so the two halves can come to be different builds. Before
1.0 the policy is one sentence: **run the CLI from the same release line
as the server.**

There is no negotiation machinery, and that is a decision rather than a
gap. The committed [`api-openapi.json`](api-openapi.json) is the
contract between the halves and its `API_VERSION` is the handle; a
version exchange, a compatibility matrix or a downgrade path would be
machinery built for an incident nobody has had yet, and each of them is
a second thing that has to stay true. What a mismatched pair does
instead is fail legibly: a command whose route the server does not have
is refused by the server, and an answer whose shape this client does not
recognize earns one sentence saying so rather than a rendering of
something nobody sent.

**A command notice from a mismatched pair is not to be followed.** The
skew above is one a route or a shape can catch; this one is neither. A
release can change the words a server puts in an operator's hands while
every route and every response shape stays exactly as it was, which is
what the #371 verb rename did: the CLI's write is spelled `import` now
and its install is spelled `apply`, and both still post to the routes
they always did. So a new CLI against an old server is told, by that
server, to run the `reload` its own grammar no longer has; and an old
CLI against a new server is told to run `apply`, which in its grammar is
the write rather than the install. Neither is an error
either half can detect, because neither half is wrong about the
protocol. The answer is the policy at the top of this section: match the
halves first, then follow what the server tells you to type.

**A refusal is not a rollback**, and the difference matters most on a
write. The client checks the answer's shape after the request has been
sent and answered, so a newer server that accepted the write and
acknowledged it in a shape this client does not know earns the same one
sentence a garbled answer would, with the write already committed. Read
the state back with `show` or `diff` before repeating a write that
refused this way; the sentence says the answer could not be read, and it
says nothing at all about what the server did with the request.

So the fix for a disagreement is upgrading the older half, and the first
step is finding out which it is. Each half says so on its own:

```bash
vinga --version

curl -s https://voice.example/healthz
# {"status":"ok","version":"...","revision":"..."}
```

`/healthz` is unauthenticated and needs no token, which is what makes it
the half of the check you can run first. `version` is what the server
is; `revision` is which build of it, so a deployment that follows a
moving tag can be matched to the image that produced it. `vinga
--version` reports the version of the installed distribution and answers
whatever else is wrong, including a configuration file it cannot read,
because comparing two halves is exactly the moment when the rest of a
machine is not in a state to be relied on.

Machinery beyond that waits for a real skew incident or for 1.0. This
paragraph is the floor, recorded deliberately.

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

## Writing a whole deployment at once

`import` takes one document holding any number of entities and settings
and writes all of it to the store in one transaction. `apply` is the
separate command that installs what is stored on the running server:

```bash
vinga-server config import -f examples/presets/local-stack.yaml
vinga-server config apply
```

Two commands rather than one, and the split is what each verb's name
promises: `import` touches nothing that is running, and `apply` touches
nothing that is stored. The gap between them is where a rebuild's
credentials go, and where an operator who wants to install at a moment
of their own choosing waits. `diff` says what is sitting in that gap.

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
of the same entity would have earned. There is no half-written document.

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
waits. An import loads the whole existing configuration and validates
the whole resulting one, and nothing about the request limits how large
either is, so no finite timeout can be derived that would not sometimes
expire on a transaction the server goes on to commit. The client
therefore waits for the answer however long it takes. The connect
timeout stays bounded, because a server that is not there must still say
so quickly. What remains is the connection dying mid-wait, which is the
exposure every write already has, and the recovery is the same one: read
the store back with `export` or `show`. `apply` is a request of its own
and carries a bound of its own, sixty seconds, which is the server's own
envelope with room to spare.

## Reading it back out

`show` and `export` are two projections of the same read. `show` is the
display one: the configuration as a person reads it, with every stored
credential listed underneath as a masked slot. `export` is the writable
one: the same content in the shape `import` takes it, with a header
saying how to reproduce the deployment.

```bash
vinga-server config export > deployment.yaml
vinga-server config agent export assistant > assistant.yaml
```

A credential never travels in a read, so an exported document does not
carry one. What it carries instead is the `secret set` command that
enters each stored credential, as comment lines at the foot of the file.
Reproducing a deployment is therefore three steps, in this order, which
is the order the export's own header names:

```bash
vinga-server config import -f deployment.yaml
# then the secret set commands the export listed, one per stored slot
vinga-server config apply
```

That order is not a nicety. A masked value is not something a creating
write would accept, so an export that injected masks into the bodies
would fail to import onto an empty store, which is the one place an
export most has to work; and a secret set addresses an entity, so it
cannot run before the entity exists. The last step is the same argument
once more: an apply builds the engines the document names, and their
credentials are the step before it.

## When the server will not start

A configuration the server refuses to boot on (a stored credential no
configured key opens, an entity that cannot be loaded, a reference that
no longer resolves) leaves nothing to write through: every command above
is a request, and there is nobody to answer it. The way back is to
rebuild the store rather than to operate on it.

Both halves of the configuration live in one Postgres database, in two
schemas: `domain`, which is what refuses to boot, and `record`, which
holds what was said. Which of the two procedures below to run is decided
by whether the deployment is recording and wants to keep what it
recorded.

**The whole database, which is the ordinary case.**

```bash
# 1. Stop the server. Nothing is connected to the database while it is
#    down, which is what lets the database be dropped rather than
#    emptied table by table.
docker stop vinga && docker rm vinga

# 2. Take the database away and make it again, owned by the server role.
dropdb "$VINGA_DB_NAME" && createdb --owner "$VINGA_DB_USER" "$VINGA_DB_NAME"

# 3. Rerun the provisioning file. Dropping the database took the two
#    schemas and their default privileges with it; vinga_ro is an
#    instance-level role and is still there, which the file expects.
psql "$ADMIN_URL" -f deploy/postgres-init.sql

# 4. Start it again, which migrates from nothing and boots clean.
docker run -d --name vinga ...

# 5. Put the configuration back: the engines it names are built by the
#    apply in step 7, and their credentials are step 6.
vinga-server config import -f deployment.yaml

# 6. Re-enter each stored credential, one per secret set command the
#    export listed at the foot of that file.
vinga-server config provider secret set -- llm claude api_key

# 7. Install what was put back.
vinga-server config apply
```

**The domain schema alone, when the record is worth keeping.** A dropped
database takes the conversation record with it, since both halves live
in one. What is broken here is the domain half, so drop that schema as
the server role and rerun the provisioning file after it:

```sql
drop schema domain cascade;
```

The rerun is the same either way, and for the same reason: a
`CREATE SCHEMA ... AUTHORIZATION` is what puts the schema back under the
server role's ownership, and a dropped database also took the default
privileges that let `vinga_ro` read tables the server has not created
yet. Steps 4 to 6 then run unchanged.

The document in step 5 is a `vinga-server config export` taken while the
deployment was healthy, which is why an export belongs in version
control beside the YAML file rather than in a drawer. What that document
does not carry is the credentials themselves: a stored credential never
travels in a read, so what the export carries is the command that enters
each of them, and step 6 is running those commands. The values come from
wherever the deployment keeps its secrets, the same place the first
secret set read them from.

This is a rebuild and not a repair, and the difference matters: it puts
back what the export says and nothing else, so a row nobody knew about
goes with the schema. A deployment that wants a surgical edit to the
stored rows instead has one, through ordinary SQL against the `domain`
schema as the server role. That is not wrapped in this grammar, and
deliberately: a second way in with its own vocabulary is a second thing
to keep honest, and `psql` is already documented by the people who wrote
it.

## Upgrading from a build that kept its configuration in a file

The same rebuild, with one ordering that has to be right: **export
first, then upgrade.** This build reads Postgres and only Postgres.
There is no driver in it for the old file, no configuration key that
would point at one, and no importer, so an export attempted after the
image has rolled is an export from a server that will not start.

```bash
# 1. With the build you are still running, take the export.
vinga-server config export > deployment.yaml

# 2. Point the VINGA_DB_* variables at an empty Postgres database and
#    provision it, as in steps 2 and 3 above.

# 3. Roll the image, which migrates from nothing on first boot.

# 4. Put the configuration back and re-enter each stored credential,
#    exactly as in steps 5, 6 and 7 above.
vinga-server config import -f deployment.yaml
vinga-server config provider secret set -- llm claude api_key
vinga-server config apply
```

**The conversation record does not come across, and nothing pretends
otherwise.** There is no export format for it and no importer, and
inventing one for a pre-release store was not worth the tool it would
have become. A deployment that wants to keep what it recorded copies the
old `conversations.db` aside before the upgrade and reads it with
`sqlite3`, which is a file it now owns rather than anything this server
will look at again. The same goes for the old `vinga.db` and for both
files' `-wal` and `-shm` sidecars: nothing in this build touches them,
nothing removes them, and they sit on the data volume until somebody
archives or deletes them deliberately.

<!-- generated: cli reference -->

Generated by `vinga cli-reference`. Do not edit anything between the two
markers around it by hand: CI regenerates this region and fails on any
difference, so an edit here is reverted by the next run. Everything outside
them is written by hand and generated by nothing.

## Recipes

One topic at a time, in the order the whole list runs in against an empty
database. Every line below is read out of the example file it names, so a
recipe cannot come to name a file that moved or an entity name a fragment no
longer uses, and the whole of it is run against a live server on every build.

<!-- generated: cli recipes -->

### A whole deployment

A whole deployment in one document: every entity it names, in one transaction,
refused whole if anything in it will not resolve. This is the shortest path
from an empty database to a server with something to say.

```bash
vinga import -f examples/presets/cloud-stack.yaml
vinga apply
vinga import -f examples/presets/local-stack.yaml
vinga apply
```

### Provider

`providers.<stage>.<name>`

One engine, named so agents can reference it.

```bash
vinga provider set llm claude -f examples/llm-anthropic.yaml
vinga provider set llm local -f examples/llm-openai-compatible.yaml
vinga provider set asr whisper -f examples/asr-faster-whisper.yaml
vinga provider set asr ears -f examples/asr-openai.yaml
vinga provider set tts piper -f examples/tts-piper.yaml
vinga provider set tts eleven -f examples/tts-elevenlabs.yaml
vinga provider set tts openai_voice -f examples/tts-openai.yaml
vinga provider set vad silero -f examples/vad-silero.yaml
```

### MCP server

`mcp_servers.<name>`

One MCP server, named so agents can reference it.

```bash
vinga mcp-server set home -f examples/mcp-server-stdio.yaml
vinga mcp-server set weather -f examples/mcp-server-streamable-http.yaml
```

### Prompt fragment

`prompt_fragments.<name>`

One named block of prompt text, shared by the agents that include it.

```bash
vinga prompt-fragment set household -f examples/prompt-fragment.yaml
```

### Agent

`agents.<name>`

One agent: a prompt, plus whichever stages it overrides.

```bash
vinga agent set assistant -f examples/agent.yaml
```

### Agent defaults

`agent_defaults`

What every agent uses unless it names something else.

```bash
vinga agent-defaults set -f examples/agent-defaults.yaml
```

### Devices and the default agent

`devices, default_agent`

Which board reaches which agent, which is the one thing a preset cannot know.
A binding applies at that device's next check-in rather than at an apply.

```bash
vinga device bind aa:bb:cc:dd:ee:ff assistant
vinga default-agent set assistant
```

### Stored credentials

A credential encrypted in the database, which never puts it in a file at all.
The value is read from stdin, or from the variable --from-env names, and never
from an argument. A stored secret wins over an environment reference written
for the same slot.

```bash
vinga provider secret set llm brain api_key
vinga provider secret set llm claude api_key
vinga mcp-server secret set home env.API_ACCESS_TOKEN
vinga mcp-server secret set weather headers.Authorization
```

<!-- end generated: cli recipes -->

## Every command

Every command of the group, with the page its own `--help` prints. A command
takes `--config` and `--api-url` before the command word as well as after it,
and a value given before it survives a command that was not given one.

### `vinga`

```
Usage: vinga [OPTIONS] COMMAND [ARGS]...

  Configure a running vinga server: providers, MCP servers, agents, devices and
  their secrets. Commands go through the configuration API.

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  --version      print the installed version and exit
  -h, --help     Show this message and exit.

Commands:
  provider         read and write providers.<stage>.<name>
  mcp-server       read and write mcp_servers.<name>
  prompt-fragment  read and write prompt_fragments.<name>
  agent            read and write agents.<name>
  agent-defaults   read and write agent_defaults
  device           read and write devices.<mac>, which agents a board reaches
  default-agent    the agent an unbound device reaches
  info             what deployment this is: the API this CLI reached, the
                   running server's version and revision, the URL to type into a
                   device's captive portal, and how much of each kind is
                   configured
  import           write a whole document to the store in one transaction,
                   refused whole if anything in it will not resolve; additive,
                   never deleting, and waiting for the answer however long the
                   transaction takes; nothing running changes until vinga apply
  list             a summary tree
  show             print the whole stored configuration, with its stored secrets
                   masked
  export           the stored configuration as a document import takes
  diff             what the stored configuration would change on the running
                   server, kind by kind, with the boundary each kind's changes
                   reach a conversation at
  session          the sessions this server recorded, and erasing them
  conversation     the conversations this server recorded, and erasing them
  memory           what is remembered about a person, a place and a conversation
  events           what the running server is saying right now, as it says it
  apply            install the stored configuration on the running server,
                   without a restart and without dropping a conversation: a
                   conversation already in progress meets new tools at its next
                   utterance and new prompt text at its next activation, while a
                   changed voice reaches the next conversation
  ota-url          the URL to type into a device's captive portal; derived from
                   this configuration and the device-auth secret, and it
                   contacts nothing
  simulator        a simulated board, checking in the way one with a screen
                   would
  schema           the JSON Schema of one entity, or of the whole domain half
  reference        the markdown reference, generated from the models
  openapi          the configuration API's OpenAPI document, generated from its
                   routes
  cli-reference    the generated half of the CLI reference: the recipes read out
                   of the example fragments, and every command's own help page
```

### `vinga provider`

```
Usage: vinga provider [OPTIONS] COMMAND [ARGS]...

  read and write providers.<stage>.<name>

Options:
  -h, --help  Show this message and exit.

Commands:
  set     create or replace providers.<stage>.<name>
  show    print providers.<stage>.<name>
  export  export providers.<stage>.<name>
  delete  delete providers.<stage>.<name>
  secret  credentials stored on providers.<stage>.<name>
```

### `vinga provider set`

```
Usage: vinga provider set [OPTIONS] {STAGE} {NAME} [KEY=VALUE]

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
  --force          answer the confirmation a destructive command asks at a
                   terminal, so it does not ask (default: it asks)
  --no-input       never prompt: a destructive command refuses rather than
                   asking, and a secret is read from stdin or --from-env
                   (default: prompt at a terminal)
  -h, --help       Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga <kind> secret set`, which reads
it from stdin or from the variable --from-env names, and never echoes it.

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

options for llm type openai_compatible:

  base_url: str  (required)
    The endpoint's OpenAI-compatible base URL, such as
    http://localhost:11434/v1 for a local Ollama; pointing it at
    api.openai.com works too.
  model: str  (required)
    The model to ask for, in the endpoint's own vocabulary (qwen3:8b on
    Ollama, an OpenAI model id on api.openai.com).
  max_tokens: int  (default: 1024)
    The cap on one reply's length, in tokens.

options for asr type faster_whisper:

  model: str  (default: "small")
    Whisper model size (tiny, base, small, medium, large-v3, or a Hugging Face
    model id); weights download at server startup.
  language: str | null  (default: null)
    Language hint (ISO 639-1, such as sv or en); omit to auto-detect per
    utterance.
  device: str  (default: "cpu")
    Where the engine runs inference, in faster-whisper's own vocabulary (cpu,
    cuda, auto).
  compute_type: str  (default: "int8")
    The quantization the weights are loaded with, in faster-whisper's own
    vocabulary (int8, int8_float16, float16, float32).
  beam_size: int  (default: 1)
    Greedy decoding by default: beam search costs a multiple of the CPU time
    and buys little accuracy on short spoken commands.
  download_dir: str | null  (default: null)
    Where the model weights are cached; unset leaves the engine its own cache
    location.
  cpu_threads: int  (default: 0)
    Threads for CPU inference.
  vad_filter: bool  (default: false)
    Strip non-speech inside the ASR call before decoding.
  vad_parameters: VadParameters  (default: {})
    Tuning for the engine's own voice-activity filter, forwarded to it as
    written.
  condition_on_previous_text: bool  (default: true)
    Feeding each window's text into the next is the documented cause of
    repetition loops; false is the standard mitigation.
  temperature: list[float] | null  (default: null)
    Fallback ladder for failed decodes, as one number or a non-empty list of
    them.
  language_detect: "every_utterance" | "once"  (default: "every_utterance")
    Detection scope.
  language_fallback: str | null  (default: null)
    The language to decode in when a detection falls below the confidence
    floor; unset means the low-confidence detection is used as it is.
  language_confidence_floor: float  (default: 0.6)
    Below this detection confidence, distrust the guess: use language_fallback
    instead when one is set, and never lock a session to it.
  vad_parameters.min_silence_duration_ms: int | null  (default: null)
    How much silence ends a speech segment, in milliseconds.

options for tts type elevenlabs:

  voice_id: str  (required)
    Voice id from your ElevenLabs voice library: the id, not the display name,
    and account-specific even for the stock voices.
  model: str  (default: "eleven_flash_v2_5")
    The synthesis model.
  output_format: str  (default: "pcm_24000")
    Audio asked of the API.
  language_code: str | null  (default: null)
    Pin the spoken language (ISO 639-1) instead of letting the model infer it
    from the text.
  voice_settings: VoiceSettings  (default: {})
    Voice tuning, passed to the API as given.
  timeout_s: float  (default: 30.0)
    Seconds before a synthesis request is abandoned.
  voice_settings.stability: float | null  (default: null)
    Higher is more monotone and more predictable.
  voice_settings.similarity_boost: float | null  (default: null)
    Higher holds the synthesis closer to the reference voice.
  voice_settings.style: float | null  (default: null)
    Style exaggeration, applied to voices that carry one.
  voice_settings.speed: float | null  (default: null)
    A multiplier around 1.0, which the API caps at 0.7 to 1.2.
  voice_settings.use_speaker_boost: bool | null  (default: null)
    Sharpens the resemblance to the reference speaker, and costs latency.

Any other key is an option for a type that declares none of its own;
see vinga-server/examples/ for those types' options.

Full descriptions: vinga schema provider
```

### `vinga provider show`

```
Usage: vinga provider show [OPTIONS] {STAGE} {NAME}

  print providers.<stage>.<name>

Arguments:
  STAGE  llm, asr, tts, vad  [required]
  NAME   [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga provider export`

```
Usage: vinga provider export [OPTIONS] {STAGE} {NAME}

  export providers.<stage>.<name>

Arguments:
  STAGE  llm, asr, tts, vad  [required]
  NAME   [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga provider delete`

```
Usage: vinga provider delete [OPTIONS] {STAGE} {NAME}

  delete providers.<stage>.<name>

Arguments:
  STAGE  llm, asr, tts, vad  [required]
  NAME   [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga provider secret`

```
Usage: vinga provider secret [OPTIONS] COMMAND [ARGS]...

  credentials stored on providers.<stage>.<name>

Options:
  -h, --help  Show this message and exit.

Commands:
  set    store a credential on providers.<stage>.<name>
  clear  remove a stored credential from providers.<stage>.<name>
```

### `vinga provider secret set`

```
Usage: vinga provider secret set [OPTIONS] {STAGE} {NAME} {SLOT}

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
  --force         answer the confirmation a destructive command asks at a
                  terminal, so it does not ask (default: it asks)
  --no-input      never prompt: a destructive command refuses rather than
                  asking, and a secret is read from stdin or --from-env
                  (default: prompt at a terminal)
  -h, --help      Show this message and exit.
```

### `vinga provider secret clear`

```
Usage: vinga provider secret clear [OPTIONS] {STAGE} {NAME} {SLOT}

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
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga mcp-server`

```
Usage: vinga mcp-server [OPTIONS] COMMAND [ARGS]...

  read and write mcp_servers.<name>

Options:
  -h, --help  Show this message and exit.

Commands:
  set     create or replace mcp_servers.<name>
  show    print mcp_servers.<name>
  export  export mcp_servers.<name>
  delete  delete mcp_servers.<name>
  status  what each configured MCP server is doing on the running server:
          connected, down, or unused because no agent references it, since when,
          and which tools it published
  secret  credentials stored on mcp_servers.<name>
```

### `vinga mcp-server set`

```
Usage: vinga mcp-server set [OPTIONS] {NAME} [KEY=VALUE]

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
  --force          answer the confirmation a destructive command asks at a
                   terminal, so it does not ask (default: it asks)
  --no-input       never prompt: a destructive command refuses rather than
                   asking, and a secret is read from stdin or --from-env
                   (default: prompt at a terminal)
  -h, --help       Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga <kind> secret set`, which reads
it from stdin or from the variable --from-env names, and never echoes it.

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

Full descriptions: vinga schema mcp-server
```

### `vinga mcp-server show`

```
Usage: vinga mcp-server show [OPTIONS] {NAME}

  print mcp_servers.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga mcp-server export`

```
Usage: vinga mcp-server export [OPTIONS] {NAME}

  export mcp_servers.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga mcp-server delete`

```
Usage: vinga mcp-server delete [OPTIONS] {NAME}

  delete mcp_servers.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga mcp-server status`

```
Usage: vinga mcp-server status [OPTIONS]

  what each configured MCP server is doing on the running server: connected,
  down, or unused because no agent references it, since when, and which tools it
  published

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga mcp-server secret`

```
Usage: vinga mcp-server secret [OPTIONS] COMMAND [ARGS]...

  credentials stored on mcp_servers.<name>

Options:
  -h, --help  Show this message and exit.

Commands:
  set    store a credential on mcp_servers.<name>
  clear  remove a stored credential from mcp_servers.<name>
```

### `vinga mcp-server secret set`

```
Usage: vinga mcp-server secret set [OPTIONS] {NAME} {SLOT}

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
  --force         answer the confirmation a destructive command asks at a
                  terminal, so it does not ask (default: it asks)
  --no-input      never prompt: a destructive command refuses rather than
                  asking, and a secret is read from stdin or --from-env
                  (default: prompt at a terminal)
  -h, --help      Show this message and exit.
```

### `vinga mcp-server secret clear`

```
Usage: vinga mcp-server secret clear [OPTIONS] {NAME} {SLOT}

  remove a stored credential from mcp_servers.<name>

Arguments:
  NAME  [required]
  SLOT  env.<KEY> or headers.<KEY>  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga prompt-fragment`

```
Usage: vinga prompt-fragment [OPTIONS] COMMAND [ARGS]...

  read and write prompt_fragments.<name>

Options:
  -h, --help  Show this message and exit.

Commands:
  set     create or replace prompt_fragments.<name>
  show    print prompt_fragments.<name>
  export  export prompt_fragments.<name>
  delete  delete prompt_fragments.<name>
```

### `vinga prompt-fragment set`

```
Usage: vinga prompt-fragment set [OPTIONS] {NAME} [KEY=VALUE]

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
  --force          answer the confirmation a destructive command asks at a
                   terminal, so it does not ask (default: it asks)
  --no-input       never prompt: a destructive command refuses rather than
                   asking, and a secret is read from stdin or --from-env
                   (default: prompt at a terminal)
  -h, --help       Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga <kind> secret set`, which reads
it from stdin or from the variable --from-env names, and never echoes it.

fragment fields for prompt fragment (prompt_fragments.<name>):

  text: str  (required)
    The text injected into the system prompt of every agent whose
    prompt_includes names this fragment, as written: its indentation and its
    own blank lines are part of it, and nothing is added around it, not even a
    heading, since this is prompt text the operator wrote and a heading would
    editorialize.

Full descriptions: vinga schema prompt-fragment
```

### `vinga prompt-fragment show`

```
Usage: vinga prompt-fragment show [OPTIONS] {NAME}

  print prompt_fragments.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga prompt-fragment export`

```
Usage: vinga prompt-fragment export [OPTIONS] {NAME}

  export prompt_fragments.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga prompt-fragment delete`

```
Usage: vinga prompt-fragment delete [OPTIONS] {NAME}

  delete prompt_fragments.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga agent`

```
Usage: vinga agent [OPTIONS] COMMAND [ARGS]...

  read and write agents.<name>

Options:
  -h, --help  Show this message and exit.

Commands:
  set      create or replace agents.<name>
  show     print agents.<name>
  export   export agents.<name>
  delete   delete agents.<name>
  preview  the system prompt a new session as this agent would be sent, block by
           block with the size of each and the total; a conversation already
           running holds what it assembled when it started
```

### `vinga agent set`

```
Usage: vinga agent set [OPTIONS] {NAME} [KEY=VALUE]

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
  --force          answer the confirmation a destructive command asks at a
                   terminal, so it does not ask (default: it asks)
  --no-input       never prompt: a destructive command refuses rather than
                   asking, and a secret is read from stdin or --from-env
                   (default: prompt at a terminal)
  -h, --help       Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga <kind> secret set`, which reads
it from stdin or from the variable --from-env names, and never echoes it.

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
  memory: MemoryPolicy | null  (default: null)
    Whether this layer may remember anything.
  prompt_includes: list[str] | null  (default: null)
    The shared prompt fragments this agent's system prompt carries, each by
    the name it is defined under in prompt_fragments, injected in the order
    listed and directly after the agent's own prompt.
  prompt: str  (default: "")
    The instruction this agent replies under, sent as the system prompt on
    every turn.
  filler.enabled: bool  (default: false)
    Whether a filled pause is played while a slow reply is prepared.
  filler.delay_ms: float  (default: 1800.0)
    How long the user hears silence before the filler starts, in milliseconds,
    counted from the transcription of their utterance.
  filler.phrases: list[str]  (default: [])
    The phrases to play, written in the agent's own language; the player
    rotates through them rather than always playing the same one.
  memory.enabled: bool  (default: true)
    Whether this agent may remember anything.

Full descriptions: vinga schema agent
```

### `vinga agent show`

```
Usage: vinga agent show [OPTIONS] {NAME}

  print agents.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga agent export`

```
Usage: vinga agent export [OPTIONS] {NAME}

  export agents.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga agent delete`

```
Usage: vinga agent delete [OPTIONS] {NAME}

  delete agents.<name>

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga agent preview`

```
Usage: vinga agent preview [OPTIONS] {NAME}

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
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga agent-defaults`

```
Usage: vinga agent-defaults [OPTIONS] COMMAND [ARGS]...

  read and write agent_defaults

Options:
  -h, --help  Show this message and exit.

Commands:
  set     create or replace agent_defaults
  show    print agent_defaults
  export  export agent_defaults
```

### `vinga agent-defaults set`

```
Usage: vinga agent-defaults set [OPTIONS] [KEY=VALUE]

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
  --force          answer the confirmation a destructive command asks at a
                   terminal, so it does not ask (default: it asks)
  --no-input       never prompt: a destructive command refuses rather than
                   asking, and a secret is read from stdin or --from-env
                   (default: prompt at a terminal)
  -h, --help       Show this message and exit.

A credential is never a key=value argument: arguments land in shell history
and in the process list. Store one with `vinga <kind> secret set`, which reads
it from stdin or from the variable --from-env names, and never echoes it.

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
  memory: MemoryPolicy | null  (default: null)
    Whether this layer may remember anything.
  prompt_includes: list[str] | null  (default: null)
    The shared prompt fragments every agent's system prompt carries unless the
    agent names a list of its own, each by the name it is defined under in
    prompt_fragments, injected in the order listed and directly after the
    agent's own prompt.
  filler.enabled: bool  (default: false)
    Whether a filled pause is played while a slow reply is prepared.
  filler.delay_ms: float  (default: 1800.0)
    How long the user hears silence before the filler starts, in milliseconds,
    counted from the transcription of their utterance.
  filler.phrases: list[str]  (default: [])
    The phrases to play, written in the agent's own language; the player
    rotates through them rather than always playing the same one.
  memory.enabled: bool  (default: true)
    Whether this agent may remember anything.

Full descriptions: vinga schema agent-defaults
```

### `vinga agent-defaults show`

```
Usage: vinga agent-defaults show [OPTIONS]

  print agent_defaults

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga agent-defaults export`

```
Usage: vinga agent-defaults export [OPTIONS]

  export agent_defaults

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga device`

```
Usage: vinga device [OPTIONS] COMMAND [ARGS]...

  read and write devices.<mac>, which agents a board reaches

Options:
  -h, --help  Show this message and exit.

Commands:
  bind     bind a device by the MAC you already know, to one or more agents
  show     print devices.<mac>: the agents that board is bound to
  delete   delete devices.<mac>, so the board it names reaches the default agent
  pending  the boards waiting to be claimed, and claiming one
```

### `vinga device bind`

```
Usage: vinga device bind [OPTIONS] {MAC} {AGENT}

  bind a device by the MAC you already know, to one or more agents

Arguments:
  MAC    [required]
  AGENT  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga device show`

```
Usage: vinga device show [OPTIONS] {MAC}

  print devices.<mac>: the agents that board is bound to

Arguments:
  MAC  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga device delete`

```
Usage: vinga device delete [OPTIONS] {MAC}

  delete devices.<mac>, so the board it names reaches the default agent

Arguments:
  MAC  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga device pending`

```
Usage: vinga device pending [OPTIONS] COMMAND [ARGS]...

  the boards waiting to be claimed, and claiming one

Options:
  -h, --help  Show this message and exit.

Commands:
  list   the devices showing an activation code, and the code each is showing
  claim  bind the device showing this activation code, which is the six digits
         on its screen; use device bind when you know the MAC instead
```

### `vinga device pending list`

```
Usage: vinga device pending list [OPTIONS]

  the devices showing an activation code, and the code each is showing

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga device pending claim`

```
Usage: vinga device pending claim [OPTIONS] {CODE} {AGENT}

  bind the device showing this activation code, which is the six digits on its
  screen; use device bind when you know the MAC instead

Arguments:
  CODE   the six digits the device is showing and speaking  [required]
  AGENT  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga default-agent`

```
Usage: vinga default-agent [OPTIONS] COMMAND [ARGS]...

  the agent an unbound device reaches

Options:
  -h, --help  Show this message and exit.

Commands:
  set    the agent an unbound device reaches
  clear  unset it, leaving the devices map as the allowlist
```

### `vinga default-agent set`

```
Usage: vinga default-agent set [OPTIONS] {NAME}

  the agent an unbound device reaches

Arguments:
  NAME  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga default-agent clear`

```
Usage: vinga default-agent clear [OPTIONS]

  unset it, leaving the devices map as the allowlist

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga info`

```
Usage: vinga info [OPTIONS]

  what deployment this is: the API this CLI reached, the running server's
  version and revision, the URL to type into a device's captive portal, and how
  much of each kind is configured

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga import`

```
Usage: vinga import [OPTIONS]

  write a whole document to the store in one transaction, refused whole if
  anything in it will not resolve; additive, never deleting, and waiting for the
  answer however long the transaction takes; nothing running changes until vinga
  apply

Options:
  -f, --file PATH  YAML document to import, or - to read it from stdin: the
                   sections of the domain configuration, with the entities in
                   each written as they are for set  [required]
  --config PATH    path to the YAML config file naming server.port and
                   server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL    base URL of the configuration API (default: $VINGA_API_URL,
                   then http://127.0.0.1:<server.port>/api)
  --force          answer the confirmation a destructive command asks at a
                   terminal, so it does not ask (default: it asks)
  --no-input       never prompt: a destructive command refuses rather than
                   asking, and a secret is read from stdin or --from-env
                   (default: prompt at a terminal)
  -h, --help       Show this message and exit.
```

### `vinga list`

```
Usage: vinga list [OPTIONS]

  a summary tree

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga show`

```
Usage: vinga show [OPTIONS]

  print the whole stored configuration, with its stored secrets masked

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga export`

```
Usage: vinga export [OPTIONS]

  the stored configuration as a document import takes

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga diff`

```
Usage: vinga diff [OPTIONS]

  what the stored configuration would change on the running server, kind by
  kind, with the boundary each kind's changes reach a conversation at

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga session`

```
Usage: vinga session [OPTIONS] COMMAND [ARGS]...

  the sessions this server recorded, and erasing them

Options:
  -h, --help  Show this message and exit.

Commands:
  list    the sessions this server recorded, newest first, one page of them;
          narrow it with --device and size the page with --limit
  show    print one recorded session: the board and agent it ran with, how it
          ended, and what it stored
  delete  erase one recorded session and everything it holds: its turns wherever
          their conversations are, the calls they made, and its events
  purge   erase every session the selectors name, in one transaction; at least
          one of --session, --device and --before is required and several are
          combined
```

### `vinga session list`

```
Usage: vinga session list [OPTIONS]

  the sessions this server recorded, newest first, one page of them; narrow it
  with --device and size the page with --limit

Options:
  --device MAC   only the sessions of this board, by MAC (default: every board)
  --limit N      how many rows this page may hold (default: the API's own, 50)
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga session show`

```
Usage: vinga session show [OPTIONS] {SESSION}

  print one recorded session: the board and agent it ran with, how it ended, and
  what it stored

Arguments:
  SESSION  the session's uuid hex, as a listing prints it  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga session delete`

```
Usage: vinga session delete [OPTIONS] {SESSION}

  erase one recorded session and everything it holds: its turns wherever their
  conversations are, the calls they made, and its events

Arguments:
  SESSION  the session's uuid hex, as a listing prints it  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga session purge`

```
Usage: vinga session purge [OPTIONS]

  erase every session the selectors name, in one transaction; at least one of
  --session, --device and --before is required and several are combined

Options:
  --session ID         only this session, by its uuid hex (default: every
                       session the other selectors leave)
  --device MAC         only the sessions of this board, by MAC (default: every
                       board)
  --before YYYY-MM-DD  only the sessions that began before this UTC day, as
                       YYYY-MM-DD (default: however far back the store goes)
  --config PATH        path to the YAML config file naming server.port and
                       server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL        base URL of the configuration API (default:
                       $VINGA_API_URL, then http://127.0.0.1:<server.port>/api)
  --force              answer the confirmation a destructive command asks at a
                       terminal, so it does not ask (default: it asks)
  --no-input           never prompt: a destructive command refuses rather than
                       asking, and a secret is read from stdin or --from-env
                       (default: prompt at a terminal)
  -h, --help           Show this message and exit.
```

### `vinga conversation`

```
Usage: vinga conversation [OPTIONS] COMMAND [ARGS]...

  the conversations this server recorded, and erasing them

Options:
  -h, --help  Show this message and exit.

Commands:
  list    the conversations this server recorded, most recently active first,
          one page of them; narrow it with --agent and size the page with
          --limit
  show    print one recorded conversation: whose thread it is, what it is called
          and when it ran, and then a page of what was said in it, oldest first
  delete  erase one recorded conversation: its turns out of whatever sessions
          they were spoken in, the calls they made, and its recap checkpoints;
          the sessions themselves are left with a gap rather than deleted
```

### `vinga conversation list`

```
Usage: vinga conversation list [OPTIONS]

  the conversations this server recorded, most recently active first, one page
  of them; narrow it with --agent and size the page with --limit

Options:
  --agent NAME   only the conversations of this agent, by name (default: every
                 agent)
  --limit N      how many rows this page may hold (default: the API's own, 50)
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga conversation show`

```
Usage: vinga conversation show [OPTIONS] {CONVERSATION}

  print one recorded conversation: whose thread it is, what it is called and
  when it ran, and then a page of what was said in it, oldest first

Arguments:
  CONVERSATION  the conversation's uuid hex, as a listing prints it  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga conversation delete`

```
Usage: vinga conversation delete [OPTIONS] {CONVERSATION}

  erase one recorded conversation: its turns out of whatever sessions they were
  spoken in, the calls they made, and its recap checkpoints; the sessions
  themselves are left with a gap rather than deleted

Arguments:
  CONVERSATION  the conversation's uuid hex, as a listing prints it  [required]

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga memory`

```
Usage: vinga memory [OPTIONS] COMMAND [ARGS]...

  what is remembered about a person, a place and a conversation

Options:
  -h, --help  Show this message and exit.

Commands:
  list    with no owner, who is remembering anything in that scope and how much;
          with one, what that agent, board or conversation holds, oldest first,
          with the number each fact is addressed by; one page at a time, and a
          page that is not the last says what to give --cursor for the rest
  set     correct one remembered fact in place, keeping its number, reading the
          corrected text from a file named with -f or from standard input and
          never from an argument
  delete  erase one remembered fact by its number, or the whole of one memory
          with --all; for a conversation, clear one entry of its ledger by a
          name read from standard input, or the whole ledger with --all
```

### `vinga memory list`

```
Usage: vinga memory list [OPTIONS] {SCOPE} [OWNER]

  with no owner, who is remembering anything in that scope and how much; with
  one, what that agent, board or conversation holds, oldest first, with the
  number each fact is addressed by; one page at a time, and a page that is not
  the last says what to give --cursor for the rest

Arguments:
  SCOPE  which memory: agent, device or conversation  [required]
  OWNER  whose memory: the agent's name, the board's MAC, or the conversation's
         uuid hex

Options:
  --limit N       how many rows this page may hold (default: the API's own, 50)
  --cursor AFTER  carry on after this, as the previous page's own notice printed
                  it (default: the first page)
  --config PATH   path to the YAML config file naming server.port and
                  server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL   base URL of the configuration API (default: $VINGA_API_URL,
                  then http://127.0.0.1:<server.port>/api)
  --force         answer the confirmation a destructive command asks at a
                  terminal, so it does not ask (default: it asks)
  --no-input      never prompt: a destructive command refuses rather than
                  asking, and a secret is read from stdin or --from-env
                  (default: prompt at a terminal)
  -h, --help      Show this message and exit.
```

### `vinga memory set`

```
Usage: vinga memory set [OPTIONS] {SCOPE} {OWNER} {ID}

  correct one remembered fact in place, keeping its number, reading the
  corrected text from a file named with -f or from standard input and never from
  an argument

Arguments:
  SCOPE  which memory: agent, device or conversation  [required]
  OWNER  whose memory: the agent's name, the board's MAC, or the conversation's
         uuid hex  [required]
  ID     the fact's number, as the listing prints it beside the fact  [required]

Options:
  -f, --file PATH  read the corrected fact from this file, or from - for
                   standard input (default: standard input); never an argument,
                   because a remembered fact is content
  --config PATH    path to the YAML config file naming server.port and
                   server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL    base URL of the configuration API (default: $VINGA_API_URL,
                   then http://127.0.0.1:<server.port>/api)
  --force          answer the confirmation a destructive command asks at a
                   terminal, so it does not ask (default: it asks)
  --no-input       never prompt: a destructive command refuses rather than
                   asking, and a secret is read from stdin or --from-env
                   (default: prompt at a terminal)
  -h, --help       Show this message and exit.
```

### `vinga memory delete`

```
Usage: vinga memory delete [OPTIONS] {SCOPE} {OWNER} [ID]

  erase one remembered fact by its number, or the whole of one memory with
  --all; for a conversation, clear one entry of its ledger by a name read from
  standard input, or the whole ledger with --all

Arguments:
  SCOPE  which memory: agent, device or conversation  [required]
  OWNER  whose memory: the agent's name, the board's MAC, or the conversation's
         uuid hex  [required]
  ID     the fact's number, as the listing prints it beside the fact

Options:
  --all          the whole of that memory rather than one fact of it
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga events`

```
Usage: vinga events [OPTIONS] COMMAND [ARGS]...

  what the running server is saying right now, as it says it

Options:
  -h, --help  Show this message and exit.

Commands:
  tail  what this server is saying right now, one line per event, as it says it;
        without --follow it waits for the first event, prints it and exits
```

### `vinga events tail`

```
Usage: vinga events tail [OPTIONS]

  what this server is saying right now, one line per event, as it says it;
  without --follow it waits for the first event, prints it and exits

Options:
  --device MAC   only the events of this board, by MAC (default: every board)
  --session ID   only the events of this session, by its uuid hex (default:
                 every session)
  --level LEVEL  the lowest level to show, in any case: DEBUG, INFO, WARNING or
                 ERROR (default: INFO, which is what the retained log carries)
  --follow       keep streaming until interrupted; without it the command prints
                 the first matching event and exits
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga apply`

```
Usage: vinga apply [OPTIONS]

  install the stored configuration on the running server, without a restart and
  without dropping a conversation: a conversation already in progress meets new
  tools at its next utterance and new prompt text at its next activation, while
  a changed voice reaches the next conversation

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.
```

### `vinga ota-url`

```
Usage: vinga ota-url [OPTIONS]

  the URL to type into a device's captive portal; derived from this
  configuration and the device-auth secret, and it contacts nothing

Options:
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  -h, --help     Show this message and exit.
```

### `vinga simulator`

```
Usage: vinga simulator [OPTIONS] COMMAND [ARGS]...

  a simulated board, checking in the way one with a screen would

Options:
  -h, --help  Show this message and exit.

Commands:
  check-in  check in to an OTA URL as a board would, and say what a board at
            that address would be handed
  run       check in to an OTA URL as a board would, then hold one conversation
            over the websocket: say the packaged sentence, and print the
            transcript and the reply as they arrive
```

### `vinga simulator check-in`

```
Usage: vinga simulator check-in [OPTIONS] {URL}

  check in to an OTA URL as a board would, and say what a board at that address
  would be handed

Arguments:
  URL  the OTA URL to check in to: the address `vinga ota-url` prints inside the
       image, or the one already written into a board's NVS  [required]

Options:
  --mac MAC      the address this simulated board presents (default:
                 02:00:00:00:00:01, whose leading octet is the locally-
                 administered bit; a second board is 02:00:00:00:00:02)
  --claim AGENT  bind this board to an agent through the configuration API and
                 check in again to be issued a token; repeat the option for
                 several agents (default: print the code and the command to run)
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.

What this simulator is and is not. Both directions, on this page, so that
nobody debugs a deployment believing this is a board. Every line below is read
out of one table, which is the same table the tests hold the command to.

Supported:
  - the check-in POST, with the two headers the handler reads and the body
    shape the firmware sends
  - the four states of the reply: activating, admitted, unwelcome, and a
    refusal for anything else
  - no redirect is followed, which is the firmware's own behavior and the
    reason every device-facing route serves the slashless spelling directly
  - the activation poll at Activation-Version 1, in the firmware's cadence of
    ten polls three seconds apart, bounded
  - claiming this board through the configuration API with --claim, and
    checking in again afterwards to be issued a token
  - the reply's firmware block, read and reported as a board reads it: whether
    an image was offered, and whether the version named back is the one this
    board announced
  - the websocket handshake with its Authorization, Device-Id, Client-Id and
    Protocol-Version headers; the last is sent because the firmware sends it
    and this server reads nothing from it
  - the hello exchange, announcing whichever framing version the check-in
    reply named, as a websocket text frame
  - one packaged utterance of Opus, paced the way a microphone delivers it and
    sent under the negotiated framing
  - binary reply frames, counted, size-checked and unwrapped, with the reply's
    duration computed from the frame count
  - the close, reported by its code compared against the closed set this side
    knows and named in this side's own words
  - one turn and one only: the reply is read to its end and then the socket is
    closed
  - sending hello
  - sending listen (state=start, mode=manual)
  - sending listen (state=stop, mode=manual)
  - reading hello
  - reading stt
  - reading tts (state=start)
  - reading tts (state=stop)
  - reading tts (state=sentence_start)

Not supported, and not planned:
  - a real microphone and speakers (they need PortAudio and a runtime encoder,
    a push-to-talk loop has no non-interactive path at all, and no CI runner
    has an audio device, so it would ship as a headline feature no lane could
    drive)
  - saying anything but the one packaged sentence (the audio is encoded once
    at build time so that what this sends is byte-identical on a laptop and on
    a runner; there is no codec in any tier to encode something else with)
  - echo cancellation and barge-in (the board's own AEC quality is the number
    the whole barge-in gate stack is built around and it is invisible from the
    server, and a simulator with no playback has nothing to cancel)
  - decoding or playing the reply audio (no codec ships in any tier, so what
    is reported about reply audio is arithmetic over frames rather than sound)
  - fetching and installing a firmware image (the block that offers one is
    read and reported, per the supported row above, and nothing is ever
    downloaded: there are no partitions here to write an image to and no
    bootloader to hand it to)
  - MQTT and UDP (vinga implements the websocket transport and promises no
    other, which is a bound of the compatibility promise itself)
  - Activation-Version 2 and its HMAC (the key is burned into a device's
    eFuses and only the vendor's cloud has a copy, which is equally true of
    every consumer board)
  - the display, the captive portal and NVS (this simulator is pointed at a
    URL rather than provisioned into one, so there is nothing to draw on and
    nothing to persist)
  - sending listen (state=start, mode=auto) (the device owns the listening
    mode, and auto re-arms itself after each tts stop, which is a second
    turn-taking design rather than a flag)
  - sending listen (state=start, mode=realtime) (realtime is the only mode
    barge-in exists in, and barge-in is built around the board's own echo
    cancellation, which a simulator with no playback has nothing to do)
  - sending listen (state=start) (this simulator always names the mode it is
    listening in, so a listen carrying no mode is one it does not send)
  - sending listen (state=stop, mode=auto) (the device owns the listening
    mode, and auto re-arms itself after each tts stop, which is a second
    turn-taking design rather than a flag)
  - sending listen (state=stop, mode=realtime) (realtime is the only mode
    barge-in exists in, and barge-in is built around the board's own echo
    cancellation, which a simulator with no playback has nothing to do)
  - sending listen (state=stop) (this simulator always names the mode it is
    listening in, so a listen carrying no mode is one it does not send)
  - sending listen (state=detect, mode=auto) (the wake word is decided on the
    chip: ESP-SR runs there, the server takes no part in it, and a simulator
    has no microphone to have heard one with)
  - sending listen (state=detect, mode=manual) (the wake word is decided on
    the chip: ESP-SR runs there, the server takes no part in it, and a
    simulator has no microphone to have heard one with)
  - sending listen (state=detect, mode=realtime) (the wake word is decided on
    the chip: ESP-SR runs there, the server takes no part in it, and a
    simulator has no microphone to have heard one with)
  - sending listen (state=detect) (the wake word is decided on the chip:
    ESP-SR runs there, the server takes no part in it, and a simulator has no
    microphone to have heard one with)
  - sending abort (abort is what a PWR press sends mid-reply, and there is no
    interactive path here to press anything from)
  - sending mcp (the hello omits features.mcp, so this board publishes no
    tools of its own: a simulated board has no volume, no screen and no
    battery to act on)
  - reading mcp (the server sends no mcp envelopes to a board whose hello
    omitted features.mcp, so there is nothing here to read)
```

### `vinga simulator run`

```
Usage: vinga simulator run [OPTIONS] {URL}

  check in to an OTA URL as a board would, then hold one conversation over the
  websocket: say the packaged sentence, and print the transcript and the reply
  as they arrive

Arguments:
  URL  the OTA URL to check in to: the address `vinga ota-url` prints inside the
       image, or the one already written into a board's NVS  [required]

Options:
  --mac MAC      the address this simulated board presents (default:
                 02:00:00:00:00:01, whose leading octet is the locally-
                 administered bit; a second board is 02:00:00:00:00:02)
  --claim AGENT  bind this board to an agent through the configuration API and
                 check in again to be issued a token; repeat the option for
                 several agents (default: print the code and the command to run)
  --config PATH  path to the YAML config file naming server.port and
                 server.api.secret_env (default: $VINGA_CONFIG)
  --api-url URL  base URL of the configuration API (default: $VINGA_API_URL,
                 then http://127.0.0.1:<server.port>/api)
  --force        answer the confirmation a destructive command asks at a
                 terminal, so it does not ask (default: it asks)
  --no-input     never prompt: a destructive command refuses rather than asking,
                 and a secret is read from stdin or --from-env (default: prompt
                 at a terminal)
  -h, --help     Show this message and exit.

What this simulator is and is not. Both directions, on this page, so that
nobody debugs a deployment believing this is a board. Every line below is read
out of one table, which is the same table the tests hold the command to.

Supported:
  - the check-in POST, with the two headers the handler reads and the body
    shape the firmware sends
  - the four states of the reply: activating, admitted, unwelcome, and a
    refusal for anything else
  - no redirect is followed, which is the firmware's own behavior and the
    reason every device-facing route serves the slashless spelling directly
  - the activation poll at Activation-Version 1, in the firmware's cadence of
    ten polls three seconds apart, bounded
  - claiming this board through the configuration API with --claim, and
    checking in again afterwards to be issued a token
  - the reply's firmware block, read and reported as a board reads it: whether
    an image was offered, and whether the version named back is the one this
    board announced
  - the websocket handshake with its Authorization, Device-Id, Client-Id and
    Protocol-Version headers; the last is sent because the firmware sends it
    and this server reads nothing from it
  - the hello exchange, announcing whichever framing version the check-in
    reply named, as a websocket text frame
  - one packaged utterance of Opus, paced the way a microphone delivers it and
    sent under the negotiated framing
  - binary reply frames, counted, size-checked and unwrapped, with the reply's
    duration computed from the frame count
  - the close, reported by its code compared against the closed set this side
    knows and named in this side's own words
  - one turn and one only: the reply is read to its end and then the socket is
    closed
  - sending hello
  - sending listen (state=start, mode=manual)
  - sending listen (state=stop, mode=manual)
  - reading hello
  - reading stt
  - reading tts (state=start)
  - reading tts (state=stop)
  - reading tts (state=sentence_start)

Not supported, and not planned:
  - a real microphone and speakers (they need PortAudio and a runtime encoder,
    a push-to-talk loop has no non-interactive path at all, and no CI runner
    has an audio device, so it would ship as a headline feature no lane could
    drive)
  - saying anything but the one packaged sentence (the audio is encoded once
    at build time so that what this sends is byte-identical on a laptop and on
    a runner; there is no codec in any tier to encode something else with)
  - echo cancellation and barge-in (the board's own AEC quality is the number
    the whole barge-in gate stack is built around and it is invisible from the
    server, and a simulator with no playback has nothing to cancel)
  - decoding or playing the reply audio (no codec ships in any tier, so what
    is reported about reply audio is arithmetic over frames rather than sound)
  - fetching and installing a firmware image (the block that offers one is
    read and reported, per the supported row above, and nothing is ever
    downloaded: there are no partitions here to write an image to and no
    bootloader to hand it to)
  - MQTT and UDP (vinga implements the websocket transport and promises no
    other, which is a bound of the compatibility promise itself)
  - Activation-Version 2 and its HMAC (the key is burned into a device's
    eFuses and only the vendor's cloud has a copy, which is equally true of
    every consumer board)
  - the display, the captive portal and NVS (this simulator is pointed at a
    URL rather than provisioned into one, so there is nothing to draw on and
    nothing to persist)
  - sending listen (state=start, mode=auto) (the device owns the listening
    mode, and auto re-arms itself after each tts stop, which is a second
    turn-taking design rather than a flag)
  - sending listen (state=start, mode=realtime) (realtime is the only mode
    barge-in exists in, and barge-in is built around the board's own echo
    cancellation, which a simulator with no playback has nothing to do)
  - sending listen (state=start) (this simulator always names the mode it is
    listening in, so a listen carrying no mode is one it does not send)
  - sending listen (state=stop, mode=auto) (the device owns the listening
    mode, and auto re-arms itself after each tts stop, which is a second
    turn-taking design rather than a flag)
  - sending listen (state=stop, mode=realtime) (realtime is the only mode
    barge-in exists in, and barge-in is built around the board's own echo
    cancellation, which a simulator with no playback has nothing to do)
  - sending listen (state=stop) (this simulator always names the mode it is
    listening in, so a listen carrying no mode is one it does not send)
  - sending listen (state=detect, mode=auto) (the wake word is decided on the
    chip: ESP-SR runs there, the server takes no part in it, and a simulator
    has no microphone to have heard one with)
  - sending listen (state=detect, mode=manual) (the wake word is decided on
    the chip: ESP-SR runs there, the server takes no part in it, and a
    simulator has no microphone to have heard one with)
  - sending listen (state=detect, mode=realtime) (the wake word is decided on
    the chip: ESP-SR runs there, the server takes no part in it, and a
    simulator has no microphone to have heard one with)
  - sending listen (state=detect) (the wake word is decided on the chip:
    ESP-SR runs there, the server takes no part in it, and a simulator has no
    microphone to have heard one with)
  - sending abort (abort is what a PWR press sends mid-reply, and there is no
    interactive path here to press anything from)
  - sending mcp (the hello omits features.mcp, so this board publishes no
    tools of its own: a simulated board has no volume, no screen and no
    battery to act on)
  - reading mcp (the server sends no mcp envelopes to a board whose hello
    omitted features.mcp, so there is nothing here to read)
```

### `vinga schema`

```
Usage: vinga schema [OPTIONS] [ENTITY] [STAGE] [TYPE]

  the JSON Schema of one entity, or of the whole domain half

Arguments:
  ENTITY  provider, mcp-server, prompt-fragment, agent, agent-defaults, mcp-
          grant, filler, memory, domain (default: domain)
  STAGE   with TYPE, the options of one provider type: llm, asr, tts or vad
  TYPE    with STAGE, the provider type whose options to print

Options:
  -h, --help  Show this message and exit.
```

### `vinga reference`

```
Usage: vinga reference [OPTIONS]

  the markdown reference, generated from the models

Options:
  -h, --help  Show this message and exit.
```

### `vinga openapi`

```
Usage: vinga openapi [OPTIONS]

  the configuration API's OpenAPI document, generated from its routes

Options:
  -h, --help  Show this message and exit.
```

### `vinga cli-reference`

```
Usage: vinga cli-reference [OPTIONS]

  the generated half of the CLI reference: the recipes read out of the example
  fragments, and every command's own help page

Options:
  -h, --help  Show this message and exit.
```
<!-- end generated: cli reference -->
