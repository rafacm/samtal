"""The committed deployment artifacts, held to the server they deploy.

`deploy/k8s/` and `deploy/docker-compose.production.yml` are the worked
paths from the published image to a running deployment, and everything
in them is a fact about this server: which port it listens on, which
path answers liveness and which answers admission, how long a drain
takes, which user id the image runs as, which paths may face the
internet. None of those facts lives in a manifest. They live in
`config/models.py`, in `device/boundary.py` and in the Dockerfile, and
a manifest is a copy that can go stale in silence: a schema validator
proves the YAML is Kubernetes, and a Kubernetes-shaped manifest pointing
liveness at a path this server does not serve validates perfectly and
restarts a healthy pod forever.

So this reads the artifacts and derives every expected value from the
code, never from a constant restated here. Three families:

- **Code agreement.** Port, probe paths, the startup budget, both grace
  periods, the image's user id, the memory-backed /tmp, and the init
  Job's credential contract.
- **The network path.** Labels to selector, `targetPort` to container
  port, Ingress backends to the Service, and the one host the Ingress
  rule, the TLS entry and the two URL placeholders all have to be.
- **The topology pins** a schema validator cannot judge: one replica,
  `Recreate`, the PVC's access mode and the `/data` mount.

Plus the applicable-manifest guard, which is about what
`kubectl apply -f deploy/k8s/` may install: no Secret and no credential
value, with the two templates outside that glob by construction.

Plain `yaml.safe_load` throughout and no Kubernetes client: what is
being checked is the text somebody applies.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from vinga_server.config.models import (
    API_MOUNT_PATH,
    HEALTH_PATH,
    ONBOARDING_MOUNT_PATH,
    READY_PATH,
    ServerConfig,
)
from vinga_server.device.boundary import WEBSOCKET_PATH

REPO = Path(__file__).resolve().parents[3]
K8S = REPO / "deploy" / "k8s"
COMPOSE = REPO / "deploy" / "docker-compose.production.yml"
DOCKERFILE = REPO / "vinga-server" / "Dockerfile"
INIT_SQL = REPO / "deploy" / "postgres-init.sql"

# The trial file at the repository root, read here for one fact: which
# Postgres image this repository has settled on. The init Job runs psql
# out of that same image, so the version the SQL's `\getenv` needs is
# one decision with one home rather than a pin in every file that has to
# talk to a database.
TRIAL_COMPOSE = REPO / "docker-compose.yml"

# What `kubectl apply -f deploy/k8s/` installs, which is the glob the
# guard at the foot of this file is about.
APPLY_GLOB = "*.yaml"

# And the two templates, which carry placeholder credentials and are
# named so that the glob above cannot reach them.
TEMPLATES = ("secret.yaml.example", "secret-init.example")

# What a template's unset value is spelled, so a filled-in copy and an
# untouched one are told apart by a string nobody types by accident.
PLACEHOLDER = "REPLACE-ME"

# The floor the startup budget has to clear, stated here because it is a
# judgement rather than a fact the code carries: a first boot downloads
# and loads models before uvicorn binds a listener, so three minutes is
# the least that is not obviously too short. The manifest's own comment
# says when to raise the budget above it.
STARTUP_FLOOR_S = 180

# How far above `drain_s` a grace period has to sit. The drain is what
# the server spends letting conversations finish speaking; the margin is
# for everything after it, which is uvicorn's own shutdown and the
# process exiting.
GRACE_MARGIN_S = 5

# Where the Ingress says not to log request lines. ingress-nginx's own
# spelling, since the manifest is written against that controller and
# says so; what it stands for is the property, which an operator on
# another controller owes under whatever name it uses.
ACCESS_LOG = "nginx.ingress.kubernetes.io/enable-access-log"

# The administrative connection the provisioning SQL is run over, and
# the three keys its Secret carries. One home: the env assertion reads
# the whole tuple and the command assertion reads the first of them, so
# a rename is one edit here.
ADMIN_URL = "ADMIN_URL"
INIT_SECRET_KEYS = (ADMIN_URL, "VINGA_DB_USER", "VINGA_DB_RO_PASSWORD")

# An environment name that carries a credential. Anything matching this
# in an applicable manifest has to arrive from a Secret rather than as a
# literal.
CREDENTIAL = re.compile(r"PASSWORD|SECRET|TOKEN|MASTER_KEY|ADMIN_URL")

# The identity the image establishes, and the only one it does: the
# Dockerfile's `useradd --uid 1000`. The group and fsGroup in the
# manifest are the manifest's own declared contract, so they are not
# asserted against this.
IMAGE_UID = re.compile(r"useradd\s+--uid\s+(\d+)")

# The required values the production compose file guards. Named here
# because the list IS the contract: the two server secrets and the five
# database facts, plus the image itself.
GUARDED = (
    "VINGA_IMAGE",
    "VINGA_AUTH_SECRET",
    "VINGA_API_SECRET",
    "VINGA_DB_HOST",
    "VINGA_DB_PORT",
    "VINGA_DB_NAME",
    "VINGA_DB_USER",
    "VINGA_DB_PASSWORD",
)


def _applicable() -> list[Path]:
    return sorted(K8S.glob(APPLY_GLOB))


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _file_of_kind(kind: str) -> Path:
    """The one applicable manifest of a kind, so a test that names a
    resource fails loudly rather than silently reading the wrong one."""
    found = [path for path in _applicable() if _load(path).get("kind") == kind]
    assert len(found) == 1, f"expected exactly one {kind} under deploy/k8s/, found {len(found)}"
    return found[0]


def _of_kind(kind: str) -> Any:
    return _load(_file_of_kind(kind))


@pytest.fixture(scope="module")
def deployment() -> Any:
    return _of_kind("Deployment")


@pytest.fixture(scope="module")
def pod(deployment: Any) -> Any:
    return deployment["spec"]["template"]["spec"]


@pytest.fixture(scope="module")
def container(pod: Any) -> Any:
    containers = pod["containers"]
    assert len(containers) == 1, "one replica of one process is one container"
    return containers[0]


@pytest.fixture(scope="module")
def service() -> Any:
    return _of_kind("Service")


@pytest.fixture(scope="module")
def ingress() -> Any:
    return _of_kind("Ingress")


@pytest.fixture(scope="module")
def claim() -> Any:
    return _of_kind("PersistentVolumeClaim")


@pytest.fixture(scope="module")
def job() -> Any:
    return _of_kind("Job")


@pytest.fixture(scope="module")
def compose() -> Any:
    return _load(COMPOSE)["services"]["vinga"]


def _env(container: Any) -> dict[str, Any]:
    """The container's env entries by name, each as it was written, so a
    literal value and a Secret reference are told apart."""
    return {entry["name"]: entry for entry in container.get("env", [])}


def _mount(container: Any, path: str) -> Any:
    found = [mount for mount in container["volumeMounts"] if mount["mountPath"] == path]
    assert len(found) == 1, f"expected exactly one mount at {path}"
    return found[0]


def _volume(pod: Any, name: str) -> Any:
    found = [volume for volume in pod["volumes"] if volume["name"] == name]
    assert len(found) == 1, f"expected exactly one volume named {name}"
    return found[0]


def _image_uid() -> int:
    found = IMAGE_UID.search(DOCKERFILE.read_text(encoding="utf-8"))
    assert found, "the Dockerfile no longer creates its user with `useradd --uid`"
    return int(found.group(1))


def test_there_are_manifests_to_check() -> None:
    """A glob that matched nothing would make every assertion below
    vacuous, which is what a directory rename produces."""
    assert len(_applicable()) == 5, [path.name for path in _applicable()]
    for name in TEMPLATES:
        assert (K8S / name).is_file(), f"{name} is missing"


# Code agreement: the facts the manifests copy out of the server


def test_the_container_port_is_the_servers_own_default(container: Any) -> None:
    ports = container["ports"]
    assert len(ports) == 1, "everything this server serves is on one port"
    assert ports[0]["containerPort"] == ServerConfig.model_fields["port"].default


def test_the_probes_hit_the_paths_the_server_serves(container: Any) -> None:
    """Restart at liveness, admission at readiness, and the startup
    probe on liveness because that is the one that answers first."""
    assert container["startupProbe"]["httpGet"]["path"] == HEALTH_PATH
    assert container["livenessProbe"]["httpGet"]["path"] == HEALTH_PATH
    assert container["readinessProbe"]["httpGet"]["path"] == READY_PATH


def test_the_startup_budget_covers_a_cold_first_boot(container: Any) -> None:
    """uvicorn binds its listener only after the lifespan has built the
    composition, and a provider loading a model can hold that for
    minutes, during which every probe meets a refused connection. The
    startup probe is what suspends liveness for the length of it."""
    probe = container["startupProbe"]
    budget = probe["failureThreshold"] * probe["periodSeconds"]
    assert budget >= STARTUP_FLOOR_S, (
        f"a cold start gets {budget}s, under the {STARTUP_FLOOR_S}s floor"
    )


def test_both_grace_periods_outlast_the_drain(deployment: Any, compose: Any) -> None:
    """SIGTERM starts a drain that lets conversations in flight finish
    their sentence. A grace period at or under `drain_s` SIGKILLs the
    process halfway through a spoken reply, on every redeploy."""
    drain_s = ServerConfig.model_fields["drain_s"].default
    floor = drain_s + GRACE_MARGIN_S

    kubernetes = deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]
    assert kubernetes >= floor, f"terminationGracePeriodSeconds is {kubernetes}, under {floor}"

    written = compose["stop_grace_period"]
    assert written.endswith("s"), f"stop_grace_period is written as {written!r}, not seconds"
    assert float(written[:-1]) >= floor, f"stop_grace_period is {written}, under {floor}s"


def test_the_pod_runs_as_the_images_own_user(pod: Any) -> None:
    """The one identity fact the Dockerfile establishes, read from it
    rather than restated. A pod running as another user meets a
    root-owned volume it cannot write and a read-only root filesystem
    with nowhere else to go."""
    security = pod["securityContext"]
    assert security["runAsNonRoot"] is True
    assert security["runAsUser"] == _image_uid()
    # And the mechanism that makes the volume writable whatever group
    # the image's user is actually in, which is the manifest's own
    # contract rather than the image's.
    assert security["fsGroup"] == security["runAsGroup"]
    assert security["fsGroupChangePolicy"] == "OnRootMismatch"


def test_the_root_filesystem_is_read_only_with_a_memory_backed_tmp(
    pod: Any, container: Any, compose: Any
) -> None:
    """The contract promises a tmpfs. A default emptyDir is node-backed
    storage, which validates identically and is not one."""
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    volume = _volume(pod, _mount(container, "/tmp")["name"])
    assert volume["emptyDir"]["medium"] == "Memory"
    assert volume["emptyDir"].get("sizeLimit"), "a memory-backed volume with no size limit"

    assert compose["read_only"] is True
    assert "/tmp" in compose["tmpfs"]


def test_the_init_job_draws_its_credentials_from_the_init_secret(job: Any) -> None:
    """The three keys the provisioning SQL needs, each from the Job's
    own Secret. `VINGA_DB_RO_PASSWORD` is the one that matters most: the
    SQL defaults it to the public string `vinga_ro`, so an omission
    installs a well-known password on a role that reads every recorded
    conversation."""
    container = job["spec"]["template"]["spec"]["containers"][0]
    env = _env(container)
    for name in INIT_SECRET_KEYS:
        assert name in env, f"the init Job does not reference {name}"
        reference = env[name]["valueFrom"]["secretKeyRef"]
        assert reference["key"] == name
        assert reference["name"] == _init_secret_name()


def _init_secret_name() -> str:
    """What the init template calls itself, so the Job and the template
    agree by construction rather than by two people remembering."""
    return _load(K8S / "secret-init.example")["metadata"]["name"]


def test_the_serving_pod_never_references_the_init_secret(container: Any) -> None:
    """Creating roles and schemas is an administrative right the serving
    process has no use for, and the blast radius of a compromised server
    is the whole instance if it holds one."""
    init = _init_secret_name()
    for entry in container.get("env", []):
        reference = entry.get("valueFrom", {}).get("secretKeyRef", {})
        assert reference.get("name") != init, f"{entry['name']} reads the administrative Secret"
    for entry in container.get("envFrom", []):
        assert entry.get("secretRef", {}).get("name") != init


def test_the_init_job_runs_the_committed_sql(job: Any) -> None:
    """One home for the SQL: this repository's copy, handed over as a
    ConfigMap the operator builds from it. A Job that named a different
    file, or that mounted nothing, would fail on the cluster rather than
    here.

    The whole command is asserted rather than sampled. Every piece of it
    is load bearing and each fails differently: without the connection
    psql falls back to a local socket and provisions the wrong instance
    or nothing; without `ON_ERROR_STOP` a failing statement scrolls past
    on the way to a green exit code and a half provisioned database is
    indistinguishable from a provisioned one; and a `-f` naming the
    wrong file runs nothing at all. The mount path is not restated: the
    file argument is what the mount is looked up from, and the argument
    itself is held to the committed file's own name.
    """
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    argv = [*container.get("command", []), *container.get("args", [])]

    assert "-f" in argv, f"the init Job runs no file: {argv}"
    named = argv[argv.index("-f") + 1]
    assert Path(named).name == INIT_SQL.name, f"{named} is not the committed provisioning file"

    # `$(ADMIN_URL)` is Kubernetes' own substitution from the env entry
    # asserted above, so what psql is given is the Secret's value and
    # not a literal anybody could read here.
    assert argv == ["psql", f"$({ADMIN_URL})", "-v", "ON_ERROR_STOP=1", "-f", named], (
        f"the init Job's command is {argv}"
    )

    mount = _mount(container, str(Path(named).parent))
    assert mount.get("readOnly") is True, (
        "what runs is what is committed, so the mount is read-only"
    )

    volume = _volume(pod, mount["name"])
    configmap = volume.get("configMap", {}).get("name")
    assert configmap, "the SQL arrives as a ConfigMap built from the committed file"
    # And it is the ConfigMap the header tells an operator to build, so
    # the name is read off the volume and the documented creation
    # command is held to it rather than the two being typed separately.
    written = _file_of_kind("Job").read_text(encoding="utf-8")
    assert f"kubectl create configmap {configmap}" in written, (
        f"the header does not tell an operator how to build {configmap}"
    )
    assert f"--from-file={INIT_SQL.relative_to(REPO)}" in written, (
        "the header builds the ConfigMap from some other file"
    )


def test_the_init_job_runs_the_psql_this_repository_pins(job: Any) -> None:
    """The SQL reads its two parameters with psql's `\\getenv`, which
    needs psql 15 or later, and the trial compose file is where this
    repository decides which Postgres it runs. Reading that pin here
    keeps it one decision: a bump there moves this Job with it, and a
    Job left on an older client would fail on a directive rather than on
    anything a reader would connect to a version."""
    pinned = _load(TRIAL_COMPOSE)["services"]["postgres"]["image"]
    image = job["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == pinned, f"the init Job runs {image}, not the pinned {pinned}"


# The network path: the agreements a schema validator cannot see


def test_the_service_selects_the_deployments_pods(deployment: Any, service: Any) -> None:
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    assert service["spec"]["selector"] == labels
    assert deployment["spec"]["selector"]["matchLabels"] == labels


def test_the_service_reaches_the_container_port(service: Any, container: Any) -> None:
    """A `targetPort` naming nothing is a Service with endpoints that
    answer nowhere, and it validates."""
    declared = container["ports"][0]
    ports = service["spec"]["ports"]
    assert len(ports) == 1
    target = ports[0]["targetPort"]
    reached = declared["name"] if isinstance(target, str) else declared["containerPort"]
    assert target == reached, f"the Service targets {target}, which the container does not declare"


def test_every_ingress_backend_names_the_service_and_its_port(
    ingress: Any, service: Any
) -> None:
    declared = service["spec"]["ports"][0]
    for rule in ingress["spec"]["rules"]:
        for path in rule["http"]["paths"]:
            backend = path["backend"]["service"]
            assert backend["name"] == service["metadata"]["name"]
            port = backend["port"]
            expected = declared["name"] if "name" in port else declared["port"]
            assert next(iter(port.values())) == expected, f"{path['path']} routes to {port}"


def test_the_ingress_terminates_tls_for_the_host_it_routes(ingress: Any) -> None:
    """TLS termination is this resource's own contract rather than an
    annotation, and every part of it can go missing while the manifest
    stays schema-valid: an empty `spec.tls`, an entry for another host,
    or one naming no certificate at all. Each of those is a deployment
    serving plaintext, or serving nothing, with everything else looking
    right.

    The Secret's name is not restated here. It is read off the resource
    and the header's own `kubectl create secret tls` line is held to
    naming the same one, so the certificate an operator is told to
    create is the certificate this Ingress asks for.
    """
    rules = ingress["spec"]["rules"]
    assert len(rules) == 1, f"expected one rule, found {len(rules)}"
    host = rules[0]["host"]
    assert host, "the rule matches every host, so the certificate below covers none of them"

    tls = ingress["spec"]["tls"]
    assert len(tls) == 1, f"expected exactly one spec.tls entry, found {len(tls)}"
    assert tls[0]["hosts"] == [host], f"the certificate covers {tls[0]['hosts']}, not {host}"

    secret = tls[0].get("secretName")
    assert secret, "spec.tls names no certificate Secret, so TLS terminates nowhere"
    written = _file_of_kind("Ingress").read_text(encoding="utf-8")
    assert f"kubectl create secret tls {secret}" in written, (
        f"the header does not tell an operator how to create {secret}"
    )


def test_the_urls_the_server_hands_devices_are_that_host_over_tls(
    ingress: Any, container: Any
) -> None:
    """The other side of the same agreement. A certificate for one host
    and a websocket URL for another is a board that fails at the
    handshake with every log line looking right, and a `ws://` value
    behind an `https://` ingress is the exact fault the server's own
    doctor command exists to name.

    The websocket path is the server's, read from the constant the
    application mounts the channel at rather than typed again here.
    """
    from urllib.parse import urlsplit

    host = ingress["spec"]["rules"][0]["host"]
    env = _env(container)

    public = urlsplit(env["VINGA_SERVER__PUBLIC_URL"]["value"])
    assert public.scheme == "https", f"the public URL is {public.scheme}, not https"
    assert public.hostname == host

    websocket = urlsplit(env["VINGA_SERVER__WEBSOCKET_URL"]["value"])
    assert websocket.scheme == "wss", f"the websocket URL is {websocket.scheme}, not wss"
    assert websocket.hostname == host
    assert websocket.path == WEBSOCKET_PATH, (
        f"the websocket URL points at {websocket.path}, not {WEBSOCKET_PATH}"
    )


def test_the_ingress_routes_exactly_the_device_paths(ingress: Any) -> None:
    """The public security boundary, derived from the paths the server
    registers rather than restated. Two are routed: the short onboarding
    route, which is how a fresh board is provisioned, and the WebSocket
    a bound device holds open.

    What is deliberately not routed is the other half of the point. The
    configuration API holds the most authority of anything this server
    serves; the probes are the kubelet's; and the legacy OTA path is the
    token issuer, so it cannot require a token and is safely public only
    behind a random segment that agrees with `VINGA_SERVER__OTA_PATH`.
    """
    routed = {
        path["path"]
        for rule in ingress["spec"]["rules"]
        for path in rule["http"]["paths"]
    }
    assert routed == {f"{ONBOARDING_MOUNT_PATH}/", WEBSOCKET_PATH}

    withheld = (
        f"{API_MOUNT_PATH}/",
        HEALTH_PATH,
        READY_PATH,
        ServerConfig.model_fields["ota_path"].default,
    )
    for path in withheld:
        assert path not in routed, f"{path} is routed to the internet"


def test_the_ingress_logs_no_request_lines(ingress: Any) -> None:
    """The same leak the server refuses at the origin, refused at the
    edge. An access line is a request line, and both routed paths carry
    a secret in theirs: the OTA path is the deployment's secret segment,
    and `/x/<key>/` is the key that stands in front of the endpoint
    issuing device tokens. `serving.py` turns uvicorn's access log off
    for precisely this, and a proxy in front that logs would put back
    what the server took out."""
    setting = ingress["metadata"]["annotations"].get(ACCESS_LOG)
    assert setting is not None, f"the Ingress no longer sets {ACCESS_LOG}"
    assert str(setting).strip().lower() == "false", f"{ACCESS_LOG} is {setting!r}"


def test_the_production_compose_publishes_the_servers_port(compose: Any) -> None:
    port = ServerConfig.model_fields["port"].default
    assert compose["ports"] == [f"{port}:{port}"]


def test_the_production_compose_refuses_without_each_required_value() -> None:
    """Every required value is an interpolation guard of its own. A
    required `env_file` proves only that a file exists, so the guards
    are what turn an omission into a refusal; CI proves each one
    actually fires, and this proves none of them was quietly deleted."""
    text = COMPOSE.read_text(encoding="utf-8")
    missing = [name for name in GUARDED if f"${{{name}:?" not in text]
    assert not missing, f"no refusal guard on: {', '.join(missing)}"


# The topology pins


def test_the_deployment_runs_exactly_one_replica_and_replaces_it(deployment: Any) -> None:
    """One replica is the supported topology, and `Recreate` is what
    keeps a rollout from briefly running two servers against one
    database while the new one migrates."""
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"


def test_the_data_volume_is_a_single_writer_claim(
    claim: Any, pod: Any, container: Any
) -> None:
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    volume = _volume(pod, _mount(container, "/data")["name"])
    assert volume["persistentVolumeClaim"]["claimName"] == claim["metadata"]["name"]


# The applicable-manifest guard


def test_no_applicable_manifest_is_a_secret() -> None:
    """A Secret template that rides along on `kubectl apply -f
    deploy/k8s/` installs whatever placeholder it carries, and every
    presence check this server makes is satisfied by a nonempty
    string."""
    for path in _applicable():
        assert _load(path)["kind"] != "Secret", f"{path.name} is applicable and is a Secret"


def test_no_applicable_manifest_carries_a_credential_value() -> None:
    """The other half: a credential written as a literal anywhere under
    the apply glob is a credential in this repository. Everything
    matching a credential name has to arrive through `valueFrom`."""
    for path in _applicable():
        document = _load(path)
        for entry in _every_env_entry(document):
            if CREDENTIAL.search(entry.get("name", "")):
                assert "value" not in entry, f"{path.name} writes {entry['name']} as a literal"
        assert PLACEHOLDER not in path.read_text(encoding="utf-8"), (
            f"{path.name} carries a template placeholder"
        )


def test_the_templates_are_the_placeholders_home() -> None:
    """And the vacuity guard for the scan above: the marker it looks for
    has to be a marker these files actually use."""
    for name in TEMPLATES:
        path = K8S / name
        assert not path.match(APPLY_GLOB), f"{name} is inside the apply glob"
        assert PLACEHOLDER in path.read_text(encoding="utf-8"), f"{name} marks no placeholder"
        assert _load(path)["kind"] == "Secret"


def _every_env_entry(node: Any) -> list[dict[str, Any]]:
    """Every container env entry anywhere in a document, found by shape
    rather than by walking the paths each kind happens to nest them
    under, so a Job and a Deployment are read the same way."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        entries = node.get("env")
        if isinstance(entries, list):
            found += [entry for entry in entries if isinstance(entry, dict)]
        for value in node.values():
            found += _every_env_entry(value)
    elif isinstance(node, list):
        for value in node:
            found += _every_env_entry(value)
    return found
