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

from typing import get_args

import pytest
from cryptography.fernet import Fernet, MultiFernet
from pydantic import BaseModel, ValidationError

from tests.support.configs import config_with
from tests.support.tools_mcp import entry_data
from vinga_server.config import Config
from vinga_server.config.boot import BootConfig
from vinga_server.config.diff import (
    APPLIES,
    FALLBACK_APPLY,
    FILLER_APPLY,
    GRANTS_APPLY,
    PROMPT_APPLY,
    McpPending,
    config_diff,
)
from vinga_server.config.models import DOMAIN_KEYS
from vinga_server.config.responses import (
    AgentsDiff,
    Applies,
    ConfigDiff,
    DiffApplies,
    EntityDiff,
    FallbackDiff,
    FillerDiff,
    GrantsDiff,
    LiveKind,
    PromptDiff,
    SingletonDiff,
)
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


def test_an_edited_agent_prompt_is_pending_a_reload() -> None:
    """A prompt is assembled at an activation, so a reload puts the new
    text in front of the next one. The whole entry is a reload's now, so
    the edit is in `changed` as well, and the prompt half is what says
    which of the three clocks it is on."""
    running = config_with(agents={"assistant": {"prompt": "A"}})
    stored = config_with(agents={"assistant": {"prompt": "Something else"}})

    answer = diff_of(running, stored)

    assert answer.agents.changed == ("assistant",)
    assert answer.agents.prompt.changed == ("assistant",)
    assert answer.agents.prompt.applies is PROMPT_APPLY
    assert answer.agents.prompt.applies is Applies.RELOAD
    assert answer.agents.applies is Applies.RELOAD


def test_an_edited_include_list_rides_the_prompt_half_too() -> None:
    """`prompt_includes` names the shared fragments an assembly injects,
    which the same activation resolves, so it converges where the prompt
    does."""
    running = config_with(
        prompt_fragments={"house": {"text": "The house is quiet."}},
        agents={"assistant": {"prompt": "A"}},
    )
    stored = config_with(
        prompt_fragments={"house": {"text": "The house is quiet."}},
        agents={"assistant": {"prompt": "A", "prompt_includes": ["house"]}},
    )

    answer = diff_of(running, stored)

    assert answer.agents.changed == ("assistant",)
    assert answer.agents.prompt.changed == ("assistant",)


def test_a_fragment_edit_is_pending_a_reload_and_names_no_agent() -> None:
    """The fragment is its own kind, and a reload applies it whole. What
    it is included by is not reported against every agent that carries
    it: the change has one home and that is where it is named."""
    running = config_with(prompt_fragments={"house": {"text": "Quiet."}})
    stored = config_with(prompt_fragments={"house": {"text": "Loud."}})

    answer = diff_of(running, stored)

    assert answer.prompt_fragments.changed == ("house",)
    assert answer.prompt_fragments.applies is Applies.RELOAD


# The agent's two regimes


def granting(*grants: object) -> Config:
    """One agent, granted whatever the case is about of the one entry."""
    return config_with(
        mcp_servers={"tools": entry_data()},
        agents={"assistant": {"prompt": "A", "mcp": list(grants)}},
    )


def test_a_grants_only_edit_is_the_registry_s_to_report() -> None:
    """An agent's `mcp` list is what a reload derives its tools from.
    The entry moved, so the agent is in `changed`; whether what it may
    reach moved is the registry's answer, and this comparison is handed
    an empty one, so nothing is claimed about the tools."""
    answer = diff_of(granting("tools"), granting())

    assert answer.agents.changed == ("assistant",)
    assert answer.agents.grants.changed == ()
    assert answer.agents.added == ()
    assert answer.agents.removed == ()


def test_a_grant_rewritten_in_the_other_form_is_no_edit_at_all() -> None:
    """The same grant has two spellings: the entry name on its own is
    the whole server, and so is the object naming that server with no
    tool list. An operator who rewrote one as the other has written
    nothing a reload would install, and an answer reporting the agent as
    pending would send them looking for an edit that is not there."""
    answer = diff_of(granting("tools"), granting({"server": "tools"}))

    assert answer.agents.changed == ()
    assert (answer.agents.added, answer.agents.removed) == ((), ())
    assert answer.agents.grants.changed == ()
    assert answer.agents.prompt.changed == ()
    assert answer.agents.filler.changed == ()
    assert answer.agents.fallback.changed == ()
    assert answer.agent_defaults.changed is False


def test_the_layer_under_every_agent_reads_its_grants_the_same_way() -> None:
    """`agent_defaults` holds the same field under the same rule, and a
    comparison that knew the two spellings for an agent's own list and
    not for the one it inherits would answer the same edit two ways."""
    stages = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

    def defaults(*grants: object) -> Config:
        return config_with(
            mcp_servers={"tools": entry_data()},
            agent_defaults=stages | {"mcp": list(grants)},
            agents={"assistant": {"prompt": "A"}},
        )

    answer = diff_of(defaults("tools"), defaults({"server": "tools"}))

    assert answer.agent_defaults.changed is False
    assert answer.agents.changed == ()


