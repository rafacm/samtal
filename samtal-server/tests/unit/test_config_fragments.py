"""Shared prompt fragments, and the lists that include them.

The section itself is small: a name, and a block of text promised
verbatim. What this file mostly pins is the discipline around it. A
fragment name and an include are written beside prompt text, which is
where an operator pastes things, so neither a bad name nor an
unresolved include is ever repeated back: the refusals name the
section, the layer, the position and the rule, and the sentinel below
is looked for in the whole exception chain, since a message built
inside an exception handler drags the rejected input along behind it.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from samtal_server.config import Config
from samtal_server.config.loader import ConfigError, StorageError, compose_config
from samtal_server.config.models import (
    AgentConfig,
    AgentDefaults,
    FileConfig,
    McpServerConfig,
    PromptFragmentConfig,
    ProvidersConfig,
    check_prompt_fragment_names,
    check_references,
)
from samtal_server.config.store import ConfigStore
from samtal_server.db import open_database, schema
from samtal_server.runtime.prompt import Fragment
from tests.support.configs import config_with

# Not a real credential. Written in the safe charset on purpose: that is
# the whole reason the charset rule does not close the leak on its own,
# so an include holding this is refused without being echoed.
SENTINEL = "sk-test-4f8b2c9e-never-a-real-credential"

# The same value where the charset itself fails, which is the other
# refusal that must not quote what it was handed.
UNUSABLE_SENTINEL = f"{SENTINEL}.pasted"

# A short credential sentinel, and a name-shaped unusable one built from
# it. Short because a long value can be lost to something truncating a
# representation, which would make an absence assertion pass for the
# wrong reason.
SHORT_SENTINEL = "hunter2"
SHORT_UNUSABLE = f"{SHORT_SENTINEL}.pasted"

# A body whose shape is the assertion: leading indentation, an inner
# blank line and a trailing newline, all of which a stripping type would
# quietly take away.
VERBATIM = "  The kitchen radio is called Bosse.\n\n    The bins go out on Tuesday.\n"


def chain(exc: BaseException) -> str:
    """Everything reachable from one exception: its own text, and every
    cause and context behind it."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


class Snapshot:
    """A domain snapshot that is not a Config, so the checks are run
    against attributes rather than against a class, the way
    `test_config_checks.py` runs them."""

    def __init__(
        self,
        prompt_fragments: dict[str, PromptFragmentConfig] | None = None,
        agent_defaults: AgentDefaults | None = None,
        agents: dict[str, AgentConfig] | None = None,
    ) -> None:
        self.providers = ProvidersConfig()
        self.mcp_servers: dict[str, McpServerConfig] = {}
        self.prompt_fragments = prompt_fragments or {}
        self.agent_defaults = agent_defaults or AgentDefaults()
        self.agents = agents or {}
        self.devices: dict[str, list[str]] = {}
        self.default_agent: str | None = None


# The section


def test_a_fragment_carries_its_text_verbatim() -> None:
    config = config_with(prompt_fragments={"household": {"text": VERBATIM}})

    assert config.prompt_fragments["household"].text == VERBATIM


def test_a_fragment_is_written_as_a_mapping_and_not_as_a_bare_string() -> None:
    """The shape every entity travels in: a stored row, a read envelope
    and a written fragment are all mappings, and a one-field mapping
    leaves room for a second field without changing what a client
    writes."""
    with pytest.raises(ValidationError):
        config_with(prompt_fragments={"household": "just the text"})

    assert PromptFragmentConfig.model_validate({"text": "a"}).text == "a"


@pytest.mark.parametrize("written", ["", "   ", "\n\n"])
def test_a_blank_fragment_body_is_refused_by_the_rule_and_not_by_its_value(
    written: str,
) -> None:
    """Non-blank is checked on a stripped copy and the original is what
    is kept, the type decision the guidance field made first."""
    with pytest.raises(ValidationError, match="only whitespace") as caught:
        config_with(prompt_fragments={"household": {"text": written}})

    assert "leave the key out" in str(caught.value)


UNUSABLE_NAMES = ["house rules", "household.facts", "hushåll", UNUSABLE_SENTINEL, SHORT_UNUSABLE]


@pytest.mark.parametrize("name", UNUSABLE_NAMES)
def test_an_unusable_fragment_name_names_the_section_and_the_rule_only(name: str) -> None:
    """Deliberately not the entry-name refusal, which interpolates what
    it rejected: a name that fails the charset is exactly the string
    that must not be echoed.

    Asserted against the rule itself rather than against a model, and
    every name is looked for rather than one sentinel: pydantic renders
    the input it rejected into a raw `ValidationError`, keys included,
    so a test that asked a model this question would be asking about
    pydantic's rendering rather than about the sentence this repository
    produces. Where that rendering could otherwise escape is the test
    below."""
    with pytest.raises(ValueError) as caught:
        check_prompt_fragment_names({name: PromptFragmentConfig(text="a")})

    rendered = chain(caught.value)
    assert "prompt_fragments" in rendered
    assert "[A-Za-z0-9_-]+" in rendered
    assert name not in rendered
    assert SENTINEL not in rendered
    assert SHORT_SENTINEL not in rendered


