"""What a server event may never say, whoever provoked it.

The sibling of `test_event_surface_pins.py`, which does this for the
session scope, and what is left of both after #210: the prose pins that
restated a template, an argument tuple and a field set are gone, because
a variant IS its declaration now and nothing is left for a call to
disagree with. What a record holds is `docs/reference/events.md`'s; that a
record really holds it is the driver suite's.

What no file but this one holds is the negative claim. A pin says the
sentence is what it is; it does not say the sentence is SAFE. Each case
below plants a credential-shaped sentinel where a stranger's bytes
really arrive, drives the production path that reports it, and hunts the
spelling in every place a value could surface: the rendered sentence,
the arguments behind it, the payload's fields, both shipped log formats,
and an attached consumer's own copy of all three.

Six paths, chosen because each is somewhere far-side bytes meet a
diagnostic:

- the onboarding banner, where this deployment's own key is what a URL
  would be built from;
- an onboarding path that missed, where two keys are in play: the
  server's, which a probe was fishing for, and the attempted one, which
  a near miss would turn into a hint at the real one;
- a capture's failed write and an unusable capture directory, where a
  bare `Exception` catch meets whatever the filesystem, the wave module
  or a JSON encoder raised, messages that carry the path they tripped on
  or the bytes they choked on;
- a refused handshake, where the Device-Id header is a string whoever
  opened the socket chose and can retry as fast as the socket opens;
- a filler voice that would not cache, where the catch is around a whole
  synthesis and an exception raised near a provider's response can carry
  a fragment of one.

The `Vandal` is the other half of the same claim, and it is about
consumers rather than records: `Emission.args` is deliberately not
copied for a tap, so an object passed as a `%` argument reaches every
consumer as the live object, its chain and everything the chain closes
over. It finds nothing to vandalize, which is the assertion.
"""

import logging
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.support.apps import entered_client
from tests.support.configs import (
    config_with_agent,
    masked_config,
)
from tests.support.events import only
from tests.support.providers import built_world
from tests.support.stores import CAPTURE_MANIFEST as MANIFEST
from tests.support.stores import store
from tests.support.wire import device_headers, handshake
from vinga_server import onboarding
from vinga_server.app import create_app
from vinga_server.capture import CaptureStore
from vinga_server.config import Config
from vinga_server.events import Emission, attach_server_tap, detach_server_tap
from vinga_server.events.values import CaptureWrite
from vinga_server.filler import build_agent_fillers
from vinga_server.logs import _STANDARD_ATTRIBUTES, JsonFormatter

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It stands in for whatever a value a pin blesses
# could turn out to be: a key, a header a stranger chose, a message an
# exception carried up from a dependency.
SENTINEL = "sk-test-6c1e9a4f-never-a-real-credential"


class Consumer:
    """A server-scope tap that keeps what it was handed.

    A pin says what a record holds; it cannot say what a *consumer*
    holds, and the two are not the same object. `Emission.args` is
    deliberately not copied for a tap (the payload is), so anything
    passed as a `%` argument reaches every consumer as the object
    itself. A claim that a value reaches nobody is therefore asserted
    here as well as at the log."""

    def __init__(self) -> None:
        self.seen: list[Emission] = []

    def emit(self, emission: Emission) -> None:
        self.seen.append(emission)

    def saw(self, event: str) -> list[Emission]:
        return [one for one in self.seen if one.payload.get("event") == event]

    def rendered(self) -> str:
        """Everything a consumer could read off what it was handed:
        every payload and every argument, including what an exception
        renders as and what its chain renders as."""
        parts = []
        for emission in self.seen:
            parts.append(str(emission.payload))
            for argument in emission.args:
                parts += [str(argument), repr(argument)]
                cause = getattr(argument, "__cause__", None) or getattr(
                    argument, "__context__", None
                )
                while cause is not None:
                    parts += [str(cause), repr(cause)]
                    cause = cause.__cause__ or cause.__context__
        return "\n".join(parts)


class Vandal:
    """A tap that edits what it was handed, before the log tap runs.

    Non-log taps dispatch first and are given the emission's own `args`
    tuple. Its members are deliberately not copied (copying an arbitrary
    argument is a copy that can fail), so an object passed as a `%`
    argument is a live object every consumer can read and write before
    the record exists. The only defence is not to pass one, which is
    what this proves: it finds nothing to vandalize."""

    def __init__(self) -> None:
        self.exceptions: list[BaseException] = []

    def emit(self, emission: Emission) -> None:
        for argument in emission.args:
            if isinstance(argument, BaseException):
                self.exceptions.append(argument)
                argument.args = ("injected by a tap",)


def planted() -> Exception:
    """An exception shaped like the ones these catches really meet: a
    message holding a value nobody wrote for a log, and a cause behind
    it holding another, since a renderer that walks the chain reaches
    both."""
    try:
        try:
            raise ValueError(f"while reading {SENTINEL}")
        except ValueError as cause:
            raise OSError(f"gave up on {SENTINEL}") from cause
    except OSError as exc:
        return exc


