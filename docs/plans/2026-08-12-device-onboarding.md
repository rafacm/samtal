# Device onboarding by short URL and activation code plan

## Goal

Implement issue #40: onboarding a stock-firmware board today means
typing a 38-character secret OTA URL into a captive portal with no
feedback on a typo, then finding the board's MAC and binding it by
hand. Three fixes, all server plus tooling, no firmware changes: the
string gets short (a secret-derived `/x/<key>/` alias for the OTA
endpoint), a wrong string says so (a 404 whose log line names the
attempted and the correct key), and binding uses the activation-code
ceremony stock firmware already ships (the OTA response carries a
6-digit code, the device shows and speaks it, the operator binds it
with one CLI command, and the device connects within seconds through
its own polling loop).

The issue's design decisions are settled and this plan does not
re-litigate them. The issue was, however, written against the
pre-#86 configuration architecture, and three of its mechanisms are
superseded by work that has since shipped; the reconciliation below
was decided with the issue's author on 2026-08-12 and is recorded
here rather than silently applied. The issue's open questions and
the smaller decisions it leaves to the plan are resolved below, each
with its reasons.

The companion implementation doc,
[`2026-08-12-device-onboarding-implementation.md`](2026-08-12-device-onboarding-implementation.md),
records what each milestone actually did, with deviations from this
plan, resolutions of its open questions, and discoveries; a
milestone with no deviations says so explicitly.

## The issue's decisions, restated for reference

From issue #40, fixed, one line each:

1. A short onboarding path `/x/<key>/` serving the same handlers as
   `server.ota_path`, where the key is
   `base32(HMAC-SHA256(auth secret, "samtal-onboarding-key-v1"))`
   truncated to 8 characters: derived from the secret the deployment
   already has, never configured, stored, or persisted, stable
   across restarts, rotating only when the secret rotates.
2. Base32 because `A-Z2-7` contains no `0`/`O` and no `1`/`I`/`l`,
   the pairs a person misreads off a 240x240 display.
3. A wrong key returns 404, indistinguishable from a path that was
   never served, and logs the attempted key next to the correct one
   so the operator sees the typo character by character. The key
   appearing in logs is a deliberate, recorded trade: it is a
   deployment-scoped path segment, not a per-device token, so the
   "tokens are never logged" rule is untouched.
4. `server.ota_path` becomes nullable so a public deployment can
   unmount the legacy route while provisioned boards keep working.
5. A startup banner names the exact URL to type, backed by a new
   `server.public_url`; when unset the banner derives the origin
   from `websocket_url`, then from `host`/`port`, and says which of
   the three it did. A guess must look like a guess. The GET
   `describe` handler gains the same line.
6. An unbound device's OTA response carries
   `activation {message, code, challenge, timeout_ms}` with a
   6-digit code drawn from `secrets`, `challenge` set to the
   device's MAC (without it the firmware polls slowly), and
   `message` as the deployment's public host over the code, exactly
   what upstream renders.
7. The server keeps an in-memory table of pending devices keyed by
   MAC: code, client id, board model, firmware version, first and
   last seen. Codes are unpredictable, expire after a TTL, re-issue
   on expiry, and issuance is bounded. The table is deliberately not
   persistent: a code is a live claim ticket, and the device's
   re-check loop heals a restart within a couple of minutes.
8. `/activate` on the same routes answers 202 while the MAC is
   unbound and 200 once bound, mirroring upstream semantics; the
   device's next poll is seconds away, so binding takes effect with
   no power cycle.
9. Binding is one CLI command taking the code the device announces;
   the code names the pending MAC.
10. `server.onboarding.key` pins the previous key across a secret
    rotation, and the 404 log hint is what makes a rotation
    diagnose itself.
11. Firmware changes are out of scope; everything here works against
    stock upstream firmware, and the OTA response cannot set the
    device language (the parser and the compiled language assets
    foreclose it; the assets-bundle lever is a follow-up).

The protocol ground truth is the activation section of
[`../xiaozhi-notes.md`](../xiaozhi-notes.md), reconstructed from the
vendored device and server sources; there is no public spec.

## Where the shipped architecture supersedes the issue

