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

### PR review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, 2026-09-05,
against commit `88a099fb` of PR #402; the reviewer ran about 4m53s.
Two P1 and two P2, mergeable after fixes, and a fifth finding raised by
PR #403's own terra round about a file this milestone owns. All five
answered, one commit each.

1. **P1: the Ingress recreated at the proxy the leak the server refuses
   at the origin.** `serving.py` keeps uvicorn's access log off
   deliberately, because an access line is a request line and two of
   this server's request lines are things nothing may print: the OTA
   path carries the deployment's secret segment, and `/x/<key>/` is the
   key standing in front of the endpoint that issues device tokens
   (`onboarding/keys.py` will not even quote a rejected attempt, since
   a near miss of a real key is a hint at the real key). Both paths
   this Ingress routes are exactly those two, so a controller logging
   request lines puts back everything the server took out.

   *Fixed*: `nginx.ingress.kubernetes.io/enable-access-log: "false"`,
   with the header carrying the reason in the code's own terms and
   saying an operator on another controller owes the same property by
   whatever name it uses, including for any legacy OTA rule they add.
   The agreement test asserts the annotation is present and false.

2. **P1: `deployment.yaml`'s header advertised a bulk apply.** It said
   what is here "is applied with `kubectl apply -f deploy/k8s/`", which
   is the one command this set must not be applied with: a Job's pod
   template is immutable, so an apply over a completed Job fails rather
   than rerunning it, and a directory sweep would update the Deployment
   and then fail on the Job, leaving the new image booting against a
   database nothing had reprovisioned.

   *Fixed*: the header carries the ordered per-resource workflow
   instead, terse (secrets out of band, the PVC, the init transaction
   of idempotent ConfigMap apply, delete-then-create Job and
   `kubectl wait`, then the Deployment, Service and Ingress), matching
   the transaction the plan records; M2's guide is still where it is
   walked. `secret.yaml.example` keeps its own mention of the bulk
   apply, since that hazard is why its name ends in `.example`.

3. **P2: the host agreement was a union of four values.** Asserting a
   one-member set is green for every way the contract actually breaks:
   an empty `spec.tls` contributes nothing to a union, and so did a
   dropped `secretName`, an `http` public URL, a `ws` websocket URL and
   a websocket URL on the right host with the wrong path.

   *Fixed*: split in two and made specific. Exactly one rule with a
   non-empty host; exactly one `spec.tls` entry whose hosts are that
   host alone and whose `secretName` is both present and the one the
   header's `kubectl create secret tls` line names, read off the
   resource rather than typed twice; `https` and `wss` schemes; and the
   websocket path equal to `device.boundary.WEBSOCKET_PATH`.

4. **P2: the init Job's command was sampled rather than asserted.** The
   test proved `ADMIN_URL` was referenced in the env and never that the
   command uses it, so an argv that dropped `$(ADMIN_URL)` would stay
   green while psql fell back to a local socket.

   *Fixed*: the argv is asserted whole, in order and with nothing
   extra, with the file argument still what the mount is looked up from
   so the mount path is derived rather than restated. Two more
   agreements read off one place each: the ConfigMap's name comes from
   the volume and the header's `kubectl create configmap` line is held
   to naming it and to building it from the committed SQL, and the
   image is read from the trial compose file's `postgres` service
   rather than pinned again, since which Postgres this repository runs
   is one decision and the SQL's `\getenv` needs psql 15 or later.

