# Cloud deployment guide: one contract, a Docker lane and a Kubernetes lane

Plan for [#397](https://github.com/rafacm/vinga/issues/397).
Implementation notes land in the companion
`2026-09-05-cloud-deployment-guide-implementation.md`, one section
per milestone, appended in the change that ticks the milestone here.

## Goal

Everything a deployment has to get right exists as an
orchestrator-agnostic contract in the server README's "Running in a
container" section, but there is no worked path from the published
image to a running deployment: the compose file is explicitly the
trial story and Kubernetes is unmentioned. This plan adds the guide
at `docs/deployment.md` (a maintained explanation that works the
contract into a Docker lane and a Kubernetes lane and links rather
than restates), the artifacts both lanes run (plain committed
manifests and a hardened production compose file under `deploy/`),
and the CI validation that keeps the manifests honest.

## The issue's decisions, restated

1. The naming convention is narrowed, not dropped: open-source
   orchestrators and tooling may be named; hosting providers stay
   unnamed. AGENTS.md's writing conventions are edited in the same
   change that introduces the guide.
2. One contract, two lanes. The README section remains the
   authority; the guide is a maintained explanation at
   `docs/deployment.md`, added to the authority list in
   `docs/README.md`; the k8s artifacts live under `deploy/`.
3. The Docker lane blesses a production compose shape (restart
   policy, pinned immutable tag, external Postgres), a stance change
   named in the changelog; the trial shape and the production shape
   remain distinct.
4. The k8s lane is plain committed manifests: Deployment (one
   replica, `strategy: Recreate`), Service, an Ingress example
   (WebSocket-aware, TLS-terminating, with the `FORWARDED_ALLOW_IPS`
   and `websocket_url`/`public_url` wiring), an RWO PVC for `/data`,
   a Secret template for the two secrets, and a one-shot Job running
   `postgres-init.sql`. No Helm, no kustomize.
5. CI holds the manifests with a kubeconform-style schema check;
   a kind-based boot smoke is deferred.
6. Postgres is bring-your-own in both lanes; no database manifests.
7. The server-half configuration reference is #396; the guide links
   `docs/reference/server-config.md` for "what can be configured"
   (#396 M1 is merged and M2 is in flight ahead of this plan's
   guide milestone; if M2 were somehow not to land first, the guide
   links `config.example.yaml` instead, per the issue).

## Open questions, resolved

**Production compose is a second, standalone file,
`deploy/docker-compose.production.yml`, not a profile of the
committed trial file.** Four reasons. First, the production topology
is not a variant of the trial topology but a different one: decision
6 removes the Postgres service entirely, so a profile would not be
selecting a subset of one topology, it would be hiding a second
topology inside a file whose header argues it exists because "the
topology is one thing". Second, the trial file's deliberate choices
are exactly wrong for production and cannot be shared: the loopback
convenience password defaults, the absent restart policy (chosen so
a trial reads the refusal), and the moving `latest` tag default.
Third, the interpolation trap the trial file itself records: compose
interpolates the whole file whatever profile is selected, so the
`${VAR:?}` refusals a production shape wants (a pinned tag, real
database facts) would break the profile-less development invocation
if they lived in the same file; in a standalone single-purpose file
those refusals are pure gain. Fourth, `deploy/` is already the home
of deployment artifacts (`postgres-init.sql`, and this plan's k8s
manifests), which is the relationship decision 2 names. The trial
file is untouched and stays the executable statement of the trial
shape that CI boots. The production file: one `vinga` service;
`image: ${VINGA_IMAGE:?...}` required, with the refusal text telling
the operator to pin an immutable `YYYY-MM-DD-HHmm` or
`sha-<revision>` tag; `restart: unless-stopped`; `env_file: .env`
required, carrying the two secrets and the `VINGA_DB_*` family
pointed at the operator's own Postgres, with no defaults for any of
them; `read_only: true` with `tmpfs: /tmp`; the `vinga-data` volume;
`stop_grace_period: 30s` above the drain; port 8003; the image's own
HEALTHCHECK left to do its job. Its header says what it is, what the
trial file remains for, and that the guide is the page that walks
it.

**The Ingress example is a committed manifest written against
ingress-nginx, named, with the guide translating.** The alternative,
a controller-neutral Ingress resource, would omit exactly the wiring
the issue names as the lane's point: WebSocket-awareness and the
proxy timings live in controller-specific annotations, and a neutral
resource validates identically under kubeconform while telling the
operator nothing. Naming one open-source controller is what decision
1 newly permits, and ingress-nginx is the one with the widest
install base. The committed `ingress.yaml` carries the ingress-nginx
annotations (the read and send timeouts that keep an idle WebSocket
open past the 60-second default, TLS termination, and the header
comment pairing it with `FORWARDED_ALLOW_IPS` and
`websocket_url`/`public_url`); the manifest's header and the guide
both say these annotations are ingress-nginx's spellings and what
each one is for, so an operator of another controller translates
facts rather than reverse-engineering intent.

**The deferred kind boot smoke becomes its own issue when M2
merges.** One sentence of scope exists (boot the freshly built image
in a kind cluster against the committed manifests); it is filed as a
follow-up issue by the coordinator after the guide lands, so the
deferral decision the issue records has an owner rather than a
memory.

## Artifact layout

- `deploy/k8s/` (new): `deployment.yaml` (one replica,
  `strategy: Recreate`, both probes pointed per the README's rule,
  restart at `/healthz` and admission at `/readyz`,
  `terminationGracePeriodSeconds: 30` above the drain, the read-only
  root filesystem with a `/tmp` emptyDir, `/data` from the PVC, env
  from the Secret and a plain ConfigMap-free env section for the
  `VINGA_DB_*` facts), `service.yaml`, `ingress.yaml` (above),
  `pvc.yaml` (RWO), `secret.example.yaml` (the two secrets plus
  `VINGA_DB_PASSWORD`, values as placeholders with the
  refuse-to-guess header), `job-postgres-init.yaml` (a one-shot
  `psql` Job that mounts a ConfigMap named in the guide). The SQL is
  NOT committed twice: the guide documents
  `kubectl create configmap postgres-init
  --from-file=deploy/postgres-init.sql` followed by applying the
  Job, so the one committed copy of the SQL stays the one home and
  the ConfigMap is derived from it at deploy time.
- `deploy/docker-compose.production.yml` (new): above.
- `docs/deployment.md` (new): the guide.
- `.github/workflows/vinga-server.yml`: the validation steps (below;
  `deploy/` is already on the workflow's paths list).
- `vinga-server/tests/unit/test_deploy_manifests.py` (new): the
  agreement test (below).
- `AGENTS.md`, `docs/README.md`, `vinga-server/README.md`,
  `README.md`, `CHANGELOG.md`: the convention edit, the authority
  listing, and the cross-links.

## CI validation

- **kubeconform over `deploy/k8s/`**, `-strict -summary`, in the
  server workflow's `unit` job (it is seconds of work and needs no
  service). The binary is a pinned release download with its sha256
  checked in the step, the same discipline every pinned tool in the
  workflow follows; the pin and checksum live in the step beside a
  comment naming where new checksums come from.
- **`docker compose config` over the production file**, with the
  required variables supplied as obviously-dummy values in the
  step's environment, so the file's syntax, its `:?` refusals and
  its schema are exercised without booting anything. The trial
  file's boot check in the `image` job is untouched.
- **The agreement test** (`test_deploy_manifests.py`, unit lane):
  parse `deploy/k8s/deployment.yaml` and
  `deploy/docker-compose.production.yml` and assert the facts CI
  cannot know are the code's: the container port equals the
  `ServerConfig` port default, the probe paths equal `HEALTH_PATH`
  and `READY_PATH`, the liveness probe is the health path and the
  readiness probe the ready path, and both grace periods are at
  least the `drain_s` default plus margin (the compose file's
  `stop_grace_period` and the Deployment's
  `terminationGracePeriodSeconds`). This is the issue's "the CI
  check is that agreement" made concrete: kubeconform proves the
  manifests speak Kubernetes, the agreement test proves they speak
  vinga.

## The guide's structure

`docs/deployment.md`, a maintained explanation (docs/README.md's
"Maintained maps and explanations" class): the contract as a table
with links into the README section that owns each fact (port 8003,
`/healthz` for restart and `/readyz` for traffic admission, SIGTERM
with `drain_s` inside the 30 s grace, read-only rootfs plus tmpfs,
the two env secrets, the five `VINGA_DB_*` variables, migrations on
boot); one replica everywhere it matters, citing the one-replica
ADR; the Docker lane walking the production compose file; the k8s
lane walking the manifests in apply order (secret, PVC, configmap
plus init Job against the operator's own Postgres, deployment,
service, ingress); `/data` sizing (model weights and voice caches
plus the capture budget when recording is provisioned, priced
against the emptyDir alternative: weights re-download on reschedule,
captures die with the pod); image tag guidance from the actual
scheme (pin `YYYY-MM-DD-HHmm` or `sha-<revision>`; `latest` and
`slim` are the moving pointers; upgrading is a new tag plus a
rollout, and the database compatibility floor ADR says what an
upgrade may assume); a closing verification section (both probes
answering, `vinga-server doctor` from where a board would stand,
onboarding a device); and the keep-in-sync note naming the
agreement test as the mechanism. Configuration links go to
`docs/reference/server-config.md` (#396) rather than restating
keys. No hosting provider is named anywhere; Kubernetes, Docker
Compose and ingress-nginx are, which is what the narrowed
convention permits.

## Documentation footprint

- `AGENTS.md`: the "Describe deployment generically" convention
  narrows to: open-source orchestrators and tooling may be named
  (Kubernetes, Docker Compose, an ingress controller); hosting
  providers stay unnamed. Edited in the guide's milestone, per
  decision 1.
- `docs/README.md`: `deployment.md` joins the "Maintained maps and
  explanations" authority list and the Start here section gains the
  pointer an operator arrives by.
- `vinga-server/README.md`: "Running in a container" gains one
  sentence sending deployments to the guide (the section stays the
  contract's authority; the guide is the worked path).
- `README.md` (root): the deployment pointer where the README hands
  off past the trial.
- `CHANGELOG.md`: M1's entry names the production compose shape as
  newly supported (the stance change decision 3 requires) and the
  committed manifests; M2's entry names the guide and the narrowed
  convention.
- The compose trial file needs no edit: its header's "trial and
  development story" sentence stays true.

## Tests

- The agreement test above (new, unit lane, no service needed;
  reads the two files with `yaml.safe_load`, plain parsing, no
  kubernetes client).
- The docs workflow's link checker covers `docs/deployment.md` and
  the README edits; the census sweeps the new files' quoted
  commands (`kubectl`, `docker compose`, `psql` lines), so the
  manifest is regenerated in the same commit as the last doc edit,
  with the new files git-added first.
- kubeconform and the compose config step are CI-side; locally the
  compose half is verifiable (`docker compose -f ... config` with
  dummy env) and kubeconform is run via its container image where
  available, stated honestly in the PR if not run locally.

## Risks

- **Manifests drifting from the image contract.** The agreement
  test pins port, probe paths and grace periods to the code's own
  constants; kubeconform pins the schema; the deferred kind smoke
  is the acknowledged residual and gets its follow-up issue.
- **The #393 capture reshape stales the sizing prose.** The guide
  prices `/data` in terms and links the capture keys through the
  generated reference rather than restating numbers, so #393's
  regeneration carries the facts and the guide's prose survives.
- **kubeconform supply chain.** Pinned version, pinned sha256,
  checked in the step; no floating action.
- **The `:?` refusals meeting CI.** The compose config step supplies
  dummy values explicitly; the trial file's lanes see no change.

## Milestones

- [ ] **M1: the artifacts and their checks.** `deploy/k8s/` (six
  manifests), `deploy/docker-compose.production.yml`, the
  kubeconform step with pinned binary and checksum, the
  `docker compose config` step with dummy env, the agreement test,
  and M1's CHANGELOG entry naming the stance change. Design
  footprint: no new Python module beyond the test; the manifests
  derive their facts from the Dockerfile and the models, and the
  agreement test is the seam that keeps it so. Documentation
  footprint: `CHANGELOG.md` and the new files' own headers; the
  guide is deliberately not in this milestone, so no index page
  changes yet.
- [ ] **M2: the guide and the convention.** `docs/deployment.md`;
  the AGENTS.md convention narrowing; the `docs/README.md`
  authority listing and Start here pointer; the server README and
  root README cross-links; M2's CHANGELOG entry; the census
  regenerated with the docs added first; the follow-up kind-smoke
  issue filed by the coordinator after merge. Design footprint:
  none in code. Documentation footprint: the five pages above, all
  named in "Documentation footprint".
