# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
using dates (`## YYYY-MM-DD`) as section headers instead of version numbers.

## 2026-09-06

### Added

- **An agent rename is one transaction across the three schemas.** The
  repository gains `rename_agent`, which gives one agent another name
  and moves every live reference to it in the same transaction or writes
  nothing at all: the agents row's key, the device bindings that name
  it, the default agent, the facts filed under it, and the threads it
  owns. The memory store and the conversation record each publish one
  function taking the caller's connection and its own chain's advisory
  lock as its first statement, so the ascending key order the deadlock
  rule is stated as is a property of those functions rather than of a
  call site; the rename is the first transaction in this server that can
  hold all three keys at once. What is not rewritten is the record of
  what happened: a turn, a session and an event say what was true when
  they were written, and nothing here touches one. Seven refusals, each
  a state the transaction can be in before it writes, and the three that
  refuse an occupied destination come from one rule that is what keeps
  the verb reversible: a rename may never merge two pasts, because no
  second rename could tell them apart afterwards.
  `AgentRenameConflictError` carries those three under a 409. Nothing an
  operator can reach changes yet: there is no route and no verb until a
  later change.

- **A rename is ordered against the conversations still in flight.** A
  live session holds the agent's name for as long as it lasts, so a
  rename moves the store underneath it, and left alone that is two
  defects rather than a cosmetic mismatch: a thread that already has a
  row refuses the next turn for misattribution and the writer drops the
  marker's whole batch, and a thread that has not materialized yet is
  written under the old name, which is a fresh reference to a name
  nothing answers to made by the act that removes them. The rename now
  takes the same ordering lock a deletion takes, across its transaction
  and the publication that follows it, and announces the pair of names
  to whichever writer is recording in this process. Which conversations
  that reaches is decided by the world each of them speaks from rather
  than by the instant it registered: a conversation binds its
  configuration before it awaits the device's hello and goes on speaking
  that world's names until it ends, so a rename published while a device
  is connecting, or while a stored change waits for its apply, reaches
  it too. The writer keeps one translation per recording session, seeded
  with what its world has not heard, and resolves the name once at the
  boundary where a name enters the record, so the turn, its handover
  legs and the thread it lands on all say the same thing. A world
  installed after a rename has nothing to translate, which is what makes
  a freed name safe to give to a second agent. What a session writes after
  a rename is a new write and carries the name the agent has now; what
  was written before it is dated record and is never touched; and the
  session row keeps the name it opened with, because that column's
  subject is the moment the session opened. Still nothing an operator
  can reach: the protocol lands whole before anything can call it.

- **The rename has a door: `POST /agents/{name}/rename`.** A POST
  because the act is not idempotent, and a body carrying `to` because
  the new name is what the request carries rather than part of what it
  addresses. It answers with the acknowledgement every domain write
  answers with, composed from what the transaction did rather than from
  what the request said, and the boundary it announces is chosen the
  same way: a rename that moved the agents row alone waits at the
  install like every other write of its kind, one that moved a device
  binding or the default agent with it waits at two boundaries at once
  and carries a sixth notice saying so, and a server that reads no store
  says that the rows are stored. Deliberately not chosen by asking which
  agents this server is serving, which is where it differs from a device
  binding: a running server may serve an agent under the new name and it
  will not be this one. An occupied destination answers 409 and no
  refusal quotes either name. `vinga agent rename` itself is the next
  change.

- **Two column comments stop describing the old behavior**, each through
  a forward migration, because a comment is committed DDL and a database
  migrated by an earlier build would go on saying something false.
  `memory.facts.owner` said that renaming an agent orphans its rows, and
  `record.conversations.agent` said the agent it names is the only one
  the thread will ever have; the first is now the rule that a rename
  moves an agent's rows with it, and the second keeps its real subject,
  that a conversation is a dialogue with exactly one agent, while saying
  that the name that agent is filed under can be rewritten and that the
  dated columns beside it keep the name of the moment. The memory chain's
  head becomes `2003_rename_moves_memory` and the conversation record's
  `1003_rename_moves_ownership`. The agent descriptor's note, the two
  memory docstrings, the server README's two orphaning passages and the
  observability page's half sentence are corrected in the same change.

- **`vinga agent rename <old> <new>`**, the verb in front of that door.
  The agent is addressed by the name it has and the name it is to have
  follows as the payload, which is the route's own order; the request is
  one POST carrying that name in a body and nothing else, so it stays
  out of every proxy log between the operator and the server. It asks no
  confirmation, and that is a decision rather than an omission: a verb
  destroys when its effect cannot be undone by running another command
  with information the operator still has, and a rename is undone by
  running it back, with the memory and the threads travelling back with
  it. The refusals are what keep that true, so they are what the verb
  rests on rather than a prompt. What it prints is the acknowledgement
  every write prints, the server's sentence about the boundary the write
  is waiting at with this client's own remedy under it, which for a
  rename that moved a binding or the default agent is the install that
  puts the renamed agent on the running server. The CLI reference grows
  the command's page, generated from the tree as every page there is.

### Changed

- **The board is one step, and it says why it starts with a flash.**
  Getting Started's steps 4, 5 and 6 were a flash, a URL read and a
  provisioning, three headings for one act performed with a board in
  hand. They are one step now, `Set up the board`, naming the three
  things a board needs at the top and doing them in order, and
  `Talk` is step 5. The flash carries the reason it never had, and
  says who can skip it: a server's address is one NVS key rather than
  a firmware property, so a board already running the xiaozhi app
  needs no new firmware, stock included, which is what vinga has been
  tested against. The step says which board and which app version it
  was walked on, the way the page says which operating system and
  shell it was walked on. Gone with the merge: a paragraph deriving the onboarding
  URL's key from the device-auth secret, which included a sentence
  about a trial that turned device authentication off, something step
  1 has never done.

- **Step 3 explains itself at the lines it is explaining.** The
  document to paste was followed by four paragraphs, three of which
  annotated lines the reader had already scrolled past: why the
  container cannot say `localhost`, what each engine is for, and how
  to bind one board instead of every board. Those are comments in the
  document now, where the line they describe is on screen. `apply` is
  its own block behind its own sentence, rather than a second command
  sharing the import's fence. What is left after the document is one
  paragraph on what import promised and one on what to do next.

- **Step 2 ends at one command instead of three.** The in-image CLI
  branch defined a shell function and then ran `vinga list` to prove
  it, both in one fence with their explanation underneath, and the
  step closed with a paragraph listing what the CLI reference covers.
  `vinga info` two lines later already proves the wiring and answers
  something the reader needs, so the `list` demonstration is gone and
  the function stands alone with its sentence in front of it. The
  reference link survives as a clause on the sentence that was
  already there.

- **Getting Started says which platform it was walked on before the
  reader spends anything.** The sentence sat under the prerequisite
  list, which is the part a reader skims. It is a callout above the
  prerequisites now, and it names the shell as well as the operating
  system, since the walk that produced these entries found a block
  that only zsh accepts.

- **Step 1 writes its comments into `.env`, instead of dropping
  them.** The block explained each variable in shell comments around
  the `echo` lines that produced the file, so the explanations lived
  in the README and the file the reader opens next month held four
  bare assignments. It is one heredoc now, and the comments land in
  the file. The delimiter is unquoted deliberately, so `openssl` and
  `$LAN_IP` resolve while writing and the file holds finished values:
  compose and the CLI both read it and they do not agree about
  expressions. The same paragraph says to keep added comments on
  their own line, since a `#` after a value is a comment to the CLI
  and part of the value to Docker.

- **Getting Started says how to see the loaded model, and how to pin
  it.** A pull puts a model on disk without loading it, and step 0's
  only answer to the five minute idle unload was an
  `OLLAMA_KEEP_ALIVE=-1` export, which takes effect where Ollama
  starts rather than in the shell walking the steps. The step now
  names `ollama ps` as the difference between what is on disk and
  what is in memory, loads the model and pins it with one request
  that restarts nothing (`keep_alive` of `-1` to `/api/generate`),
  and says to read `Forever` under `UNTIL`, since any duration there
  is a countdown to a device that answers nothing. `ollama stop` ends
  the pin, and the memory it holds. The `OLLAMA_KEEP_ALIVE` export is
  gone with the paragraph explaining it: teaching Ollama to start with
  a different default is Ollama's documentation to write, not a step
  in getting a board talking. `curl` is named as a step 0
  prerequisite as well.

### Fixed

- **Step 1's guard line is no longer a bash syntax error.** The
  `LAN_IP` check spelled its message "your machine's address", and
  bash reads that apostrophe as an opening quote inside
  `${var:?word}`, so a reader pasting the block into bash got
  `unexpected EOF while looking for matching '` before anything ran.
  zsh accepts the same line, which is why a macOS walkthrough never
  met it. The message says the same thing without the apostrophe.

