"""Comparing what a server is serving with what the database holds.

Two composed worlds and the secrets loaded beside each, which is what
the comparison takes: no database is opened here, because nothing about
judging two configurations equal needs one. The MCP half arrives as the
registry's own answer, so a case about it here is about carrying that
answer through under the right label; what the registry answers is
`test_mcp_pending.py`.

Two properties carry the file. Every kind is reported by the name an
operator addresses it by, a provider's being its stage and its name
together. And nothing but names and labels comes out: the planted
credential, the ciphertext holding it and the mark taken over it are
each asserted absent from the whole answer.
"""

import dataclasses

from cryptography.fernet import Fernet, MultiFernet

from tests.support.configs import config_with
from tests.support.tools_mcp import entry_data
from vinga_server.config import Config
from vinga_server.config.boot import BootConfig
from vinga_server.config.diff import (
    APPLIES,
    GRANTS_APPLY,
    Applies,
    ConfigDiff,
    LiveKind,
    McpPending,
    config_diff,
)
from vinga_server.config.models import DOMAIN_KEYS
from vinga_server.config.secrets import SecretLocation, SecretStore, encrypt, generate_key

# Not a real credential, and shaped so a substring check for it cannot
# match by accident. Planted in a stored slot, where an answer that
# carried values rather than names would have to put it.
SECRET = "sk-test-9c4e17ab-never-a-real-credential"

MOCK = {"type": "mock"}
STAGES = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")


def side(config: Config, secrets: SecretStore | None = None) -> BootConfig:
    """One side of the comparison, in the shape a running server has it:
    a composed configuration and the store it was loaded with.

    `BootConfig` is what both sides really are, and a deployment whose
    credentials are all environment references has an empty store rather
    than none, which is the same world.
    """
    return BootConfig(config, secrets if secrets is not None else SecretStore())


def providers(**stages: dict[str, object]) -> dict[str, dict[str, object]]:
    """The four stages with a mock entry each, plus whatever a case
    names of its own. Every stage has to resolve, since the composed
    configuration validates its own references."""
    return {stage: {"mock": MOCK} | stages.get(stage, {}) for stage in STAGES}


def diff_of(
    running: Config,
    stored: Config,
    running_secrets: SecretStore | None = None,
    stored_secrets: SecretStore | None = None,
) -> ConfigDiff:
    """The two worlds compared, with the MCP half empty: a case about
    the MCP half hands one in."""
    return config_diff(
        side(running, running_secrets), side(stored, stored_secrets), McpPending()
    )


# What each kind is compared by


def test_two_worlds_that_agree_report_nothing() -> None:
    config = config_with(providers=providers())

    answer = diff_of(config, config)

    assert (answer.providers.added, answer.providers.removed) == ((), ())
    assert answer.providers.changed == ()
    assert answer.prompt_fragments.changed == ()
    assert answer.agents.changed == ()
    assert answer.agent_defaults.changed is False


def test_a_provider_is_named_by_its_stage_and_its_name() -> None:
    """Two stages may hold the same name, so neither half of the pair
    identifies an entry on its own."""
    running = config_with(providers=providers())
    stored = config_with(providers=providers(llm={"local": MOCK}, tts={"local": MOCK}))

    answer = diff_of(running, stored)

    assert answer.providers.added == ("llm.local", "tts.local")
    assert answer.providers.removed == ()


def test_a_provider_that_is_gone_is_removed_and_an_edited_one_is_changed() -> None:
    running = config_with(providers=providers(llm={"local": MOCK, "spare": MOCK}))
    stored = config_with(
        providers=providers(llm={"local": {"type": "mock", "reply": "hello"}})
    )

    answer = diff_of(running, stored)

    assert answer.providers.added == ()
    assert answer.providers.removed == ("llm.spare",)
    assert answer.providers.changed == ("llm.local",)


def test_a_rotated_stored_secret_reports_the_provider_as_changed() -> None:
    """The entry is byte-identical on both sides, so the credential
    behind it is the whole of what the comparison has to see: an
    operator who rotates one has changed what this provider talks to as
    surely as one who edits its base URL."""
    keys = MultiFernet([Fernet(generate_key())])
    location = SecretLocation.provider("llm", "mock", "api_key")
    envelope = encrypt(location, SECRET, keys)
    before = SecretStore({location: envelope}, keys)
    after = SecretStore({location: encrypt(location, "a-new-value", keys)}, keys)
    config = config_with(providers=providers())

    answer = diff_of(config, config, running_secrets=before, stored_secrets=after)

    assert answer.providers.changed == ("llm.mock",)
    # And nothing of any of it travels: not the plaintext, not the
    # ciphertext the database holds, and not the mark taken over it.
    rendered = repr(answer)
    assert SECRET not in rendered
    assert envelope["enc"] not in rendered
    assert before.fingerprint("provider", "llm.mock") not in rendered


def test_one_provider_s_secret_says_nothing_about_another_s() -> None:
    """The mark is per entity, so a rotation is reported against the
    entry it happened on and against nothing else."""
    keys = MultiFernet([Fernet(generate_key())])
    location = SecretLocation.provider("llm", "mock", "api_key")
    config = config_with(providers=providers(llm={"local": MOCK}))

    answer = diff_of(
        config,
        config,
        running_secrets=SecretStore({location: encrypt(location, SECRET, keys)}, keys),
        stored_secrets=SecretStore({location: encrypt(location, "a-new-value", keys)}, keys),
    )

    assert answer.providers.changed == ("llm.mock",)


