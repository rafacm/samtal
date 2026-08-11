"""The two validation phases, run against a snapshot on their own.

Boot runs both and reports them together, which the loader tests
already pin. What matters here is that they separate: writes run the
reference half, so a half-built configuration can be built up in the
natural order, and the completeness half stays where a runnable server
is decided.
"""

from dataclasses import dataclass, field

from samtal_server.config.models import (
    AgentConfig,
    AgentDefaults,
    McpServerConfig,
    ProviderConfig,
    ProvidersConfig,
    check_completeness,
    check_references,
)


@dataclass
class Snapshot:
    """A domain snapshot that is not a Config, which is the point: the
    checks run against the attributes, not against the class."""

    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)
    agent_defaults: AgentDefaults = field(default_factory=AgentDefaults)
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    devices: dict[str, list[str]] = field(default_factory=dict)
    default_agent: str | None = None


def _providers(**llm: str) -> ProvidersConfig:
    return ProvidersConfig(llm={name: ProviderConfig(type=type_) for name, type_ in llm.items()})


def test_a_resolved_snapshot_has_no_problems() -> None:
    snapshot = Snapshot(
        providers=_providers(claude="anthropic"),
        agents={"sam": AgentConfig(llm="claude")},
        default_agent="sam",
    )

    assert check_references(snapshot) == []
    assert check_completeness(snapshot) == []


def test_an_unknown_provider_reference_is_a_reference_problem() -> None:
    snapshot = Snapshot(
        providers=_providers(claude="anthropic"),
        agents={"sam": AgentConfig(llm="ghost")},
        default_agent="sam",
    )

    problems = check_references(snapshot)

    assert problems == ['agents.sam.llm: unknown llm provider "ghost" (defined: claude)']


def test_an_unknown_mcp_reference_is_a_reference_problem() -> None:
    snapshot = Snapshot(agents={"sam": AgentConfig(mcp=["home"])}, devices={"aa": ["sam"]})

    problems = check_references(snapshot)

    assert problems == [
        'agents.sam.mcp: unknown MCP server "home"; no mcp_servers entries are defined'
    ]


def test_an_unknown_binding_and_default_are_reference_problems() -> None:
    snapshot = Snapshot(devices={"aa:bb:cc:dd:ee:ff": ["ghost"]}, default_agent="nobody")

    problems = check_references(snapshot)

    assert 'default_agent "nobody" is not a defined agent' in problems
    assert 'devices.aa:bb:cc:dd:ee:ff: agent "ghost" is not a defined agent' in problems


def test_the_first_agent_is_a_completeness_problem_only() -> None:
    """The write-time deadlock this split exists for: an agent cannot be
    created before default_agent names it, and default_agent cannot name
    it before it exists. So writing the agent has to be allowed, and
    booting on it must not be."""
    snapshot = Snapshot(agents={"sam": AgentConfig()})

    assert check_references(snapshot) == []
    assert check_completeness(snapshot) == [
        "default_agent is required when agents are defined and no device is "
        "bound to one; set it to one of: sam"
    ]


def test_a_bound_device_makes_the_default_agent_optional() -> None:
    snapshot = Snapshot(
        agents={"sam": AgentConfig()},
        devices={"aa:bb:cc:dd:ee:ff": ["sam"]},
    )

    assert check_completeness(snapshot) == []


def test_an_empty_snapshot_passes_both_checks() -> None:
    """Where every deployment starts, and where the natural creation
    order begins."""
    snapshot = Snapshot()

    assert check_references(snapshot) == []
    assert check_completeness(snapshot) == []
