# Device onboarding implementation

Companion to [`2026-08-12-device-onboarding.md`](2026-08-12-device-onboarding.md).
One section per milestone, recording what was actually built, the
deviations from the plan, the resolutions of what it left to the
implementer, and the discoveries a later milestone would otherwise have
to make again.

## Milestone 1: the short path and the banner

The alias route and the URL an operator reads off the server: the
configuration keys, the key derivation, the `/x/<key>/` router with its
404-and-hint miss, the startup banner and the same line on the OTA GET.
No activation object, no pending table, no `/activate`, no API route and
no CLI command: an unbound device receives exactly what it received
before, and the legacy path answers exactly what it answered before.

### What landed

**The configuration keys (`config/models.py`).** `OnboardingConfig`
carries `enabled` (true) and an optional `key` validated to eight base32
characters, upper-cased and stripped, refused without quoting the value.
`ServerConfig` gains `public_url`, an http or https origin with an
optional path prefix whose trailing slash is normalized away and which
refuses userinfo, a query and a fragment, also without quoting;
`ota_path` becomes `str | None`, its validator reserving
`ONBOARDING_MOUNT_PATH` (`/x`) beside `API_MOUNT_PATH` and passing null
through; and a model validator refuses a null `ota_path` with onboarding
disabled, naming both ways out. `ONBOARDING_MOUNT_PATH` lives on the
models for the reason `API_MOUNT_PATH` does: the `ota_path` validator is
what has to reserve it.

**Key derivation (`onboarding.py`).** `derive_key(secret)` is
`base32(HMAC-SHA256(secret, b"samtal-onboarding-key-v1"))[:8]`,
uppercase canonical. `onboarding_key(config)` resolves what this server
serves under: a pinned key when there is one, else the derivation over
the variable `server.auth.secret_env` names, else `None` (auth off, no
secret, keyless route). The secret is read from the environment here
rather than taken from `DeviceAuth`, so no object grows a property that
hands the secret out; an enabled auth with no secret has already refused
the boot by the time this runs.

**The short router (`onboarding.py`, `app.py`).** `build_router(key)`
registers `/x/{key}/` GET and POST bound to `ota.describe` and
`ota.check_version` through a guard, or the literal `/x/` when there is
no key. The guard compares the attempted segment case-insensitively with
`hmac.compare_digest` over the encoded pair, logs the mismatch at
warning level with the attempted key beside the correct one (event
`onboarding_key_mismatch`), and raises `HTTPException(404)`. `app.py`
mounts the legacy router only when `ota_path` is not null and the short
router only when onboarding is enabled.

**The banner and the describe line (`onboarding.py`, `main.py`,
`ota.py`).** `public_origin(config)` returns an `Origin` carrying the
URL, the source it came from, whether it is a guess and the note that
goes with a guess; `Origin.provenance` renders the parenthetical the
banner and the describe line share. `log_banner(config)` is called from
`main()` after `logs.configure` and after the app is built, and prints
either the short URL or, with onboarding off, the origin plus the fact
that configuration is served at `server.ota_path`, which it does not
print. `ota.describe` appends `portal_url_line(config, request.url.path)`
to its body.

**Documentation.** `config.example.yaml` gains commented `public_url`
and a real `onboarding` block (mirroring how `auth` and `api` are
shipped), and its `ota_path` comment now says null unmounts the route.
`config.deploy.example.yaml` sets `public_url` (TLS ends at the proxy,
so nothing a request carries says what a person should type), ships the
`onboarding` block, and advises injecting a pinned key through
`SAMTAL_SERVER__ONBOARDING__KEY`, the posture it already holds for the
OTA path segment. `CHANGELOG.md` records the addition under 2026-08-12.

**Tests.** `tests/unit/test_onboarding_config.py` (the pinned key
accepted and normalized, six wrong shapes refused, the sentinel in no
message, no chain and no log record; `public_url` accepted and
normalized in five forms and refused in five more, with userinfo, a
query and a fragment refused without the sentinel anywhere; the null
`ota_path`; the null-plus-disabled boot refusal; the `/x/` reservation
with the configured segment not quoted back).
`tests/unit/test_onboarding.py` (the derivation vector, the alphabet and
length, stability and secret-dependence, the configured variable, the
pinned override, the keyless mount with auth off, the four mounted
states, the case-insensitive match, the short and legacy bodies equal,
the 404 byte-identical to an unserved path, the hint logged with nothing
in either response, and the trailing-slash 307 preserving the POST body
on both paths). `tests/unit/test_onboarding_banner.py` (the three
sources, the precedence between them, the guess reading as a guess, the
keyless URL, the `ota_path` never quoted, the sentinel-userinfo
websocket URL reaching neither the banner nor the describe line, and the
describe line naming the path it was reached on).

### Deviations from the plan

Two, both narrow, neither changing what the milestone delivers.

**"Byte-identical body" holds strictly for the 404 and modulo the clock
for the OTA reply.** The plan's M1 coverage asks for "the byte-identical
body between short and legacy paths". Two requests to one server cannot
have byte-identical OTA replies: `server_time.timestamp` is the
millisecond the reply was built. The test therefore asserts the two
bodies are equal once that one field is removed, and that both carry it.
The mismatch 404 is byte-identical in the literal sense, and by
construction rather than by comparison: the guard raises
`HTTPException(404)` instead of composing a response, so FastAPI's own
handler renders both it and an unserved path, and the two cannot drift
apart later.

**A pinned `onboarding.key` is honoured with device auth off.** The plan
says auth off means the route mounts keyless at `/x/`, which is about
the derivation having no secret to run on. A pinned key replaces the
derivation rather than depending on it, so it is honoured either way and
the keyless mount is the case where there is no key at all. Refusing a
pinned key without auth would be a boot refusal the plan does not ask
for, and silently ignoring one would serve a route the operator did not
configure.

### Resolutions of what the plan left open

**The describe line uses the resolved origin and the request's own
path.** The plan says the GET handler "prints the same URL line for the
path it was reached on". It prints the origin `public_origin` resolves
(so the line agrees with the banner about where this server is) joined
to `request.url.path` (so it is the URL that just worked, whether that
is the short path or the legacy one).

**The guess names the wildcard address specifically.** "A guess must
look like a guess" is implemented as the word "guessed" plus the source,
plus, when `server.host` is a wildcard, a clause saying that this is
where the server listens rather than a name a device can reach, and
where to write the real one. A non-wildcard guess still reads as a guess
and points at `server.public_url`.

**Userinfo in `public_url` is refused, not stripped.** Stripping it
would print a URL that works while hiding that a password was written
into a configuration file. The refusal names the problem and does not
repeat the value.

**IPv6 hosts get their brackets back.** `urlsplit(...).hostname` strips
them, so an origin built from a websocket URL or from a listen address
puts them back; otherwise the port would land after a bare `::1`.

### Discoveries

**`ota.py` imports the onboarding module inside `describe`.** The
onboarding router serves the OTA handlers, so `onboarding` imports
`ota` at module scope; a module-scope import in the other direction
fails, because the pair loads onboarding first and the name it wants is
defined after that import line. The import sits in the function body
with a comment saying so. Whichever milestone moves shared rendering out
of `ota.py` can remove it.

**The describe body still echoes `websocket_url` verbatim.** Its
existing second line is "Devices are sent to `<websocket_url>`", which
carries userinfo if an operator wrote any, and that is pre-existing
behavior: it is the URL the device is handed, not a derived one. The
no-leak assertions are therefore scoped to the new portal line and the
banner, which build their origin from the parsed hostname and port. If
that echo should change, it is a change to what a device-facing endpoint
reports and belongs in its own decision.

**Starlette's redirect really is a 307 on both paths.** Asserted with
redirects disabled (the status and the `Location`) and then followed,
checking the reply carries the firmware version the POST body named: a
302 or a 303 would have turned the device's POST into a GET and answered
with the unknown-version reply instead.

**`log_banner` is called from `main()`, not from `create_app`.** An app
built by a test lane or by an external ASGI server has no operator
reading its startup output, and `create_app` runs in both.

### Notes for the milestones that follow

