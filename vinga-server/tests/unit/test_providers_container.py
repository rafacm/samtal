"""What a build says about an entry pointing at this machine (#340).

The trap: a `base_url` naming localhost is what an operator runs on
their own machine, and copied into a container it means the container.
The server boots clean, applies clean and hears the utterance, and the
first sign of the mistake is a call that fails at the first round with
nothing on the device to see. So the build says so, once, naming the
entry and the fix.

Three things are pinned here. That the warning is emitted exactly when
the two facts hold, the image's own marker and one of the three host
spellings. That it is a warning and not a refusal, because the same
configuration is right where the endpoint shares this container or its
network namespace. And that nothing of the `base_url` but the loopback
token itself reaches the record, which is what the sentinel is for: a
`base_url` may carry a password, and the field is declared as a closed
set of three so it cannot carry one.
"""

import pytest

from tests.support.events import both_formats, fields_of, only
from tests.support.providers import built_world
from vinga_server.build_info import CONTAINER_ENV
from vinga_server.config import Config
from vinga_server.config.models import ProviderConfig
from vinga_server.providers import build_entry
from vinga_server.providers.openai_llm import OpenAiCompatibleLlm

EVENT = "provider_reaches_loopback"

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. It rides in as the password of a base_url, which is
# where an endpoint behind basic auth puts one.
PASTED = "hunter2-never-a-real-password-9c3f"


@pytest.fixture
def in_a_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """The marker the image sets in its own ENV, which is the only thing
    that says this process is inside one."""
    monkeypatch.setenv(CONTAINER_ENV, "1")


@pytest.fixture(autouse=True)
def _outside_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lane run inside a container would otherwise warn its way through
    every test below that asserts silence."""
    monkeypatch.delenv(CONTAINER_ENV, raising=False)


def llm(base_url: str) -> ProviderConfig:
    return ProviderConfig.model_validate(
        {"type": "openai_compatible", "base_url": base_url, "model": "qwen3:8b"}
    )


async def test_the_warning_names_the_entry_the_stage_and_the_fix(
    in_a_container: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        await build_entry("llm", "local", llm("http://localhost:11434/v1"))

    record = only(caplog, EVENT)
    assert record.levelname == "WARNING"
    said = record.getMessage()
    assert "providers.llm.local" in said
    assert "host.docker.internal" in said
    assert fields_of(record) == {
        "event": EVENT,
        "stage": "llm",
        "provider": "local",
        "type": "openai_compatible",
        "host": "localhost",
    }


@pytest.mark.parametrize(
    ("base_url", "host"),
    [
        ("http://localhost:11434/v1", "localhost"),
        ("http://127.0.0.1:11434/v1", "127.0.0.1"),
        ("http://[::1]:11434/v1", "::1"),
    ],
)
async def test_every_spelling_of_this_machine_is_one_the_check_knows(
    base_url: str, host: str, in_a_container: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        await build_entry("llm", "local", llm(base_url))

    assert fields_of(only(caplog, EVENT))["host"] == host


async def test_outside_a_container_the_same_entry_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Where the endpoint really is on this machine, localhost is the
    right answer and there is nothing to say about it."""
    with caplog.at_level("WARNING"):
        await build_entry("llm", "local", llm("http://localhost:11434/v1"))

    assert not [record for record in caplog.records if getattr(record, "event", "") == EVENT]


async def test_an_endpoint_somewhere_else_says_nothing(
    in_a_container: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        await build_entry("llm", "local", llm("http://host.docker.internal:11434/v1"))

    assert not [record for record in caplog.records if getattr(record, "event", "") == EVENT]


async def test_a_bundled_engine_reaches_nothing_and_says_nothing(
    in_a_container: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A provider that runs in this process publishes no host at all,
    which is the case the check has to pass over rather than match."""
    with caplog.at_level("WARNING"):
        await build_entry("vad", "gate", ProviderConfig.model_validate({"type": "mock"}))

    assert not [record for record in caplog.records if getattr(record, "event", "") == EVENT]


@pytest.mark.parametrize(
    ("stage", "name", "entry"),
    [
        (
            "tts",
            "voice",
            {"type": "openai", "voice": "alloy", "base_url": "http://localhost:8080/v1"},
        ),
        ("asr", "ears", {"type": "openai", "base_url": "http://localhost:8000/v1"}),
    ],
)
async def test_the_other_types_that_name_an_endpoint_are_covered_too(
    stage: str,
    name: str,
    entry: dict[str, object],
    in_a_container: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The check reads the host every provider already publishes for its
    identity, so the openai speech and transcription types are in by
    construction rather than by a second mechanism."""
    with caplog.at_level("WARNING"):
        await build_entry(stage, name, ProviderConfig.model_validate(entry))

    assert fields_of(only(caplog, EVENT))["stage"] == stage


def test_a_world_still_builds_around_the_warning(
    in_a_container: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A warning and not a refusal, said on the one path a boot and an
    apply share: `build_world` is what both of them call, so a
    configuration that boots is a configuration a reload accepts, warning
    and all."""
    config = Config(
        providers={
            "llm": {"brain": llm("http://localhost:11434/v1").model_dump(exclude_none=True)},
            "asr": {"ears": {"type": "mock"}},
            "tts": {"voice": {"type": "mock"}},
            "vad": {"gate": {"type": "mock"}},
        },
        agents={
            "assistant": {"llm": "brain", "asr": "ears", "tts": "voice", "vad": "gate"}
        },
        default_agent="assistant",
    )
    with caplog.at_level("WARNING"):
        world = built_world(config)

    assert isinstance(world.agents["assistant"].llm, OpenAiCompatibleLlm)
    assert fields_of(only(caplog, EVENT))["provider"] == "brain"


async def test_a_credential_in_the_base_url_reaches_no_part_of_the_record(
    in_a_container: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole reason the host field is a closed set of three.

    An endpoint behind basic auth puts its password in the base_url, and
    this warning is the one record that is about a base_url. It says
    which of three spellings of this machine the entry named and nothing
    else, so there is no rendering of the record in which a password can
    appear.
    """
    with caplog.at_level("WARNING"):
        await build_entry(
            "llm", "local", llm(f"http://user:{PASTED}@localhost:11434/v1")
        )

    assert fields_of(only(caplog, EVENT))["host"] == "localhost"
    rendered = both_formats(caplog)
    assert PASTED not in rendered
    assert "user" not in rendered
