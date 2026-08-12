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
constructed at boot with the composed snapshot and the database
directory. Its `agents_for(mac)` opens the database, reads the
`devices` rows and `default_agent`, and resolves with the same rule
`Config.agents_for_device` applies today (bound list, else default
agent, else nothing). Three deliberate properties:

- **Per-lookup read, no cache, no cross-app wiring.** The
  alternative (refresh callbacks from the API sub-application into
  the parent app) is less code on the hot path but couples the two
  apps and misses `--local` writes entirely. A per-lookup open is
  the API's own per-request pattern, and the call sites are
  low-rate: OTA check-ins (boot plus the activation loop's
  re-checks), `/activate` polls (3 s bursts per pending device), and
  websocket connects. If a future fleet makes this measurable the
  change is local to `DeviceBindings`.
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
place, and the CLI prints it verbatim as today. Live sessions are
deliberately untouched by a binding change: deleting a binding stops
the next token issuance and the next connection, not a conversation
in flight, the same line the session-boundary work drew.

### Activation gates on being unbound, with onboarding on

The OTA response carries `activation` exactly when
`server.onboarding.enabled` is true and the presented MAC resolves
to no loaded agent. Auth state does not gate it: with `auth.enabled`
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
activation object) and a warning names the cap. One live code per
MAC bounds per-device issuance; the cap bounds the total. These are
constants in `onboarding.py`, not configuration: nobody has field
evidence to tune them by, and a knob nobody can reason about is
schema noise. If the field says otherwise they graduate to config
then.

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
the live view: 200 when bound, 202 otherwise, including for MACs
with no pending entry (a restart loses the table; the device's loop
re-checks OTA and gets a fresh code, and answering 202 meanwhile
matches upstream's "keep waiting").

The issue says a version-2 poll's HMAC is verified before 200. It
cannot be, and the plan records the honest resolution rather than
pretending: the HMAC is computed with an eFuse-burned per-device key
that upstream's cloud knows from vendor registration and samtal has
no copy of. What the server can check, it does: a version-2 body
must parse, must name a known algorithm, and must echo the challenge
this server issued for that MAC; a mismatch is refused with 202 and
a distinct log reason, because a poll answering someone else's
challenge is not evidence of anything. Beyond that, version 1 and
version 2 rest on the same authority the issue itself names:
possession of the code is possession of the device's screen. The
serial number a version-2 body carries is recorded in the pending
entry as an observed fact.

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
handler looks the code up in the pending table (runtime state owned
by the serving app and shared with the sub-application at mount
time), then calls `ConfigStore.bind_device`, the same repository
method `PUT /api/devices/{mac}` uses, so reference checking and
transactionality are inherited, not restated. An unknown, expired,
or already-used code is a 404 whose detail says to read the code
currently on the device's screen. A successful bind removes the
pending entry and answers with the mac it bound and the
no-restart-needed notice; the device's next poll flips to 200.

`GET /api/devices` keeps its shape (bound devices only); the CLI
merges the two listings for display. Both routes join the committed
OpenAPI document under the existing regenerate-and-diff discipline.

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

A missing trailing slash on POST must still reach the handler:
Starlette's `redirect_slashes` answers 307, which preserves method
and body, but a captive portal may strip the slash and this is
asserted, not assumed, for both the short and the legacy path.

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
optionally with a path prefix, trailing slash normalized.

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

New coverage, by milestone:

- **Unit, M1**: key derivation vectors (secret and label to key,
  case-insensitive match, `onboarding.key` override and its shape
  refusal); the mount matrix including the two boot refusals; the
  byte-identical body between short and legacy paths; the 404 hint
  logged with nothing about the correct key in any response; the
  trailing-slash 307 preserving POST bodies on both paths; banner
  source selection naming which origin it used; the describe line.
- **Unit, M2**: a bind through the repository observed by the next
  OTA check and websocket connect on the same app with no rebuild;
  delete stopping issuance; the unloaded-agent filter with its
  distinct log line; the database-failure fallback logging and
  resolving from the snapshot; the changed write notices.
- **Unit, M3**: activation object contents for an unbound device
  (code, challenge equals MAC, message layout, empty token beside
  it) and its absence for bound devices and when onboarding is off;
  expiry, re-issue, code uniqueness, the cap answering with today's
  behavior plus a warning; `/activate` 202/200 on both routers; the
  version-2 checks (bad body, unknown algorithm, challenge
  mismatch) each refused with a distinct reason; add-by-code
  through the API including unknown/expired code wording and
  reference-check inheritance; codes absent from responses they do
  not belong in.
- **Integration, M3**: the firmware's activation loop simulated
  over HTTP against a served app: OTA check yields a code,
  `/activate` answers 202, add-by-code lands, `/activate` answers
  200, the next OTA check yields a real token and no activation
  object, and a whole conversation then runs with the simulator's
  `ota_url` pointed at the short path.
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
  call rate (boot check-ins, activation bursts, connects) and by
  the snapshot fallback on failure; the per-lookup open is the
  API's own accepted pattern, and the component is the single seam
  if it ever needs a cache.
- **Probing mints codes.** The pending table caps at 128, one live
  code per MAC, entries expire in 10 minutes, and the cap answers
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

## Milestones

One PR per milestone, stacked, ticked with its PR number, each
linking to its section of the implementation doc when written.
Milestone 5 is documentation-only and may land directly on `main`
per repository convention.

- [ ] **M1: the short path and the banner** (branch
  `feature/device-onboarding`): this plan; `OnboardingConfig`
  (`enabled`, `key`) and `server.public_url`; nullable `ota_path`
  with the `/x/` reservation and the null-plus-disabled boot
  refusal; key derivation and the short-path router with the
  404-and-hint mismatch branch; the startup banner and the describe
  line; both example configs updated in the same change; CHANGELOG.
  Accept: the unit M1 coverage above; both lanes and lint green; no
  behavior change for any configured deployment. Merge gate: the
  portal-retype hardware check recorded in `docs/xiaozhi-notes.md`.
- [ ] **M2: live device bindings** (branch
  `feature/device-onboarding-m2`): `DeviceBindings` consumed by the
  OTA handlers and the session layer; the no-restart write notices
  for device and default-agent writes; CHANGELOG. Accept: the unit
  M2 coverage; a bind through the real API observed by a served
  app's next OTA check without restart in the integration lane;
  lanes green.
- [ ] **M3: the activation ceremony** (branch
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
