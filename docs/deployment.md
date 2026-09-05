# Deploying vinga-server

A maintained explanation of the worked path from a published image to a
running deployment, in two lanes: Docker Compose and Kubernetes. It
changes when the artifacts under [`../deploy/`](../deploy/) change.

The contract both lanes implement is stated once, in the server
README's
[Running in a container](../vinga-server/README.md#running-in-a-container),
and that section stays the authority for it. This page links every one
of those facts rather than restating it, and spends its own words on
the two things the README deliberately does not carry: the artifacts
that implement the contract, and the order the pieces go in. Where this
page and the README disagree about a fact, the README is right and this
page is the bug.

What is not here: how to configure the server once it runs. Which
providers, which agents and which devices go in over the configuration
API, and the file half's every key has its own generated page,
`docs/reference/server-config.md`, rendered from the models. Onboarding
a board is
[Onboarding a device](../vinga-server/README.md#onboarding-a-device),
and the trial path that gets a first conversation running on a laptop
is the project README's [Getting Started](../README.md#getting-started).

## On this page

- [The contract](#the-contract): everything a deployment has to get
  right, with the section that owns each fact.
- [One replica](#one-replica): the topology, and the three places it
  shows up as a setting.
- [The Docker lane](#the-docker-lane): the production compose file, its
  refusals and its `.env`.
- [The Kubernetes lane](#the-kubernetes-lane): the manifests, the apply
  order, the routing boundary, and the provisioning transaction an
  upgrade reruns.
- [Sizing `/data`](#sizing-data): what is on the volume and what it
  costs to do without one.
- [Choosing a tag](#choosing-a-tag): which tags are immutable and which
  ones move.
- [Verifying a deployment](#verifying-a-deployment): the probes, the
  doctor, and a board.
- [Keeping this page honest](#keeping-this-page-honest): what fails when
  it goes stale.

## The contract

One process, one port, one volume and one database it does not run.
Everything below is stated by the server README, which is where a
disagreement is settled.

| What a deployment provides | The contract | Stated in |
| --- | --- | --- |
| **A port** | One, `server.port`, 8003 by default. The WebSocket, the OTA endpoint, the short onboarding route, the configuration API and both probes are all on it, because the server is a single ASGI application. Whatever routes that port decides the security boundary. | [Ports and topology](../vinga-server/README.md#ports-and-topology) |
| **A restart signal** | `/healthz`. This process is alive and serving its control surface; a draining server answers 200 deliberately, so a redeploy is not reported as a failure part way through. | [Limits](../vinga-server/README.md#limits) |
| **A traffic decision** | `/readyz`. This process may be handed a new device conversation: 200 `ok`, or 503 with one word (`draining`, `full`, `unavailable`). Point restart at the first and admission at the second. | [Limits](../vinga-server/README.md#limits) |
| **A shutdown budget** | SIGTERM drains: no new sessions, replies in flight finish speaking, sockets close with 1001, all inside `server.limits.drain_s` (20 s by default). Give whatever stops the container a grace period above it; both artifacts here use 30 s. | [Limits](../vinga-server/README.md#limits) |
| **A filesystem** | A read-only root filesystem works and is what both lanes run: add a writable `/tmp` and keep `/data`, the volume every engine caches into (`HOME` points there). Model weights are never baked into the image. | [Running in a container](../vinga-server/README.md#running-in-a-container) |
| **Two secrets** | `VINGA_AUTH_SECRET` signs the device tokens the OTA endpoint issues, and `VINGA_API_SECRET` gates the configuration API, which is always mounted and always gated. A third, `VINGA_MASTER_KEY`, is needed only once a credential is stored encrypted rather than named as an environment reference. | [The configuration API in a deployment](../vinga-server/README.md#the-configuration-api-in-a-deployment) |
| **A database** | `VINGA_DB_HOST`, `VINGA_DB_PORT`, `VINGA_DB_NAME`, `VINGA_DB_USER` and `VINGA_DB_PASSWORD`, or `VINGA_DB_URL` in place of all five. The database is yours to provide and neither lane provisions one. **The shipped default password is a loopback development convenience and never a deployment password.** | [The configuration database in a deployment](../vinga-server/README.md#the-configuration-database-in-a-deployment) |
| **A provisioned database** | [`../deploy/postgres-init.sql`](../deploy/postgres-init.sql), run once by a role that may create roles and schemas, and rerun before booting an image whose release moved the file. The server itself migrates both halves on boot, so there is no init command to forget beyond that one. | [The configuration database in a deployment](../vinga-server/README.md#the-configuration-database-in-a-deployment) |
| **The name it is reached by** | Behind a TLS-terminating proxy, `server.websocket_url` and `server.public_url` are set explicitly and `FORWARDED_ALLOW_IPS` names the proxy, never `*`. Get this wrong and boards fail at the handshake with every log line looking right. | [Behind a reverse proxy](../vinga-server/README.md#behind-a-reverse-proxy) |

Two things the contract deliberately leaves open. The database is
bring-your-own, so nothing under [`../deploy/`](../deploy/) starts one
or backs one up; `pg_dump` and the restore rehearsal are in the README
section above. And a configuration file is optional: every key of the
server half has a default and an environment override, so a container
with nothing mounted at `/config` serves on those, which is what both
lanes here do.

## One replica

One process, one replica, is the supported topology, and it is a
decision rather than an accident: everything a running server serves
from is state in its process, from the pending activation codes a new
board shows to the configuration generation an apply swaps in to the
session count the door enforces. The inventory behind it, and what
would have to be built before a second replica made sense, are in the
record
[One process, one replica is the supported topology](adr/2026-09-04-one-replica-is-the-supported-topology.md).

It shows up as a setting in three places, and all three are already
written into the artifacts:

- `replicas: 1` in
  [`../deploy/k8s/deployment.yaml`](../deploy/k8s/deployment.yaml), and
  never a `HorizontalPodAutoscaler` in front of it.
- `strategy: Recreate` in the same file, which is what keeps a rollout
  from briefly running two servers against one database. It is also
  what makes the upgrade order below hold, since the old pod is
  terminated and gone before the new image's pod, and therefore before
  that image's boot-time migrations, exists.
- `ReadWriteOnce` on
  [`../deploy/k8s/pvc.yaml`](../deploy/k8s/pvc.yaml), which says the
  same thing to a scheduler.

The two probes exist so an orchestrator can manage this one replica's
lifecycle, a rollout, a restart, a drain. They are not there so a
balancer can spread devices across several.

## The Docker lane

[`../deploy/docker-compose.production.yml`](../deploy/docker-compose.production.yml)
is one `vinga` service against a Postgres you already run. It is a
standalone file rather than a profile of the trial file at the
repository root, because the topology is not a variant: there is no
database service in it at all.

**What the trial file remains for.** The
[`../docker-compose.yml`](../docker-compose.yml) at the repository root
is untouched and stays exactly what it was, the trial and development
story: two containers including a database, loopback convenience
passwords, a moving image tag, and deliberately no restart policy so a
trial reads the refusal rather than a restart loop. Its database-only
invocation is the local development loop a checkout runs against, and
CI boots the whole of it against the image it builds. Nothing on this
page replaces it; a trial is still the fastest way to hear vinga
answer, and the project README's [Getting
Started](../README.md#getting-started) is that path.

### Every required value refuses

Each one carries its own `${VAR:?...}` guard, so an omission stops
`docker compose` before a container starts, naming the variable and
never printing a value. Eight of them: the image tag, the two secrets,
and the five database facts. The database facts are guarded rather than
defaulted for a specific reason, which is that the server's own
defaults describe an instance bound to loopback on a development
machine, so a deployment that reached them by leaving a line out would
be pointed at the wrong database or, worse, at the right one with a
password printed in this repository.

`env_file` is required beside the guards and is not the same promise: a
required env file only requires the file to exist and enforces nothing
about what is in it. It is there for what the guards cannot enumerate,
which is the provider credentials the domain configuration names by
variable and that differ with every deployment. Compose's project
directory for this file is `deploy/`, so the file it interpolates from
and the file mounted into the container's environment are one file,
`deploy/.env`, and there is nothing to keep in step.

### The `.env`

```bash
# The image, pinned. See "Choosing a tag" below.
VINGA_IMAGE=ghcr.io/rafacm/vinga-server:2026-09-05-1430

# The two secrets, each generated once with `openssl rand -hex 32` and
# kept wherever this deployment keeps its secrets.
VINGA_AUTH_SECRET=...
VINGA_API_SECRET=...

# The Postgres this deployment provides.
VINGA_DB_HOST=postgres.internal
VINGA_DB_PORT=5432
VINGA_DB_NAME=vinga
VINGA_DB_USER=vinga
VINGA_DB_PASSWORD=...

# And whatever else this server should find in its environment: the
# provider credentials the domain configuration names by variable, and
# VINGA_MASTER_KEY once a credential is stored encrypted rather than
# named as a reference.
ANTHROPIC_API_KEY=...
```

The file holds secrets, so create it with a `umask` that keeps it to
you, and keep the values wherever this deployment already keeps
secrets: the same place a restore would need them from.

### Running it

```bash
psql "$ADMIN_URL" -f deploy/postgres-init.sql
docker compose -f deploy/docker-compose.production.yml up -d --wait
```

The provisioning file first, because the server role is given schemas
it already owns rather than the right to create them. `--wait` gates on
the image's own `HEALTHCHECK`, which is `/healthz`; nothing here reads
`/readyz`, which is for an orchestrator deciding where to send traffic
while the server runs.

Stopping is `docker compose -f deploy/docker-compose.production.yml
down`, and the file's `stop_grace_period: 30s` is above `drain_s`, so
conversations in flight finish their sentence rather than being cut off
mid-reply.

Upgrading is the same two commands in the same order: rerun the
provisioning file (every statement in it is written to be run again, so
a rerun over a database that already has everything is a no-op), then
change `VINGA_IMAGE` and bring it up again.

### What is in front of the port

The file publishes `8003:8003`, which is the shape a trial of the
deployment file wants and not the shape a public deployment wants: one
port carries the configuration API along with the two device paths.
Behind a reverse proxy, publish to loopback (`127.0.0.1:8003:8003`) and
let the proxy route outward exactly the two paths the Kubernetes lane's
Ingress routes, for the reasons spelled out there. The proxy also needs
the three wiring values from the contract table, a read timeout above
the server's 20-second ping interval, and the WebSocket upgrade passed
through with response buffering off.

## The Kubernetes lane

[`../deploy/k8s/`](../deploy/k8s/) holds five plain committed
manifests and two Secret templates. No Helm and no kustomize: there is
one topology to describe and no variants to parameterize, so a template
engine would put a language between an operator and the handful of
facts they have to get right. Every placeholder in them says so where
it stands.

The two templates are named `secret.yaml.example` and
`secret-init.example` deliberately. A `.yaml` would ride along on
`kubectl apply -f deploy/k8s/`, and every value in them is a nonempty
string that satisfies every presence check the server makes, so the
deployment would come up protected by strings printed in this
repository. **Never fill a template in place and apply it.** Both real
Secrets are made with `kubectl create secret generic` from values kept
wherever this deployment keeps secrets.

### What is routed, and what is not

[`../deploy/k8s/ingress.yaml`](../deploy/k8s/ingress.yaml) routes
exactly two paths:

| Path | What it is |
| --- | --- |
| `/x/` | The short onboarding route, `/x/<key>/`. It is the OTA endpoint a fresh board is pointed at and the one address a person types into a captive portal. |
| `/xiaozhi/v1/` | The WebSocket a bound device holds open. |

And three things are deliberately not routed:

- **`/api/`**, the configuration surface. It holds the most authority
  of anything on the port and is protected by a bearer token that rides
  on every request, so the README's own first answer is not to route it
  at all. Administer it with a port forward for the length of a
  session:

  ```bash
  kubectl port-forward deploy/vinga 8003:8003
  ```

  and point the CLI at `http://127.0.0.1:8003/api`, which is the one
  address a plain `http://` URL is allowed for. Or run the CLI from
  inside the pod, where the token and the loopback address already are.
  The README's second answer, a separately restricted route of its own,
  is for a deployment that genuinely needs one and is a route this file
  does not write: see
  [The configuration API in a deployment](../vinga-server/README.md#the-configuration-api-in-a-deployment).

- **`/healthz` and `/readyz`**, which the kubelet reaches directly on
  the pod. Nothing outside the cluster has a use for them.

- **`/xiaozhi/ota/`**, the legacy OTA path. It is the token issuer and
  so cannot itself require a token, which makes it safely public only
  behind a long random segment. That is an agreement between two files
  and it is the operator's to keep: a deployment whose boards were
  provisioned on the legacy path adds a rule for
  `/xiaozhi/ota/<long-random-segment>/` and sets
  `VINGA_SERVER__OTA_PATH` to the same value in `deployment.yaml`.
  Boards onboarded through `/x/` need neither.

The Ingress is written against ingress-nginx, and its two annotations
are that controller's spellings of one fact: the proxy read and send
timeouts, raised to an hour because the 60-second default closes a
healthy conversation WebSocket that has simply been quiet between
utterances. An operator running another controller translates those two
timeouts rather than reverse-engineering what they were for. The server
pings every connected device every 20 seconds, so a truly dead peer is
found by the ping rather than by a timeout.

TLS is not an annotation but the resource's own contract. The
placeholder host `voice.example` appears three times, in the Ingress
rule, in the `spec.tls` entry and inside `deployment.yaml`'s two URL
values, and the agreement test holds all three to being one string,
because a certificate for one name and a WebSocket URL for another is a
board that fails at the handshake with nothing obviously wrong. Replace
it everywhere with the name this deployment is reached by.

### Apply order

Six steps, in this order, on a namespace of your choosing.

**1. The server's Secret.** From values kept wherever this deployment
keeps secrets, never by editing the template:

```bash
kubectl create secret generic vinga \
  --from-literal=VINGA_AUTH_SECRET="$(openssl rand -hex 32)" \
  --from-literal=VINGA_API_SECRET="$(openssl rand -hex 32)" \
  --from-literal=VINGA_DB_PASSWORD="$THE_DATABASE_PASSWORD"
```

[`../deploy/k8s/secret.yaml.example`](../deploy/k8s/secret.yaml.example)
says what each key is and carries `VINGA_MASTER_KEY` commented out,
with the command that generates one and the rule that a rotation adds a
key and retires none.

**2. The TLS certificate.** From a certificate you already hold:

```bash
kubectl create secret tls vinga-tls --cert=fullchain.pem --key=privkey.pem
```

Or have one issued in-cluster with
[cert-manager](https://cert-manager.io/), which is the open-source way
to do it and needs an annotation on the Ingress naming its issuer.
Either way the certificate is provisioned by the operator and is never
a committed file.

**3. The volume.**

```bash
kubectl apply -f deploy/k8s/pvc.yaml
```

Sizing it is [below](#sizing-data). The claim is `ReadWriteOnce` and
`storageClassName` is deliberately unset, so the cluster's default
class is used; naming one is the usual reason to touch that file.

**4. The provisioning transaction**, which is the next section. Nothing
after this point works against an unprovisioned database, and the
server refuses to boot rather than waiting.

**5. The server, its address and its route.**

```bash
kubectl apply -f deploy/k8s/deployment.yaml \
  -f deploy/k8s/service.yaml \
  -f deploy/k8s/ingress.yaml
```

`kubectl apply -f deploy/k8s/` applies the same set plus the PVC and
the Job, and is what a rerun looks like once the deployment exists. It
is safe: the templates are outside the glob by their suffix, and a
completed Job is not rerun by being applied again, which is exactly why
an upgrade deletes it first.

**6. Watch the first boot.** It is the slow one. The
`startupProbe` budget is five minutes (`failureThreshold: 60` at
`periodSeconds: 5`), which is what a cold volume needs while the
engines download and load their models, and liveness and readiness are
suspended entirely for as long as it runs. A pod restarting during the
first boot with no server error in its log is that budget being too
short for this deployment's link or model, and the threshold is the
number to raise.

### The provisioning transaction

Written as a transaction that can be rerun, rather than a one-time
incantation, because an upgrade reruns exactly this before the image
tag changes.

```bash
# 1. The SQL, as a ConfigMap built from the one committed copy. The
#    dry-run pipe is what makes it idempotent: a plain `create` fails
#    once the name exists.
kubectl create configmap postgres-init \
  --from-file=deploy/postgres-init.sql \
  --dry-run=client -o yaml | kubectl apply -f -

# 2. The Job's own credentials, which are not the server's. Creating
#    roles and schemas is an administrative right the serving pod
#    never has, and deployment.yaml references no key of this Secret.
kubectl create secret generic vinga-init \
  --from-literal=ADMIN_URL="$ADMIN_URL" \
  --from-literal=VINGA_DB_USER=vinga \
  --from-literal=VINGA_DB_RO_PASSWORD="$(openssl rand -hex 32)"

# 3. Delete the previous run and make a new one. Applying a completed
#    Job does not rerun it.
kubectl delete job vinga-postgres-init --ignore-not-found
kubectl apply -f deploy/k8s/job-postgres-init.yaml

# 4. Wait, with a timeout, and read the exit.
kubectl wait --for=condition=complete --timeout=5m job/vinga-postgres-init
```

`VINGA_DB_RO_PASSWORD` is required rather than defaulted: the SQL falls
back to the public string `vinga_ro` for the loopback compose case, so
omitting it installs a well-known password on a role that can read
every conversation this server ever recorded. `ADMIN_URL` is a
superuser or the database's owner with `CREATEROLE`, and it names the
database to provision, so a typo there provisions the wrong one.

**A failed Job stops the upgrade with the Deployment untouched.** That
is the property the order buys, and it is why the wait is a step rather
than a nicety: `kubectl wait` returns nonzero on a Job that did not
complete, the image tag has not changed yet, and the running server is
still the old one against the database it already understands. Read the
failure with `kubectl logs job/vinga-postgres-init`; `ON_ERROR_STOP=1`
means the last statement in that log is the one that failed, rather
than one error scrolling past in the middle of a file that exited 0.

The init Secret exists for the length of the transaction. Delete it
when the Job has completed, and make it again for the next upgrade's
rerun.

### Upgrading

Three steps, in this order, and the order is the contract's:

1. **Rerun the provisioning transaction** above. A release that moves
   [`../deploy/postgres-init.sql`](../deploy/postgres-init.sql), for
   instance by adding a schema, needs this before the new image starts,
   and the new image refuses to start without it rather than migrating
   half of itself.
2. **Change the image tag** in
   [`../deploy/k8s/deployment.yaml`](../deploy/k8s/deployment.yaml) to
   the new immutable tag and apply it.
3. **Let `Recreate` do the rollout.** It terminates the old pod and
   waits for it to be gone before the new pod exists, which is the
   no-overlap guarantee: an older process is never still serving while
   a newer one migrates a schema underneath it. The cost is an
   interruption for the length of the drain, which the one-replica
   record prices and accepts.

What an upgrade may assume about the database it finds, and what it may
not, is
[Database upgrades have a compatibility floor](adr/2026-08-20-database-upgrades-have-a-compatibility-floor.md):
the migration chains are re-cut rather than accumulated, so a
deployment that skips far enough behind the floor rebuilds rather than
upgrades. Read the [changelog](../CHANGELOG.md) between the tag you run
and the tag you are moving to before an upgrade, because that is where
a release that moves the provisioning file or forces a step says so.

## Sizing `/data`

`/data` is the volume every engine caches into, because `HOME` points
there. Three things are on it and one thing is not.

**Model weights.** The default image carries both local engines and no
weights: a whisper model is hundreds of megabytes and a Piper voice is
tens, and both download at the first conversation. This is the reason
the volume exists at all.

**Voice caches**, alongside them, in the same order of magnitude.

**Captures, when recording is provisioned.** `server.capture` is off by
default, and with it on the volume also holds a stereo WAV, a decision
track and a manifest per session. The budget is a setting rather than a
guess: the capture section names a total budget for the directory, a
per-session ceiling and a free-space floor beneath which a capture is
refused, and the keys and their defaults are in the generated
`docs/reference/server-config.md` rather than repeated here, so a
release that retunes them carries the numbers and this paragraph stays
true. Size the volume as weights plus voices plus that budget plus the
free-space floor, and set the floor high enough that the caches and the
captures cannot squeeze each other.

**Not on it:** the database, which is bring-your-own and outside this
cluster's business, and the configuration, which is in that database.
There is nothing beside the database left to back up.

[`../deploy/k8s/pvc.yaml`](../deploy/k8s/pvc.yaml) requests 10Gi, which
is the default image's engines and their caches with headroom and no
recording. Raise it before turning capture on, not after.

**Why a volume rather than an `emptyDir`.** An `emptyDir` works and is
the wrong trade twice. The weights download again on every reschedule,
so a routine eviction becomes minutes of cold start and the startup
probe's budget has to cover that download every time rather than once.
And every capture dies with the pod, which makes recording a setting
that appears to work and quietly keeps nothing.

## Choosing a tag

Two variants are published from one Dockerfile: the default carries
both local engines, and `slim` carries neither and is for a deployment
whose ASR and TTS both name external providers. The full comparison,
sizes included, is
[Choosing an image](../vinga-server/README.md#choosing-an-image).

**Pin an immutable tag.** `YYYY-MM-DD-HHmm` for the build, or
`sha-<revision>` for the commit. Neither is ever reused, so a rollback
names the build it wants, and `sha-<revision>` is the one that matches
across both variants: they are built by separate jobs that finish
minutes apart, so one commit can produce a dated tag and a `-slim`
dated tag a minute earlier.

**`latest` and `slim` are the moving pointers**, which makes them the
tags to pull when trying the server and the wrong ones to deploy from.
A moving pointer under a restart policy, or under a pod that
reschedules, is a deployment that upgrades itself at the worst possible
moment. The compose file refuses to start without a tag for exactly
this reason; the Deployment ships with `latest` as a placeholder that
is the one value in it that should not stay as committed.

## Verifying a deployment

Three checks, in the order that isolates a fault: the server answers,
the address it hands out is right, and a board gets on.

**1. Both probes answer.** From inside the cluster or through a port
forward:

```bash
curl -sf http://127.0.0.1:8003/healthz
curl -si http://127.0.0.1:8003/readyz
```

The first is liveness and answers 200 whenever the process is serving,
draining included. The second is admission: 200 `{"status": "ok"}`, or
503 carrying one word for why not. `draining` and `full` are
self-explanatory and transient; `unavailable` means an application that
was described and never served, which is a configuration fault rather
than a load one. A probe against a server that is still starting meets
a refused connection rather than any of those, because uvicorn binds
its listener only once the lifespan's startup has finished.

**2. `doctor`, from where a board would stand.** It answers the
question a board is about to ask, which is what the OTA endpoint hands
out:

```bash
kubectl exec deploy/vinga -- vinga-server doctor
```

It names the onboarding URL, the build serving it, and the WebSocket
URL devices are sent to. The fault it exists to catch is the one the
contract table's last row is about: a `ws://` WebSocket URL behind an
`https://` onboarding URL, which is a board failing at the handshake
with every other line looking right. In the Docker lane the same
command is `docker compose -f deploy/docker-compose.production.yml exec
vinga vinga-server doctor`.

**3. A device.** Onboarding is unchanged by either lane and is
[Onboarding a device](../vinga-server/README.md#onboarding-a-device):
`vinga info` prints the URL to type, the board's own guide in
[`devices/`](devices/README.md) says how that URL reaches it, and
`vinga events` is the server's account of what it then decided, turn by
turn. Nothing on this page changes any of that; what a deployment
changes is only which URL comes out, which is why step 2 is worth doing
before a board is involved at all.

## Keeping this page honest

Two mechanisms, and between them this page's facts are checked rather
than remembered.

**The artifacts are held to the server.**
`vinga-server/tests/unit/test_deploy_manifests.py` parses every
manifest under [`../deploy/k8s/`](../deploy/k8s/) and the production
compose file, and asserts the facts CI could not otherwise know: the
container port against `ServerConfig`'s own default, the probe paths
against the routes the application registers, both grace periods
against the `drain_s` default, the security context's user id against
the Dockerfile's, the Service's selector and target port against the
Deployment's labels and container port, the Ingress backends and the
three copies of the placeholder host, and the topology pins kubeconform
cannot judge (one replica, `Recreate`, `ReadWriteOnce`, the `/data`
mount). It also asserts that nothing under `deploy/k8s/*.yaml` is a
Secret or carries a placeholder credential. CI runs kubeconform over
the same directory, so the manifests are proven to speak Kubernetes and
proven to speak vinga.

**This page is held to its links.** `scripts/check_doc_links.py`, run
by the docs workflow, resolves every relative link and heading anchor
above, so a README section that is renamed out from under this page
fails a run rather than becoming a dead pointer. What it cannot check
is a fact restated instead of linked, which is the reason the contract
table is links.