Issue #40 predates the domain-config split (#86) and the REST API
(#101). Three of its mechanisms collide with decisions that have
since shipped and carry their own settled rationale. Resolved with
the issue's author, 2026-08-12:

### Bindings live in the database, not a YAML overlay

The issue proposed a `server.onboarding.registry` YAML store shaped
like the config file, with the config `devices:` map winning over
it. Since #86 there is no config-file `devices:` map to win: device
bindings are rows in the SQLite `devices` table, written through the
`ConfigStore` repository inside one transaction with reference
checks, and a `devices:` key in YAML is a boot refusal naming where
it moved. A second, YAML-shaped write path for bindings is exactly
the "two co-equal write paths" state the #101 stack was structured
to forbid.

Add-by-code therefore writes through the existing repository into
the existing table. Everything the issue wanted from the overlay
survives: bindings persist on the `/data` volume (as database rows
rather than a YAML file), a GitOps deployment stays declarative
(`config.deploy.example.sh` is a script of CLI calls), and the
promotion path out of runtime state (`devices yaml`) is retired
because there is nothing to promote between: `config show device`
already renders any binding, however it was written.

### The admin surface is the existing `/api`, not a new `/admin`

The issue proposed `/admin/devices` behind a bearer token derived
from the auth secret under a distinct HMAC label, off by default,
with "registry set while auth disabled" as a boot failure. That
design existed to avoid inventing a second secret when no admin
surface existed. One now does: `/api` is always mounted behind the
mandatory `SAMTAL_API_SECRET` bearer gate, with typed refusals, a
committed OpenAPI document, and a CLI that is already its client. A
second admin surface with a second auth scheme on the same server
would be the new inconsistency. The onboarding endpoints join `/api`
and inherit its gate, its error mapping, and its drift-checked
document; the issue's auth-interaction boot failure is moot because
the API token is independent of device auth and always required.

### The CLI grows; no separate `samtal-config` distribution

The issue specified a separate small distribution so
`uvx samtal-config` would not resolve the server's heavy
dependencies to send one HTTP request. Since #101 the operator
surface is `samtal-server config`, an HTTP client of the API with
URL and token resolution, transport policy, and an acceptance suite.
Two overlapping CLIs would cost more than the dependency weight
saves, and the issue's key-derivation drift test exists only to
mitigate the duplication a second distribution creates. The new
commands (`add-device`, `pending`, `ota-url`, `doctor`) join
`samtal-server config`; a slim redistribution of the same code is a
packaging follow-up, tracked outside this plan, and until then the
documented laptop invocation remains `uvx --from` the server
package.

## Resolved open questions and further decisions

### A live view of device bindings, and only device bindings

Activation binds a device while the server runs, and the device's
next `/activate` poll is three seconds away, so the running server
must observe the write. Configuration is otherwise a boot-time
snapshot by explicit #86/#101 decision, and this plan does not
reopen that: the live view is scoped to exactly the data the
onboarding ceremony changes underneath a running server, which is
the `devices` table and `default_agent` (the two inputs of
`agents_for_device`). Providers, agents, MCP servers and everything
else stay boot-snapshot, restart notice unchanged.

The mechanism is a `DeviceBindings` component on `app.state`,
constructed at app build with the composed snapshot and a read
engine of its own, created after boot has already run migrations
and disposed in the app's lifespan. `agents_for(mac)` reads the
`devices` rows and `default_agent` in one ordinary deferred read
transaction and resolves with the same rule
`Config.agents_for_device` applies today (bound list, else default
agent, else nothing). Five deliberate properties:

- **Reads never block the event loop and never migrate.** The OTA
  and session handlers are async, so a lookup that ran
  `open_database()` inline would put an Alembic check inside
  `BEGIN IMMEDIATE`, a write lock with a 10-second busy timeout,
  on the loop (the REST API tolerates exactly that because its
  handlers run on the threadpool; device paths cannot). The read
  engine skips the migration check, boot already performed it, and
  every lookup is awaited off the loop (`asyncio.to_thread` or the
  threadpool equivalent at each call site). Under WAL a deferred
  read transaction takes no write lock and does not block on
  writers, so a held write lock cannot stall a lookup; a
  contention test holds a real write lock and asserts lookups and
  unrelated conversations stay live.
- **Per-lookup transactions, no cache, no cross-app wiring.** The
  alternative (refresh callbacks from the API sub-application into
  the parent app) is less code on the hot path but couples the two
  apps and misses `--local` writes entirely. The call sites are
  low-rate: OTA check-ins (boot plus the activation loop's
  re-checks), `/activate` polls (3 s bursts per pending device), and
  websocket connects. If a future fleet makes this measurable the
  change is local to `DeviceBindings`.
- **The boot contract changes by exactly this much.** `boot.py`'s
  "nothing after boot reads the database" narrows to "nothing
  after boot reads the database except `DeviceBindings`, which
  reads only the `devices` and `domain_settings` tables through
  its own read engine"; the sentence changes where the contract
  lives, in the same commit that adds the component.
- **Resolution filters to agents the boot snapshot loaded.** A
  binding written after boot can name an agent created after boot,
  whose providers were never built; issuing a token for it would
  invite a websocket the session layer must refuse. `agents_for`
  drops names absent from the loaded agent map and the OTA log line
  says so distinctly ("bound to agent X, which this server has not
  loaded; restart to load it"), instead of the generic "has no
  agent" advice.
- **A failed read falls back to the boot snapshot, loudly.** The
  OTA endpoint is every fleet device's boot dependency; a transient
  `/data` hiccup must not refuse every check-in. On a database
  error, `agents_for` logs a warning naming the fallback and
  resolves from the snapshot. Staleness is visible in the log, never
  silent.

The write acknowledgements for `PUT /api/devices/{mac}`,
`DELETE /api/devices/{mac}` and the default-agent writes change
their notice: device bindings now apply to the device's next OTA
check or connection with no restart, while everything else keeps the
restart sentence. The notice text lives in `config/writes.py`, one
place, and the CLI prints it verbatim as today. The API's own
contract states the restart rule in three more places, and all of
them change in the same milestone the behavior does: the
sub-application's description, the `Acknowledgement` model's
`notice` documentation, and the `_acknowledge` helper that
hardcodes the sentence gain the two-notice reality, and
`docs/reference/api-openapi.json` is regenerated in M2, because
every merge publishes an image and a published contract must not
describe behavior the release no longer has. Live sessions are
deliberately untouched by a binding change: deleting a binding stops
the next token issuance and the next connection, not a conversation
in flight, the same line the session-boundary work drew.

### Activation gates on being unbound, with onboarding on

The OTA response carries `activation` exactly when
`server.onboarding.enabled` is true and the database holds neither
a binding row for the presented MAC nor a configured default
agent. That is deliberately database truth, not the loaded-agent
filter: the two disagree exactly when a binding or a default agent
was written after boot naming an agent the running server never
loaded, and that state must not mint codes for a device an
operator already added. Such a device gets no token, no activation
object, and a log line and acknowledgement naming the restart that
will load its agent. The other side of the same coin is upgrade
compatibility: a deployment with a configured default agent covers
every unknown MAC by design, so its devices keep receiving a token
and no activation object, exactly today's behavior, and an upgrade
regression test pins that state.

Fresh deployments make that state ordinary rather than exotic: a
first start boots an empty domain, and an agent written afterward
is not loaded, so add-by-code cannot promise a no-restart bind
there. The acknowledgement therefore says which case happened:
when every agent it bound is loaded, the notice says the device
connects with no restart; otherwise it carries the restart
sentence, and after the restart the device's own loop completes
the ceremony with no device-side action. The onboarding docs and
the deployment profile state the ordering out loud: configure the
domain, restart, then onboard devices restart-free.

Auth state does not gate activation: with `auth.enabled`
false the token is always empty and the websocket accepts anyone,
but an unbound device still resolves to no agent and is refused at
session start, so the code ceremony is how a trial network binds a
board too. What auth changes is only the path: no secret means no
derivable key, so the short route mounts keyless as `/x/` (decision
recorded in the issue's verification list).

`onboarding.enabled` defaults to true. The issue's admin API was off
by default because it could have been an unauthenticated mutation
surface; that concern died with the mandatory API token. What
remains is the OTA endpoint minting codes for whoever reaches it,
which the pending table bounds (below), and the ceremony working out
of the box is the point of the issue.

### The pending table is bounded and its parameters are constants

Codes are 6 digits from `secrets.randbelow`, unique among live
pending entries, TTL 10 minutes, re-issued at the next OTA check
after expiry (the device re-checks every half minute to two minutes,
so the screen heals itself; the operator always types what is
currently displayed). The table caps at 128 pending MACs; at the
cap, a new unbound device gets today's behavior (empty token, no
activation object) and a warning names the cap. Minting is also
rate-limited globally, independently of how many entries are live:
at most 30 new codes in any sliding 10-minute window, counting
mints and re-issues but not re-displays of a live code, so an
attacker who fills and refills the table is bounded per window,
not only per snapshot. At an exhausted budget a new device gets
the same silent response and a warning names the budget. One live
code per MAC per TTL bounds per-device issuance; the cap bounds
the standing table; the budget bounds the mint rate. The table
takes an injected clock so expiry and the window are tested
deterministically. These are constants in `onboarding.py`, not
configuration: nobody has field
evidence to tune them by, and a knob nobody can reason about is
schema noise. If the field says otherwise they graduate to config
then.

The table is shared between the device-facing handlers on the
event loop and the API handlers on the threadpool, so every
operation on it happens under one mutex, held only for in-memory
work (microseconds, so holding it briefly on the loop is fine) and
never across a database write. A claim follows a live, reserved,
consumed lifecycle: `add-device` reserves the code under the lock,
performs the repository write with the lock released, then
consumes the entry on success or releases the reservation on
failure. A concurrent claim of a reserved code is refused with
retryable wording rather than reported bound, so two operators
racing one code cannot both succeed; expiry, uniqueness, re-issue,
and the listing are single lock-held steps.

`timeout_ms` and the exact `message` layout mirror what upstream's
manager-api sends and the firmware renders (host on one line, code
under it); the implementer verifies both against the vendored
sources and records the values in the implementation doc. `message`
needs no localization: it is a hostname and six digits, and the
words on screen around it come from the firmware's compiled assets,
which the server cannot influence.

### `/activate` semantics, and the version-2 HMAC honestly

`/activate` is registered on both routers (the short path and the
legacy `ota_path`), reads the MAC from `Device-Id`, and answers by
the live view: 200 when the MAC resolves to a loaded agent, so
that a 200 always means the next OTA check hands the device its
real configuration; 202 otherwise, including bound-but-unloaded
MACs (which flip to 200 at the restart that loads their agent) and
MACs with no pending entry (a restart loses the table; the
device's loop re-checks OTA and gets a fresh code, and answering
202 meanwhile matches upstream's "keep waiting").

The issue says a version-2 poll's HMAC is verified before 200. It
cannot be: the HMAC is computed with an eFuse-burned per-device key
that upstream's cloud knows from vendor registration and samtal has
no copy of. This is therefore the fourth deviation from the issue's
text, put to the issue's author with the plan review and decided on
2026-08-12: the code ceremony governs both versions. The rationale
is that a 200 carries no secret, and a device token only ever
arrives over the key-protected OTA path, so verifying an HMAC the
server has no key for would authenticate nothing; the authority for
binding is the one the issue itself names, possession of the code
on the physical screen plus the API token. A per-device key
registry was considered and declined as speculative (no version-2
hardware to test, no key source), and declaring version 2
unsupported was declined because it would shut version-2 boards out
of the ceremony to defend a property the server cannot have.

What the server can check while a MAC is pending, it does: a
version-2 body must parse, must name a known algorithm, and must
echo the challenge this server issued for that MAC; a mismatch is
refused with 202 and a distinct log reason, because a poll
answering someone else's challenge is not evidence of anything. The
challenge check is scoped to the pending state: once the MAC is
bound and its agent loaded, the pending entry is gone and
`/activate` answers 200 with nothing left to check, which is also
what keeps the post-binding poll working. The serial number a version-2 body
carries is recorded in the pending entry as an observed fact.

### The admin surface: two routes under `/api/devices`

```
GET  /api/devices/pending          code -> {mac, client_id, board,
                                   firmware, first_seen, last_seen,
                                   expires_at}
POST /api/devices/pending/{code}   body {"agents": [...]}; binds the
                                   pending MAC and retires the code
```

Keyed by code because the code is what the operator has: the listing
answers "which of these is the thing on my desk" with the board
model and firmware version the OTA POST reported. The add-by-code
handler claims the code in the pending table (runtime state owned
by the serving app and shared with the sub-application at mount
time, under the table's claim lifecycle), then calls
`ConfigStore.bind_device`, the same repository method
`PUT /api/devices/{mac}` uses, so reference checking and
transactionality are inherited, not restated. An unknown, expired,
or already-consumed code is a 404 whose detail says to read the
code currently on the device's screen; a code mid-claim by a
concurrent request is a retryable refusal, never a second success.
A successful bind consumes the pending entry and answers with the
mac it bound; the device's next poll flips to 200.

`GET /api/devices` keeps its shape (bound devices only); the CLI
merges the two listings for display. Both routes join the committed
OpenAPI document under the existing regenerate-and-diff discipline.
One mechanical constraint is stated because Starlette matches
routes in registration order: `/devices/pending` must be
registered before `/devices/{mac}`, or the literal word `pending`
would enter MAC normalization and 400; a regression test pins that
the static path never reaches the MAC handler.

### The CLI: four new commands in the existing grammar

```
samtal-server config pending                      GET  /api/devices/pending
samtal-server config add-device CODE AGENT...     POST /api/devices/pending/CODE
samtal-server config ota-url                      contacts nothing
samtal-server config doctor [URL]                 GET on the OTA describe
```

`add-device` sits beside `bind-device` deliberately: bind-device
takes the MAC you know, add-device takes the code the device is
showing, and the help text says exactly that. `pending` lists codes
with their device facts.

`ota-url` produces the string to type into the portal's Advanced
tab, before any server runs: it reads the file config the way the
CLI already does (`load_file_config` for `server.onboarding`,
`public_url`, `websocket_url`, `host`/`port`, and the
`auth.secret_env` name), reads the secret from the environment, and
derives key and URL with the same functions the server uses; same
package, so there is nothing to drift and the issue's
derivation-equality table test is unnecessary. Output states its
origin the way the banner does when it had to guess. With
onboarding disabled it says so and points at `ota_path` without
printing the secret segment.

`doctor` diagnoses the two mistakes that cost the most time: a
`websocket_url` still saying `ws://` behind a TLS proxy, and a URL
that reaches some other service. It GETs the describe endpoint of
the given or derived URL and reports what a device would be told:
unreachable, not samtal-server (body does not parse as the describe
text), a `ws://` websocket URL behind an `https://` OTA URL, or
healthy with the websocket URL and server version named. It never
POSTs, so it mints no codes and touches no rate bound.

### The short route, mechanically

`onboarding.py` derives the key (uppercase canonical, matched
case-insensitively, since a phone keyboard will produce lowercase)
and builds a router with `/x/{key}/` GET and POST plus
`/x/{key}/activate` POST, delegating to the same handler functions
the legacy router uses. A non-matching key logs the warning with
attempted and correct key and returns the stock 404 body,
byte-identical to a route that never existed; the correct key
appears in the log line only, never in a response.
`server.onboarding.key`, when set, replaces the derivation (shape
validated: 8 base32 characters) and the example configs advise
injecting it from the environment
(`SAMTAL_SERVER__ONBOARDING__KEY`) rather than committing it, the
same posture as the `ota_path` segment today.

Mount matrix, asserted by tests: `ota_path` set and onboarding on
mounts both; `ota_path: null` unmounts the legacy route;
`onboarding.enabled: false` mounts no short route and issues no
codes; auth disabled mounts the keyless `/x/`; and `ota_path: null`
with onboarding disabled refuses the boot, because a server no
device can discover is a misconfiguration, not a choice. The
`ota_path` validator keeps its `/api/` reservation; `/x/` joins it
as reserved (a configured `ota_path` of `/x/...` would collide with
the onboarding router).

A missing trailing slash on POST must still reach the handler, and
not by redirect. This paragraph originally relied on Starlette's
`redirect_slashes` 307 preserving method and body; the hardware
checkpoint (2026-08-13, recorded in the implementation doc and
`docs/xiaozhi-notes.md`) superseded that: a factory board whose
portal saved the URL slashless surfaced the 307 as `code=307` on
screen and restart-looped, because the firmware's OTA client does
not follow redirects at all. The rule the finding leaves behind: a
device-facing endpoint serves every reachable spelling directly and
emits no redirect, because the firmware is not a browser. Both
spellings are served and asserted on the short path; the legacy
router's slashless dispatch lands with the milestone that reworked
its routes.

### The banner

At serve startup, after logging is configured, one log line names
the URL to type into the portal and where it came from:
`server.public_url` verbatim; else the origin of `websocket_url`
with the scheme mapped (`wss` to `https`, `ws` to `http`); else
`http://<host>:<port>` from the listen address, which is the guess
that must look like one (it names the fallback and that `0.0.0.0`
is not a reachable name). With onboarding enabled the URL is the
short one; with it disabled the line says configuration is served
at the configured `ota_path`, without quoting it, because the
legacy segment is a credential the logs must not carry (the derived
key is the recorded exception, the `ota_path` segment is not). The
GET describe handler prints the same URL line for the path it was
reached on. `public_url` is validated as an `http(s)` origin,
optionally with a path prefix, trailing slash normalized, and it
refuses userinfo, a query, or a fragment without quoting the
rejected value, the same no-echo posture the config validators
already hold. The `websocket_url` fallback constructs its origin
from the parsed hostname and port, never from the raw `netloc`,
because the current websocket validator accepts any
`ws://`/`wss://` string and a `user:password@host` must not reach
a log line through the banner. Sentinel no-leak assertions cover
the validation errors, the banner, and describe responses.

## Module layout

```
samtal_server/
    onboarding.py        key derivation, public URL resolution and
                         banner line, the short-path router, the
                         pending-device table, /activate handlers
    ota.py               check_version gains the activation object
                         for unbound devices; token issuance and
                         agent resolution move to the live view
    device/bindings.py   DeviceBindings: the live devices +
                         default_agent view with snapshot fallback
    device/session.py    agent resolution at connect consumes
                         DeviceBindings
    config/models.py     OnboardingConfig (enabled, key),
                         server.public_url, nullable ota_path,
                         reserved /x/ prefix
    config/api.py        the two pending routes; document regen
    config/cli.py        pending, add-device, ota-url, doctor
    config/writes.py     the binding notices (no-restart wording)
    app.py               mounts the short router, shares the pending
                         table with the sub-application
    main.py              emits the banner on serve
```

## Tests

Reuse, do not restate: the unit OTA suites (`test_ota.py`,
`test_ota_tokens.py`) keep guarding the response shape and the
allowlist semantics; the integration lane's `booted`/`running_app`
machinery and the `xiaozhi_sdk` simulator drive the end-to-end
cases; the OpenAPI drift test covers the new routes by regeneration.
One piece cannot be reused as is: `booted` seeds a scratch database
inside a `TemporaryDirectory` that is gone by the time the app
runs, and composes a config still pointing at the default database
directory, so a live write through the served app would address the
wrong database. The lane gains a fixture variant that keeps one
database directory alive for the app's lifetime, composes
`server.database.dir` to it, and can boot a second app from the
same directory afterward, which is what the restart assertion
needs.

New coverage, by milestone:

- **Unit, M1**: key derivation vectors (secret and label to key,
  case-insensitive match, `onboarding.key` override and its shape
  refusal); the mount matrix including the two boot refusals; the
  byte-identical body between short and legacy paths; the 404 hint
  logged with nothing about the correct key in any response; the
  trailing-slash 307 preserving POST bodies on both paths; banner
  source selection naming which origin it used; the `public_url`
  refusals (userinfo, query, fragment) without the value echoed,
  and a sentinel-userinfo `websocket_url` never reaching the
  banner or describe; the describe line.
- **Unit, M2**: a bind through the repository observed by the next
  OTA check and websocket connect on the same app with no rebuild;
  delete stopping issuance; the unloaded-agent filter with its
  distinct log line; the database-failure fallback logging and
  resolving from the snapshot; the contention case, a held write
  lock while lookups and the loop stay live; the changed write
  notices.
- **Unit, M3**: activation object contents for an unbound device
  (code, challenge equals MAC, message layout, empty token beside
  it) and its absence for bound devices, for unknown devices under
  a configured default agent (the upgrade regression: token, no
  activation), and when onboarding is off;
  the bound-but-unloaded state (no code, no token, the
  restart-naming log line, `/activate` staying 202) and both
  add-by-code notices;
  expiry, re-issue, code uniqueness, the cap and the global mint
  budget each answering with today's behavior plus their own
  warning, driven through the injected clock; `/activate` 202/200
  on both routers; the
  version-2 checks (bad body, unknown algorithm, challenge
  mismatch) each refused with a distinct reason; add-by-code
  through the API including unknown/expired code wording and
  reference-check inheritance; the claim races (two concurrent
  claims of one code yield one success and one retryable refusal,
  a failed repository write releases the reservation, issuance
  races expiry, listing races mutation); the route-order
  regression (`GET /api/devices/pending` never enters MAC
  normalization); codes absent from responses they do not belong
  in.
- **Integration, M3**: the firmware's activation loop simulated
  over HTTP against a served app: OTA check yields a code,
  `/activate` answers 202, add-by-code lands, `/activate` answers
  200, the next OTA check yields a real token and no activation
  object, and a whole conversation then runs with the simulator's
  `ota_url` pointed at the short path. Then the restart assertion
  from the issue's verification list: boot a second app from the
  same database directory and the binding still applies with the
  onboarding URL unchanged.
- **Unit, M4**: `ota-url` output equals the server's derivation for
  the same file config and environment, offline; `doctor`'s four
  verdicts against canned responses; both commands' failure wording.
- The hardware items are milestone 5, recorded, never claimed from
  code.

## Risks and mitigations

- **Every merge publishes an image.** Each milestone leaves `main`
  releasable: M1 adds an alias route and keys with compatible
  defaults, M2 makes bindings live (a pure improvement with a
  documented notice change), M3 changes what an unbound device
  receives (the deliberate feature, changelogged), M4 is tooling and
  docs. No new mandatory environment variable is introduced.
- **The firmware-behavior claims rest on vendored source, not a
  spec.** The activation loop, the challenge requirement, and
  `timeout_ms` semantics come from reading `ota.cc`,
  `application.cc` and the manager-api. Mitigation: the hardware
  checkpoint (M5) validates the whole ceremony on the factory-
  firmware board, and the load-bearing unknown, whether retyping
  the OTA URL in the portal preserves Wi-Fi provisioning, is
  checked on hardware before M1 merges; if it does not hold, the
  short URL still helps every new device but is not a recovery path
  for a rotated secret, and the docs say so.
- **The OTA and websocket paths gain a database read.** Bounded by
  call rate (boot check-ins, activation bursts, connects), run off
  the event loop through the boot-created read engine with no
  migration check, unable to block on writers under WAL, and
  covered by the snapshot fallback on failure; the component is
  the single seam if it ever needs a cache.
- **Probing mints codes.** The pending table caps at 128, one live
  code per MAC, entries expire in 10 minutes, minting is bounded
  to 30 codes per sliding 10-minute window, and both bounds answer
  with today's silence plus a warning, so an outsider who already
  found the key can at worst fill a table the operator can read;
  binding still requires the API token and a code read off a
  physical screen.
- **The derived key lands in logs by design.** Recorded trade from
  the issue, restated in the docs: it is a deployment-scoped path
  segment, deliberately printed so a typo diagnoses itself; device
  tokens stay out of logs, asserted as today, and the legacy
  `ota_path` segment is never printed.
- **The stale bytecode trap** (AGENTS.md) during manual runs:
  `PYTHONDONTWRITEBYTECODE=1` outside pytest, and suspect the cache
  before the code when a result contradicts the source.

## Open questions deferred beyond this issue

- A slim redistribution of the CLI so `uvx` resolves light
  dependencies; packaging only, same code.
- Per-board assets bundles to flip a factory board's language after
  onboarding (`self.assets.set_download_url`), the follow-up the
  issue names.
- Whether `GET /api/devices` should merge pending devices once a UI
  exists; the CLI merges client-side meanwhile.

## Plan review round

One external review of the plan as first committed (b174c4c): codex
CLI 0.147.0, model gpt-5.6-sol, read-only against this repository
with the issue #40 body supplied, 2026-08-12. Verdict: not ready,
naming the version-2 requirement, the hot database reads, and the
pending-state lifecycle as the blockers. Findings as received,
condensed; each carries its resolution once the amendment
addressing it lands.

1. **P1: version-2 activation contradicts a settled requirement and
   cannot work as described.** The issue requires verifying the
   HMAC before 200; the plan replaces verification with checks of
   the public algorithm and challenge, a fourth deviation beyond
   the three approved ones, and not authentication. The plan also
   retires the pending entry on binding, so the challenge is gone
   when the subsequent version-2 poll arrives. Say how per-device
   keys would be provisioned and verified, or declare version 2
   unsupported; either way this needs an explicit issue-author
   decision.
   *Resolution*: put to the issue's author on 2026-08-12 and
   decided: the code ceremony governs both versions, recorded in
   the plan as a fourth author-approved deviation with its
   rationale (a 200 carries no secret, and the token only ever
   arrives over the key-protected OTA path, so an unverifiable
   HMAC adds nothing enforceable); the registry and unsupported
   alternatives are recorded as considered and declined. The
   challenge check is scoped to the pending state, which also
   fixes the mechanical bug: once the MAC is bound, `/activate`
   answers 200 with nothing left to check.
2. **P1: synchronous database work would block the server event
   loop.** The live view opens the database per lookup, but OTA
   and session resolution run in async handlers, and
   `open_database()` runs an Alembic check inside
   `BEGIN IMMEDIATE` with a 10-second busy timeout; the API
   precedent is inapplicable because its handlers run on the
   threadpool. Introduce a nonblocking read seam that never runs
   migrations on device paths, and prove a locked database cannot
   stall conversations or the loop.
   *Resolution*: `DeviceBindings` now holds a read engine created
   at app build, after boot has already migrated, and disposed in
   the lifespan; lookups are awaited off the event loop and use
   ordinary deferred read transactions, which under WAL take no
   write lock and do not block on writers, and no migration check
   runs on any device path. The scoped exception to the "nothing
   after boot reads the database" contract is stated where the
   contract lives, and the M2 coverage gains the contention test
   holding a real write lock while lookups and the loop stay
   live.
3. **P1: the pending table has no concurrency or single-consumer
   design.** OTA handlers mutate pending state on the event loop
   while API handlers run on the threadpool; two concurrent
   add-by-code requests can both resolve one code and both report
   success, and expiry, uniqueness, listing and re-issue are
   multi-step. Define an atomic claim lifecycle with rollback and
   race tests.
   *Resolution*: the table is one mutex-guarded structure with a
   live, reserved, consumed lifecycle: a claim reserves the code
   under the lock before the database write, a failed write
   releases the reservation, success consumes it, and a
   concurrent claim of a reserved code is refused with retryable
   wording. The lock is held only for in-memory steps, never
   across the repository write. The M3 coverage races two claims,
   issuance against expiry, and listing against mutation.
4. **P1: fresh-deployment onboarding cannot satisfy the claimed
   no-restart ceremony.** A first start loads an empty snapshot;
   an agent written afterward is not loaded, so a device bound by
   code to it stays at 202 and cannot connect, while the plan
   promises add-by-code applies with no restart. Specify the
   initial workflow, differentiated notices, and the ordering in
   the deployment docs.
   *Resolution*: activation now gates on database truth (no
   binding row and no configured default agent) while token
   issuance keeps the loaded-agent filter, so a bound-but-unloaded
   device gets no new code, no token, a restart-naming log line
   and acknowledgement, and a 202 that flips to 200 at the restart
   that loads its agent. Add-by-code answers with the no-restart
   notice only when every bound agent is loaded and with the
   restart sentence otherwise; the onboarding docs and the
   deployment profile state the ordering (configure the domain,
   restart, then onboard devices restart-free); both notices and
   the bound-but-unloaded state are tested.
5. **P2: a capacity cap is not the required global issuance rate
   limit.** An attacker fills 128 slots and repeats every ten
   minutes; the issue requires per-MAC and global rate limits, and
   the tests cover only the cap.
   *Resolution*: code minting gains a global sliding-window budget
   (30 new codes per 10 minutes, a constant beside the others)
   independent of the live-entry cap; at exhaustion a new device
   gets today's silent response and a warning names the budget.
   One live code per MAC per TTL stays the per-MAC bound. The
   table takes an injected clock, and the budget, the cap, and
   expiry are tested deterministically.
6. **P2: the reused integration fixture deletes the database
   needed for live writes.** `booted` seeds a scratch database,
   exits the temporary directory, and composes a config that
   still points at the default directory, so live API writes
   would address the wrong database; the plan also omits the
   issue's binding-survives-restart assertion.
   *Resolution*: the tests section now names the fixture change (a
   variant keeping one database directory alive for the app's
   lifetime, composing `server.database.dir` to it, able to boot a
   second app from the same directory), and the M3 integration
   coverage ends with the restart assertion: bind through the API,
   boot a second app from the same directory, and the binding
   holds with the onboarding URL unchanged.
7. **P2: M2 would publish a false API contract.** The API
   description, the acknowledgement schema, and `_acknowledge()`
   all hardcode the restart sentence, and the plan postponed
   document regeneration to M3 while every merge publishes an
   image.
   *Resolution*: M2 now carries the API description, the
   acknowledgement schema documentation, and the `_acknowledge`
   change alongside the notice split, and regenerates
   `docs/reference/api-openapi.json` in the same milestone; the M2
   coverage asserts both notices.
8. **P2: "unbound" and "resolves to no loaded agent" are different
   states.** `agents_for_device` gives every unknown MAC the
   default agent, and the plan's activation gate would have minted
   codes for devices the default agent already covers.
   *Resolution*: folded into the finding-4 gate and made explicit:
   activation applies only when the database holds no binding row
   and no default agent, so a deployment with a default agent
   keeps today's behavior for unknown MACs (token, no activation
   object), pinned by an upgrade regression test in the M3
   coverage.
9. **P2: the public-URL fallback can log credentials.** The
   websocket URL validator accepts userinfo, and a naive origin
   derivation from `netloc` would log `user:password@host`; the
   API client already strips and refuses userinfo.
   *Resolution*: `public_url` refuses userinfo, query, and
   fragment without quoting the rejected value; the websocket
   fallback builds its origin from parsed hostname and port,
   never raw `netloc`; sentinel no-leak assertions cover the
   validation errors, the banner line, and describe responses.
10. **P2: the pending listing route can be shadowed by the
    existing MAC route.** Starlette matches in registration
    order, so `GET /api/devices/pending` registered after
    `GET /api/devices/{mac}` would enter MAC validation.
    *Resolution*: the plan now requires registering
    `/devices/pending` before `/devices/{mac}` and adds the
    regression test proving the literal path never reaches MAC
    normalization.
11. **P3: M1's "no behavior change" acceptance is false.**
    Onboarding defaults on, so M1 adds a reachable route and a
    new startup log line to every deployment.
    *Resolution*: the acceptance now says legacy OTA behavior is
    unchanged and names the short route and the banner as the two
    deliberate additions an upgrading deployment sees.

## Milestones

One PR per milestone, stacked, ticked with its PR number, each
linking to its section of the implementation doc when written.
Milestone 5 is documentation-only and may land directly on `main`
per repository convention.

- [x] **[M1: the short path and the banner](2026-08-12-device-onboarding-implementation.md#milestone-1-the-short-path-and-the-banner)**
  (PR #115, branch
  `feature/device-onboarding`): this plan; `OnboardingConfig`
  (`enabled`, `key`) and `server.public_url`; nullable `ota_path`
  with the `/x/` reservation and the null-plus-disabled boot
  refusal; key derivation and the short-path router with the
  404-and-hint mismatch branch; the startup banner and the describe
  line; both example configs updated in the same change; CHANGELOG.
  Accept: the unit M1 coverage above; both lanes and lint green;
  legacy OTA behavior unchanged for every configured deployment,
  with exactly two deliberate additions visible to an upgrading
  one, the short route answering beside the legacy path and the
  banner line at startup. Merge gate: the
  portal-retype hardware check recorded in `docs/xiaozhi-notes.md`.
- [x] **[M2: live device bindings](2026-08-12-device-onboarding-implementation.md#milestone-2-live-device-bindings)**
  (PR #116, branch
  `feature/device-onboarding-m2`): `DeviceBindings` consumed by the
  OTA handlers and the session layer; the no-restart write notices
  for device and default-agent writes, with the API description,
  the acknowledgement schema, and the regenerated OpenAPI document
  in the same change; CHANGELOG. Accept: the unit
  M2 coverage; a bind through the real API observed by a served
  app's next OTA check without restart in the integration lane;
  lanes green.
- [x] **[M3: the activation ceremony](2026-08-12-device-onboarding-implementation.md#milestone-3-the-activation-ceremony)**
  (PR #117, branch
  `feature/device-onboarding-m3`): the pending table; the
  activation object; `/activate` on both routers; the two
  `/api/devices/pending` routes and the regenerated OpenAPI
  document; `config pending` and `config add-device`; CHANGELOG.
  Accept: the unit and integration M3 coverage, including the whole
  conversation over the short path; lanes green.
- [ ] **M4: `ota-url`, `doctor`, and the onboarding docs** (branch
  `feature/device-onboarding-m4`): the two commands; the onboarding
  rewrite in the server README and the root README's device step;
  `config.deploy.example.yaml` guidance replacing the
  inject-the-segment advice with the derived key story; CHANGELOG.
  Accept: the unit M4 coverage; a reader can onboard a device from
  the docs alone; lanes green.
- [ ] **M5: the hardware checkpoint**: on the factory-firmware
  AMOLED-2.16 board, given only the short URL typed into the
  Advanced tab: the code is announced, `config add-device` binds
  it, and the device connects with no power cycle; the observed
  `Activation-Version`, poll cadence, and screen behavior recorded
  in `docs/xiaozhi-notes.md`; the already-provisioned Waveshare
  board still reaches its legacy `ota_path`; any boxes that cannot
  be checked are collected per the unverified-claims convention.