- **A legacy control character in a name is no longer spoken raw**
  (#414). A name is held to one URL path segment at write time only, and
  the rule refuses a control character for a reason it states in as many
  words: it does not survive a header or a log line intact. A row
  written before that rule still boots and still names itself, so the
  byte reached every sentence composed over a stored identity, the
  loudest of them being a boot refusal on a server's stderr, where
  nothing between the composition and the terminal escapes anything.
  Every such sentence now leaves through `spoken_identity`, the
  credential strip #381 put on those surfaces with the control
  characters escaped as `\xNN` behind it. Escaped rather than replaced
  or withheld, because unlike a credential-bearing name such a row is
  reachable: a control character percent-encodes losslessly, so `%1b` in
  a path fetches, renames and deletes it, and the escaped spelling is
  what says which byte to encode. Every name a write would accept today
  is printed byte for byte as it was. The rule deliberately stops at the
  sentences: a document and a listing hand a name back as stored,
  because a read is a fragment a write of it accepts back and every
  writer of that document (JSON, YAML, the CLI's terminal door) already
  neutralizes the byte on its own. Four locations that joined a section
  to a stored name by hand now read the one composition the rest do.
- **A stored secret's location no longer says either half of itself
  verbatim** (#414). `SecretLocation.describe` is the one string that
  thirteen encryption and decryption refusals and four write
  acknowledgements are built from, and it rendered the entity's identity
  and the credential slot exactly as stored. A slot is held to the
  addressability rule at write time only, exactly as a name is, and
  `verify_secrets` opens every stored envelope at startup, so an entity
  or a slot written before that rule reached a boot's stderr whole. Both
  halves now go through the same door every spoken identity does, split
  into the parameters that address the entity first; the fields
  themselves are untouched, because they are what a lookup is made from.
- **A successful write no longer speaks a stored name verbatim either**
  (#414). Ten acknowledgements joined a stored identity into the sentence
  a 200 carries, and two of those are reachable with a name a write of
  that name would refuse today: a delete goes by membership, so a legacy
  row is deletable and said its own name on the way out, and a device
  binding or the default agent references an agent rather than creating
  one, carrying the name in a JSON body where a slash is no obstacle.
  Binding a device to such an agent answered with the credential in it.
  Every identity in an acknowledgement now leaves through the door, the
  bound-agent list included.
- **A credential written into a URL whose scheme a control character
  splits is stripped again** (#414). `url_credential` looked for a
  literal `://` before parsing, and the parser deletes every tab,
  carriage return and newline before it reads anything, so
  `https:/<CR>/user:password@host/v1` was a URL carrying a credential to
  the library and to any client that opens one, and not one to the rule
  that takes credentials out of what is displayed, recorded and spoken.
  The test now runs on the value the parse will see.
- **A legacy URL credential no longer reaches the provider build
  either** (#413). #381 put every DISPLAY of a stored identity through a
  strip and #382 put every REFUSAL through the same door, both of them
  inside the composition that turns a stored snapshot into a
  configuration. What runs next is the build, and it names the same
  entries again in a vocabulary of its own, composed by a different
  renderer after the composition has finished: the label every provider
  refusal carries (`providers.<stage>.<name>`, which reaches an unknown
  type, a bad option, a missing extra, a library that would not start
  and every egress refusal), the sentence an agent missing a stage is
  refused with, the location `provider_for_agent` answers beside the
  name, and the identity every provider event calls the entry by. A name
  written before the addressability rule can hold a URL carrying a
  credential, and all four carried it whole. All four now leave through
  the same door, so what a boot prints on stderr as it fails to start,
  and what an event says about the entry it is about, is the address
  without the credential. Two things are deliberately untouched: the
  name a stored credential is filed under, which is an address rather
  than a sentence, and an apply's answer, which already names nothing
  stored. The location an entry is written at moved to `entities`
  beside the addressing it is built from, so the sentence a boot prints
  about a provider row it could not read and the sentence it prints
  about one it could not build cannot come to disagree.

  Three more of the build's own words were closed with them, because a
  name is not the only stored string it speaks. The model an entry runs
  is free text a vendor names, and the identity every provider event
  carries reported it verbatim, so it now reports it without the
  credential while the entry goes on running the string exactly as
  configured. The type a row is filed under is no longer quoted back
  when it is not one of the four stages' known types: what comes back is
  the entry, the rule the type broke and the closed set it should have
  been in, which is the answer a stage column holding what no stage is
  already gets. And the names of options a type never asked about come
  back shortened, since there is no closed set to list in their place
  and an operator with a typo has to be able to see it.

## 2026-09-05

### Added

- **A write says which boundary it is waiting at, in tokens.** The
  configuration API's acknowledgement carried the answer only as a
  sentence, and two of the five sentences it can compose name a command
  of the CLI's grammar, so an image built before a rename told an
  operator to run a command the client beside it no longer had. A write
  now answers with `applies` beside the sentence, the same closed
  vocabulary the comparison read publishes, so a client can phrase the
  remedy in its own words. `Applies` gains `store-boot`, the boundary a
  write to a server serving a handed configuration waits at, and the
  comparison's own fields narrow to the three it can announce, held
  together by a pin that the two sets account for the enum and by one
  that constructs every model of the comparison with the fourth and
  asserts it is refused. The field is defaulted, which is this API's one
  exception to nullable-and-required: a server older than the vocabulary
  sends no key, and this client reads a sequence of closed tokens whole
  or not at all, so a token from a newer server reads as that same
  silence rather than turning a write that landed into a refusal.
  Nothing printed changes; the sentences are byte-identical, and the
  half that names commands moves in the change after this one.

- **The upstream wire contract is watched.** vinga speaks a protocol
  it does not own, read from upstream sources at pinned commits, and
  until now nothing noticed when upstream moved.
  `docs/upstream-watch.yaml` names, per upstream repository, the clone
  URL, the commit the protocol notes were read at, the read date and
  the exact paths that carry the contract; the server repository's
  paths were resolved from a clone at the pin rather than from memory.
  `scripts/upstream_watch.py` is the one thing that parses it, and the
  docs lane runs its `check` to hold the manifest and the currency
  table in `docs/xiaozhi-notes.md` to identical repository sets, equal
  commits and equal read dates in both directions.
  `.github/workflows/upstream-drift.yml` runs every Monday: blobless
  clones, a diff of the watched paths from each pin to upstream's HEAD
  and to its latest release, ancestry validated so a target behind the
  pin is reported rather than diffed backwards, and one labeled issue
  opened or updated with the files and commit subjects that moved. A
  week with no drift writes nothing.

- **The supported firmware is a declared floor.** A new decision
  record, `docs/adr/2026-09-05-supported-firmware-is-a-declared-floor.md`,
  enumerates the firmware releases vinga promises to speak: the two
  images actually observed on boards, 2.2.4 on the AMOLED-2.16 and
  2.4.0 on the Touch-LCD-1.54. That set is what every drift report is
  triaged against (does this change move anything inside the floor?),
  it widens by dated addendum when a new version is observed on a
  board and the protocol notes are re-read against it, and it narrows
  only by a superseding record. The stock-firmware promise's
  version-target bound cites the record; the promise itself is
  unchanged.

- **The server half has a generated reference.**
  `docs/reference/server-config.md` documents every key of the
  `server:` section: its type, its default, the bounds pydantic
  enforces and its description, section by section in declaration
  order, with each section's own prose, the `VINGA_SERVER__<PATH>`
  override scheme and the database section's four short spellings, the
  two values that deliberately have no key (`VINGA_DB_PASSWORD` and
  `VINGA_DB_URL`), and the combinations refused at boot in the
  sentences the validators raise. It is rendered from the models by
  `vinga-server config reference server`, a new optional `HALF`
  selector on the existing verb whose bare form still prints the domain
  page, and CI regenerates and diffs it beside the other generated
  documents. `config.example.yaml` keeps its role as the annotated
  starting point; the page is the complete contract.
- **The identity's leading claim is recorded.** A new decision record,
  `docs/adr/2026-09-05-pluggability-leads-self-hosting-supports.md`,
  states that when vinga explains itself, mix-and-match leads (every
  stage the server runs is a slot the user fills) and self-hosting is
  the ground that makes the choice real, with a fully local deployment
  as the limiting case of the blend. The guidelines' identity section
  cites the record and draws the altitude line that keeps it compatible
  with "not another pluggable VAD/ASR/LLM/TTS server": the promise is
  choice among engines, delivered behind stable slots, never by making
  the pipeline machinery the product.

- **Deployment artifacts for both lanes** (#397). `deploy/k8s/` holds
  five plain committed manifests, no Helm and no kustomize: a
  Deployment (one replica, `strategy: Recreate`, a startup probe on
  `/healthz` generous enough for a cold start that downloads models and
  only behind it the README's pair of restart at `/healthz` and
  admission at `/readyz`, a read-only root filesystem with a
  memory-backed `/tmp`, and a security context that makes the volume
  writable by the image's own user), a Service, an Ingress written
  against ingress-nginx that routes exactly `/x/` and `/xiaozhi/v1/`
  and deliberately leaves `/api/`, the probes and the legacy OTA path
  unrouted, an RWO PersistentVolumeClaim for `/data`, and a one-shot
  Job that runs `deploy/postgres-init.sql` from a ConfigMap built out
  of the one committed copy. Two Secret templates sit beside them,
  `secret.yaml.example` for the server and `secret-init.example` for
  the provisioning Job's administrative credential, named so they
  cannot ride along on `kubectl apply -f deploy/k8s/`. Beside the
  manifests, `deploy/docker-compose.production.yml` is the Docker
  lane's file. CI validates the manifests with a pinned, checksummed
  kubeconform, proves each of the compose file's refusals fires, and a
  new agreement test
  (`vinga-server/tests/unit/test_deploy_manifests.py`) reads the port,
  the probe paths, the drain budget, the routed paths and the image's
  user id out of the code and the Dockerfile and holds the manifests to
  them.

- **A deployment guide, `docs/deployment.md`** (#397). The worked path
  from a published image to a running deployment, which the repository
  had never carried: the contract every deployment implements as a table
  of links into the server README section that owns each fact, the
  one-replica topology and the three settings it shows up as, the Docker
  lane walking `deploy/docker-compose.production.yml` and its refusals,
  the Kubernetes lane walking the manifests in apply order with the
  routing boundary spelled out (`/x/` and `/xiaozhi/v1/` routed, `/api/`
  and the probes and the legacy OTA path deliberately not) and the
  provisioning transaction an upgrade reruns before the tag changes,
  what `/data` holds and what an `emptyDir` costs, which image tags are
  immutable and which move, and a verification section that ends at a
  board. It links rather than restates: the README section stays the
  authority, and the page names the agreement test and the docs link
  checker as what keeps it from becoming a second one. It is listed in
  `docs/README.md` under "Maintained maps and explanations", and the
  three READMEs point at it.

### Changed

- **A boot refusal about the stored half answers the same location
  policy as the API** (#382). The two halves of a boot were rendered by
  two renderers: the file half by the shared policy, which walks every
  location segment against the model and answers a key nobody declared
  by naming the rule instead of repeating it, and the domain half by a
  private renderer in the loader that printed every segment. Provenance
  is what the two disagreed about, and it is what decides. A key in a
  file is text an operator typed a moment ago and nothing has accepted,
  so it is still not repeated. A key in the stored half is the identity
  of a row a write already accepted, and the store's own refusals, the
  API's answers and this deployment's documents all print those in
  full, so a boot refusal now prints them too: an entry is named
  `agents.<name>`, exactly as the write that stored it names it, rather
  than truncated to `agents`. One renderer answers both halves and is
  told which it is holding. What tightens with it is the other
  direction: a key an operator wrote inside a stored body, an MCP
  server's `env` entry or a provider's option, is no longer printed by
  a boot either, and an unrecognized key under `agent_defaults` is
  answered by the rule it broke in the words the write has always used.

- **A write's acknowledgement states the boundary, and the CLI names
  the command.** The two sentences that named one no longer do: a write
  to the domain half now says it is stored and not yet serving, and a
  binding whose agent this server is not serving yet says the agent
  arrives with the install that adds it. The commands moved to the side
  that owns the grammar. The CLI reads the `applies` tokens beside the
  sentence and prints its own advice under it, from one table keyed by
  the boundary set, so `vinga apply` and `vinga diff` are spelled where
  a rename of either fails a test in the same checkout rather than
  reaching an operator through an image built before it. A boundary
  this client cannot name, which is what a set from a newer server
  arrives as, prints the server's sentence alone: an unknown state is
  quoted, never guessed at. An import deduplicates on the boundary set
  where there is one and on the sentence where there is not, so nine
  entities waiting on one apply are advised once while two entries from
  a server older than the vocabulary keep both their sentences. The
  served contract follows: `AppliedEntry.notice` describes a reader's
  sentence and directs a program to `applies`. What no longer changes
  is what an operator reads: the same two commands are printed, by the
  half that has them.

- **vinga has a face.** The README header's generated illustration
  gives way to a drawn mark: a white rounded V with dark oval eyes
  and an open smiling mouth with a lilac tongue, on an orchid-purple
  circle. The mark is hand-written SVG with PNG renders beside it, in
  two variants: `assets/vinga-logo-circle.svg` for floating in
  content (the README header now uses it) and
  `assets/vinga-logo-rounded-square.svg` for full-bleed icon slots
  such as favicons and avatars. The old `assets/vinga-logo.png` is
  deleted; nothing referenced it any more.

- **The README front page leads with the mix.** The identity record
  applied to the shopfront. A new "What is vinga?" opener: a
  self-hostable home for voice agents, where agents live on the server
  and move between boards, and every stage is mixed and matched. The
  origin story shrinks to three sentences that no longer restate the
  opener, and the features list turns user-facing and reorders to
  match: the cast of agents first, your-server-only closing, engine
  and provider names linked, the simulator bullet removed until the
  simulator is polished. Quick Start is renamed Getting Started and
  rewritten against the current CLI: one `vinga import` document
  instead of seven writes, device authentication on by default with
  `VINGA_SERVER__PUBLIC_URL` and a LAN address check, and the Ollama
  keep-alive trap called out. The Hardware section (now Supported
  Hardware) and Documentation move below Getting Started, and the
  GitHub repository description and topics moved with it all.

- **A hardened compose deployment is now a supported shape** (#397).
  Until now the only compose file in the repository was explicitly the
  trial and development story, and a deployment was left to derive its
  own. `deploy/docker-compose.production.yml` is a second, standalone
  file that says the production shape out loud: a required, pinned
  image tag, `restart: unless-stopped`, an external Postgres and no
  database service at all, a read-only root filesystem with a tmpfs,
  and an explicit `${VAR:?}` refusal on every required value, so an
  omission stops before a container starts instead of inheriting the
  loopback development password. The trial file at the repository root
  is untouched and remains exactly what it was: the two-container
  trial CI boots, with its convenience defaults and its deliberate
  absence of a restart policy.

- **The deployment naming convention is narrowed, not dropped** (#397).
  `AGENTS.md` used to say to describe deployment generically and to name
  no hosting provider or platform, which read as one rule and was two:
  the second half made a runnable Kubernetes or Docker Compose procedure
  unwritable. Open-source orchestrators and tooling may now be named
  where naming them is what makes a procedure runnable (Kubernetes,
  Docker Compose, an ingress controller). Hosting providers stay
  unnamed, which was always the part that mattered.

### Fixed

- **A legacy URL credential no longer reaches a refusal either**
  (#382). #381 put every DISPLAY of a stored identity through a strip,
  because a name written before the addressability rule can hold a URL
  carrying a credential. A refusal says an identity rather than showing
  one, and it says it somewhere no display goes: a server's stderr as
  it fails to start, which an operator, a container log and whatever
  collects one all read. Four sentences composed over a stored name
  carried one verbatim: the reference check that names the entry whose
  provider does not resolve and lists the ones that do, the
  completeness check that lists the agents a default could be set to,
  the location every per-row storage refusal is built from
  (`agents.<name>: the row cannot be read`), and the walk over a
  validation error's locations. All four now leave through the door the
  displays leave through, so what a refusal names is the address
  without the credential. A write is unaffected: a name reaches those
  sentences only after the addressability check has passed it, and a
  name carrying a credential holds a slash.

- **A legacy URL credential is no longer displayed** (#381). A URL
  carrying a credential, either before its host or in an `auth`-family
  query parameter, has been refused at every write since #279, and no
  capture manifest or conversation record has carried one since then
  either. Both of those are write-time rules, though, and a row stored
  before them still boots and still reads: until now every display
  handed the credential straight back, in a single read, in a listing,
  in the whole-configuration document, over the API and through the CLI
  renderings built on them. The display boundary now strips one from
  every string it shows, at every depth and for both kinds (an MCP
  server's `url` and a provider's `base_url`), so what a read answers
  with is the address without the credential. Nothing else about a
  displayed value changes: a string that is not a URL carrying a
  credential is shown exactly as it is stored. The export gains a
  working round trip as a consequence, which it did not have: a
  document exported from a store holding such a row used to be refused
  whole by the very import path it was written for.

- **A URL credential in a mapping KEY is refused and no longer
  displayed** (#408). The rule above, and the one #279 wrote, both
  asked their question of values. Three groups are keyed by whatever
  the caller wrote and were never asked it at all: a provider's
  options, the structures those options pass through, and an MCP
  server's `env` and `headers`. A key spelled
  `https://user:password@host/x` was therefore accepted by every write
  and handed back verbatim by every read, every export and every
  capture or conversation manifest, which is the value-side leak over
  again on the other half of the pair. The write path now refuses such
  a key at all four doors, in a refusal that names the entry, or the
  declared group inside it, and never the key, since the key is the
  credential. The display and record boundaries strip one from a key
  exactly as they do from a value, at one shared site. Where two keys
  reach the same spelling once the credential is out of them, both are
  kept: the first keeps the spelling and the next takes `#2`, in the
  order the row holds its keys, because dropping a pair from a read
  would delete configuration on the re-import the read is meant to
  feed.

- **A URL credential in an entity NAME is no longer displayed** (#408).
  The third place a stored string reaches a caller, after a field and a
  mapping key, is the identity a view is keyed by or hands back. Names
  are held to one URL path segment at write time only, and the rule
  says so in as many words: a row written before it still boots and
  still appears in a whole-configuration read. It appeared with the
  credential in it, as a map key in the document and in every listing,
  in the secret locations beside them, in a device's bindings and in
  the default agent's name. All of them now show the name without the
  credential, through the same site and the same collision rule as a
  mapping key. The device map's own key is the one exception and is
  deliberately untouched: a MAC that is not six colon-separated hex
  pairs is refused by the load path, not just the write, so such a row
  never reaches a view and a strip there would be unreachable code.
  The trade-off, stated rather than buried: a sanitized name cannot be
  used to address the row it names. It costs nothing in practice,
  because a name this rule changes contains `://` and therefore a
  slash, which is exactly what the addressability rule refuses, so such
  a row already could not be fetched, replaced or deleted over the API
  and answered 404 raw or percent-encoded. The delete routes were
  deliberately not widened to accept sanitized spellings; no read
  carries a credential outranks addressability derived from a display,
  and the suffix rule already makes a shown name a display artifact
  rather than a guaranteed address.

## 2026-09-04

### Added

- **A leaked tool call is never spoken** (#385). Some models, small
  local ones especially, write a tool call out as ordinary prose
  instead of issuing it, and vinga read it out loud: JSON in the
  assistant's voice, on the one user-facing surface with no filter on
  what a model produced. Each sentence of a reply is now tested before
  it is spoken, and one shaped like a call to a tool that reply
  actually offered is dropped whole. The check is narrow on purpose and
  anchored to the offered tools rather than to "looks like JSON", so an
  agent asked to explain a JSON snippet still answers: a sentence goes
  only when it contains a complete JSON object that names an offered
  tool, in its own `name` or in the `name` under a `function` key, or
  whose keys all fall inside the properties one offered tool declared.
  That second form is the shape the field produced, which carries no
  name at all, so keys are compared and values never are.

  A withheld sentence enters nothing: not the speaker, not the display,
  not the conversation this server keeps, not the stored turn, and no
  event or log line carries a byte of it. A new `sentence_withheld`
  event says it happened, carrying the sentence's length in characters
  and which tool it was shaped like, under the same naming rule
  `tool_call` follows. A reply left with nothing at all to say says the
  fallback phrase from the entry below, under the second reason
  `reply_fallback` now carries, `nothing_sayable`.

  **One bound is stated rather than closed.** Sentences are cut at
  newlines, so a pretty-printed call arrives as fragments no JSON
  decoder can read and those fragments are still spoken; closing that
  would mean holding sentences back from a voice they have already been
  handed to, stalling live speech at every ordinary brace in every
  reply. The event is what keeps the residue visible, and repeated
  records of it are a fact about the model a deployment chose.

- **A failed reply says so, out loud and on the display** (#384, #343).
  A reply that failed terminally was a deliberately silent turn: the
  failure arm logged one class name and nothing reached the speaker or
  the display, so from the couch a broken pipeline was
  indistinguishable from a slow one, and diagnosing a cold local model
  during the Getting Started walkthrough took an hour and a container
  log. Every agent now has a `fallback` section holding a short fixed
  phrase, synthesized in that agent's own voice when the world is built
  and cached as PCM exactly the way the filler clips are, which the
  failure arm shows on the display and plays. It is vinga's own words
  rather than the agent's: it never enters the reply's spoken
  sentences, the conversation history or the stored conversation, and a
  new `reply_fallback` event carries the reason and whether the phrase
  was heard as well as shown, never the phrase. The phrase is fixed
  configuration and is never the failure's own message, which arrives
  from the far side of a network. Only a terminal failure speaks: a
  device that went away is told nothing, and a reply cancelled by a
  barge-in stays silent because a cancellation means the user is
  talking.

  **Deployments upgrade into a speaking failure arm.** The section is on
  by default, which is the one place it differs from the filler beside
  it: the silent turn is at its worst during onboarding, where a
  misconfiguration is likeliest and nobody has a log open. A deployment
  that would rather keep the silence sets `fallback: {enabled: false}`,
  on `agent_defaults` or per agent, and its failed turns go back to
  being what they were, message for message. Being on by default also
  means **every server start synthesizes one phrase for every agent that
  has not switched it off**, through whatever TTS provider is
  configured, before the server begins serving: one provider call per
  agent, a few seconds of startup, and on a metered voice a few seconds
  of billed synthesis, paid again at every restart, redeploy and
  container replacement rather than once at the upgrade. Nothing is
  cached across processes, and there is no way to stage an opt-out
  before the first start after upgrading, because a configuration
  written against the previous models refuses the unknown key.
  Synthesis is bounded per phrase, so a provider that hangs delays a
  start by seconds rather than indefinitely, and a phrase that will not
  synthesize degrades to the display alone with a `fallback_degraded`
  event naming the agent; that turn still shows its sentence and still
  closes with the `tts stop` a device waits on. Inside one running
  process the cost does not recur: an agent whose `fallback` section and
  whose voice are both unchanged keeps the clip it had across a `vinga
  apply`, and the two kinds of cached clip are staled apart, so
  switching the latency mask on or off never re-synthesizes a failure
  phrase.

- **The OTA reply says why a device token is empty** (#369). Two
  unrelated answers left the wire looking identical: a board this
  deployment has nothing to admit, and a board that is admitted on a
  deployment with device authentication turned off, which issues no
  tokens to anyone. Both were answered with `websocket.token: ""`, byte
  for byte, so anything reading the reply had to guess which it was,
  and the device simulator guessed the only way the reply allowed, as
  "not admitted". The reply now carries a closed `access` field beside
  the token: `token` (admitted, and the non-empty token beside it is
  the credential), `open` (admitted, and this deployment issues no
  device tokens) or `denied` (not admitted, which is why the token is
  empty). The token and the word for it are decided in one place, so
  they cannot disagree, and being unresolved wins over the auth setting
  because turning authentication off does not give a board an agent to
  reach. The field is top level rather than a member of `websocket`,
  which is the boundary stock firmware ignores: it parses exactly
  `activation`, `mqtt`, `websocket`, `server_time` and `firmware` and
  reads no other top-level key, while it writes every member of
  `websocket` into NVS, where a key added would have left a stray entry
  on every board. Nothing reads the field yet.
- **A tool call whose argument the model only quoted now works**
  (#383). Small local models routinely send `{"volume": "100"}` where
  the schema declares an integer, and the far side is entitled to
  refuse it: the device firmware validates its own tools, so the tools
  of the board were decorative on exactly the stack the README
  recommends. An argument whose string form converts to the declared
  type exactly is now converted once, at the dispatch, for every tool
  source alike: `integer`, `number` and `boolean`, held to an ASCII
  grammar and, for a number, to an exactness check, so `"100"` becomes
  `100` while `"one hundred"`, `"100.5"` against an integer, `"True"`
  and `"1"` against a boolean, and any spelling whose value a `float`
  cannot hold exactly are left as the model sent them and fail the way
  they did before. What the record, the API and the history keep is
  unchanged: they show what the model passed, which is what diagnosing
  a marginal model needs. A call that was corrected says so on the
  structured surface as `tool_arguments_coerced`, carrying how many
  arguments were converted and never which or to what.
  - One upgrade-visible consequence, and it crosses an irreversible
    operation: `forget` erases a fact permanently only when
    `permanently` is exactly `true`, so a model that sent the string
    `"true"` used to get the recoverable removal and now erases the
    fact for good. Every other value, `"false"`, `"True"` and `"1"`
    among them, still takes the recoverable path.
- **The two long waits say so at a terminal** (#297). `vinga import`
  waits on a transaction nothing bounds and `vinga apply` waits up to a
  minute for a server to build a new world, and both used to leave an
  operator watching an empty screen for the length of it. Each now
  draws one line on stderr while it waits, carrying a fixed phrase and
  the elapsed whole seconds, rewritten in place once a second and taken
  back off on the way out so that the answer or the refusal after it
  lands in an empty line. A terminal that has stopped accepting output
  is the one case where it does not: the line is given up rather than
  waited on, because a command whose request has been answered has to
  be able to say so. It is the interactive affordance the CLI
  guide's determinism practice licenses, and it is bounded by that
  licence: the terminal is asked once on the way in, so a pipe, a
  redirect and a log file get exactly the bytes they got before, proven
  by running a command both ways and comparing them; the line
  re-presents only what the non-terminal path reports anyway; and it
  carries no caller value at all, not the document's path, not an
  entry's name and not the address reached. No colour, no spinner and
  no emoji. `vinga events tail` is deliberately not narrated, since
  there the stream is the answer rather than the wait.
- **One process, one replica is the supported topology** (#316),
  recorded as an ADR. The inventory behind it: pending activation
  codes, the loaded configuration generation, session admission,
  provider singletons and the drain are all process-local, while the
  database writes (advisory-locked) and device tokens (stateless HMAC)
  are merely safe across processes, not coordinated. The server README
  now says a deployment runs one replica and why, the token
  statelessness passages no longer read as replica support, and the
  ADR names the one future module (cluster coordination: activation
  claims, configuration revisions, node status) plus the
  reconsideration triggers that would justify building it. The
  topology itself is exercised by CI booting the committed compose
  file, one server service, no replica setting.

### Fixed

- **A reply's length cap can be configured at last** (#277).
  `max_tokens` contains the word `token`, and the rule that refuses an
  inline secret in a provider option matches that word anywhere in a
  key, so writing the cap was refused on every surface and for every
  provider type: the `anthropic` and `openai_compatible` builders read
  an option no entry could carry, the built-in default of 1024 silently
  always won, and the refusal advised writing `max_tokens_env`, which
  nothing reads. Every operator-facing reference documented the field
  as writable with no caveat, so the only way to find out was to
  measure a reply. The rule now carries a bounded exemption, exact and
  case-sensitive, for that one name: the cap installs from a file, over
  the API and from the command line, it reaches both builders and the
  request they send, it is shown rather than masked on every read and
  in every export, and both LLM example fragments document it. Nothing
  else moved. `MAX_TOKENS` and `Max_Tokens` are spellings nothing
  declares and are still refused, and so are `max_token`, `tokens`,
  `max_tokens_backup` and every other name carrying one of the words
  the rule is built from; an MCP server's env key, an MCP header or a
  URL query parameter called `max_tokens` is still treated as a
  credential, because those are named by whoever runs the server or the
  endpoint and have no declared reader to earn an exemption.

  **Deployments upgrade past a credential slot that is withdrawn.**
  Because the slot check read the same rule,
  `vinga provider secret set <stage> <entry> max_tokens` was accepted
  and stored an encrypted value in a slot no read, no build and no
  request ever consulted. That command is refused from this release on,
  with the sentence any other name that is not a credential slot meets.
  A stored row would otherwise be verified at every boot, listed as
  stored-secret metadata, and rendered into the foot of `vinga export`
  as a command the `vinga import` that same document prescribes now
  refuses, which would break the documented export-and-reapply
  recovery. So a forward migration on the domain chain deletes exactly
  those rows at the first start after upgrading. Nothing that was ever
  read is lost, sibling credentials on the same entry are untouched,
  and a deployment that never entered one has nothing to delete.
- **The unit lane's boot tests run against the lane's own database**
  (#333). The lane promises that every test writes to the per-worker
  database the autouse truncation clears, and two doors to that fact
  were closed: the model default and `VINGA_DB_NAME`. There was a
  third. Pydantic inlines a sub-model's schema, defaults included, into
  every embedding model's compiled validator at class creation, so a
  composition whose payload carried a `database` mapping with fields
  missing filled them from a stale copy, and fifteen boot tests booted
  against the compose instance's real `vinga` database instead. One of
  them asserted a row count of zero against a database it had never
  booted into, which it would have done however many rows the boot
  wrote. The lane's conftest now rebuilds the whole cascade
  (`DatabaseConfig`, `ServerConfig`, `FileConfig`, `Config`, innermost
  first, because a child's rebuild does not propagate upward), the
  fixture that restores the shipped defaults mirrors the same move and
  owns its own environment edits so no other finalizer can undo them out
  of order, and the lane pin grows the payload door, all four connection
  facts rather than the name alone, and a completeness rule derived from
  the models so a fourth embedder cannot open the door again silently.
  Nothing a deployment runs is affected: the pydantic-level mutation
  lives only in the test lane, and a server's own boot has always
  resolved its database through the loader.
- **The simulator holds a conversation on a deployment that issues no
  device tokens** (#369). With `server.auth.enabled: false`, a default
  agent set and the store applied, the server admitted the simulated
  board and said so in its own log, and `vinga simulator run` refused
  to speak: the reply's empty token was the only thing it had to read,
  and an empty token had always meant "not admitted". It now reads the
  `access` word the reply carries beside the token, so an admitted
  board on a deployment that mints no credentials holds its
  conversation, a board that was turned away is still reported as such,
  and a reply from a server too old to carry the word is read exactly
  as it was before. A reply whose word contradicts what stands beside
  it is refused rather than resolved, as contradictory replies already
  were.
- **The simulator's messages stop advising their own opposite** (#369).
  `simulator run` on a board that may not speak advised `--claim`,
  which answered that a board showing no activation code has nothing to
  claim and told the reader to drop the flag. The refusal now points at
  `simulator check-in` and mentions the claim only for the board that
  is showing a code. The check-in's own account of a board that may not
  speak names every configuration that produces one, which is two more
  than it used to (a deployment that could not read its own record of
  what is bound, and an agent named by `default_agent` rather than by a
  binding), plus the reading that is not a configuration at all: a
  deployment that issues no device tokens and is too old to say so. The
  post-claim refusal names that last reading too, and an admitted board
  is told whether a credential was issued or whether that deployment
  issues none.

- **An MCP server's address may not carry a credential either** (#279):
  `url: https://user:password@host/mcp` names nothing secret-shaped, so
  every rule this project had about inline secrets passed it, and it was
  stored as written and read back on every display path. A provider's
  `base_url` has been refused for the same shape since the record work;
  an MCP server's `url` is a declared field of a closed model that the
  provider's option walk never reaches, so it got the rule of its own it
  needed. Writing such a URL is refused where an MCP server is written
  (both write paths, since the rule is the repository's), for a user and
  password before the host and for a credential-shaped query parameter
  alike, with a refusal that names the field and the rule, says to send
  the credential as a header read from the server's own environment, and
  never quotes the value. Write time only, exactly like the provider's
  rule, so a deployment that already has such a row still boots, still
  reads it and can still edit it out.
- **A URL's query is read for `auth` as well as for `token`** (#279):
  what counted as a credential in a query parameter was the set of six
  words a provider option is held to, and none of them is a substring of
  `auth` or of `authorization`. So `?auth=...` was stored as written on
  an MCP server and on a provider alike, printed wherever an address is
  shown, and left in place in the record built from a stored one: the
  three readers of that rule agreed with each other and all three were
  wrong. They now read one predicate over the wider set of names, the
  one an MCP server's headers and env are already held to, on the
  grounds that a query parameter is named by the vendor whose endpoint
  it addresses rather than by this project.
  **Operator-visible:** a provider `base_url` or an MCP `url` carrying
  `?auth=` or `?authorization=` is refused at the write where it used to
  be accepted; write the address on its own and name the variable
  holding the credential (`api_key_env` for a provider, a `$NAME` header
  for an MCP server). A stored one still boots and still reads, and a
  displayed address no longer shows the parameter.
- **A config file that will not read is refused without naming it**
  (#291). The boot loader echoed the submitted `--config` path and the
  operating system's own wording, quoted the path again in its YAML and
  invalid-config sentences, and chained the library exception that
  holds the filename. It now answers the way the `-f` write does: an
  ordered table of failure class to fixed sentence (not there, cannot
  be read, not UTF-8 text, could not be read), the YAML locator and
  nothing else off the parser, and every refusal built inside its
  handler and raised after it, so no exception chain carries the path
  or the buffer. What a refusal names instead is the door the path came
  through, `--config` or `VINGA_CONFIG`, which is what its reader goes
  and changes. A config file of bytes rather than text was not caught
  at all and left as a traceback retaining what it could not decode;
  that arm is closed too, as are the three failures that are not
  `YAMLError` at all (a scalar the constructors refuse, an impossible
  date, a document nested past the stack), which the `-f` write already
  answered and the boot path did not.
- **A config file is read once, and what it parsed is what the server
  boots on** (#291). It used to be opened twice, once by the loader and
  once by the settings machinery behind it, and the second read answered
  to nobody: a file deleted between the two booted the defaults in
  silence, one that had turned malformed or into bytes left as the
  parser's own exception with the path and the offending line in it,
  and that read named no encoding while the first named UTF-8.
- **A value a server key refuses is not quoted back** (#291). The
  `log_level` and `ota_path` refusals opened with the rejected value in
  quotes, in a file that sits beside the auth secret and the database
  password; they now name the rule and the accepted spellings and say
  the value is not repeated, which is what the same key's
  reserved-prefix refusals already did. A misspelled section is answered
  the same way: the file half's validation refusals go through the walk
  the API and the store already use, so a key an operator typed is
  answered by the rule's name rather than printed back into the boot
  log.
- **`vinga list` reads the whole configuration through the same gate
  the counts do** (#351). The tree walked the masked document trusting
  every nested type, so a section that answered a scalar or a list
  where a mapping belongs left the boundary as a `TypeError` or a
  `KeyError`: a traceback with the answer inside it, on the command an
  operator reaches for when something is wrong. Both renderings of the
  document now read it once, as the shapes the entity registry's
  addressing says its sections have, so a section that is malformed or
  missing meets the one fixed sentence a body this client cannot read
  gets. An entry that is not a body at all is refused rather than
  rendered, since what the tree would print of one is a key read off a
  string. And every value that reaches a line goes through the display
  door: entity names, device MACs and the agents they reach, the slots
  a stored secret fills, the type and transport a suffix names, and
  both halves of every inlined pair. A mapping where a word belongs
  reads as the fact that it is one rather than as its repr, and nothing
  an answer carries can steer the terminal the tree lands on. The
  rendering of a well-formed document is unchanged, byte for byte.
  `vinga info` reads the same step, so it now refuses a document whose
  `agent_defaults` it cannot read, though it never prints that section:
  the sentence is about the answer, not about the line.
- **Every rendering of the whole configuration reads it through one
  gate** (#351, #380). `vinga list`, `info`, `show` and `export` walked
  the masked document trusting every nested type, so an answer that put
  a scalar or a list where a mapping belongs left the boundary as a
  `TypeError`, a `KeyError` or an `AttributeError`: a traceback with the
  answer inside it, on the commands an operator reaches for when
  something is wrong. All four now read the answer once, as a mapping
  before anything is looked up in it, then as the shapes the entity
  registry's addressing says its sections have, so a section that is
  malformed or missing meets the one fixed sentence a body this client
  cannot read gets. An entry that is not a body at all is refused rather
  than rendered, since what the tree would print of one is a key read
  off a string. A stored location naming a kind this client has no
  command for meets the same sentence, where `export` used to raise a
  `KeyError` whose one argument was the value the answer supplied.
- **Nothing an answer carries can steer the terminal these renderings
  land on** (#351). Every value that reaches a line goes through the
  display door: entity names, device MACs and the agents they reach, the
  slots a stored secret fills, the type and transport a suffix names,
  both halves of every inlined pair, and the three fields of every
  stored location `show` writes under the document, which used to be
  interpolated into a comment raw. A structure is named rather than
  opened at every depth: a mapping reads as `{...}` wherever it appears,
  including inside a list, where it used to arrive as a Python repr with
  its keys and whatever they held. The one depth still opened is the
  outermost list, because that is what an agent's includes and its
  grants are.
- **`vinga export` refuses a stored location it cannot write down**
  (#351). It renders every stored credential as the command that enters
  it, and the identity and the slot go into that line as written,
  because the command has to address the entity it names. Those lines
  are `#` comments inside a YAML document, and quoting a value is what
  makes a line safe to paste rather than safe to read: a newline in an
  identity ended the comment, and the rest of it landed on a bare line
  of a document whose own header says to import it. A location carrying
  a line separator, a control character or one of the format characters
  a terminal obeys silently (the right-to-left override among them) now
  meets the fixed refusal before any of the document is rendered. A name
  carrying a space, a percent sign, a character outside ASCII or a
  leading dash still exports verbatim, which is what the refusal exists
  to protect. The write path already refuses a control character in a
  name, so only an answer that did not come from this API can meet
  this.
- **The `mcp` grants written in the object form read as structures in
  the `vinga list` tree** (#351). They used to print as a Python repr,
  which is the same rule change as above seen on a well-formed
  document: an agent granting part of a server now reads
  `mcp=[lights, {...}]`. Everything else a well-formed document renders
  as, in all four commands, is unchanged byte for byte, which committed
  pins hold it to.
- **`vinga info` refuses a document whose `agent_defaults` it cannot
  read**, though it never prints that section (#351), because the four
  renderings share one reading of the document: the sentence is about
  the answer, not about the line. `vinga export` is the exception and
  says so where it is written: it dereferences neither a section nor an
  entry, so it prints back what the server said rather than refusing to
  hand an operator their configuration over a section it never looks
  at.

### Changed

- **A capability row says which kind of row it is** (#303). The
  simulator's capability table told a wire message row from a written
  one by reading the row's first word, so a prose row that happened to
  begin "reading " or "sending " was held to the protocol's message
  inventory it has nothing to do with. One row was deliberately worded
  around the trap with a comment saying so, which made the English of a
  help line load bearing. Every row now declares its own kind, the
  assertions filter on that field, and the wording constraint is gone,
  with a case pinning that a prose row opening with a direction word
  passes. Test harness and the table module only: no rendered byte
  moves and `docs/reference/cli.md` is unchanged.

- **The image door of the CLI documentation speaks compose, and the
  front page offers it.** The CLI reference's install-nothing door used
  a plain `docker exec` against a container named `vinga`, which is not
  the name the quick start's compose stack gives its container, so the
  one deployment most readers have was the one the recipe missed. The
  door now leads with the compose spelling and its shell function
  (`docker compose exec -T vinga vinga`, with `-T` explained beside
  `-i`), keeps plain `docker exec` for a container run without compose,
  and says out loud what makes this door special: the client is the
  build the server is, so the two halves cannot disagree about the
  grammar. Getting Started's install step now offers it as the
  install-nothing choice beside `uv tool install`, verified end to end
  against the published image (list, export to stdout, import from
  stdin, apply, diff).

## 2026-09-03

### Added

- **Readiness is its own probe, `/readyz`** (#318). `/healthz` says the
  process is alive and serving its control surface; `/readyz` says it
  may be handed a new device conversation, which is the other question
  and diverges from the first exactly when it matters. It answers
  `200 {"status": "ok"}` or 503 with one word: `draining` while a
  shutdown finishes the conversations in flight, `full` at
  `max_sessions`, `unavailable` when there is no serving composition.
  An orchestrator with two probe slots points restart at `/healthz` and
  traffic admission at `/readyz`; the server README says so, the image
  `HEALTHCHECK` says why it stays on liveness, and the compose file says
  what its own gate means. Recoverable provider and MCP failures cannot
  reach it: the handler asks composition presence and one registry flag
  and nothing else. `/healthz` is unchanged, body and all. Both probes
  answer both spellings of their path, `/readyz` and `/readyz/`, rather
  than redirecting between them: a redirect's `Location` carries the
  request's own query string back to whoever sent it, and a probe URL is
  what goes into a manifest or a CI script.
- **A handshake refused by a shutdown says so** (#318). The
  `session_rejected` event gained a variant with `reason: draining` and
  a sentence naming the shutdown, beside the capacity one. The two send
  a reader somewhere else, and reported as one word every rolling
  restart read as a load problem.

### Changed

- **Admission has one classifier, and it latches when a shutdown
  begins** (#318). The registry answers `admission` (`admitting`,
  `draining` or `full`, draining winning) and `admit` decides through
  it, so the door and the probe reporting on the door cannot disagree.
  `stop_admitting` shuts it synchronously, and
  `DrainingServer.handle_exit` calls it before anything else on every
  path: the drain used to be scheduled onto the loop, so the server went
  on admitting until that task ran, for the whole of a `drain_s: 0`
  shutdown, and after a second signal. A signal that arrives while the
  lifespan is still building is remembered and applied to the registry
  the build publishes, since uvicorn binds its listener before it
  notices it was told to stop.
- **`server.ota_path` refuses the two probe paths** (#318). The probes
  are registered before the OTA router, so an endpoint configured at
  `/healthz/` or `/readyz/` would never be reached and a board's
  check-in would be answered with a health body. Refused at load, beside
  the reservations of `/api/` and `/x/`.
- **The composition lives exactly as long as the lifespan that built
  it** (#318). `app.state.composition` was assigned and never cleared,
  so an application that had been served once went on answering
  requests with engines, a writer and providers its teardown had
  already closed. The removal is registered on the build's exit stack
  after every other registration, so the unwind takes it away before
  anything behind it is released.
- **The lanes wait for readiness rather than liveness** (#318). The
  smoke lane's `serve.sh` and its server fixture, the seeding wait in
  the integration lane and the live CLI lane's serving check all polled
  `/healthz` while waiting for a server they were about to work with.
  The image healthcheck, the revision comparisons and the version-skew
  procedure in the CLI reference are liveness and revision reads and
  stay where they were.
- **The two-clock verbs are renamed so each names its own act** (#371).
  `vinga apply` becomes `vinga import`: it writes a whole document to
  the store in one transaction and stops there. `vinga reload` becomes
  `vinga apply`: it installs the stored configuration on the running
  server. The names pair deliberately, `import`/`export` for the store's
  document I/O and `diff`/`apply` for the store-versus-running-server
  reconciliation, and "apply" names the outcome where "reload" named the
  mechanism. Pre-release stance: no aliases. `vinga reload` is an unknown
  command, and `vinga apply -f` is refused, because the new `apply` takes
  no document.
- **The per-write boundary notice is one line** (#371). It said which
  command installs the write and then named the three moments a
  conversation meets an installed change at, printed verbatim on every
  entry of every domain-half write; a Quick Start run printed it six
  times. It now says the write is stored and not yet serving, names
  `vinga apply` and `vinga diff`, and stops. The three clocks moved to
  `vinga apply --help` and stay in `docs/reference/domain-config.md`. The
  binding-not-yet-served notice is respelled and tightened the same way.
- **The server API keeps the mechanism's vocabulary** (#371).
  `POST /api/runtime/config/reload`, the whole-document write route, the
  response model names and the diff's `applies` tokens are all unchanged;
  the CLI's `import` posts to the API's apply route and its `apply` posts
  to the reload route, which the two act rows say where they are
  declared. What did change server-side is every sentence telling an
  operator what to type: the two event message templates and the
  unserved-agent refusal now name `vinga-server config apply`.

### Removed

- **`--no-reload`** (#371), deleted rather than renamed: a write-only
  `import` needs no flag to stay write-only. Scripts using it type
  `vinga import` and then `vinga apply`.

## 2026-09-01

### Changed

- **Getting Started reaches a working deployment by typing, not by
  editing a file** (#346). Step 3 was `curl` a preset, edit its
  `base_url` in an editor the page could not see, then `apply`. It is
  now eight `vinga` lines that can be pasted in one go: four providers,
  the defaults naming them, the agent, the default agent, and the one
  `vinga reload` that installs all of it. A new step 0 pulls the model
  the stack needs, a Prerequisites list names everything the walkthrough
  invokes (`curl`, `openssl` and `git` among them, which macOS ships and
  a stripped-down Linux may not), and the section opens by saying what a
  reader ends up with rather than what they are about to configure. The
  simulator block leaves Getting Started, keeping its Features bullet.
  Every command of steps 0 to 3 and step 5 was executed against an empty
  deployment before it was published, and the stored configuration
  compared whole against `local-stack.yaml`; the board steps were not
  walked, so what they say is unchanged from before.
- **The container section of `vinga-server/README.md` shows the inline
  spelling beside `-f`** (#346), so a reader with a short entry to write
  can see it is one line, with the reason a credential is never one of
  those arguments.
- **The front page opens with the board rather than a block diagram**
  (#346). "What is vinga?" now leads with a photograph of a
  Touch-LCD-1.54 running stock upstream firmware, waiting on its own
  access point; the architecture diagram it replaces stays where it is
  explained, in `docs/system-overview.md` and the diagram index.
  Features is seven bullets rather than nine, each saying what a reader
  can do or why this beats the obvious alternative, and the thin-fork
  bullet, an implementation fact carrying a 🚧, is the one that left.
  Project Layout is gone: it was a second index beside
  `docs/README.md`, and nothing outside the page linked to it. A
  Documentation section in its place points at the four doors into
  `docs/`, and Credits links the neighbouring-projects survey. The
  `#getting-started` and `#credits` anchors are untouched, because
  three pages link into them from outside.
- **Both hardware tables lead with the board vinga is tested on**
  (#346). The Touch-LCD-1.54 first, then the Touch-AMOLED-2.16 and the
  ePaper-1.54, both marked `planned 🚧`, in the project README and in
  `vinga-esp32/README.md` alike. The sentence above the project
  README's table called all three boards ones vinga "targets and
  tests", which stopped being true once two of the rows became
  planned; it now distinguishes the one board this project is
  developed and tested on from the two it targets.

### Removed

- **The OpenAPI TypeScript client spike is no longer in the tree.**
  `spikes/2026-08-20-openapi-ts-client/` was written to answer one
  question for #129, said in its own README that none of it ships, and
  is answered: the per-criterion evaluation, the findings and the
  recommendation are the M5 section of
  `docs/plans/2026-08-19-governance-simplification-implementation.md`,
  and they are what the admin UI will build on. The code stays
  recoverable from the annotated tag `spike/openapi-ts-client` and from
  PR #222. With it goes the `spikes/` directory, whose other occupant
  was archived the same way in 2026-08.

### Fixed

- **A conversation test no longer races the writer's two transactions**
  (#367). A marker commits the durable half first (the session row, the
  turns) and the events after it, holding no lock in between, so the
  turn row is visible before its events are. The mid-session read test
  waited on the turns and then asserted about the events, which on a
  runner slow enough to widen that interval found no `heard` row at all.
  It waits on the events half now, which the durable half strictly
  precedes.

## 2026-08-31

### Added

- **Whether an agent remembers at all is now one line of its
  configuration** (#83). An agent, or the `agent_defaults` layer every
  agent that names none inherits, carries a `memory` section with one
  field: `memory: {enabled: false}` opts that agent out. It inherits
  and replaces exactly as `filler` does, and it is on where nothing
  says otherwise, so a deployment that writes none of this behaves
  exactly as it did.
  Off is the whole family at once. The agent is offered none of the
  seven memory tools, is answered as it would be for a name this server
  does not publish if it asks for one anyway, and is sent none of the
  injected blocks: not its own facts, not the conversation's ledger,
  and not the notes its siblings on the same board keep. Tools and
  injection are one feature seen from two sides, and half of it would
  be worse than either whole answer: an agent that could be told things
  and never write them down would recall for ever and never learn.
  Nothing already stored is deleted by switching it off. The rows stay
  under the agent's name, `vinga memory list agent` still shows them,
  and switching it back on is an agent that remembers what it
  remembered before; `vinga memory delete` is what takes rows away.
  A reload applies the change at that agent's next utterance, and one
  reply never gets half of it: which tools a reply offers and what its
  prompt carries are resolved together, once, before its first round.
  `vinga agent preview` honours the section, so what it prints is what
  that agent is sent.

## 2026-08-30

### Added

- **What this deployment remembers is an operator's to read and to
  correct** (#83). A scope-addressed `/api/memory` namespace and a
  `vinga memory` noun in front of it. `vinga memory list agent` says
  which agents are remembering anything and how much,
  `vinga memory list agent poet` says what one of them holds with the
  number each fact is addressed by, and `device` and `conversation` are
  the same two questions about a board's notes and about one
  conversation's ledger. `vinga memory set agent poet 7` corrects one
  fact in place, keeping its number, and `vinga memory delete` removes
  one fact, the whole of one memory with `--all`, or one entry of a
  conversation's ledger. Every deletion through this door is permanent:
  the soft forgetting an assistant does belongs to the conversation
  that spoke it, and this door is correction and audit rather than that
  flow.
  The listings answer owners nothing is configured under, which is what
  they are for: renaming an agent orphans what it remembered and
  replacing a board orphans that board's notes, and `--all` is how
  those rows leave.
  A corrected fact and the name of a ledger entry are read from a file
  or from standard input and never from an argument, and they travel in
  a request body and never in a URL. Both are content somebody said or
  a model chose, so either can be shaped like a credential, and an
  argument reaches shell history and the process list while a path
  reaches proxy and access logs.
  The `memory` schema is still granted to no read-only role, so this
  API is the surface rather than SQL.
- **An assistant can correct, remove and bring back what it
  remembered, and keep notes about the place** (#83). `remember` gains
  a scope and answers with the number the fact is kept under, and four
  builtins join it. `update_memory` replaces one fact's words by that
  number; `forget` removes one and answers with the words it removed,
  which the assistant is asked to say out loud so the user can ask for
  it back with `restore_memory`; `permanently: true` erases outright
  instead, with nothing to bring back. `recall` looks facts up by their
  words over everything the agent and its device hold, newest first,
  and is where the numbers come from: the injected block shows none.
  Every numbered operation reaches the agent's own facts and its
  device's and nothing else, and a number belonging to somebody else is
  answered exactly as a number belonging to nobody.
  `remember` called with `scope: device` keeps the fact for the board
  rather than for the agent, shared by every agent bound to it: the
  room, the household, how the hardware here behaves. It is steered by
  the tool's description rather than enforced, and the two memories stay
  separate in every direction. A device note therefore reaches the LLM
  provider of every agent on that device, which is worth knowing before
  telling one something about your household; storage never leaves your
  host, and `server.local_only` is the guard it always was.
- **A conversation can write down what is currently true in it** (#83).
  Two new builtins, `set_state` and `clear_state`, keep a small keyed
  ledger for the conversation happening now, under names the assistant
  chooses; writing the same name again replaces what it held. It is
  what a game's scene and hit points, a board position, or the step of
  something in progress belong in, and it is injected as its own block
  above the remembered facts, under a heading that says what is current
  wins. Capped at 4 KiB or 50 entries, and a write past either is
  refused with a sentence rather than trimmed, because a ledger that
  drops entries is one the assistant cannot trust.
  The ledger is keyed by the conversation rather than by the
  connection, so it survives a device hanging up and comes back when
  that conversation is resumed. It shares its conversation's lifetime
  exactly: erasing a thread through the API or pruning it by retention
  deletes it in the same transaction as the thread's turns, and a boot
  sweeps whatever no transaction can reach. On a deployment that does
  not store conversation text a thread can never be resumed, so every
  conversation there starts with an empty ledger and anything worth
  keeping has to be remembered with `remember` instead.
- **Memory grows scopes, editing and a conversation ledger, in the
  storage** (#83). The `memory` schema gains a forward migration,
  `2002_memory_scopes`: `facts` is now addressed by a scope and an
  owner rather than by an agent alone, a forgotten fact is held rather
  than erased until the conversation that forgot it ends, and a new
  `state` table keeps one keyed ledger per conversation. The store
  behind them gains the operations the tools will speak: add with a
  returned id, correct, forget and bring back, a bounded lookup over
  everything an agent can reach, the ledger's writes, one call that
  answers a whole prompt's memory in a single round trip, and the
  purges that take a thread's memory when the thread goes.
  No agent-visible behavior changed. No new tool is offered, no
  injected prompt moves, and every agent remembers exactly what it did
  before: what lands here is the storage and the sentences, and the
  behavior arrives in the milestones that follow.
  One surface an operator reads did move. The `memory_unreadable` and
  `memory_unwritable` events gain a `scope` field, one of
  `conversation`, `agent` or `device`, and their sentences name it; a
  third event, `memory_cleanup_failed`, reports a failure to remove the
  memory of conversations that are gone, carrying the failure's class
  name and no agent, because the paths it answers for act for none. A
  consumer that pins event payload keys should expect the new field.
  **Stop the running server before starting this image.** The
  migration renames a column, so an older process still serving through
  it would fail its next memory statement. Stop, then start: this
  single-server project has no rolling upgrade to preserve, and an
  expand-and-contract migration would price a property no supported
  deployment shape uses. Every fact already stored is carried across as
  it stands, under the scope it always had.
- **A schema for what an agent was asked to remember** (#314). The
  database gains a third schema, `memory`, with one table, `facts`, and
  a migration chain of its own whose baseline is `2001_agent_memory`.
  The server migrates it at every boot the way it migrates the
  conversation record. What lands here is the storage the move needs,
  empty and current; the store that reads and writes it is the Changed
  entry below.
  **Rerun [`deploy/postgres-init.sql`](deploy/postgres-init.sql)
  administratively before deploying this version.** The file now
  creates three schemas rather than two, and on the least-privilege
  contract the deployment documentation states, the server role
  deliberately may not create a schema for itself; a server started
  before the rerun refuses with a fixed sentence naming the rerun and
  repeating no part of the connection. Nothing already stored is
  touched: the two existing schemas keep every row and the new one
  starts empty. The read-only `vinga_ro` role is granted nothing on it,
  with the same explicit revoke the `domain` schema carries, because the
  operator read surface for remembered facts is being designed as an
  addressed API rather than as raw tables.

### Changed

- **An agent may remember far more than a prompt can carry** (#83). The
  agent scope grows from 200 facts in 8 KiB to 1000 in 64 KiB, and the
  block injected into a reply becomes the newest 40 lines within 4 KiB
  rather than the whole scope. Everything past that is still remembered
  and is reached with `recall`, which searches the injected part too.
  Nothing is lost in the upgrade: what was stored is what is stored, and
  an agent whose facts fit inside both of the block's limits, 40 lines
  and 4 KiB, sends exactly the prompt it sent before. An agent with
  fewer than 40 facts that ran past 4 KiB of them is the one case that
  moves, and it moves by injecting less: the oldest lines fall out of
  the block and stay in the memory, where `recall` reaches them.
- **A single fact too long for its scope is now refused** (#83).
  `remember` used to keep one, because the pruning never goes below one
  fact, which left that scope over its own cap for as long as the fact
  lived. It is refused with a fixed sentence asking for fewer words, on
  every door: the tool, the store, and the correction. Facts already
  stored are untouched.
- **Four more reserved names** (#83). Builtin tool names may not be used
  as `mcp_servers` entry names, and the set has grown by `update_memory`,
  `forget`, `restore_memory` and `recall`. A deployment with an entry
  called one of those refuses the boot with the existing sentence naming
  the reserved set; rename the entry and its tools are published under
  the new prefix.
- **`set_state` and `clear_state` are reserved names** (#83). Builtin
  tool names may not be used as `mcp_servers` entry names, and the set
  has grown by two. A deployment with an entry called `set_state` or
  `clear_state` refuses the boot with the existing sentence naming the
  reserved set; rename the entry and its tools are published under the
  new prefix.
- **Erasing a session or a thread answers two more counts** (#83).
  `state` and `held_facts` say how much of a conversation's memory went
  with it, on both `DELETE /api/sessions...` and
  `DELETE /api/conversations/{conversation}`, and `vinga session delete`
  and `vinga conversation delete` print the pair. They come out of the
  same transaction as the counts beside them, so a later failure cannot
  make them false.
- **Agent memory is on whenever the server runs** (#314). It used to be
  a deployment's choice: no `memory:` section meant no `remember` tool
  and no injected block, because a file store needed a directory an
  operator had to pick. A schema needs no such choice, so every agent is
  offered `remember` and every reply is assembled with whatever that
  agent has been told. An agent that has been told nothing gets no
  memory block, exactly as an empty file rendered nothing. A deployment
  that deliberately ran without memory loses that choice until #83 adds
  per-agent control, which is where whether a given agent may remember
  belongs.
- **Remembered facts are stored in the database** (#314). The file
  store is gone: `read` and `remember` now go through the `memory`
  schema added above, one row per fact, with the same rendering, the
  same normalization, the same refusal for an empty fact and the same
  caps (200 lines or 8 KiB, oldest dropped first). The insert and the
  pruning happen in one transaction under the schema's own writer lock,
  so a fact written through one connection cannot be lost by a
  concurrent write through another and no reply ever reads a memory
  that is over its cap, neither of which the per-process lock and the
  file rename could promise. Reads go through a separate read-only
  connection and take no lock, so assembling a prompt never waits on a
  write. Memory is in the backup story it was outside of: it travels in
  the same `pg_dump` as the other two schemas, and there is no longer a
  directory to back up beside the database. A write the database
  refuses answers with a fixed sentence that repeats no part of the
  connection, and emits the new `memory_unwritable` event carrying the
  failure's class name; a read that fails is contained as it always
  was, with `memory_unreadable` and a reply that happens anyway. That
  event's channel moved to `vinga_server.memory.store` with the module
  that emits it.
  **Existing memory files are not carried over.** Whatever sits under
  the old `memory.dir` is not read, not imported and not deleted by
  this release. Database memory starts empty, so every agent begins the
  first conversation after the upgrade remembering nothing and stores
  each fact again the first time it is said; archiving or deleting the
  old files is your own deliberate act.

### Removed

- **The `memory:` configuration section** (#314). It named a directory
  for the file store that no longer exists, so it retired whole rather
  than becoming a key that means nothing. A configuration file that
  still carries it refuses the boot with one sentence saying remembered
  facts live in the database now and the section is retired, and the
  `VINGA_MEMORY` and `VINGA_MEMORY__...` environment overrides are
  refused the same way in any spelling: the refusal names the variable
  and never its value. Both example configuration files lose the block.

### Fixed

- **The simulator's peer-close unit test no longer races the client's
  own close** (#328). `test_a_peer_close_reason_is_read_and_never_relayed`
  asserted which of two closes won: the scripted peer's 4001 and, after
  `tts stop`, this side's own normal one. A contended runner could
  process the normal close first, at which point the verdict is honestly
  "the session ended normally" and the case failed, twice on CI in two
  days on branches that touch nothing here. The peer now holds its 4001
  until this side has reached its close, and this side's close waits for
  the peer's frame to have been read, so the case pins the one
  interleaving it is about. Both waits are bounded and both outcomes are
  asserted, so a runner that outlives either bound says so by name
  rather than handing the verdict back to the race. The client's
  behavior is unchanged: it was correct on both sides of the race.

## 2026-08-29

### Added

- **The structured events, streamed live** (#342). `GET
  /api/runtime/events` answers `text/event-stream` and keeps answering:
  one JSON object per event, carrying that event's catalogued fields
  plus two the stream owns, `ts`, the wall-clock instant it was emitted
  at, and `level`, the name of its level. Those are the fields the
  retained JSON log carries too, under the same names; the log's own
  record-keeping beside them, the channel and the rendered sentence, is
  not repeated on the stream. Nothing is kept behind it, so a reader
  joins the present and reads what happened before from the conversation
  record; what was said in a conversation is not on it, because the
  events are metadata by construction. Three filters narrow it, applied
  as the events arrive: `device` takes a MAC in either separator and any
  case, `session` a session's uuid hex, and `level` a threshold over the
  four level names, defaulting to `INFO`, which is what the retained log
  carries. A reader that falls behind loses its oldest events rather
  than slowing a conversation down, and is told how many by a `dropped`
  event of its own; an idle stream sends a keepalive comment so a proxy
  does not close it; and the stream ends when the reader goes away or
  when the server shuts down, which it now does explicitly, between the
  session drain and uvicorn's own shutdown.
- **`vinga events tail`** (#342). The stream above, read from wherever
  the CLI already reaches, so the two moments that used to mean reading
  the container's log on the server host (watching a board's `ota_check`
  after provisioning, and diagnosing a reply that never came) are a
  command. One physical line per event on stdout: the clock time, the
  level's name unless it is `INFO`, the event's name, and its own fields
  as `key=value`, every non-numeric value rendered as compact JSON so a
  value carrying a newline or an escape sequence arrives escaped rather
  than breaking the line or steering the terminal. `--device`,
  `--session` and `--level` are the query's own three filters, in the
  same words. Without `--follow` it waits for the first matching event,
  prints it and exits 0, which is a scriptable "wait for the next X";
  with `--follow` it prints until interrupted, which is exit 0. An end
  nobody asked for is exit 1 with a fixed sentence on stderr, and
  nothing reconnects: a tail that rejoined across a gap would look
  continuous while missing what happened in it. A reader that fell
  behind is told on stderr, so `tail | grep` still reads only events;
  `tail | head -n 1` answers the shell's own status for a closed pipe
  rather than a traceback.
- **`vinga info`** (#341). One read that says what deployment the CLI
  is talking to: the address it actually contacted, the version and
  revision of the build that answered, the URL to type into a device's
  captive portal with the provenance of the origin in it, and a count
  per configured kind with the default agent. It is two requests on one
  row, because identity is the running server's and the counts are the
  store's, and it opens with the address it reached because a device's
  onboarding URL and the address an operator dials the API on can
  legitimately differ. Getting Started reads the URL with it now, from
  wherever the CLI runs; `vinga ota-url` keeps its place as the offline
  derivation on the server's own host, and `vinga-server doctor` still
  answers whether anything replies on that URL.
- **`GET /api/runtime/info`** (#341), which is what answers it. It is a
  credential-bearing read and is designed as one: the onboarding URL's
  last segment is derived from the device-auth secret and stands in
  front of the endpoint that issues device tokens, so the route sits
  behind the same bearer gate every secret write does, the response
  carries `Cache-Control: no-store`, and the CLI renders the value to
  stdout alone, where it reaches no notice, no refusal, no log record
  and no exception chain. With `server.onboarding.enabled` false the
  URL and its provenance are null and the flag says why; an application
  built without a server around it answers 503 rather than inventing a
  version. Recorded in `docs/architecture/cli-guide.md` as the second
  exception to "a credential is never an argument, and never travels in
  a read", beside `ota-url`.
- **A warning when a provider entry points at localhost from inside a
  container** (#340). The image now sets `VINGA_CONTAINER` in its own
  environment, and a build that meets a `base_url` naming `localhost`,
  `127.0.0.1` or `::1` says so once, at WARNING, on the boot and on
  every apply that rebuilds the entry, naming the entry and
  `host.docker.internal` as the likely fix. The new
  `provider_reaches_loopback` event carries the stage, the entry, its
  type and which of the three spellings it named, and nothing else of
  the URL. Never a refusal: the same configuration is right where the
  endpoint shares the container or its network namespace.

### Changed

- **A bare invocation prints its own help** (#341). `vinga` on its own,
  and every noun with no verb after it (`vinga provider`, `vinga device
  pending`), print the page they were one word short of instead of one
  sentence telling the reader to ask for it. The page goes to stderr and
  the exit code stays 1, because stdout is data and this invocation
  produced none, and a 0 would say a command completed when none was
  typed. `--help` is unchanged: stdout, exit 0.
- **The root help says what the CLI does in the reader's words** (#341).
  Its first sentence opened with "the domain half of the configuration",
  a distinction nobody has met at the moment they run `vinga` for the
  first time; it now names the thing they came to do.
- **`vinga status` is `vinga mcp-server status`** (#341). The read says
  what each configured MCP server is doing, which is a verb of one noun
  rather than of the whole deployment. What it prints is unchanged, and
  there is no alias: the old spelling answers the same refusal any other
  word the grammar does not have answers.
- **`vinga apply` applies what it wrote** (#341). The verb wrote the
  document and stopped, so every operator learned to type `vinga reload`
  after it and the ones who did not left a deployment serving what it
  had before. It now writes the document and installs it, as two
  requests on one row, and prints the reload's listing under the
  outcomes; the per-entity boundary notices are dropped there, because
  the listing under them is the boundary being crossed. `--no-reload`
  writes without installing, which is what a rebuild does while its
  credentials are still missing, and it keeps the notices. A write that
  committed whose reload then failed prints the server's refusal and
  then only what the client knows: the apply was answered and the store
  says what the document says, and no completed reload answer arrived,
  so run `vinga diff` and then `vinga reload` if they differ. The bounds are per request, the write unbounded and the
  reload's sixty seconds. The header `vinga export` writes now names
  three steps, staging the apply so that the stored credentials are
  entered before anything is built.

### Fixed

- **The onboarding line on the OTA GET answers with the address the
  request arrived on** (#340). The same reply derived the websocket URL
  from the request and printed the listen-address guess beside it, so a
  default deployment was told to type `http://0.0.0.0:8003/...` under a
  `ws://192.168.1.34:8003/...` that worked. A configured origin still
  wins (`server.public_url`, then the origin of
  `server.websocket_url`), the request's own address is the fallback,
  and the provenance in brackets says which it was. The startup banner,
  `vinga-server config ota-url` and `vinga-server doctor` are unchanged:
  none of them has a request to read, so they keep the guess and its
  caveat.
- The `vinga` command line imports `Exit` from the vendored Click's core module, where typer 0.27.1 and 0.27.2 both define it, instead of the exceptions module 0.27.2 moved it out of. A fresh install resolving typer 0.27.2 crashed at import; installs pinned by the lockfile were unaffected.

## 2026-08-28

### Added

- **Conversations are a first-class entity** (#190). A conversation is
  a durable thread between a user and exactly one agent, with its own
  identity, a title derived from the earliest utterance stored on it,
  and created and last-active timestamps. Every recorded turn now names
  both the session it was spoken in and the thread it belongs to, so the
  session timeline and the thread are two readings of one set of rows
  rather than two stores. A session that talks to two agents touches two
  threads, and a reply that moves between them records a turn on each:
  the turn that asked belongs to the thread it started on, and the
  greeting the other side answers with is the first turn of the thread
  it landed on, with nothing heard on it, because what the user said was
  said before the move.
- The `conversations_pruned` event gains a `conversations` count beside
  its `sessions` count, because retention's unit is now the thread.
- Turn responses on the API carry a `conversation` field.
- **Erasing a recorded session on demand** (#190, #282). `DELETE
  /api/sessions/{session}` erases one named session, and `DELETE
  /api/sessions` with at least one of `session`, `device` and `before`
  erases every session those selectors name, combined so that all of
  them have to match; `before` is a UTC day and is strict, so a session
  that began at any moment of the named day survives. Both run in one
  transaction and answer the counts they took, per table. Erasure
  outranks the copies the store derived: a conversation whose title
  came from an erased turn is renamed from its earliest surviving
  utterance or loses its title, recap checkpoints whose coverage held
  an erased turn are deleted along with everything descended from them,
  `last_active_at` moves back when the turn that wrote it is gone, and
  a conversation left with no turns is deleted whole. A session that is
  still running when its row goes stops being recorded.
- **A `session` CLI noun**: `vinga session list [--device MAC]
  [--limit N]`, `vinga session show <session>`, `vinga session delete
  <session>` and `vinga session purge [--session ID] [--device MAC]
  [--before YYYY-MM-DD]`. All four are requests to the API and reach no
  database of their own (#281, #282); the two erasures confirm at a
  terminal and take `--force`. This replaces `vinga-server
  conversations purge`, which was removed with the store's file.
- **Reading a conversation as a conversation** (#190). `GET
  /api/conversations` lists the threads this deployment recorded, most
  recently active first and filterable with `agent`; `GET
  /api/conversations/{conversation}` reads one whole; and `GET
  /api/conversations/{conversation}/turns` reads its dialogue oldest
  first, across every session it was spoken in, with the calls each
  turn made nested under it. The listing orders on activity, which
  moves, so it pages on the pair `cursor_active` and `cursor_id`, sent
  together or not at all, rather than on a row id.
- **Erasing a recorded conversation on demand** (#190). `DELETE
  /api/conversations/{conversation}` erases one named thread: its row,
  its turns out of whatever sessions they were spoken in, the calls
  those turns made, and its recap checkpoints. The sessions themselves
  and their telemetry are left standing with a gap in them, which is
  the opposite direction from erasing a session. A thread a deletion
  took is never written to again: a turn still on its way to it is
  discarded rather than recreating the row.
- **A `conversation` CLI noun**: `vinga conversation list [--agent
  NAME] [--limit N]`, `vinga conversation show <conversation>` and
  `vinga conversation delete <conversation>`. All three are requests to
  the API and reach no database of their own (#281, #282); the erasure
  confirms at a terminal and takes `--force`. `show` prints the
  thread's header and then its dialogue as speaker-labelled blocks,
  because a column holding an utterance is a column that wraps. Every
  title and every dialogue line is bounded and made printable before it
  is written, so nothing a room said can add a line or steer a
  terminal.
- **Resuming a conversation** (#190), behind
  `server.conversations.resumption` (off by default, and refused at
  boot without `enabled` and `text`). Every agent is offered two more
  builtin tools: `new_conversation`, which leaves the current thread
  and starts a fresh one, and `resume_conversation`, which takes a
  spoken description ("that thing we were saying about the trip"),
  answers a short list of that agent's own past conversations to read
  out, and picks up the one the user chooses. Only a conversation the
  tool has just offered can be resumed, and only by the agent it was
  offered to. Resuming rebuilds the model's context from the stored
  dialogue, whole turns and newest first, under
  `resumption_budget_tokens` (6000 by default, an estimate of four
  characters per token); a thread longer than that resumes from its
  recent turns and says so, and one whose record has holes in it says
  that too. With the switch off, both tools answer a fixed sentence the
  agent reads out and nothing moves.
- The `conversation_resumed` event: which thread was picked up, how
  many stored turns were rebuilt, how many could not be, and whether
  there was more of the thread than the budget had room for.
- **A recap of a long conversation, only by consent** (#190). Where a
  thread holds more than `resumption_budget_tokens` has room for,
  `resume_conversation` no longer hands over its recent tail: it
  answers with a choice for the agent to put to the user, and the
  answer comes back as its new `start_from` argument. `recent` picks
  the thread up from its recent turns and stores nothing. `recap` runs
  one summarization round against the agent's own model, and the agent
  speaks that summary out loud, word for word as the summarizer wrote
  it. Only once the user has heard it to the end is it stored, as a row
  in `conversation_milestones` recording the range of turns it really
  read; every later resume of that thread is rebuilt from the
  checkpoint plus what was said after it. A recap cut off by an
  interruption, a voice that failed, a device that went away or a
  database that refused is not stored, and the next resume offers the
  same choice again; a summarization that failed or ran long falls back
  to the recent turns with the agent told why. `start_from` is honoured
  only for the conversation the tool actually offered the choice about.
- Recap checkpoints are readable on `GET
  /api/conversations/{conversation}`, which gains a `checkpoints` list
  beside the `milestones` count it already answered: each carries its
  id, the range of turns it covers, the checkpoint it consumed, when it
  was stored, and its text.
- The `milestone_recorded` event: a consented recap was stored as a
  checkpoint on its thread. The thread's identity and nothing else,
  because a recap is a summary of what a room said.
- **The store's durable path**: a marker now commits conversation
  content and telemetry in two transactions with independent fates. A
  durable transaction that meets a transient database refusal is
  retried in place; a batch that is finally dropped marks the threads
  it fed `conversations.incomplete`, so a resume can say the record has
  gaps. The mark is deliberately outside the metrics switch, which
  zeroes `sessions.dropped`.

### Changed

- **BREAKING: the conversation store's three reads move to
  `/api/sessions`** (#190). `GET /api/conversations`,
  `/api/conversations/{session}` and `/api/conversations/{session}/turns`
  become `GET /api/sessions`, `/api/sessions/{session}` and
  `/api/sessions/{session}/turns`, and their response models are renamed
  to match (`SessionList`, `SessionSummary`, `SessionDetail`,
  `SessionTurn`, `SessionTurns`; `ToolInvocation` and `TurnLeg` keep
  their names). What these answer is one connection episode and the
  turns inside it, which is a session; the thread that spans several of
  them is a different entity and gets a namespace of its own. The config
  section `server.conversations.*`, the four `conversations_*` events,
  `vinga-server conversations schema` and the reference it prints all
  keep their names, because those address the store from outside, where
  what a reader is after is the conversations in it. The Postgres schema
  itself is renamed, below.
- **BREAKING: the store's Postgres schema is renamed from
  `conversations` to `record`**. A qualified name now reads
  `record.sessions`, `record.turns` and `record.conversations`, so the
  double word is gone: the schema name says what the whole of it is, the
  durable record of what was said and what it cost to say it, and the
  table name is left saying what one entity is.
  `deploy/postgres-init.sql` creates `record` and grants `vinga_ro` on
  it, and every `psql` recipe in the documentation moves with it. There
  is no migration and none is coming. **The upgrade is: rerun the
  updated `deploy/postgres-init.sql`, then boot.** That is the reset
  story's own shape, and it is the order rather than a convenience: the
  supported server role has no `CREATE` on the database, so a
  deployment provisioned by the previous file has no `record` schema
  and no role that can make one, and a server started before the rerun
  refuses with the fixed database refusal that repeats no part of the
  connection. A deployment whose server role owns its database does
  create the schema at first boot, and still needs the rerun, because
  nothing else grants `vinga_ro` on the new schema. Either way the old
  `conversations` schema stays where it is for an operator to read
  beside the new one or to drop, and what is in it does not come
  across. That is the same pre-release reset this release already
  prices, recorded in
  `docs/adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md`.
- **BREAKING: retention prunes conversations rather than sessions**
  (#190). `server.conversations.retention_days` is unchanged (90 by
  default, `0` keeps everything); what it is measured against is now a
  thread's last activity. A conversation older than the window is
  deleted whole, with its turns; events are pruned on their own
  session's age; and a session record is deleted once no turn names it
  any more. A deployment that does not resume conversations sees the
  behavior it saw before, because a thread's age and its session's
  coincide there.
- **BEHAVIOR: an agent handed a conversation no longer reads what was
  said to the agent before it** (#190). A conversation is a thread
  between a user and exactly one agent, and each agent in a session now
  keeps its own: `switch_agent` binds the incoming agent to its own
  thread and its own history, so what it starts with is the instruction
  to greet and carry on, and switching back returns an agent to what it
  was saying. Until now the whole session transcript carried across a
  switch, which moved words spoken to one agent onto whatever provider
  the next one runs on. Carrying context deliberately ("ask Nadia about
  this") is a separate capability and is not built.
- `turns.agent` now records the agent that OWNS a turn, which is the
  one the turn started with, rather than the one that finished the
  reply. A handover makes those different, and `turns.legs` is where a
  split reply's per-agent shares live.
- The cli-guide's noun-naming rule keeps its wording and loses its
  example: `sessions list` was the spelling it illustrated a plural
  noun with, and the noun it named is now `session`, singular, because
  it takes `show` and `delete`. The two merged plural command groups
  (`conversations`, `events`) address no entry and keep their names.
  The reasoning is in
  `docs/plans/2026-08-28-first-class-conversations.md`.

### Removed

- **BREAKING: databases stamped `1001_postgres_conversations` are
  unsupported** (#190). The conversations migration chain is re-cut:
  the single baseline is replaced by `1002_conversation_threads`,
  carrying the whole thread schema. There is no migration and no
  backfill, because `turns.conversation` is not null and a turn
  recorded before threads existed names no thread. A server that meets
  such a database refuses to start with a sentence naming the reset:
  drop and recreate the database (or the `record` schema on its own),
  rerun `deploy/postgres-init.sql`, and start again, which migrates a
  blank schema to current in one step. **The conversation record is not
  carried across**; take a `pg_dump` of the schema first if it is
  wanted. The schema rename above means a store recorded by an earlier
  build meets neither this refusal nor an upgrade, because the schema
  it lives in is no longer the one the server migrates; what it meets
  is the rerun above. The compatibility
  decision and the tested path back are recorded in
  `docs/adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md`.

## 2026-08-27

### Added

- A CI lane for documentation changes: `.github/workflows/docs.yml`
  runs the internal link-and-anchor check and the command-spellings
  census on every change the server workflow's path filter ignores,
  so a documentation-only merge can no longer stale the census with
  no run going red (#329). The link checker is committed as
  `scripts/check_doc_links.py`.
- **The compose file carries the server too, behind a `server`
  profile** (#309). `docker compose --profile server up -d --wait`
  starts the published image and its Postgres together, health-gated
  on both, with the database on loopback and the server's 8003 on
  every interface because boards on the LAN have to reach it. The
  development loop is untouched: `docker compose up -d --wait` with no
  profile still starts the database alone. The two secrets have no
  default anywhere and arrive through a required `env_file`
  (`./.env`), which is the one thing compose resolves for the selected
  services alone: `${VINGA_API_SECRET:?…}` would have refused the
  profile-less invocation too. With no file compose refuses before a
  container starts. After that the two secrets differ: the
  configuration API is always mounted behind its token, so a missing
  `VINGA_API_SECRET` always refuses the boot, while `VINGA_AUTH_SECRET`
  is required exactly when device authentication is on, so the quick
  start's `VINGA_SERVER__AUTH__ENABLED=false` trial boots without one
  and serves an onboarding URL with no key in it.
- **CI guards the committed compose shape** (#309). The `unit` lane
  asserts that the profile-less invocation resolves with no secrets
  and lists exactly `postgres`, that the `server` profile refuses
  without them, and that it lists both services with them. The `image`
  job boots the file against the image it has just built, pointed
  there with `VINGA_IMAGE`, and asserts the revision the served
  `/healthz` reports.

### Changed

- **The quick start is the compose pair, and needs no checkout**
  (#309). Getting Started step 1 was seven commands that spelled the
  compose file's contents out by hand (a network, two `docker run`s, a
  `pg_isready` loop, two exports); it is now two `curl`s and one
  command, with the provisioning file fetched into `deploy/` beside
  the compose file because that is the relative path the bind mount
  names. Nothing in the README restates a value the file holds. Step 2
  reads the API token back out of the same `.env`, step 3 loses its
  Linux-only `--add-host` caveat because the server service resolves
  `host.docker.internal` on every platform, and step 5 is
  `docker compose exec vinga vinga-server config ota-url`, which needs
  no `--profile` once the stack is up. The single-container
  `docker run` keeps its one home in the server README's
  [Running in a container](vinga-server/README.md#running-in-a-container),
  which now says which of the two stories it is.

- **The domain model distinguishes what is built from what is
  decided** (#310). Every section of `docs/concepts.md` opens with its
  status, `Implemented today` or `Decided direction`, and each
  direction names the issue or record that owns it: the cross-session
  conversation (#190), the durable per-device record (#96), memory
  scopes (#83) and their move to Postgres (#314), the help agent
  (#21), and the wake-word-audio question that is still open (#112).
  A direction nobody owns says so in those words rather than
  borrowing authority from the page, so the page can be read as a
  decision record for nothing. Where its semantics had drifted from
  #190 they now defer to it: a fresh wake starts a fresh thread and
  resuming an earlier one is always asked for, threads are listable,
  deletable and covered by retention rather than suspended forever, a
  long thread is recapped only by consent, and a session and a
  conversation are two views of one set of turns. Protocol and
  configuration mechanics are linked to the Xiaozhi notes and the
  generated references instead of restated.
- **The Xiaozhi notes separate what is maintained from what is dated**
  (#310, #312). `docs/xiaozhi-notes.md` now says of every section which
  of four things it holds: maintained protocol facts, dated field
  observations, the historical reading of the upstream server, or
  licensing evidence. The maintained sections carry an explicit
  upstream currency statement, naming the two vendor-clone commits they
  were last read against and the two firmware versions actually
  observed on boards, which is what the stock-firmware promise's
  version target is measured in. The clone commands stay at the top,
  and the constraints stock firmware puts on the server each have an
  anchor now, so a page that summarizes one can link it.
- **Board procedures live with the device guides** (#310, #312).
  Writing an `ota_url` into NVS over USB, resetting a board, reading
  its boot log and reading its NVS back moved from the notes to
  `docs/devices/README.md`, and the AMOLED's silent portal save to that
  board's own guide; `AGENTS.md`, the root README and the firmware
  README point there now. The notes keep the shared protocol behavior
  those procedures exercise. Four claims were reconciled in the same
  pass: the root README's "no activation" against the activation code
  its own quick start describes, the notes' two claims that predate
  onboarding landing (#40), and the Touch-LCD guide's account of what
  the notes say about wake-word audio, which is open (#112).
- **The generated references point back at the domain model** (#310).
  `docs/reference/domain-config.md` and
  `docs/reference/conversations-schema.md` each carry one sentence,
  written through their generators, sending a reader to
  `docs/concepts.md` for what the fields and the rows mean to a user.
  The conversation store's sentence also names the collision its own
  schema invites: the `conversations` schema holds sessions and
  turns, and the cross-session conversation is a domain noun that has
  no table yet.
- **Every summary now answers to a source** (#310, #313). The audit
  that closes the reorganization read the root, server and firmware
  READMEs, `AGENTS.md`, the glossary, the regression suite, the device
  guides and the generated-reference introductions against the pages
  that own what they summarize. The summaries that stated a
  commitment in their own words now link it: thin device and smart
  server to the guidelines, the first-class local deployment to the
  promises, the event names in the glossary and the regression suite
  to the generated event reference. `AGENTS.md`'s description of the
  protocol notes matches the notes as they now stand and names
  `docs/README.md`, which is where a page's class is declared. Two
  generated introductions named a source file under a path the
  repository does not have, corrected through their generators. No
  page under `docs/` still routes a reader through
  `architecture/principles.md` for content, including the two server
  docstrings that did.

## 2026-08-26

### Changed

- **Both stores move from SQLite to Postgres, and SQLite support is
  removed rather than kept as a fallback** (#283). The domain
  configuration and the conversation record now live in one database,
  each in a schema of its own (`domain` and `conversations`), which is
  also where each Alembic chain keeps its version table and what the
  read-only analyst role is scoped to. **From this release the image
  refuses to boot without a database**: a server that cannot reach the
  one it was pointed at stops with a fixed sentence naming the
  variables to check, and nothing of the connection is repeated back,
  because a database URL carries a password in its authority and can
  carry another in its query. The developer loop is
  `docker compose up -d --wait` from the repository root and nothing
  else; a deployment names its own instance and provisions it with
  `deploy/postgres-init.sql`.
- **`server.database` is four keys instead of a directory** (#283):
  `host`, `port`, `name` and `user`, defaulting to the compose
  service's own values. Their documented environment spellings are the
  short `VINGA_DB_HOST`, `VINGA_DB_PORT`, `VINGA_DB_NAME` and
  `VINGA_DB_USER`, because the compose file feeds the Postgres image
  from the same four and one `.env` flows into both sides; the generic
  `VINGA_SERVER__DATABASE__*` spelling is refused with a sentence
  naming the short one. `VINGA_DB_PASSWORD` has no configuration key
  at all, and neither does `VINGA_DB_URL`, which replaces all five when
  it is set and accepts only the `postgresql` and `postgresql+psycopg`
  schemes. The image no longer sets any database variable of its own,
  and `/data` is now model caches alone.
- **The retryable-409 contract keeps its shape on the new
  vocabulary** (#283). Every write transaction takes its store's
  transaction-scoped advisory lock before it reads, so writers still
  serialize whole and validation still runs against a state no
  concurrent writer is mutating; `lock_timeout` bounds each lock
  acquisition at ten seconds, and a writer that cannot take the gate
  inside it gets the same retryable refusal in the same words. The
  two string sniffs for "locked" or "busy" collapse into one
  classifier over a closed set of psycopg errors, matched by type.
- **The conversation reads answer their ordinary empty shapes for a
  deployment that never recorded** (#283), where they used to answer
  404. The distinction that 404 drew was between a `conversations.db`
  that existed and one that did not; there is no file, boot migrates
  the schema whether or not recording is on, and empty tables are not
  a recording. The 404 for a session id that is not there is
  unchanged. `server.conversations.enabled` still decides whether any
  row is ever written.
- **The read-only analyst role replaces copying a database file**
  (#283). `deploy/postgres-init.sql` provisions `vinga_ro` with
  `SELECT` on the conversations schema, now and on tables created
  later, and nothing at all on the domain schema where the stored
  secrets' ciphertexts are, plus role-level timeouts so a session left
  open in a terminal cannot make the next boot's migration wait. Reading
  the record is a live `psql` as that role rather than a WAL-safe copy.
- **Deletion says what it really promises** (#283). A deleted row is
  invisible to every transaction that begins after the deletion
  commits, including the analyst role's; a repeatable-read transaction
  already in flight keeps seeing it until it ends; and reclaiming the
  space is the database server's own storage maintenance. The
  `secure_delete` pragma, the truncating checkpoint and the promise of
  zero overwritten bytes go with the file they were about.
- **The documentation tree declares what each page may claim** (#310).
  `docs/README.md` now states one closed set of seven authority
  classes (product promises, guidelines, maintained maps and
  explanations, generated references, decisions, dated execution
  records, and research and field notes) and classifies every page
  against it, by directory where a directory holds one class and page
  by page where it does not. Audience stays what it always was, a
  routing concern, and is no longer read as a claim to authority.
- **The architecture corpus has one landing page, organized by reader
  question** (#310). `docs/architecture/README.md` routes by what
  brought you: designing a feature or deciding direction, splitting a
  file or naming an interface, adding a command, placing a datum, or
  understanding a conversation end to end.
- **The conversation walkthrough is `docs/system-overview.md`**
  (#310). The step-by-step tour of one turn, from the wake word to the
  spoken reply, moved out of the architecture README to a page of its
  own for the audience it was written for. Its old callers point at
  the new path.
- **Diagrams live under `docs/architecture/diagrams/`** (#310), still
  a directory per authoring tool, now with one index over both that
  says which question each diagram answers, including what separates
  the two architecture-overview pictures.
- **The promises and the guidelines are two documents** (#310).
  `docs/architecture/product-promises.md` holds the three commitments
  to the person running vinga, and the database promise there now
  states the operational floor it always implied: in-place upgrades
  begin at the two Postgres baselines, this build opens no SQLite
  file, a pre-beta reset is possible if recorded, and recovery is
  export and reapply with the secrets re-entered from the
  environment. `docs/architecture/guidelines.md` holds vinga's
  identity and the revisable defaults, with the three hardware-edge
  principles consolidated into one guideline.
  `docs/architecture/principles.md` stays as a compatibility page for
  the dated records that link it and holds nothing of its own.
- **`docs/architecture/pipeline-ownership.md` is gone** (#310). Which
  parts of the pipeline any framework provides and which are vinga's
  own semantics, and the three conditions that reopen the framework
  question, are now a guideline; the dated pipecat measurements stay
  where they were taken, in the alignment spike's implementation
  record.
- **The CLI guide leads with its reviewer checklist** (#310). The
  eleven questions a new command answers are the first section of
  `docs/architecture/cli-guide.md` instead of the last, each linked to
  the practice or grammar rule behind it, with the reasoning after
  them and an On this page section over both. Where the guide restated
  current command spellings it now links
  `docs/reference/cli.md`, whose generated half cannot describe a CLI
  this repository does not build.
- **The CLI guide's source audit is a dated record of its own**
  (#310). The walk of four published CLI guides, one row per
  guideline, is `docs/architecture/cli-guide-audit.md`, dated
  2026-08-24 and classified as research: evidence behind the guide,
  outranked by it wherever the two disagree about the code today. The
  guide keeps a short summary of the four sources and what became of
  them.
- **The observability page leads with the current data map** (#310).
  `docs/architecture/observability-surfaces.md` opens with the
  four-surface table and a status column read from the repository:
  the structured events, the conversation store and capture have all
  landed, and the audit surface is still future with no issue owning
  it. Exact event variants and schema columns are linked to their
  generated references rather than copied. The 2026-08-15 needs
  assessment and external survey are now a dated decision-evidence
  appendix at the foot of the page.

### Removed

- **Everything that was about a SQLite file** (#283): `server.database.dir`
  and `VINGA_SERVER__DATABASE__DIR`, the `vinga.db` and
  `conversations.db` filenames, the stranded-database refusal and the
  machinery behind it (no Postgres database can be stamped at a
  SQLite-era revision, because the only databases carrying those stamps
  are files this build cannot open), the write-ahead-log checkpointing,
  and the `device_bindings_snapshot_only` event, which said there was no
  configuration database at a given path and can no longer be reached.
  Conversation history is **not** migrated by anything: a deployment
  that wants to keep what it recorded copies `conversations.db` aside
  before upgrading, and the old files and their `-wal`/`-shm` sidecars
  are archived or deleted deliberately, because nothing in this build
  will touch them again.

## 2026-08-25

### Added

- **`vinga simulator run URL` holds a conversation without a board**
  (#248). The verb beside `check-in` does everything that one does and
  then opens the websocket: the handshake with the four headers the
  firmware sets, the hello exchange at whichever framing version the
  check-in reply named, one packaged utterance of Opus paced the way a
  microphone delivers it, and the transcript and the reply's sentences
  printed as they arrive. The terminal is the display. Half duplex, one
  turn, and one sentence: the audio is encoded once at build time and
  shipped inside the package, because no client tier carries a codec and
  packets encoded once are byte-identical on a laptop and on a runner.
  The sentence is "Hello, can you hear me?", synthesized with Piper from
  a voice whose weights are MIT over a public-domain dataset, with the
  provenance and a checksum recorded in a manifest beside it.
  The websocket client rides a **`sim` extra** carrying exactly one
  distribution, so the default install stays the configuration client;
  asked for a conversation without it, `run` names the extra and stops
  before it sends anything. `check-in` needs none of it. The capability
  table on both verbs' help pages moves with the code: everything the
  conversation half does is now on the supported side, and the "not
  available yet" side is empty.
- **`vinga simulator check-in URL` puts a simulated board in the
  grammar** (#248). Trying a deployment used to need hardware; the
  protocol a board speaks is HTTP and a websocket, and this command
  speaks the HTTP half. It performs the real OTA check-in that a board
  makes on every boot, with the two headers the handler reads and the
  system-info body the firmware sends, and prints what a board at that
  address would be handed. Both halves of the board's identity are
  derived rather than stored: `--mac` defaults to the documented
  `02:00:00:00:00:01`, whose leading octet carries the
  locally-administered bit, so a binding sticks across runs with nothing
  written to disk, and a second board is `--mac 02:00:00:00:00:02`.
  The reply is read into four states, and the fourth is the one that
  costs an evening: a board whose MAC is not bound is answered `200 OK`
  with an empty token and no activation section, which this reports as
  a board that may not speak rather than as a success. `--claim AGENT`
  binds the board through the configuration API, waits where a waiting
  board waits, and checks in again, which is the only thing that mints a
  token. Without the flag no API token is read and no API request is
  made. **What it does not do is on its own help page**, in both
  directions and rendered from the same table the tests read, so nobody
  debugs a deployment believing this is a board: the conversation half
  is not in this version and says so.
- **`vinga-server doctor` and the simulator share one device-facing
  address module** (#248). The policy that permits a plain `http://`
  address to any host (which is what a board on a LAN is pointed at),
  the stand-in name every verdict uses instead of the URL, and the
  request lifecycle that holds the request loggers quiet, follows no
  redirect and names a failure by its class alone now live in one place
  rather than in two. Nothing changes about what `doctor` does.

### Changed

- **User-facing documentation leads with the standalone CLI** (#304).
  The root README's Getting Started drove every configuration step
  through a shell function that exec'd into the container
  (`vinga() { docker exec -i vinga vinga-server "$@"; }`), so a reader
  came away believing vinga is administered by getting a shell inside
  it, while the API-speaking client #223 shipped appeared only in the
  simulator's one `uvx` line. It is now seven steps, each one action:
  start the container, install the client with
  `uv tool install "git+https://github.com/rafacm/vinga#subdirectory=vinga-server"`
  and point `VINGA_API_URL` and `VINGA_API_SECRET` at the deployment,
  apply the committed preset fetched from the repository, flash, get the
  OTA URL, provision, talk. The prose the section was carrying a level
  down (the `__` delimiter shape, the YAML mount, the image tag policy,
  the `apply` transaction semantics) is now a link to the section of
  `vinga-server/README.md` that already owns each fact. One step stays a
  `docker exec`, and says why: `ota-url` derives its URL from the file
  half and the device-auth secret, which live with the server.
- **The docker shim is the advanced door, not the first one** (#304).
  `docs/reference/cli.md` presented it first of three; the order is now
  the workstation client, a checkout, and the image, and the client door
  names `uv tool install` alongside the one-off `uvx`. The shim keeps
  its place and gains the fact that makes it advanced rather than
  deprecated: it is the same client making the same requests, running
  where the token already is, so nothing about it goes around the API.
  `vinga-server/README.md` moves the same stance into Configuration and
  Running in a container, whose blocks now read as the recipes generated
  from the example fragments do.
- **`docs/README.md` navigates by intent** (#304): start here,
  reference, device guides, architecture, research notes, and the
  record, with the user-facing versus working-notes distinction stated
  up front. Five reference pages that were missing from the index are on
  it (the CLI, the domain configuration, the API contract, the events,
  the conversation store schema), as are the CLI guide, the
  observability surfaces and the pipeline ownership inventory.
- **The image workflow's Docker actions moved to their Node 24 majors**:
  `build-push-action` v6 to v7, `login-action` v3 to v4,
  `metadata-action` v5 to v6, `setup-buildx-action` and
  `setup-qemu-action` v3 to v4. GitHub is retiring the Node 20 runner
  runtime and every run warned that these five were being forced onto
  Node 24; the new majors target it natively. No inputs or outputs the
  workflow uses changed.

- **A claim the configuration superseded names the condition, not the
  device** (#248). Claiming a code that a binding or a default agent
  overtook while it sat on a screen is still refused, still changes
  nothing, and still points at the listing; what its two sentences no
  longer carry is the MAC the code resolved to. A caller addresses that
  write by six digits, so the address in the refusal was one the write
  resolved rather than one anybody sent, and the sentence travels into
  an API response body, into whatever keeps the logs, and onto the
  stderr of whichever command was holding the code. Every other refusal
  in the configuration store already named its condition this way.
- **The default install of `vinga-server` is the configuration client**
  (#223). It carries httpx, pydantic, pydantic-settings, python-dotenv,
  PyYAML and typer, and none of FastAPI, uvicorn, SQLAlchemy, Alembic,
  cryptography, the LLM SDKs or the audio stack, so a workstation that
  administers a deployment it does not host installs a client rather
  than a whole server:
  `uvx --from "git+https://github.com/rafacm/vinga#subdirectory=vinga-server" vinga list`.
  Serving from a checkout or an image build needs the new `serve` extra.
  **The published container image is unaffected and operators do
  nothing**: the image build names the extra in its own Dockerfile, and
  a deployment runs the image and installs nothing, as it always has. A
  checkout is unaffected too: `uv sync` still yields a runnable server
  with no new flags, because the dev dependency group names the extra.
- **Four commands need the server half installed** (#223): `openapi`
  and `ota-url` in the configuration grammar,
  `vinga-server conversations`, and `vinga-server doctor` **with no
  URL**. Each reads the server's own code: `openapi` builds the
  configuration application to describe it, `ota-url` and the doctor's
  derivation read the onboarding package, and the conversations group
  renders the store's tables off the SQLAlchemy metadata. On a
  client-only install each stays where it was and answers one fixed
  sentence naming the missing half rather than an ImportError
  traceback. Inside the image and from a checkout they behave exactly as
  before. **`vinga-server doctor <url>` is not gated**: diagnosing an
  address a workstation was given opens a socket and reads an answer,
  which is exactly what a laptop administering a remote deployment
  does, and it keeps working on the client half alone. Only deriving
  the URL needs the other half. The committed
  `docs/reference/api-openapi.json` is where a client-only install
  reads the contract, and `config` and `events` are the client half
  throughout.
- **The configuration grammar is noun first** (#223). `vinga-server
  config set provider llm local` is `vinga-server config provider set
  llm local`, and every command word in the grammar moved with it:
  `bind-device` is `device bind`, `add-device` is `device pending
  claim`, `pending` is `device pending list`, `set-secret provider` is
  `provider secret set`, `set-default-agent` is `default-agent set`,
  `prompt` is `agent preview`, and each of the five configuration kinds
  carries its own `set`, `show`, `export` and `delete`. The flat verbs
  are unchanged, because their subject is the whole deployment or
  nothing stored at all: `apply`, `export`, `show`, `list`, `reload`,
  `status`, `ota-url`, `schema`, `reference`, `openapi`,
  `cli-reference`. How deep the tree goes is derived from the API's own
  paths rather than decided per command: a path segment followed by an
  identity of its own is a sub-noun, which is what makes `provider
  secret set` and `device pending claim` three words, and a trailing
  segment with no identity is an attribute, which is what makes `agent
  preview` a verb. `docs/reference/cli.md`, `domain-config.md`,
  `api-openapi.json` and `events.md` all move with the grammar, the
  last of them because two shipped OTA warnings name the command that
  binds a board.
- **Every description in the command tree is one lowercase sentence**
  with no full stop in it, `apply` included, and a test holds every row
  and every group to it.

### Added

- **`vinga` is a console script of its own** (#223). `vinga provider set
  llm local` and `vinga-server config provider set llm local` are the
  same command through the same entry function; the short spelling drops
  the `config` word because it has no server to dispatch away from.
  Both read a `.env` file at the invocation directory, with the real
  environment still winning. Everything this repository generates is
  rendered in the short spelling whatever invocation rendered it, since
  a generated document may no more vary with the entry point than with
  the terminal; a live `--help` prints the spelling it was reached by.
- **`diff` says what the store holds that the running server is not
  serving** (#223, filling the seat #193 reserved), kind by kind, with
  the boundary each kind's changes reach a conversation at. Names and
  labels only: no bodies, no values, no masks and no secret marks cross
  the surface.
- **A destructive verb confirms at a terminal** (#223). The eight rows
  that delete something ask before they do it, take `--force` to skip
  the asking, and ask nothing at all when stdin is not a terminal, so a
  script is never blocked. `--no-input` disables every prompt in the
  grammar: it refuses a destructive verb, because a confirmation has no
  other way to be answered and `--force` is that other way, and it does
  not refuse a secret write, because a secret has three doors and
  disabling one leaves two. The question and both refusals are fixed
  sentences carrying no address and no other value from the command
  line.
- **`--version`, and `-h` beside `--help`** (#223). `--version` prints
  the installed distribution and its version and exits 0; `-h` answers
  on every page of the tree.
- **A written version-skew policy** (#223). Two of the three install
  doors put the CLI on a different machine from the server it talks to,
  so `docs/reference/cli.md` now says what to do when the two halves are
  different builds: before 1.0, run the CLI from the same release line
  as the server, and upgrade the older half when they disagree. There is
  deliberately no negotiation machinery. The committed
  `docs/reference/api-openapi.json` is the contract, and a mismatched
  pair fails legibly rather than silently: the server refuses a route it
  does not have, and the client answers one sentence for a shape it does
  not recognize. That sentence is not a rollback, which the page says
  where it matters: the shape is checked after the request was answered,
  so a write a newer server accepted is committed whatever this client
  could make of the acknowledgement, and the way to know is to read the
  state back with `show` or `diff`. Which half is older is read from
  `vinga --version` and from the unauthenticated `/healthz`, which
  serves `version` and `revision`.

### Fixed

- **`-f -` at a terminal with nothing piped in no longer hangs**
  (#223). It read standard input unconditionally, so a person who typed
  it at a prompt met a cursor and no explanation. It answers one
  sentence pointing at the help and exits 1, which is what every other
  mistake in the grammar answers with.

## 2026-08-24

### Added

- **The CLI has a design guide, and the grammar is noun first** (#285).
  `docs/architecture/cli-guide.md` records the settled decision that a
  command names the thing before it names what to do to it (`vinga
  provider set llm local`, `vinga agent show kids`), the reasoning that
  chose it over the verb-first shape (kubectl's closed verb set works
  because a new resource inherits every verb; vinga's periphery is
  noun-specific and its noun set is the growing one, which is the shape
  docker had before it moved to management commands), and the rules for
  naming a new noun or verb. Beside them sit eleven practices, each
  with an example from the merged CLI and the shape it rejects, that
  shape labelled merged, historical or constructed so a reader can tell
  a recorded rejection from an argument: the
  stdout/stderr split, refusals as fixed sentences that quote nothing
  back, one sentence and exit 1, notices that say when a write takes
  effect, credentials that are never an argument, the export/apply
  round trip as the machine interface, deterministic output, prompts
  that are never mandatory, the resolution order with no flag that
  weakens the transport, bounded waits, and a grammar derived from the
  models it addresses. The practices were produced by an audit of four
  published guides (ThoughtWorks, clig.dev, Heroku, 12 Factor CLI
  Apps), and the audit is committed with them: 153 rows covering every
  guideline in every source, each carrying exactly one disposition from
  a declared vocabulary of eight (adopted, adapted, owed, rejected,
  deferred, split, tension recorded, not applicable) and the reason for
  it. A `--json` output flag is deferred, with
  the case written out. Rules the CLI does not satisfy yet are marked
  owed rather than described as though they held: noun-verb itself,
  confirmation on destructive verbs, a progress line for the long
  waits, `-h`, `--version`, `-f -` at a terminal, and description
  normalization, where `apply` is the one row of forty-eight that is
  not a single lowercase sentence. Two places where the merged code
  contradicts a rule the guide states are recorded as tensions with
  the issues that track them, rather than softening the rule: the
  fragment and `--from-env` refusals echoing what was given, with a
  `UnicodeDecodeError` escaping as a traceback (#289), and an accepted
  URL reaching later refusals without passing through `shown_url`
  (#290).
- **A provider type can declare what it accepts, and `faster_whisper`
  is the first that does** (#88, M1). Its fourteen options are a
  pydantic model in a new `config/provider_options.py`, with the
  example fragment's own sentence on each field, and the provider
  registry derives its own table from that declaration, so
  construction, write-time validation, read-back and every document
  read one place. A typo in an option is refused when the entry is
  written, with the field named and the value never quoted back,
  instead of at the next build with only the entry named.
- **`elevenlabs` declares its options too, and the hand-rolled version
  is gone** (#88, M2). Its six options and the five keys of
  `voice_settings` are a pydantic model, so the type is documented in
  the same four places the first one is: `vinga-server config schema
  provider tts elevenlabs`, a table per section in
  `docs/reference/domain-config.md`, the `TtsElevenlabsOptions` and
  `VoiceSettings` components of the OpenAPI document, and the `set
  provider` help. What it replaces is the code this pattern
  generalizes: a `voice_settings` reader with its own key tables and
  its own two type checks, and a separate function that parsed the
  output format. Both said something the model now says once.
- **`openai_compatible` declares its options too, and now honours what
  it does not declare** (#88, M3). Its `base_url`, `model` and
  `max_tokens` are a pydantic model like the other two types', so it is
  documented in the same four places, but its door stays open: a key the
  model does not declare is kept rather than refused, because the type
  exists to reach a server this repository has never seen and every one
  of them takes parameters no other does. The change an operator will
  notice is what happens to such a key next. Until now the builder
  refused every option it did not read, so a server-specific parameter
  could not be configured at all; it now travels into the outgoing
  request body, so `top_p: 0.9` on the entry reaches the endpoint
  alongside the model and the messages. Five names cannot be passed
  through, because vinga composes them for every request: `messages`,
  `stream`, `stream_options`, `tools` and `tool_choice`. A key by one of
  those is refused when the entry is written, naming the one that was
  written, since it would rewrite the request rather than configure the
  server; `model` and `max_tokens` are composed too and are not on that
  list, because they are declared options of the type and a key by
  either name is the option itself. The published schema says all of
  this: the component excludes exactly those five names and its
  description lists them. What the hatch does not open is a way past
  anything else: a passthrough key that looks like a secret, or holds a
  URL with a credential in it, is refused exactly as it was, and a
  refusal about an undeclared key names the entry and the rule rather
  than the key.
- **A declared type's options are documented in all four places the
  configuration is documented** (#88, M1). `vinga-server config schema
  provider asr faster_whisper` prints the contract as JSON Schema;
  `docs/reference/domain-config.md` gains a table per declared type
  under the provider section, with nested sections rendered down to
  their leaves; the OpenAPI document carries each model as a component
  named for its stage and type (`AsrFasterWhisperOptions`), with the
  provider PUT's description naming which component a fragment of that
  stage and type carries; and `vinga-server config set provider
  --help` lists the same fields. Nothing changes for the types that
  declare no model yet: their factories are called as before, their
  options are still passed through, and the example fragments are
  still where those are documented.

### Removed

- **`vinga-server conversations purge`, the local session-record
  erasure** (#282). The command deleted whole sessions from
  `conversations.db` by `--session`, `--device` or `--before`, going
  straight to the file named by `server.database.dir` with no server
  involved, and it is gone with everything only it had: its parser row
  and selectors, its `--config` flag, the three caveats in its help, the
  report it printed when a reader deferred the truncating checkpoint,
  and `store.purge()` behind them. The census is what says that was safe
  to take whole. The CLI was `purge()`'s only caller; retention reaches
  the same deletion through `_delete_sessions`, which stays, with the
  checkpoint and the rest of the shared core. Two arguments on
  `existing_engine` existed for the command alone and went with it,
  leaving one read-only shape. `vinga-server conversations schema` is
  untouched and is now the group's only command: it renders the
  reference off the table declarations and opens nothing, which is what
  lets it survive the move.

  It goes for the reason `--local` went the same day: the store is
  moving to a real database, where the file the command opened does not
  exist, and any CLI access that needs local access to that file has to
  be retired before the move rather than ported through it.

  What a deployment has in the meantime is stated rather than left to be
  discovered. Automatic retention is untouched and still prunes whole
  sessions older than `server.conversations.retention_days`, row and
  children together, at startup and at each session close, physically
  and with the log truncated, exactly as before. Manual erasure comes
  back as an act of the conversations API, with a CLI verb in front of
  it (#190), which is where the command's deletion semantics and its
  caveats are recorded. Until that lands there is no way to erase one
  named session, and a deployment that must erase now stops the server
  and deletes three files rather than one: `conversations.db`, and the
  `conversations.db-wal` and `conversations.db-shm` sidecars beside it.
  All three, because a committed row's bytes live in the write-ahead log
  until a checkpoint folds them back, so removing the database alone
  leaves what was meant to be erased in the file next to it. That takes
  every session rather than the one that was asked about. It is said in
  those words in the server README, in `config.example.yaml`, and in the
  generated
  [`docs/reference/conversations-schema.md`](docs/reference/conversations-schema.md),
  whose deletion section is the one committed artifact this moves.

- **`vinga-server config --local`, the break-glass path** (#281). The
  flag opened the configuration database on the server's own disk for
  four commands (`show`, `delete`, `set-secret`, `clear-secret`), and it
  is gone with all of its plumbing: the banner it printed, the subset
  refusal, the `local_ok` column on the command table, every local arm
  of an act, the store opener, the encryption-key load and the per-kind
  renderers behind them. `vinga-server config` is now a client of the
  configuration API and nothing else: it imports no `ConfigStore`, no
  database opener and no encryption-key loader, and opens neither a
  database nor a key wherever it is run. What it still takes from
  `store.py` is three pure helpers, the JSON-transportability check, the
  apply location and the identity splitter, none of which reads
  anything.

  Three things retired it together. The store is moving to a real
  database, where the file the flag opened does not exist and recovery
  against a dead server is a database client's job. Upgrade care per
  change begins at the first public release, so the scenario the flag
  was kept for (a stored row a newer model refuses, killing the boot) is
  not owed machinery yet. And `config export` and `config apply` changed
  the arithmetic: the recovery that is actually written down now is stop
  the server, delete the database, boot clean, apply the export, re-run
  the `set-secret` commands the export annotated, which no longer makes
  a second write path worth four duplicated command families and their
  byte-parity obligations.

  That procedure is now `When the server will not start` in
  [`docs/reference/cli.md`](docs/reference/cli.md) and in the server
  README, and it is driven end to end in the live CLI lane against a
  real server whose database is deleted mid-test: the second export is
  the first one's bytes. What it does not do is repair in place. A
  deployment that wants a surgical edit to the stored rows has one
  through ordinary SQLite tooling against the database file, documented
  as exactly that rather than wrapped in this project's grammar. The
  two refusals that used to name the flag now say what to do instead.

### Changed

- **`openai_compatible` entries are validated more strictly than they
  were, and one thing loosens** (#88, M3). What the type accepts is
  otherwise unchanged in kind, held by the same table-driven parity test
  taken call by call off the reader it replaces: `base_url` and `model`
  are still required and still may not be blank, and `max_tokens` is
  still an integer and never a bool, a float or the digits written as a
  string. The loosening is the hatch: a key the type does not declare
  used to fail the build naming it, and is now accepted and forwarded to
  the endpoint. Two things tighten. A stored entry whose options the
  model refuses is now refused on read as well as on write, which a
  deployment meets as a boot refusal naming the entry. The way out is
  the recovery procedure in `docs/reference/cli.md`: boot on an empty
  database and apply a kept export, or take the row out with ordinary
  SQLite tooling against the database file. And a passthrough key naming
  one of the request's own fields is
  refused rather than silently losing to them. A `base_url` that is not
  a URL is unaffected: that rule was never the reader's and still runs
  at build, where all three stages speaking this dialect ask it.
- **`elevenlabs` entries are validated more strictly than they were**
  (#88, M2). What the type accepts is unchanged in kind, held by the
  same table-driven parity test taken call by call off the reader it
  replaces: a voice id is still required and still may not be blank, a
  bool is still not a number, an unknown `voice_settings` key is still
  refused, and a null written under one of those keys still travels to
  the API. `voice_settings: null` still means no voice settings, which
  is what it meant when that section was read through a call answering
  an empty mapping for a missing key, and `voice_settings: ""` now means
  the same, which is one spelling wider than the reader took and matches
  what the same section accepts under `faster_whisper`. Three things
  tighten. An explicit `null` written where a defaulted option sits
  (`model`, `output_format`) is refused rather than passed on, which
  used to be an assertion failure inside the builder rather than a
  refusal at all; an empty string is unaffected there, because those two
  were never read with a fallback that swallowed one. A stored entry
  carrying an option the type does not declare is now refused on read as
  well as on write, which a deployment that wrote one meets as a boot
  refusal naming the entry, and the way out is the same recovery
  procedure: an export applied onto an empty database, or ordinary
  SQLite tooling against the file. And an `output_format` this stage
  cannot stream is refused by
  its rule rather than quoted back, which is the discipline every other
  refusal here already follows.
- **`faster_whisper` entries are validated more strictly than they
  were** (#88, M1). What the type accepts is unchanged in kind, and the
  parity is held by a table-driven test taken call by call off the
  reader it replaces: a boolean is still not a number, `"5"` is still
  not an integer, an empty temperature ladder is still refused and a
  scalar one is still taken as a ladder of one, and the blank spellings
  of an absent option (`model: ""`, `device: null`, `vad_parameters:
  null`) still read as the default. One thing does tighten: a stored
  entry carrying an option the type does not declare is now refused on
  read as well as on write, which a deployment that wrote one meets as
  a boot refusal naming the entry and the field; the way out is the
  recovery procedure, an export applied onto an empty database or
  ordinary SQLite tooling against the file. A delete goes by identity
  and never reads the row it removes, which is what keeps such an entry
  removable once a server is up. The engine's own `vad_parameters` are
  unaffected: that section keeps its door open on purpose and forwards
  every key it is given.

- **The config CLI has a documentation home, and half of it is
  generated** (#194, M4). `docs/reference/cli.md` joins the four
  committed references. Its head is written by hand and covers what no
  generator knows: installing the CLI (inside the container, from a
  checkout, or as a tool of its own), how it finds a server and carries
  its token, why the connection is loopback or TLS with no flag to
  override it, what to do when there is no server to ask, and what
  `apply` and `export` promise. Its second half is generated by a new `vinga-server config
  cli-reference` and diffed by CI like the others: every command's own
  help page, walked off the registration table and rendered through a
  context that states its width and refuses color, so the document
  cannot depend on the terminal it was rendered in. The generated region
  sits between two marker comments and the check rebuilds the page
  around them, so a hand-edited region is reverted by the next run and a
  hand-edited head is nobody's to revert.
- **Two presets, each a whole deployment in one document** (#194, M4).
  `vinga-server/examples/presets/local-stack.yaml` runs on this host and
  reaches no vendor; `cloud-stack.yaml` is the same deployment on vendor
  APIs, with every credential named as the environment variable that
  holds it. Either takes an empty database to a server with something to
  say in one `config apply`. Neither names a device, because which board
  reaches which agent is the one thing a preset cannot know. Both are
  applied against a live server on every build, twice, so their claim to
  be idempotent is checked rather than asserted.
- **Per-topic command recipes, read out of the example fragments**
  (#194, M4). The commented fragments already quote the command that
  installs each of them; the presets quote the apply that writes them;
  the ones that can hold a credential quote the `set-secret` that fills
  the slot. Those quoted lines are now collected, grouped by topic and
  published in the generated half of `cli.md`, rather than being written
  a second time beside the files they describe. Every published line
  runs, in the published order, against a real server in CI, so a recipe
  naming a fragment that no longer validates or a reference written
  after its target fails a build instead of an operator's afternoon.

- **The config CLI is driven over a real socket, and every command it
  registers is** (#194, M3). The six acceptance suites run the entry
  point against the real application with one thing replaced: the client
  factory hands back an in-process test client instead of opening a
  connection. That is the right seam for them and it means the
  addressing, the transport policy, the bearer token and the timeouts
  are exercised only up to it. A new integration suite boots a real
  server on a loopback port and runs the same entry point at it with
  nothing patched, so a refusal is composed by the API, serialized,
  sent, parsed and printed before it is asserted. Coverage is derived
  rather than declared: the suite records which command each successful
  run named and holds that recording to the registration table, so a
  command added to the grammar and not to the suite fails a test instead
  of quietly skipping the lane. All forty-one registered commands run in
  the lane and answer successfully: the thirty-seven that reach a server
  do it over real HTTP, and the four that deliberately reach nothing
  (`config schema`, `reference`, `openapi` and `ota-url`) are held to
  that opposite claim in an environment that names a running server and
  a database directory. Each of the twenty command families has a
  refusal asserted as the whole of what it printed, and every one of
  those refusals is run a second time against an address nothing listens
  on, which is what says whether it was the server or the client that
  composed it. A credential-shaped value is planted in the inputs whose
  shape can hold one, and it is looked for on every surface a refusal
  can come out on: both streams, the log records any thread made while
  the command ran, and the exception the refusal is carried by. Two
  claims that need a real connection are proven here for the first time:
  a document one entry under the limit is applied while an ordinary read
  of the same server under a bound this server cannot meet gives up, so
  `config apply` really does wait however long the transaction takes;
  and an over-limit document is refused with the store read back empty.
  The suite's server is booted from environment variables alone with no
  configuration file anywhere, which is the fileless start the quick
  start will document. It adds about three seconds to the integration
  lane.

### Changed

- **The quick starts are one command against a preset** (#194, M4). The
  repository README's six heredocs and the server README's ten-line
  bootstrap are each one `config apply`, and the paragraphs explaining
  which entity to write before which are one sentence saying that
  applying handles it: a document is validated against the state it
  would leave, so there is no creation order to get right and nothing is
  ever half applied. `config.example.yaml`'s command block moves with
  them and loses its "start the server, configure it, restart it"
  sentence, untrue since the reload landed. The deployment profile's
  domain half (`config.deploy.example.sh`) is one applied document too,
  carrying the same measured values and now binding its device in the
  document rather than in a command after it.
- **The container image starts with no configuration file mounted**
  (#194, M4). It used to set `VINGA_CONFIG=/config/config.yaml` as an
  environment variable, and a named file that is not there is refused,
  so `docker run` with no `-v` failed on a file the operator had never
  named. That refusal is right and stays: naming a path and having the
  typo in it ignored would serve a configuration nobody wrote. What
  changed is that the image no longer names the path on the operator's
  behalf. Its entrypoint names `/config/config.yaml` when a file is
  mounted there and nothing when there is not, so a container started
  with only `VINGA_SERVER__*` variables comes up on the defaults.
  Setting `VINGA_CONFIG` yourself still wins either way, and every
  existing `docker run` that mounts a YAML behaves exactly as before.
- **The example fragments and presets ship inside the package** (#194,
  M4). They were beside `src/` and so in neither the wheel nor the
  image, which left `vinga-server config cli-reference` unable to run
  from either: its recipes are read out of those files. The build now
  carries the directory into the package, the renderer prefers the
  packaged copy over a checkout, and CI renders the reference from the
  built wheel to prove the copy arrived. `vinga-server/examples/` is
  still the one directory anybody edits.

### Fixed

- **A fragment file that cannot be read is refused in this CLI's own
  words** (#289). `vinga-server config set ... -f PATH` used to answer a
  file it could not read with the path it was given and the operating
  system's `strerror`, and a file that will not parse with the path
  again. The path is typed, and `-f` is one option away from the
  commands that carry a credential, so all of it is now one fixed
  sentence per failure, chosen by the class of the failure: no file
  there, cannot be read, not UTF-8 text, or could not be read at all.
  The parse failure calls the file "the fragment file" and locates the
  mistake by line and column, which is what was useful about naming it.
  A file that is not UTF-8 used to be worse than an echo: the decoding
  failure is a `ValueError` rather than an `OSError`, so it escaped the
  boundary as a traceback carrying the buffer it could not decode, which
  for a secrets file pointed at by mistake is the credential itself. It
  is now caught with the rest, and the classes the boundary catches are
  read off the same table the sentences come from.
- **`--from-env` no longer repeats the variable name it was given**
  (#289). The refusal for a variable that is not set said which name it
  was looking for. That name is typed on the command line, and the
  mistake that produces this refusal most often is typing the secret
  itself one word early, where the name belongs. The refusal names the
  rule instead, and says to check the spelling and that the variable is
  exported.
- **A failure after an accepted API URL no longer prints its query
  string** (#290). The transport policy refuses a credential written
  into a URL's userinfo and says nothing about one written into its
  query, which is the other form vendors accept, so
  `--api-url https://host/api?token=...` was accepted and then named in
  full whenever the connection failed or the answer could not be read:
  the two failures an operator retries in front of a terminal. An
  accepted address now travels as the parts it always was, what a
  request is built from and what may be shown, and every sentence that
  names an address names the second. The address is still named, because
  a refusal that named none would leave nobody knowing which one was
  tried.
- **No request the config CLI makes narrates itself** (#290). httpx
  writes one line per request at INFO carrying the URL, which this
  server floors deliberately because for every other caller that URL is
  already public. For this one it is the address an operator typed, so
  the credential the refusals stopped printing was still landing in a
  log record, which is retained in a way a terminal is not. The request
  now runs with httpx and httpcore held at WARNING for its length, the
  way the doctor's probe already ran.
- **An address the client library refuses is a sentence, not a
  traceback** (#290). A hostname that urllib reads and IDNA cannot
  encode passed the transport policy and was refused by httpx when the
  client was built, which was outside the boundary that turns failures
  into sentences, so it left as a traceback quoting the hostname as it
  was typed. Construction, the timeout, the request and the close are
  all inside that boundary now, and an address that cannot be opened has
  a fixed sentence of its own.
- **A base URL carrying a query reaches the right endpoint** (#290).
  With `--api-url https://host/api?token=...`, every request was sent to
  `/api?token=.../<endpoint>`: a client joins an endpoint's path onto
  the base's raw path, and a raw path carries the query, so the
  endpoint's name landed inside the parameter's value and no command
  reached the API at all. The query is now held apart from the base and
  reattached after the path, exactly as it was written rather than
  re-encoded.

## 2026-08-23

### Added

- **`vinga-server config apply -f setup.yaml` writes a whole
  configuration in one transaction** (#194). The document is the domain
  half in the shape `docs/reference/domain-config.md` documents: its
  top-level keys are the configuration's own sections, an entity's body
  is exactly the fragment `config set` takes, and the two settings are
  in the shape the configuration holds them in (`devices` as a MAC
  holding its agents, `default_agent` a name or an explicit null). The
  references are checked once against the configuration the whole
  document would leave, so one document can create an agent and bind a
  device to it in the same breath, which no sequence of single writes
  can: each of those is refused for the state it would leave on its own.
  Applying is **additive** and never deletes, so a section or an entry
  the document does not name is left alone; the one entry that takes
  something away is `default_agent: null`, which is the explicit unset.
  It is **idempotent**, so the same document twice reports every entry
  `unchanged` and writes nothing, which is what makes an applied
  document something to keep in a repository and re-run. And it is
  **refused whole**: any mistake leaves the store exactly as it was, and
  the refusal names every mistake at once in the sentences a single
  write earns. A document may name at most 500 entries, refused before
  anything is written. The command waits for the server's answer for as
  long as the transaction takes, deliberately and with no timeout: an
  apply validates the whole resulting configuration, whose size nothing
  about the request bounds, and a client that gave up on a write the
  server then committed would leave nobody able to say what is stored.
  The API serves it at `POST /api/apply`, which the committed OpenAPI
  document now carries.

- **`vinga-server config export` prints the stored configuration as a
  document `apply` takes** (#194), and `vinga-server config export
  <kind> <identity>` prints one entity as the fragment `config set`
  takes. This is the writable projection where `config show` is the
  display one, which is the whole difference between them. **A stored
  credential never travels in an export**, because a read never carries
  one: an environment reference is a body value and is exported as
  itself, and a credential stored in the database is named at the foot
  of the document as the `vinga-server config set-secret` command that
  enters it. The supported way to reproduce a deployment is therefore
  two steps in this order, which the export's own header states: apply
  the document, then run the set-secret commands it names. Nothing else
  is needed, and an export applied back onto the store it came from
  reports every entry unchanged.

- **`vinga-server config set` accepts `key=value` arguments beside
  `-f`** (#194). `config set provider llm claude type=anthropic
  model=claude-sonnet-5` writes exactly what the equivalent YAML
  fragment writes, through the same validation, the same request and the
  same acknowledgement. A dotted key nests (`filler.enabled=true`), a
  value reads as one YAML scalar (`0.7`, `true`, `null`, a quoted
  string), and a value that reads as a list or a mapping is refused:
  a structure belongs in a fragment, where it can be read. `-f` and the
  pairs are alternatives and never both. **A credential is still never
  an argument**, and every `set` help page now says so and why:
  arguments land in shell history and in the process list, and
  `config set-secret` reads a credential from stdin or from the variable
  `--from-env` names.

### Removed

- **The `random_number` builtin tool is gone** (#245). Every agent used
  to be offered it, under no condition at all, and no agent is offered
  it any more: a model asked to roll a die now answers out of its own
  distribution again, or calls an MCP server that draws numbers.
  Nothing needs configuring for the removal and no deployment has to do
  anything; the two other builtins, `switch_agent` and `remember`, are
  unchanged, as is the rule that neither is granted the way an MCP
  server is. `random_number` is also free as an `mcp_servers` entry
  name from now on, whose tools would publish as
  `random_number__<tool>`, since an MCP tool is always qualified by its
  entry.

- **Domain configuration databases written before this build are not
  upgradeable** (#243). Every entity is now stored as one validated JSON
  body, which no in-place migration can produce, so the domain migration
  chain (revisions `0001` to `0004`) is deleted and replaced by a single
  baseline, `2001_json_body_baseline`. **A deployment carrying an older
  database must delete `vinga.db` (with its `-wal` and `-shm`
  companions) from `server.database.dir` and re-seed the configuration,
  in the same step as the image bump and never after it.** Delete that
  file and not the directory: `conversations.db` lives beside it and
  must be kept. The revision id is one nothing was ever stamped with, so such a
  database is never taken for current: opening it refuses with a
  sentence saying it predates the storage reshape and what to do about
  it. Encrypted secrets come back from the environment through
  `vinga-server config set-secret`, as they were written the first time.
  The conversations database is untouched, keeps its chain and needs no
  reset, and the refusal above is never raised about it. The compatibility decision, what it retracts, and what "tested"
  means for the reset path are recorded in the 2026-08-23 addendum to
  `docs/adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md`.
  The upgrade-from-`0001` tests and their fixtures go with the chain.

- **The event catalog is pinned by one committed artifact instead of
  three** (#241). Two of them are gone: the golden inventory
  (`tests/unit/test_event_golden.py` and
  `tests/unit/data/event-catalog-golden.json`) and the record baseline
  (`tests/unit/data/event-baseline.json`), with the two regeneration
  commands that fed them. Every event change used to cost three
  regenerations and three review surfaces, and no second party consumed
  either JSON file. `docs/reference/events.md` stays the single
  committed pin, regenerated and byte-diffed in CI as before, and it
  gained the one structural fact it did not carry: each argument
  position now names the declared field it renders, so an `ARGS` tuple
  reordered between two same-kinded positions is a diff on a reviewed
  file. Everything the record baseline uniquely proved is a live
  assertion in `tests/unit/test_event_baseline.py` now, needing nothing
  on disk: every produced record conforms to a variant its event
  declares, and a per-driver table of the fields each of the
  eighty-one paths actually carries holds the optional fields that
  `required <= keys <= declared` cannot. The harness of drivers stays
  where it was; what it no longer does is write a file. No behavior
  moved: nothing about what this server emits, or when, is different.

### Changed

- **A mistake in a `vinga-server config` command line no longer repeats
  what was typed** (#194). Every shape of usage error now answers with a
  fixed sentence of the grammar's own, and an unrecognized shape answers
  with a deliberately vague one, which is the rule the `conversations`
  and `doctor` grammars already kept. The one that was leaking is a word
  that is not a command: it used to be answered with `invalid choice:
  'doctor'`, quoting it back, and a mistyped command at this entry point
  is followed by whatever was about to be handed a command that takes
  secrets. Nothing else about a refusal changed: an unrecognized extra
  argument still says that a secret is never given as one, and every
  sentence the API or the repository answers with is passed through as it
  always was.

- **The `vinga-server config` grammar is built on Typer** (#194).
  Internal, with no change to the commands, their arguments, their
  output or their exit codes: the same verbs, the same positionals, the
  same `-f` writes, the same `--config`, `--api-url` and `--local`
  accepted before the command word and after it. What changed underneath
  is that a command is a row in one table rather than a paragraph of
  parser construction, which is what the inline `key=value` writes,
  `apply` and `export` are built on next. Two visible edges of the
  library change: the help's usage line reads `Usage:` where it read
  `usage:`, and every command's help page now lists its options with
  their descriptions, with each `set` command's fragment fields carrying
  the type and the default the generated reference has always shown
  beside them.

- **The device session is four modules instead of one** (#245).
  Internal, and with one deliberate behavior change, the capture-codec
  failure above, which the split's own review round asked for. Beyond
  it: nothing a device sends or receives, nothing a capture records, and
  nothing the logs say is different, and the session's device-facing
  boundary is byte for byte the interface it
  was. `device/session.py` used to carry three clusters that had nothing
  to do with each other beyond happening to belong to one connection,
  and each is its own module now. `device/pacing.py` owns the reply
  audio clock: the Opus encoder, the frame cadence, the pause a barge-in
  confirmation holds it with, and the per-reply latches measured against
  it. `device/capture_audio.py` owns recording's own decode path, which
  cannot share the pipeline's codecs because it records the frames the
  pipeline never sees. `device/watchdog.py` owns both of the deadlines a
  connection is held to, the hello wait and the idle timer, neither of
  which knows what it is timing. What stays in the session is the
  handshake, the wire, the manifest, the close path, and what a deadline
  means.

- **`vinga-server config doctor` is now `vinga-server doctor`** (#244).
  The endpoint diagnostic was about 400 lines of `config/cli.py` that
  lived there only because it reads the `server` section, and it is a
  module and a top-level command of its own now: `vinga-server doctor
  [URL] [--config PATH]`, beside `config`, `conversations` and
  `events`. **There is no alias for the old spelling**, which answers
  the config grammar's ordinary invalid-choice refusal. The diagnosis
  itself is unchanged: the same four verdicts, the same single GET, the
  same refusal to follow a redirect, the same sentences. Seven things
  did change, all of them at the edges. The sentence for a
  configuration with onboarding turned off names the new spelling
  (`vinga-server doctor URL`). The command's usage errors come from its
  own parser now, which answers fixed sentences and never argparse's,
  because the argument an operator mistypes here is a URL and an OTA
  URL can be the deployment's own secret. The entry point's own
  refusals became fixed sentences for the same reason: a first word
  that is not a command names the four that are, without repeating what
  was typed, and the server's argument errors no longer echo a shape.
  And a URL that any of these commands displays now loses its
  secret-shaped query parameters along with its userinfo, so a far side
  reporting `wss://host/ws?token=<secret>` no longer publishes it on
  stdout, and neither does a refusal naming an operator-typed
  `?token=` address. That last one is a security fix, display-only, and
  it reuses the rule `config/models.py` already applies to URL-shaped
  configuration values. The probe also holds the HTTP client's own
  request logging at WARNING while it runs, which is the second
  security fix: that library writes one INFO record per request naming
  the URL in full, and every verdict this command prints hides that URL
  because it can be the deployment's secret `ota_path`. And a
  connection that cannot be closed after the request is now a sentence
  and exit 1 rather than a library traceback, which is what it used to
  be: the close ran outside the handler that sanitizes everything else
  the probe meets.

- **The domain configuration model is declared once, and three
  admin-surface modules shrank around it** (#242). `DomainConfig` was
  written out twice, in `config/store.py` and again inside
  `models.Config`: the same seven sections, the same three validators,
  the same descriptions. It is one declaration in `config/models.py`
  now, and `Config` subclasses it, which is what keeps write-time
  validation to the reference half (a write is still judged against
  the domain half alone, so the first `set agent` into an empty
  database is still accepted) while `config schema` and the generated
  reference render exactly the bytes they rendered before. Beside it:
  `config/writes.py` is gone, its thirteen one-line acknowledgement
  factories written out where each is answered and its two timing
  decisions moved whole to the single path each serves; the descriptor
  registry drops `leads_with` and `always_shown`, whose one fact each
  is now a literal in the display module that asks the question; and
  `outcomes`, `flags` and `RELOAD_SECTIONS` move from
  `config/responses.py`, which holds the shapes two surfaces share, to
  the CLI that prints with them. Nothing an operator writes, reads or
  is answered with changed, which is what the byte-identical
  `docs/reference/api-openapi.json` and
  `docs/reference/domain-config.md` say.

- **The configuration API's document prose lives in data files, and the
  suites stopped pinning sentences** (#242). The nine module-level
  description literals in `config/api.py`, 204 lines of string in the
  middle of the module that owns transport, are one file each under
  `config/api_descriptions/`, read at import by a loader beside them
  that fills `$MASK$`-style sigils from the same constants the literals
  interpolated. A route's own description stays its docstring, which is
  what FastAPI reads it as; the runtime refusal bodies stay in code,
  being behavior an operator meets. A missing file or an unfillable
  sigil refuses at import with a sentence naming it, and because a
  checkout cannot prove a wheel carries package data, CI now renders
  the document from the built wheel with the source tree off `sys.path`
  and diffs it against the committed copy. Beside that, twenty-three
  test files stopped asserting the exact sentence a refusal or an
  acknowledgement carries and assert what it is instead: the status,
  the problem shape, and the semantic tokens (the section a refusal
  names, the entity, the field each problem addresses, the boundary a
  write converges at). Two claims the goldens were holding are held
  better without them, differentially: that the API answers the
  repository's own sentence, and that the CLI prints what the API
  answered. Forty refusal sentences with no reader left outside their
  module are module-private now.
  `docs/reference/api-openapi.json` is byte-identical throughout, which
  is the whole proof that what changed is how the document is produced
  and nothing it promises.

- **A domain entity is one JSON body beside its keys, not a column per
  field** (#243). `providers`, `mcp_servers`, `prompt_fragments`,
  `agents` and `agent_defaults` each keep the columns that carry
  identity, keep their encrypted `secrets` column where they have one,
  and hold everything else in a `body` column: the entity's pydantic
  model dumped to JSON and validated back through the same model on
  read. `devices` and `domain_settings` are unchanged. Nothing an
  operator writes or reads is different, and no configuration key,
  command, route or field moved. What moved is where a field lives:
  adding one to an entity is now a change to its model, to its example
  fragment under `vinga-server/examples/`, and to the two generated
  reference documents, each rebuilt by its own command. There is no
  column to add, no migration to write and no mapper arm to update; the
  five hand-written pairs that translated rows to models are one generic
  pair. A field that later needs SQL-side filtering earns a column back
  through `alembic revision --autogenerate`, which is now runnable:
  `python -m vinga_server.db.migrations.autogen "<message>"` opens a
  scratch database at head and writes a candidate migration for review.
  Because the stored form is the model's own dump, the models are the
  compatibility surface, and real bodies are committed under
  `vinga-server/tests/unit/data/domain-bodies/` so that a model change
  which cannot read one fails CI.

- **The CI unit lane runs in parallel** (#254). The workflow's unit step
  gained ` -n auto --dist loadfile` and the dev group gained
  `pytest-xdist`, taking the longest lane from its measured 6m31s to a
  measured 2m25s and leaving the critical path on the integration lane,
  which now finishes last at 4m54s. `loadfile` keeps a whole file on one
  worker, so module-scoped fixtures stay paid once per file and
  intra-file order is exactly what it was. Local runs are unchanged and
  serial; `uv run pytest tests/unit -q -n auto --dist loadfile` is in
  both command blocks as the way to reproduce the lane. Two supporting
  fixes came with it: the tests' refusal ledger now carries a residual
  refusal (one belonging to no test) from a worker up to the controller,
  which prints it and fails the run; without the repair a worker's
  residual would have vanished silently. And the MCP HTTP suite's
  free-port helper holds its socket for the test rather than releasing
  the number and assuming nothing takes it. The workflow also caps the
  compute thread pools (`OMP_NUM_THREADS` and friends), since
  onnxruntime sizes its pool to the core count and four workers on four
  cores would otherwise ask for sixteen threads, and it gained a
  `concurrency` block so a superseded pull-request run is cancelled
  instead of racing the run that replaced it. A push to main is never
  cancelled: that is the event that publishes the image.

- **The event reference names the field behind each `%` position**
  (#241). `vinga-server events reference` and the committed
  `docs/reference/events.md` print an argument as its declared field
  name followed by its kind, `` `session` (`ID`) `` where the cell used
  to read `` `ID` ``, and the paragraph explaining how to read a variant
  says so. That is the whole of the production change in #241, and it is
  what keeps a reordered `ARGS` tuple a visible diff once the golden
  inventory that used to pin argument order is gone. The document moved
  in its argument rows and in that one paragraph; no template, field,
  token, syntax, grammar or note changed.

### Fixed

- **A value that is not a MAC address is no longer repeated back**
  (#205). The refusal used to be `"<what you typed>" is not a MAC
  address; expected six colon-separated hex pairs, ...`, and it went out
  as a configuration API body, as a line on the CLI's stderr, and into
  the boot refusal for a configuration file. That value arrived in a URL
  path segment or on a command line, which is where a paste lands, so it
  could be a credential typed one argument early. It is now one fixed
  sentence, `a MAC address is six colon-separated hex pairs, for example
  aa:bb:cc:dd:ee:ff`, which still says what a MAC has to be and carries
  nothing of what was sent. This is the same treatment the entity, stage
  and credential-slot refusals were given (#132), applied to the one
  refusal that fires before any lookup. Nothing about a device changes:
  a MAC is accepted in exactly the forms it was, dashes and upper case
  included. The OTA check-in and the conversations query already
  answered with fixed sentences of their own and are unchanged.

- **A capture whose codecs will not open no longer ends the
  conversation** (#245). Starting a recording opens three codec objects
  through a media library, and a library that cannot open one raises. It
  used to take the session down with it, and a device would see its
  conversation end with no explanation. It is now caught where it
  happens: the half-started capture is released immediately, the session
  goes on without a recording, and one warning line names the failure by
  class (`session <id>: recording could not start (<ClassName>)`),
  carrying nothing the library said. This is the same rule the capture
  store already applied to a directory it cannot use, a conversation
  being worth more than a recording of it, applied to the one step that
  had been left out of it. **A deployment with capture enabled should
  watch for that line**: it is the only thing that says a session that
  ran fine was not recorded.

## 2026-08-22

### Changed

- **Three events are declared once instead of twice, and event
  assembly moved out of the reply path** (#240). `llm_retry`,
  `llm_round` and `provider_failed` each had two variants saying one
  sentence about two shapes: one for a provider the registry built out
  of a configured entry, one for a provider it never built. They are
  one variant each now, carrying the entry name, its type, its host and
  its model as fields that may be absent, so a record is identical to
  the one it was before on both paths and the reference describes 82
  variants instead of 85. The code that chose between the shapes,
  ordered their fields and wrapped their values was a third of
  `runtime/pipeline.py`'s executable code; it is `events/assembly.py`
  now, whose functions take plain values and answer the variant that
  describes them. One documented constraint genuinely loosened to make
  the collapse possible: the `quoted_provider` grammar admits the empty
  rendering, because the surviving sentence has to be able to name no
  entry. Nothing an operator reads changed: the committed record
  baseline is byte-identical on every driven path.

- **The CI suite runs as two parallel lanes instead of one queue**
  (#233). The single `test` job paid for everything in a row: lint,
  typing, the unit tests, the integration tests, the four
  generated-document drift checks and the wheel migration, a 12m45s
  critical path of which the unit tests alone were 7m46s. It is now a
  `unit` job (lint, the events type check, the unit tests) and an
  `integration` job (the integration tests, the drift checks, the wheel
  migration) that start together, so the path is roughly the longer
  lane. Nothing changed about what runs or when the workflow runs it,
  and the image job now needs both lanes, so publishing is still gated
  on the whole suite. Both pytest invocations gained `--durations=25`:
  one job's wall time used to make a slow test obvious, and with two
  lanes the durations table is what says so instead.

- **The event baseline harness stops waiting out real provider
  timeouts** (#233). Four of its eighty-one drivers were seventy of its
  eighty-seven seconds, all four for the same reason: the production
  default first-token timeout of ten seconds, waited out in full
  because nothing in those drivers shrank it. The three filler drivers
  now stall the model for half a second rather than thirty, which is
  still eight times the sixty milliseconds their filler timer needs,
  and the retry driver runs both of its halves under the same shrunk
  bound instead of one at the production default. The committed record
  baseline is byte-identical, the four drivers went from 70.1 s to
  1.7 s, and the unit lane is about a minute shorter. A companion
  script, `tests/tools/driver_times.py`, times each driver on its own,
  which `pytest --durations` cannot: the drivers all run inside one
  module-scoped fixture, reported as a single setup line.

- **An event field is declared as the closed set it carries** (#238).
  A variant that says one of a fixed set of words used to name the
  `TokenValue` subclass built for that enumeration, so the set existed
  twice: once as the members, once as a class holding a string checked
  against them at construction. A field can now be annotated with the
  enumeration itself, or with a `Literal` over some of its members
  where a variant admits fewer than the enumeration holds, and the
  catalog derives the kind and the declared set from the annotation.
  Every token field has moved, and `TokenValue` and its twenty-two
  subclasses are deleted with the twenty names they added to the
  vocabulary. The three variants that admit fewer members than their
  enumeration keep their narrowing, and their name, as a `Literal`
  alias. Nothing an operator sees changed: the generated reference and
  the committed record baseline are byte-identical, and a record still
  carries a plain string. The golden inventory now records each token
  field's declared set, so a narrowing stays pinned by something other
  than the reference's prose.

- **Getting Started reads like a front page again.** The eight steps
  keep every command block but shed the deep rationale between them
  (the secret-regeneration trap, write-then-apply semantics, the
  onboarding-code alphabet, remote CLI use), each replaced by a link
  to the vinga-server README section that already tells the full
  story. Nothing was moved, because everything trimmed was already
  documented there.

- **The README's status marks caught up with the code.** The warning
  block still described the pre-vinga demo (upstream firmware and
  upstream server); the loop has run on vinga's own server for a
  while, so it says that now. The Pluggable LLM bullet loses its 🚧
  (the anthropic, openai_compatible, and openai providers all exist),
  and Features gains the bullet the repository description promised:
  configurable agents, several per device, switchable mid-conversation
  through the builtin `switch_agent` tool.

- **The credits say what was actually taken from the upstream
  server.** vinga-server was written against the firmware's protocol
  docs (now hyperlinked where mentioned), not started from the
  xinnan-tech codebase; what carried over is the device-token scheme,
  kept wire-compatible so stock firmware connects unchanged. The
  README credits, the "What is vinga?" story, and the vinga-server
  README opening now say exactly that instead of "starts from" and
  "based on".

- **The tagline and repository description speak of agents, on your
  own terms.** The README header line becomes "Come on, speak, on
  your own terms": the old "with your own hardware" implied a
  local-only pipeline, while the architecture's promise is choice
  (local, hosted, or hybrid providers behind your own server). The
  GitHub description becomes "Conversational AI. Sweded.
  Self-hostable voice agents on ESP32-S3 devices with pluggable LLM,
  voice, and MCP providers", making the agent, the concept the
  configuration surface already speaks (`config set agent`,
  `agent-defaults`, `set-default-agent`), the named thing instead of
  the generic "voice assistant". README and AGENTS.md prose follow.

- **The LLM event loop dispatches with `match`** (#235). The reply
  round in `runtime/pipeline.py` read its provider stream through an
  `isinstance` chain; it now states the same four fates for the same
  four event shapes as `match` arms, with the everything-else
  tool-call arm spelled out as `case _:`, which is the idiom the
  device edge already dispatches its protocol messages in. The stream
  the loop consumes is annotated `AsyncIterator[LlmEvent]`, which is
  what its docstring and its one caller already said it was. Nothing
  an operator sees changed: the same events with the same fields, and
  the same speech.

### Removed

- **The event enforcement layer, and with it the `schema_violation`
  event and `VINGA_EVENTS_ENFORCEMENT`** (#239). The emitters used to
  hold every emission to its declaration a second time at emit, and the
  variable decided what a failure cost: `strict` raised, `forgiving`
  dispatched a declared `schema_violation` event in the emission's
  place. Construction-time validation of every value, strict typing over
  the events package and the CI catalog diff already prevent what that
  layer caught, so it is gone. What stays is the guarantee it was built
  around: an emission that cannot be built costs one plain sentence on
  the emitter's own channel, naming a fixed label and a fixed code, and
  is then dropped, so a telemetry bug still never costs a reply. Three
  surfaces an upgrader can notice: the `schema_violation` event no
  longer exists, so a collector filtering on it will see nothing;
  `VINGA_EVENTS_ENFORCEMENT` is inert wherever it is still set,
  including the image, which no longer sets it; and a misspelled value
  of it no longer refuses to start the server.

- **`config doctor` follows no redirect at all** (#225). It used to
  follow one shape of one: the trailing slash a deployment older than
  the 2026-08-13 checkpoint canonicalized for itself, before every
  device-facing route began answering both spellings of its path
  directly. No supported deployment sends that redirect, so the
  tolerance had no beneficiary, and what is left behind one is a proxy
  or something else choosing where this request goes next inside the
  network a deployment sits in. The probe is one GET now, and every
  redirect meets the refusal that already covered the rest, which names
  neither the target nor a URL an operator supplied. An operator behind
  a canonicalizing proxy meets that refusal instead of a silent second
  hop, and the way out is the one the refusal states: ask the address
  you meant directly, trailing slash included.

- **A device binding is a list of agent names, and only a list**
  (#225). The conversion that read a bare string as a one-agent list
  dates from when the whole domain configuration was a YAML file an
  operator wrote by hand. It is a database now, and every path that
  writes a binding builds a list before the models see it: the API
  refuses a non-list body, the CLI builds one from its arguments, the
  repository wraps what it is given, a leftover `devices:` section in
  the configuration file is refused whole, and the database read
  refuses a stored string. The one route left was composing a
  configuration from a raw mapping, and it now meets the field's own
  type, reported against the device it was written under and without
  the value. Nothing an operator can type moves.

### Fixed

- **The configuration diff reads a grant, not its spelling** (#225).
  An `mcp` entry can be written as the server name on its own or as an
  object naming that server with no tool list, and the two mean one
  grant. The comparison behind `GET /api/config/diff` read them as two,
  so an operator who rewrote one form as the other was told an agent
  was pending a reload that would install nothing. Both the agent
  comparison and the layer under it now read the list as the grants it
  means. Unset stays distinct from empty: an agent edited from no `mcp`
  list to an empty one is still reported, because that edit revokes
  every tool it inherited.

- **An `openai_compatible` LLM refuses a malformed `base_url` at the
  boot that reads it** (#225). The `openai` ASR and TTS types have
  refused a `base_url` with no scheme or no host since the option
  existed; the LLM type, which joined the dialect later, built a
  provider anyway and failed on its first request instead, with the
  round's token counts silently gone. It now meets the same refusal at
  boot or apply. A deployment carrying a malformed URL that has been
  failing every conversation will fail its next boot instead, which is
  where the problem is. The refusal itself stops echoing the value it
  rejected, for all three types: a key pasted where the URL goes is
  exactly the shape that has no host, so the sentence that names the
  problem would otherwise carry the key to stderr, to the API's 422
  body and to the log.

- **The conversation store's event strip is documented as write-time
  only** (#225). Its comments and the generated
  `docs/reference/conversations-schema.md` also claimed it reads a
  database written before the narrowing; no read applies it. Nothing
  about the strip changed.

## 2026-08-21

### Added

- **The external review has a claude fallback backend.** The review
  pipeline's codex quota is a weekly allowance and it can run out
  mid-week; `run-pr-review.sh` now reads `REVIEW_BACKEND`, keeping
  codex as the default and running the claude CLI (`claude-opus-5`,
  read-only tool set enforced by a deny list) when asked. The
  provenance header stamps the backend and its enforcement
  mechanism. A claude round is same-vendor and therefore less
  independent than codex; the skill documents it as the interim
  tier for quota outages only.

- **The domain concepts explain worlds.** A `docs/concepts.md` section
  on how configuration changes arrive: composed and built as a whole
  next world before anything swaps, met by a live conversation only at
  its own boundaries (tools per reply, prompt per activation, engines
  and filler clips per conversation), never mid-turn.

- **Three glossary entries: wire-true capture, output pacing, world.**
  The first two are the properties the pipecat alignment spike
  measured and issues #84 and #92 lean on with nowhere to link; the
  third is the configuration state a running server serves (in code,
  a `Generation`), introduced by the reload work.

- **`docs/architecture/pipeline-ownership.md`.** The inventory behind
  the #84 standing decision, moved from issue comments into the
  repository: which pipeline parts any streaming framework provides,
  which are vinga's own semantics, the spike's measured evidence, and
  the three triggers that would reopen the adoption question.

## 2026-08-20

### Added

- **The stored configuration can be applied to a running server**
  (#191). `POST /api/runtime/config/reload`, which
  `vinga-server config reload` calls, re-reads the stored configuration
  and applies the whole domain half while the process runs: the
  `providers` entries and the `mcp_servers` entries with the secrets
  stored on them, the agents' effective `mcp` grant lists, the shared
  prompt fragments, the agents themselves and the `agent_defaults` layer
  under them. Adding an agent, deleting one, editing a persona, a
  fragment it includes, the voice it speaks in or the phrases it masks a
  slow reply with no longer costs a restart and every conversation on
  the server; a deployment's whole life after the process is up needs
  none. Nothing is swapped, stopped or started
  until the whole new world has been composed, validated and built, so a
  refusal has changed nothing at all; one apply runs at a time, and a
  second is refused as retryable having changed nothing. When a live
  conversation meets the change depends on which half moved: the tools
  an agent may reach are snapshotted per reply, so a moved MCP entry is
  picked up on the next utterance, while prompt text is assembled once
  per activation, so a rewritten prompt, fragment or `instructions`
  reaches a conversation at its next activation, which is a new session
  or an agent switch, and filler clips are bound by a conversation when
  it opens, so a re-synthesized one reaches the next conversation. An
  agent is synthesized again when any field of its effective `filler`
  section moved or when the voice that speaks it did, which a reload now
  moves too, so an edit to a
  prompt sends nothing to a text-to-speech engine while an edit to
  `delay_ms` alone, or to the provider entry an agent speaks through, is
  a round of work at the configured provider, and an
  agent whose synthesis fails applies with no clip and runs unmasked rather
  than making the reload refuse, which the answer's `fillers` section
  reports under its own outcome. The engines keep the same clock and
  cost what they cost: an entry whose definition and stored credential
  have not moved is carried into the new world as the object it already
  was, so an edit to a prompt reloads no local model, while a rewritten
  entry is built before anything is swapped and the conversations that
  open after the apply speak through it. One a conversation is still
  speaking through is released when that conversation ends, so applying
  a change to a local model briefly holds two of it, and an entry that
  will not build refuses the reload with nothing changed. The agent set
  moves with the rest: an agent the store has added is built with
  everything else the apply builds and is servable the instant the
  request answers, so a device bound to it reaches it at its next
  check-in, while an agent it has deleted is one no session can be
  opened as from that same instant and a conversation already talking as
  it finishes on the world it was built from, served that world's prompt
  to the end. The answer carries one section per kind, the `agents`
  section naming what was added and removed and whether `agent_defaults`
  moved. What still waits for a start is the `server` section, which is
  this process's own file and holds nothing this API writes; and an
  agent's memory, which is keyed by its name and stays where its name
  left it.

- **A running server says what it has not picked up yet** (#193).
  `GET /api/runtime/config/diff` answers what the database holds that
  the server is not serving, kind by kind: the entity names added,
  removed and changed, and for each kind the boundary its changes
  converge at, which is `reload` for every kind of the domain half and
  `check-in` for the
  device bindings and the default agent, which a device is answered as
  it asks and which are therefore never pending. An agent entry converges
  at three moments inside the one reload, so its kind carries `grants`,
  `prompt` and `filler` beside its own lists, each a breakdown of what
  changed rather than an exception to it. Until now the only
  trace of a pending change was the sentence in a write's
  acknowledgement, which is gone the moment the response is read.
  Changed means the stored state differs from what is running rather
  than that something was written, so an edit changed back before
  anyone looked produces no diff, and an entity whose stored credential
  was set again is reported as changed because what is compared is an
  opaque mark over the ciphertext. The MCP half is compared against the
  entries running right now rather than the ones the process booted
  with, so a change a reload has already applied is not reported as
  pending. Entity names and those labels are the whole of the answer:
  no bodies, no values and no marks cross the surface, and the refusal
  for a stored half that cannot be read carries a fixed sentence rather
  than the store's own, since the store's names the stored value it
  refused on. The stored half is the re-read the MCP reload begins
  with, so a stored configuration that will not compose, or a
  credential that will not decrypt, is refused here under the status it
  would be refused under there; the equivalence runs that way only,
  because a reload goes on to build a server per referenced entry and
  can still refuse on one of those.

- **Database upgrades have a written compatibility floor.** The
  2026-08-20 ADR records what was previously implicit: upgrades are
  supported from the first beta image onward, best-effort from the
  first revision until then (which the upgrade tests already prove),
  and migration history is never rewritten as a cleanup; squashing
  would be a compatibility decision with a superseding record and a
  tested reset path. Nothing about the databases changes; the
  promise's terms are now a record instead of an assumption.

- **The frontend's client seam is decided, and the evidence is
  committed** (#210). `spikes/2026-08-20-openapi-ts-client/` generates a
  TypeScript client from `docs/reference/api-openapi.json` twice, once
  with Hey API's openapi-ts and once with openapi-typescript and
  openapi-fetch, with exact pinned versions, lockfiles, and the output
  committed so it can be read. Each carries a strict-mode consumer
  fixture, because a generated client can compile and still be unusable:
  the fixtures exercise every read and write and delete the five
  entities declare (the agent defaults are a singleton and have no
  delete, which the fixtures assert rather than assume), the RFC 9457
  problem document a refusal answers with, one field of each
  optional-versus-nullable character, and the provider entries'
  passthrough options, over an exhaustive inventory of all thirty-eight
  operations. The bearer token is the one claim settled by running
  rather than by compiling, since what a type says about an auth option
  is not what a client puts on the wire: each sub-project drives a
  generated operation against an injected fetch that records the request
  and never opens a socket, and asserts the header it observed. Both
  generators are byte-deterministic across three runs and neither output
  was hand-edited. The recommendation is Hey API, on authentication,
  which is the one criterion the two split on; the reasoning, the
  per-criterion results and the two decisions the admin UI still owes
  are in the plan's implementation doc. Nothing here ships or runs in
  CI.

### Changed

- **`POST /api/runtime/mcp-servers/reload` is replaced by
  `POST /api/runtime/config/reload`** (#191), and the old path is gone
  rather than aliased. `vinga-server config reload` moved with it and
  takes no new arguments. The successful body is what the old route
  answered, nested under the new result's `mcp` section, and the
  outcomes, the status document and the status codes are unchanged. The
  one other deliberate difference is the sentence a refused stored half
  carries: it is now fixed and names no location, for the reason the
  comparison read beside it already carried a fixed one, since what a
  reload refuses on is arbitrary stored state and a sentence composed
  over it can quote a value written into the wrong field. A server
  started from the same store refuses on the same state and names the
  location it refused on.

- **Every write of the domain half now names the reload** (#191), and
  says the three moments a conversation already in progress meets an
  applied change at. The one write that says anything else is a device
  binding, which a running server reads as the device asks; a binding
  naming an agent this server is not serving yet says both, since the
  row is live and the agent arrives at the reload that installs it.

- **The comparison read breaks an agent entry into the moments it
  converges at** (#191). `GET /api/runtime/config/diff` gains
  `agents.prompt` and `agents.filler` beside `agents.grants`, all three
  labelled `reload`, and every kind of the domain half carries that
  label now. The three are a breakdown of what changed rather than an
  exception to it: an agent whose prompt alone moved is in `changed` and
  in `prompt` both, which is one change reported at the altitude an
  operator asks about it.

- **A server serving a configuration it was handed rather than read**
  refuses both surfaces that span the two sides (#191, closing part of
  #195). The comparison read and the apply answer a fixed 409 saying
  that no stored configuration describes what the server is running, and
  a device or default-agent write acknowledges that it is stored and
  takes effect when a server boots from that store. That is the test
  lane's and an embedded caller's shape; an ordinary deployment reads
  its configuration from a store and is unaffected.

- **The tests reach for the names a caller reaches for, and the ones
  that still do not say why** (#210). A committed tokenizer walk
  (`tests/tools/reach_ins.py`) counted every place a test read a private
  name through a dot: 440 sites over 85 names across 55 files. 138 of
  them had a public route already and take it, through seams that
  existed for production reasons: the runtime's own `start_reply` and
  `drain`, `SessionInput.audio`, the composition root's providers
  parameter, `SessionEvents.attach`, the providers' injected clients,
  `runtime.prompt.know_how`, and the `tts sentence_start` message a
  device already receives. 10 asserted nothing that survived and are
  gone. The remaining 292 are white-box safety invariants and each now
  states, where it happens, which property public observation cannot
  establish: a race that is only ever probabilistically reproducible, a
  released resource that is invisible because it was released, a token
  older than an issuer will stamp, a stored column a migration reads
  rather than an accessor. The walk reports 162 sites over 75 names
  across 46 files. No production interface was added, and no production
  code changed.

- **Every event this server may emit is declared as a type, and the
  machinery that reconciled a declaration with a call is gone** (#210).
  The last forty-nine emit sites, across the OTA check-in and its
  activation ceremony, the onboarding banner, the handshake gate, the
  ASR echo guard, the MCP lifecycle, memory, filler, capture, the drain,
  the live bindings view and the configuration API, construct a typed
  variant instead of restating a template, an argument order, an event
  name and a field set. With nothing left to reconcile, the seventeen
  fault codes, the two-step judging, the variant matching and the
  recovery rebuild go, and `events_schema.py` with them: its vocabulary
  is the value types' now and its channels the catalog's. What operators
  consume is unchanged, held by a record baseline of all eighty-one emit
  paths captured before the conversion and byte-identical after it, and
  by a golden inventory of every declared event's structure. The
  generated event reference keeps its counts and its content; its
  sections appear where the catalog declares them.

- **The configuration API's entity routes are written out, and the
  entity registry holds only facts** (#210). The twenty-two routes for
  providers, MCP servers, prompt fragments, agents and the agent
  defaults were synthesized from tuples on the registry by a factory
  that set a generated function's name, docstring and signature; each is
  now an ordinary route function carrying its own operation id, summary,
  description, response model and refusals. `docs/reference/api-openapi.json`
  regenerates byte-identically, which is the proof that the two
  spellings describe one API, and the CLI's rendered output and every
  acknowledgement are unchanged.

### Fixed

- **A failing event tap no longer names the exception it raised**
  (#210). A tap is code this server does not own, and `type()` accepts
  any string as a class name, so an exporter holding whatever a far side
  answered it with could put those bytes into a retained log line
  through the report about its own failure. The report names the tap and
  stops there.

### Removed

- **The event surface's four reconciliation sidecars and their suite**
  (#210). The pin map, the token-decision map, the spread inventory and
  the call-alternatives table existed to hold a declaration, a call site
  and a pin in agreement; a variant is its own declaration now, so the
  structures that must agree per event fall from nine to two. The prose
  pins that restated templates, argument tuples and field sets retire
  with them; the no-leak sentinel suites are kept whole and rewritten
  against the typed path.

- **The entity descriptors' eleven behavior hooks and the import-time
  mutation that installed them** (#210). Row mappings and write checks
  are a private typed table inside the repository, which both installed
  and consumed them; the masked body builder, the summary lines and the
  acknowledgement sentences are called directly by the modules that own
  them; and the routes and the CLI's break-glass paths call the
  repository's typed methods by name. `fill()`, its `object.__setattr__`
  and the `Callable[..., object]` alias go with them, so a descriptor is
  whole the moment it is declared and no module has to be imported
  before another for it to be. `notice` stays a descriptor fact, since
  when a write takes effect is data about the kind; `fields_in_help` had
  no consumer and is deleted.

## 2026-08-19

### Changed

- **The session channel's events are declared as types** (#210).
  Twenty-seven emit sites across the device edge, the pipeline,
  turn-taking, the filler runner and the websocket endpoint stop
  restating a template, an argument order, an event name and a field set
  and construct a typed variant instead; their declarations move into
  the catalog and their sidecar entries are deleted. The vocabulary
  grows to cover what a conversation says: a device MAC and a language
  code held to their syntaxes, a bounded client id, prompt provenance by
  size, and the closed sets as enumerations, with a variant narrowing
  one where it admits fewer members and fixing one where the variant IS
  the value. What operators consume is unchanged: the same channels,
  levels, sentences, arguments and fields, held by a record baseline
  captured before the conversion and byte-identical after it, and by the
  golden inventory of every declared event's structure. The generated
  event reference keeps its counts and its content; the converted
  sections move to where the catalog declares them, and the composed
  grammars name the value types that build them now.

- **Events are declared as types, starting with the conversation
  store** (#210). `vinga_server/events.py` becomes a package, and
  beside it a catalog declares one event per code holding a
  discriminated set of typed variants: each variant a frozen dataclass
  owning its channel, its level, its exact payload shape and its
  rendering, written in a vocabulary of value types that refuse at
  construction what the registry used to refuse at emit. An emit site
  hands the emitter a construction thunk and names the variant and its
  values, nothing else, and building, validating, rendering and
  serializing all happen inside the guard, so a telemetry bug still
  costs a log line rather than a reply. The conversation store's five
  paths are converted; the other 76 are unchanged and their
  declarations stay where they are. What operators consume is
  unchanged: the same channels, levels, sentences, arguments and
  fields, held by a committed record baseline that does not move across
  the conversion, and by a committed golden inventory of every
  declared event's structure asserted against the catalog both ways.
  The generated event reference keeps its counts and its content; four
  of its sections move within the document. mypy now runs strict over
  the events package in CI, because annotations nothing checks would be
  decoration.

- **The project is now vinga** (#209). samtal collides with an
  existing Swedish commercial conversational-AI product, so every
  surface renames: the repository (`rafacm/vinga`, old URLs redirect),
  the directories (`vinga-server/`, `vinga-esp32/`), the Python
  distribution and import package (`vinga-server`, `vinga_server`),
  the CLI (`vinga-server`), the container image
  (`ghcr.io/rafacm/vinga-server`; the old `samtal-server` package
  stays as published history), and all `SAMTAL_*` environment
  variables (`VINGA_*`). Historical records keep the old name: this
  changelog's earlier entries, `docs/adr/`, `docs/plans/`, and
  `docs/features/`.

  This is a hard cutover with no aliases. An existing deployment
  updating to a renamed image must, in the same step: rename every
  `SAMTAL_*` variable to `VINGA_*` (values unchanged, including
  `VINGA_MASTER_KEY`, which still unlocks the same encrypted
  secrets), move the data directory from `/var/lib/samtal` to
  `/var/lib/vinga`, and rename `samtal.db` to `vinga.db` inside it
  (`conversations.db` keeps its name). One derived value changes as
  well: the onboarding key's HMAC label is versioned with the project
  name, so the short onboarding path a provisioned board was given is
  no longer served; print the new URL with `vinga-server config
  ota-url` and re-provision the board's `ota_url`.

### Changed

- **samtal-server moved to the src layout** (#208). The import package
  now lives at `samtal-server/src/samtal_server`, so a process started
  from the project directory can no longer import it off the working
  directory: what imports is what was installed, and a file missing
  from the wheel fails at once instead of only inside the container.
  The wheel's contents are unchanged (the same 107 entries as the flat
  layout built). Checkout-relative paths moved with the tree: the
  bytecode-cache clearing in `tests/conftest.py`, the integration
  lane's leftover-cache guard (which now also asserts the package
  directory exists, so a future move cannot make it pass vacuously),
  the build-info checkout root, and the Dockerfile's source copy.

- **CI's image lane skips pull requests for now** (#212). The image
  job (both variants, arm64 under QEMU, the whole smoke conversation)
  was the longest pole in every PR round-trip, and the upcoming
  simplification and rename work arrives as many PRs in quick
  succession. The job now runs on pushes to `main` and on manual
  `workflow_dispatch` runs, so every merged commit still gets the full
  build, smoke, drain check, and publish; a branch that touches the
  image's own plumbing gets a dispatch run before merging. This is a
  churn-period measure with its revert condition written next to the
  gate: when the work settles, the job's `if` is deleted and PRs run
  the full lane again.

### Added

- **Agents can roll an honest die** (#82). A third builtin tool,
  `random_number`, draws one whole number between two bounds, both
  included, from the operating system's entropy (`secrets`). A language
  model cannot do this on its own: asked to roll a die it writes
  whichever digit its distribution favours, writes the same one again
  next time, and sounds certain either way, which is a poor game and a
  worse tie-break. Both bounds are optional and default to a die (1 to
  6), so a model that sends no arguments gets one, and each is held
  between -1000000 and 1000000, since the output of this pipeline is a
  voice and a twenty-digit number is not an answer anybody asked to
  hear. A range that runs backwards, a bound that is not a whole
  number, and a bound outside those limits are refused in words the
  model reads and calls again from, the way any builtin refuses
  arguments it cannot use. Unlike its two siblings the tool is offered
  unconditionally: it is configured by nothing and reaches nothing, so
  there is no fact about a deployment or a board that would make chance
  apply to one agent and not another. `random_number` joins the
  reserved names an `mcp_servers` entry may not take, beside `self`,
  `switch_agent` and `remember`.

### Changed

- **Every refusal the configuration API answers is now an RFC 9457
  problem document** (#192). A refusal used to be `{"detail": "..."}`
  served as `application/json`, which leaves a form with a paragraph to
  quote and nothing to mark. It is now
  `{"title", "status", "detail", "errors"}` served as
  `application/problem+json`: the status's standard reason phrase, the
  status repeated so a body separated from its response still says what
  it was, the same sentence as before, and a list of the fields the
  refusal names. `errors` is always present and empty where a refusal
  names no field of the request, so every refusal has one shape. Each
  entry addresses its field by RFC 6901 JSON Pointer
  (`/filler/phrases`, `/mcp/0`, and the empty string for the fragment
  as a whole), escaped as the RFC says.
  `type` and `instance` are deliberately absent, which means
  `about:blank`, and is the truth: these problems are described by
  their status and their prose rather than by a URI registry nobody
  serves. The sentence itself is unchanged byte for byte, and so is
  what the CLI prints, except on the two refusals the next two entries
  record; the committed OpenAPI document moves with the wire. The framework's own refusals join them: an
  authenticated request to an unmatched path, or to a route with the
  wrong method, used to be answered by Starlette in a body nothing else
  here sent, and is now the same document, keeping the `Allow` header a
  405 needs to be one.
- **A validator says which field it refused** (#192). The three rules
  that know their semantic field (a provider's inline-secret walk, an
  MCP entry's transport rule, an agent's filler block) are model-level,
  so what they knew survived only inside the sentence. They now carry
  it, and the API answers it. One sentence changes with them: the
  transport rule found several problems and joined them into a single
  `; `-separated line, which no reader can decompose back into the
  fields it names, and it now reads one line per problem, the same
  words in the same order.
- **A refusal names only fields this server declares** (#192). Both the
  sentence and the `errors` entries are built from where validation
  failed, and that location can be a key the request invented: an
  unrecognized key on a fragment, an option a provider passes through
  to its implementation, an entry of an MCP server's `env` or
  `headers`. A key is as good a place to paste a credential as a value
  is, and better at hiding there, because a key looks like a name. So a
  refusal renders only names this server declares and positions in a
  list, and stops at the first segment that is neither: an unrecognized
  key is refused as "an unrecognized key is not permitted" pointing at
  the object it was written in, and a rule about a secret-shaped key
  names the word from this server's own closed list that the key
  matched, and where the key was written, rather than the key. The rule
  was already written down for one refusal, a malformed `mcp` grant; it
  now has one home and every renderer reads it from there. Refusals
  about declared fields are unchanged, `api_key_env` among them: a name
  this server chose is a name it may print.
- **A masked read is a write** (#192). The `entity` half of a read was
  meant to be resubmittable, and one case was not: a value the display
  masks is not always a stored secret. A lowercase environment name in
  an `*_env` option and a whitespace-padded `$VAR` in an MCP server's
  `env` are both values a write accepts and the display refuses to show,
  because a bare lowercase name in a credential slot is as likely to be
  a pasted key as a variable, so a read of such an entity came back with
  `********` in it and could not be sent back. Writing the mask now
  means keep the value stored there: the repository substitutes it
  before the fragment is validated, walking the incoming body with the
  same secret-shaped-key rule the display masks by, at the same depth,
  so what a read hides and what a write restores cannot come to
  disagree. The mask written where nothing is stored is refused rather
  than stored, naming the field where the field is one this server
  declares, because the mask is not a value. Nothing else about a write
  changes: a PUT still replaces the model-shaped half and leaves stored
  credentials alone, and rotating one is still the secret PUT. The
  contract says all of this now, in the API description and in the
  envelope's own field descriptions, including which read shapes are
  display-only.

- **A read shows every field its entity's model declares, masked at
  every depth** (#176, #171). The five body builders behind
  `config show` and the whole-configuration document each listed their
  kind's fields by hand, so a field added to a model was invisible on
  every read until somebody remembered to add it there, and no test
  failed for the omission: the descriptor work measured exactly that,
  with a scratch field that reached the store, both APIs, the CLI and
  both generated references untouched and never appeared in a read.
  One builder now derives the body from the entry's own model. The
  display fails open by policy, because a read is thrown away as soon
  as it has been read and an operator debugging with an incomplete
  answer is the worse failure; a record keeps the opposite answer for
  the opposite reason, since a capture manifest and a session row
  outlive the conversation, and `views.provider_record` stays built key
  by key and now says so as a split rather than as a preference. A
  field is shown at whatever it holds, its default included, and is
  left out only when it holds a default that means absence, because a
  read is a fragment a write of it accepts back and an MCP server is
  refused for naming a field of the other transport at all. Nothing
  prints anything new today: the fields the five builders listed are
  exactly the fields the five models declare. The masking is one walk
  now, and it does not stop, which closes the gap beside it: a
  provider's options were masked at every depth while an MCP server's
  env and headers were masked one level down, so a credential nested
  inside one was displayed as written. Which key names hold a
  credential is a fact each kind's descriptor carries, and it is the
  same predicate the models refuse an inline value under, so what a
  write rejects and what a read masks cannot come to disagree.

### Fixed

- **DEBUG logging no longer puts the request line, a device's token or
  a device frame back on the log** (#124). The vendor floor in
  `logs.configure` covered the four libraries that speak to a provider
  and left the two that carry this deployment's own bytes.
  `uvicorn.error` is the HTTP server's trace: at debug it prints the
  request line and every request header, so the OTA path's secret
  segment and a device's bearer token came back on the surface the
  access log is turned off to keep them off, and uvicorn hands that
  same logger to the websockets protocol, so those records also
  rendered every device frame's payload with the text decoded.
  `sqlalchemy` is the same hole one level louder, since an engine whose
  logger is enabled for INFO echoes every statement with the parameters
  bound to it; its floor is WARNING for that reason. An operator who
  genuinely wants a wire trace still raises the logger by name, which
  no configuration key does for them. The floor goes on where each way
  of starting the server begins rather than in `logs.configure` alone,
  which one entry point never reaches and the other reaches only after
  the boot has read its configuration out of a database.

- **A read or a delete of something that is not there names its
  section, not what was addressed** (#132). Four entity kinds and the
  devices map answered with the identity that had been asked for
  (`providers.llm.<name>: no such provider` and its siblings), and that
  identity is a value nothing in this deployment has validated: it
  arrived in a URL path or on a command line, and the sentence built
  from it travels out as a 404 body, a printed line and a log record.
  Every section now answers one fixed sentence, including the ones
  whose identity has a rigid shape, and so do the two secret-slot
  refusals. The sentences an operator reads change: `providers: no
  provider of that name exists for that stage`, `mcp_servers: no MCP
  server of that name exists`, `agents: no agent of that name exists`,
  `devices: no device with that MAC is bound`, and `<section>: no
  secret is stored for that slot`. The two other segments a caller
  sends are the same story and changed with them: a stage that is not
  one of the four is refused with `providers: the stage has to be one
  of asr, llm, tts, vad`, and a credential slot that is not one is
  refused with the rule for that kind, since `set-secret` is the
  command a credential gets pasted into and the slot is where one
  typed an argument early lands.

- **A failed barge-in confirmation names the class it failed with, not
  what the provider said** (#183). The gate ladder's catch was a
  `logger.exception`, so an ASR failure put the provider's message, the
  chain behind it and a traceback of both onto the retained log, which
  the content-and-telemetry ADR's no-leak contract forbids. It now logs
  one fixed sentence and the exception's class name. Nothing is lost:
  the confirmation runs inside the runtime's provider watch, so a
  failure on the wire is already reported as `provider_failed` with the
  stage, the entry and the host. The resume-and-drop behavior is
  unchanged.

- **A filler clip that fails to encode is told apart from a device that
  left** (#182). The playback arm caught `(DeviceGone, RuntimeError)`,
  and `DeviceGone` subclasses `RuntimeError`, so it caught the base
  class and swallowed every local bug in the block in silence as though
  the device had disconnected. Only the translated type is a disconnect
  now; a resample, encode, flush or send failure is logged by class
  name and the mask stands down exactly as before, with the reply it
  masks unharmed. The catch-all also stopped rendering exception text.
  One behavior change is deliberate: a bare `RuntimeError` from the
  send is now logged rather than swallowed.

- **A device's abort reason is reported from a closed set** (#185).
  `device aborted (...)` interpolated the abort message's `reason`, a
  free string the far side writes, straight into a retained line. The
  firmware's own enum has one spelling, so the line now names
  `wake_word_detected`, says `none` when the abort carried no reason at
  all, and reports anything else as `other` without repeating it. An
  abort with no reason renders `none` where it used to render
  `no reason`.

- **A malformed control message is refused without being quoted**
  (#185). The refusal wrapped pydantic's validation rendering, which
  carries `input_value=`, and the session edge logs it verbatim, so an
  abort whose `reason` was the wrong shape put the device's own bytes
  on the retained log through a path the closed reason set never sees.
  The refusal now names the message type, the field, and the rule it
  broke, all of them this server's own vocabulary. A malformed hello
  still closes the connection and any other malformed message is still
  ignored, both unchanged.

- **The integration lane stops leaving bytecode caches behind** (#199).
  The two suites that run the deployment seeding scripts verbatim start
  `samtal-server` and its CLI as subprocesses, and nothing put
  `PYTHONDONTWRITEBYTECODE` in their environment, so each run left about
  fifteen `__pycache__` directories under `samtal_server/`. Those are
  exactly the caches the repository's stale-bytecode safeguard exists to
  keep off the tree: `tests/conftest.py` clears what it finds once,
  before the first import, so a cache written by a subprocess later in
  the run outlives it and goes stale on the next edit. The harness now
  builds every such environment through one helper that sets the flag,
  and the scripts stay deployment artifacts with nothing test-shaped in
  them. A session-scoped check at the end of the lane fails, listing the
  paths, if a cache is there anyway.

### Added

- **The design method the architecture review used is written down**
  (#145). `docs/architecture/design-guide.md` defines the vocabulary
  the 2026-08-14 review judged plans and diffs by (module, interface,
  depth, seam, adapter, locality, the composition root) against this
  codebase rather than in the abstract, states the deletion test and
  the rule that a test reaches the interface, and works through four
  merged changes: the prompt assembler as deepening in place (#122),
  the CLI's response frozensets as one shape encoded twice (#139),
  `ReplyControl` and `TurnView` as seams a part can state about its
  parent (#141), and `ToolSource` as one question three sources
  answer (#140). A 29-line section of `AGENTS.md` carries the short
  form into every session, since a criterion agents do not read
  shapes only the code that was already written. The two process
  skills carry it too: a plan names the modules each milestone
  deepens and the seams it adds, and the external plan review applies
  the deletion test to every new module a plan proposes.

## 2026-08-18

### Changed

- **The OTA endpoint is a package too, and the import cycle between it
  and onboarding is gone** (#143, M2). `ota.py` held the check-in reply
  and the activation poll in one file and reached the onboarding module
  through three function-body imports, each with a comment apologizing
  for itself, because that module imported this one to serve its
  handlers. The handlers now have a file each and the router that
  mounts them at the short onboarding alias lives beside them, so
  nothing in the onboarding package imports the OTA endpoint and
  nothing in either package defers an import to a function body. Three
  things follow that a reader will notice: one function answers what a
  device with no agent gets, one helper assembles every address this
  server names itself by (verbatim for the wire, rebuilt for anything
  retained), and `samtal-server config` and the configuration API's
  document render no longer load a conversation's machinery to read the
  pending table or the origin, which a test now holds them to. Nothing
  a device or an operator sees changed: every route, both of their
  spellings, the activation ceremony, the 404, the banner and every
  event's channel, sentence and fields are byte for byte what they
  were, checked by driving both before and after and diffing the
  responses, with the whole onboarding and OTA test surface passing
  unmodified.

- **Device onboarding is a package with a file per responsibility**
  (#143, M1). `onboarding.py` held two halves its own docstring
  declared: the short `/x/<key>/` path with its key derivation and its
  hint-free 404, and the activation ceremony with the table of devices
  waiting to be claimed. They are now a file each (the key and its
  guard, the origin and the startup banner, the pending table, and the
  unbound-device decision) behind a facade that answers to the name
  every caller already imports. Nothing an operator sees changed: the
  routes, both of their spellings, the 404, the activation ceremony,
  the banner and every event's channel, sentence and fields are what
  they were, and the whole onboarding and OTA test surface passes
  unmodified. The one home of "what does a device with no agent get"
  now exists beside the table it asks; the OTA endpoint starts calling
  it in the next step, which is what ends the import cycle between the
  two modules.

- **A shutdown waits for the drain it started** (#142, M4). The task
  that lets conversations finish their sentence was created and
  forgotten: nothing held a reference to it, so the event loop was free
  to collect it mid-drain, and anything it raised was reported by the
  garbage collector instead of by the shutdown that started it. The
  server now owns that task and does not finish serving until the task
  has finished, or until its bound expires (the drain budget plus the
  margin the registry holds back for the closes), in which case the
  drain is abandoned with a warning rather than allowed to hold the
  process up, whatever it makes of being cancelled. A drain that failed
  is reported in one line naming the class of what went wrong and not
  what it said, since a client failing on its way out can quote the
  endpoint or the credential it was given. What an operator sees on a
  redeploy is otherwise unchanged: the first signal drains, a second one
  still forces the exit on the spot,
  and a signal arriving before there is anything to drain still passes
  straight through.

- **The configuration API is served over one database engine, not one
  per request** (#142, M3). Every call to `/api` opened the
  configuration database, ran an Alembic up-to-dateness check inside
  it, re-parsed `SAMTAL_MASTER_KEY` and threw the engine away. The
  engine is now opened once by whichever lifespan owns the application
  (the server's when the API is mounted, the API's own when it is run
  standalone, never both, since a mounted application gets no lifespan
  of its own) and disposed when that lifespan ends, with the encryption
  keys derived once beside it. The open still migrates, so a first
  deployment whose first act is an API write still gets a schema, and a
  server that migrated at boot pays one no-op check. Nothing an
  operator sees changed: a write still takes the lock per transaction,
  contention is still met inside the request and still answered 409
  with the repository's own sentence, and a database another writer is
  holding at startup is refused as the boot failure it is, with the one
  sanitized sentence a refused boot prints. The conversation store's
  reads keep opening per request, deliberately: a store restored or
  purged under a running server is met as it is now, which is that
  store's own property.

- **A server acquires what it is made of when it starts serving, and
  lets go of it when it stops** (#142, M2). `create_app` built
  everything: an application that was built and never served held a
  connection pool on the configuration database, a migrated
  conversation store and every provider model its agents named. It now
  describes the application (its routes, its mounted configuration API
  with the gate armed, and the refusals a deployment missing a secret
  must meet whatever launched it) and builds nothing; the lifespan
  builds, in the order the old function documented, registering every
  acquisition for release as it makes it, so a startup that fails part
  way through leaves nothing open. The provider build, which loads
  models for seconds to minutes, runs on a worker thread instead of the
  event loop. A boot failure is still one sanitized sentence on stderr
  and an exit code of 1: the lifespan records it and raises
  `StartupFailed` with nothing chained to it, and the entry point
  prints it. The captive-portal banner is now said after a successful
  startup rather than before one, so a server that fails to start
  announces nothing, and a signal arriving before the composition
  exists goes straight to uvicorn's shutdown rather than to a drain
  with nothing to drain. Nothing on the wire changed, and all four
  generated references regenerate identical.

- **What a running server is made of is one typed object** (#142, M1).
  The composition root hung thirteen untyped attributes on `app.state`,
  and the mounted configuration API kept a seven-attribute bag of its
  own beside them, so a handler reading any one of them could learn
  what was there and what type it had only by reading the function that
  set it. `samtal_server/composition.py` declares it instead: the
  `Composition` a server is (the configuration, the device auth, the
  bindings, the pending table, the MCP managers, the memory store, the
  session registry, the providers, the filler cache, the conversation
  store, the runtime factory, the device facts, the capture store, and
  the API's own `ApiRuntime`). `app.state` carries that one attribute
  and the sub-application carries `api_runtime`; the websocket
  endpoint, the OTA endpoint, the shutdown drain and the API's
  dependencies bind one of the two and read declared fields. The filler
  cache is an `AgentFillers` rather than a bare dictionary, which
  answers exactly as the empty dictionary did before boot fills it and
  exactly as the filled one after, and can now say which of the two it
  is doing. Nothing an operator sees changed: construction still
  happens where it happened, the API token is still resolved into a
  local and stored nowhere, and all four generated references
  regenerate identical.

- **The latency mask is its own module** (#141, M2). The second of the
  two clusters the conversation runtime was carrying: the filler timer
  armed at the transcription, the fire-time checks that stand it down
  when the user is still speaking or a barge-in is being confirmed, the
  cached clip that goes out when neither does, and the arbitration that
  makes the reply's first real sentence queue behind a clip's tail
  rather than cut it mid-word. `runtime/filler_runner.py` holds it as
  `FillerRunner`, which asks whoever holds the floor two questions
  through a `TurnView` interface and tells it nothing, so the one piece
  of state the two clusters share crosses as a read-only property.
  Nothing an operator sees changed: `filler_played` and
  `filler_skipped` keep their messages, their fields and their firing
  conditions, the clip is still encoded whole before the first byte
  goes out, and the generated events reference regenerates identical.
  What did change is that the mask can be driven onto each of its
  outcomes without a session, a socket or a synthesis behind it, and
  that the runtime is 1,484 lines against the 1,820 it started at, with
  the responsibilities it kept named in its own class docstring.

- **Who holds the floor is its own module** (#141, M1). The
  conversation runtime kept the turn-taking core alongside everything
  else it does: the utterance buffer and the tail cap that bounds it,
  the endpointer feed, the pre-roll trim, and the gate ladder that
  decides whether speech arriving mid-reply may cancel that reply.
  `runtime/turntaking.py` holds that cluster as `TurnTaking`, reaching
  the rest of the runtime through one narrow `ReplyControl` interface,
  and the runtime's device-facing methods are one-line delegations to
  it. Nothing an operator sees changed: every decision event keeps its
  message, its fields and its firing conditions, both halves of the
  log still ride the one session channel, and the generated events
  reference regenerates byte-identical. What did change is that the
  gate ladder can now be driven onto each of its five decisions
  without building a session around it, and that the tail cap and the
  trim arithmetic have tests of their own for the first time.

- **The MCP subsystem answers for itself** (#140, M4, which closes the
  issue). The last of the four milestones is about what the tests are
  allowed to know: a registry says which manager an entry has and
  whether a reload is between its two phases, a manager says which
  session it is calling over, the tasks a stop gave up waiting for
  have a name rather than a hiding place, and `McpManager` states what
  a registry needs of one server, so a suite can stand something in
  for one instead of inheriting from the real thing and reaching
  inside it. Four tests that used to rewrite a running server's
  configuration to make it look like a box coming back now let the box
  come back. Nothing an operator sees changed, which is the point of
  the milestone as it was of the three before it.

  With it #140 is done. A 2,338-line file is a package of six modules
  with a stated responsibility each, under the module name it always
  had, so every retained record still reads the same (M1); the status
  and reload surface the API sends is built where the knowledge is,
  with the document byte-identical (M2); the three places a tool can
  come from answer one interface, and the runtime loops over them
  instead of knowing each by heart (M3).

- **One interface under the three tool sources** (#140, M3). The
  conversation runtime knew each of the three places a tool comes from
  by heart: the builtins it built itself, the device's list it scanned
  twice, and the MCP registry it asked by name, with four calling
  conventions between them and a timeout that forked on where the call
  had come from. `samtal_server/tools/source.py` states what they have
  in common as `ToolSource`, and the runtime's snapshot, dispatch and
  timeout are one loop over the three sources it is built with.
  Every question a source is asked is asked about the same claim, the
  classification reserved on the turn's record before anything ran, so
  MCP routing is never in a position to resolve a name a second time
  (the device source deliberately keeps its live-list ownership scan,
  the recorded edge behavior) and a
  reload cannot reroute a call in flight. What no source can answer
  stays with the runtime: arguments a model never closed, a name nobody
  publishes, and the handover, which ends the tool loop rather than
  answering the model. Nothing an operator sees moved: the same tools
  are offered in the same order, the same sentences come back, and the
  `tool_call` event says what it said.

## 2026-08-17

### Added

- **One descriptor per domain entity** (#139, M1).
  `samtal_server/config/entities.py` declares what the pydantic models
  cannot carry, in three tiers: the five kinds written with a command
  of their own, the two shapes that exist only nested inside one of
  those, and the two domain-level fields written with their own verbs.
  Each entry holds its documentation prose, how the kind is addressed
  (the API path prefix and the parameters under it, which are also the
  CLI's positional arguments), whether it has a delete, whether stored
  secrets hang on it, and which moved configuration key its command is
  quoted for. The generated reference now renders those descriptors
  rather than keeping a second copy of them: docgen's own entity
  dataclass and its two data tables are gone. Nothing else reads the
  registry yet, and nothing changed for a reader or an operator, which
  is the point of doing it this way: both committed references,
  `docs/reference/domain-config.md` and
  `docs/reference/api-openapi.json`, regenerate byte for byte, so the
  move is provable rather than asserted.
- **The event schema reference cannot drift** (#155, M3).
  `samtal-server events reference` prints the whole registry as a
  document: one section per event, one subsection per shape it may be
  emitted in, with the channel it rides, its level, the byte-exact
  sentence it renders, the kind of every argument position and a field
  table naming every field's kind, requiredness, nullability, token
  set, syntax or bounds, plus the taxonomies, syntaxes and grammars
  those tables point at. It is committed at `docs/reference/events.md`,
  and CI regenerates it and diffs it byte for byte, the fourth such
  step beside the domain configuration, the conversation store and the
  OpenAPI document. The command opens nothing and dispatches before the
  entrypoint resolves `SAMTAL_EVENTS_ENFORCEMENT`, so an unusable value
  of a server-only variable cannot stand between a reader and the
  document.
  With this, #155 is done. One registry declares every event, the
  emitters hold every emission to it, and the documentation is that
  registry rendered rather than a second copy of it: a new field
  carrying far-side bytes is now a schema violation a test lane
  refuses, not a review finding somebody has to notice. The far-side
  values the surface deliberately keeps are the declared `DESCRIPTOR`
  fields and only those, which the amended content-and-telemetry ADR
  admits as bounded device-descriptor metadata; they are named as such
  in the reference, held to their bounds at emit, and an undeclared one
  beside them is refused like any other.
- **The emitters enforce the event schema at emit time** (#155, M2).
  Both `_emit` paths now hold an emission to its declaration in two
  ordered steps: what the caller passed is judged before the base
  fields are merged, so a `**fields` spread carrying `session=` is
  refused as the identity spoofing it is rather than merged over the
  emitter's own; then the finished payload is matched whole against the
  event's declared variants, the emitting channel, the level, the
  sentence compared byte for byte against the registry's template, the
  argument tuple against its per-position kinds, and the fields against
  the variant's table. `SAMTAL_EVENTS_ENFORCEMENT` picks the mode.
  `strict` raises `EventSchemaError`, which is what every context that
  is not a running server gets by default: the test lanes, an import, a
  REPL. `forgiving` recovers, which is what a running server gets:
  the emission is rebuilt against its variant where that produces a
  declared shape, and becomes a `schema_violation` event carrying
  nothing but the emitter's own identity where it does not, with one
  ERROR line on the emitter's channel naming what was refused. A
  telemetry bug can therefore no longer cost a reply, and the whole
  path runs under one guard so that a bug in the enforcement itself
  cannot either. Diagnostics render registry-owned identifiers only: a
  declared event or field name, a fixed violation code, a count, an
  argument position, never a rejected name or a value.
- `SAMTAL_EVENTS_ENFORCEMENT` (`strict` or `forgiving`), resolved by
  `create_app` and by the entrypoint after it has loaded `.env`. Unset
  means `forgiving` at both, because a running server is a deployment
  whatever artifact it runs from; anything else refuses to start,
  naming the variable and the two values it takes. The container image
  sets `forgiving` explicitly, redundant with that default on purpose,
  so the production posture is visible in the artifact. Documented in
  the README's Logging section.
- **Every event's schema is declared as data** (#155, M1):
  `samtal_server/events_schema.py` holds one declaration per event,
  naming its channel, its level, the exact sentence template it
  renders, the kind of every argument position, and its field set with
  per-field kinds, closed token sets, syntaxes and bounds. 58 events in
  99 variants: the 57 with ordinary emit sites, plus the internal
  recovery event the forgiving mode will emit. Declarations only in
  this change; the emitters do not read the registry yet, so no
  behaviour depends on it, and the release is behaviour-identical
  except where the four sanitization fixes below bite. A conformance
  test walks all 81 emit sites and holds the declarations to them both
  ways, so a field the registry declares that nothing emits fails as
  loudly as one the code emits and the registry does not know.
- The five conversation-store event paths gained exact pins
  (`tests/unit/test_conversations_event_pins.py`), in the two contract
  pin suites' style. They postdate those suites' baseline and had none.

### Fixed

- **Four decision sites bound the far-side strings their events
  retain** (#155, M1). Each takes an event-only copy: printable
  characters only, trimmed, and cut to a declared limit, with what the
  site answers elsewhere untouched. Visible to adversarial input only;
  every value a real device sends passes through unaltered.
  - `ota_check`'s `board` and `firmware` are bounded in the payload and
    in the four sentences that render them. The OTA reply still echoes
    the reported version verbatim, which is how the firmware decides
    whether it is up to date, and the recorded device facts a capture
    manifest is built from are unchanged.
  - `ota_check`'s `client` is bounded, and null where nothing printable
    survives. The device token is still signed for the Client-Id header
    exactly as it arrived.
  - `session_open`'s `client` is bounded in its field and in its
    sentence. The capture manifest and the conversation store still
    hold the header itself.
  - The websocket capacity refusal names the normalized MAC or no
    device at all. With device authentication off nothing had verified
    that header, so a full server could be made to write one
    caller-chosen string per attempt into the retained log.

### Changed

- **The MCP runtime routes know what they were handed** (#140, M2).
  The status read and the reload action declared their two
  dependencies as `Any`, because the registry that answers them imports
  the MCP SDK and the provider layer and rendering the committed
  OpenAPI document must load neither. They now declare
  `McpStatusSource` and `McpReloader`, a protocol and a callable shape
  stated in `config/responses.py` out of typing and the response models
  alone, so a route says what it asks of a running server and the
  document still renders without any of that being importable. The
  answers are composed where the knowledge is: the registry validates
  its own status into the models the contract declares, and the reload
  returns the endpoint's whole reply, its outcomes and the status taken
  with no await between them, which used to be an invariant a request
  handler was holding by hand. Nothing changed on the wire, which the
  byte-identical OpenAPI document proves.
- **Content a voice assistant cannot speak is named once** (#140, M2).
  This server speaks MCP twice, to the device over JSON-RPC and to
  configured servers over the SDK, and each side had its own copy of
  the sentence a model reads out when a tool answers with an image or
  audio. The sentence and the join are one function in
  `protocol/mcp.py` now, over a normalized sequence of a content type
  and the text it carries; both sides keep the decoding that is theirs,
  including the device channel's tolerance of a result of any shape.
- **The MCP subsystem is six files under one name** (#140, M1).
  `samtal_server/tools/mcp.py` was 2,338 lines and at least twelve
  responsibilities, of which one class was 885 lines. It is now the
  package `samtal_server/tools/mcp/`, whose `__init__` is still the
  module `samtal_server.tools.mcp`: it builds the one emitter, so the
  channel these events ride is unchanged by construction, and it
  re-exports what the six submodules define. `transport` brings a
  connection up and classifies what went wrong, `prompts` captures what
  a server ships about itself under its bounds, `manager` runs one
  server's lifecycle, `slice` holds the configuration a registry was
  built from, `reload` applies a newly read one, and `registry` answers
  what needs the managers and the slice together. Straight moves rather
  than rewrites: every sentence, token, grant rule, refusal and capture
  bound behaves as it did, and nothing an operator reads or a collector
  groups by has moved, which the untouched event pin suites and a
  byte-identical events reference prove. Two rules hold across the
  split and are stated in the package docstring: events go through the
  package emitter, and ordinary prose records go through the package
  logger, so what an operator watches is still one logger's worth of
  lines. The conformance suite's channel-ownership rule gained the
  package form to match, with planted cases for the shapes it accepts
  and the three it refuses.
- **Importing the MCP layer no longer rearranges logging** (#140, M1).
  Importing `tools/mcp` used to install a filter on four of the MCP
  SDK's loggers and turn propagation off at the root of its namespace,
  as a side effect of the import, so a tool that imported it to read a
  type paid for it too. The same filters and the same propagation are
  now `transport.quiet_sdk_loggers()`, called where a manager creates
  the task one connection lives in. That is the one place a connect is
  ever begun, and it is the boundary for a reason: a start is not the
  only way in, since a session opening revives a down server through a
  background reconnect that comes straight there. What a third-party
  server's own client writes still reaches no handler of ours, on
  either path.
- **The domain configuration's schema is single-sourced** (#139, M5).
  With this, #139 is done. A domain entity was spelled at 14 to 19
  places across 12 to 14 files, and adding the most recent kind
  hand-edited 13 of them; it is now its pydantic model plus one entry in
  `samtal_server/config/entities.py`, which the documentation renderer,
  the repository, the read views, the API's route factory and the CLI's
  dispatch table all read. M1 built the registry and pointed docgen at
  it, M2 gave the repository one read, one write and one delete over all
  five kinds, M3 built the twenty-two entity routes from the endpoint
  facts their descriptors carry, M4 made a command one row that both
  paths run and deleted the hand-kept shapes the CLI used to check an
  answer against, and M5 splits the 2,305-line acceptance file along the
  boundaries the other four produced. Nothing an operator or a client
  reads changed at any of the five: every refusal, notice, column and
  document is byte for byte what it was, and both committed references
  regenerate unchanged with no regeneration commit anywhere on the
  branch. What a new field costs was measured rather than asserted: a
  scratch field added to one entity's model, its column and its
  migration reached the store, the API, the CLI and both generated
  references with no descriptor edit and no other hand edit, the whole
  unit lane green but for the two drift pins a regeneration command
  answers. The one surface it does not reach on its own is the read
  view, whose per-kind body builders are written key by key, which is
  deliberate for `provider_record` and incidental for the other four;
  filed rather than changed here, since changing it would change what a
  read prints.
- **The config CLI's acceptance suite is six files** (#139, M5).
  `tests/unit/test_config_cli.py` was 2,305 lines and 101 tests because
  the module it tested was one module. It keeps the acceptance spine,
  the empty-database-to-working-configuration walk and the per-kind
  write, show, list and delete behavior that #139 made
  descriptor-driven; the rest moved, one file per concern, to
  `test_config_cli_transport.py`, `test_config_cli_rendering.py`,
  `test_config_cli_secrets.py`, `test_config_cli_grammar.py` and the
  `test_config_cli_local.py` M4 started. The scaffolding six suites
  share (the runner that drives a command against a server of the test's
  own, the sentinels, the fragment constants) is in
  `tests/support/config_cli.py`, per #144's rule that no test module
  imports another. A pure move: every one of the 101 test functions is
  byte for byte what it was, and the lane collects exactly what it
  collected before.
- **The CLI dispatches from one table and renders from the API's own
  shapes** (#139, M4). A command was two implementations of one act:
  fourteen `if args.local:` branches chose between reaching the API and
  opening the database, each with its own copy of the sentence the act
  answers with and the notice saying when it applies. An act is one row
  now, carrying the verb, the path this command's arguments address,
  the body it sends, the shape it is answered with, how that is
  printed, and, for the four commands `--local` covers, the same act
  against the database; one dispatcher reads a row, and the
  acknowledgement and the notice are printed in one place whichever
  path produced them. The five commanded kinds' rows are built from
  their descriptors rather than written per kind, the summary tree asks
  a kind how one of its entries reads, and `--local`, its preamble and
  its four-command subset are exactly what they were. What the CLI
  accepts as an answer is the shape `responses.py` declares the route
  answers with: the four frozensets naming a body's fields and a
  state's vocabulary, and the ten predicate functions that walked a
  body by hand, are gone, and with them a whole class of drift where a
  field renamed on the model left the CLI refusing every well-formed
  answer. The fifth of those constants, `RELOAD_OUTCOMES`, survives in
  derived form: it is the order the reload prints its four lines in,
  read off the result model's own fields rather than listed again.
  Validation is strict, so nothing is coerced into a rendering, and
  unknown fields are dropped rather than refused, so a newer server
  stays readable; the rejected body reaches neither the output nor an
  exception chain.
  Nothing an operator reads changed: every refusal, notice, column and
  document is byte for byte what it was, and each of the sixteen acts
  with a break-glass path is now pinned to print the same thing on both
  of them.
- **The API's entity routes are built from the descriptors** (#139,
  M3). The nine reads and thirteen writes of the five commanded kinds
  were hand-written handlers saying the same nine things in five
  vocabularies; they are one factory now, walking the registry. What
  the committed OpenAPI document carries about each route is written
  down on the kind's descriptor, because those bytes are contract: the
  operation's name, its description, the response model and the
  refusals it declares. What the verb settles the same way for every
  kind is not written down at all: the method, the path under the
  kind's route, the parameters, the request body and which repository
  call is made. The pydantic response and request models move to
  `samtal_server/config/responses.py`, which imports pydantic and
  nothing else, so that the CLI can render an answer against the shape
  the API declared without importing FastAPI to do it; `api.py` imports
  them back and re-exports them under the names they had. The routes
  the tiers do not describe (the whole-configuration read, the devices
  and the pending claim, the default agent, the runtime namespace) stay
  written by hand, because a setting is not an entity. Nothing changed
  for a caller: `docs/reference/api-openapi.json` regenerates byte for
  byte, every operation id, summary, description, parameter and status
  in it included.
- **The repository and the read views work from the descriptors**
  (#139, M2). `store.py` no longer names its five entity kinds at
  fourteen methods and a whole-configuration read: the table a kind is
  rowed in, how one of its rows maps to its model and back, what to
  refuse when an entry is not there, and which checks the write runs
  are facts on the kind's descriptor, and there is one read, one write
  and one delete over all of them. The default row mapping is the model
  itself, so a kind pays a hook only where its model asks for something
  a dump cannot say: the MCP server's per-column omissions, the layer's
  None-inherits tri-state, and the provider's split between declared
  fields and options extras, all three moved rather than rewritten.
  `views.py` reads which builder shows a kind from the same registry,
  and `views.provider_record` stays deliberately hand-built. The two
  walkers that refused a value JSON cannot carry merge into one, since
  the second was the first's float branch. Nothing changed for a reader
  or an operator: every refusal sentence is byte for byte what it was,
  both committed references regenerate unchanged, and the acceptance
  suites are untouched.
- **The README's event table is a name-and-when index** (#155, M3). Its
  fields column is gone: prose field and token claims nothing parses go
  stale while a name-level check stays green, and half-checked
  documentation reads as checked. The index now names all 58 events,
  the 24 it had never listed included, each with a sentence saying when
  it fires, and every field, token, kind and bound lives in the
  generated reference it points at. A test holds the index to the
  registry at name level: every declared event in exactly one row,
  every row a declared event, no duplicates. The lead sentence's
  base-field claim is scoped to the session channel, where it is true.
  The conversation store's reference points at the event reference for
  the same reason.
- **Breaking: the echo guard's recovered sentence no longer says what
  was recovered** (#165): `asr_prompt_echo`'s one INFO outcome, the
  retry that heard a real utterance where the guard was about to
  discard the clip, stopped rendering the transcript into the retained
  log. The sentence now names the duration alone, which with the
  event's unchanged `outcome`, `duration_s`, `retry_ms` and `host`
  fields is the whole of what the branch has to report; the template
  drops from two arguments to one, and its level, its channel and its
  field set are untouched. This was the last conversation text on the
  event surface, which the content-and-telemetry ADR bans without
  exception, and it was there however innocently the transcript was
  recovered: a user reading a credential aloud is a turn like any
  other. A sentinel test plants a credential-shaped transcript and
  asserts it stays in the transcription result the provider answers
  with, which is what the session goes on to hear, and reaches neither
  the log records nor an attached event consumer. **Migration:** what
  was said comes from the conversation store instead, keyed by the same
  session id and subject to `server.conversations.enabled` with
  `text: true`; an operator who greps the logs for recovered utterances
  queries the store's `turns` rows.
- The content-and-telemetry ADR gained a dated amendment: bounded
  device-descriptor metadata (the board, firmware, and client id a
  device reports at check-in, sanitized at their decision sites) is
  metadata the events may carry; conversation-derived text remains
  banned without exception, and the one standing violation (the
  `asr_prompt_echo` recovered-transcript sentence) was removed by its
  own narrowing above (#165), ahead of the #155 schema registry.

## 2026-08-16

### Added

- **The conversation store is real: `server.conversations` records what
  was said** (#120): the machinery and the content record landed
  dormant; this is the release in which an operator can switch them on,
  and in which switching them on does everything the documentation
  says. The section is optional and off by default, with `enabled`, two
  independent storage switches (`metrics` for the events and every
  measured number, `text` for conversation text and tool names,
  arguments and results) and `retention_days`, which defaults to 90 and
  takes `0` as the deliberate keep-forever. Enabled, the server opens
  and migrates `conversations.db` beside `samtal.db` in
  `server.database.dir` at boot, warns that it is recording and names
  the file, and writes one session row per conversation, one turn row
  per utterance-and-reply with its text and its ASR, LLM and TTS
  numbers, one row per tool call under it, and one row per structured
  event, which is the same decision track a capture writes beside its
  audio. Nothing of it is on the conversation's path: a background
  thread does every database call behind a queue no producer waits on,
  commits at turn boundaries and at session close, and drops events
  with one warning per session when the database is wedged rather than
  ever delaying a reply. Retention prunes whole sessions older than the
  window at startup and at each close. The server README now carries
  the store's own section: what is kept, what each switch takes away in
  all four combinations, the shared-device limit stated out loud
  (deployment-wide switches are the only privacy control this release
  has), purge and its physical-deletion semantics, and the WAL-safe way
  to read a live file. **Operator-visible:** a server with no
  `conversations` section behaves exactly as before and creates no
  file; one that has recorded before has its file migrated at every
  start even with recording off, so history stays readable. Four new
  events on the store's own channel: `conversations_enabled` (a warning
  at startup, like capture's, because the server is keeping what is
  said to it), `conversations_dropped`, `conversations_failed` and
  `conversations_pruned`.
- **The conversation record is readable over the API** (#120): three
  gated reads under `/api/conversations`, in the committed OpenAPI
  document with the rest of the contract. `GET /api/conversations`
  lists the sessions newest first, filtered by `?device=` when given;
  `GET /api/conversations/{session}` is one session whole, with how
  many turns and events hang off it; `GET
  /api/conversations/{session}/turns` is one session's timeline oldest
  first, each turn carrying its numbers and the tool calls it made
  nested in the order the model issued them. The list and the timeline
  page on the store's monotonic row ids, which are never reused, and the
  detail read is singular and takes neither argument: `?limit=` holds 50
  rows by default and 200 at most, `?cursor=` means the sessions before
  it in the listing and the turns after it in a timeline, and a page
  answers `{"items": [...], "next_cursor": <id or null>}`. Each read
  opens the file for the length of one request, takes no lock and
  creates nothing. **Operator-visible:** a deployment that never
  recorded answers 404 naming `server.conversations.enabled`, and one
  that recorded and has since switched recording off still serves what
  it recorded, because switching recording off stops the writer and not
  the reader. Content columns come back as they were stored, which is
  null where text storage was off, and every session says which way its
  switches were set. The events themselves are deliberately not served:
  the database is that surface. Deletion over HTTP is not here either;
  `samtal-server conversations purge` is what deletes, and it needs no
  server.

### Changed

- **The API namespace redirects nothing, so no response header quotes a
  request back** (#120): the router's default answered a path with a
  stray trailing slash (`/api/config/`, `/api/agents/<name>/`,
  `/api/conversations/?limit=<value>`) with a 307 whose `Location`
  carried the request's own path segments and query string. That is a
  value quoted back in a header, where a proxy and a browser both keep
  it, and it is the one place this API still did so. Trailing-slash
  redirects are off for the whole gated namespace now.
  **Operator-visible:** a request to a route path with a trailing slash
  that used to be redirected is answered 404 (401 without a token, as
  ever); address the route as the committed OpenAPI document spells it.
  `/api` and `/api/` both still resolve, which was never a redirect.
- **A provider's address may not carry a credential, and no record
  keeps one that does** (#120): `base_url:
  https://user:password@host/v1` names nothing secret-shaped, so every
  rule this project had about inline secrets passed it, and it was
  stored as written, shown on every read, and copied verbatim into the
  manifest of every session held against that provider. Two halves fix
  it. Writing such a URL is refused where a provider is written (both
  write paths, since the rule is the repository's), for a user and
  password before the host and for a credential-shaped query parameter
  alike, with a refusal that names the option and the rule and never the
  value; the rule is write-time only, exactly like the addressability
  rule, so a deployment that already has such a row still boots, still
  reads it and can still edit it out. And a record is built through an
  explicit representation instead of a model dump, masking secret-shaped
  keys at every depth and taking the credential out of any URL-shaped
  value, so a row written before the rule cannot leak either.
  **Operator-visible, and a deliberate narrowing of an existing
  surface:** the same builder feeds the **capture manifest**, so a
  capture taken from now on records such an address without its
  credential where it used to record it whole. The entry name, the type
  and the exact model string are untouched, which is what a manifest is
  kept for.
- **Breaking: the conversation events carry no conversation text**
  (#120): `heard`, `replied` and `agent_said` lose their `text` field,
  and their sentences stop rendering it. `heard` says how long the user
  spoke and, where the engine detected it, in what language; `replied`
  and `agent_said` say which agent spoke and how many sentences of the
  reply the user actually heard, which is a number no event carried
  before and which reports what went out rather than what was
  generated. The retained logs are now metadata only, which is what
  makes the no-leak contract on them a property of the schema instead
  of a review finding, and it is the supersession the 2026-08-04
  observability ADR anticipated: its follow-up note records that it has
  happened. **Migration:** transcripts come from the conversation store
  instead, keyed by the same session id
  (`select heard, reply from turns where session = ?`, with
  `server.conversations.enabled` and `text: true`); every duration,
  count and identifier a latency brief reads is unchanged.
- **Breaking: `llm_round`'s token counts are named as the GenAI
  conventions name them** (#120): `prompt_tokens` becomes
  `input_tokens` and `completion_tokens` becomes `output_tokens`, which
  is the vocabulary the observability ADR adopts where one exists and
  the names the store's `turns` and `turns.legs` columns have carried
  since their first migration, so a dashboard and a SQL query no longer
  need a translation between them. **Migration:** a token-count
  dashboard renames two fields.
- **Breaking: `tool_call` names only what this server authored** (#120):
  the event drops `tool` for every call whose name a peer chose and
  gains `source`, one of `builtin`, `device`, `mcp` or `unknown`, from
  the same classifier the store's `tool_invocations` rows are written
  with. A builtin still carries `tool`, because those names are this
  application's own; an MCP call carries `entry`, the name an operator
  wrote in their configuration, and never the far side's tool name; a
  device tool and a name nobody publishes are named by their `source`
  and nothing else. A published tool name is half whatever the far side
  called it and an alphanumeric credential survives sanitizing intact,
  which is the exposure #154 closed on the MCP lifecycle events and
  this closes on the one event that still carried a name.
  **Migration:** a tool dashboard groups by `source`, and by `entry`
  for MCP calls; the full name, its arguments and its result are on the
  store's `tool_invocations` rows. Alongside it, the warning about a
  model's unparseable tool arguments reports how many characters they
  were instead of printing them.
- **The provider-bearing events say which model answered** (#120):
  `llm_round`, `llm_retry` and `provider_failed` gain `model`, the
  configured model identifier, wherever the entry names one, which is
  OTel's `gen_ai.request.model`. Two entries of one type can run
  different models, and a turn's token totals blend the rounds that
  answered it, so the per-round, per-model truth needed somewhere to
  be. Additive: an entry with no model to name (the bundled VAD, a
  Piper voice, the mocks) carries no field rather than an invented one.
- **An MCP tool call is executed by the entry it was resolved against,
  or by nobody** (#120): a reload can move a published name between
  entries (`home__inside` coming up takes `home__inside__x` over from
  `home`), and a call resolved just before one landed could be executed
  by the new owner, under that owner's timeout, while being recorded
  and logged as the entry that had not run it. The entry a call was
  classified against now decides its routing and its timeout, and the
  registry refuses a name that has moved instead of following it. The
  model is told the tool did not run and answers in its own words, the
  same as for any tool that fails. **Operator-visible:** only in that
  window, and only as a tool call that failed where it used to
  succeed against a server the caller did not mean.
- **The session capture's decision track inherits the narrowing**
  (#120): the capture is a consumer of the same events, so its
  `<session>.jsonl` keeps every event minus the text, minus every tool
  name a peer chose, and with the renamed token fields. A builtin's own
  name is still on a captured `tool_call`, exactly as it is on the
  event; the conversation store's `events` rows drop even that, keeping
  every called tool's name on `tool_invocations` where the text switch
  governs it. Nothing else about a capture
  changes: the WAV beside it still records everything said in the room,
  which is the division of labour the capture was built on, and the
  session id correlates all three records. The startup warning says so
  in the same words: it announced "room audio and transcripts" while the
  events carried them, and now announces room audio and a track of the
  session's events.
- **`session_closed` says why a conversation ended** (#120): the event
  carried only `duration_s`, so the reason was inferable from whichever
  line happened to precede it and from nothing else. It gains `reason`,
  one of `limit`, `idle`, `drain`, `client` or `error`, decided at the
  site that decides and latched by the first cause to fire, so a drain
  closing a session an idle timer was about to hang up on reads
  `drain`. The conversation store copies the token onto the session
  row. Additive: no field was renamed or removed, and the README's
  event table and the pin suite moved with it. Alongside it, the three
  cleanup steps ahead of the event are now guarded individually, so one
  that fails is reported by class and the close still completes rather
  than the session ending with no record of having ended.
- **The test suite gets a shared fakes package, starting with the LLM
  SDK shapes** (#144): `tests/support` held four real MCP subprocess
  servers and nothing else, so a double for a vendor SDK object had to
  live in whichever suite happened to need it first, and the next suite
  copied it. `tests/support/llm_sdk.py` now holds the fourteen classes
  that fake the two streaming dialects a provider talks (the anthropic
  messages stream and the openai chat completions one), moved
  byte-identical out of the tool-calling suite, plus the one probe that
  answers False to a truth test, which four provider suites had each
  hand-rolled. Test-only: no source file changes, no assertion changes,
  and the moved classes are compared against their origin by normalized
  AST rather than by eye. A contract test pins the probe's falsiness
  where the probe lives, because the suites that inject it assert only
  that the provider kept the object it was handed, which a truthy probe
  would satisfy while testing nothing.
- **The session suites stop importing each other** (#144): the session
  family was a web of test modules borrowing helpers from each other,
  with `test_session.py` alone imported by thirteen, so a suite about
  capture or about the conversation store could not be read without
  first reading the session suite it borrowed its handshake from. Seven
  more modules under `tests/support` now hold what was shared, each
  named for the seam it serves: `configs.py` (the configurations and
  their constants), `providers.py` (the scripted models, ears, voices
  and endpointer), `sockets.py` (the three device-socket stand-ins),
  `wire.py` (driving a session over a real websocket and reading the
  reply back), `sessions.py` (building one in process and driving a
  reply through it), `events.py` (reading the structured log), and
  `device_tools.py` (the board's half of the device tool channel). The
  boundary pair (`StubRuntime` and its `FakeDevice`) is promoted to
  `boundary.py` as the package's stated seam-testing template: name the
  far side after the side it replaces, and give it only the calls the
  near side makes. No test module imports `test_session*`,
  `test_tools_device` or `test_boundary_contract` any more. Test-only:
  no source file changes, no assertion changes, the collected count
  unchanged, and every relocated definition compared against its origin
  by normalized AST rather than by eye.
- **No test module imports another, and a test says so** (#144): the
  feature suites were the other half of the web, thirty-three imports
  reaching into the OTA, onboarding, MCP, capture, conversations,
  bindings, drain, memory and ws-auth suites. Four more modules under
  `tests/support` hold what was shared: `checkin.py` (what a board says
  at its configuration check and the ceremony that binds it),
  `tools_mcp.py` (the MCP entries, the configurations that grant them,
  and one real server hosted for the length of a block), `stores.py`
  (the capture directory, the conversations database and the corrupted
  memory file) and `registry.py` (the sessions a drain walks and the
  bindings an operator writes). `configs.py`, `events.py`, `wire.py`
  and `sessions.py` gained the rest. Collisions were settled by naming
  rather than by merging: two manifests, two client builders, two
  MAC constants and two configuration builders that were not the same
  thing keep separate definitions with names that say which seam they
  serve, and every importing site keeps its own spelling through an
  import alias, so no assertion moved. The new
  `tests/unit/test_support_boundaries.py` walks the whole lane with
  `ast` and fails on a test module that imports another, at the top of
  a file or inside a function, and on anything under `tests/support`
  that imports a test module, so the criterion is enforced rather than
  remembered; a conftest stays allowed. Test-only: no source file
  changes, no assertion changes, the collected count up by exactly the
  guard's four tests, and every relocated definition compared against
  its origin by normalized AST rather than by eye.
- **Three places where two encodings have to agree are pinned by a
  test** (#144, completing it): the drift the 2026-08-14 review found
  was in pairs nothing checked against each other, so each pair is now
  a test that states the relation the two sides actually hold.
  `config.example.yaml` is walked against `ServerConfig`: every leaf
  field, through the nested sections found in the annotations rather
  than from a list kept by hand, has to be in the example, written or
  commented out, at the depth it belongs to, since the file's own
  convention is that a default worth keeping appears as a commented
  `# key:` line with its reasoning. The example filenames in
  `docgen.ENTITIES` are checked against `examples/` both ways: every
  name an entity gives is a file that exists, and every file is claimed
  by exactly one entity, so neither a renamed example nor an unclaimed
  new one can pass. And the five frozensets in `cli.py` that decide
  whether a body can be read as a pending listing, a status entry, a
  reload's answer or a prompt block are bridged to the pydantic models
  in `api.py` that produce those bodies, each with its true relation:
  a subset where the CLI renders less than the model carries, equality
  for the status fields and the state vocabulary, the reload outcomes
  read off the model's annotations with the tuple's no-duplicates
  clause pinned separately, and the prompt-block fields against what
  `PromptBlock` requires rather than everything it has, since `name` is
  optional there. Every branch of every pin was exercised by mutation,
  applied and reverted: each proof watched failing with its message,
  and the one control that must pass (deleting a member of the pending
  predicate, whose relation is a subset rather than an equality)
  watched passing. Test-only: no source file changes, no assertion
  changes, and no existing test touched. The bridge file says in its
  docstring that it exists to be deleted whole by #139, which deletes
  the predicates it pins.

## 2026-08-15

### Added

- **The MCP lifecycle is on the event surface, and consumers have an
  interface to attach to** (#138): the subsystem that runs an
  operator's MCP servers recorded nothing structured, so a collector
  could not count a connection that came up, group failures by kind,
  or tell a reload that applied from one that refused. Five events now
  say all of it on the channel that module already logged on:
  `mcp_connected` (`entry`, `transport`, a count of `tools`,
  `duration_ms`), `mcp_down` (`entry`, `reason` from a closed set of
  six, `duration_ms` where a connect ran), `mcp_call_dropped`
  (`entry`, `position`, `error`), `mcp_reload` (`outcome` applied or
  refused, with the four counts or a refusal reason) and
  `mcp_tool_shadowed` (`entry`, `position`, `owner`). All five are in
  the README's event table and are a compatibility surface from here
  on. None of them carries a byte a third-party server chose: the
  reasons and the failure classes are tokens and type names picked
  where a failure is classified, never a message, and no line names a
  tool at all. A published tool name is half whatever the far side
  called it, sanitizing replaces only the characters both LLM APIs
  refuse, and an alphanumeric credential goes through that untouched,
  so every line about one tool says which one by its position in that
  server's listing and `samtal-server config status` prints the names
  to a terminal for whoever asks.
  Alongside them, every event this server emits is now offered to an
  explicit **tap** interface (`Emission`, `EventTap`) before it is
  logged, with a hub a server-scope consumer attaches to once for
  every subsystem; the JSON log and the session capture are its first
  two implementations, and #120's conversation store and the #66/#67
  exporters attach without touching a single emit site. **Operator-visible:**
  two new lines an MCP deployment did not see before, an intentional
  stop (at INFO, because a shutdown is not a problem) and a refused
  reload (at WARNING). A tool call that fails because its MCP server
  did also changes what the model is told: it now gets a fixed sentence
  naming the entry, where it used to be handed whatever the far side's
  SDK put in its exception, which could quote a response body straight
  into the conversation. What the failure was is in the
  `mcp_call_dropped` event instead, by class.
- **The conversation store's foundation, dormant** (#120): a second
  SQLite database, `conversations.db`, beside `samtal.db` in
  `server.database.dir`, with its own metadata and its own migration
  chain, holding sessions, turns, tool invocations and events as the
  queryable record of what was said. Nothing constructs it yet: no
  configuration key exists, no server behaviour changes, and no
  `conversations.db` is created by a server that is not asked for one.
  What landed is the machinery. The writer runs on one background
  thread behind an unbounded queue, so no producer on the session loop
  can ever wait on it; the bound sits on the droppable class, so a
  wedged database drops events with one warning per session and a count
  on the session row, and never refuses a turn or a close at the queue
  (a close whose own transaction fails still leaves the session row
  open-shaped, which is the documented incomplete state). It commits at
  markers into per-session batches, so a session's turn commits its own
  session and holds no write lock between turns, and every marker first
  confirms its session row still exists, so a purge of a running
  session is final rather than a race the next turn undoes. Retention
  deletes whole sessions older than a stated window (90 days by
  default, `0` an explicit opt-out) and deletion is physical:
  `secure_delete` and a truncating checkpoint, so a purge reaches the
  file's bytes and not only its index. The two storage switches
  (metrics, text) are applied at write time, with the events table
  stripped of conversation text from its first row.
  Alongside it, `samtal-server conversations purge --session | --device
  | --before` deletes from the file with no server running, because
  deletion has to work exactly when the server is broken or gone, and
  `samtal-server conversations schema` prints the generated reference
  committed at `docs/reference/conversations-schema.md`, which states
  the compatibility promise, what each switch takes away, the retention
  and deletion semantics with their limits, the WAL-safe way to take a
  copy, and the OpenTelemetry GenAI correspondence. **Operator-visible:**
  nothing yet, by design.
- **The conversation store's content record, dormant** (#120): the
  pipeline now assembles one record per completed turn and hands it to
  an optional recorder where `replied` is emitted, so a cancelled or
  failed reply records what its finally saw and an utterance nobody
  transcribed records nothing. The record carries what was heard with
  its duration and language, the joined reply and its per-agent legs
  after a handover (each with its own token counts, because a turn's
  totals blend agents that may run different models), every call the
  model issued with the position it issued it at, and the turn's
  measured numbers. Tool calls are recorded from a closed set of
  sources (`builtin`, `device`, `mcp`, `unknown`) decided by one
  classifier consulted before anything runs, so the record covers the
  paths the routing hides: a malformed call flagged as one, a name
  nobody publishes with its refusal, and a handover with its refusal or
  with the switch it made. Two measurements are new: the elapsed of the
  transcription a turn ran (null when the turn reused a barge-in's,
  which was measured elsewhere for another decision), and the reply's
  first synthesis request to its first audio bytes, taken at the
  synthesis provider and deliberately not at the device. This is a
  content channel beside the event tap rather than text read back off
  the events, because tool arguments and results never rode the events
  at all and the events are about to lose their text.
  **Operator-visible:** nothing yet, by design: no configuration key
  exists, nothing injects a recorder, and no event, log line or
  timing changed.

### Changed

- **One emitter serves every event, at an altitude no subsystem owns**
  (#138): the emitter behind the declared observability surface lived
  in `device/events.py` and was called by the device edge and the
  pipeline alone, while eleven other modules hand-built an
  `extra={...}` dict at each of forty-two sites and one provider had
  invented a private builder of its own. It now lives in
  `samtal_server/events.py`, which imports nothing from the packages
  that import it, and it emits rather than returning a payload for a
  call site to log around: a site says
  `events.info("heard %r", text, event="heard", ...)` and the emitter
  builds the payload, wraps it and offers it to every consumer.
  Session-scoped events keep the pinned `samtal_server.session`
  channel and server-scoped ones each keep the module logger name they
  already had, so every retained record's `logger` field, event name,
  level and field set is byte-identical; two characterization suites
  written before the move and unchanged after it are the evidence, and
  an AST test now fails any production logging call that carries its
  own `extra=`. The capture is offered every event before it is
  logged, as it always was, and a consumer that raises no longer costs
  the operator a log line. **Operator-visible:** writing a pin for every
  record turned up several that quoted something they should not, and
  each of those is now narrower. A rejected `Device-Id` is no longer
  echoed into the line that turns the device away, and a refused
  handshake names an unidentified client with a null `device` rather
  than whatever header arrived; the lines that report a failure render
  its class name rather than a dependency's own sentence; and the
  onboarding banner and the key-miss line carry no key material at
  all, with `samtal-server config ota-url` the only route to the URL.
  No event name, level or channel changed, and the only field that did
  is the one that held a value nobody had authenticated.

- **The egress guarantee is one rule, and a provider's marking is
  mandatory** (#136): the `server.local_only` enforcement behind the
  local-first promise lived in two places, the provider registry and
  the MCP build path, each with its own semantics, wording and
  exception type. Both now call `samtal_server/egress.py`, which holds
  the rule and every refusal sentence unchanged, word for word,
  including the provider messages' "off this host" and the MCP ones'
  "off this network"; each call site keeps only the exception type its
  surface promises. The registry no longer defaults an undeclared
  provider type to egress, and the `Provider` base no longer carries
  `egress = True`: every provider class declares its own marking in its
  own class body, inheriting one from a parent does not count, and a
  class that declared nothing or declared something other than true,
  false or null is refused when it is built, in any mode, with a
  message naming the class and not the value. Operator-visible
  behavior is unchanged for every declared provider and MCP entry; the
  new refusals can only be reached by code adding a provider type.

- **Providers share a kit, and a failed request has a type** (#137):
  the five provider types that reach a network re-implemented the same
  plumbing, three of them importing credential resolution from the
  Anthropic provider and two carrying identical copies of the PCM
  alignment loop, while `DEFAULT_TIMEOUT_S` was declared three times.
  `samtal_server/providers/kit.py` now owns credential resolution, the
  timeout and token defaults, the retries-off policy and the alignment
  helper, and no provider imports another. Request failures leave as
  `ProviderCallError`, or `ProviderCallTimeout` when the failure was a
  wait; neither is a `RuntimeError`, so a provider failure can no
  longer be mistaken for a vanished device, and the timeout one is a
  `TimeoutError`, so classifying it needs no substring matching. Both
  LLM providers gained the injectable `client=` seam the other three
  already had, and their SDK clients now carry a 30 s per-operation
  transport timeout with automatic retries off, where they previously
  had no bound at all and the SDK's two retries; no new configuration
  key, and a streaming reply that keeps delivering is not cut off by
  it. **Operator-visible:** a wrapped failure's message and the
  `provider_failed` event's `error` field now report the taxonomy class
  with the SDK's class name and the HTTP status, and no longer the
  vendor's own sentence or response body, which could echo request
  content or a credential into the retained logs. The ElevenLabs
  failure message loses its quoted body detail for the same reason.

### Fixed

- **A session test's synthesis fixtures pass a real failure callback**
  (#135): `test_only_a_sentence_whose_audio_finished_counts_as_spoken`
  built its two `_Synthesis` objects with the session id where the
  constructor takes the callback a failed synthesis is reported
  through. The test itself was earning its verdicts (it speaks one
  sentence to the end and cancels another mid-send, and neither path
  reaches the callback), but the fixture violated the constructor's
  contract, and under a failing voice that argument turns the provider
  failure into `TypeError: 'str' object is not callable` on its way
  out. Both constructions now share a recording callback with the
  signature spelled out, and the test asserts nothing was reported, so
  a failure appearing where the mock voice is supposed to work is a
  failing test rather than an unnoticed one. No production code
  changed.

- **A provider failure during a reply is reported instead of read as a
  vanished device** (#137): the reply body caught `RuntimeError`
  broadly and returned in silence, because that is what a starlette
  socket raises for a send that comes after the close and a
  disconnected device is not worth a word. A provider failure raised as
  a bare `RuntimeError` went out the same door, with the reply ending
  quietly and nothing on the record. Both of the transport's disconnect
  shapes are now translated at the device edge into the `DeviceGone`
  the boundary always promised, the reply body catches that type alone,
  and everything else is logged as "reply failed". A local bug in a
  reply, in the encoder or the resampler or anywhere else that speaks,
  is likewise on the record now instead of being taken for a device
  that went away. **Operator-visible:** that line names the exception's
  class and stops there. It used to carry the traceback, and the arm it
  comes from now catches every provider failure as well, whose message
  and chain of causes can hold whatever a response body held; the
  stage, the provider and the host of anything that failed on the wire
  are still on the `provider_failed` event beside it. Whether a failure
  was a wait is decided by type rather than by looking for "Timeout" in
  a class name; the `provider_failed` event's fields and its sentence
  are unchanged.
  The filler playback keeps its broad catch, whose narrowing is #141's.

## 2026-08-14

### Added

- **An MCP server entry carries its own guidance** (#122): an
  `instructions` field on an `mcp_servers` entry holds what the model
  should know about using that server's tools, and it is injected into
  the system prompt of every agent the entry is granted to, under a
  heading naming the prefix its tools carry (`home__`). Know-how about
  a capability now lives beside the capability instead of being copied
  into every persona that was granted it. The grant is the whole
  condition: it is injected whether or not the server is connected and
  whatever an allow list narrows its tools to, and an agent with
  `mcp: []` sees none of it. The text is stored and injected exactly as
  written, indentation and blank lines included. Editing it does not
  restart the connection, so a reload reports the entry as `unchanged`
  and the tools do not blink; the new text reaches a conversation at
  its next activation, a new session or an agent switch, and never a
  reply of one already running.
- **Agents share blocks of prompt text** (#122): a `prompt_fragments`
  section maps a name to one block of text, and `prompt_includes` on an
  agent or on `agent_defaults` names the fragments that agent's system
  prompt carries. Household facts or a house style are then written once
  instead of being copied into every persona prompt and drifting apart.
  The list follows the `mcp` field's rules exactly: unset inherits the
  `agent_defaults` list, naming a list replaces the inherited one rather
  than extending it, and `prompt_includes: []` opts one agent out of
  what its siblings share. A fragment is injected in the order the layer
  lists it, between the agent's own prompt and any MCP guidance, with
  nothing added around it and nothing trimmed inside it, and it is
  counted under `fragment:<name>` by `samtal-server config prompt` and
  by the `prompt_assembled` event. A name that matches no fragment is
  refused when it is written and reported by layer and list position
  rather than by value, since a name written beside prompt text is
  where a credential gets pasted. Fragments are part of the boot-time snapshot,
  so a write applies at the next server start.
- **A server's own guidance is consumed behind two opt-ins** (#122):
  an MCP server ships guidance about itself through two channels, the
  `instructions` of its handshake and the prompts it publishes, and
  each is injected only where an entry says so.
  `use_server_instructions` (off by default) takes the first;
  `inject_prompts` names published prompts one at a time, by the name
  the server lists them under, and injects them in the order listed. A
  published prompt renders as the text of its messages in order, joined
  by blank lines and with the roles dropped. Names are validated
  against the server's own paginated listing before anything is
  fetched, and a name it does not publish, a prompt declaring required
  arguments, and one that renders anything but text are each skipped
  with a warning naming the entry and the position in the list, never
  the name, since a prompt name is a string the server chose. Both
  channels are capped at 4000 characters per block, skipped whole
  rather than truncated, and nothing a server ships is ever written to
  a log. What is injected is counted under
  `server_instructions:<entry>` and `server_prompt:<entry>:<position>`
  by `samtal-server config prompt`, under a heading in the prompt
  saying the server is the one talking. What a server ships is captured
  on every connect whatever the flag says, so turning
  `use_server_instructions` on applies at the next reload with no
  reconnection; editing `inject_prompts` changes what a connect
  fetches, so that one restarts the connection.
- **An operator can read an agent's assembled prompt** (#122):
  `GET /api/runtime/agents/{name}/prompt`, and `samtal-server config
  prompt <agent>` as its client, answer the system prompt a session
  opening now as that agent would be sent: the ordered blocks, each
  with its provenance (`persona`, `fragment:<name>`,
  `instructions:<entry>`, `server_instructions:<entry>`,
  `server_prompt:<entry>:<position>`, `memory`), its size in characters
  and its text, plus the total to tune a small model's context budget
  against. It is assembled from the loaded agents, the running MCP slice
  and the memory store rather than from the database, so it cannot
  disagree with what a session would get, and it is a preview of a new
  session rather than a readback of a running one. The CLI prints whole
  blocks and truncates nothing, since a concealed tail is exactly what
  an operator came to see. Agent activation also logs a
  `prompt_assembled` event with the same per-source counts.

### Changed

- **The system prompt is assembled in one documented order** (#122):
  the agent's own prompt, then the shared fragments it includes in the
  order its layer lists them, then the guidance of each granted MCP
  entry in grant order, then the remembered facts last under the
  heading they have always had, separated by blank lines. For a
  deployment with no guidance and no fragments configured, the prompt is
  character for character what it was. The persona, the fragments and
  the guidance are assembled when a conversation starts and again when
  it switches agents, and held for the life of
  that activation; the remembered facts keep the clock they had, read
  on every reply, so a fact stored by one conversation is still known
  to a concurrent one on its next reply. That read now happens in a
  worker thread rather than on the event loop every conversation
  shares.

### Fixed

- **A `--local` write says when it really applies** (#134): three
  commands on the break-glass path told an operator to restart the
  server for a change `samtal-server config reload` applies. `config
  --local delete mcp-server`, and `set-secret` and `clear-secret` on an
  MCP entry's slot, now name the reload, which is what the same act
  through the configuration API has always answered. The line every
  `--local` invocation prints first changed with them: it said that a
  running server observes no local change until its next start, device
  bindings excepted, which would have contradicted the corrected notice
  one line later. It now says what the path is and leaves the timing to
  the write, which answers it in the same three cases the API does. A
  test runs each act of the `--local` mutating subset both ways and
  compares what the two printed, so a future notice on one path that
  the other does not follow fails rather than being noticed by
  somebody. The deployment notes and the configuration section of the
  server README carried the same absolute claim in four places and are
  corrected with it.

## 2026-08-13

### Added

- **A running server says what its MCP servers are doing** (#121):
  `GET /api/runtime/mcp-servers`, and `samtal-server config status` as
  its client, answer with one entry per configured `mcp_servers` entry:
  whether it is connected or down, since when, the reason token if it
  is down, the tools it published, and the agents that may reach it. An
  entry no agent references reports `unused`, which is the answer to
  "why does the agent not have that tool" when the entry itself looks
  right and was invisible until now. The read is answered from the
  running server's own managers rather than from the database, so it
  cannot disagree with what is connected, and it carries published tool
  names only: a description or a server's own listed name is bytes that
  server chose, and one holding a credential of the deployment's could
  reflect it there. `/runtime` is a namespace of its own because an
  `mcp_servers` entry may legally be named `status`.
- **An MCP change applies without a restart** (#121):
  `POST /api/runtime/mcp-servers/reload`, and `samtal-server config
  reload` as its client, have a running server re-read the
  `mcp_servers` entries, the secrets stored on them and the agents'
  effective `mcp` grant lists, and apply them: an entry that is new or
  newly referenced is started, one whose fragment or whose stored
  secrets changed is stopped and rebuilt (so a rotated credential
  applies here too), one that is gone or no longer referenced is
  stopped, and an unchanged one keeps the connection it had. No session
  is dropped: a conversation in progress picks the new world up on its
  next utterance. The reply carries the four outcomes by entry name and
  the whole status document, so one round trip both applies and
  verifies. Nothing is stopped or started until every manager the new
  configuration needs has been built, so a refusal (an unset `$VAR`, a
  credential that will not decrypt, an egress declaration
  `server.local_only` forbids, a snapshot that will not validate)
  changes nothing at all; a server that merely will not connect applies
  as `down` with its reason, revivable as ever. One reload runs at a
  time, and a concurrent one is refused with the same retryable 409 a
  contended write answers with.

- **An agent can be granted single tools of an MCP server** (#121): an
  `mcp` list entry is either the entry name on its own, which is the
  whole server as before, or an object naming the server and the tools
  of it that layer may reach (`{server: home, tools: [turn_on_light]}`),
  on agents and on `agent_defaults` alike. Tools are named the way the
  model is given them minus the entry prefix, which is the name
  `samtal-server config status` prints, so what the server called a tool
  before the publishing rule got to it never has to appear. It is an
  allow list and there is no deny list, since a denied set would
  silently grant whatever the server adds next. The grant filters what
  the agent is offered and is checked again when a call arrives, so a
  tool an agent was not offered is refused rather than run. A name the
  server does not publish is logged when its tools come out of the
  publishing rule, compared against what published rather than what was
  listed, and the status surface shows each agent's allowed tools beside
  the published ones.

### Changed

- **Three tool decisions are documented where they are met** (#121):
  that the builtins are outside the grant model, each appearing under a
  structural condition (`switch_agent` when the device is bound to more
  than one agent, `remember` when memory is configured) because
  withholding either per agent would strand a handoff or leave an agent
  recalling and never learning; that an SSE-only server is configured as
  a stdio server behind an `mcp-proxy` bridge, there being no native SSE
  arm now that the specification has deprecated the transport in favour
  of streamable HTTP; and that a tool result is speakable text, with
  content of any other kind rendered as a named placeholder rather than
  dropped, so the model can say what it received, until a display path
  that renders more than speech makes structured results worth carrying
  to the board. The server README's tools section carries all three, the
  two MCP server examples carry the bridge, and the generated reference
  carries a sentence of each on the `mcp`, `transport` and `mcp_servers`
  descriptions.

- **MCP writes name the reload instead of a restart** (#121): the entry
  writes and deletes, and the writes and clears of the secret slots on
  them, answer with a notice naming `samtal-server config reload`.
  Provider writes, provider secrets and agent writes keep the restart
  sentence, the last of them because an agent fragment mixes the
  reloadable `mcp` list with a prompt, providers and filler that are
  not. The CLI's `--local` recovery path keeps it for every entity,
  since it runs where there may be no server to ask.

- **The MCP streamable_http transport left the SDK's deprecated
  client** (#98): the transport now builds its own `httpx.AsyncClient`
  with the configured headers and the HTTP policy the SDK's factory
  used to supply for it (redirects followed, a 30 s timeout with a
  300 s read for a stream a server holds open), and hands that client
  to the SDK's replacement transport. Nothing changes for a running
  deployment; what changes is that the transport is no longer one SDK
  release away from breaking, and that the header-delivery test no
  longer has to ignore a deprecation warning to make its point. The
  `mcp` requirement moves from `>=1.2` to `>=1.24,<2`, since 1.24 is
  the first release carrying the replacement and MCP 2 needs httpx 2
  and yields a transport this client does not unpack, which makes
  migrating to it separate work.

- **The hardware checkpoint's findings are in the notes and the
  AMOLED guide** (#40): the captive portal's Custom OTA URL field is
  vendor-build-dependent rather than a firmware-version threshold
  (present on the Waveshare factory AMOLED-2.16 image at 2.2.4,
  absent from stock 2.4.0 on the Touch-LCD-1.54); the firmware's OTA
  client does not follow redirects, so a slashless OTA URL against a
  redirecting server shows `code=307` and restart-loops, which is why
  a device-facing endpoint must serve the slashless spelling
  directly; no portal re-entry was found on a provisioned factory
  AMOLED, so repointing a provisioned board is the NVS-over-USB
  route, not a portal retype; and the activation ceremony is
  validated end to end on both boards against samtal-server, code to
  connected with no power cycle. The AMOLED guide gains the factory
  launcher's behavior (the assistant is the AIChats app), the three
  labeled buttons, the white-screen quirk, and a portal-first
  onboarding section.

### Fixed

- **The short onboarding path answers with and without its trailing
  slash** (#40): a captive portal saves the typed URL as it likes, and
  the first factory board onboarded over this route saved it without
  the slash, POSTed to `/x/<key>`, and refused the redirect the server
  answered with, showing `code=307` on screen and restarting in a loop.
  Both spellings are now served by the same handlers, so nothing a
  device reaches depends on a redirect being followed. A wrong key
  still answers the same 404 as any unserved path on either spelling.
  Typing the URL with its trailing slash is still what the banner and
  the docs print, but it is no longer load-bearing.

### Security

- **An MCP server can no longer write to the logs** (#98): the SDK's
  HTTP client narrated what the far end said, including the session id
  it chose, the raw body of an initialization result that would not
  parse, and a traceback quoting the bytes that failed, all of which
  reached the collected JSON log of any deployment with an HTTP MCP
  server configured. Those records now stop at the SDK's own logger,
  and this server's unavailability warning prints a reason built from
  exception types instead of the exception's message, which a broken or
  hostile server writes half of. The line an operator greps for is
  unchanged and still names the server and the kind of failure, for
  example "mcp server weather is unavailable, its tools are absent:
  ValidationError". Debug logging is unaffected and still prints
  response headers from every HTTP client in the process, MCP servers
  included.

## 2026-08-12

### Changed

- **`server.websocket_url` refuses a URL carrying credentials** (#40):
  the OTA endpoint renders this value verbatim in the reply to a GET,
  and the short onboarding path serves that same GET, so a
  `user:password@host` written here was readable by anyone holding the
  onboarding URL. It is now refused at load, without the value being
  quoted back. This is a behavior change for a configuration that was
  already leaking, and no other websocket URL is affected.

- **Binding a device no longer needs a restart** (#40): a running
  server now reads the `devices` table and `default_agent` as a device
  asks for them, so binding a board, unbinding it, or changing the
  default agent applies at that device's next OTA check or connection.
  Everything else stays a boot-time snapshot with its restart notice
  unchanged, and the acknowledgement for a device write says which of
  the two happened: it names the restart when the binding points at an
  agent this server has not loaded, which is what a binding written
  after boot to a newly created agent does. A conversation already
  running is never touched, and a delete stops the next token and the
  next connection rather than reaching into one in progress. The read
  runs off the event loop through a connection of its own that never
  migrates and takes no write lock, so an operator's write cannot stall
  a device's check-in, and a database that cannot be read falls back to
  the configuration the server started with, saying so in the log. The
  configuration API's description, its acknowledgement schema, the
  committed OpenAPI document and the server README carry the two
  notices.

- The word "persona" is gone from the server's own voice: model
  docstrings and field descriptions, the generated domain reference
  and OpenAPI document, the example config's memory comments, two
  code comments, both READMEs, and the example fragments now say
  agent, matching the terminology decision recorded in
  `docs/concepts.md` and the glossary. Historical records (old
  changelog entries, plans, issues) keep their original wording, and
  tests keep their internal persona identifiers.

### Added

- **`samtal-server config ota-url` and `samtal-server config doctor`**
  (#40): the two commands the onboarding ceremony is run with.
  `ota-url` prints the URL to type into a board's captive portal and
  contacts nothing at all: no server, no database, no API token. It
  reads the same config file the server reads, takes the device
  authentication secret from the same environment variable, and derives
  the key and the origin with the server's own functions, so the string
  it prints is the string that server answers on; the URL goes to
  stdout alone and the guidance goes to stderr, with an origin nobody
  configured reading as the guess it is. Onboarding turned off names
  `server.ota_path` without quoting the segment, a missing secret names
  the variable, and a key pinned under `server.onboarding.key` prints
  its URL with no secret at all. `doctor` GETs that URL, or one given
  as an argument, and reports what a device would be told: nothing
  answers there, something other than samtal-server answers,
  samtal-server answers but sends devices to a plain `ws://` URL from
  behind TLS (the proxy misconfiguration that fails at the handshake
  with every other line looking right), or it is healthy, naming the
  websocket URL, server version and protocol version it reported. It is
  a GET and never a POST, so it mints no activation code and spends
  none of the issuance budget, and it carries no bearer token, since
  the token issuer cannot require one. Both READMEs and both example
  configs are rewritten around the ceremony, and the deployment
  profile's advice to inject the OTA path segment from a secret store
  is replaced by the derived key, with the legacy `ota_path` kept
  documented for boards already carrying one.

- **Onboarding a board by the code it shows** (#40): a device the
  database holds no binding for, on a deployment with no default agent
  to cover it, is now answered at its configuration check with a
  six-digit activation code instead of an empty token. Stock firmware
  shows that code on screen, speaks it digit by digit, and polls the
  server every three seconds, so an operator reads the code off the
  board in front of them, runs `samtal-server config add-device CODE
  AGENT`, and the board connects seconds later with no power cycle and
  no button press. `samtal-server config pending` lists the boards
  waiting, with the model and firmware version each reported, which is
  what tells two boards on one desk apart; `add-device` sits beside
  `bind-device`, which still takes a MAC you already know. The two
  routes behind them are `GET /api/devices/pending` and
  `POST /api/devices/pending/{code}`, both in the committed OpenAPI
  document, and the claim writes through the same repository call
  binding by MAC uses, so its reference checks and its acknowledgement
  are the same. Nothing changes for a deployment with a default agent
  set: it covers every unknown MAC by design, so its devices keep
  receiving a token and no activation object. Codes last ten minutes,
  are re-issued at the next check-in after that (the board displays
  whatever the fresh reply carries), are forgotten when the server
  restarts, and are bounded both in how many devices may wait at once
  and in how many may be minted per ten minutes, so an unauthenticated
  endpoint cannot be made to mint without limit. A version-2 activation
  poll is answered by the same ceremony: its HMAC is computed with a
  key burned into the device, which this server has no copy of and
  cannot verify, so what is checked instead is that the body parses,
  names a known algorithm, and echoes the challenge this server issued.

- **A short onboarding path and a startup banner** (#40): the OTA
  endpoint is now also served at `/x/<key>/`, where the key is eight
  base32 characters derived from the device authentication secret, so
  onboarding a board means typing eight unambiguous characters into its
  captive portal instead of a long random path segment. Nothing about
  the key is configured or stored: it is stable across restarts and
  rotates only when the secret does, `server.onboarding.key` pins the
  previous one across a rotation, and a wrong key answers the same 404 a
  path that was never served answers while logging the attempted key
  beside the correct one, so a typo diagnoses itself. With device
  authentication off there is no secret, so the route is served keyless
  at `/x/`. `server.onboarding.enabled` (on by default) switches the
  route off, `server.ota_path` accepts null to unmount the legacy route,
  and unmounting both refuses the boot. The server now prints the whole
  URL to type at startup and on a GET of the OTA endpoint, naming the
  origin it came from: the new `server.public_url`, else the origin of
  `server.websocket_url`, else the listen address, which reads as the
  guess it is. Legacy OTA behavior is unchanged; the short route and the
  startup line are what an upgrading deployment sees.

- **The stock activation ceremony, documented** (#40):
  `docs/xiaozhi-notes.md` now records the 6-digit code flow
  reconstructed from the vendored firmware and manager-api sources
  (the optional `activation` object, the `/activate` poll and its two
  `Activation-Version` forms, the challenge that makes polling fast,
  and the loop that makes binding take effect without a power cycle),
  plus why the OTA URL is the only place a stock board can carry a
  secret, and that the OTA response cannot change the device language.
  The captive-portal note is corrected: current factory firmware has a
  Custom OTA URL field on its Advanced tab, which v2.4.0 lacked.

- **Device guides** (#93): `docs/devices/`, one user-facing guide per
  supported board plus a common page for what every board running the
  upstream firmware shares. The Touch-LCD-1.54 guide is written in
  full (the complete control inventory read against the board support
  code, including the gestures the old firmware README never listed
  and the idle-only echo-cancellation toggle that takes barge-in with
  it; the wake word with an exact account of what the server does and
  does not learn; the voice commands the board publishes as MCP tools;
  the display's two power-saving timers with their real conditions);
  the ePaper and AMOLED guides are stubs marked 🚧 whose every section
  names its source, hands-on or read from the firmware. The
  "Using the device" section moves out of `samtal-esp32/README.md`,
  correcting on the way its claim that the listening behavior it
  described was universal, and leaves a pointer behind. Both hardware
  tables link the guides as `guide · wiki`, the root README's Getting
  Started sends the reader to their board's guide for which button to
  press, and `docs/README.md` indexes the new directory.

- `docs/concepts.md`, the domain model from the user's point of view:
  device, agent, binding, conversation, session, and users as a later
  stage, with the decided semantics. The wake word wakes the device
  and the default agent answers; a mid-session switch lasts for the
  session and starts clean by default, carrying context only on
  explicit request; conversations suspend rather than end, can resume
  from another device, and are cross-linked with the sessions that
  touched them, which makes the session transcript its own artifact;
  turns that are only meta requests are recorded as session events,
  not conversation entries; memory stays agent-keyed with a planned
  shared user profile; meta questions are built-in tools in every
  agent with conversation search scoped to the asking agent; a
  built-in help agent explains the device and the system, reading
  observed device facts (#96) and the per-board guides (#93). The
  glossary gains entries for the new nouns and links them to the
  page.

- **Documentation for the configuration API** (#101). The server README
  gains a section on it under Configuration: what mounts at `/api`, the
  bearer token and how to generate one, the routes a noun at a time,
  what a masked read adds to an entity, the acknowledgement that says
  when a write applies, the refusal mapping, and
  `docs/reference/api-openapi.json` as the contract. The
  `samtal-server config` commands are documented as the client they now
  are, with the order they resolve the URL and the token in and exec
  into the running container as the deployment story, and `--local` as
  the break-glass path with its subset and its notice.

  Deployment notes beside the ones for the configuration database cover
  the four decisions a deployment has to make: setting
  `SAMTAL_API_SECRET` before rolling the image rather than meeting the
  boot error, what to do with `/api/` at the edge (do not route it
  outward, route it separately and restrict it, or accept that the token
  is the only thing in front of it), loopback-or-TLS for the whole API
  because the token rides on every request, and the recovery procedure
  in full.

  The generated domain reference now says that those commands are a
  client of the API and points at its document and its schemas; the root
  README's Getting Started gains the from-outside case beside the
  from-inside one it already had.

### Fixed

- Documentation that the API's arrival had made untrue (#101): the
  server README's list of what the server exposes and its "nothing else
  is exposed" paragraph, neither of which mentioned `/api/` or the rule
  that keeps the OTA endpoint out of it; the Stack section's "future
  admin API"; the promise that hot apply is what the admin API will
  bring, when the API answers with the same restart notice; and
  `config.example.yaml`'s comments about who writes the database, one
  of which offered `config list` as an example of a command that opens
  it directly, which it no longer is.

- `docs/concepts.md` claiming that the protocol version, the feature
  map and the device's own MCP tool list all arrive at `hello` (#109):
  the tool list comes from a separate background MCP handshake started
  after the hello, which a first utterance can beat, so the Observed
  facts bullet now names the arrival phases in wire order (OTA
  check-in, hello, background discovery, first listen, `listen`
  `detect`) and defers to the protocol notes for the race.

- The same bullet's "today these are parsed and dropped" (#109), which
  understated retention: board model and firmware version cross the
  OTA-to-session boundary in the bounded in-memory cache, which a
  session reads into its manifest when capture is enabled, and the
  protocol version, the discovered MCP tools and the listening mode
  are retained for the life of the session, the protocol version also
  entering an enabled capture's manifest. Only the wake-word report is
  merely debug-logged, and what none of it enters is a durable,
  queryable per-device record (#96).

- `docs/xiaozhi-notes.md` saying that the server "learns only that a
  session opened" when a wake word fires (#109). The trigger audio is
  genuinely unreachable, since ESP-SR decides on-device, but the
  firmware sends `listen` `detect` with the fired word in `text`,
  which `device/session.py` receives and debug-logs; the entry now
  separates the constraint from the report and gives the report its
  tier.

- The glossary's Manifest entry (#109), which listed "device,
  firmware" and named neither the client ID, nor the board model
  cached from the last OTA check-in, nor the session's protocol
  version, all of which the manifest actually carries.

### Removed

- The pipecat spike code under `spikes/pipecat-alignment/`, 3,714
  lines across 15 files, throwaway by the declared policy of its own
  plan and now that its findings are written down. The record of
  reference is unchanged: `docs/plans/2026-08-11-pipecat-alignment-spike.md`
  and its implementation doc still hold both gate verdicts, the line
  counts, the 23-item seam-obligation map, the offsets and drift
  figures, and the pinned pipecat-ai 1.7.0. The code stays recoverable
  from the annotated tag `spike/pipecat-alignment`, on the last commit
  that contains it, and from PR #90. Removing it costs nothing
  structural (no README, AGENTS.md or workflow referred to the
  directory, and no CI step ran it) and settles a standing ambiguity:
  a second uv project beside `samtal-server/` reads as a component,
  and unlinted code pinned to a release it can no longer be measured
  on only drifts. The pipecat issues stay open on their own terms and
  none of them is v1 work: #84 is a decision record that already
  concludes "not now", #91 promotes the serializer as an external
  showcase that never touches `samtal_server/`, and #92 is a design
  note whose later stages wait for a driving use case.

## 2026-08-11

### Added

- **The configuration API writes** (#101). Every write the
  `samtal-server config` commands can make is now a route under `/api`:
  PUT and DELETE per entity kind, the two write-only secret endpoints,
  the device binding, and the default agent with its idempotent clear.
  A successful write answers with what it did and the sentence saying
  when it applies, because configuration stays a boot-time snapshot.
  Fragment bodies are received as opaque objects and validated only in
  the repository, so the one validator that could echo a rejected body
  back (and with it a pasted credential) is never in front of them; the
  three argument-shaped bodies are parsed to an exact shape and refused
  with a description of what was expected rather than a quote of what
  arrived. The committed OpenAPI document
  (`docs/reference/api-openapi.json`) carries every write, the schema
  each body takes, and the refusals each can answer with.

- **`samtal-server config --local`** (#101), the break-glass recovery
  subset: `show`, `delete`, `clear-secret` and `set-secret` against the
  database directly, for a deployment whose server will not start. Every
  `--local` invocation says on stderr that it bypasses the API and that
  a running server will not observe the change until its next start;
  every other command refuses the flag by naming the four. Its `show`
  and `delete` go by what is stored rather than by what a new write
  would be allowed to create, so a row that predates the addressability
  rule stays readable and removable.

- **The configuration API reads** (#101). Every read the
  `samtal-server config` commands can do is now a GET under `/api`:
  `/config` for the whole masked document with the location of every
  stored secret beside it, an identity-keyed listing per entity kind,
  one route per addressable entity, and `/default-agent`. A read
  answers with the entity's masked body and the slots holding a stored
  secret, each marked with the entity key its value displaces, which is
  the fact a masked read exists to convey and the one the entity itself
  can never carry. Reads are masked and stay masked: no secret value,
  and no plaintext that got into a row another way, appears in a
  response, a header or a log. The committed OpenAPI document
  (`docs/reference/api-openapi.json`) describes the routes, the
  refusals each can answer with, and the entity schemas a client needs
  to write an entity back.

### Changed

- **A secret-shaped key is refused anywhere inside a provider's
  options, not only at the top level** (#101). A provider entry passes
  its options through to the implementation, so an option can be a
  structure, and `connection: {api_key: ...}` was accepted, stored and
  read back verbatim by every display path. It is now refused when the
  fragment is parsed, with a message naming the dotted path and the
  rule rather than quoting the value. Being honest about the behavior
  change: a fragment that nested a secret-shaped key was never usable
  (nothing resolved it, and every read published it), and such a
  stored row now reports that it cannot be read as configuration
  rather than showing what it holds. The display path masks
  secret-shaped keys at every depth too, since it is the last thing
  between a row that got its contents another way and a caller.

- **A number that is not finite is refused in a fragment** (#101).
  YAML spells `.nan` and `.inf`; JSON does not, so such a value would
  be read back over the API as null, quietly turning the option into
  the provider's own default. Every fragment is checked at any depth
  when it is written, and a stored value that is not finite reports
  that the row cannot be read rather than answering with a value
  nobody wrote.

- **A stored row that cannot be read as configuration is reported as a
  server-side failure** (#101), which over the API means 500 rather
  than the 422 that says the caller sent something wrong. A row that
  fails the models is nothing a reader of it can fix, and it is the
  same situation as a column holding the wrong shape, which already
  said so. The message names the row and the fields that failed and
  never their values. Boot is unaffected: the storage refusal is a
  `ConfigError`, so a server meeting an unreadable row still refuses to
  start with the same sentence it printed before.

- **A name or a secret slot must be expressible as one URL path
  segment** (#101). An entity is addressed by putting its identity in
  a path, so a name or a slot holding a slash or a control character
  could never be fetched, replaced or deleted over the API. Both are
  now refused when they are written, naming the rule and the kind of
  character rather than the value; an MCP slot's key half must
  additionally be an environment variable name after `env.` or an HTTP
  header name after `headers.`, which is what the value would have
  referenced. Spaces, percent signs and characters outside ASCII stay
  legal and percent-encode losslessly. The rule runs at write time
  only: a row written before it still boots, still appears in
  `config show`, and is still deletable.

- **`samtal-server config` is a client of the configuration API**
  (#101). The grammar is unchanged and every message is unchanged: the
  API carries the repository's own sentence and the CLI prints it. What
  changed is that a command now needs a running server to talk to. It
  finds it at `--api-url`, then `SAMTAL_API_URL`, then
  `http://127.0.0.1:<server.port>/api`, and authenticates with the value
  of the variable `server.api.secret_env` names, which on a deployment
  is the variable the server itself was started with: exec into the
  running container and the CLI has the address and the token for free.

- **The API client refuses to send the token in clear** (#101). The
  bearer token crosses every request and grants everything the API can
  do, secret writes included, so a plain `http://` connection to a host
  that is not this machine is refused outright, with no flag to override
  it: use TLS, a tunnel that terminates it, or loopback from inside the
  container. A URL carrying a username or password is refused too, and
  no URL is printed with either still in it. A non-2xx response that is
  not this API's own is reported as a status code and never relayed,
  because what a proxy returns is not sanitized output.

- **Getting Started reorders to start, configure, restart** (#101).
  Configuring requires a running server, and an empty database is a
  valid state to run one on: it comes up serving no agents, is
  configured over its own API, and picks the configuration up at the
  restart, which is now a documented step of its own rather than a
  sentence at the end of another one.

- **The smoke lane's seeding scripts manage their own server** (#101).
  `seed.sh`, `seed-slim.sh` and `seed-local-engines.sh` each start a
  server inside the seeding container, wait for `/healthz`, write
  through it over loopback with the image's own CLI, and stop it again,
  printing the server's log if anything fails. The seeding containers
  are given the two secrets a server needs, and no provider credential,
  because an empty domain half builds no provider.
  `config.deploy.example.sh` is rewritten to run against a running
  server, and both sets of script tests move to the integration lane.

- **Every deployment must set `SAMTAL_API_SECRET` before upgrading**
  (#101). The configuration API is always mounted and always behind a
  bearer token, deliberately without an `enabled` flag, so a server
  started with that variable unset or blank refuses to boot. The
  refusal names the variable, prints
  `SAMTAL_API_SECRET=$(openssl rand -hex 32)`, and says that the token
  grants everything the API can do, so it belongs on a loopback
  connection or behind TLS. `server.api.secret_env` renames the
  variable for a deployment that wants another one.

- **`server.auth.secret_env` and `server.api.secret_env` must hold the
  name of an environment variable** (#101), the rule a provider's
  `*_env` keys already follow. Pasting a secret into either key is now
  refused when the configuration is read, with a message that says
  what the key must hold and shows an example rather than quoting what
  was written. This is a behavior change and worth being honest about:
  a pasted value never worked, because the name is looked up in the
  environment and no lookup of a pasted secret succeeds, so such a
  configuration was already failing, only later and while echoing the
  paste.

- The repository's refusals carry types (`UnknownEntityError`,
  `DatabaseBusyError`, `StorageError`, all subclassing `ConfigError`)
  so that the API can map a refusal to a status code without reading
  its message (#101). No message text changes anywhere, and the CLI
  prints exactly what it printed before.

- **The server boots its domain half from the database** (#86). The
  YAML file keeps `server:` and `memory:`; providers, MCP servers,
  agent defaults, agents, devices and the default agent now come from
  the database `samtal-server config` writes, read once at boot and
  composed onto the file half into the same validated shape the server
  has always used. Nothing about a conversation changes; where the
  configuration comes from does.

  **A domain section left in the YAML file, or a `SAMTAL_` override for
  one, refuses the boot** naming the key, the command that writes it
  now, and the reference. A configuration that silently stopped
  applying would be worse than one that will not start, and
  pydantic-settings ignores unmatched prefixed variables, so the
  environment is scanned explicitly for the six moved names.

  `config.example.yaml` shrinks to the server half, with its header
  pointing at the config commands, the generated reference and the
  example fragments; `config.deploy.example.yaml` keeps its profile's
  domain half as the commands that write it, values and reasoning
  intact. The container image sets
  `SAMTAL_SERVER__DATABASE__DIR=/data/db`, so the database lives on the
  data volume beside the model caches and the memory files, and the
  smoke lane seeds each image check's configuration through the CLI
  from the image itself before the container starts.

  Deployment notes for it are in `samtal-server/README.md`: generating
  and escrowing `SAMTAL_MASTER_KEY`, rotation adding a key without
  being able to retire one, WAL-safe backups (`VACUUM INTO` or
  `.backup`, or a plain copy only of a stopped and checkpointed
  database), what a restore needs, and what a copy of the file does and
  does not expose.

### Removed

- The staging notice on every mutating `samtal-server config` command,
  and the staging paragraphs in the generated reference and the
  examples README (#86). The window they existed for closed when the
  server started reading the database. Mutating commands now print that
  the write applies at the next server start, which is the trap a
  boot-time snapshot does have.

### Added

- **A REST API for the domain configuration, mounted at `/api`**
  (#101), as a skeleton: the namespace exists, is gated, and has no
  routes yet. Every request under it must carry
  `Authorization: Bearer <token>` whose value is the environment
  variable `server.api.secret_env` names, and one that does not is
  refused with 401 whether or not the path it asked for exists, since
  only an authenticated caller gets to learn which routes there are.
  Refusals from the repository keep their exact wording and gain a
  status code: 404 for a missing entity, 409 for the retryable busy
  lock, 500 for stored state that cannot be read, 422 for everything
  else. No request body and no traceback is ever quoted back.

  `samtal-server config openapi` prints the API's OpenAPI document,
  generated from its routes and committed at
  `docs/reference/api-openapi.json`, where CI regenerates and diffs it
  the way it already does the domain reference. Like `config schema`
  and `config reference` it is read-only: no configuration file, no
  database, no encryption key, no token.

  Nothing about a device conversation changes, and the device-facing
  endpoints are structurally absent from the API's document: the API
  is a second application mounted on the server's port, not a router
  on the one that serves devices.

- Generated documentation for the domain configuration (#86). Every
  domain model field now carries a description, and three renderings
  come from that one source: `samtal-server config schema [entity]`
  prints JSON Schema, `samtal-server config reference` prints the
  markdown reference (committed at `docs/reference/domain-config.md`),
  and the `config set` commands list their fragment's fields in
  `--help`. Both new commands are read-only: no database, no
  configuration file, no encryption key.

  The long-form narrative that documented these settings in
  `config.example.yaml` (the measured latencies, the field findings,
  the tuning advice) moves to richly commented per-entity fragments
  under `samtal-server/examples/`, one file per entity or provider
  type, each naming the `config set` command that installs it and each
  run through that command by the test suite. `config.example.yaml`
  itself is unchanged in this release and still carries its domain
  sections; the switchover is what removes them.

  CI regenerates the committed reference and fails on any difference,
  and the workflow now also runs on changes under `docs/reference/`, so
  the committed copy cannot drift from the models.

- The repository and the write path for the DB-backed domain
  configuration (#86): `samtal_server/config/store.py` reads the
  database into the existing pydantic models and writes fragments back
  through them, and `samtal-server config <command>` is the CLI in
  front of it. **The server does not read the database yet**: it still
  boots its whole configuration from the YAML file, so every mutating
  command prints a staging notice saying that this write takes effect
  at the switchover and changes nothing about the running server.

  The grammar covers one noun per entity kind: `set` for providers, MCP
  servers, agents and agent-defaults, `delete` for providers, MCP
  servers, agents and devices, `bind-device`, `set-default-agent` and
  `clear-default-agent`, `set-secret` and `clear-secret`, `list`, and
  `show` with every stored secret masked and every environment
  reference it shadows marked. A fragment is the same
  YAML shape the section has in the file today, read from a path or
  from stdin, and `set` replaces the entity without touching its stored
  secrets. A secret never passes through an argument: `set-secret`
  reads stdin, without echo at a terminal, or names the variable
  holding the value with `--from-env`.

  Every write validates the resulting snapshot's references inside one
  `BEGIN IMMEDIATE` transaction, so an agent naming an unknown
  provider, a device bound to an unknown agent, or deleting something
  still referenced is refused, and two concurrent writers cannot each
  validate against the state before the other's change. Completeness
  rules stay at boot, so a deployment can be built up from an empty
  database in the natural order without wedging.

  `server.database.dir` (`SAMTAL_SERVER__DATABASE__DIR`, default
  `/var/lib/samtal`) is where the database lives, read by the CLI
  through the same settings machinery the server reads its own
  configuration with. `verify_secrets` is implemented and tested but
  not yet called: the switchover is what puts it on the boot path.

- The storage foundation for the DB-backed domain configuration (#86):
  `samtal_server/db/` opens, configures and migrates a SQLite database
  holding the domain half of the configuration, and
  `samtal_server/config/secrets.py` defines the envelope its secrets
  are stored in. Nothing reads or writes it yet: no server behavior
  changes, and the YAML file is still the only configuration the
  server boots from.

  `open_database` creates the directory when it can, opens the file in
  WAL mode with a busy timeout, and runs the packaged Alembic
  migrations, so a fresh data volume becomes a current database with no
  init command to forget. Every transaction begins with `BEGIN
  IMMEDIATE`, which is what stops two processes racing the baseline
  migration and, later, two CLI writes interleaving their
  read-validate-write. Opening deliberately does not verify stored
  secrets: a missing or wrong key is exactly when the CLI has to stay
  usable as the recovery tool.

  A stored secret is either an environment reference, unchanged from
  today (`api_key_env`, `$VAR`), or Fernet ciphertext under
  `SAMTAL_MASTER_KEY`. The encrypted payload carries the secret
  together with its canonical location, and decryption refuses a token
  whose location is not the slot being read, so a valid token copied
  into another entity's row does not decrypt there. The key variable
  holds one or more keys, newest first: the newest encrypts, decryption
  tries them in order, and until a re-encrypt command exists every old
  key must stay configured while any token written under it is stored.

  CI now also builds the wheel, installs it into a scratch environment,
  and migrates a fresh database from the installed artifact alone, since
  a test run from the checkout cannot show that the migration scripts
  were packaged.

- A throwaway spike under `spikes/pipecat-alignment/` that measures
  whether pipecat could sit behind samtal's device edge, and answers
  it with numbers (#89). It runs a minimal pipecat 1.7.0 pipeline
  (Silero VAD, a canned reply, no LLM and no cloud) behind a xiaozhi
  frame serializer on pipecat's FastAPI websocket transport, driven by
  the unmodified xiaozhi-sdk device simulator, and runs the
  repository's existing `scripts/echo_leakage.py` analysis unmodified
  over the capture it produces. It is its own uv project so its
  dependencies never touch the server's, and nothing from it lands in
  `samtal_server/`.

  **Gate 1, capture alignment: passed**, at 250 ms and 1500 ms injected
  delay alike. A capture built on pipecat recovers a known echo's delay
  to 1.2 ms and its gain to 0.2 dB, in all 125 candidate windows per
  delay, with a lag IQR of zero and no measurable drift over two
  minutes. Three qualifications come with it. `AudioBufferProcessor`
  offers two bot tracks and only `on_bot_turn_audio_data` works: the
  delivered track, the one whose arrivals can be timestamped, is
  silently corrupted whenever the device streams microphone audio
  during a reply, because the processor pads the bot track up to the
  user track at every delivery, turning 126 s of reply into 150 s with
  13 s of silence inserted inside continuous speech. The track that
  works arrives once per turn, so it serves offline echo measurement
  and not a real-time AEC reference. And the processor has to record at
  the native output rate, because asking it for the capture rate puts
  its never-flushed streaming resampler in the path and truncates each
  turn's tail.

  **Gate 2, adapter size: not passed, and not failed.** The
  adoption-required adapter is 312 lines against 899 for the whole
  bespoke edge, but against the comparable slice of it that this
  exchange actually exercises it is 312 against 222, or 154 against 155
  counting code only: the same size, not smaller, while covering 8 of
  the seam's 23 obligations and speaking xiaozhi protocol v1 only. Its
  shape stayed message translation, which is the half of the answer
  that would have condemned it.

  Consequence: adopting pipecat for the streaming conversation runtime
  (#31) is a genuine tradeoff to be argued with these numbers rather
  than a question settled either way. Along the way the spike
  established that pipecat's websocket transport does pace output at
  real time, and that whether its recording is wire-aligned depends
  entirely on which recording API the adopter picks and at what rate.

  An external review of the spike's own pull request caught two
  measurement errors that had produced an earlier, wrong, "gate 1
  failed" conclusion: tap packets placed at the end rather than the
  start of their playout slot, and two different resamplers where the
  method required one. Both are recorded in the implementation doc
  along with the reversal.

## 2026-08-10

### Changed

- The device edge and the conversation runtime are now separate
  packages with an explicit interface pair between them (#85). What was
  one 2,138-line `session.py` owning both sides is
  `samtal_server/device/` (the xiaozhi handshake, Opus codecs, framing,
  frame pacing, capture, the device MCP transport, session limits and
  the idle watchdog) and `samtal_server/runtime/` (endpointing, the
  barge-in gate ladder, the filler, ASR, the LLM tool loop, sentence
  splitting, speech synthesis and its lookahead, history and agent
  handover). They meet at `device/boundary.py`: a `SessionInput` the
  edge feeds and a `DeviceOutput` the runtime drives, both described in
  device terms, with reply audio crossing as an opaque batch so the
  runtime never learns Opus. The bespoke pipeline is built for each
  connection by a factory assembled at startup, which is the seam a
  second runtime plugs into.

  A pure refactor, with one deliberate exception stated in the
  implementation doc: no event name, field, reason or ordering changes,
  no wire bytes change, and the `logger` field stays
  `samtal_server.session` on every conversation record, which a
  characterization test now pins. The whole integration lane passes
  with a single import line changed.

### Fixed

- A device that disconnects while one of its own MCP tools is being
  called now reaches the conversation runtime as the boundary's
  `DeviceGone` rather than as the websocket transport's exception,
  which is what every other outgoing message already did (#85). The
  runtime's observable behavior is unchanged: `DeviceGone` subclasses
  `RuntimeError`, so the tool loop turns it into the same error result
  and the same `tool_call` event as before.

- The principles page now separates product promises (falsifiable
  commitments to the person running samtal) from the architecture
  principles that keep them, with an explicit precedence rule: when
  the two conflict, the promise wins. Thin device smart server moved
  to the promises, and the declared-egress principle folded into the
  local-first promise as its enforcement mechanism.

### Added

- An ADR recording the device-facing boundary decision
  (`docs/adr/2026-08-10-normalize-the-hardware-edge.md`): interfaces at
  samtal's core describe device capabilities, the xiaozhi edge is
  normalized once, runtimes stay themselves behind it, there is no
  universal `ConversationBackend`, and the decision sites keep their
  reasoned events. The three architecture principles it supports now
  cite it.
- The implementation doc for that extraction
  (`docs/plans/2026-08-10-device-facing-session-boundary-implementation.md`).
- The accepted plan for extracting a device-facing session boundary
  (`docs/plans/2026-08-10-device-facing-session-boundary.md`): the
  interface pair, ownership map, and ten-commit sequence for splitting
  `session.py` into a device edge and a pipeline runtime with no
  behavior change. Reviewed adversarially in three rounds before
  acceptance; the review settled the send-path batch surface, event
  attribution, the pinned logger identity, and the commit ordering.
- Two product promises in `docs/architecture/principles.md`: stock
  xiaozhi firmware is the compatibility floor (a stock board holds a
  conversation without a reflash, bounded to the WebSocket transport,
  versioned by the firmware in the field, and a floor rather than a
  ceiling), and a fully local deployment is first-class (every core
  capability reachable with local providers, enforced by declared
  egress and `local_only`).

- The standing architecture principles as
  `docs/architecture/principles.md`: samtal's identity (own the
  appliance, let conversation runtimes stay themselves), the
  device-facing boundary with its phone-call litmus test, the
  runtimes-are-siblings rule with the universal-abstraction
  anti-goal, thin device smart server, reason-annotated decision
  sites, and the declared-egress guarantee, each principle with an
  example and a counterexample. Pointers added from `AGENTS.md`, the
  docs index, and the ADR README, whose division of labor gains a
  fourth clause: issues hold evidence, ADRs hold decisions, plans
  hold execution, principles hold direction.

## 2026-08-09

### Fixed

- The conversational filler talking over the user. The filler timer
  checked only whether reply audio had started before playing its
  clip, never whether the user was speaking, so when the endpointer
  ended a turn at a mid-sentence thinking pause (common in
  dictation-style turns: "guarda en la memoria que...") the clip
  fired into the user's continuation. Field round 2 measured 4 of 20
  fires landing 1.4 to 1.8 s into speech already underway, one of
  them 52 ms before the barge-in it was firing into cancelled the
  reply it masked. The timer now stands down at fire time, with a
  `filler_skipped` event, when the endpointer holds unresolved speech
  or a barge-in confirmation has the outgoing frames paused
  (`docs/features/2026-08-09-filler-yields-to-live-speech.md`).

### Added

- Five turn-taking entries in the glossary (continuation, gate
  ladder, premature endpoint, structured event, and a sharpened
  refractory period), extracted from the field analysis of the
  latency-masking deployment so the vocabulary the analysis leans on
  is addressable from issues and PRs.
- Three end-of-turn entries in the glossary (end-of-turn detection,
  prosodic cues, semantic completeness) with worked examples, and
  examples added to the continuation, gate ladder, endpointer, and
  trailing silence entries, so the turn-taking discussion in the
  issues reads without a linguistics background.
- The echo leakage measurement as committed tooling
  (`scripts/echo_leakage.py` and `scripts/echo_leakage_control.py`),
  so the analysis that settled #48's headline question can be re-run
  on any future captures instead of living in a session's scratch
  space. The measurement cross-correlates a capture's microphone
  channel against its paced-reply channel over windows where the
  assistant plays and the user is silent, reporting the leakage and
  path delay per session and per voice, and the detectability bound
  where nothing is found; the control injects a known synthetic echo
  and refuses a passing verdict unless the measurement recovers it,
  which is the condition for trusting any null result. Both verified
  to reproduce round 1's figures from the original captures.

## 2026-08-08

### Fixed

- Every cloud ASR call failing at connect time on the build carrying
  the echo retry (#75). The retry work passed `Omit()` as the
  per-request timeout on the ordinary path, matching the idiom of the
  neighbouring form fields; but timeout is a client option, `Omit` is
  not a `NotGiven` to the SDK, and the sentinel instance reached httpx
  as the literal connect timeout, failing every transcription in
  milliseconds. The fix is the SDK's `NOT_GIVEN` sentinel, plus a
  regression test pinning that every timeout value reaching the
  transport layer is a real number, which is the class of check a mock
  transport can actually make (it never connects, which is how the
  suite and a green deploy verification both missed the bug).

### Added

- Latency masking with pre-synthesized conversational fillers (#74).
  When a reply's first audio has not started within a configured
  delay of the utterance being transcribed (default 1800 ms, above
  the roughly 1.2 s a healthy reply takes to its first audio and
  below the 2 to 3 s of dead air field round 1 measured on ordinary
  turns), the session plays a short filled pause ("Hmm, let me
  see...") in the active agent's own voice, and the real reply queues
  behind the clip's tail. The clips are synthesized once at boot and
  cached as PCM, so the mask costs nothing at the moment it masks and
  keeps working when the TTS provider is the thing being slow; a
  synthesis failure logs a warning and leaves the feature off for
  that agent rather than failing the boot. The filler is honest
  assistant speech: it moves the device into its speaking state,
  counts as the turn's `speaking_started`, lands on capture channel
  1, and enters the barge-in gates like any reply audio. One filler
  per turn, logged as a `filler_played` event with the measured delay
  and the phrase index, and composed with the first-token watchdog:
  the filler is the soft early threshold, the watchdog the hard late
  one. Off by default, configured per agent (or in `agent_defaults`)
  as `filler: {enabled, delay_ms, phrases}` with the phrases written
  in each agent's own language.

- Two reference pages in `docs/`, distilled from the first field-test
  rounds (#48, #73). The
  [regression suite for conversational quality](docs/conversational-quality-regression-suite.md)
  explains why field tests exist, the shape of a conversation turn
  with the event each step emits, the preflight and artifacts of a
  test round, and the three layers findings age in: the instrument
  (stack-independent), the interaction layer (survives provider
  swaps), and the calibration (the stack-specific constants worth
  optimizing once a deployment settles). The
  [glossary](docs/glossary.md) gives one short definition per
  concept, technique, and technology the project is built on, with
  pointers for going deeper. Both are indexed from
  [docs/README.md](docs/README.md).

- A first-token watchdog on the LLM round (#68). Nothing used to bound
  the gap between sending a chat request and the first byte of the
  answer, so a provider that stalled there froze the session in
  `replying`, not listening, for as long as it cared to take: a field
  test saw a 17 s stall that only a barge-in rescued. The watchdog
  cancels a round whose first token has not arrived within
  `server.llm_first_token_timeout_s` (default 10 s, chosen against the
  field data: healthy first tokens cluster at 500 to 800 ms, the worst
  spike that still answered was 8.9 s) and retries it once, which the
  same field data says answers quickly; a second stall gives the round
  up as a `provider_failed` event with `error: FirstTokenTimeout` and
  the session returns to listening, so the failure mode is a silent
  turn rather than a wedged session. Only the wait for the stream to
  begin is bounded: a long generation that is already streaming is
  healthy and runs to the end, the provider adapters announce their
  first chunk off the wire so a round that streams nothing but a
  buffered tool call (a handover does) is not mistaken for a stall,
  and barge-in keeps working through the whole watchdog window exactly
  as before. The retry is its own structured event, `llm_retry`,
  documented in the server README's event table.

### Fixed

- An ASR transcript that comes back as the configured prompt is no
  longer taken as proof of silence (#69). The #54 guard discarded the
  echo outright, and the #48 field test showed it swallowing real
  speech: nine echoes in two days, every one on a clip of 0.78 to
  1.92 s, two of them a user answering "yes, please" to an offer and
  being ignored. The guard itself stands, an exact echo of the prompt
  is still never handed to the LLM as if spoken, but the clip is now
  transcribed once more with the prompt withheld: a real short answer
  transcribes fine without the prompt's help and is heard normally,
  while a retry that comes back empty or as the prompt again is
  discarded as before. Only the tripped guard pays for the second
  round trip; the normal path is one request per utterance, exactly as
  it was. The retry lives on what the first request left of the
  provider's `timeout_s` rather than getting a fresh timeout of its
  own, which would have quietly doubled the bounded wait that
  disabling client retries exists to keep: with under a second of
  budget left no retry is sent at all, and a retry the deadline cuts
  off discards the clip rather than surfacing an error. Each trip
  logs one `asr_prompt_echo` event (in the server README's event
  table) whose `outcome` tells `recovered` apart from
  `confirmed_empty`, `confirmed_echo`, `timed_out` and `skipped`, and
  whose `retry_ms` carries what the retry cost, so future field data
  can measure how often the guard was swallowing real speech. The old
  warning line ("treating
  N s of audio as nothing said") now appears only when the retry
  confirms there was nothing, so a log search for it keeps meaning
  what it meant.

## 2026-08-07

### Added

- The four stage acronyms are expanded where the server README first
  uses them. It opened by describing the pipeline as VAD, ASR, LLM and
  TTS without ever saying what any of them stand for, though those four
  names are the vocabulary of the whole configuration file. A table
  gives each in full with what it decides, and notes that they run
  strictly in order, which is why any one of them is latency the user
  hears. Separately, two unrelated options are both called `prompt`: a
  list of words the transcriber should expect, and the persona
  instruction sent to the LLM. The ASR one is a hint about vocabulary
  rather than a request for behaviour, which is exactly what makes an
  echoed prompt becoming an actionable utterance surprising, so both
  the README and `config.example.yaml` now tell them apart where
  somebody editing the value will be looking.

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
- Architecture diagrams, and a walkthrough that teaches them.
  `docs/architecture/excalidraw/` holds a high-level overview, now
  embedded in the root README, and a detailed picture of one
  conversation turn across four lanes (human, device, server, external
  services); the editable originals are scenes in the team Excalidraw
  workspace and the committed files are their exports.
  `docs/architecture/README.md` embeds both and walks the detailed one
  step by numbered step, explaining each concept and the problem it
  solves and expanding every acronym (AEC, VAD, ASR, LLM, TTS, MCP)
  before using it, then closes by naming the two things a happy-path
  picture cannot show, interrupting a reply and which stages leave the
  host, pointing at the diagram for each.
- Three PlantUML diagrams under `docs/architecture/plantuml/`, whose
  source is text in the repository rather than an export of a hosted
  scene, so a pipeline change and the picture of it move in the same
  commit and a reviewer reads the diff. `architecture-overview` colours
  every provider by its `egress` class marking, which is the thing
  `server.local_only` is checked against at boot, so the picture
  answers "what leaves this host" from the same source the enforcement
  reads. `conversation-turn` is a sequence diagram, chosen because what
  matters in a turn is ordering and overlap: the tool loop's rounds and
  its final `tool_choice: none`, the next sentence being synthesized
  while the current one plays, and which listening mode re-arms the
  microphone. `barge-in-decision` is an activity diagram of the gates
  an utterance passes before it may cancel a reply, which is branches
  rather than flow and so could not be grafted onto the conversation
  diagram's happy path; it names both thresholds and the structured
  event every branch emits, those being what the thresholds get tuned
  from. Rendering is a local `plantuml` invocation documented beside
  them; the Excalidraw pair stays for the README's front door.
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