def test_an_agent_opting_out_of_the_tools_it_inherits_is_an_edit() -> None:
    """Unset and empty are two states and not one: an agent with no
    `mcp` list of its own inherits the layer's, and an agent naming an
    empty one replaces that list with nothing. The edit between them
    revokes every tool the agent had at the next reload, so a comparison
    that read the absent list as an empty one would report nothing
    pending for the change that takes them all away."""
    stages = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")

    def agent(**own: object) -> Config:
        return config_with(
            mcp_servers={"tools": entry_data()},
            agent_defaults=stages | {"mcp": ["tools"]},
            agents={"assistant": {"prompt": "A"} | own},
        )

    answer = diff_of(agent(), agent(mcp=[]))

    assert answer.agents.changed == ("assistant",)
    assert answer.agent_defaults.changed is False


def test_an_edit_beside_the_three_halves_is_a_change_of_the_entry() -> None:
    """The three halves are a breakdown and not the whole of what an
    entry holds. Which provider entry serves a stage is on none of them,
    and a reload applies it with the rest, so it is reported as the
    entry changing and under none of the three."""
    running = granting("tools")
    stored = config_with(
        providers={
            "llm": {"mock": {"type": "mock"}, "other": {"type": "mock"}},
            "asr": {"mock": {"type": "mock"}},
            "tts": {"mock": {"type": "mock"}},
            "vad": {"mock": {"type": "mock"}},
        },
        mcp_servers={"tools": entry_data()},
        agents={"assistant": {"prompt": "A", "mcp": [], "llm": "other"}},
    )

    answer = diff_of(running, stored)

    assert answer.agents.changed == ("assistant",)
    assert answer.agents.prompt.changed == ()
    assert answer.agents.filler.changed == ()


def test_a_filler_only_edit_rides_the_filler_half() -> None:
    """An agent's own `filler` section is what a reload synthesizes its
    next session's clips from, so the half it is reported under is what
    says when the edit reaches a conversation: the next one, rather than
    the activation the prompt half converges at."""
    running = config_with(agents={"assistant": {"prompt": "A"}})
    stored = config_with(
        agents={
            "assistant": {
                "prompt": "A",
                "filler": {"enabled": True, "phrases": ["Hmm..."]},
            }
        }
    )

    answer = diff_of(running, stored)

    assert answer.agents.changed == ("assistant",)
    assert answer.agents.filler.changed == ("assistant",)
    assert answer.agents.filler.applies is FILLER_APPLY
    assert answer.agents.filler.applies is Applies.RELOAD
    assert answer.agents.applies is Applies.RELOAD


def test_a_fallback_only_edit_rides_the_fallback_half() -> None:
    """The failure phrase is its own cached clip, so it is its own half:
    an operator who reworded what a broken turn says has asked for that
    phrase to be spoken again in each agent's voice and for nothing else
    to be sent to one."""
    running = config_with(agents={"assistant": {"prompt": "A"}})
    stored = config_with(
        agents={"assistant": {"prompt": "A", "fallback": {"phrase": "Sorry, I broke."}}}
    )

    answer = diff_of(running, stored)

    assert answer.agents.changed == ("assistant",)
    assert answer.agents.fallback.changed == ("assistant",)
    assert answer.agents.fallback.applies is FALLBACK_APPLY
    assert answer.agents.fallback.applies is Applies.RELOAD
    # And the filler half is untouched by it, which is the whole reason
    # the two are separate: toggling one must not send the other's
    # phrases to a voice.
    assert answer.agents.filler.changed == ()


def test_a_filler_only_edit_leaves_the_fallback_half_alone() -> None:
    """The same claim from the other side, so neither direction rests on
    the other's evidence."""
    running = config_with(agents={"assistant": {"prompt": "A"}})
    stored = config_with(
        agents={
            "assistant": {
                "prompt": "A",
                "filler": {"enabled": True, "phrases": ["Hmm..."]},
            }
        }
    )

    answer = diff_of(running, stored)

    assert answer.agents.filler.changed == ("assistant",)
    assert answer.agents.fallback.changed == ()


def test_an_agent_defaults_filler_edit_is_reported_against_the_layer() -> None:
    """The layer under every agent is not the agent's own half. What
    `agent_defaults.filler` holds is inherited by every agent that
    configures none, and a reload applies the layer whole, so the edit
    is reported against `agent_defaults` and against no agent's own
    filler section."""
    stages = dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
    running = config_with(agent_defaults=stages, agents={"assistant": {"prompt": "A"}})
    stored = config_with(
        agent_defaults=stages | {"filler": {"enabled": True, "phrases": ["Hmm..."]}},
        agents={"assistant": {"prompt": "A"}},
    )

    answer = diff_of(running, stored)

    assert answer.agent_defaults.changed is True
    assert answer.agent_defaults.applies is Applies.RELOAD
    assert answer.agents.filler.changed == ()


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

    assert answer.devices == LiveKind(applies=Applies.CHECK_IN)
    assert answer.default_agent == LiveKind(applies=Applies.CHECK_IN)


