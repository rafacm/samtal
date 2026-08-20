"""Applying the stored configuration to a running server.

The MCP half is exercised against real servers in
`test_tools_mcp_reload.py` and the transport around the route is
`test_config_api_runtime.py`'s. What is left here is what the
generalized apply itself decides: which slices of the stored world it
installs, what it refuses when they do not add up with the ones it is
keeping, what it reports having done, and what a live session reads once
it has done it.

The overlay is most of the file, and its cases are chosen for the shape
they share: a stored edit reaches a running session or it does not, and
which one it is has to follow the field rather than the entity. An
agent's own prompt reaches it; the fragments every agent inherits
through `agent_defaults` do not, because the effective-value helpers
read that layer and installing it would apply a start-bound change
through the back door.
"""

import asyncio
import json
import threading
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.support.apps import entered_client
from tests.support.configs import config_with, world
from tests.support.problems import problem
from tests.support.providers import RecordingLlm
from tests.support.sessions import run_reply, session_for
from tests.support.tools_mcp import reading
from vinga_server import app as app_module
from vinga_server.app import _prompt_preview, config_diff_reader, config_reloader
from vinga_server.config import Config
from vinga_server.config.api import MOUNT_PATH
from vinga_server.config.boot import BootConfig, load_boot_config
from vinga_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    ReloadInProgressError,
    StorageError,
)
from vinga_server.config.reload import RELOAD_IN_PROGRESS, ConfigReload
from vinga_server.config.responses import ConfigReloadResult
from vinga_server.config.secrets import (
    MASTER_KEY_ENV,
    SecretLocation,
    generate_key,
    load_keys,
)
from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database
from vinga_server.generation import Generations
from vinga_server.logs import JsonFormatter
from vinga_server.tools.mcp import McpServers

DEVICE = "aa:bb:cc:dd:ee:ff"


def served(**overrides: object) -> Config:
    """A configuration one device reaches one agent through, so a
    session can be built on it."""
    return config_with(**({"devices": {DEVICE: ["assistant"]}} | overrides))


def applying(running: Config, stored: Config) -> tuple[Generations, ConfigReload]:
    """A running server and the apply that would put `stored` in front
    of it, with the holder the apply installs into handed back so a test
    can read what is being served."""
    generations = world(running)
    return generations, ConfigReload(
        generations, McpServers.build(running), reading(stored)
    )


async def applied(running: Config, stored: Config) -> tuple[Generations, ConfigReloadResult]:
    generations, reload = applying(running, stored)
    return generations, await reload.apply()


# What an apply installs, field by field


async def test_an_agents_own_prompt_is_applied() -> None:
    generations, result = await applied(
        served(agents={"assistant": {"prompt": "A"}}),
        served(agents={"assistant": {"prompt": "B"}}),
    )

    assert generations.current().config.prompt_for_agent("assistant") == "B"
    assert result.prompts.changed == ["assistant"]


async def test_the_shared_fragments_are_applied_whole() -> None:
    """The fragment kind is replaced from the store rather than merged,
    so an edit to the text every including agent carries reaches all of
    them at once."""
    running = served(
        prompt_fragments={"house": {"text": "Quiet."}},
        agents={"assistant": {"prompt": "A", "prompt_includes": ["house"]}},
    )
    stored = served(
        prompt_fragments={"house": {"text": "Loud."}},
        agents={"assistant": {"prompt": "A", "prompt_includes": ["house"]}},
    )

    generations, result = await applied(running, stored)

    fragments = generations.current().config.fragments_for_agent("assistant")
    assert [fragment.text for fragment in fragments] == ["Loud."]
    # The agent's assembled inputs moved, which is what the section
    # reports: the fragment's own name is the diff's answer, not this
    # one's.
    assert result.prompts.changed == ["assistant"]


async def test_an_agent_defaults_include_does_not_reach_an_inheriting_agent() -> None:
    """The inheritance path the overlay exists for. `agent_defaults` is
    what every effective-value helper falls back through, so installing
    the stored one would apply a start-bound change to every agent that
    names nothing of its own."""
    running = served(
        prompt_fragments={"house": {"text": "Quiet."}},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock"),
        agents={"assistant": {"prompt": "A"}},
    )
    stored = served(
        prompt_fragments={"house": {"text": "Quiet."}},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
        | {"prompt_includes": ["house"]},
        agents={"assistant": {"prompt": "A"}},
    )

    generations, result = await applied(running, stored)

    assert generations.current().config.fragments_for_agent("assistant") == []
    assert result.prompts.changed == []