@pytest.fixture
def vandal() -> Iterator[Vandal]:
    """A consumer attached to the server hub that writes to whatever it
    is handed."""
    consumer = Vandal()
    attach_server_tap(consumer)
    try:
        yield consumer
    finally:
        detach_server_tap(consumer)


@pytest.fixture
def tap() -> Iterator[Consumer]:
    """A consumer attached to the server hub for one test, which is what
    a #66/#67 exporter will be."""
    consumer = Consumer()
    attach_server_tap(consumer)
    try:
        yield consumer
    finally:
        detach_server_tap(consumer)


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Every record this server wrote, in both shipped formats, so a
    sentinel is hunted in the human sentence, in the JSON object, and in
    the arguments behind both.

    Only this server's channels. A driver that goes through
    `TestClient` puts httpx's own request line in `caplog` too, and what
    the test's HTTP client says about the URL it just fetched is not
    something this server chose to write."""
    formatter = JsonFormatter()
    return "\n".join(
        f"{record.getMessage()}\n{record.args!r}\n{formatter.format(record)}"
        for record in caplog.records
        if record.name.startswith("vinga_server")
    )




def payload_of(record: logging.LogRecord) -> dict[str, Any]:
    """The structured half of a record: exactly the attributes the JSON
    formatter emits as top-level keys, read through `logs.py`'s own
    standard-attribute set rather than through a list written here."""
    return {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES}



































# --- onboarding.py: the banner and the key that missed ----------------

# A pinned key rather than a derived one, so what the tests below hunt
# for is a literal instead of something recomputed from the secret by
# the code under test. Pinning is a supported configuration: it is what
# carries provisioned boards across a secret rotation.
PINNED_KEY = "ABCDEFGH"


def banner_config(**onboarding_options: object) -> Config:
    return Config(
        server={
            "public_url": "https://voice.example",
            "onboarding": {"key": PINNED_KEY, **onboarding_options},
        }
    )




def test_a_keyless_short_route_says_so_rather_than_naming_a_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With device auth off there is no secret to derive a key from and
    the route mounts at /x/ bare. That is a fact about the deployment
    rather than about the key, which is what makes it safe to say."""
    config = Config(server={"public_url": "https://voice.example", "auth": {"enabled": False}})

    with caplog.at_level("INFO"):
        onboarding.log_banner(config.server)

    assert payload_of(only(caplog, "onboarding_banner"))["keyed"] is False


def test_the_onboarding_key_reaches_no_record_and_no_consumer(
    caplog: pytest.LogCaptureFixture, tap: Consumer
) -> None:
    """The sentinel for the banner. A pin says the sentence is what it
    is; it does not say the sentence is safe. The key is planted as the
    pinned one, so a URL built from it anywhere in the line, the fields,
    the arguments or a consumer's copy is found."""
    key = "S7K3XQ2M"
    config = Config(
        server={"public_url": "https://voice.example", "onboarding": {"key": key}}
    )

    with caplog.at_level("DEBUG"):
        onboarding.log_banner(config.server)

    banner = only(caplog, "onboarding_banner")
    assert key not in banner.getMessage()
    assert key not in str(banner.args)
    assert key not in str(payload_of(banner))
    assert key not in logged(caplog)
    assert tap.saw("onboarding_banner"), "it reached no tap at all, so this proves nothing"
    assert key not in tap.rendered()
    # And the line still does its job: which deployment, and where the
    # URL comes from.
    assert banner.origin == "https://voice.example"
    assert "vinga-server config ota-url" in banner.getMessage()






def test_neither_key_reaches_a_record_or_a_consumer_on_a_miss(
    caplog: pytest.LogCaptureFixture, tap: Consumer
) -> None:
    """The sentinel for the miss, and it needs two: the server's own key
    is what a probe was fishing for, and the attempted one is a string a
    stranger chose that a near miss would turn into a hint at the real
    one."""
    key = "S7K3XQ2M"
    attempted = "S7K3XQ2N"
    config = Config(server={"onboarding": {"key": key}})

    with entered_client(config) as client, caplog.at_level("DEBUG"):
        assert client.get(f"/x/{attempted}/").status_code == 404

    miss = only(caplog, "onboarding_key_mismatch")
    for planted in (key, attempted):
        assert planted not in miss.getMessage()
        assert planted not in str(miss.args)
        assert planted not in str(payload_of(miss))
        assert planted not in logged(caplog)
        assert planted not in tap.rendered()
    assert tap.saw("onboarding_key_mismatch"), "it reached no tap at all"
    assert miss.attempted_length == len(attempted)




# --- capture.py: what a recording says about itself -------------------














