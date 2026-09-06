# CLI guide

What vinga's command line looks like, and what a reviewer holds a new
command to. [`product-promises.md`](product-promises.md) says what
vinga promises;
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
giving each a disposition from a fixed vocabulary of eight words. That
record is [`cli-guide-audit.md`](cli-guide-audit.md), dated and kept
whole, so a later reader can check what was considered rather than
trusting that it was; the short version is at the foot of this page.

## On this page

- [What a reviewer holds a new command
  to](#what-a-reviewer-holds-a-new-command-to): the sixteen questions a
  new command answers, each linked to the rule behind it. This page's
  interface; everything after it is the reasoning.
- [The two spellings](#the-two-spellings): `vinga` and
  `vinga-server config`, one grammar, and which one this page uses.
- [The grammar](#the-grammar): noun first and why, identity
  addressing, the flat system verbs, naming a noun or a verb, and how
  deep the tree may go.
- [The practices](#the-practices): the sixteen rules a command is held
  to, each with an example from the merged CLI and the shape it
  rejects.
- [The sources, and what became of
  them](#the-sources-and-what-became-of-them): the four published
  guides this was audited against, and where the row-by-row record
  lives.

## What a reviewer holds a new command to

A short list, in the order the questions come up in review. Each is a
practice below, restated as the question to ask, and linked to the
section that carries its reasoning.

1. **Is it noun first?** Does the noun already exist, and is the verb
   the core-set word if the core set will do?
   ([Noun first, verb second](#noun-first-verb-second),
   [Naming a new noun, naming a new
   verb](#naming-a-new-noun-naming-a-new-verb))
2. **If it introduces a noun**, is that the configuration's own word
   for the thing, lowercase, kebab-case, and singular or plural
   according to whether it addresses one entry?
   ([Naming a new noun, naming a new
   verb](#naming-a-new-noun-naming-a-new-verb))
3. **Do the leading positionals form the address**, in the same order
   and with the same names the API's path uses, and are there at most
   three of them? If a payload follows, is there exactly one group of
   it, is it last, and is every element the same kind of thing?
   ([Identity addressing](#identity-addressing), and
   [How deep the tree goes, and
   why](#how-deep-the-tree-goes-and-why) for a level between the noun
   and the verb)
4. **Is everything it prints on stdout about the artifact, and
   everything on stderr about the run?** A document that explains
   itself is still the artifact; a count of what this invocation did is
   not.
   ([Data on stdout, notices on
   stderr](#data-on-stdout-notices-on-stderr))
5. **Does it say when what it wrote takes effect**, in the words the
   kind's own notice uses rather than new ones?
   ([A write says what it did and when it takes
   effect](#a-write-says-what-it-did-and-when-it-takes-effect))
6. **Does it print only what has something to say?** An empty list, a
   false flag and a section of nothing are absent rather than
   enumerated, and a run with nothing to report says so in one fixed
   sentence rather than printing nothing at all.
   ([An answer prints what has something to
   say](#an-answer-prints-what-has-something-to-say))
7. **Does it answer in the words that were typed**, rather than in the
   field names of the layer that did the work? And where it knows the
   boundary set, does its own line replace the server's sentence rather
   than following it?
   ([An answer speaks the verb that was
   typed](#an-answer-speaks-the-verb-that-was-typed))
8. **If it changes something, does it say that it worked**, in one line
   of its own on stderr, with no wall-clock number in it?
   ([An action that succeeds says so](#an-action-that-succeeds-says-so))
9. **Is a boundary stated once over the group it is true of**, rather
   than once per kind or once per entry?
   ([A boundary is stated once per run, over the
   group](#a-boundary-is-stated-once-per-run-over-the-group))
10. **Does it volunteer advice about a feature this deployment is not
    using?** That one is no: a feature's own noun answers questions
    about it.
    ([A command volunteers no advice about features not in
    use](#a-command-volunteers-no-advice-about-features-not-in-use))
11. **Can any sentence it prints contain something the caller typed, or
    something the network handed back?** Both are no.
    ([A refusal is a fixed sentence that quotes nothing
    back](#a-refusal-is-a-fixed-sentence-that-quotes-nothing-back))
12. **Could a credential reach an argument** on any path through it,
    including a mistyped one?
    ([A credential is never an argument, and never travels in a
    read](#a-credential-is-never-an-argument-and-never-travels-in-a-read))
13. **Is every wait it makes bounded**, and if one is not, is the reason
    written down where the constant is?
    ([Bound every wait that has a bound, and write down why one does
    not](#bound-every-wait-that-has-a-bound-and-write-down-why-one-does-not))
14. **Is its description one lowercase sentence with no full stop, does
    its help page render the same on every machine, and does any
    terminal-dependent behavior it has leave the non-terminal path
    complete?**
    ([Naming a new noun, naming a new
    verb](#naming-a-new-noun-naming-a-new-verb) for the description,
    [Output is deterministic, and an answer cannot steer a
    terminal](#output-is-deterministic-and-an-answer-cannot-steer-a-terminal)
    for the rest)
15. **Is any part of it written twice?** A verb list, a field name, a
    section name or an address that also exists on a model is derived
    from that model, not restated.
    ([The grammar is derived from the model it
    addresses](#the-grammar-is-derived-from-the-model-it-addresses))
16. **If it destroys something**, does its row say so, so that it
    confirms at a terminal and takes `--force`? And is it destructive
    by the line the practice draws, rather than merely alarming?
    ([Prompt where there is somebody to ask, and never require
    it](#prompt-where-there-is-somebody-to-ask-and-never-require-it))

And the rule that outranks the list: this page never outranks
[`product-promises.md`](product-promises.md). A command that reads
beautifully and breaks a product promise is a command that is wrong.

## The two spellings

The same command has two spellings, `vinga` and `vinga-server config`,
and both appear in this repository's documents. Which is which, what
each entry point is for, what a live `--help` prints and why every
generated document carries the short one whatever rendered it are in
[`../reference/cli.md`](../reference/cli.md#the-two-spellings), which
is where the current spellings belong.

What matters here is the one property the pair has: everything after
the dispatching word is identical, which is what makes the two one
grammar rather than two. This page uses the short spelling wherever
the point is the grammar rather than the invocation.

## The grammar

### Noun first, verb second

A command names the thing before it names what to do to it.

```bash
vinga provider set llm local
vinga agent show kids
vinga session list
```

This was settled on 2026-08-24 and is not reopened by a later command's
convenience. #223's re-cut turned the grammar around, so it is the
merged shape rather than an owed one: the five configuration kinds are
nouns carrying their own verbs, beside `conversations` and `events` on
the server's own entry point, which already had it
(`vinga-server conversations schema`, `vinga-server events
reference`).

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
The periphery is not: stored credentials have verbs that two of the
kinds carry and the other three do not, the devices have two binding
verbs of their own, and an agent has a prompt nothing else has. Which
peripheral verbs exist at any moment is
[`../reference/cli.md`](../reference/cli.md#every-command)'s to say,
since that half is generated from the command tree; what the argument
needs is only that the list is uneven and keeps growing. The noun set
is growing too, and growing faster than the verb set: the conversation
store work (#190) adds `session` and `conversation`, each with verbs
of its own.

Verb first does not force a compound word for each of those; what it
forces is a choice, taken one command at a time, and the grammar before
the re-cut had taken it both ways. `set-secret` grew a noun level under
itself (`set-secret provider llm claude api_key`), while `bind-device`
welded the noun into the verb. That was the real cost, and it was not
hypothetical: one grammar, two shapes for the same relationship, with
nothing to tell a reader which shape the next command would use. Under
noun first there is one shape, and the top level is a list of things
rather than a list of things-and-actions. Each peripheral verb's
spelling is settled below, against the rules on this page.

**Example.** `vinga provider set llm local` and `vinga provider delete
llm local`: one noun, two verbs, and a third verb arriving for
providers alone changes nothing about any other noun's page.

**Counterexample, historical.** It was this grammar's own top level
until #223. The command listing of `vinga-server config --help` read
`set`, `delete`,
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
the body of it: `device bind` declares `AGENT` as a variable-length
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
needed (`provider secret set llm claude api_key` addresses a stage, a
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
vinga info
vinga import -f deployment.yaml
vinga export > deployment.yaml
vinga diff
vinga apply
vinga schema provider asr faster_whisper
vinga ota-url
```

Two groups, for two reasons. `info`, `import`, `export`, `diff` and
`apply` act on the configuration as a whole: their subject is the
deployment, and inventing a noun to put in front of them would be
inventing a word (`deployment import`) that names the thing the program
is already about. `schema`, `reference`, `openapi`,
`cli-reference` and `ota-url` render a document out of the models, the
routes, the command tree or the file half, and reach no database, no
key and no server at all; they have no stored subject to be a verb of.

`info` (#341) is in the first group and is the clearest case of what
puts a word there. What it answers is which deployment this is: the
address the CLI reached, the version and revision of the build that
answered, the URL a board is onboarded at, and a count per kind. Every
one of those is a fact of the whole, so a noun in front of it
(`deployment info`, `server info`) would name the thing the program is
already about, and there is no per-entity form of the question for a
noun to be earned by.

A verb that has both a whole-deployment form and a per-entity form
keeps the flat spelling for the whole and moves under the noun for the
one: `vinga export` beside `vinga provider export llm local`.

**Example.** `vinga apply`. It installs the stored configuration on the
running server. There is no noun because it does not apply a provider,
it applies the deployment.

**Counterexample, constructed.** `vinga config import`, adding a noun
for symmetry with the noun-verb commands. It reads as though there were
some other kind of import, and there is not.

**Counterexample, historical.** `vinga-server config status`, flat until
#341 and quoted in the spelling it had while it was. Its subject was
never the deployment: what it says is what each configured MCP server is
doing, which is a verb of one noun, and that noun was already in the
tree. What put it at the top level is that it arrived before the top
level was a list of things. It is `vinga mcp-server status` now, with no
alias behind it.

### Naming a new noun, naming a new verb

- **A noun is the configuration's own word for the thing.** `provider`,
  `mcp-server`, `prompt-fragment`, `agent`, `agent-defaults`, `device`.
  Where the store calls a section `prompt_fragments`, the command word
  is `prompt-fragment`, and the help says which section it is
  (`create or replace prompt_fragments.<name>`) so the two are visibly
  the same thing.
- **Singular when it addresses one entry, plural when the noun is a
  collection you only ever ask about as a whole.** `provider set llm
  local` is one provider; `conversations schema` renders one document
  about a store and addresses no entry of anything. Revised on
  2026-08-28 by
  [the first-class conversations plan](../plans/2026-08-28-first-class-conversations.md),
  which is where the reasoning is: this page presented `sessions list`
  as the spelling before either noun existed, and #190's nouns take
  `show` and `delete`, so under this rule they are `session` and
  `conversation`. The two merged plural groups (`conversations`,
  `events`) address no entry and keep their names under the same
  rule.
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
  80 columns.** The width is `REFERENCE_WIDTH`, because the help pages
  are a committed artifact that CI diffs byte for byte. Every row in
  `GROUPS` and `COMMANDS` meets it, `apply` included since #223
  normalized the one row that carried three sentences, and a test holds
  every row and every group to it rather than a reviewer's eye.
- **Derive the verb set where the kinds are uniform.** The `set`,
  `delete`, `show` and `export` rows are generated from
  `entities.ENTITIES`, and each row's help is written by
  `_about(verb, kind)` from the descriptor's own `location`. A kind
  cannot come to be described one way in the help and another way in
  the reference, because there is one description.

One case was open rather than settled, and #223 settled it. `prompt` was
the one command word in the old grammar that was a noun, and under noun
first it would have landed as `agent prompt kids`, which reads as a
possessive rather than an action. **It got a verb, and the verb is
`preview`.** The exception was not taken, because a rule with an
exception at the first command that asks it is unenforceable, and
`pending` and `status` were both queued behind it, and both landed under
a noun in the end: `device pending`, and `mcp-server status` since #341.
`preview` is this
repository's own word for the act: the design guide, describing exactly
this route, says it *previews what an agent would be sent*. And it pairs
with `show`, which is what noun first is for: `agent show kids` prints
what is stored, `agent preview kids` prints what a new session would be
sent, and stored against assembled is a real distinction an operator
has to make. The ambiguity objection ("preview what?") is answered by
the help string and by an agent having exactly one previewable thing.

### How deep the tree goes, and why

Two commands need a level between the noun and the verb, and inventing
a rule per command is how the old grammar came to have two shapes for
one relationship. The rule is derived instead, from the paths the
grammar already mirrors:

> **A path segment followed by an identity of its own is a sub-noun. A
> trailing segment with no identity after it is an attribute of its
> parent, and reading an attribute is a verb on the parent.**

It is the rule above it ("the grammar is derived from the model it
addresses") applied to tree depth rather than to row content, which is
the one place that rule was not yet reaching. Its three worked cases
are the three it was derived from.

- `/providers/{stage}/{name}/secrets/{slot}`: `secrets` is followed by
  `{slot}`, so `secret` is a sub-noun of `provider`, with `set` and
  `clear` as its verbs. Identity depth is three, which is the floor of
  what is needed and also the ceiling.
- `/devices/pending/{code}`: `pending` is followed by `{code}`, so
  `pending` is a sub-noun of `device`, with `claim` as its verb. The
  listing `GET /devices/pending` is `list` on the same sub-noun, which
  is what makes `device pending list` a verb command rather than the
  adjective-in-the-verb-slot `device pending` would have been.
- `/runtime/agents/{name}/prompt`: `prompt` is trailing with no
  identity, so it is an attribute, and it becomes a verb on `agent`.

**Counterexample, constructed.** `agent prompt show kids`, which
introduces a sub-noun for a sub-thing with exactly one verb. A sub-noun
is earned by an identity under it, and that route has none.

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
three steps, and its foot lists a `secret set` command per stored slot
under a line saying where in those steps they go, all as YAML comments,
and all of it belongs in the file because the file is what somebody
opens six months later with no terminal scrollback to consult. `import`
reads it back with the comments in it.

The test is therefore redirection in both directions.
`vinga export > deployment.yaml` must produce a file that `import` takes
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
most often: typing the value after `secret set ... api_key`.

**Two merged paths did not meet this standard, and both are closed.**
Both were found by holding this page against `cli.py`, which is what a
written standard is for, and neither was fixed in the change that wrote
it down, because a documentation change is the wrong place to change a
refusal path. They are recorded here because what the standard caught
is the argument for keeping it written.

- **Rejected input was echoed on two paths, and one of them escaped as
  a traceback** (#289, fixed). `_file` named the fragment path it was
  given and the library's `strerror`, and `_read_secret` named the
  variable `--from-env` pointed at. Worse, `_file` caught
  `FileNotFoundError` and `OSError`, and a file that is not UTF-8 raises
  `UnicodeDecodeError`, which is a `ValueError`: it escaped the
  boundary entirely, as a traceback whose exception retains the buffer
  it failed to decode. Both are one table of fixed sentences read by
  exception class now, with the classes the boundary catches read off
  that same table, and the parse failure that named the path calls the
  file what this module calls it.
- **An accepted URL reached later refusals unsanitized** (#290, fixed).
  `_permitted` computed `shown`, used it only in the refusals it raised
  itself, and returned the URL it was given. `_sent` and `_unreadable`
  interpolated that raw value, so an `https://` address carrying a
  secret in a query parameter was printed on stderr whenever the
  connection failed or the answer could not be read. Userinfo is
  refused outright, so this was the query string and the path, which
  the policy does not inspect. An accepted address travels as an
  `Address` now, what is reached and what may be shown, and every
  sentence that names one reads the second.

Neither was licence to weaken the standard, and a new command is held
to the whole of it.

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

**A bare invocation is answered with its own help page** (#341). `vinga`
on its own, and every noun with no verb after it (`vinga provider`,
`vinga device pending`), prints the page it was one word short of, on
stderr, and exits 1. Three things are being held together there. The
reader is not making a mistake about the grammar, they are asking to
see it, so what they get is the grammar rather than a sentence telling
them to ask a second time. Stdout stays data-only, per [Data on stdout,
notices on stderr](#data-on-stdout-notices-on-stderr): this invocation
produced no data, so nothing goes there, and a pipe does not fill with
a help page. And the exit code still says what happened, which is that
no command was typed; a 0 would say one completed. `--help` is the
other invocation and keeps stdout and 0, because asking for help is not
a failure. The library's own no-args-help is deliberately off, so the
decision is made in one place: it sees only the invocation where nothing
at all followed, and `vinga --api-url URL` named no command either and
is owed the same page.

**And the invocation is recognized by type, never by wording.** The
group raises the one exception class that means "no command was named",
and the boundary answers that class. Reading it off the text of a
library's error would be reading something a caller can type: `vinga
"Missing command"` is an unknown command whose name is the phrase the
library uses, and it must meet the refusal every other unknown command
meets. The rule generalizes past this one case: a boundary that decides
what to print by matching words in a message has made the message an
input, and every practice on this page about not quoting the caller
back is undone by it.

**The exception, stated because it is real.** The root `vinga-server`
dispatch answers its own usage errors, and an unrecognized first word,
with exit 2. That is what argparse has always answered a usage error
with, and nothing scripted around this entry point should learn a new
number from a change about what is printed. So the contract is: 0, 1,
and 2 from the root dispatch alone.

**Counterexample, constructed.** A code per failure kind (3 for
unreachable, 4 for unauthorized, 5 for a validation error). It is in two
of the four audited guides and it is rejected in
[the audit](cli-guide-audit.md), with the reason.

### A write says what it did and when it takes effect

There are no implicit steps, and the corollary is that there is no
implicit *timing* either. A stored write and a running server are two
different clocks, and a command that changed the first says which.

**Example.** Seven sentences in `config/entities.py`, one per answer,
each a fact of what was written rather than of the command that wrote
it. `APPLY_NOTICE` says the write is stored and not yet serving, and
the boundary it is waiting at travels beside it as `applies`, a token
of the same closed set the comparison read publishes.
`BINDING_UNSERVED_NOTICE` exists because a binding whose agent this
server is not serving yet is true two ways at once, and neither of the
other sentences would have been honest, which is why the token is a set
rather than a word. `DEFAULT_AGENT_UNSERVED_NOTICE` is the same pair of
boundaries about the other live row, and it exists because the binding's
sentence was printed over a document that bound nothing (#424): a
sentence has to be true of the row that was written, not only of the
boundary it waits at. `import` says the whole of it in one line, because
a document that wrote nine entities is waiting on one apply, not nine.

**The sentence states and the client speaks** (#386, #426). None of the
seven names a command. A server ships in an image and a client is
installed beside it, so a command spelling in a sentence the server
composes is advice about a grammar the server does not own and cannot
see change: an image built before a verb rename told an operator to run
a command the client no longer had, and both halves were internally
consistent. So the boundary is what travels, as a token, and whichever
side can answer the whole question answers it once. The CLI can wherever
it knows the set: `cli.SPOKEN` is keyed by the boundary set and holds
this grammar's own words, and its line **replaces** the server's
sentence rather than following it, naming the state first and the
command after it (`vinga apply` installs the stored configuration,
`vinga diff` lists what is pending). The spelling is then on the side
that owns the word and inside the command-spellings census's reach,
where a rename that missed it fails a test in the same checkout. A set
the client cannot name, which is what a boundary from a newer server
arrives as, and a set with nothing to run about, are both answered with
the server's sentence quoted alone: an unknown state is quoted, never
guessed at.

**The sentence is as short as the boundary allows** (#371). The three
clocks an installed change converges at (tools at the next utterance,
prompt text at the next activation, voice at the next conversation) are
true and are published, in `vinga apply --help` and in the domain-config
reference. They are not in the per-write sentence, because that sentence
is printed once per entry of every domain-half write: a Quick Start run
printed it six times, and a paragraph worth reading once is a wall of
text at that count. What a per-write notice carries is what the operator
acts on now.

**Counterexample, constructed.** A single "written" line. The operator
finds out at the next field test that the board is still speaking in the
old voice, and has no way to know whether that is a bug or a boundary.

**A verb does what its name says, and the rule has two merged
instances** (#341, #371). The rule: when a verb's plain meaning and what
it does come apart, move the command rather than the reader.

**The first instance** (#341). `apply` wrote the document and stopped,
so every operator learned to type `reload` after it and the ones who did
not left a deployment serving what it had before. The reading taken then
was that applying a configuration means installing it, so the verb was
widened to do both and the narrower behavior became a flag,
`--no-reload`, named for what it turned off. The half of the rule that
sentence added, give the narrower behavior a flag rather than giving the
wider one a second verb, is the half the second instance retired.

**The second instance** (#371). A verb doing two acts is a verb that has
to be described twice, and every consequence #341 derived was a
consequence of the second act: a rendering that dropped the boundary
notice because the install's listing followed it, a sentence explaining
what an answered write behind an unanswered install may honestly claim,
and a flag to turn the second act off. So the pair was cut the other
way. `apply` narrowed to the act it names, which is installing what is
stored, and the write it used to do first moved to a verb whose plain
meaning is exactly that write: `import`. `--no-reload` was deleted
rather than renamed, because a write-only `import` needs no flag to stay
write-only. The names now pair: `import`/`export` are the store's
document I/O, `diff`/`apply` are the store-versus-running-server
reconciliation.

What both instances share is the move: the command changed so that the
reader would not have to. What #371 adds to the rule is that a flag is
the answer only when the two behaviors are one act with a narrower
scope. When they are two acts, they are two commands.

**Counterexample, merged and retired.** `apply` as write-then-install,
under one name. It read as one operation, and it needed a flag, a second
rendering and a sentence about a half-finished pair to stay honest about
being two.

### An answer prints what has something to say

An empty list, a false flag and a section with nothing in it are absent,
not enumerated. Absence is absence, and a column of `(none)` is
something an operator reads in order to learn nothing.

The determinism objection is answered by what the filter reads. What is
printed is a function of the answer, so two renders of one answer are
the same bytes and [Output is deterministic, and an answer cannot steer
a terminal](#output-is-deterministic-and-an-answer-cannot-steer-a-terminal)
holds unchanged: filtering by state is not varying by terminal, and the
state is what the reader came to ask about.

The one thing absence may not mean is silence. A run with nothing at all
to say says that in one fixed sentence, because empty output reads as a
command that failed to answer rather than as one with nothing to report,
and an empty deployment is exactly what somebody following Getting
Started is looking at.

**Example.** Three renderings and three sentences, all merged under
#426. `_diff_listing` prints a line for a kind with names in a list or a
flag that is true, and `SERVING_THE_STORE` where no kind has either.
`_apply_listing` does the same over the outcome sections, with
`NOTHING_DIFFERED` for an apply that moved nothing; a section the build
answered null keeps its `NOT_APPLIED` line, because "this build does not
touch this kind" is content rather than emptiness, and a kind that went
silently missing would read as one with nothing to report.
`_configured_counts` drops a kind whose count is zero and answers
`configured: nothing yet` for a store nothing has been written to.

**Two absences are deliberately not filtered**, and they are where this
rule stops. `info` says `no devices` and `no default agent` rather than
dropping them, because an unbound board reaching no agent is the fact an
operator is hunting for rather than an empty field to hide. And the
`(none)` in the `list` tree and in the MCP status blocks stays, as does
the `-` a session or memory row prints for a field with nothing in it:
each of those answers a per-row question somebody asked, rather than
enumerating an empty change set.

**Counterexample, historical** (#426). `vinga info` printed the tally as
a block whatever was in it, so what a person got the first time they ran
it was six rows of nothing:

```
configured:
  providers: 0
  mcp_servers: 0
  prompt_fragments: 0
  agents: 0
  devices: 0
  default_agent: (none)
```

It is one line now, and on that deployment the line is `configured:
nothing yet`.

### An answer speaks the verb that was typed

The operator ran `import`, so the answer says `imported`. It does not
say `wrote`, `stored` and `resynthesized`, which are what the layers
underneath call what each of them did.

A field name is the vocabulary of the side that did the work, and it is
the right vocabulary there: `fallback_resynthesized` names what the
reload layer rebuilt, it is documented, and clients other than this one
read it. What it is not is an answer to a question a person asked in
their own words about a document they wrote themselves.

**Example.** `_imported_entries` answers a whole document in the verb
that was typed, in one line: the act, the count, and the state the
entries are in.

```
imported 4 entries, not serving yet: run `vinga apply`
```

`APPLY_LABELS` sits beside `APPLY_SECTIONS` and maps (section,
field) to the operator's phrase for that outcome, so
`fallback_resynthesized: assistant` is read out as `failure phrase
spoken again: assistant`; the table is keyed by the pair because one
word means two things in two sections, and a completeness pin keyed off
the models holds it total, so a field added to the contract is a failing
test rather than a line that quietly goes missing. Both sides keep their
own words: no response model moved for any of this, and
`docs/reference/api-openapi.json` is the document it was.

**The boundary half of this rule is stated once, above.** Where the
server's sentence states a boundary this client knows, the client's line
replaces it, and where it does not, the sentence is quoted alone; the
mechanism, and why the command spelling has to be on this side, is [A
write says what it did and when it takes
effect](#a-write-says-what-it-did-and-when-it-takes-effect)'s "the
sentence states and the client speaks", and is not restated here.

**Counterexample, historical** (#424). Importing Getting Started's
document answered on stderr with the server's sentence and the client's
advice under it, once per distinct boundary set, wrapped as a terminal
wraps it and saying nothing about what had just been run:

```
This is stored and not yet serving: the running server goes on serving
what it already has until the stored configuration is installed on it.
`vinga apply` installs the stored configuration on the running server,
and `vinga diff` lists everything pending.
The binding applies at the device's next OTA check or connection, but
this server is not serving the agent it names yet: the agent arrives
with the install that adds it, and the device reaches it at the check-in
after that.
`vinga apply` installs the stored agents.
```

Four lines of prose to say "run `vinga apply`", in nobody's words but
the two programs' own. `The binding` is the other half of #424: that
sentence was the device binding's, reused for the `default_agent` write
an operator's document had just made, so it named a row the document
never contained. It became a seventh sentence in `config/entities.py`
rather than a reworded shared one, for the reason the comment above
those sentences gives.

### An action that succeeds says so

In one line of its own, on stderr, because "it worked" is a fact about
this invocation rather than about the deployment.

A listing that stops is not a statement that anything worked. A reader
cannot tell a command that finished from one that printed what it had
and then went quiet, and the shorter the listing the worse that reads:
an apply that moved one thing, or nothing at all, is where the whole
answer is a line or two and the full stop is the only thing saying the
install happened.

**Example.** `_applied` is the apply's own render callable rather than
`_printed`: the listing to stdout, a flush, then `INSTALLED` on stderr,
which says the stored configuration is installed and serving. The flush
is the discipline `_acknowledged` and `_imported_entries` already
document, since stderr is unbuffered and stdout is not, and without it
the success could land above the listing it is about on a merged
terminal. The import's count line is the same shape on the same stream,
and the split is the one [Data on stdout, notices on
stderr](#data-on-stdout-notices-on-stderr) draws: the outcome listing is
the artifact and the success is about the run.

**And no wall-clock number in it** (#426). A slow act's elapsed time is
the progress line's job, `narrated`, which draws whole seconds at a
terminal and writes no byte anywhere else, under the licence in [Output
is deterministic, and an answer cannot steer a
terminal](#output-is-deterministic-and-an-answer-cannot-steer-a-terminal).
A duration in retained output would make two runs against one stored
state different bytes, and time is not state. A deployment that wants
durations in what it keeps is asking an events question rather than a
rendering one.

**Counterexample, historical** (#426). `vinga apply` printed twenty
lines of outcome fields, then the MCP status block, and stopped:

```
mcp:
  started: weather
  restarted: (none)
  stopped: (none)
  unchanged: home
prompts:
  changed: house
fillers:
  resynthesized: (none)
  reused: house, kids
  disabled: (none)
providers:
  built: (none)
  reused: asr.ears, llm.local, tts.voice, vad.gate
  retired: (none)
agents:
  added: house
  removed: (none)
  defaults_changed: no
```

Nothing in it says the command did what it was asked to do, and the
operator most in need of hearing so is the one whose deployment had
little to install.

### A boundary is stated once per run, over the group

Every kind in one group of an answer is waiting at the same boundary, so
the boundary is said once over the group rather than once per kind: a
label repeated down a column stops being read after the second row, and
what it was telling the reader is a fact about the group rather than
about the row.

Once per group, not once per answer, because an answer can carry more
than one. The kinds waiting on an install are one group and a kind
waiting at the next server start is another, and each says its own
boundary once. What the rule forbids is the same boundary said twice in
one answer, not two boundaries each said once.

**Example.** `HEADS` gives each boundary this client's own words for it,
and `_diff_listing` prints one head per boundary present, in the order
`Applies` declares them, with one line per kind underneath. Only one of
those heads names a command, because only one boundary has one:
`INSTALLS` is where that spelling lives, once, and the reload head and
the write-side lines both read it, while the restart head says what
crosses it, which is a server starting. The import's count line
carries `NOT_SERVING_YET` for the whole document rather than a clause
per entry, and carries it once however many entries wait, because the
two boundary sets this client knows are waiting on that same install.

`READ_AS_ASKED` is the deliberate exception and is not a boundary being
restated: it says why the two live kinds are never in a group at all,
which is a question about every comparison rather than about this one's
state.

**Counterexample, historical** (#425). `vinga diff` labelled every kind
with its own boundary and defined the vocabulary in a preamble printed
on every run. The state Getting Started's step 3 leaves behind has three
changes in it, and this is what they were answered with:

```
# what the stored configuration would change on the running server. `applies`
# says when a change of that kind reaches a conversation: `reload` when `vinga apply`
# next installs the stored configuration, `check-in` as a device next asks, and
# `restart` at the next server start.
providers: applies at reload
  added: asr.whisper, llm.local, tts.voice, vad.ears
  removed: (none)
  changed: (none)
mcp_servers: applies at reload
  added: (none)
  removed: (none)
  changed: (none)
prompt_fragments: applies at reload
  added: (none)
  removed: (none)
  changed: (none)
agent_defaults: applies at reload
  changed: yes
agents: applies at reload
  added: assistant
  removed: (none)
  changed: (none)
  grants: applies at reload
    changed: (none)
  prompt: applies at reload
    changed: (none)
  filler: applies at reload
    changed: (none)
  fallback: applies at reload
    changed: (none)
devices: applies at check-in
default_agent: applies at check-in
```

The token `reload` labels nine of those lines, and the four lines above
them are the definition a reader needs to make sense of it. The same
three changes are three lines under one head now, and the head names the
command rather than the token, so the definition has nothing left to
define.

### A command volunteers no advice about features not in use

A feature's own noun answers questions about it. That is a good deal of
what noun first bought (see [Noun first, verb
second](#noun-first-verb-second)): every feature has a place its answer
belongs, and it is the place a reader goes when they want it, rather
than the tail of an answer about something else. Advice printed where it
was not asked for is read once as noise and thereafter not read at all,
which costs the sentences beside it that were addressed to the reader.

**Example.** `_apply_listing` asks `_status_block` for the MCP half only
where there are entries to say something about, and `NOTHING_CONFIGURED`
goes on being exactly what `vinga mcp-server status` answers for a
deployment with none, where the same sentence is the whole answer to a
question somebody asked. One entry point either way: what changed is who
asks it. The same instinct had put a paragraph advertising the CLI
reference at the end of Getting Started's step 2, and it was deleted for
the same reason.

**Counterexample, historical** (#426). Every `vinga apply` on a
deployment with no MCP servers at all ended with this, under the
outcomes it had just listed:

```
this server has no MCP servers configured. An entry is written with
`vinga mcp-server set`, and an agent reaches it by naming it in its mcp
list
```

The walkthrough that found it had no MCP servers, was not being asked
about MCP servers, and got that paragraph on every apply.

### A credential is never an argument, and never travels in a read

**Example.** A `secret set` reads the value from stdin, without echo
when stdin is a terminal, or from the variable `--from-env` names. The
`MASK` shown in a read is a fixed eight characters rather than the
value's length, because a mask that tracks the length is a length
oracle. An `export` carries no credential at all: what it carries is
the `secret set` command for each stored slot, as comment lines, which
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

**Two recorded exceptions, and they are the same value twice.** The
onboarding URL ends in a key derived from the device-auth secret, and
that key stands in front of the endpoint that issues device tokens, so
it is a credential by this page's own definition. It is nevertheless
printed, because a URL nobody can be told is a URL nobody can type into
a captive portal, and the whole point of it is that it is short enough
to type. Both exceptions are recorded rather than assumed, and each
carries the design that makes it safe.

- **`vinga ota-url`** derives it locally and prints it. It contacts
  nothing, so there is no connection for it to cross: it reads the file
  half and the device-auth secret the server itself reads, on the host
  the server runs on. The URL goes to stdout alone and the provenance to
  stderr, so a capture gets the value and nothing else, and it is
  deliberately not what the startup banner prints, since a banner is a
  retained record shipped to whatever collects logs.
- **`vinga info`** reads it back over the API (#341), which is what
  makes a deployment administrable from somewhere other than its own
  host. That one is a credential travelling in a read and is designed as
  one: the route sits behind the same bearer gate every secret write
  does, so it widens nothing a caller does not already hold; the answer
  carries `Cache-Control: no-store`, so nothing between the two ends
  retains it; the transport policy already refuses plain HTTP to
  anything but a loopback address; and the value is rendered to stdout
  alone, appearing in no notice, no refusal, no log record and no
  exception chain. Each of those is a test rather than a sentence
  (`tests/unit/test_config_cli_info.py`), with the URL derived from a
  real device-auth secret so that what is hunted for is what a
  deployment would really serve.

What the pair does **not** license is a read that answers a stored
provider credential. Those have no counterpart to the argument above:
nobody types one into a portal, an `export` carries the command to set
one rather than the value, and a read masks with a fixed eight
characters. A third exception would need its own case made here, in
the same shape.

### One machine-readable shape, and it is the document `import` takes

The machine interface is not a serialization mode bolted onto a human
one. It is a round trip: `export` emits exactly what `import` consumes,
so the automation story is "read it, edit it, write it back" rather
than "parse our display format".

Where a listing is not a document it is a borderless table: a header
row, columns padded with spaces, one entry per line, which greps and
counts with `wc -l`. Where the fields are lists rather than scalars it
is blocks instead, because a column holding a list is a column that
wraps and stops being one line per entry.

**Example.** `vinga export > deployment.yaml` followed by
`vinga import -f deployment.yaml`, the `secret set` commands the export
listed, and `vinga apply` reproduces a deployment; the import writing
and stopping is what keeps the engines from being built before their
credentials are back. `pending`
prints five short columns, header included, because the question it
answers ("which of these boards is the one I am holding") is read
across a line. `mcp-server status` prints blocks, because two of its
three fields are lists of names.

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

Scope first, because the rule is about artifacts and not about
terminals. **What may never vary with the terminal:** the data on
stdout, every generated document, and any message at all once its
stream is not a terminal. Two runs against one stored state produce the
same bytes on a laptop, on a runner and through a pipe.

**What may vary:** an interactive affordance, on stderr or at the
prompt, provided the non-terminal path is complete and deterministic on
its own. The affordance may only ever be the presentation of something
the other path also delivers, so nothing that is only visible
interactively is load-bearing. A script is entitled to the same
information and the same bytes whatever the terminal does.

And the second property, which has no exception: nothing an answer
carries can steer the terminal it is printed into.

**Example.** The generated help pages are rendered through a context
with `terminal_width` and `max_content_width` stated and `color=False`,
because CI diffs them byte for byte and a page that wrapped differently
on a laptop and on a runner would fail its own drift check on an
unrelated change. `printable` truncates a value first and then replaces
every unprintable character with a question mark, so no answer can
choose how long a command's output is or put an escape sequence into
it. `_granted` sorts by agent name, so two reads of an unchanged world
print the same block.

**Example of the licensed kind**, merged: `_read_secret` asks with
`getpass` when stdin is a terminal and reads the pipe plainly when it
is not. The affordance is the prompt and the suppressed echo; the value
that comes out is the same either way, and nothing downstream can tell
which path produced it.

**A second example of the licensed kind**, merged (#297), and the one
this section's licence was written in advance of: the progress line for
the two long waits. `import` waits on a transaction with no bound at
all and `apply` waits up to sixty seconds, and until #297 both left an
operator watching an empty screen for the length of it. `narrated`
draws one line on stderr while such a wait runs, carrying a fixed
phrase and the elapsed whole seconds and nothing else, rewritten in
place once a second by a daemon thread and taken back off on the way
out, so that the answer or the refusal after it prints into an empty
line on any terminal still accepting output.

Every clause of the licence is a condition it meets rather than a claim
made about it. The terminal is asked once, on the way in, so a run
whose stderr is not one constructs no writer and emits no byte: the
non-terminal bytes are identical with the feature and without it, which
is a test that runs a command both ways and compares them, and the rest
of the suite runs redirected and would show these carriage returns in a
few hundred assertions about output if the check ever stopped being
made. What the line re-presents is only what the non-terminal path
delivers anyway, which is that the client is waiting for the API's
answer. It carries no caller value at all, not the document's path, not
an entry's name and not the address reached, because the no-leak
posture applies to progress exactly as it applies to a refusal. And
none of the four things the counterexample below rejects appears in it.

**What the empty line is worth, stated exactly, because two absolutes
were wanted here and only one of them can be had.** Two threads write
that line, the command's own and the one redrawing it, and they share a
lock held across the write itself. The first thing wanted was that no
redraw ever lands after the line is finished, since one that did would
sit on top of what printed next, which for a refused import is the one
sentence the command has to say. The second was that a command whose
request has been answered can always say so, since a wait an operator
sits through after the server has replied is the ambiguity every timeout
in this grammar exists to prevent, reached from the other side. A
terminal under flow control stops accepting writes and may never start
again, and on such a stream the lock that orders the two threads is the
lock the finished command would be stuck behind, so both cannot hold.

Completion wins, and the ordering is kept as far as anything can keep
it. The way out sets the finished flag first, which every redraw reads
inside the lock, so nothing that has not already begun writing can ever
write again: that half is absolute. Then it waits for the lock under a
bound. Getting it means a redraw in flight has finished and the erase is
the last thing written, which is the ordinary case and the whole of what
a live stream ever does. Not getting it means one write is wedged inside
the terminal, and the erase is abandoned rather than raced: the command
returns and reports itself, the line stays on the screen, and the
sentence after it prints on the same line rather than under it. That is
the degradation, it is named rather than discovered, and it is on a
terminal that had already stopped taking output.

Three cases hold the three claims: a redraw caught inside a slow stream
and released, proving the erase waits for it and lands last; a redraw
wedged and never released, proving an import that was committed still
prints what it did and a refusal still arrives whole; and the whole of
it run on a thread with a join, so a completion that stopped being
bounded fails rather than hangs.

Which acts narrate is a fact on the act's row, beside the bound rather
than read off it, because the two say different things: every act has a
bound, and only two are waits a person sits through. `events tail` is
the deliberate absence, since there the stream is the answer rather
than the wait.

**Counterexample, constructed.** Color, spinners, emoji or ASCII art in
anything a document is generated from. All four audited guides recommend
some of those, and each is rejected in
[the audit](cli-guide-audit.md) for this one reason.

### Prompt where there is somebody to ask, and never require it

An interactive prompt is a convenience for the person at the keyboard,
and it must never be the only way to supply a value, because everything
here has to be scriptable.

**Example.** `_read_secret` resolves in three steps: `--from-env` if it
was given, then a no-echo prompt if stdin is a terminal, then a plain
read of stdin, which is what a pipe and a script use. The same value,
three ways in, none of them mandatory.

**Counterexample, historical.** The function below that one read
standard input unconditionally until #223, so `apply -f -` typed at a
terminal blocked with no prompt and no explanation: the same rule
broken from the other side, by never asking whether there is anybody
there. It answers one sentence and exit 1 now, which is this grammar's
own shape for a mistake in it. The published answer is to print the
help and quit; the sentence carries the usage tail every other mistake
here carries instead of printing a page, because one refusal shape is
worth more than matching the wording of a guideline.

**And the confirmation, paid.** A destructive verb asks when stdin is a
terminal, takes `--force` to skip the asking, and asks nothing at all
when stdin is not a terminal, so a script is never blocked by one.
`--no-input` disables every prompt in the grammar, and the asymmetry is
deliberate: it refuses a destructive verb, because a confirmation has no
other way to be answered and `--force` is that other way, and it does
not refuse a secret write, because a secret has three doors and
disabling one leaves two. `--force` alone and not a `--force`/`--yes`
pair: two words for one act is what the verb rules forbid. Which verbs
are destructive is a fact on the registration row, and the line it
draws is that a verb destroys when its effect cannot be undone by
running another command with information the operator still has: a
delete destroys the body, a `set` does not as long as an `export`
exists, and a rebinding is an overwrite the API acknowledges.

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
nothing about what happened. `apply` gets sixty, derived from the
server's own envelope. `import` gets none, and the comment says why: the
transaction validates the whole resulting configuration, whose size
nothing about the request bounds, so no finite number can be derived
that would not sometimes expire on a transaction the server goes on to
commit, which is the one outcome every timeout here exists to prevent.

A bound belongs to the endpoint rather than to the command that reached
it, and the two above are what make that readable: `import` waits
without a bound and `apply` waits sixty seconds, because those are facts
of the two endpoints rather than of one command that happens to reach
both. Splitting the verbs (#371) is what turned that from a rule about
one command's two acts into two rows each stating its own.

**Example, the second unbounded one**, and it reaches the same
conclusion from the opposite direction. `events tail` reads a response
that never finishes arriving: the answer *is* the server saying what it
is doing, and a deployment saying nothing at four in the morning is a
stream with nothing on it, which is the reading an operator opened it
for. `STREAM_READ_TIMEOUT_S` is therefore `None` with the paragraph
attached, because any finite number would end a healthy tail and report
it as the server going away, which is the one thing that command's
end-of-stream sentence has to be able to mean. What makes that safe
rather than merely intended is not the client at all: the stream writes
a keepalive comment on its own idle interval, so a connection that has
genuinely died is a read that fails rather than a read that waits
forever. The connect timeout stays five seconds in both cases, for the
reason it always is.

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

## The sources, and what became of them

The practices above were arrived at by walking four published guides
one guideline at a time on 2026-08-24, giving each guideline a
disposition from a fixed vocabulary of eight words. The walk itself is
[`cli-guide-audit.md`](cli-guide-audit.md), a dated record with one
row per guideline, so what was considered can be checked rather than
trusted. The four sources are ThoughtWorks, "Elevate developer
experiences with CLI design guidelines" (eight guidelines in ten
rows); clig.dev, "Command Line Interface Guidelines" (ninety-six);
the Heroku CLI style guide (thirty-five rules); and 12 Factor CLI
Apps (twelve factors).

The shape of the answer, across all four. Most of it is **adopted**
outright: the stream split, the exit codes, the help obligations, the
prompt rules, the credential rules, the naming rules. A second group
is **adapted**, and nearly always for one reason: what the guides call
an argument this grammar calls an address, so every prefer-flags and
count-your-arguments rule lands on identity segments rather than on
flags. Four things are **rejected** on purpose, each with its reason
in its row: color, ASCII art, emoji and any other terminal-dependent
rendering, under output determinism; a traceback or debug dump, under
the no-leak posture; an error code and a support URL, because the
contract is one fixed sentence carrying the fix; and the XDG spec,
because the deployment surface is a container. Three rows are
**deferred**, all of them `--json`, and the case is
[above](#the---json-question-deferred). One thing is **owed**: examples
on the command pages. The second owed item was the progress line, and
#297 merged it, which is
[above](#output-is-deterministic-and-an-answer-cannot-steer-a-terminal);
the audit rows that recorded it as owed still read that way,
because that record is dated and says of itself that the guide is where
what vinga does today is written. **Tension recorded** is in the
vocabulary and no row carries it today.