@pytest.mark.parametrize("name", UNUSABLE_NAMES)
def test_no_boundary_hands_out_pydantics_rendering_of_the_name(
    tmp_path: Path, name: str
) -> None:
    """The three ways an unusable name reaches a model, and what each of
    them answers with.

    Pydantic puts the input it rejected into a `ValidationError`, so the
    raw exception carries the whole mapping and its keys. No operator
    ever meets one: every boundary renders the error's locations and
    messages only and raises its own refusal after the handler, leaving
    neither a cause nor a context to walk back to. That is what makes the
    charset rule's promise true rather than merely stated, and it is
    what this pins, on the write, at boot, and on a row that got into the
    table some other way.

    The sentinels are short on purpose: a long one could be hidden by
    something truncating a representation rather than by anything
    keeping it out.
    """
    engine = open_database(tmp_path / "db")
    caught: list[BaseException] = []
    try:
        store = ConfigStore(engine)
        with pytest.raises(ConfigError) as written:
            store.set_prompt_fragment(name, {"text": "a"})
        caught.append(written.value)

        with pytest.raises(ConfigError) as booted:
            compose_config(
                FileConfig(),
                {"prompt_fragments": {name: {"text": "a"}}},
                "the test's database",
            )
        caught.append(booted.value)

        # A row nothing here wrote, which is how such a name gets stored
        # at all: a hand edit, a restore, a database from somewhere else.
        with engine.begin() as connection:
            connection.execute(schema.prompt_fragments.insert().values(name=name, text="a"))
        with pytest.raises(StorageError) as loaded:
            store.load()
        caught.append(loaded.value)
    finally:
        engine.dispose()

    for problem in caught:
        rendered = chain(problem)
        assert "prompt_fragments" in rendered
        assert name not in rendered
        assert SENTINEL not in rendered
        assert SHORT_SENTINEL not in rendered
        # The tell of a raw pydantic error travelling out behind ours.
        assert "input_value" not in rendered


def test_a_reserved_tool_name_is_a_usable_fragment_name() -> None:
    """The charset is shared with the entry-name rule; the reserved
    names are not, because a fragment is in no tool list."""
    config = config_with(prompt_fragments={"remember": {"text": "a"}})

    assert config.prompt_fragments["remember"].text == "a"


# The include lists


def test_an_include_list_is_optional_on_both_layers() -> None:
    config = config_with()

    assert config.agent_defaults.prompt_includes is None
    assert config.agents["assistant"].prompt_includes is None


def test_an_empty_include_list_is_kept_apart_from_an_unset_one() -> None:
    """`[]` opts a layer out of the fragments its siblings share, which
    only means something if it is a different value from unset."""
    config = config_with(
        prompt_fragments={"household": {"text": "a"}},
        agent_defaults=dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
        | {"prompt_includes": ["household"]},
        agents={"assistant": {"prompt": "A", "prompt_includes": []}},
    )

    assert config.agent_defaults.prompt_includes == ["household"]
    assert config.agents["assistant"].prompt_includes == []


@pytest.mark.parametrize("layer", ["agent_defaults", "agents"])
def test_a_fragment_named_twice_is_refused_by_position(layer: str) -> None:
    written = ["household", "household"]
    overrides: dict[str, object] = (
        {"agent_defaults": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
         | {"prompt_includes": written}}
        if layer == "agent_defaults"
        else {"agents": {"assistant": {"prompt": "A", "prompt_includes": written}}}
    )

    with pytest.raises(ValidationError, match="more than one position") as caught:
        config_with(prompt_fragments={"household": {"text": "a"}}, **overrides)

    assert "(1, 2)" in str(caught.value)


def test_a_duplicate_refusal_names_positions_and_never_the_name() -> None:
    with pytest.raises(ValidationError) as caught:
        config_with(
            prompt_fragments={SENTINEL: {"text": "a"}},
            agents={"assistant": {"prompt": "A", "prompt_includes": [SENTINEL, SENTINEL]}},
        )

    assert SENTINEL not in chain(caught.value)


# References, at write time and at boot


def test_a_resolved_include_is_no_problem() -> None:
    snapshot = Snapshot(
        prompt_fragments={"household": PromptFragmentConfig(text="a")},
        agents={"sam": AgentConfig(prompt_includes=["household"])},
    )

    assert check_references(snapshot) == []