- `onboarding.onboarding_key(config)` is the one place the key is
  resolved; the M4 `ota-url` command reads the same function over
  `load_file_config`, which is what makes a drift test unnecessary.
- The short router's handlers are `ota.check_version` and `ota.describe`
  by reference. M3's `/activate` registers on both routers the same way.
- The guard wrapper (`_guarded`) is where any further short-path route
  gets its key check; it takes a handler and returns one.
- `app.py` does not keep the key on `app.state`. M3 needs the pending
  table there, and can put the key beside it if a handler ever needs it.

### Verification

`uv run ruff check .`, `uv run pytest tests/unit -q` and
`uv run pytest tests/integration -q` from `samtal-server/`, all green.
The hardware items are not verifiable from code and are not claimed: the
milestone's merge gate, the portal-retype check on a real board, and
everything in milestone 5 remain open.

### PR #115 review round

One external review of the pull request's diff: codex CLI, model
gpt-5.6-sol, read-only, 2026-08-12. Six findings; the five code ones
are fixed with a commit each, and the sixth is procedural. Four of the
five are the same shape, and it is worth saying once: this milestone
gave two configuration values a new reader (a log line and a page a
person is handed) and gave one URL segment a new writer (whoever types
it), and every finding below is a place where the old handling of that
value was fine for its old reader and not for the new one.

1. **P1: the short endpoint served WebSocket credentials.** `describe`
   renders `websocket_url_for(...)` verbatim, and this milestone put
   that handler on `/x/<key>/`, so a `wss://admin:secret@host/` was
   read back by anyone holding the onboarding URL; the banner tests
   scoped around exactly that line, which is how the leak passed a
   suite about not leaking.
   *Resolution*, dc64bfd: `server.websocket_url` refuses userinfo at
   load, without quoting the value, the posture `public_url` already
   held. Refused rather than stripped, because a configuration carrying
   a password is a mistake to name rather than to quietly repair. The
   sentinel is asserted absent from the refusal message, the exception
   chain, the log records and the CLI's stderr; the response-level
   assertion is the boot refusal itself, since the only configuration
   that could put the sentinel in a response is one that no longer
   loads. The banner tests keep their coverage of the derivation as a
   second line of defence, building the credentialed configuration by
   hand with `model_construct` because no file can produce one. The
   behavior change is in the changelog: it is a refusal for a
   configuration that was already leaking, and no other websocket URL
   is affected.
2. **P1: a malformed port crashed the boot with a library traceback.**
   The validator checked the scheme alone while the banner reads
   `parts.port`, which raises for `wss://voice.example:hunter2/`, and
   the banner ran outside `main.py`'s handled boot block, so the
   ValueError reached stderr as a traceback, quoting the port the
   refusals are careful not to.
   *Resolution*, 0ec6a5d, in the three places the reviewer named,
   because any one alone leaves a way in. A shared `url_problem` helper
   parses once and names the scheme, a missing host, userinfo and an
   unreadable port without the value; `public_origin` and `_origin_of`
   are total, falling through to the listen address and saying that the
   better source could not be read, so a configuration built in code
   cannot crash a startup either; and the banner call moved inside the
   `try` block, where anything it can still raise is one printed
   sentence. Malformed-port, out-of-range-port and malformed-IPv6 cases
   with sentinel no-leak assertions, at the config boundary and again
   at the banner.
3. **P1: the missing-slash redirect bypassed the key guard.** Only the
   trailing-slash route was registered, so Starlette's own
   `redirect_slashes` answered `/x/WRONGKEY` with a 307 whose
   `Location` echoed the attempted key, before any handler ran: a wrong
   key was distinguishable from a path that was never served, which is
   the one thing the miss branch must not be.
   *Resolution*, 0b7faff: the router registers the slashless keyed
   routes itself, behind the same guard. The correct key gets the 307
   Starlette would have issued, query string included; a wrong one gets
   the same 404 body and headers as any unserved path, with no
   `Location`. Asserted for GET and POST, on the body and the headers.
4. **P1: a malformed attempted key was injected verbatim into logs.**
   The decoded path parameter went into the message and the structured
   fields, so `/x/AAAA%0ABBB/` forged a second log entry (the decoded
   parameter really does arrive holding a raw newline, checked by hand)
   and an oversized segment let a caller choose how long an entry was.
   *Resolution*, 2a663fd, with the rule stated exactly rather than
   approximately, in the module docstring and in the tests: after case
   folding, one to ten characters of the base32 alphabet are repeated
   back, which covers the mistyped and the over-typed key the line
   exists to diagnose. Ten is `KEY_LENGTH + 2`. The folded form is both
   what is compared and what is logged, which is what makes the shape
   check a guarantee about the output rather than about the input,
   since upper-casing some Unicode characters yields ASCII letters.
   Anything else is counted under its own event with no raw value, and
   that line also leaves out the correct key, so probing cannot turn
   the log into a broadcast of it. Control-character, escape-sequence
   and oversized cases assert both shipped formats, the human one and
   the JSON one a container writes.
5. **P2: `public_url` accepted a non-numeric port and republished
   it.** The validator read the hostname but never the port, so
   `https://voice.example:hunter2` was accepted and printed by the
   banner and the describe line.
   *Resolution*, cf29735: it goes through the same shared check, which
   refuses an unreadable host and a port that is not a whole number in
   range, without quoting either. The query and fragment refusal stays
   its own, because that rule is about this key being an origin rather
   than about a value being readable.
6. **P3: the hardware merge gate.** Deferred, as it must be: the
   portal-retype check on a real board is milestone 1's recorded merge
   gate and cannot be answered from code. The driving session carries
   it.