def test_prompt_fragments_are_compared_by_what_they_say() -> None:
    running = config_with(prompt_fragments={"house": {"text": "Be brief."}})
    stored = config_with(
        prompt_fragments={"house": {"text": "Be brief and kind."}, "extra": {"text": "More."}}
    )

    answer = diff_of(running, stored)

    assert answer.prompt_fragments.added == ("extra",)
    assert answer.prompt_fragments.changed == ("house",)
    assert answer.prompt_fragments.removed == ()


def test_the_agent_defaults_answer_with_a_boolean() -> None:
    """There is one of them, so there is nothing to name: it moved or it
    did not."""
    running = config_with(providers=providers(tts={"alto": MOCK}))
    stored = config_with(
        providers=providers(tts={"alto": MOCK}), agent_defaults=STAGES | {"tts": "alto"}
    )

    assert diff_of(running, running).agent_defaults.changed is False
    assert diff_of(running, stored).agent_defaults.changed is True


def test_an_agent_that_arrives_or_goes_is_named() -> None:
    running = config_with(agents={"assistant": {"prompt": "A"}, "helper": {"prompt": "H"}})
    stored = config_with(agents={"assistant": {"prompt": "A"}, "poet": {"prompt": "P"}})

    answer = diff_of(running, stored)

    assert answer.agents.added == ("poet",)
    assert answer.agents.removed == ("helper",)
    assert answer.agents.changed == ()


def test_an_edited_agent_prompt_is_pending_a_restart() -> None:
    running = config_with(agents={"assistant": {"prompt": "A"}})
    stored = config_with(agents={"assistant": {"prompt": "Something else"}})

    answer = diff_of(running, stored)

    assert answer.agents.changed == ("assistant",)
    assert answer.agents.applies is Applies.RESTART


# The agent's two regimes


def granting(*grants: object) -> Config:
    """One agent, granted whatever the case is about of the one entry."""
    return config_with(
        mcp_servers={"tools": entry_data()},
        agents={"assistant": {"prompt": "A", "mcp": list(grants)}},
    )


def test_a_grants_only_edit_is_not_claimed_pending_restart() -> None:
    """An agent's `mcp` list is what a reload derives its tools from, so
    an edit to it is not waiting for a restart. What moved is reported
    under the grants, by the registry that knows whether the running
    world already has it."""
    answer = diff_of(granting("tools"), granting())

    assert answer.agents.changed == ()
    assert answer.agents.added == ()
    assert answer.agents.removed == ()


def test_an_edit_beside_the_grants_is_still_pending_a_restart() -> None:
    """The exclusion is one field and not a general softening: the rest
    of the entry converges where it always did."""
    running = granting("tools")
    stored = config_with(
        mcp_servers={"tools": entry_data()},
        agents={"assistant": {"prompt": "Something else", "mcp": []}},
    )

    assert diff_of(running, stored).agents.changed == ("assistant",)


def test_the_grants_answer_is_the_registry_s_and_rides_the_reload() -> None:
    config = granting("tools")

    answer = config_diff(side(config), side(config), McpPending(grants=("assistant",)))

    assert answer.agents.grants.changed == ("assistant",)
    assert answer.agents.grants.applies is GRANTS_APPLY
    assert answer.agents.grants.applies is Applies.RELOAD


def test_the_mcp_entries_are_the_registry_s_answer_under_the_reload_label() -> None:
    """Carried through rather than computed: the boot's entries are not
    the running server's, because a reload can have replaced them."""
    config = config_with(mcp_servers={"tools": entry_data()})

    answer = config_diff(
        side(config),
        side(config),
        McpPending(added=("weather",), removed=("old",), changed=("tools",)),
    )

    assert (answer.mcp_servers.added, answer.mcp_servers.removed) == (("weather",), ("old",))
    assert answer.mcp_servers.changed == ("tools",)
    assert answer.mcp_servers.applies is Applies.RELOAD


# The kinds that are already live


def test_the_live_kinds_answer_with_their_label_and_no_comparison() -> None:
    """A device binding and the default agent are read per check-in, so
    a write of either is in effect within seconds and was never pending.
    The label keeps the knowledge of why here rather than in every
    consumer, and there is deliberately nothing beside it to read."""
    running = config_with(agents={"assistant": {"prompt": "A"}}, default_agent="assistant")
    stored = config_with(
        agents={"assistant": {"prompt": "A"}, "helper": {"prompt": "H"}},
        devices={"aa:bb:cc:dd:ee:ff": ["helper"]},
        default_agent="helper",
    )

    answer = diff_of(running, stored)

    assert answer.devices == LiveKind(Applies.CHECK_IN)
    assert answer.default_agent == LiveKind(Applies.CHECK_IN)


# The map, held to the domain


def test_every_domain_kind_carries_a_regime_and_a_row() -> None:
    """The completeness pin. The domain declares its kinds once, and a
    seventh added next year must not fall silently out of this answer:
    it arrives with this test failing, naming the module that has to
    place it.
    """
    assert tuple(APPLIES) == DOMAIN_KEYS
    assert tuple(field.name for field in dataclasses.fields(ConfigDiff)) == DOMAIN_KEYS