@pytest.mark.parametrize("layer", ["agent_defaults", "agents.sam"])
def test_an_unknown_include_names_the_layer_the_position_and_the_rule(layer: str) -> None:
    includes = ["household", "missing"]
    snapshot = Snapshot(
        prompt_fragments={"household": PromptFragmentConfig(text="a")},
        agent_defaults=(
            AgentDefaults(prompt_includes=includes) if layer == "agent_defaults" else None
        ),
        agents={
            "sam": AgentConfig(prompt_includes=includes if layer == "agents.sam" else None)
        },
    )

    problems = check_references(snapshot)

    assert problems == [
        f"{layer}.prompt_includes: entry 2 names no prompt fragment that exists, and "
        f"the name is not quoted back (defined: household)"
    ]


def test_an_unknown_include_with_no_fragments_at_all_says_so() -> None:
    snapshot = Snapshot(agents={"sam": AgentConfig(prompt_includes=["household"])})

    assert check_references(snapshot) == [
        "agents.sam.prompt_includes: entry 1 names no prompt fragment that exists, and "
        "the name is not quoted back; no prompt_fragments entries are defined"
    ]


@pytest.mark.parametrize("layer", ["agent_defaults", "agents"])
def test_an_unresolved_include_fails_the_boot_without_quoting_it(layer: str) -> None:
    """The same refusal reached the way a server reaches it, through the
    whole-snapshot validator, since that is the path a boot log carries."""
    overrides: dict[str, object] = (
        {"agent_defaults": dict.fromkeys(("llm", "asr", "tts", "vad"), "mock")
         | {"prompt_includes": [SENTINEL]}}
        if layer == "agent_defaults"
        else {"agents": {"assistant": {"prompt": "A", "prompt_includes": [SENTINEL]}}}
    )

    with pytest.raises(ValidationError) as caught:
        config_with(prompt_fragments={"household": {"text": "a"}}, **overrides)

    rendered = chain(caught.value)
    assert "prompt_includes: entry 1" in rendered
    assert SENTINEL not in rendered


# What an agent's prompt is made of


def _resolving(**layers: object) -> Config:
    return config_with(
        prompt_fragments={
            "household": {"text": "The bins go out on Tuesday."},
            "style": {"text": "Answer in one sentence."},
        },
        **layers,
    )


def _defaults(**overrides: object) -> dict[str, object]:
    return dict.fromkeys(("llm", "asr", "tts", "vad"), "mock") | overrides


def test_an_agent_inherits_the_defaults_includes() -> None:
    config = _resolving(agent_defaults=_defaults(prompt_includes=["household"]))

    assert config.fragments_for_agent("assistant") == [
        Fragment("household", "The bins go out on Tuesday.")
    ]


def test_an_agents_own_list_replaces_the_inherited_one() -> None:
    """Replaces rather than extends, exactly like `mcp`: what an agent
    lists is all of what it includes."""
    config = _resolving(
        agent_defaults=_defaults(prompt_includes=["household"]),
        agents={"assistant": {"prompt": "A", "prompt_includes": ["style"]}},
    )

    assert config.fragments_for_agent("assistant") == [
        Fragment("style", "Answer in one sentence.")
    ]


def test_an_empty_list_opts_an_agent_out_of_what_its_siblings_share() -> None:
    config = _resolving(
        agent_defaults=_defaults(prompt_includes=["household"]),
        agents={
            "assistant": {"prompt": "A", "prompt_includes": []},
            "sibling": {"prompt": "S"},
        },
        devices={},
    )

    assert config.fragments_for_agent("assistant") == []
    assert [block.name for block in config.fragments_for_agent("sibling")] == ["household"]


def test_the_fragments_are_resolved_in_the_order_they_are_listed() -> None:
    """Listed order rather than alphabetical: what the operator wrote is
    what the model reads."""
    config = _resolving(
        agents={"assistant": {"prompt": "A", "prompt_includes": ["style", "household"]}}
    )

    assert [block.name for block in config.fragments_for_agent("assistant")] == [
        "style",
        "household",
    ]


def test_an_agent_that_includes_nothing_carries_no_fragments() -> None:
    assert _resolving().fragments_for_agent("assistant") == []


def test_a_composed_configuration_with_fragments_boots() -> None:
    """The whole path once, so the section is proven to compose rather
    than only to validate field by field."""
    config = Config(
        providers={"llm": {"mock": {"type": "mock"}}},
        prompt_fragments={"household": {"text": VERBATIM}},
        agent_defaults={"llm": "mock", "prompt_includes": ["household"]},
        agents={"assistant": {"prompt": "A"}},
        default_agent="assistant",
    )

    assert config.prompt_fragments["household"].text == VERBATIM
    assert config.agent_defaults.prompt_includes == ["household"]