async def test_an_agent_the_store_added_is_not_served() -> None:
    """A start is what builds an agent's providers, so the agent set
    does not move here. What the store added arrives at the restart that
    can serve it."""
    generations, result = await applied(
        served(agents={"assistant": {"prompt": "A"}}),
        served(agents={"assistant": {"prompt": "A"}, "helper": {"prompt": "H"}}),
    )

    assert set(generations.current().config.agents) == {"assistant"}
    assert result.prompts.changed == []


async def test_an_agent_the_store_deleted_is_still_served() -> None:
    """The other direction of the same rule, and the one that keeps a
    live session survivable: an agent this server is talking as does not
    disappear from under it."""
    generations, _ = await applied(
        served(
            agents={"assistant": {"prompt": "A"}, "helper": {"prompt": "H"}},
            devices={DEVICE: ["assistant", "helper"]},
        ),
        served(agents={"assistant": {"prompt": "A"}}),
    )

    assert set(generations.current().config.agents) == {"assistant", "helper"}
    assert generations.current().config.prompt_for_agent("helper") == "H"


async def test_a_provider_edit_stays_pending() -> None:
    running = served(agents={"assistant": {"prompt": "A"}})
    stored = served(
        providers={
            "llm": {"mock": {"type": "mock"}, "other": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        agents={"assistant": {"prompt": "A", "llm": "other"}},
    )

    generations, _ = await applied(running, stored)

    assert generations.current().config.provider_for_agent("assistant", "llm")[0] == "mock"


# What an apply refuses


async def test_an_overlay_that_no_longer_composes_refuses_whole() -> None:
    """The slice interaction the whole-snapshot re-validation is for. A
    fragment deleted in the store is applied; the `agent_defaults` list
    naming it is not, because it is start-bound. The two together
    describe a world nothing can serve, so the apply refuses and says
    which reference did not resolve rather than installing half of it.
    """
    defaults = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock") | {
        "prompt_includes": ["house"]
    }
    running = served(
        prompt_fragments={"house": {"text": "Quiet."}},
        agent_defaults=defaults,
        agents={"assistant": {"prompt": "A"}},
    )
    # The store deleted the fragment and the layer that names it in the
    # same breath, which is a perfectly valid stored world.
    stored = served(agents={"assistant": {"prompt": "A"}})
    generations, reload = applying(running, stored)
    before = generations.current()

    with pytest.raises(ConfigError) as caught:
        await reload.apply()

    assert "nothing was changed" in str(caught.value)
    assert "prompt_includes" in str(caught.value)
    # And nothing moved: the refusal is a refusal.
    assert generations.current() is before
    assert generations.mark == 0


class _Held:
    """A stored read a test releases when it likes.

    The read is where the first half of an apply spends its await, so it
    is where a second one has to arrive to meet the exclusion at all.
    Semaphores rather than events because the wait for the read to have
    started happens off the loop, which is the only way to observe a
    worker thread from a coroutine without racing it.
    """

    def __init__(self, answer: Config) -> None:
        self._answer = reading(answer)
        self._entered = threading.Semaphore(0)
        self._release = threading.Semaphore(0)

    def __call__(self) -> BootConfig:
        self._entered.release()
        assert self._release.acquire(timeout=30)
        return self._answer()

    async def in_flight(self) -> None:
        assert await asyncio.to_thread(self._entered.acquire, True, 30)

    def let_through(self) -> None:
        self._release.release()


async def test_a_second_apply_while_one_is_running_is_refused() -> None:
    """One at a time, refused rather than queued: a second would carry a
    configuration read later than the first one's into a world the first
    is in the middle of replacing."""
    running = served(agents={"assistant": {"prompt": "A"}})
    held = _Held(running)
    reload = ConfigReload(world(running), McpServers.build(running), held)

    first = asyncio.create_task(reload.apply())
    await held.in_flight()
    try:
        with pytest.raises(ReloadInProgressError) as caught:
            await reload.apply()
    finally:
        held.let_through()
        await first

    assert "already running" in str(caught.value)
    # And the exclusion is released once the first has finished, so the
    # next one is answered.
    held.let_through()
    assert (await reload.apply()).prompts.changed == []


# What a live session reads across one


def talking_to(config: Config, generations: Generations, llm: RecordingLlm):
    """One session built the way the server builds one, against the
    holder an apply installs into."""
    return session_for(
        config, DEVICE, {"assistant": llm}, generations=generations
    )


async def test_a_session_activated_before_an_apply_keeps_its_know_how() -> None:
    """The convergence point, from the side that must not move. Prompt
    text is assembled once per activation and cached for it, so a
    conversation already in progress goes on speaking the world it was
    activated in."""
    running = served(agents={"assistant": {"prompt": "BEFORE"}})
    stored = served(agents={"assistant": {"prompt": "AFTER"}})
    generations, reload = applying(running, stored)
    llm = RecordingLlm(["one", "two"])
    session = talking_to(running, generations, llm)

    await run_reply(session, "hello")
    await reload.apply()
    await run_reply(session, "hello again")

    assert [system.startswith("BEFORE") for system in llm.systems] == [True, True]


async def test_a_session_opened_after_an_apply_assembles_the_new_text() -> None:
    """And the side that must: a session opening now reads the holder,
    so it is activated in the world the apply installed."""
    running = served(agents={"assistant": {"prompt": "BEFORE"}})
    stored = served(agents={"assistant": {"prompt": "AFTER"}})
    generations, reload = applying(running, stored)

    await reload.apply()
    llm = RecordingLlm()
    await run_reply(talking_to(running, generations, llm), "hello")

    assert llm.systems[-1].startswith("AFTER")


async def test_an_applied_fragment_reaches_the_next_activation() -> None:
    running = served(
        prompt_fragments={"house": {"text": "The house is quiet."}},
        agents={"assistant": {"prompt": "A", "prompt_includes": ["house"]}},
    )
    stored = served(
        prompt_fragments={"house": {"text": "The house is loud."}},
        agents={"assistant": {"prompt": "A", "prompt_includes": ["house"]}},
    )
    generations, reload = applying(running, stored)

    await reload.apply()
    llm = RecordingLlm()
    await run_reply(talking_to(running, generations, llm), "hello")

    assert "The house is loud." in llm.systems[-1]
    assert "The house is quiet." not in llm.systems[-1]


async def test_the_preview_and_the_comparison_agree_with_an_activation() -> None:
    """The three surfaces that answer about one agent's prompt, taken
    against one apply.

    They are three different questions and they read one world: the
    comparison says what is stored and not yet served, the preview says
    what a session opening now would be sent, and the activation is what
    a session is actually sent. Before the apply the comparison names
    the agent and the other two answer the old text; after it the
    comparison is empty and the other two answer the new one, character
    for character.
    """
    running = served(agents={"assistant": {"prompt": "BEFORE"}})
    stored = served(agents={"assistant": {"prompt": "AFTER"}})
    generations = world(running)
    servers = McpServers.build(running)
    reload = ConfigReload(generations, servers, reading(stored))
    preview = _prompt_preview(generations, servers, None)
    diff = config_diff_reader(generations, servers, reading(stored))

    pending = await diff()
    assert pending.agents.prompt.changed == ("assistant",)
    assert (await preview("assistant")).text == "BEFORE"

    await reload.apply()

    settled = await diff()
    assert settled.agents.prompt.changed == ()
    assembled = await preview("assistant")
    assert assembled.text == "AFTER"
    llm = RecordingLlm()
    await run_reply(talking_to(running, generations, llm), "hello")
    assert llm.systems[-1] == assembled.text


# What a refusal says, and what it does not carry
#
# The composition root's half: the closure the API is handed, which runs
# the re-read where a reload runs it and replaces a refused stored half's
# sentence with a fixed one. What is refused there is arbitrary stored
# state, and a sentence composed over it can quote a value written into
# the wrong column, which a credential pasted into one is exactly the
# shape of. So these cases take a real database and a real key: a stub
# would be asserting on the stub.

STAGES = ("llm", "asr", "tts", "vad")

# The forms a stored credential takes, each planted where an answer that
# carried it would have to put it, and each shaped so that a substring
# check for it cannot match by accident.
PLAINTEXT = "sk-reload-2b4d6f80-never-a-real-credential"

ROTATED = "sk-reload-7e1c3a95-also-never-a-real-credential"

ENV_NAME = "VINGA_RELOAD_SENTINEL_ENV_4d8e2a"

# What a refused row holds, which is what the fixed sentence exists to
# keep out of an answer.
REJECTED = "sk-stored-in-the-wrong-column-9b2e"

RELOAD_PATH = f"{MOUNT_PATH}/runtime/config/reload"

TOKEN = "test-api-token-" + "0123456789abcdef" * 2

API_SECRET_ENV = "VINGA_API_SECRET"


def bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The database directory a deployment names through its
    environment, which is what makes `load_boot_config` read this test's
    database rather than a real one's."""
    monkeypatch.delenv("VINGA_CONFIG", raising=False)
    monkeypatch.setenv("VINGA_SERVER__DATABASE__DIR", str(tmp_path / "db"))
    monkeypatch.setenv(API_SECRET_ENV, TOKEN)
    return tmp_path / "db"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> str:
    key = generate_key()
    monkeypatch.setenv(MASTER_KEY_ENV, key)
    return key


def seeded(directory: Path, secret: str | None = None, **entries: object) -> None:
    """A deployment's stored domain half, written the way the API writes
    it, plus whatever a case adds."""
    engine = open_database(directory)
    try:
        store = ConfigStore(engine, load_keys())
        for stage in STAGES:
            store.set_provider(stage, "mock", entries.get(stage, {"type": "mock"}))
        store.set_agent_defaults(dict.fromkeys(STAGES, "mock"))
        store.set_agent("assistant", {"prompt": "A"})
        store.set_default_agent("assistant")
        if secret is not None:
            store.set_secret(SecretLocation.provider("llm", "mock", "api_key"), secret)
    finally:
        engine.dispose()


def envelope_of(directory: Path) -> str:
    """The ciphertext the database holds for the planted slot, read as a
    row rather than through the store: what must not travel is the bytes
    on disk, so the sentinel has to be taken from the disk."""
    engine = open_database(directory)
    try:
        with engine.connect() as connection:
            secrets = connection.execute(
                text("select secrets from providers where stage = 'llm' and name = 'mock'")
            ).scalar_one()
    finally:
        engine.dispose()
    return str(json.loads(secrets)["api_key"]["enc"])


def mark_of(directory: Path) -> str:
    """The mark taken over what the database holds for the planted slot
    right now, which is the stored side of the comparison a later
    milestone will rebuild providers by."""
    engine = open_database(directory)
    try:
        snapshot = ConfigStore(engine, load_keys()).load()
    finally:
        engine.dispose()
    return snapshot.secrets.fingerprint("provider", "llm.mock")


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """What the server kept about a request, in both shipped formats."""
    return caplog.text + "".join(
        JsonFormatter().format(record) for record in caplog.records
    )


def failing(exc: Exception):
    def read() -> BootConfig:
        raise exc

    return read


def reloader(exc: Exception):
    """The composition root's closure over a read that refuses."""
    running = served(agents={"assistant": {"prompt": "A"}})
    return config_reloader(world(running), McpServers.build(running), failing(exc))


@pytest.mark.usefixtures("keys")
def test_a_stored_secret_that_will_not_open_refuses_under_a_fixed_sentence(
    directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-read verifies that every stored credential opens before it
    composes anything, so a deployment whose key has been rotated away
    from its secrets is refused with nothing swapped.

    The sentence is the route's own and not the store's: what the store
    would have said names the slot, and a reload's stored half is
    arbitrary bytes."""
    seeded(directory, secret=PLAINTEXT)
    booted = load_boot_config()

    with entered_client(booted.config, booted.secrets) as serving:
        monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
        refused = serving.post(RELOAD_PATH, headers=bearer())

    assert refused.status_code == 422
    assert refused.json() == problem(422, app_module.RELOAD_REFUSED)
    assert "api_key" not in refused.text


@pytest.mark.usefixtures("keys")
def test_a_stored_domain_that_will_not_compose_refuses_the_same_way(
    directory: Path,
) -> None:
    """Model-valid rows that are not a valid deployment, planted as a
    row because no write this server offers can produce one. What the
    row holds is as likely to be a credential pasted into the wrong
    column as a name somebody mistyped, which is the whole reason the
    sentence is fixed."""
    seeded(directory)
    booted = load_boot_config()
    engine = open_database(directory)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"update agents set llm = '{REJECTED}'"))  # noqa: S608
    finally:
        engine.dispose()

    with entered_client(booted.config, booted.secrets) as serving:
        refused = serving.post(RELOAD_PATH, headers=bearer())

    assert refused.status_code == 422
    assert refused.json() == problem(422, app_module.RELOAD_REFUSED)
    assert REJECTED not in refused.text


@pytest.mark.parametrize(
    ("raised", "sentence"),
    [
        (ConfigError, "RELOAD_REFUSED"),
        (StorageError, "RELOAD_UNREADABLE"),
        (DatabaseBusyError, "RELOAD_DATABASE_BUSY"),
    ],
)
async def test_a_refused_stored_half_keeps_its_type_and_loses_its_words(
    raised: type[ConfigError], sentence: str
) -> None:
    """The type is what the API turns into a status, so it survives; the
    words are composed over stored state, so they do not.

    Built in the handler and raised after it, which is load bearing:
    raised inside one, the replacement would carry the original as its
    context, and anything walking an exception chain would find the
    words again with the sanitizing bypassed."""
    apply = reloader(raised(f'agents.assistant.llm: unknown provider "{REJECTED}"'))

    with pytest.raises(raised) as caught:
        await apply()

    assert str(caught.value) == getattr(app_module, sentence)
    assert type(caught.value) is raised
    assert REJECTED not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_the_exclusion_refusal_keeps_its_own_words() -> None:
    """The one refusal the closure passes through as itself: a second
    apply while one is running is about this server's own exclusion and
    was composed over nothing stored, so replacing its sentence would
    lose the only advice it carries."""
    running = served(agents={"assistant": {"prompt": "A"}})
    held = _Held(running)
    apply = config_reloader(world(running), McpServers.build(running), held)

    first = asyncio.create_task(apply())
    await held.in_flight()
    try:
        with pytest.raises(ReloadInProgressError) as caught:
            await apply()
    finally:
        held.let_through()
        await first

    assert str(caught.value) == RELOAD_IN_PROGRESS
    assert "already running" in str(caught.value)


@pytest.mark.usefixtures("keys")
def test_neither_an_answer_nor_a_refusal_carries_a_credential(
    directory: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Every form of both sides, over both paths, in the body and in the
    log.

    Both sides, because an apply reads two: the world this server is
    serving and the one the database holds. The credential is rotated so
    that the two really differ, which means the running side holds one
    value and the stored side another, and each has a plaintext, a
    ciphertext and a mark of its own. Sentinels taken only before the
    rotation would leave the values the apply actually read on the
    stored side unchecked.
    """
    seeded(directory, secret=PLAINTEXT, llm={"type": "mock", "api_key_env": ENV_NAME})
    booted = load_boot_config()
    booted_side = (
        PLAINTEXT,
        envelope_of(directory),
        booted.secrets.fingerprint("provider", "llm.mock"),
    )

    with caplog.at_level("INFO"), entered_client(booted.config, booted.secrets) as serving:
        rotated = serving.put(
            f"{MOUNT_PATH}/providers/llm/mock/secrets/api_key",
            json={"secret": ROTATED},
            headers=bearer(),
        )
        assert rotated.status_code == 200, rotated.text
        stored_side = (ROTATED, envelope_of(directory), mark_of(directory))
        sentinels = (*booted_side, *stored_side, ENV_NAME)
        # Seven distinct strings that are all really there, so an
        # absence asserted below is an absence rather than an empty
        # needle or a value that was never read.
        assert len(set(sentinels)) == 7
        assert all(sentinels)

        answered = serving.post(RELOAD_PATH, headers=bearer())
        assert answered.status_code == 200, answered.text

        monkeypatch.setenv(MASTER_KEY_ENV, generate_key())
        refused = serving.post(RELOAD_PATH, headers=bearer())
        assert refused.status_code == 422

    for sentinel in sentinels:
        assert sentinel not in answered.text
        assert sentinel not in refused.text
        assert sentinel not in logged(caplog)
