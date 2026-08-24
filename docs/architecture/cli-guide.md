# CLI guide

What vinga's command line looks like, and what a reviewer holds a new
command to. [`principles.md`](principles.md) says what vinga promises;
[`design-guide.md`](design-guide.md) says what a module inside one of
those promises looks like; this page says what a command looks like.
Read it before adding a command, a noun, a verb or a flag.

[`../reference/cli.md`](../reference/cli.md) is the other half of the
pair and answers a different question. That page is what the grammar
*is*, half written by hand and half generated from the command tree, so
it can never describe a grammar this server does not have. This page is
why the grammar is that shape, and it is the only one of the two a
person writes when they are designing rather than documenting.

Two conventions run through it.

**Every example is real.** A spelling shown here is one the CLI
answers to today, or one this guide marks as **owed**: a rule the
grammar does not satisfy yet, whose implementation belongs to a named
piece of work. Where the merged code actively contradicts a rule this
page states, that is a **tension**, recorded in place with the issue
that tracks it. An owed rule and a rule with a tension against it are
both still the standard. It is the reviewer's job to hold new commands
to them, and nobody's job to pretend the old ones already comply.

A counterexample is different from an example: it is the shape a rule
rejects, and every one of them below is labelled with where it comes
from. **Merged** means the code contains it or refuses it today,
**historical** means this repository once did it, and **constructed**
means it is written here to make the rule falsifiable rather than
reported as something that happened.

**The audit is recorded, not summarized.** The practices below were
arrived at by walking four published guides one guideline at a time and
dispositioning each as adopted, adapted or rejected. That record is at
the foot of the page, so a later reader can check what was considered
rather than trusting that it was.

## The two spellings

The same command has two spellings, and both appear in this
repository's documents.

```bash
vinga provider set llm local          # the console script
vinga-server config provider set llm local   # inside the image
```