def test_a_failed_writes_own_words_reach_no_record_or_consumer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, tap: Consumer, vandal: Vandal
) -> None:
    """The sentinel for the capture's own failures. `_disable` catches a
    bare `Exception` around a write, so what reaches it is whatever the
    filesystem, the wave module or a JSON encoder raised, and those
    messages carry the path they tripped on or the bytes they choked on.
    Driven through `_disable` directly, because the point is the
    exception's contents and a real failed write raises what it raises.
    """
    capture = store(tmp_path).open("s1", time.monotonic(), MANIFEST)
    assert capture is not None

    with caplog.at_level("DEBUG"):
        # White-box, per the docstring: a real failed write raises what
        # the filesystem, the wave module or a JSON encoder happens to
        # raise, and what is under test is that whatever it holds does
        # not reach the record. Planting the exception is what makes
        # "whatever it holds" a value this test can hunt for.
        capture._disable(CaptureWrite.AUDIO, planted())

    failed = only(caplog, "capture_failed")
    assert SENTINEL not in failed.getMessage()
    assert SENTINEL not in str(failed.args)
    assert SENTINEL not in str(payload_of(failed))
    assert SENTINEL not in logged(caplog)
    assert tap.saw("capture_failed"), "it reached no tap at all, so this proves nothing"
    assert SENTINEL not in tap.rendered()
    assert vandal.exceptions == [], "a consumer was handed the exception itself"
    # And the diagnosis survives it: what the capture was doing, and
    # what kind of failure stopped it.
    assert (failed.reason, failed.failure) == ("write audio", "OSError")


def test_an_unusable_directorys_own_words_reach_no_record_or_consumer(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tap: Consumer,
    vandal: Vandal,
) -> None:
    """The same for the decline, which is the one an operator meets: a
    volume that will not take a capture answers with the operating
    system's own sentence about it."""
    keeper = store(tmp_path)
    monkeypatch.setattr(CaptureStore, "_free_mb", lambda self: (_ for _ in ()).throw(planted()))

    with caplog.at_level("DEBUG"):
        assert keeper.open("s1", time.monotonic(), MANIFEST) is None

    declined = only(caplog, "capture_declined")
    assert SENTINEL not in declined.getMessage()
    assert SENTINEL not in str(declined.args)
    assert SENTINEL not in str(payload_of(declined))
    assert SENTINEL not in logged(caplog)
    assert tap.saw("capture_declined"), "it reached no tap at all"
    assert SENTINEL not in tap.rendered()
    assert vandal.exceptions == []
    assert (declined.reason, declined.failure) == ("unusable", "OSError")















# --- ws.py: the two handshakes that never became sessions -------------


def refused_handshake(
    token: str | None, device_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    """One handshake that never becomes a session, with the Device-Id of
    the caller's choosing."""
    with caplog.at_level("DEBUG"):
        with TestClient(create_app(config_with_agent())) as client:
            with pytest.raises(WebSocketDisconnect):
                with handshake(client, device_headers(token, device_id)):
                    pass




@pytest.mark.parametrize(
    ("token", "reason"),
    [(None, "no_token"), ("a-token-this-server-never-issued.1700000000", "bad_token")],
)
def test_a_refused_handshakes_device_id_reaches_nothing(
    token: str | None, reason: str, caplog: pytest.LogCaptureFixture, tap: Consumer
) -> None:
    """The sentinel for both refusals. This endpoint is reachable by
    anything that finds it, and a caller who is turned away can retry as
    fast as the socket opens, so a header echoed here is a value of
    their choosing written into the retained surface once per attempt."""
    refused_handshake(token, SENTINEL, caplog)

    rejected = only(caplog, "auth_rejected")
    assert rejected.reason == reason
    assert SENTINEL not in rejected.getMessage()
    assert SENTINEL not in str(rejected.args)
    assert SENTINEL not in str(payload_of(rejected))
    assert SENTINEL not in logged(caplog)
    assert tap.saw("auth_rejected"), "it reached no tap at all, so this proves nothing"
    assert SENTINEL not in tap.rendered()
    assert payload_of(rejected)["device"] is None












# --- filler.py: a voice that could not be cached ----------------------




async def test_a_broken_voices_own_words_reach_no_record_or_consumer(
    caplog: pytest.LogCaptureFixture, tap: Consumer, vandal: Vandal
) -> None:
    """The sentinel for the boot's one degrading path. The catch is
    around a whole synthesis, so an exception raised near a provider's
    response can carry a fragment of one, and this line is written once
    per agent at every start."""

    class PlantedTts:
        sample_rate = 24000

        def synthesize(self, text: str) -> AsyncIterator[bytes]:
            raise planted()

    config = masked_config()
    providers = dict(built_world(config).agents)
    providers["poet"] = replace(providers["poet"], tts=cast(Any, PlantedTts()))

    with caplog.at_level("DEBUG"):
        await build_agent_fillers(config, providers)

    disabled = only(caplog, "filler_disabled")
    assert SENTINEL not in disabled.getMessage()
    assert SENTINEL not in str(disabled.args)
    assert SENTINEL not in str(payload_of(disabled))
    assert SENTINEL not in logged(caplog)
    assert tap.saw("filler_disabled"), "it reached no tap at all"
    assert SENTINEL not in tap.rendered()
    assert vandal.exceptions == []
    assert (disabled.agent, disabled.error) == ("poet", "OSError")
