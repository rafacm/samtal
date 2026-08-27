# The compose file carries the server, and the quick start leads with it

**Date:** 2026-08-27 · **Issue:** #309

## Problem

The root README's Getting Started step 1 was seven commands (two
secret generations, `docker network create`, two `docker run`s, a
hand-rolled `pg_isready` loop, two exports) that together spelled out
exactly what a compose file exists to say once: two services, a
network, a health gate, an env contract. The repository already
committed a `docker-compose.yml`, but it carried the database half
alone, because #283 introduced it for the development loop. A
newcomer following the quick start got no benefit from it and
maintained the two-container topology by hand, and the README's
`docker run` lines and the compose file were two structures that had
to agree about the same env contract.

## Changes

- `docker-compose.yml` gains a `vinga` service behind a `server`
  compose profile. `docker compose up -d --wait` still starts the
  database alone and means exactly what it meant;
  `docker compose --profile server up -d --wait` is the whole trial.
  One file, because the topology is one thing.
- The service takes its image from `${VINGA_IMAGE:-ghcr.io/rafacm/vinga-server:latest}`,
  gates on `depends_on: postgres: condition: service_healthy` plus the
  image's own `HEALTHCHECK`, points `VINGA_DB_HOST` at the service
  name `postgres` and `VINGA_DB_PORT` at the container port `5432`,
  reads the DB name, user and password from the same `${VINGA_DB_*}`
  substitutions the database service reads, publishes `8003:8003` on
  every interface, keeps `/data` on a named `vinga-data` volume, and
  resolves `host.docker.internal` through `extra_hosts`.
- The two secrets arrive through a **required `env_file`**
  (`./.env`), not through `${VAR:?message}`. See decision 1.
- Root README step 1 is now two `curl`s and one command, and works
  with no checkout. Step 2 reads the token back out of the same
  `.env`; step 3 loses its Linux-only `--add-host` caveat because the
  compose file carries `extra_hosts`; step 5 is
  `docker compose exec vinga vinga-server config ota-url`.
- `vinga-server/README.md`: the Development section says the same file
  now carries the server behind a profile and that the development
  command is unchanged; **Running in a container** opens by saying
  which of the two stories it is (the single container a deployment
  composes) and links the other.
- `.github/workflows/vinga-server.yml` gains two steps. See decision 2.
- `AGENTS.md`'s CI paragraph updated: the `unit` job's contents, the
  `image` job's existence, and the two path entries
  (`docker-compose.yml`, `deploy/`) its list had been omitting.

## Key parameters

- Profile name: `server`. Selected with `--profile server` on `up` and
  `config`; **not needed on `exec`** once the stack is running.
- Image override: `VINGA_IMAGE`, defaulting to
  `ghcr.io/rafacm/vinga-server:latest`.
- Secrets file: `./.env`, `required: true`, relative to the compose
  file's directory. Already covered by `.gitignore`.
- Bind mount: `./deploy/postgres-init.sql`, unchanged and relative,
  which is why the README fetches with
  `curl --create-dirs -o deploy/postgres-init.sql` rather than
  flattening.
- Published ports: `127.0.0.1:${VINGA_DB_PORT:-5432}:5432` for the
  database (unchanged), `8003:8003` for the server. The asymmetry is
  deliberate and the file says so where the two differ.
- No restart policy on the server service, deliberately: it would turn
  a refusal to boot into a container restarting until `--wait` times
  out, and the refusal is what a trial needs to read.

## Decisions

### 1. The auth toggle, and how optional overrides pass through

**Decided:** the toggle is not in the compose file at all. Every
optional override, `VINGA_SERVER__AUTH__ENABLED=false` among them,
goes in the `.env` the service already reads as its `env_file`.

Both alternatives were tested against the real loader rather than
assumed.

**An `environment:` entry with an empty default is unsafe.** An empty
string is not "unset" to this loader; it is a parse failure:

```
$ VINGA_SERVER__AUTH__ENABLED= uv run python -c "from vinga_server.config.models import FileConfig; FileConfig()"
1 validation error for FileConfig
server.auth.enabled
  Input should be a valid boolean, unable to interpret input [type=bool_parsing, input_value='', input_type=str]
```

So `VINGA_SERVER__AUTH__ENABLED: ${VINGA_SERVER__AUTH__ENABLED:-}`
would refuse every boot that did not set it. A non-empty default
(`:-true`) avoids that and buys a second home for a fact the server
already owns (`AuthConfig.enabled: bool = True`), which is the shape
this project treats as a bug pending.

**A bare pass-through name in `environment:` is worse than useless
here.** Compose renders `- VINGA_SERVER__AUTH__ENABLED` as `null` when
the host has not set it, and a null `environment` entry *removes* the
value, including one the `env_file` set. Measured:

| `environment:` | `env_file` has it | host has it | container sees |
| --- | --- | --- | --- |
| bare name | yes | no | **nothing** |
| bare name | yes | yes | the host's |
| absent | yes | no | the file's |

A user who wrote the toggle into `.env` would have found it silently
dropped. So nothing optional is listed under `environment:`, and the
file says why beside the `env_file` entry.

**Proven end to end against the published image**, both directions:
with the line in `.env` the container reported
`VINGA_SERVER__AUTH__ENABLED=false`; with the line removed the
variable was *unset* rather than empty (`toggle=[unset]`) and the
server's own default applied, which the onboarding key confirms
(`config ota-url` printed `http://0.0.0.0:8003/x/` with auth off and
`http://0.0.0.0:8003/x/YT42NJSI/` with auth on).

### 2. Whether the image job's smoke lane adopts the file

**Decided:** the smoke lane keeps its hand-built topology; two
cheaper steps guard the compose file instead.

Full adoption was rejected on the evidence in the job: the smoke lane
is a two-variant matrix over three scenario databases
(`vinga_slim_boot`, `vinga_slim_refuse`, `vinga_smoke`), each with its
own mounted server-half YAML, its own seeding container run under a
different entrypoint, and one scenario that asserts a container
*fails*. The compose file models none of that and should not: it is
the trial topology, one server on one database with no config mounted.
Bending it to carry three scenarios would make the committed file
worse to serve a lane that is already green.

What landed instead:

- **`unit` job, `The compose file resolves both ways`**, before the
  install because it needs only a checkout. Three assertions: the
  profile-less invocation resolves with no secrets set and lists
  exactly `postgres`; the `server` profile *refuses* with no secrets;
  the `server` profile lists exactly `postgres vinga` once they are
  there. The first is the one genuinely at risk, because compose
  interpolates the whole file whatever profile is selected, so a
  `${VINGA_API_SECRET:?}` written into the server service would refuse
  the development loop too, and that is one character away at any time.
  This runs on pull requests, and `docker-compose.yml` and `deploy/**`
  are already in this workflow's path list.
- **`image` job, `Check the compose file boots the built image`**,
  gated to the default variant (the file is variant-agnostic, and the
  tag it defaults to is `latest`, which is the default variant). It
  points `VINGA_IMAGE` at `vinga-server:ci-amd64`, runs the quick
  start's own command, and asserts the revision the served `/healthz`
  reports is the one the image was built with. That is the only place
  anything proves the committed file still boots the artifact its
  default tag names. Placed before the arm64 build because it
  publishes the same port the seeded smoke container uses later, and
  torn down in a trap for the same reason.

Both steps delete the `.env` they write, on every exit path: the
server finds one by walking up from `vinga-server/`
(`find_dotenv(usecwd=True)`), so a leftover would feed every lane after
it.

### 3. What happens to the manual `docker run` sequence

**Decided:** the two-container hand-built topology dies; the
single-container `docker run` keeps its existing home in
`vinga-server/README.md` under **Running in a container**, and the
root README links there.

The network create, the second `docker run`, and the `pg_isready` loop
were the compose file's contents written out, so they were a second
spelling of one fact and are gone. What was never a duplicate is the
single container and what it needs (`/config`, `/data`, the
`VINGA_DB_*` family, the read-only root filesystem, `docker stop -t
30`): a deployment composes that into whatever it already runs, and
that section is its one home. It now opens by saying which of the two
it is, so a reader who arrived from the quick start knows why there
are two.

This keeps the rest of the corpus coherent without edits: the
`docker exec -i vinga …` lines in `vinga-server/README.md` and
`docs/reference/cli.md` all name the container that section starts,
and it still exists.

### 4. The step-5 exec spelling, and the sweep behind it

**Decided:** `docker compose exec vinga vinga-server config ota-url`,
with no `--profile` and no `-T`.

Both were measured against the running stack rather than assumed:

- **`--profile` is not needed.** `docker compose exec vinga …`
  succeeded against the profiled service while it was up. The profile
  decides what *starts*; `exec` reaches what is running.
- **`-T` is not needed** for these two commands. `config ota-url`
  succeeded with and without it, into a pipe. `-T` is the analogue of
  the old `docker exec -i` and remains what a command reading stdin
  wants (`config apply -f -`, `config … secret set`); neither of the
  two commands the root README runs reads stdin, so the short spelling
  is the honest one there.

The sweep behind the change, over every tracked Markdown file:

```
git grep -n "docker exec\|docker run\|vinga-db\|vinga-net\|docker compose\|--name vinga" -- '*.md'
```

| Hit | Disposition |
| --- | --- |
| `README.md` step 1 (`vinga-net`, `vinga-db`, two `docker run`s, the wait loop) | deleted, replaced by the compose pair |
| `README.md` step 3 (`--add-host` on Linux) | deleted; `extra_hosts` in the compose file |
| `README.md` step 5 (`docker exec -i vinga` twice) | `docker compose exec vinga` |
| `README.md` Project Layout, `docs/README.md` `deploy/` entry | reworded: the file runs the provisioning against the database it starts, either shape |
| `vinga-server/README.md` Development (`docker compose up -d --wait`) | unchanged, plus a paragraph on the profile |
| `vinga-server/README.md` Running in a container (`docker run -d --name vinga`) | kept; it is that section's own topology (decision 3) |
| `vinga-server/README.md` 2651–2704, 2815 (`docker exec -i vinga`) | kept; they name the container the line above starts |
| `vinga-server/README.md` smoke-lane prose (`docker run --rm --network …`) | kept; it describes the smoke lane, which did not change |
| `docs/reference/cli.md` 172, 178, 402 (`docker exec -i vinga`, `docker run -d --name vinga …`) | kept; the in-image CLI door and the deployment rebuild recipe, both against the container the server README starts |
| `THIRD_PARTY_LICENSES.md` 77 | prose about what the image carries, not an invocation |

## The secret refusal, verbatim

Two layers, neither of which can boot on a default.

**No `.env` at all**: compose refuses at config time, before any
container starts, in one line, exit 1. This is what the `server`
profile buys, and it is why `env_file` is used rather than
`${VINGA_API_SECRET:?…}`:

```
$ docker compose --profile server config
env file /path/to/vinga/.env not found: stat /path/to/vinga/.env: no such file or directory
```

**An `.env` present but incomplete** does *not* behave the same for the
two secrets, and the first version of this record said it did. The
correction is finding 6 of the review round below; what is true:

`VINGA_API_SECRET` **always** refuses. The configuration API is always
mounted and always behind its token, with deliberately no enabled
flag, so `config.api.api_token` reads the variable unconditionally.
Measured with device authentication *off* and no API secret: compose
starts the pair, `--wait` reports `container …-vinga-1 exited (1)`,
and the log carries

```
the configuration API is mounted at /api but VINGA_API_SECRET is not set.
Generate a token and put it in the environment:
  VINGA_API_SECRET=$(openssl rand -hex 32)
It is the bearer token every request to /api must carry, and it grants
everything the API can do, so keep it to a loopback connection or TLS.
```

`VINGA_AUTH_SECRET` refuses **only when device authentication is
enabled**, which is the default but is not what the quick start
selects. `auth.build_device_auth` returns `None` before it reads the
variable when `auth.enabled` is false. Measured with
`VINGA_SERVER__AUTH__ENABLED=false` and no `VINGA_AUTH_SECRET` at all:
both services **healthy**, exit 0. With device authentication on, the
same absence gives

```
device authentication is enabled but VINGA_AUTH_SECRET is not set.
Generate a secret and put it in the environment:
  VINGA_AUTH_SECRET=$(openssl rand -hex 32)
Or turn authentication off for a trial on a trusted network, with
server.auth.enabled: false in the config file, or
VINGA_SERVER__AUTH__ENABLED=false in the environment.
```

The reason to mint it on day one anyway is not a refusal: the secret
signs every device token and the key in the onboarding URL is derived
from it, so regenerating it later invalidates every token a device has
stored and moves that URL.

**Deviation from the issue, stated plainly.** The issue asked for
`${VAR:?message}`, "a one-line refusal naming the variable". The
measured behavior makes that impossible without breaking the
development loop, which the issue's Bounds hold fixed and whose
CAREFUL note anticipated this: compose interpolates the whole file at
config time whatever profile is selected, so
`${VINGA_API_SECRET:?…}` refuses `docker compose config` **and**
`docker compose up -d --wait` with no profile. Measured:

```
$ docker compose up -d --wait          # no profile, no secrets
error while interpolating services.vinga.environment.[]: required variable VINGA_API_SECRET is missing a value: set it
```

`env_file` is the only thing compose resolves for the selected
services alone (measured: a missing required env file is ignored
entirely when the service is not selected). The cost is that the
config-time refusal names the **file** rather than the variable; the
variable is named by the second layer, and by the README.

## Verification