5. **P1 (from PR #403's terra round, in this milestone's file): an
   unguarded `VINGA_DB_URL` could beat all five guarded database
   fields.** `db.connection_url` lets that one variable replace the
   discrete facts whole, and this file requires an env file for the
   provider credentials its guards cannot enumerate, so one line there
   would point the container at another database with every guard
   beside it satisfied by values nothing then reads. The trial file has
   prevented exactly this since it was written; the production file did
   not.

   *Fixed*: pinned `VINGA_DB_URL: ""` rather than guarded, because this
   lane's convention is deliberately the five discrete fields and a
   deployment whose convention is one connection string writes its own
   file. Empty rather than absent, since absent is what the env file
   could fill in. Verified against the real resolver rather than
   assumed: unset and empty both yield the discrete facts and a set one
   yields itself. The agreement test asserts the pin, reading the
   variable's name from `db.URL_ENV`.

**Template-guide alignment**, alongside the round and surfaced by the
M2 agent working on the guide. `secret-init.example`'s header carried
two shapes the guide had just corrected, and a template that disagrees
with the page walking it is worse than no template. Its
`kubectl create secret generic` is now piped through
`--dry-run=client -o yaml | kubectl apply -f -`, since the init
transaction is rerunnable and a retry after a failed Job would
otherwise die on AlreadyExists before reaching the thing that actually
failed. And `VINGA_DB_RO_PASSWORD` no longer appears as an inline
`$(openssl rand -hex 32)`: the SQL runs `ALTER ROLE ... WITH LOGIN
PASSWORD` on every run, so generating a fresh value each rerun rotates
the analyst role's password on every upgrade and silently invalidates
the credential in use. The header and the key's own comment both say
to generate it once and pass the same value after that.

Re-verified after the round: `uv run ruff check .` clean; the deploy
and census cases green; the new TLS, whole-command and `VINGA_DB_URL`
assertions each proven to bite by breaking a copied-aside value and
reading the named failure (`assert 0 == 1` for the emptied `spec.tls`,
the whole argv diff for the dropped connection, `assert 'ws' == 'wss'`
for the downgraded scheme, and `'<absent>' == ''` for the removed pin),
restored by copy-back and touched; the extracted kubeconform step body
rerun so the new annotation is schema-checked (5 valid, plus 1 each for
the templates); and the extracted compose step body rerun, with a
`VINGA_DB_URL` planted in the temporary env file confirmed not to reach
the rendered config with a value.

## M2: the guide and the convention

Delivered as planned: `docs/deployment.md`, the AGENTS.md convention
narrowing, the `docs/README.md` authority listing and Start here
pointer, the server README and root README cross-links, M2's CHANGELOG
entries, and the census manifest regenerated with the new page tracked.

### Deviations from the plan

**One, and it is about a file that is not in this branch's tree yet.**
Decision 7 says configuration links go to
`docs/reference/server-config.md`, #396's generated page. That page
lands with #396 M2, whose PR (#401) was still open when this milestone
was written, so the file does not exist on this branch. A markdown link
to it would fail `scripts/check_doc_links.py`, which resolves every
relative target and is the docs workflow's first step, and this
milestone's diff runs that workflow.

So the guide names the page as an inline code path,
`docs/reference/server-config.md`, twice: once in the head, saying
where "what can be configured" lives, and once in the `/data` sizing
section, saying that
the capture budget's keys and defaults are there rather than repeated in
prose. The decision's substance is kept, which is that the guide points
at that page instead of restating keys; only the link is deferred.

**The fallback to `config.example.yaml` was deliberately not taken.**
The plan permits it only if #396 M2 does not land first, and `gh pr view
401` reported the PR open rather than closed or failed, so the page is
still expected ahead of this one. Turning the two code paths into
markdown links is a one-line edit per site once #396 M2 is on `main`.

### Decisions taken inside the plan's latitude

- **The contract is nine rows rather than the seven facts the plan
  lists.** The plan names port, the two probes, SIGTERM with `drain_s`,
  the read-only rootfs plus tmpfs, the two env secrets, the five
  `VINGA_DB_*` variables and migrations on boot. The table splits the
  probes into two rows, because they are answered by two different
  README sentences and pointed at two different orchestrator slots, and
  it adds a row for the proxy wiring (`websocket_url`, `public_url`,
  `FORWARDED_ALLOW_IPS`), because both lanes are behind TLS termination
  and getting it wrong is the failure the guide is most likely to be
  read after.
- **The apply order is written as explicit paths, not
  `kubectl apply -f deploy/k8s/`.** A directory apply creates the
  Deployment and the provisioning Job together, and on a fresh database
  that boots a server against schemas that do not exist yet. The
  ordered form applies the PVC, runs the init transaction, then applies
  deployment, service and ingress; the guide then says the directory
  form is what a rerun looks like once the deployment exists, and why it
  is safe (the templates are outside the glob, a completed Job is not
  rerun by being applied again).
- **`vinga-server doctor` is run through `kubectl exec` rather than a
  port forward.** It answers what the OTA endpoint hands a board, which
  is derived from the server's own configuration, so running it where
  the server is, is what makes the answer the board's answer. The
  compose spelling is given beside it.
- **The verification section ends at the existing onboarding
  documentation rather than restating it.** Three checks (both probes,
  the doctor, a board), and the third is a paragraph of links: what a
  deployment changes about onboarding is only which URL comes out, which
  is what the second check already covers.

### Verification

- `python3 scripts/check_doc_links.py .`: 197 files, 0 failures. Every
  anchor the guide links into the server README, the project README and
  the two ADRs resolves.
- `uv run ruff check .`: clean, and unchanged, since M2 touches no
  Python.
- `uv run pytest tests/unit/test_deploy_manifests.py
  tests/unit/test_command_spellings.py -q`: green.
- The census manifest regenerated after the new page was tracked, in
  the same commit as the last documentation edit.
- The full unit lane was deliberately not run: M2 is a documentation
  diff, and the two suites above plus the link checker are what it can
  actually stale.

### PR review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-terra`, 2026-09-05,
against commit 472d936e of PR #403; the reviewer ran 2m02s. Verdict:
mergeable after one P1 and one P2, both answered below. Finding 1 has
an artifact half as well as a guide half, and the artifact half lands
on the M1 branch; what is recorded here is the guide's.

1. **P1: the Docker lane's `.env` walkthrough invites a stale
   `VINGA_DB_URL`.** The example block ended on a comment offering the
   file for "whatever else this server should find in its
   environment", and `VINGA_DB_URL` is exactly the variable that
   replaces all five discrete database facts whole when it is set. An
   env file written for a `psql` session on the host, or carried over
   from another deployment, would therefore send the container at a
   different database while the five guarded values above it looked
   authoritative and did nothing.

   *Resolution*: accepted in full. The comment now names what the env
   file is for (the provider credentials the guards cannot enumerate,
   and `VINGA_MASTER_KEY`) instead of offering it for anything, and a
   paragraph under the block states the lane's stance: the production
   compose file pins `VINGA_DB_URL` to the empty string in the
   container's environment, which the resolver reads exactly as unset,
   so a value in `deploy/.env` cannot reach the server at all; a
   deployment whose convention really is one connection string adapts
   the file deliberately, replacing the five guards with a guarded
   `VINGA_DB_URL` and removing the pin. The pin itself is the artifact
   half of this finding and lands on the M1 branch; it was not yet in
   this branch's tree when the paragraph was written, so the guide
   states the file's agreed behavior ahead of the file, and the two
   have to land together.

2. **P2: the init transaction's Secret step is not idempotent.** The
   ConfigMap step went through a dry-run pipe and the Secret step did
   not, so a plain `kubectl create secret generic` fails with
   `AlreadyExists`. A failed Job deliberately leaves the Secret
   behind, which means the retry the same section prescribes died on
   the step before the one that had actually failed.

   *Resolution*: accepted in full. The Secret goes through the same
   dry-run pipe, so applying it over itself is the same operation as
   creating it, and a paragraph after the block states rerunnability as
   the property every step is written for. The delete-after-success
   sentence is kept rather than dropped, because an administrative
   credential with no reader between transactions should not sit in the
   cluster, and it now says step 2 remakes the Secret whether or not a
   failed run left one behind.

   One thing was fixed beside the finding, in the same block.
   `VINGA_DB_RO_PASSWORD` was spelled `$(openssl rand -hex 32)` inline,
   which a rerunnable transaction turns into a bug: the SQL rotates the
   analyst role's password rather than failing on it, so a fresh random
   value on every upgrade would silently invalidate whatever credential
   the last analyst session was using. It is now a kept value, with the
   generate-once-and-keep instruction in the prose.

   Noted and not changed here: `deploy/k8s/secret-init.example`'s own
   header shows the same non-idempotent `create` and the same inline
   `openssl rand`, and it is an M1 artifact rather than this
   milestone's file.

### Verification after the review round

- `python3 scripts/check_doc_links.py .`: 197 files, 0 failures.
- `uv run pytest tests/unit/test_command_spellings.py -q`: green after
  the manifest was regenerated. Both fixes add lines above the guide's
  two quoted invocations, so the two rows moved from 534 and 536 to 565
  and 567; nothing was reclassified and no invocation changed.
- `uv run pytest tests/unit/test_deploy_manifests.py -q`: green, and
  untouched by this round, which changes no manifest.
- No em-dashes in the round's diff.
