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
   *Resolution*, a26e00f: `server.websocket_url` refuses userinfo at
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
   *Resolution*, 137a33f, in the three places the reviewer named,
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
   *Resolution*, f2175b4: the router registers the slashless keyed
   routes itself, behind the same guard. The correct key gets the 307
   Starlette would have issued, query string included; a wrong one gets
   the same 404 body and headers as any unserved path, with no
   `Location`. Asserted for GET and POST, on the body and the headers.
4. **P1: a malformed attempted key was injected verbatim into logs.**
   The decoded path parameter went into the message and the structured
   fields, so `/x/AAAA%0ABBB/` forged a second log entry (the decoded
   parameter really does arrive holding a raw newline, checked by hand)
   and an oversized segment let a caller choose how long an entry was.
   *Resolution*, fa7f08a, with the rule stated exactly rather than
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
   *Resolution*, 48749f1: it goes through the same shared check, which
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