`vinga-server` is the server's own entry point, and `config` is the
word that dispatches away from serving to configuring. It has three
siblings: `conversations`, `events` and `doctor`. `vinga` is the CLI as
a tool of its own, which the standalone-CLI work (#223) packages, and
it has no server to dispatch away from, so it drops the `config` word.
Everything after that word is identical, which is what makes the two
one grammar rather than two.

Until #223 lands, `vinga-server config ...` is what a checkout runs and
what every help page prints, so it is the spelling the reference uses.
This page uses the short one wherever the point is the grammar rather
than the invocation.

## The grammar

### Noun first, verb second

A command names the thing before it names what to do to it.

```bash
vinga provider set llm local
vinga agent show kids
vinga sessions list
```

This was settled on 2026-08-24 and is not reopened by a later command's
convenience. It is **owed**: today's grammar is verb first
(`vinga-server config set provider llm local`), and the re-cut that
turns it around is #223's. What already exists in the right shape is
the top level of the server's own entry point, where `conversations`
and `events` are nouns carrying their own command word
(`vinga-server conversations schema`, `vinga-server events
reference`), which is what the configuration kinds become.

### Why not verb first

kubectl is the famous verb-first CLI and it works, so the question is
what makes it work. kubectl has a small closed verb set (`get`,
`describe`, `apply`, `delete`, `logs`, and a handful more) that applies
uniformly to every resource in the cluster. A new resource kind arrives
with no new verbs at all: it inherits the whole set on the day it is
registered. Verb first is right when the verbs are the stable axis and
the nouns are the growing one.

docker started verb first and stopped. As noun-specific verbs
accumulated (`docker ps`, `docker images`, `docker rmi`, `docker rm`,
`docker inspect` across four different kinds of thing), the flat verb
list stopped being a set anybody could hold in their head, and 1.13
introduced the noun-verb management commands (`docker container ls`,
`docker image rm`) that everything since has been added under.

vinga has the docker shape, not the kubectl shape, and the evidence is
in its own command list. The core is uniform: `set`, `show` and
`export` apply to all five configuration kinds and `delete` to the four
that can be deleted, and every one of those rows is built from the
descriptor registry rather than written out.
The periphery is not: `set-secret` and `clear-secret` exist for two
kinds and not the other three, the devices have two binding verbs of
their own, and an agent has a prompt nothing else has. The noun set is
growing too, and growing faster than the verb set: the conversation
store work (#190) adds `sessions` and `conversations`, each with verbs
of its own.

Verb first does not force a compound word for each of those; what it
forces is a choice, taken one command at a time, and the merged grammar
has taken it both ways. `set-secret` grew a noun level under itself
(`set-secret provider llm claude api_key`), while `bind-device` welded
the noun into the verb. That is the real cost, and it is not
hypothetical: one grammar, two shapes for the same relationship, with
nothing to tell a reader which shape the next command will use. Under
noun first there is one shape, and the top level is a list of things
rather than a list of things-and-actions. What each peripheral verb ends
up spelled as is the re-cut's to settle; what this page fixes is the
rule it settles them against.

**Example.** `vinga provider set llm local` and `vinga provider delete
llm local`: one noun, two verbs, and a third verb arriving for
providers alone changes nothing about any other noun's page.

**Counterexample, merged.** It is this grammar's own top level. The
command listing of `vinga-server config --help` reads `set`, `delete`,
`bind-device`, `add-device`, `apply`, `pending`, `status`, `prompt`,
`reload`, `ota-url`, `set-default-agent`, `clear-default-agent`,
`set-secret`, `clear-secret`, `list`, `schema`, `reference`, `openapi`,
`cli-reference`, `show`, `export`. Twenty-one words in one list, in
which a verb that applies to everything, a verb that applies to one
kind, a noun-verb compound and a document renderer are typographically
indistinguishable, and the only way to learn which is which is to read
all of them.

### Identity addressing

The **leading** words after the verb are not arguments in the sense the
published guides mean. They are the address of one row, in the order
the API's own URL uses.

```bash
vinga provider set llm local     # providers.llm.local
vinga device bind aa:bb:cc:dd:ee:ff assistant
```

`llm` is the stage and `local` is the name, and they are positional for
the same reason `/api/providers/llm/local` has them in that order: they
are what makes the entry one entry. The CLI does not choose them.
`_identity` in `config/cli.py` reads them off the entity descriptor's
`addressing` tuple, which is also what builds the URL path, so a kind
addressed by two segments on the API is addressed by two segments on
the command line and cannot come to differ.

**Not every positional is an address, and the code says so.** In the
second line above, the MAC addresses the request and `assistant` is
the body of it: `bind-device` declares `AGENT` as a variable-length
positional, so `vinga device bind aa:bb:cc:dd:ee:ff kids guest` binds
one board to two agents. `Invocation` separates the two in its own
field list, with `stage`, `name`, `mac`, `code` and `slot` under "what
addresses one entry" and `agents`, `file` and `pairs` under "the rest
of what a command can carry". The `KEY=VALUE` pairs a `set` takes are
the same shape: a payload, positional, and as many as the entity has
fields.

The rule that keeps that from becoming the mess the published guides
warn about is homogeneity, and it is the one both clig.dev and 12
Factor state: several arguments of *one* kind read fine, and two
arguments of *different* kinds do not. So a command may carry at most
one payload group; it comes last, after the whole address; every
element of it is the same kind of thing; and anything heterogeneous is
a flag instead. `-f/--file` is a flag precisely because a document is
not one more agent.

This is why the published rule of thumb (one argument is fine, two are
questionable, three are never good) is adapted here rather than
adopted. That rule is about *options wearing positional clothes*, where
`fork sourceapp destapp` leaves a reader unable to say which is which.
An identity segment carries no such ambiguity: the order is the
resource's own, and it is printed in the help, in the URL and in the
generated reference identically.

What the rule caps here is identity depth, and it counts address
segments only: a payload group is not one of them, however many words
it runs to. Three segments under a verb is the floor of what is already
needed (`set-secret provider llm claude api_key` addresses a stage, a
name and a slot, and its route is
`/providers/{stage}/{name}/secrets/{slot}`) and it is the ceiling. A
fourth segment means the noun is wrong: something in the middle of that
address is a thing in its own right and should be the noun.

**Example.** `vinga mcp-server set home -f examples/mcp-server-stdio.yaml`.
One identity segment, one flag carrying the body, and the body is a
document rather than a pile of options.

**Counterexample, constructed.** The same command with its address
demoted to options: `vinga set --kind provider --stage llm --name local
-f ...`. It is longer, it is order-independent in a way nobody needed,
and it breaks the correspondence with the URL that keeps the two
surfaces from drifting.

### The flat system verbs

A verb with no noun in front of it is one that acts on the whole
deployment, or on nothing stored at all. Those stay at the top level.

```bash
vinga apply -f deployment.yaml
vinga export > deployment.yaml
vinga reload
vinga schema provider asr faster_whisper
vinga ota-url
```

Two groups, for two reasons. `apply`, `export`, `reload` and the
reserved `diff` (#193) act on the configuration as a whole: their
subject is the deployment, and inventing a noun to put in front of them
would be inventing a word (`deployment apply`) that names the thing the
program is already about. `schema`, `reference`, `openapi`,
`cli-reference` and `ota-url` render a document out of the models, the
routes, the command tree or the file half, and reach no database, no
key and no server at all; they have no stored subject to be a verb of.

A verb that has both a whole-deployment form and a per-entity form
keeps the flat spelling for the whole and moves under the noun for the
one: `vinga export` beside `vinga provider export llm local`.

**Example.** `vinga reload`. It asks the running server to re-read the
store. There is no noun because it does not reload a provider, it
reloads the server.

**Counterexample, constructed.** `vinga config apply`, adding a noun for
symmetry with the noun-verb commands. It reads as though there were some
other kind of apply, and there is not.

### Naming a new noun, naming a new verb

- **A noun is the configuration's own word for the thing.** `provider`,
  `mcp-server`, `prompt-fragment`, `agent`, `agent-defaults`, `device`.
  Where the store calls a section `prompt_fragments`, the command word
  is `prompt-fragment`, and the help says which section it is
  (`create or replace prompt_fragments.<name>`) so the two are visibly
  the same thing.
- **Singular when it addresses one entry, plural when the noun is a
  collection you only ever ask about as a whole.** `provider set llm
  local` is one provider; `sessions list` is not one session.
- **Lowercase, and kebab-case where more than one word is
  unavoidable.** No underscores in a command word, ever, even where the
  store's key has one.
- **A verb comes from the core set where the core set will do**: `set`,
  `show`, `export`, `delete`, `list`. Reaching for a synonym (`create`,
  `update`, `print`, `dump`) when one of those is what is meant makes
  two words for one act.
- **A noun-specific verb is allowed, and is the whole point of noun
  first, but it has to be a verb.** A noun in the verb slot reads as a
  possessive and hides what the command does.
- **A description is one lowercase sentence with no full stop, inside
  80 columns.** This is what every row in `GROUPS` and `COMMANDS`
  already does, and the width is `REFERENCE_WIDTH`, because the help
  pages are a committed artifact that CI diffs byte for byte.
- **Derive the verb set where the kinds are uniform.** The `set`,
  `delete`, `show` and `export` rows are generated from
  `entities.ENTITIES`, and each row's help is written by
  `_about(verb, kind)` from the descriptor's own `location`. A kind
  cannot come to be described one way in the help and another way in
  the reference, because there is one description.

One case is open rather than settled, and it is recorded here rather
than decided: `prompt` is the one command word in today's grammar that
is a noun. Under noun first it lands as `agent prompt kids`, which
reads as a possessive rather than an action. Either it earns an
exception (an agent's prompt is a thing you ask for, and the noun slot
is doing the work) or it gets a verb. The re-cut decides; the rule it
is decided against is the one above.

## The practices

Each is stated with an example from the merged CLI and the shape it
rejects, so that a reviewer can hold a command to it rather than to a
feeling. Every counterexample says where it comes from: **merged** if
the code contains it or refuses it today, **historical** if this
repository once did it, and **constructed** if it is written here to
make the rule falsifiable. A constructed counterexample is not a weaker
rule, it is an honest label. Where a practice is not met today, or the
merged code contradicts it, the practice says so.

### Data on stdout, notices on stderr

Stdout carries the thing a caller came for. Stderr carries everything
about the run that produced it: when a write takes effect, what to run
next, and the one sentence a failure gets.

The line is not prose against data. It is **about the artifact** against
**about this invocation**. A document that explains itself is still the
artifact: an export's header says how to reproduce the deployment in
two steps, and its foot lists a `set-secret` command per stored slot,
both as YAML comments, and both belong in the file because the file is
what somebody opens six months later with no terminal scrollback to
consult. `apply` reads it back with the comments in it.

The test is therefore redirection in both directions.
`vinga export > deployment.yaml` must produce a file that `apply` takes
as it stands and that tells its own reader what it is, what it does not
carry and what to run after it; and nothing about *that run* may be in
it.

**Example.** `_exported` composes `EXPORT_HEADER`, the configuration,
and `_secret_commands`, all to stdout, and `_secret_commands` emits the
stored locations in the store's own fixed order so that two exports of
one configuration are the same bytes. `ota-url` splits the other way:
the URL alone on stdout, because it is what gets pasted into a captive
portal, and what to do with it plus where its origin came from on
stderr. `_acknowledged` prints `wrote provider llm.claude` on stdout
and the take-effect notice on stderr, and it flushes stdout first,
because stderr is unbuffered and stdout is not, so without the flush
the notice would land above the line it is about.

**Counterexample, constructed.** An export header saying how many
entities were written and when. That is about the invocation, so it
belongs on stderr if anywhere, and putting it in the document breaks the
property `_secret_commands` is written to keep: two exports of an
unchanged configuration would stop being the same bytes, and a
checked-in export would show a diff on every run.

### A refusal is a fixed sentence that quotes nothing back

The standard: every failure is a sentence this codebase wrote, no
sentence repeats what was typed, no sentence relays a body that did not
come from vinga's own sanitized output, every URL a sentence names is
passed through `shown_url` first, and nothing leaves through a
traceback.

The reason is not tidiness. The values passing through this CLI are a
bearer token that grants everything the API can do, provider
credentials, and an OTA URL that is itself a deployment's secret. A
message that echoes an argument echoes those, onto a terminal, into
shell history, and into whatever collects stderr. The surface is wider
than the message, too: an exception raised while another is being
handled carries the first on `__context__`, and httpx's exceptions
carry the request while Click's carry the argument list, which is why
this module builds its sentences inside the handler and raises them
after it.

**Example.** `_usage_problem` translates Click's usage errors to fixed
sentences **by exception class**, which is the reading that cannot be
fooled by wording, and falls back to a deliberately vague sentence for
a shape it has not seen, because a message this code has not seen is a
message that may carry a value. An unrecognized answer from the network
is reported as a status code plus "a body this client does not
recognize", never quoted, because what a proxy or a captive portal
returns is not this API's output.

**Counterexample, historical.** The pre-Typer argparse grammar passed
argparse's own `invalid choice: 'x'` through verbatim, echoing the typed
word, while every sibling grammar in this repository already refused to.
The #194 rebuild closed it by translating Click's `UsageError`
subclasses by class, and recorded the strengthening in the changelog.
What made it worth closing is the mistake that produces that sentence
most often: typing the value after `set-secret ... api_key`.

**Tension recorded: two merged paths do not meet this standard.** Both
were found by holding this page against `cli.py`, which is what a
written standard is for, and neither is fixed here, because a
documentation change is the wrong place to change a refusal path.

- **Rejected input is echoed on two paths, and one of them can escape
  as a traceback** (#289). `_file` names the fragment path it was given
  and the library's `strerror`, and `_read_secret` names the variable
  `--from-env` pointed at. Worse, `_file` catches `FileNotFoundError`
  and `OSError`, and a file that is not UTF-8 raises
  `UnicodeDecodeError`, which is a `ValueError`: it escapes the
  boundary entirely, as a traceback whose exception retains the buffer
  it failed to decode.
- **An accepted URL reaches later refusals unsanitized** (#290).
  `_permitted` computes `shown` and uses it only in the refusals it
  raises itself, then returns the URL it was given. `_sent` and
  `_unreadable` interpolate that raw value, so an `https://` address
  carrying a secret in a query parameter is printed on stderr whenever
  the connection fails or the answer cannot be read. Userinfo is
  refused outright, so this is the query string and the path, which the
  policy does not inspect.

Both are owed against the standard above, not licence to weaken it. A
new command is held to the whole of it.

### One sentence and exit 1, and asking for help is not a failure

A command that worked exits 0. A command that did not prints one
sentence on stderr and exits 1. `--help` exits 0, because asking is not
failing.

Uniformity is the point. Four command groups (`config`,
`conversations`, `events`, `doctor`) share this shape, including for
mistakes in the grammar itself: each drives its parser inside the
boundary rather than letting the library print and exit on its own,
because a failure that bypassed the boundary would bypass the
sanitizing with it.

**The exception, stated because it is real.** The root `vinga-server`
dispatch answers its own usage errors, and an unrecognized first word,
with exit 2. That is what argparse has always answered a usage error
with, and nothing scripted around this entry point should learn a new
number from a change about what is printed. So the contract is: 0, 1,
and 2 from the root dispatch alone.

**Counterexample, constructed.** A code per failure kind (3 for
unreachable, 4 for unauthorized, 5 for a validation error). It is in two
of the four audited guides and it is rejected below, in the audit, with
the reason.

### A write says what it did and when it takes effect

There are no implicit steps, and the corollary is that there is no
implicit *timing* either. A stored write and a running server are two
different clocks, and a command that changed the first says which.

**Example.** Five sentences in `config/entities.py`, one per answer,
each a fact of what was written rather than of the command that wrote
it. `RELOAD_NOTICE` names three clocks rather than one: an in-progress
conversation meets new tools at its next utterance and new prompt text
at its next activation, while a new voice reaches the next
conversation. A sentence saying "immediately" would have been wrong
about all three. `BINDING_UNSERVED_NOTICE` exists because a binding
whose agent this server is not serving yet is true two ways at once,
and neither of the other sentences would have been honest. `apply`
prints each distinct notice once, because a document that wrote nine
entities is waiting on one reload, not nine.

**Counterexample, constructed.** A single "written" line. The operator
finds out at the next field test that the board is still speaking in the
old voice, and has no way to know whether that is a bug or a boundary.

### A credential is never an argument, and never travels in a read

**Example.** `set-secret` reads the value from stdin, without echo when
stdin is a terminal, or from the variable `--from-env` names. The
`MASK` shown in a read is a fixed eight characters rather than the
value's length, because a mask that tracks the length is a length
oracle. An `export` carries no credential at all: what it carries is
the `set-secret` command for each stored slot, as comment lines, which
is also why rebuilding a deployment is two steps in a stated order.
Every `set` help page carries the sentence saying an inline `key=value`
is the wrong place for one, ahead of the field list. And the transport
policy refuses plain HTTP to anything but a loopback address with no
flag to override it, because the token crosses every request.

The rule underneath is one function, `is_secret_option`, with three
readers: it is what makes an inline value in a fragment an error, what
decides which names are credential slots, and what the display path
masks. One rule rather than three lists is the design guide's locality
rule applied to the thing it would hurt most to get inconsistently
right.

**Counterexample, merged.** `vinga provider set llm claude
api_key=sk-...`, which is refused by the shape of the key whichever way
the entity was written. Arguments land in shell history and in the
process list, where a value cannot be taken back.

### One machine-readable shape, and it is the document `apply` takes

The machine interface is not a serialization mode bolted onto a human
one. It is a round trip: `export` emits exactly what `apply` consumes,
so the automation story is "read it, edit it, write it back" rather
than "parse our display format".

Where a listing is not a document it is a borderless table: a header
row, columns padded with spaces, one entry per line, which greps and
counts with `wc -l`. Where the fields are lists rather than scalars it
is blocks instead, because a column holding a list is a column that
wraps and stops being one line per entry.

**Example.** `vinga export > deployment.yaml` followed by
`vinga apply -f deployment.yaml` reproduces a deployment. `pending`
prints five short columns, header included, because the question it
answers ("which of these boards is the one I am holding") is read
across a line. `status` prints blocks, because two of its three fields
are lists of names.

**Counterexample, constructed.** `_status_listing` rendered as columns.
The tools a server published would wrap, and the one-entry-per-line
property that makes the pending listing greppable would be gone.

#### The `--json` question, deferred

Three of the four audited guides ask for a `--json` output flag. It is
deferred rather than adopted, and the case is recorded here so that
adopting it later is a decision rather than a drift.

- **The read output is already machine-readable, and already
  specified.** It is YAML, and `docs/reference/domain-config.md` is
  generated from the models that define it. A second serialization
  would not add machine-readability; it would add a second format of
  it.
- **A second format is a second no-leak audit.** Every field that
  renders has to honour the masking and the no-leak discipline
  independently per format: the fixed-length mask, the credential that
  never travels in a read, `printable`'s bounding of anything an answer
  contains. Two renderers means those properties are proven twice or
  true once.
- **No consumer needs it.** The admin UI consumes the configuration API
  directly, not the CLI, and the API's JSON is already specified by
  `docs/reference/api-openapi.json`. An integration wanting JSON has a
  better door than a CLI subprocess.
- **JSON is already emitted where the artifact is JSON.** `schema` and
  `openapi` print JSON, because a JSON Schema and an OpenAPI document
  are JSON. That is the shape of the artifact, not a mode of the
  reader.

What would change the answer: a real consumer that cannot parse YAML
and cannot reach the API. Then it is its own issue, with the per-format
no-leak audit priced into it, and `--json` is the name to use, because
it is the name all three guides use.

### Output is deterministic, and an answer cannot steer a terminal

Two properties, one rule: what a command prints depends on the request
and the stored state, never on the terminal it is printing into, and
never on the bytes an answer happened to carry.

**Example.** The generated help pages are rendered through a context
with `terminal_width` and `max_content_width` stated and `color=False`,
because CI diffs them byte for byte and a page that wrapped differently
on a laptop and on a runner would fail its own drift check on an
unrelated change. `printable` truncates a value first and then replaces
every unprintable character with a question mark, so no answer can
choose how long a command's output is or put an escape sequence into
it. `_granted` sorts by agent name, so two reads of an unchanged world
print the same block.

**Counterexample, constructed.** Color, spinners, emoji or ASCII art in
anything a document is generated from. All four audited guides recommend
some of those, and each is rejected in the audit below for this one
reason.

**Owed.** The one animation this rule would allow is a progress line
for the two long waits (`apply`, which has no bound at all, and
`reload`, which has a sixty-second one), on stderr and only when stderr
is a terminal, which is exactly where the rule permits it: nothing that
lands in a file, nothing that reaches a redirected stream.

### Prompt where there is somebody to ask, and never require it

An interactive prompt is a convenience for the person at the keyboard,
and it must never be the only way to supply a value, because everything
here has to be scriptable.

**Example.** `_read_secret` resolves in three steps: `--from-env` if it
was given, then a no-echo prompt if stdin is a terminal, then a plain
read of stdin, which is what a pipe and a script use. The same value,
three ways in, none of them mandatory.

**Counterexample, merged.** One function below that one. `_stdin` reads
standard input unconditionally, so `apply -f -` typed at a terminal
blocks with no prompt and no explanation: the same rule broken from the
other side, by never asking whether there is anybody there. The
published answer, and the standard here, is to print the help and quit
when a command that expects a pipe is run interactively. Owed.

**Owed too.** A destructive verb has no confirmation today:
`vinga-server config delete agent kids` deletes without asking, whether
or not anybody is watching. The standard is a confirmation when stdin is
a terminal, `--force` (or `--yes`) to skip it, and no prompt at all when
stdin is not a terminal, so a script is never blocked by one. A
`--no-input` flag that disables every prompt at once belongs with it.

### Where to reach, in a stated order, with no flag that weakens it

Resolution order is published, short, and the same everywhere: the
flag, then the environment variable, then a default derived from
configuration this deployment already has.

**Example.** The API address is `--api-url`, then `VINGA_API_URL`, then
`http://127.0.0.1:<server.port>/api` with the port read from the same
YAML file the server was started with, so the two cannot disagree about
it. The token is the variable `server.api.secret_env` names, read from
that same file, and a missing one is a sentence naming the variable
printed before any request is sent, rather than a 401.

**Counterexample, merged as a refusal.** An `--insecure` or
`--no-verify-tls` flag. The bearer token grants everything the API can
do and rides on every request, so such a flag's only purpose would be
sending it in clear. `_permitted` refuses plain HTTP to a non-loopback
host and says in the refusal that there is deliberately no override.

### Bound every wait that has a bound, and write down why one does not

**Example.** The connect timeout is five seconds always, because a
server that is not there must say so quickly. The read timeout is
thirty, chosen with margin above the database's ten-second busy
timeout, so that the retryable 409 the server answers with survives as
an answer instead of becoming a client-side transport error that says
nothing about what happened. `reload` gets sixty, derived from the
server's own envelope. `apply` gets none, and the comment says why: the
transaction validates the whole resulting configuration, whose size
nothing about the request bounds, so no finite number can be derived
that would not sometimes expire on a transaction the server goes on to
commit, which is the one outcome every timeout here exists to prevent.

**Counterexample, merged as a rejected default.** Leaving the HTTP
library's five-second default in place. It is below the database's busy
timeout, so it would turn exactly that retryable answer into a transport
error, replacing "nothing was changed, run the command again" with a
sentence that says nothing about what happened. That is why all four of
these numbers are named constants with a paragraph attached rather than
defaults nobody chose.

### The grammar is derived from the model it addresses

Two structures that must agree are one structure with a bug pending.
This is the design guide's rule, and the command tree is where it bites
hardest, because a grammar is exactly the kind of thing that gets
written out by hand.

**Example.** The `set`, `delete`, `show` and `export` rows are built by
looping over `entities.ENTITIES`; each row's addressing comes from the
descriptor's `addressing` tuple, which is also the URL's path
parameters; each row's help comes from `_about(verb, kind)` reading the
descriptor's `location`; and a `set` page's field list is rendered from
the same `Field(description=...)` values the markdown reference and the
JSON Schema come from. The generated half of `cli.md` then walks that
tree, and CI fails on any difference from the committed copy.

**Counterexample, historical.** The frozensets this CLI used to carry
(`PENDING_FIELDS`, `STATUS_FIELDS`, and ten predicates walking a body
key by key), a second encoding of models the API already declared, with
nothing connecting the two. That is a worked example in
[`design-guide.md`](design-guide.md), and it started life as a
reasonable-looking piece of defensiveness.

## The audit record

Four published guides, walked one guideline at a time. Every guideline
in each source has a row, so the coverage is checkable rather than
asserted. Where two sources say the same thing, the second names the
practice the first was dispositioned into rather than repeating the
reasoning.

Dispositions: **Adopted** (the rule holds as written), **Adapted** (the
rule holds in a modified form, and the row says how), **Owed** (adopted
as the standard, not met by the grammar today), **Rejected** (the rule
is deliberately not followed, and the row says why), **N/A** (out of
scope for a configuration CLI).

### ThoughtWorks, "Elevate developer experiences with CLI design guidelines"

Eight guidelines.

| # | Guideline | Disposition | Where |
| --- | --- | --- | --- |
| 1 | Be consistent in structure and follow common naming; `platform-cli [noun] [verb]` | Adopted | The settled decision; owed until the #223 re-cut |
| 2 | Prompt if you can, but never mandate; confirmation prompts for critical actions; a force flag | Adopted, partly owed | Prompt where there is somebody to ask; the confirmation half is owed |
| 3 | Use expressive flags; one argument fine, two questionable, three never | Adapted | Identity addressing: the cap applies to identity segments, not to flags |
| 4 | Avoid implicit steps; inform or split the command | Adopted | A write says what it did and when it takes effect; `apply` never deletes; a write never reloads by itself |
| 5 | Always provide help: command, arguments and flags described, examples most read | Adopted, examples owed | The whole tree's help is the committed reference; examples are the recipes region, not the command pages |
| 6a | Exit nonzero if and only if the program terminated with errors | Adopted | One sentence and exit 1 |
| 6b | stdout for information and warnings, stderr for errors | Adapted | Data on stdout, notices on stderr: warnings and notices go to stderr here, following the other three sources, because a notice must survive `export > file` |
| 6c | Error messages carry an error code, title, description, resolution steps and a URL | Rejected | The contract is one fixed sentence carrying the fix. A code is a second vocabulary to keep honest; a URL dates and cannot be reached from a private deployment; and the five-part format invites quoting the input back, which is the one thing these sentences exist not to do |
| 7 | Keep the user in the loop: current step, long-run indicator, OS notifications | Partly adopted, partly owed, partly rejected | `apply` prints one line per entry as it reports them; the progress line is owed; OS notifications are rejected as a desktop assumption a container CLI does not have |
| 8 | Be fun and fancy: color, spinners, tables, machine-readable output | Split | Tables and machine-readable output adopted; color, ASCII art and emoji rejected under output determinism |

### clig.dev, "Command Line Interface Guidelines"

Ninety-six guidelines, in the document's own section order.

| # | Guideline | Disposition | Where |
| --- | --- | --- | --- |
| 1 | Use a command-line argument parsing library | Adopted | Typer over Click, driven with standalone mode off so refusals stay ours |
| 2 | Zero exit code on success, non-zero on failure | Adopted | One sentence and exit 1 |
| 3 | Send output to stdout | Adopted | Data on stdout, notices on stderr |
| 4 | Send messaging to stderr | Adopted | Same |
| 5 | Display extensive help text when asked | Adopted | Every command's `--help`, and the whole tree as the committed reference |
| 6 | Display concise help text by default | Adopted | One lowercase sentence per row in the command listing |
| 7 | Show full help on `-h` and `--help` | Owed | `--help` only today; `-h` is unbound |
| 8 | Provide a support path for feedback and issues | Rejected for now | Pre-release, self-hosted, no support channel to name. The refusals carry the fix instead of a channel |
| 9 | Link to the web version of the documentation in help | Adapted | Help names the command that prints the document (`Full descriptions: vinga-server config schema provider`), because the documents ship with the CLI and a URL would date |
| 10 | Lead with examples | Adapted, partly owed | Examples are the generated recipes region of the reference, read out of `vinga-server/examples/`; examples on the command pages are owed |
| 11 | Put loads of examples somewhere else | Adopted | The recipes region and `vinga-server/examples/` |
| 12 | Most common flags and commands at the start of the help | Adopted | The command listing order is the table's, restored after Typer's own ordering |
| 13 | Use formatting in your help text | Rejected | `rich_markup_mode=None` and `color=False`: the help pages are a committed artifact CI diffs byte for byte |
| 14 | If you can guess what they meant, suggest it | Rejected | A suggestion is built from the typed word. Closed deliberately in the #194 review rounds; see the refusal practice |
| 15 | A command expecting a pipe, run at a TTY, shows help and quits | Partly adopted, partly owed | `set-secret` prompts instead, which is better; `-f -` blocks, which is owed |
| 16 | Provide web-based documentation | Adopted | `docs/reference/cli.md` |
| 17 | Provide terminal-based documentation | Adopted | `--help`, plus `schema`, `reference`, `openapi`, `cli-reference` |
| 18 | Consider providing man pages | N/A | The deployment surface is a container image |
| 19 | Human-readable output is paramount | Adopted | One machine-readable shape (which is also the readable one) |
| 20 | Machine-readable output where it does not hurt usability | Adopted | Same |
| 21 | `--plain` when human output breaks machine output | Rejected | There is one rendering, with no color, no borders and no animation in it, so there is nothing for a plain mode to strip |
| 22 | Display formatted JSON if `--json` is passed | Deferred | The `--json` question, above |
| 23 | Display output on success, but keep it brief | Adopted | `wrote provider llm.claude` |
| 24 | If you change state, tell the user | Adopted | A write says what it did and when it takes effect |
| 25 | Make it easy to see the current state of the system | Adopted | `list`, `show`, `status`, `pending`, `prompt` |
| 26 | Suggest commands the user should run | Adopted | `NOTHING_CONFIGURED`, `NOTHING_PENDING`, the reload notice, the OTA guidance, and the `set-secret` lines an export writes |
| 27 | Actions crossing the boundary of the program's world should be explicit | Adopted | `reload` is a verb an operator runs; a write never reloads on its own |
| 28 | Increase information density with ASCII art | Rejected | Output determinism |
| 29 | Use color with intention | Rejected | Output determinism |
| 30 | Disable color when not in a terminal or when asked | N/A | Nothing colors |
| 31 | No animations when stdout is not an interactive terminal | Adopted in advance | The owed progress line is stderr-only and TTY-only |
| 32 | Use symbols and emoji where they make things clearer | Rejected | Output determinism, and `printable` maps anything unprintable to `?` |
| 33 | Do not output information only the creators understand | Adopted | Refusals are operator sentences; the exception chain is suppressed on the way out |
| 34 | Do not treat stderr like a log file | Adopted | Stderr carries notices and one refusal sentence; the structured JSON log is the server's surface, not the CLI's |
| 35 | Use a pager for a lot of text | Rejected | The long outputs are documents meant to be redirected (`export > deployment.yaml`); a pager on a redirected stream is noise, and on a captured one is a hang |
| 36 | Catch errors and rewrite them for humans | Adopted | Every boundary raises `ConfigError` with a written sentence |
| 37 | Signal-to-noise ratio is crucial | Adopted | One sentence |
| 38 | Consider where the user will look first | Adopted | The sentence is the last thing on stderr |
| 39 | For unexpected errors, provide debug and traceback information | Rejected | Deliberately: an httpx exception carries the request URL and a Click context carries the argument list, so a traceback is where a token or a secret would surface. The no-leak posture outranks the debugging convenience. #289 is the case where one escapes anyway, recorded as a tension against the refusal practice |
| 40 | Make it effortless to submit bug reports | N/A | Pre-release; see 8 |
| 41 | Prefer flags to args | Adapted | Identity addressing |
| 42 | Have full-length versions of all flags | Adopted | `--config`, `--api-url`, `--file`, `--from-env` |
| 43 | Only use one-letter flags for commonly used flags | Adopted | `-f` is the only one |
| 44 | Multiple arguments are fine for simple actions against multiple things | Adopted | `bind-device <mac> <agent>...` takes a variable-length agent list |
| 45 | Two or more arguments for different things is probably wrong | Adopted | It is the rule that governs payload positionals: one group, last, homogeneous, and anything heterogeneous is a flag. The identity segments in front of it are capped separately, at three |
| 46 | Use standard names for flags where a standard exists | Adopted | `-f/--file`, and the owed `--force`; `--json` is reserved rather than renamed |
| 47 | Make the default the right thing for most users | Adopted | The API address defaults to loopback on the port the file half names, which is the in-container case |
| 48 | Prompt for user input | Adopted | `set-secret` at a terminal |
| 49 | Never require a prompt | Adopted | `--from-env` and stdin |
| 50 | Confirm before doing anything dangerous | Owed | No confirmation on `delete` today |
| 51 | Support `-` to read from stdin or write to stdout | Adopted | `-f -` |
| 52 | If a flag takes an optional value, allow a word like "none" | Adapted | `default_agent: null` in a document; the command form is a verb of its own (`clear-default-agent`) rather than a magic value |
| 53 | Make arguments, flags and subcommands order-independent where possible | Adopted | `--config` and `--api-url` are accepted before and after the command word, and a value given before it survives a command that was not given one |
| 54 | Do not read secrets directly from flags | Adopted | A credential is never an argument |
| 55 | Only prompt if stdin is an interactive terminal | Adopted | `isatty` decides between the no-echo prompt and a plain read |
| 56 | If `--no-input` is passed, do not prompt | Owed | With the confirmation prompts |
| 57 | Do not print a password as it is typed | Adopted | `getpass` |
| 58 | Let the user escape | Adopted | Nothing is trapped; Ctrl-C is the interpreter's |
| 59 | Be consistent across subcommands | Adopted, mechanically | The rows are generated from the descriptor registry |
| 60 | Use consistent names for multiple levels of subcommand | Adopted | The noun word is spelled the same under every verb |
| 61 | Do not have ambiguous or similarly-named commands | Tension recorded | `bind-device` and `add-device` are two ways to bind one board, told apart only by their help text. Noun first is where this is fixed: they are two verbs of `device`, addressed by a MAC and by an activation code |
| 62 | Validate user input | Adopted | The same pydantic models validate a write and the read of the answer |
| 63 | Responsive is more important than fast | Owed | The progress line |
| 64 | Show progress if something takes a long time | Owed | Same |
| 65 | Do stuff in parallel where you can | N/A | One command is one request |
| 66 | Make things time out | Adapted | Bound every wait that has a bound: one act has none, with the reason written down |
| 67 | Make it recoverable | Adopted | `apply` is one transaction refused whole; the documented recovery is reading the store back, and the rebuild path is a section of the reference |
| 68 | Make it crash-only | Adopted | The client holds no state between runs; every command is one request |
| 69 | People are going to misuse your program | Adopted | `printable`, the URL policy, the secret-never-an-argument sentence |
| 70 | Keep changes additive where you can | Adapted | Pre-release: nothing is owed to the current grammar, and what survives a re-cut does so on merit. The compatibility floor vinga does promise is the database's, not the CLI's |
| 71 | Warn before you make a non-additive change | Adapted | The changelog records grammar changes and the drift checks make them visible in review; there is no deprecation cycle before the first beta |
| 72 | Changing output for humans is usually OK | Adopted | With the generated-document drift checks as the mechanism that makes it visible |
| 73 | Do not have a catch-all subcommand | Adopted | An unrecognized first word is a fixed sentence naming the four groups |
| 74 | Do not allow arbitrary abbreviations of subcommands | Adopted | Click matches a command word exactly |
| 75 | Do not create a time bomb | Adopted | Nothing in the CLI expires. Activation codes expire on the server, and the empty listing says so |
| 76 | On Ctrl-C, exit as soon as possible | Adopted | Nothing is trapped |
| 77 | On Ctrl-C during clean-up, skip it | N/A | The CLI has no clean-up phase. The server does exactly this on its drain |
| 78 | Follow the XDG spec | Rejected | The deployment surface is a container, and the configuration file is named by `--config` or `VINGA_CONFIG` so the server and the CLI cannot disagree about which one it is. A home-directory default would be a second answer to that question |
| 79 | Ask consent before modifying configuration that is not yours | Adopted trivially | The CLI writes only through the API, into vinga's own store, and touches no file |
| 80 | Apply configuration parameters in order of precedence | Adopted | Where to reach, in a stated order |
| 81 | Environment variables are for behavior that varies with context | Adopted | `VINGA_API_URL`, `VINGA_CONFIG`, the token variable |
| 82 | Uppercase, numbers and underscores only | Adopted | |
| 83 | Aim for single-line values | Adopted | |
| 84 | Avoid commandeering widely used names | Adopted | Everything vinga defines is `VINGA_`-prefixed; the provider credential variables are named by the operator's own configuration |
| 85 | Check general-purpose environment variables where possible | N/A | None apply. `NO_COLOR` would, if anything colored |
| 86 | Read environment variables from `.env` where appropriate | Adopted | `load_dotenv(find_dotenv(usecwd=True))` at the entry point, with the real environment winning |
| 87 | Do not use `.env` as a substitute for a configuration file | Adopted | The file half is the configuration; `.env` carries variables only |
| 88 | Do not read secrets from environment variables | Rejected, tension recorded | The API token and `--from-env` are both environment reads, deliberately. On a container deployment the alternatives are a file on disk or an argument, and both are worse. The environment is how a credential is handed over once; the encrypted store is where it lives |
| 89 | Make it a simple, memorable word | Adopted | `vinga`, which the rename delivers |
| 90 | Use only lowercase letters, and dashes if you need them | Adopted | |
| 91 | Keep it short | Adopted | |
| 92 | Make it easy to type | Adopted | |
| 93 | Distribute as a single binary if possible | Adapted | The image ships the CLI, which is the intended path. A tool of its own is #223's; there is no published package yet, and the reference says so plainly rather than implying one |
| 94 | Make it easy to uninstall | Adopted | `uv tool uninstall`, or deleting the container |
| 95 | Do not phone home usage or crash data without consent | Adopted, and stronger | Nothing phones home at all |
| 96 | Consider alternatives to collecting analytics | N/A | See 95 |

### Heroku CLI style guide

Thirty-five rules. The Node-specific dependency rules are grouped at
the end.

| # | Rule | Disposition | Where |
| --- | --- | --- | --- |
| 1 | The CLI is for humans before machines | Adopted | |
| 2 | Input and output consistent across commands, so users learn new ones | Adopted | The grammar is derived from the model it addresses |
| 3 | Topics are plural nouns; commands are verbs | Adapted | Singular where the noun addresses one entry (`provider set llm local`), plural where it is a collection (`sessions list`) |
| 4 | Plugins export a single topic | N/A | No plugin system, and none planned |
| 5 | Topic and command names are a single lowercase word without delimiters | Adapted | Kebab-case where more than one word is unavoidable, which rule 7 allows |
| 6 | Colons delineate subcommands (`heroku pg:credentials:repair-default`) | Rejected | Spaces. Heroku's technical reason is that a topic-level command taking an argument becomes ambiguous under spaces; vinga's one word that is both a group and a command (`show`) takes no positional argument, so the ambiguity does not arise, and a space-separated tree is what the generated reference walks |
| 7 | Kebab-case if multiple words are unavoidable | Adopted | `mcp-server`, `prompt-fragment`, `agent-defaults` |
| 8 | The root command of a topic lists those nouns | Adopted | `list` and `show`; under noun first, `sessions list` |
| 9 | Never create a `*:list` command | Adapted | `list` here is a whole-configuration summary tree, not a topic's listing. Where a noun's only verb is a listing (`sessions list`), the noun word alone prints help rather than data, which is what the rule's premise assumes away |
| 10 | Descriptions for all topics and commands | Adopted | Every row in `GROUPS` and `COMMANDS` |
| 11 | Descriptions fit 80-column screens | Adopted | `REFERENCE_WIDTH` |
| 12 | Descriptions begin with a lowercase character | Adopted | |
| 13 | Descriptions do not end in a period | Adopted | |
| 14 | Flags are preferred to arguments | Adapted | Identity addressing |
| 15 | Descriptions for all flags | Adopted | |
| 16 | Flag descriptions lowercase | Adopted | |
| 17 | Flag descriptions concise, for narrow screens | Adopted | |
| 18 | Flag descriptions do not end in a period | Adopted | |
| 19 | Arguments acceptable when there is one, or when they are obvious and in an obvious order | Adopted | The identity order is the URL's path order, printed identically in both places |
| 20 | Use inquirer for prompts | N/A | Node-specific |
| 21 | Prompting must never be required; args or flags bypass it | Adopted | Prompt where there is somebody to ask |
| 22 | Output commands print to stdout | Adopted | |
| 23 | Action commands show a spinner, on stderr, with a non-TTY fallback | Partly owed | The stderr half already holds for notices; the spinner is the owed progress line, and the non-TTY fallback is the rule it will be built to |
| 24 | Color is encouraged; standard colors per noun | Rejected | Output determinism |
| 25 | Color disabled by `--no-color`, `COLOR=false`, or a non-TTY | N/A | Nothing colors |
| 26 | Human-readable output should be grep-parseable; tables without borders | Adopted | The pending listing |
| 27 | `--json` when tables grow too long to fit | Deferred | The `--json` question |
| 28 | After general availability, do not change inputs and stdout in ways that break scripts | Adapted | Pre-release; see clig 70 |
| 29 | Offer `--json` and/or `--terse` where valuable | Deferred | Same |
| 30 | Stdout for all output | Adopted | |
| 31 | Stderr for warnings, errors and out-of-band information | Adopted | |
| 32 | No native dependencies | N/A | Node-specific. The equivalent holds by accident: the CLI is the server package, and the argument layer added exactly one dependency |
| 33 | Be judicious with dependencies | Adopted in spirit | |
| 34 | Use dev dependencies for what is only needed to work on it | Adopted | The `dev` dependency group |
| 35 | Discouraged dependencies (request, underscore) | N/A | Node-specific |

### 12 Factor CLI Apps

Twelve factors.

| # | Factor | Disposition | Where |
| --- | --- | --- | --- |
| 1 | Great help is essential: in-CLI and web, every spelling shows it, examples matter most | Adopted, partly owed | See clig 5 to 17. One deliberate deviation: `vinga-server config` with nothing after it is a mistake in the grammar, not a request for help, so it answers with a sentence pointing at `--help` and exit 1, the way every other mistake does |
| 2 | Prefer flags to args; one type fine, two suspect, three never; support `--` | Adapted | Identity addressing; `--` is accepted, and the reference's rebuild section uses it |
| 3 | Make the version reachable several ways | Owed | There is no `--version`. The running server answers `version` and `revision` on `/health` and in the OTA reply, and stamps both on every session record, while the CLI cannot be asked at all, which is the wrong way round for the thing an operator has in their hand |
| 4 | Mind the streams: stdout is for output, stderr is for messaging | Adopted | Data on stdout, notices on stderr |
| 5 | Handle things going wrong: informative errors, a traceback or debug mode, error logs without ANSI | Split | Informative fixed sentences adopted; the traceback and debug-dump half rejected, per clig 39 |
| 6 | Be fancy: colors, spinners, OS notifications, with fallbacks and `NO_COLOR` respected | Rejected, except the owed progress line | Output determinism |
| 7 | Prompt if you can, never require | Adopted | |
| 8 | Use tables: one entry per row, no borders, plus `--columns`, `--no-truncate`, `--no-headers`, `--filter`, `--sort`, csv and json | Split | One entry per row and no borders adopted, and the pending listing is exactly that. The six table flags are rejected: they are a query language over an answer that is already small and already a document, and `grep`, `wc` and a YAML parser cover it |
| 9 | Be speedy | Adapted | What is pinned is import weight, by a test, and the offline commands open no database, need no key and reach no server. Nothing else is measured, and no startup budget is claimed |
| 10 | Encourage contributions | Adopted | MIT, public repository, upstream licence notices kept |
| 11 | Be clear about subcommands: multi-command, list them when given nothing, colons over spaces | Split | Multi-command adopted; spaces over colons (Heroku 6); listing on no arguments deliberately not done (factor 1) |
| 12 | Follow the XDG spec | Rejected | See clig 78 |

## What a reviewer holds a new command to

A short list, in the order the questions come up in review. Each is a
practice above, restated as the question to ask.

1. **Is it noun first?** Does the noun already exist, and is the verb
   the core-set word if the core set will do?
2. **If it introduces a noun**, is that the configuration's own word
   for the thing, lowercase, kebab-case, and singular or plural
   according to whether it addresses one entry?
3. **Do the leading positionals form the address**, in the same order
   and with the same names the API's path uses, and are there at most
   three of them? If a payload follows, is there exactly one group of
   it, is it last, and is every element the same kind of thing?
4. **Is everything it prints on stdout about the artifact, and
   everything on stderr about the run?** A document that explains
   itself is still the artifact; a count of what this invocation did is
   not.
5. **Does it say when what it wrote takes effect**, in the words the
   kind's own notice uses rather than new ones?
6. **Can any sentence it prints contain something the caller typed, or
   something the network handed back?** Both are no.
7. **Could a credential reach an argument** on any path through it,
   including a mistyped one?
8. **Is every wait it makes bounded**, and if one is not, is the reason
   written down where the constant is?
9. **Is its description one lowercase sentence with no full stop, and
   does its help page render the same on every machine?**
10. **Is any part of it written twice?** A verb list, a field name, a
    section name or an address that also exists on a model is derived
    from that model, not restated.
11. **If it destroys something**, does it confirm at a terminal and
    take `--force`? (Owed today, and a new destructive verb is where
    the debt gets paid rather than grown.)

And the rule that outranks the list: this page never outranks
[`principles.md`](principles.md). A command that reads beautifully and
breaks a product promise is a command that is wrong.