# The map, held to the domain


def test_every_domain_kind_carries_a_regime_and_a_row() -> None:
    """The completeness pin. The domain declares its kinds once, and a
    seventh added next year must not fall silently out of this answer:
    it arrives with this test failing, naming the module that has to
    place it.
    """
    assert tuple(APPLIES) == DOMAIN_KEYS
    # And the published shape with it, since the answer is the model the
    # route sends: a kind the map placed and the response left out would
    # be a kind no client can read.
    assert tuple(ConfigDiff.model_fields) == DOMAIN_KEYS


# The vocabulary, held from both ends
#
# `Applies` is what the server states a boundary in, and a comparison
# can announce three of its four members: nothing pending against this
# process is what a write to a server serving a handed configuration is
# waiting on, so `store-boot` is a write's answer and never a diff's.
# The narrowing is the alias `DiffApplies`, and it is only worth
# anything if both halves hold: that the two sets together are the whole
# enum, and that the seven fields really carry the alias.


def test_the_diff_and_the_write_between_them_name_every_boundary() -> None:
    """A fifth boundary cannot be added on one side alone.

    Membership bookkeeping, deliberately: it says the alias and the one
    member left out of it account for the enum, so a member added to
    `Applies` and not placed here arrives with this failing.
    """
    assert set(get_args(DiffApplies)) | {Applies.STORE_BOOT} == set(Applies)


# Every model of the comparison that carries a boundary, with the rest
# of its fields at their emptiest, so that what a case varies is the
# boundary alone. Written out rather than derived, and held to being all
# of them by the completeness assertion below.
NARROWED: dict[type[BaseModel], dict[str, object]] = {
    EntityDiff: {"added": (), "removed": (), "changed": ()},
    AgentsDiff: {
        "added": (),
        "removed": (),
        "changed": (),
        "grants": GrantsDiff(applies=Applies.RELOAD, changed=()),
        "prompt": PromptDiff(applies=Applies.RELOAD, changed=()),
        "filler": FillerDiff(applies=Applies.RELOAD, changed=()),
        "fallback": FallbackDiff(applies=Applies.RELOAD, changed=()),
    },
    GrantsDiff: {"changed": ()},
    PromptDiff: {"changed": ()},
    FillerDiff: {"changed": ()},
    FallbackDiff: {"changed": ()},
    SingletonDiff: {"changed": False},
    LiveKind: {},
}


def _models_in(annotation: object) -> list[type[BaseModel]]:
    """Every model an annotation names, however deeply it is wrapped.

    Walked rather than matched on a container, because what a field of
    this answer is declared as is the answer's business and not this
    test's: a kind carried in a list, an optional or a union tomorrow
    is still a model of the comparison.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for argument in get_args(annotation) for model in _models_in(argument)]


def _reachable(root: type[BaseModel]) -> set[type[BaseModel]]:
    """Every model the comparison's answer is composed of, itself
    included."""
    found: set[type[BaseModel]] = set()
    queue = [root]
    while queue:
        model = queue.pop()
        if model in found:
            continue
        found.add(model)
        queue += [
            nested
            for field in model.model_fields.values()
            for nested in _models_in(field.annotation)
        ]
    return found


def test_every_boundary_a_comparison_carries_is_one_of_the_narrowed_models() -> None:
    """The completeness pin for the table above, and the one place the
    narrowing is asked about rather than assumed.

    Reached from `ConfigDiff` and selected on carrying a boundary at
    all, deliberately: a set selected on already being narrowed would
    answer the question with itself, so a model added to this read and
    typed with the whole enum would drop out of the comparison instead
    of failing it. Which is the shape of the defect this pin exists for:
    a fifth boundary reaching a field that never sends it.

    The narrowing is therefore its own assertion, made over the models
    this walk found rather than over the ones the table lists.
    """
    carrying = {
        model for model in _reachable(ConfigDiff) if "applies" in model.model_fields
    }

    assert carrying == set(NARROWED)
    for model in carrying:
        assert model.model_fields["applies"].annotation is DiffApplies, model.__name__


@pytest.mark.parametrize("model", list(NARROWED), ids=lambda model: model.__name__)
def test_a_comparison_cannot_announce_the_boundary_it_never_reaches(
    model: type[BaseModel],
) -> None:
    """Constructed rather than inspected, which is the half the
    membership pin above cannot reach: seven fields could still be typed
    `Applies` while the alias and the enum accounted for each other, and
    the contract would declare a value this read never sends.

    Both directions, so the narrowing is not merely a refusal: every
    boundary a comparison does announce is still accepted.
    """
    with pytest.raises(ValidationError):
        model(applies=Applies.STORE_BOOT, **NARROWED[model])

    for boundary in get_args(DiffApplies):
        assert model(applies=boundary, **NARROWED[model]).applies is boundary