Full lanes after the fixes: ruff clean, both suites green (counts in
the PR's verification section).

### Hardware checkpoint round, 2026-08-13

One finding, from the first real onboarding of a factory Waveshare
AMOLED board (firmware 2.2.4) over the short path. It is the one thing
in this milestone that no test lane could have produced, and it
invalidates a claim the plan and the milestone both made.

**The device does not follow the trailing-slash redirect.** The
operator typed the onboarding URL into the captive portal, the portal
saved it without its trailing slash, and the board POSTed to
`/x/<key>`. The router answered the guarded 307, which is correct HTTP
and preserves the method and the body, and the firmware treated it as
an error: the screen showed `code=307` and the board went into a
restart loop. Server-side there was nothing at all to see, for two
compounding reasons: the redirect is answered by the router before any
handler runs, so no `ota_check` line was ever emitted, and the access
log is off by design, so the request itself left no trace. An operator
watching the logs sees a device that never arrived.

*Resolution*, b7eacaf: both spellings of the short path, with and
without the trailing slash, are registered on the same handlers behind
the same key guard, so nothing device-facing on this route spends a
round trip on a redirect. The keyless mount (auth off) gets the same
treatment, because a trial network's portal behaves like every other
portal. A wrong key still answers the stock 404 with no `Location` on
either spelling, which is the review round's finding 3 and stays
asserted. The tests that asserted the 307 for the correct key now
assert direct dispatch with redirects disabled, which is what the
device does, and compare the slashless reply with the slashed one under
the same `server_time` exclusion the milestone established.

Three consequences worth carrying forward.

- **The trailing slash is no longer load-bearing for a typed URL.**
  The docs and the banner can keep printing the canonical form with the
  slash, because it is what a person should type, but nothing depends
  on the portal preserving it any more.
- **The plan's trailing-slash paragraph is superseded.** It says a
  missing slash "must still reach the handler: Starlette's
  `redirect_slashes` answers 307, which preserves method and body", and
  asks for that to be asserted rather than assumed. It was asserted,
  and the assertion was true and useless: what matters is not whether
  the redirect is correct but whether the device follows it, and this
  one does not. The rule this leaves behind is broader than one route:
  a device-facing endpoint gets no redirects, because the firmware is
  not a browser and its HTTP client is whatever upstream compiled.
- **The legacy `ota_path` router is deliberately untouched here.** On
  this branch it still registers only the slashed spelling, so
  Starlette answers the other with the same unfollowed 307, and the
  same failure is available to anyone whose portal strips the slash off
  a legacy URL. It is not what this milestone added, the change to it
  belongs where it is already being made, and duplicating it here would
  only be a conflict for the branch that carries it. The unit lane
  records the current behavior as characterization, named as such, so
  the state is visible rather than implied.

#### What milestone 3 carries of it

Written while rebasing this milestone onto the fix above, which is
where the rule met the routes this milestone adds.

**`/activate` has the same two spellings, and now answers both.** The
finding is the endpoint's, but the consequence is not only the
endpoint's: the firmware builds the activation URL by appending
`activate` to whatever it holds (`Ota::Activate` adds the separating
slash when the stored URL lacks one), so a board whose portal dropped
the trailing slash polls the slashless spelling of this route too. Both
spellings are registered on both routers, behind the key guard on the
short one, and the test that asserted a guarded 307 for the correct key
here now asserts direct dispatch with redirects disabled. That test was
written for the review round's finding 2, whose concern was the
`Location` header carrying a secret; serving both spellings satisfies
that concern and this one at once, since a route that never redirects
has no `Location` to put anything in.

**The third bullet above is superseded at this milestone's tip.** The
legacy `ota_path` router is no longer untouched: this milestone's own
review round (finding 2, commit d797a8e) had already given it both
spellings of both routes, for the narrower reason that the `Location`
it would otherwise emit carries the deployment's secret path. The
characterization test recording the legacy redirect is therefore
dropped rather than carried, and the rule now has one implementation
for both routers, `ota.spellings`, which is where the hardware finding
is recorded in the code.

**No device-facing route on this branch emits a redirect.** Short or
legacy, base or activate, either spelling: every one dispatches, and
every test of them asserts the reply with redirects disabled, which is
what the device does. The one path that still redirects is the
configuration API's bare `/api` prefix, which is a browser and a CLI
surface and no device ever reaches.

#### What milestone 4 carries of it

Written while rebasing that milestone onto the fix above. It changes
nothing it does and one thing it says.

**`config doctor` still follows one redirect, and now for somebody
else's server.** Its review round (finding 6) had narrowed the probe
from "follow anything, twenty deep" to "follow the canonical
trailing-slash redirect on the same origin, once", on the reasoning
that this was the one redirect a deployment produces. After the
checkpoint no current deployment produces it at all: every
device-facing route answers both spellings itself. The behavior is
kept, because what it now covers is a server older than this change,
or a proxy in front of one that canonicalizes a missing slash, and
reporting either as "not samtal-server" would be this command's worst
answer. What changed is the wording that justified it, in
`CANONICAL_REDIRECTS`, in `_probed` and in `_canonical_slash`: a
comment claiming a current server sends this would send a later reader
looking for a route that no longer exists.

**The tests that stood on that claim now say whose server they mean.**
Two of them build a canned application registering only the slashed
route, which is exactly what a pre-checkpoint server was, and their
names and docstrings say so rather than saying "the server's own
redirect". Beside them is a new one asserting the other half against
the real routers: a current server answers both `/x/<key>` and
`/x/<key>/` with 200 and no redirect (checked with redirects
disabled), and `doctor` probing the slashless spelling reports it
healthy without a second request existing to make.


## Milestone 2: live device bindings

The one hole in the boot-time snapshot, opened exactly wide enough for
the onboarding ceremony to work: the `devices` rows and `default_agent`,
read by the running server at the two moments a device asks. No
activation object, no pending table, no `/activate`, no new API route
and no new CLI command.

### What landed

**`DeviceBindings` (`device/bindings.py`).** Built by `create_app` from
the composed snapshot and a read engine of its own, disposed in the
lifespan. `agents_for(mac)` reads the device's row and `default_agent`
in one transaction, resolves by the rule `Config.agents_for_device`
applies (the bound list, else the default agent, else nothing), and
returns a `DeviceAgents` carrying two tuples: the names the boot
snapshot loaded, and the names it did not. Two fields rather than one
list, because the two states they separate need different sentences said
to the operator. `resolve(mac)` is `agents_for` awaited through
`asyncio.to_thread`, and is what both async call sites use. A read that
fails logs `device_bindings_unreadable` at warning level, naming the
driver's own message, and answers from the snapshot.

**The read engine (`db.read_engine`).** A second engine factory beside
`open_database`, which does three things a device path must not: it
migrates, it begins every transaction `BEGIN IMMEDIATE`, and it creates
the directory. The read engine does none of them. It sets
`busy_timeout` and leaves `journal_mode` alone (the pragma is a property
of the file, and setting it would be a write from the read path), hands
transaction control to SQLAlchemy, and emits a plain `BEGIN`, spelled
out rather than left to the default because the default for this project
is the immediate one.

**The consumers (`ota.py`, `device/session.py`, `ws.py`).**
`check_version` and the session's connect check both await
`bindings.resolve(mac)`; the session drops the loaded-agent filter it
applied by hand, since the view applies the same one. Each edge logs the
bound-but-unloaded state distinctly ("bound to agent X, which this
server has not loaded; restart to load it") rather than the generic bind
advice, and the `ota_check` record carries the unloaded names as a
field. `DeviceSession` takes the view as an optional constructor
argument.

**The notice split (`config/writes.py`, `config/api.py`,
`config/cli.py`).** `BINDING_NOTICE` beside `RESTART_NOTICE`, and
`binding_notice(unloaded)` choosing between them. Device writes and
default-agent writes carry the binding notice when every agent they name
is loaded and the restart sentence when one is not; both deletes carry
the binding notice unconditionally, having no agent to load. `build_api`
takes the loaded agent names, `_acknowledge` takes a notice with the
restart sentence as its default, and `docs/reference/api-openapi.json`
was regenerated with
`uv run samtal-server config openapi > ../docs/reference/api-openapi.json`.

**The contract sentences.** `config/boot.py` (where the contract lives),
the API description, the `Acknowledgement.notice` documentation, the
`--local` banner, and the four places in `samtal-server/README.md` that
stated the always-restart rule.

**Tests.** `tests/unit/test_device_bindings.py` (a bind seen by the next
check-in and the next connection on one app with no rebuild; the default
agent equally live; a delete stopping the next token while a
conversation in flight finishes; the unloaded-agent filter with its
distinct line at both edges and a partial binding still answering; the
unreadable database logging and resolving from the snapshot, including
that the fallback is the snapshot and not an empty answer; a missing
database as a quiet state; the read path leaving an unmigrated file
unmigrated; and the contention case).
`tests/unit/test_config_api_writes.py` and `tests/unit/test_config_cli.py`
gained the notice cases at the API and through the CLI, `--local`
included. `tests/integration/test_device_bindings.py` binds an unbound
board over the served `/api` and watches the same process hand it a
token and then a conversation.

### Deviations from the plan

Three, all narrow.

**The unloaded case reuses `RESTART_NOTICE` verbatim** rather than
getting a sentence of its own. The plan says the acknowledgement
"carries the restart sentence" in that case, and it is accurate as
written (the agent set is what is read once at boot); a third sentence
would have been a third thing to keep true. What names the specific
problem is the log line at the OTA and session edges, which is where an
operator is when the device does not connect.

**`DeviceBindings` also has a snapshot-only mode.** The plan describes
the component with a read engine. A configuration composed in memory has
no database to read, which is what most of the unit lane is and what an
embedding of this server would be, and the honest view of it is the
snapshot itself. `open` takes that branch when the database file is
absent, saying so once at debug level rather than warning at every
lookup; `snapshot_only` is the same object, and is what a `DeviceSession`
constructed without a view uses, so resolution has one implementation
rather than a live one and a fallback one that could come to disagree.

**The `--local` device delete says the same sentence the API does.**
The plan scopes the notice change to the API's writes. Leaving the
break-glass path saying "restart" for a row the server reads live would
be the two paths describing one act differently, which is the thing
`writes.py` exists to prevent, so `--local delete device` prints the
binding notice and the `--local` banner names the exception.

### Resolutions of what the plan left open

**Where the offloading happens.** The plan says each call site awaits
the lookup off the loop. It is `DeviceBindings.resolve` that calls
`asyncio.to_thread`, and both call sites await that: one implementation
rather than a convention every future caller has to remember, and the
synchronous `agents_for` stays available to the threadpool callers a
later milestone may add.

**A partial binding resolves to what is loaded.** A device bound to a
loaded agent and an unloaded one talks to the loaded one, and the
unloaded name still travels in `DeviceAgents.unloaded` and in the
`ota_check` record. The restart-naming log line fires only when nothing
resolved, because that is when the operator has a device that will not
connect.

**The websocket close text is one sentence for both refusals.** The two
states differ in what an operator must do, not in anything the device
can act on, so they differ in the log line and in the `session_rejected`
reason (`agent_not_loaded` beside `no_agent`) rather than in what is
said down the socket.

**How the API learns what its server loaded.** `build_api` takes the
names as an argument, defaulting to none, and the routes read them
through a dependency the way they read the store. The document is
rendered from an application built without a server, so nothing a route
declares may depend on there being one; an application told nothing
answers every write with the restart sentence, which is the conservative
direction.

### Discoveries

**The completeness rule shapes every test in this milestone.** Boot
refuses a configuration with agents that no device and no default agent
reaches, so "an agent is loaded and the device under test is unbound"
needs a second, already-bound device in the database. That is the
ordinary shape of onboarding a second board, and both new test modules
carry it.

**The integration lane's `booted` could not be reused.** It seeds a
scratch database inside a `TemporaryDirectory` and composes a config
still naming the packaged default directory, so a write through the
served app's own API would address a different file. `booted_in` and
`running_app_in` (fixture `serve_app_in`) keep the directory and compose
it onto the file half. This is the fixture variant the plan's review
finding 6 asks for, and M3's restart assertion can boot a second app
from the same directory through it.

**No new parameters leaked into the OpenAPI document.** The loaded-agent
dependency is a plain `Depends`, so the regenerated document differs
only in the description strings, which is worth knowing before adding
the next dependency to a documented route.

**A test-built app now looks at `server.database.dir`.** Most of the
unit lane composes a `Config` in memory and never names a directory, so
the view falls to its snapshot-only branch on the packaged default
(`/var/lib/samtal`), which no development machine or runner has. On a
machine that did have a database there, those tests would read it. The
mitigations considered (a read-only URI, refusing to look outside a
temporary directory) each cost more than they buy: `mode=ro` cannot
create the `-shm` file a WAL database needs a reader to have, and a
test-shaped exception in production code is worse than the exposure. It
is recorded rather than fixed.

### Notes for the milestones that follow

- `app.state.bindings` is the seam M3's `/activate` answers from: a MAC
  resolving to a loaded agent is the 200, and the plan's 202 is
  everything else.
- Activation gates on database truth rather than on this resolution
  (plan review findings 4 and 8), so M3 needs a lookup that reports the
  raw row and the default agent as well. `DeviceAgents.unloaded` being
  non-empty is the bound-but-unloaded state; a device with neither is
  the one that gets a code.
- `binding_notice(unloaded)` is what M3's add-by-code answers with, and
  the API already knows which agents its server loaded.

### Verification

`uv run ruff check .`, `uv run pytest tests/unit -q` and
`uv run pytest tests/integration -q` from `samtal-server/`, all green,
plus the OpenAPI regeneration diff CI runs. Nothing here needs hardware:
the milestone is server-side, and the M5 checkpoint still owns every
claim about a board.

### PR #116 review round

One external review of the pull request's diff: codex CLI, model
gpt-5.6-sol, read-only, 2026-08-12. Verdict mergeable after fixes; six
findings, a commit each. Two of them share a shape worth naming once:
this milestone gave the `devices` and `domain_settings` rows a second
reader, on a path that is not the CLI's or the API's, and the first
version of that reader carried its own copy of two things the
repository already owned, what a stored row means and what a written
row is called.

1. **P1: the fallback logged unsanitized library text.** The warning
   copied the DBAPI exception's own message into the line, and that
   message carries the statement, the parameters bound to it and
   whatever the driver quoted; the test that covered it exercised
   SQLite's fixed "file is not a database", which carries nothing, so
   it proved nothing about the rule.
   *Resolution*, 82ecf26: the sentence is fixed, and the only thing
   recorded about a failure is its class name, a code identifier, in a
   structured field of its own. The new test injects an engine whose
   failure carries a sentinel in all three places (statement, bound
   parameters, driver message) and asserts it is absent from both
   shipped log formats, in the message and in the structured fields,
   while the snapshot still answered.
2. **P2: the live reader restated and weakened repository semantics.**
   It parsed the rows itself and so accepted an empty binding, a
   duplicate, a blank name and a string where an array belongs
   (iterating a string succeeds and yields its characters), and read a
   malformed `default_agent` as unset. Each turns a row nobody could
   have written into a device refused for a fact nobody established,
   and the loud fallback that exists for exactly that never fired.
   *Resolution*, 801b7dc: `read_live_binding` moved into `store.py`,
   taking the deferred read engine rather than a `ConfigStore`, reading
   both rows in one transaction and validating them through the same
   array check and the same `DomainConfig` model the boot load uses. A
   row that will not validate is a `StorageError`, answered the way a
   database that will not open is: warn, and resolve from the boot
   snapshot. Six impossible rows and a malformed default agent are
   tested.
3. **P2: notices were computed from raw request values.** With `sam`
   loaded, `{"agents": [" sam "]}` bound `sam` and then answered that a
   restart was needed, sending an operator to restart a server that was
   already serving that agent; the default-agent route had the same bug,
   and the API normalized the MAC a second time to build its line.
   *Resolution*, 9f3a1f2: the repository answers with what it wrote (a
   `BoundDevice` for a bind, the canonical name or MAC for the other
   two), and both halves of the acknowledgement are built from that.
   The second normalizations in the API and the CLI are gone rather
   than corrected, so there is nothing left to drift.
4. **P2: the read engine could create a database.** An ordinary SQLite
   filename creates the file when it is missing, and the existence
   check at construction does not cover a file that goes away before
   the first lazy connection, which is a volume unmounting or a restore
   moving it aside.
   *Resolution*, 3da73c0: the database is named as a URI with
   `mode=rw`, which opens an existing file and refuses a missing one.
   Not read-only, which would be the wrong mode as well as a stronger
   claim: a WAL reader maps the `-shm` index and may extend it. The
   path is percent-encoded, because a URI ends at a `?` or a `#` and a
   path holding one would otherwise open somewhere else entirely
   (checked: it does). The new test deletes the database between
   construction and the first lookup and asserts the loud fallback and
   an empty directory.
5. **P2: the generated domain reference still promised restart-only
   writes.** The page a person reads before writing any of this
   configuration said a change takes effect at the next server start,
   full stop.
   *Resolution*, 21d9992: the generator names the exception and where
   it ends, and `docs/reference/domain-config.md` is regenerated in the
   same change, so its drift check stays green.
6. **P2: the contention test did not prove conversations stay live.**
   A ticking coroutine is not a conversation, and nothing tied its
   progress to the interval the lock was held.
   *Resolution*, 9c27f6e: the integration lane holds a real
   `BEGIN IMMEDIATE` on the served app's own database across a whole
   simulated conversation (the OTA check that resolves the binding, the
   handshake, the utterance, the spoken reply) plus a lookup on the
   app's own view, and asserts the writer is still in its transaction
   when all of it has finished. The unit test keeps its narrower job
   with the same discipline: the tick counter is read on both sides of
   the lookup, and the lock is checked afterwards. Verified by
   reverting the read engine to `BEGIN IMMEDIATE`, where the new test
   fails on a conversation that cannot connect at all.

Full lanes after the fixes: ruff clean, both suites green, both
generated documents current (counts in the PR's verification section).

## Milestone 3: the activation ceremony

The feature the previous two milestones were the ground for: an unbound
board is answered with a six-digit code instead of an empty token, shows
and speaks it, and polls until an operator claims it with one command.
The table of waiting devices, the `activation` section of the OTA reply,
`/activate` on both routers, the two `/api/devices/pending` routes and
the two commands that use them.

### The upstream constants, verified

The plan asks the implementer to check `timeout_ms` and the exact
`message` layout against the vendored sources and record what was found.
Both were read in `vendor/xiaozhi-esp32` (device) and
`vendor/xiaozhi-esp32-server` (manager-api).

**`message` is the host, a newline, then the code.**
`DeviceServiceImpl.buildActivation` sets
`code.setMessage(frontedUrl + "\n" + cachedCode)`, where `frontedUrl` is
the console URL from the `server.fronted_url` system parameter, and sets
`challenge` to the device id (the MAC) in both the fresh and the cached
branch. `Ota::ParseVersionResponse` stores the string and
`Application::ShowActivationCode` hands it to the display verbatim.
samtal sends the same two lines with the origin M1 resolves in place of
the console URL.

**`timeout_ms` is not sent by upstream at all, and the firmware never
reads it.** `DeviceReportRespDTO.Activation` has exactly three fields,
`code`, `message` and `challenge`; nothing in the manager-api sets a
timeout. On the device, `ota.cc` parses the key into
`activation_timeout_ms_`, declared in `ota.h` with a default of `30000`,
and that member appears nowhere else in `main/`: no accessor, no use in
`application.cc`, no effect on the poll loop, which is a fixed burst of
ten attempts three seconds apart (`ESP_ERR_TIMEOUT` from a 202) or ten
seconds apart (any other failure). So `ACTIVATION_TIMEOUT_MS = 30000` is
the firmware's own default sent back to it: it keeps the object the
shape issue #40 documents and changes nothing on any board. Recorded
rather than dropped, because a field that is inert today is exactly the
kind of thing a later firmware could start reading.

Two more things the same reading settled. The device appends the segment
itself (`Ota::Activate` adds `/activate` or `activate` depending on
whether the stored URL ends in a slash), so both spellings arrive as one
path. And the activation loop exits only when the fresh OTA reply
carries neither a code nor a challenge, which is why the reply to a
bound device must omit the whole section rather than send an empty one.

### What landed

**The pending table (`onboarding.py`).** `PendingDevices`, one per app,
holding `PendingDevice` records keyed by normalized MAC with a code
index beside them. Constants in the same module: six digits, a
ten-minute TTL, a cap of 128 waiting devices, and a sliding budget of 30
mints per ten minutes. The clock is injected. Every operation is one
step under one `threading.Lock`, held only for in-memory work, and
records are handed out as copies, so a listing is a moment of the table
rather than a walk over one being written. `observe` returns an `Offer`
(a record to show, or the bound that fired, in the words its warning
prints); `reserve` returns a `Claim` (the record, or `in_flight`), and
`consume` and `release` end it. Anything a device says about itself
(`board`, `firmware`, `client_id`, `serial_number`) is truncated to 64
characters and made printable before it is kept.

**The activation object (`ota.py`).** `check_version` gains
`_activation(...)`, which gates on onboarding being enabled and on the
live view resolving to neither a loaded nor an unloaded agent, that
being database truth: the two together are empty exactly when the
database holds no binding row for the MAC and no default agent.
`onboarding.activation_object` renders the four fields. The empty token
stays beside it, and the reply's `activation` key is absent rather than
empty whenever there is no ceremony.

**`/activate` (`ota.py`, `onboarding.py`).** `ota.activate` beside
`check_version` and `describe`, registered by `ota.build_router` at
`<ota_path>activate` and by `onboarding.build_router` at
`/x/<key>/activate` behind the same key guard. 200 when the live view
resolves the MAC to a loaded agent, 202 otherwise. A version-2 body is
checked for what can be checked (it parses, names a known algorithm,
echoes the issued challenge), each failure with its own `reason` on an
`activation_refused` record and none of the body quoted; the serial
number of a body that passes is recorded as an observed fact.

**The API (`config/api.py`).** `GET /devices/pending` returning the
listing keyed by code, and `POST /devices/pending/{code}` claiming it.
The listing route is registered immediately after `/devices` and before
`/devices/{mac}`. The claim parses its body, reserves, calls
`ConfigStore.bind_device`, and consumes or releases; it answers with
`bound_device(...)` and `binding_notice(...)`, the M2 sentences
unchanged. `build_api` takes the table as a fourth argument the way it
took the loaded agents.

**The CLI (`config/cli.py`).** `pending` renders a five-column listing
(code, device, board, firmware, expires) or a sentence saying nothing is
waiting; `add-device CODE AGENT...` posts the claim. `bind-device`'s
help now says it takes the MAC you already know and `add-device`'s says
it takes the code the device is showing, each naming the other.

**Documentation.** `docs/reference/api-openapi.json` regenerated;
`CHANGELOG.md` under 2026-08-12.

**Tests.** `tests/unit/test_onboarding_pending.py` (the code's shape,
re-display, expiry and re-issue including the instant of expiry,
uniqueness, the cap and the budget with their words, re-displays costing
nothing, the window refilling, the claim lifecycle, a reservation
outliving its entry's expiry, eight threads racing one code, a listing
racing a writer, and the bounding of what a device says).
`tests/unit/test_onboarding_activation.py` (the object's contents and
the message layout, the empty token beside it, its absence for a bound
device, for an unknown device under a default agent, with onboarding
off, and for a bound-but-unloaded device; both bounds through the real
endpoint; the code in the log with the command that binds it and no code
in a bound device's record; `/activate` 202 and 200 on both routers, the
wrong key and the trailing slash; the three version-2 refusals with
their distinct reasons and no part of the body in either log format).
`tests/unit/test_config_api_pending.py` (the listing's shape and
expiry, the route-order regression at the route and through the mount,
both notices, the retirement of a claimed code, the unknown, expired and
in-flight refusals, a deterministic two-claim race through the real
routes, a refused write leaving the code claimable, and codes absent
from every response they do not belong in).
`tests/unit/test_config_cli.py` gained the two commands, and
`tests/unit/test_api_openapi.py` the two routes.
`tests/integration/test_activation.py` runs the firmware's loop against
a served app and ends with the restart assertion.

### Deviations from the plan

Three, all narrow.

**`/activate` lives in `ota.py`, not `onboarding.py`.** The plan's
module layout puts the handler in `onboarding.py`; M1's own note for the
milestones that follow says the short router registers `ota.check_version`
and `ota.describe` by reference and that "M3's `/activate` registers on
both routers the same way", which it cannot do if the handler is in the
module doing the registering. The handler is therefore the third OTA
handler, beside the two it is a sibling of, and `onboarding.py` keeps
the table, the constants and the object rendering. The one import that
crosses in the other direction (`activation_object`) sits in the
function body with the comment `describe` already carries.

**The API models the two claim refusals with a new `ConfigError`
subclass rather than reusing `DatabaseBusyError`.** `ClaimInFlightError`
lives in `config/api.py` and maps to 409. It is defined there rather
than in `config/loader.py` because it is the one refusal that comes out
of the serving app's runtime state instead of the database, and calling
it a busy database would have been the wrong sentence in a place where
the sentences are the contract. The document's 409 description now
covers both.

**The pending table reaches the sub-application as an argument, and the
type does not.** `build_api(token, dir, loaded_agents, pending)` is the
seam the plan asks for. What the plan does not anticipate is that
`config/api.py` must not import `onboarding` at module scope: that
module imports `ota`, which imports the websocket session and everything
a conversation needs, and the same application is what `config openapi`
renders the committed document from with no server anywhere. So the
import is under `TYPE_CHECKING`, the route's dependency is annotated
`Any` (FastAPI resolves a route's annotations at import, so a forward
reference would fail to resolve), and the default empty table is built
by a function-body import nothing on the document path calls.

### Resolutions of what the plan left open

**Database truth is read through the M2 view, not through a second
lookup.** `DeviceAgents.agents` and `.unloaded` together are every name
the database resolved for this MAC before the loaded-agent filter split
them, so "no binding row and no default agent" is exactly "both are
empty". No new query, and no second way for the two to disagree.

**The listing publishes instants as ISO-8601 in UTC**, and the table's
clock is therefore the wall clock rather than a monotonic one: these
timestamps are read by a person deciding whether the number on the
screen is this entry, and compared against a server's log.

**The code is the listing's key and is not repeated inside the entry.**
One place per fact.

**A cap that cannot fire, kept.** With the shipped constants the budget
strictly dominates the cap: at most 30 codes are minted per ten minutes
and an entry lives ten minutes, so no more than 30 devices can ever be
waiting and the cap of 128 can never be reached. Both are implemented
and tested (the cap with the budget lifted, which the test says out
loud) because they bound different things and the plan asks for both:
the cap is what still holds if the budget is ever raised.

**Both bounds log and then fall through.** A device that is refused a
code gets the `activation_not_offered` warning naming the bound and
pointing at `bind-device`, and then the ordinary "has no agent" warning,
because both facts are true and an operator reading either one is where
they need to be.

### Discoveries

**Upstream's activation object has no `timeout_ms`.** Recorded above,
because the plan's text and the issue's both name four fields and only
three of them exist upstream.

**Starlette's slash redirect leaks the key on the activate route too,
in the other direction.** M1's finding 3 was about `/x/<key>` redirecting
to `/x/<key>/`; the canonical activate path has no trailing slash, so the
same mechanism answers `/x/<key>/activate/` with a 307 whose `Location`
echoes the attempted key. The route is registered behind the guard and
the redirect issued by hand, which is M1's fix applied in reverse, and
both directions are asserted.

**A version-1 body and an unreadable one had to be told apart.**
`_read_json_object` answers `{}` for both, which is right for
`check_version` and wrong here: `{}` is exactly what a version-1 board
sends. The reader is now `_json_object`, which returns `None` for a body
that is not a JSON object, with the old helper built on it.

**The activation loop exits on the reply, not on the poll.** The
firmware breaks out of `CheckNewVersion` only when a fresh OTA reply
carries neither a code nor a challenge. A 200 from `/activate` ends the
inner burst; what ends the ceremony is the check-in after it. That is
why the integration test asserts the order it does, and why sending an
empty `activation` object to a bound device would leave a board looping
forever.

**The CLI's own test fixture builds an application per request.** It
had to grow one pending table shared across the requests of a test,
since on a deployment the table is state of the running server and a
command that could not see what the previous one left in it would be
exercising a table nobody has.

### Verification

`uv run ruff check .`, `uv run pytest tests/unit -q` and
`uv run pytest tests/integration -q` from `samtal-server/`, all green,
with the OpenAPI drift check inside the unit lane. Nothing here is
claimed about hardware: the ceremony has been driven only against the
simulator and the served app, and whether a factory board shows the code,
speaks it, and connects after a claim is milestone 5's checkpoint.

### PR #117 review round

One external review of the pull request's diff: codex CLI, model
gpt-5.6-sol, read-only, 2026-08-12. Eight findings, verdict mergeable
after fixes; each is fixed with a commit of its own. Five of the eight
are one shape, and it is worth saying once: this milestone gave three
attacker-reachable strings a new reader (a header, a path segment, and
an agent name typed beside a code), and gave one in-memory fact a new
authority (an empty resolution now decides whether a claim ticket is
minted). Every P1 below is a place where the old handling of such a
value was right for its old reader and wrong for the new one.

1. **P1: production access logs exposed secrets and rejected values.**
   Uvicorn's access log was on and its loggers propagate into the root
   handler, so the legacy request line carried the secret `ota_path`
   and a claim's carried the rejected code.
   *Resolution*, 36762e0: `access_log=False` in the one uvicorn
   configuration `serve()` now builds, rather than a sanitized
   route-template access log. An access line was never part of the
   observability surface this project decided on
   (`docs/adr/2026-08-04-json-logs-are-the-observability-surface.md`):
   what an operator reads is the structured events, which name the
   device, the agent and the outcome, and are written by handlers that
   know which of their values may be said out loud. A second vocabulary
   to keep sanitized would be a second one to get wrong. The test runs a
   real server through that configuration at INFO with both sentinels in
   real request paths and asserts both formats are clean; it fails with
   the access log on, which is how the assertion is known to be about
   something.
2. **P1: the legacy activation redirect returned the OTA credential in a
   `Location` header.** Only one spelling of each legacy route was
   registered, so Starlette's own trailing-slash handling answered the
   other with a 307 whose `Location` was the corrected URL, which on
   this router is the deployment's secret path. The base route had the
   same shape, reachable by a portal that stripped the slash.
   *Resolution*, d797a8e: both spellings of all three legacy routes are
   registered and dispatched directly, so there is no redirect left to
   carry anything. The short onboarding path keeps its guarded redirect,
   because its key is deliberately printable and the miss branch is what
   the key check exists for. The parametrized trailing-slash test splits:
   the short path still asserts the 307, and the legacy one asserts the
   reply, no `Location`, and the segment absent from the headers.
3. **P1: a rejected `Device-Id` reached the log and the response.**
   `normalize_mac`'s refusal names the value it refused, which is right
   for a configuration file and wrong for a header on an
   unauthenticated endpoint; a newline in it forged a log entry.
   *Resolution*, bd9fb40, with the integration lane's own assertion in
   04effbf: one fixed sentence on both the configuration check and the
   activation poll, saying what a Device-Id has to be and that what
   arrived is not repeated. The rule is stated on the refusal
   helper rather than at the two call sites, since it is about every
   caller of it. Sentinel assertions over the body, the headers, both
   log formats and the process's own streams.
4. **P1: add-by-code echoed the agent names it refused.** The
   repository's unresolved-reference refusal names them, and this is
   the one route where an agent name is typed by hand beside an
   activation code.
   *Resolution*, 2340e64: the claim answers with a sentence naming the
   field rather than its contents and pointing at `config list`. Only
   that refusal is re-worded: one about the server rather than the
   request (a busy database, unreadable stored state) travels out as
   itself, or a retryable failure would read as a bad agent name. It is
   recorded and re-raised outside the handler, since `from None` clears
   the cause and leaves the context. Identifying the failing *index*
   was considered and declined: the handler cannot know which name the
   repository objected to without either parsing its sentence or
   restating its rule, and both are worse than naming the field.
   `PUT /devices/{mac}` keeps the repository's own wording, per the
   reviewer's scope note; the leak there is the same shape and predates
   this work, and is recorded here rather than fixed out of scope.
5. **P1: a failed database read could mint a code for a bound device.**
   The live view's fallback answered from the boot snapshot in the same
   shape as a successful read, so the activation gate could not tell
   "nothing is bound" from "this server could not find out", and a
   device bound after boot was offered a code while the read was
   failing.
   *Resolution*, 8d70cd0: `DeviceAgents` carries whether it is
   authoritative. Token issuance keeps the fallback unchanged, since a
   stale answer there only repeats what boot decided; activation refuses
   to act on one and says so. A view with no database behind it is
   authoritative, the snapshot being the whole truth there is, which is
   what the unit lane and an embedded server have.
6. **P1: a stale code could overwrite a newer binding.** The claim
   called `bind_device`, an upsert, and pending entries survived a
   covering write, so a code issued minutes earlier replaced a binding
   made by another request.
   *Resolution*, 976ac62: the repository grows `claim_device`, a
   conditional bind that reads the device row and the default agent
   inside the same transaction as the write and refuses if either has
   taken the device, raising `DeviceAlreadyBoundError`. Two decisions
   recorded: the refusal has its own sentences rather than reusing the
   read-the-screen one, because what happened is worth knowing and the
   MAC may be named (it is already in the acknowledgement and the log);
   and it maps to 404, since what the request addressed, a device
   waiting to be claimed under this code, is not there. The entry is
   consumed rather than released on that refusal, being claimable by
   nobody now. Beside it is housekeeping: a device bound by its MAC
   leaves the listing, and setting a default agent empties it. The
   condition is the guarantee and the housekeeping is not: a write made
   where the table cannot be reached (`--local`, a second process)
   reconciles nothing, which is how both race tests write.
7. **P2: the claim's acknowledgement was built from the request.** A
   name sent with spaces bound the loaded agent while the answer named
   the spaced string and demanded a restart.
   *Resolution*, f92c5a0: it answers with the `BoundDevice` the
   repository returns, which is what binding by MAC was already changed
   to do in the previous milestone's review round. One rule for both
   routes: what a write says it did is about the row.
8. **P2: a failed claim near expiry was not claimable again.** Release
   only cleared the flag, and a write that fails on a busy database
   takes the ten-second busy timeout, long enough to step over a
   deadline with seconds left, so "run the command again" was answered
   with "no device is waiting".
   *Resolution*, 970dacf: a release moves the deadline out to at least
   a minute from now, which is what makes that advice true. Extension
   only, so a code with most of its ten minutes left keeps them, and it
   is a grace rather than a reprieve: a code nobody claims afterwards
   expires the way every other one does. The alternative considered was
   pausing the clock across the reservation, which preserves the total
   lifetime exactly but can hand back milliseconds, which is the same
   unactionable answer in a smaller font.

One discovery from the round, recorded because a later reader will meet
it: restoring a file with `git checkout <file>` during this work
discarded uncommitted edits to it, exactly as `AGENTS.md` warns, and
`claim_device` had to be written twice.

Full lanes after the fixes: ruff clean, both suites green, both drift
checks unchanged (counts in the PR's verification section).

## Milestone 4: `ota-url`, `doctor`, and the onboarding docs

The two commands the ceremony is actually run with, and the
documentation that describes it as a ceremony rather than as an NVS
write. No new route, no new API surface, nothing on the device path.

### What landed

**The onboarding helpers take the server half (`onboarding.py`).**
`onboarding_key`, `public_origin`, `portal_url_line`, `log_banner` and
`activation_object` read `config.server` and nothing else, so they now
take a `ServerConfig`. That is what lets the CLI call them: a command
holds a `FileConfig`, and the alternative was either composing a whole
`Config` around an empty domain to satisfy a type or reimplementing the
derivation, the second of which is the one thing that could send an
operator to a URL this server does not serve.

**`config ota-url` (`config/cli.py`).** Reads the file half through
`load_file_config`, resolves the origin and the key through the two
functions above, and prints `<origin><path>` to stdout alone, so
`$(samtal-server config ota-url)` is a URL. The guidance and the
origin's provenance go to stderr, the way every other notice does, and a
guessed origin reads as a guess because `Origin.provenance` is the
banner's own rendering. Three states are refusals rather than URLs:
onboarding disabled (naming `server.ota_path` without quoting the
segment), a missing device-auth secret (naming the variable and the
pinned-key way out), and, inherited, any file the loader refuses. A
pinned `onboarding.key` prints its URL with no secret in the
environment at all.

**`config doctor` (`config/cli.py`).** GETs the given URL, or the
derived one, through `build_client` with no token, and answers with one
of the plan's four verdicts: unreachable, not samtal-server, a `ws://`
websocket URL behind an `https://` OTA URL, or healthy with the
websocket URL, server version and protocol version the endpoint
reported. Healthy exits 0; the other three are `ConfigError`s and exit
1. It never POSTs, so it mints no code and spends none of the mint
budget. `_device_url` applies the parts of the API's transport policy
that are about the URL (unreadable refused without quoting it, userinfo
refused) and not the part that is about the token (loopback-or-TLS),
because no credential crosses this request and a plain `http://` LAN
address is exactly what a device is pointed at.

**Everything the far end says is bounded before it is printed.**
`_printable` truncates to 120 characters and replaces anything
unprintable, the rule `onboarding._fact` applies to what a device says
about itself; `_shown_url` additionally strips userinfo from the
websocket URL a response names; and only the first 4 KB of a body is
ever matched against a pattern.

**Documentation.** `samtal-server/README.md` gains "Onboarding a
device" in place of "Pointing a device at the server": the five steps,
which devices are offered a code (only those the database resolves to
nothing, so a deployment with `default_agent` never sees one), the
fresh-deployment ordering, already-provisioned boards, and a rotated
secret. Its security section now describes two paths to one endpoint
and why the derived key is printed where the `ota_path` segment is not;
its reverse-proxy list names `config doctor` and `public_url`; the
configuration section points at the ceremony from the `bind-device`
line. The root README's steps 6 to 8 follow the same path.
`config.deploy.example.yaml` leads with the onboarding block and the
derived key, and the legacy `ota_path` keeps its own paragraph with the
secret-store advice that is still right for it.
`config.example.yaml` gains the two commands and the sentence about
`default_agent`. `CHANGELOG.md` under 2026-08-12.

**Tests.** `tests/unit/test_config_cli_onboarding.py`: for `ota-url`,
the printed URL served by an app built from the same file, the printed
URL equal to the banner's for the same file, no socket and no database
and no API token, the URL alone on stdout, the guess reading as a
guess, the keyless URL with auth off, the pinned key with no secret,
the missing secret naming the variable, the configured secret variable,
and onboarding off naming `ota_path` without its segment; for `doctor`,
the four verdicts, the real describe body recognized (the drift guard),
`ws://` behind plain `http://` staying healthy, the slashless URL
following the server's own 307, no Authorization header reaching a
device-facing address, GET and only GET, the URL refusals, and the
hostile endpoint (an oversized body, a body of control characters, a
credential in the websocket URL, an unreadable websocket URL).

### Deviations from the plan

Five, all narrow.

**The helpers were narrowed to `ServerConfig`.** The plan says the
command derives "key and URL with the same functions the server uses;
same package, so there is nothing to drift". It does, and this is what
that costs: five signatures and their call sites. Recorded because the
plan describes a new command and this is a change to three existing
modules.

**Onboarding disabled is exit 1, not exit 0.** The plan says the
command "says so and points at `ota_path`". It does, on stderr, through
the same `ConfigError` boundary every other refusal leaves by. The
alternative, a sentence on stdout and exit 0, would put prose where a
caller capturing the output expects a URL.

**`doctor` with no URL and onboarding disabled refuses rather than
probing the legacy path.** It could construct the `ota_path` URL and
check it, but every verdict names the URL it checked, and that segment
is the one string these commands may not print. So it says onboarding is
off and asks for the URL as an argument, which is the same sentence
`ota-url` prints with a different fix at the end.

**The transport error is named by its exception class only.** The
plan's verdict is "unreachable (with the transport error)". httpx puts
the request into its exceptions and drivers put whatever they like into
their messages, which is the M2 review's first finding; the class name
is the part that says what happened (`ConnectError`, `ReadTimeout`,
`ConnectTimeout`) and carries nothing else.

**`doctor` follows redirects.** Not in the plan either way. A URL typed
without its trailing slash is answered by the server's own 307, and
reporting that as "not samtal-server" would be this command's worst
answer; the redirect is asserted against a real Starlette one.

### Resolutions of what the plan left open

**The missing-secret state is told apart without reading the secret.**
`onboarding_key` answers `None` for three different situations: no key
pinned and auth off, no key pinned and the secret variable empty, and
(inside a server) never, because that second one has already refused the
boot. The CLI separates them with the two fields that decide it,
`server.auth.enabled` and `server.onboarding.key`, so nothing in the
command reads or holds the secret, which is the posture M1 chose when it
kept the environment read inside `onboarding_key`.

**The describe body is parsed, not shared.** `doctor` is a client of an
HTTP endpoint, the way the rest of the CLI is a client of the API, so it
matches two patterns against what came back rather than importing a
format string from `ota.py` (which it cannot import anyway, see below).
What keeps the two in step is a test that runs the real handler's
output through the real patterns.

**Both commands take `--config` and nothing else.** `--api-url` and
`--local` address the configuration API, which neither command touches,
and offering the flags would say otherwise. The top-level parser still
accepts them before the command, which is where the grammar has always
allowed them.

**`build_client` grew an optional token.** One seam rather than two, so
the acceptance suite replaces one function; without a token there is no
Authorization header, which is what a device-facing GET needs.

### Discoveries

**`config/cli.py` cannot import `onboarding` at module scope either.**
The same constraint M3 found for `config/api.py`: `onboarding` imports
`ota`, which imports the websocket session and everything a
conversation needs, and `config reference` and `config openapi` are
rendered from the models and the routes with none of that loaded. The
import sits in `_onboarding_url`'s body, with `Origin` imported under
`TYPE_CHECKING` for the annotation.

**A test double for the client seam has to carry the header logic.**
The "no bearer token is sent" assertion is only worth something if the
factory the test installs would have sent one; a double that ignored its
`token` argument would have made the assertion vacuous. The fixture
mirrors `build_client` instead.

**`create_app` needs `SAMTAL_API_SECRET`, and `ota-url` does not.** The
acceptance test sets the variable only after the command has run, which
turns an ordering detail into the assertion that the command needs no
token.

**The root README's own walkthrough never reaches the ceremony.** Its
step 3 sets `default_agent`, which covers every unknown board by design,
so no board following those instructions is ever offered a code. That is
correct behavior and would have read as a broken feature, so both
READMEs now say which devices are offered a code and what unsetting
`default_agent` changes.

### Notes for the milestone that follows

- `config ota-url` is what M5 reads the URL off, and `config doctor`
  is what it checks the deployment with before typing anything into a
  board; neither needs the server that is about to be tested.
- The claim "the firmware shows the code and speaks it" is still a
  reading of upstream's sources. The server README says so in one
  sentence, which is the sentence M5 gets to delete.

### Verification

`uv run ruff check .`, `uv run pytest tests/unit -q` and
`uv run pytest tests/integration -q` from `samtal-server/`, all green,
plus both generated documents diffed clean (`config reference` against
`docs/reference/domain-config.md`, `config openapi` against
`docs/reference/api-openapi.json`), which is what a milestone that adds
no route has to show. Both commands were also run by hand against a
real server started from this tree: the derived URL matched the startup
banner exactly, `doctor` reported it healthy, `/healthz` as not this
endpoint, and a dead port as unreachable. The `ws://`-behind-TLS verdict
has been exercised only against a canned endpoint, since this tree has
no TLS proxy in front of it. Nothing is claimed about hardware.

### PR #118 review round

One external review of the pull request's diff: codex CLI, model
gpt-5.6-sol, read-only, 2026-08-12. Verdict not mergeable; eight
findings, a commit each. Five of them share one shape, and it is worth
saying once. This milestone added a command that takes a URL from an
operator and repeats what a stranger returned, onto a reader nothing
else in this repository writes to: a terminal, and the shell history
and scrollback behind it. Every no-leak rule the earlier rounds
established was written for a log line or an HTTP response, and each of
the five findings is a place where the old rule was applied to the new
reader by analogy rather than by argument.

1. **P1: every verdict printed the URL it was given.** A URL passed to
   `doctor` may be the legacy `ota_path` URL, whose segment is the whole
   protection that endpoint has, and the documented way to check a
   deployment with onboarding turned off is to pass exactly that. All
   four verdicts and both URL refusals interpolated it.
   *Resolution*, 7c4d72b: a supplied URL is called "the supplied OTA
   endpoint" in every line, and the refusals in front of the request no
   longer quote the address even with its userinfo removed, since what
   is left still holds the segment. The derived short URL is still
   shown, which is the recorded exception for its key and the reason an
   operator ran the command. Sentinel tests carry a secret legacy
   segment through all four verdicts and both refusals.
2. **P1: an accepted control character escaped as a traceback.**
   `urlsplit` deletes tabs, carriage returns and newlines rather than
   refusing them, so a URL carrying one passed every check and reached
   httpx, whose `InvalidURL` is not an `HTTPError` and was caught by
   nothing; the command exited with a traceback quoting the address.
   *Resolution*, 751f6ca, in both places, since either alone leaves a
   way in: a URL with a space, a control character or anything else
   unprintable is refused before it is parsed and without being quoted,
   and building the client joined the request and the close inside the
   sanitizing boundary, because httpx validates a URL when it is handed
   one. The tests drive the real streams through `cli.main` and assert
   the exception carries no chain.
3. **P1: the wrong-service verdict relayed an untrusted body.** It
   printed the first 120 printable characters of whatever answered,
   which contradicts the fixed sentence the API client uses for a body
   it cannot read, and a proxy or a metadata endpoint can put a
   credential in a first line.
   *Resolution*, 5ef67b5: the status code and a fixed sentence, reusing
   `UNRECOGNIZED_ANSWER` verbatim so the two policies are one policy.
   The test that asserted a captive portal's page was echoed back is
   now the test that asserts it is not.
4. **P1: an unreadable reported websocket URL leaked and passed.** The
   display form fell back to the raw string when the parse failed,
   which is exactly the case whose userinfo could not be stripped, and
   the committed test embedded a password in one, expected exit 0, and
   never asserted the password was absent. It was not.
   *Resolution*, 4e21433: no fallback. A reported URL that will not
   parse, names no host, or is not `ws`/`wss` is a verdict of its own,
   quoting nothing and pointing at `server.websocket_url` on that
   deployment. Six unreadable shapes are tested, each carrying the
   sentinel where the leak was.
5. **P2: the TLS verdict compared raw prefixes.** Case-sensitive
   `startswith` on the typed string and on the reported text, so
   `HTTPS://` with `WS://` was reported healthy, and a URL that
   redirected was judged by where it started.
   *Resolution*, cce6376: both sides are normalized parses, the probe's
   from `response.url` (where the answer came from) and the reported
   one from the parse that already had to happen to strip credentials.
   Upper-case and mixed-case cases both ways, and a redirected probe
   judged on its final URL.
6. **P2: unrestricted redirect following is a pivot.** The probe
   followed anything, up to the client's default of twenty, though only
   samtal's own trailing-slash 307 is needed; a public endpoint could
   steer it at an internal or metadata address.
   *Resolution*, dcccc72: the client follows none, and the probe
   follows exactly one whose target is the same scheme, host, port and
   query with the path just asked for plus a slash. Everything else is
   refused without repeating the target, in front of the second request
   rather than after it, which the tests assert by counting what the
   endpoint saw. This also answers the cross-scheme half of finding 5
   by refusing the case rather than reasoning about it.
7. **P2: the guidance promised a code many boards never show.** It said
   the board "then shows a six-digit code", while the documented
   walkthrough sets `default_agent`, which covers every unknown board
   and produces no code at all.
   *Resolution*, 6eb1dd9: the sentence is conditional and names why the
   other branch exists, the README's step says the same in the same
   words, and a test reads the README's own section and asserts the two
   keep agreeing, since the failure was two documents describing one
   behavior differently.
8. **P2: the laptop invocation was claimed and not shown.** The plan
   keeps `uvx --from` the server package as the documented laptop path
   until a slim CLI redistribution exists; the section claimed "from a
   laptop" and showed only an installed invocation.
   *Resolution*, f9a1762: the exact command, with the git URL and its
   subdirectory, the config file and the secret, plus two caveats that
   are true: it resolves the whole server to run a command that opens
   no socket, and it resolves without this repository's lockfile. What
   was verified is in the verification note below.

### Discoveries from this round

**The test client is a different httpx.** Starlette's `TestClient` in
this environment is built on `httpx2` 2.9.1 while the CLI uses `httpx`
0.28.1, so an exception raised by the double is not an exception the
CLI's boundary catches. Anything asserting transport-error handling
therefore has to go through the real client (the dead-port test) or
raise httpx's own class explicitly (the InvalidURL test), and a double
that raised "an httpx error" would prove nothing.

**httpx builds the redirect request even when not following it.** With
`follow_redirects=False` it still parses `Location` to fill
`response.next_request`, so an unreadable `Location` surfaces as a
transport error before the canonical-slash rule is consulted. The rule
still refuses it, and is asserted directly on a hand-built response
rather than through the command.

**`uvx --from` does not use this repository's lockfile.** Verified
offline against the local path (`uvx --offline --from ./samtal-server`),
which resolved an `mcp` release incompatible with the code and failed at
import. That is evidence for the caveat in the README rather than for
the invocation: the git URL form needs the network and was not run, and
the documented shape of the command (`config --config PATH ota-url`)
was verified through `uv run` instead.

### Verification after the round

`uv run ruff check .`, `uv run pytest tests/unit -q` and
`uv run pytest tests/integration -q` from `samtal-server/`, all green,
with both generated documents still diffing clean. `doctor` was also run
read-only against a server already running from this worktree: the
healthy verdict names the endpoint without repeating the URL it was
given, and the same URL typed without its trailing slash still reaches
the endpoint, which is the one redirect that is still followed. The
`uvx` invocation is not verified: it needs the network, and it is
documented with the caveats above rather than claimed to have been run.