Everything below was executed on this machine unless it says
otherwise. Docker Compose v5.4.0, Docker Desktop on macOS. Ports 5432
and 8003 were both free before the run (`lsof -nP -iTCP:5432
-iTCP:8003 -sTCP:LISTEN` returned nothing), so the committed defaults
were exercised as committed; isolated project names (`-p
vinga-309-dev`, `-p vinga-309-test`, `-p compose-smoke`) and `down -v`
were used throughout, and nothing was left running.

- **Dev loop, config.** `docker compose config` with no profile and no
  secrets resolves, and `--services` prints exactly `postgres`. The
  `vinga-data` volume does not even appear.
- **Dev loop, up.** `docker compose -p vinga-309-dev up -d --wait`
  with no profile and no secrets: postgres alone, Healthy, exit 0.
  `down -v` clean.
- **Refusal.** `docker compose --profile server config` with no `.env`
  fails with the one line quoted above, exit 1.
- **Fetch layout.** The README's two `curl` commands were run for real
  against `raw.githubusercontent.com/rafacm/vinga/main` into an empty
  directory (both files exist on `main` today, in their pre-change
  form) and produced exactly `./docker-compose.yml` and
  `./deploy/postgres-init.sql`. This branch's versions were then
  copied over them, because the new file 404s until this merges.
- **The quick start, end to end, against the published image.**
  `docker pull ghcr.io/rafacm/vinga-server:latest` (digest
  `sha256:97c3023…`, revision `9b44a76`), the README's `.env` block,
  then `docker compose --profile server up -d --wait`: both services
  Healthy in 11s, exit 0.
  - `curl http://127.0.0.1:8003/healthz` →
    `{"status":"ok","version":"0.1.0","revision":"9b44a76"}`.
  - `docker compose exec vinga vinga-server config ota-url` and
    `… doctor` both answer, without `--profile`.
  - The API answers on the token from `.env` and returns 401 without
    it.
  - `psql` inside the database container confirms the bind mount ran
    from the fetched layout: role `vinga_ro` and schemas `domain` and
    `conversations` all exist.
  - `getent hosts host.docker.internal` resolves inside the server
    container.
  - `down -v` clean.
- **Boot refusal.** With an empty `.env`, `--wait` reports
  `container …-vinga-1 exited (1)` and the log carries the refusal
  quoted above.
- **The auth-toggle proof**, both directions, as recorded in decision 1.
- **The CI steps, as stand-ins.** Both step scripts were run verbatim
  in a shell: the unit lane's three assertions pass and the `.env` is
  removed; the image job's script passes including the revision
  assertion (run with `VINGA_IMAGE` on the pulled `latest` and
  `REVISION=9b44a76`, which is that image's revision), and tears down.
  **The CI runs themselves are not verified**: the `image` job never
  runs on a pull request, so a `workflow_dispatch` is what proves it.
  All three workflow and compose files parse as YAML.
- **Lanes.** From `vinga-server/`: `uv run ruff check .` clean;
  `uv run pytest tests/unit -q` 4027 passed, 19 skipped;
  `uv run pytest tests/integration -q` 200 passed. The census
  (`tests/unit/test_command_spellings.py`) staled on line numbers
  alone and was regenerated with
  `uv run python -m tests.unit.test_command_spellings`; no spelling
  changed.
- **Links.** `python3 scripts/check_doc_links.py .` → 159 files, 0
  failures.

**Not verified, and why.** The published-image *pull path as the
README prints it* is proven, but the README's `curl` lines will not
fetch this change's compose file until it is on `main`; the layout
they produce is proven, the content they will carry is not. The two CI
steps have never run in CI. Nothing here was exercised on Linux, so
the `extra_hosts: host-gateway` entry is proven only on Docker
Desktop, where `host.docker.internal` already resolved; the entry is
there for the platform that was not tested.

**Noted, then changed.** With `VINGA_SERVER__AUTH__ENABLED=false`,
which the quick start sets, `config ota-url` prints a keyless
`http://…/x/` rather than the `http://…/x/AB2C4D5E/` the README's
example comment shows; the key is derived from the device-auth secret
and exists only while device authentication is on. The mismatch
predates this change and the URL works either way (a keyless `/x/`
serves the activation flow, verified: HTTP 200 with an activation
code), so the first version of this record left it alone as out of
scope. The review round's finding 6 landed on the same fact from the
other side, so step 5 now says it in half a sentence rather than
leaving a reader to discover it.

## Files modified

`docker-compose.yml`, `README.md`, `vinga-server/README.md`,
`docs/README.md`, `AGENTS.md`,
`.github/workflows/vinga-server.yml`,
`vinga-server/tests/unit/command-spellings.txt` (regenerated by
`uv run python -m tests.unit.test_command_spellings`, because this
change shifts tracked lines), `CHANGELOG.md`, this file.
