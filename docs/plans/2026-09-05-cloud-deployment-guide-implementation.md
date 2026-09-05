# Cloud deployment guide: implementation notes

Companion to [`2026-09-05-cloud-deployment-guide.md`](2026-09-05-cloud-deployment-guide.md),
one section per milestone, appended in the change that ticks the
milestone there.

## M1: the artifacts and their checks

Delivered as planned: `deploy/k8s/` with five applicable manifests and
two non-applicable Secret templates, `deploy/docker-compose.production.yml`,
two new steps in the server workflow's `unit` job, the agreement test
at `vinga-server/tests/unit/test_deploy_manifests.py`, and M1's
CHANGELOG entry naming the stance change.

### Deviations from the plan

**One, and it is about kubeconform's file selection rather than about
the design.** The plan says the two `.example` templates are "passed by
explicit path so they are schema-checked without being apply-shaped".
Measured against kubeconform 0.8.0, that does not work: the tool
selects files by extension even when a path is named explicitly, so
`deploy/k8s/secret.yaml.example` and `deploy/k8s/secret-init.example`
are found to hold no resource at all and the run exits 0 having checked
nothing. The templates' names cannot change, because that suffix is
what keeps them out of `kubectl apply -f deploy/k8s/`, which is finding
9's whole point.

So the CI step feeds each template to kubeconform on stdin (`- <
"$template"`), which reads it whatever the file is called, and asserts
the resource count afterwards. The count assertion is the same defence
applied to the directory run: `Valid: 0` is a green exit code, verified
by running kubeconform against an empty directory, so the step derives
how many manifests it expects to have validated from the glob and fails
when the number it got is smaller.

**And one thing the local run could not see, found by the first CI
run.** The compose check passed locally and failed in CI 27 seconds
into the unit job, reporting that the file resolved with
`VINGA_DB_HOST` unset. Compose resolves an interpolation from the
process environment first and from the env file only after it, and the
unit job exports `VINGA_DB_HOST` and its four siblings at job level for
the Postgres service the lanes connect to. So a variable left out of
the temporary `deploy/.env` was still set in CI, its guard never fired,
and the negative arm of the check correctly reported a file that
refused nothing. The environment those five are absent from is a
developer's shell, which is why nothing local reproduced it until they
were exported by hand.

The fix is to take every required name off the process environment for
each compose invocation, the omission runs and the final full-set
resolution alike, so the temporary file is the only place a value can
come from. The `env -u` list is built from the same `required` list the
loop iterates rather than written out a second time, so a variable
added to the contract is scrubbed by the edit that starts guarding it.
The trial compose check above it is unaffected: the two secrets it
withholds are not among the job's exports.

Everything else follows the plan and its amendments as written,
including the delta re-review's three: the Job's complete
`ON_ERROR_STOP` command with the ConfigMap mount and the file argument
asserted, the Ingress's `spec.rules[].host` and matching `spec.tls`
entry on `voice.example` with the operator-provisioned `vinga-tls`
Secret, and the UID as the only identity fact asserted against the
Dockerfile.

### The kubeconform pin, and where its checksum came from

Pinned at **v0.8.0**, the current release, with the linux-amd64
tarball's sha256:

    9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883

Obtained two ways, which is why it is trusted: the tarball was
downloaded and `sha256sum` run over it locally, and the value compared
against the `CHECKSUMS` file the project publishes beside the release's
own assets. The two agree. The workflow step re-verifies the checksum
against the download before the binary is executed, and the step's
comment names the `CHECKSUMS` file as where a new version's value comes
from.

### Decisions taken inside the plan's latitude

- **`VINGA_SERVER__HOST` and `VINGA_SERVER__PORT` are pinned in the
  compose file and deliberately not in the Deployment.** The compose
  files pin them because a `.env` written for a host shell reaches into
  the container and would silently invalidate the port mapping, the
  image's healthcheck and the URL a device is told. A Deployment's env
  block is the whole of what its container gets, so the image's own
  defaults are already the only answer there and a second statement of
  them would be one more pair of numbers to keep in step.
- **`VINGA_MASTER_KEY` is in `secret.yaml.example`, commented out.** It
  is the third secret the server README names and the one a deployment
  storing an encrypted credential cannot do without, so leaving it
  unmentioned would hide it; leaving it live would make a placeholder
  Fernet key look required. Commented, with the generation command and
  the rotation rule beside it.
- **The init Job runs as uid 70.** `postgres:17-alpine` starts as root
  because its entrypoint expects to drop privileges before running a
  server, and nothing in this Job runs a server. The uid is the
  `postgres` user in that image, verified by running `id postgres` in
  it; the Debian-based tags use 999, which is why the number is
  commented rather than assumed.
- **The compose file declares `name: vinga`.** Without it compose
  derives the project name from the directory, so the deployment's
  volume would be `deploy_vinga-data` and its project would be called
  `deploy`.
- **`backoffLimit: 2` on the init Job.** The SQL is repeatable by
  construction, so a retry cannot corrupt anything; what it buys is a
  transient connection failure not becoming a failed upgrade, and what
  it cannot paper over is a SQL error, which `ON_ERROR_STOP=1` makes
  fail every attempt identically.

### Verification

- `uv run ruff check .`: clean.
- `uv run pytest tests/unit -q`: green, the 22 new cases included.
- kubeconform 0.8.0 run locally over `deploy/k8s/` and over both
  templates on stdin, `-strict`: 5 valid, 0 invalid, 0 errors, plus 1
  valid each for the templates. Strictness confirmed to bite by feeding
  it a Service with a misspelled field.
- The production compose file's guards proven locally with the same
  omission-loop-then-full-set procedure the CI step encodes: each of
  the eight required variables omitted in turn makes `docker compose
  config` fail with that variable's own value-free message, and the
  complete dummy set resolves. Re-run afterwards with the unit job's
  own five `VINGA_DB_*` exports in the environment, which is the shape
  that broke it: the unfixed step body fails there exactly as CI did,
  and the fixed one passes with all eight refusals still firing.
- The agreement test proven to bite: six values were broken in a
  copied-aside `deployment.yaml` (replicas, `runAsUser`,
  `terminationGracePeriodSeconds`, the startup `failureThreshold`, the
  readiness path and the emptyDir medium) and each produced its own
  named failure; the file was restored by copy-back and touched.
