"""One document, applied whole: what it writes, what it leaves alone,
and what it refuses.

The repository half of `config import`. What makes this a verb of the
repository rather than a loop above it is the transaction: a document
that creates an agent and binds a device to it in the same breath passes
through states no single write would accept, so the reference check runs
once against the state the whole document would leave, and any refusal
leaves nothing behind.

Three properties carry the file. Applying is additive, so a section the
document does not name is untouched and nothing here deletes. Applying
is idempotent by comparison rather than by rewrite, so the same document
twice is a no-op with an outcome listing that says so. And a refusal is
whole and reported whole: every mistake at once, in the sentences the
single writes earn, with the store afterwards exactly what it was.

The creation order is exercised where it is observable, which is not
here: an apply validates once against the finished candidate, so a
complete document resolves whichever order its entries were staged in.
What the order can be held to is the outcome listing, and the edges
themselves are driven through sequential single writes at the foot of
this file, where the wrong order is refused.
"""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet, MultiFernet

from vinga_server.config import ConfigError
from vinga_server.config.models import DOMAIN_KEYS, DatabaseConfig
from vinga_server.config.secrets import MASK, SecretLocation, generate_key
from vinga_server.config.store import APPLY_LIMIT, ConfigStore
from vinga_server.db import open_database

# Not a real credential, and shaped so a substring check for it cannot
# match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"


@pytest.fixture
def keys() -> MultiFernet:
    return MultiFernet([Fernet(generate_key())])


@pytest.fixture
def store(tmp_path: Path, keys: MultiFernet):
    engine = open_database(DatabaseConfig())
    try:
        yield ConfigStore(engine, keys)
    finally:
        engine.dispose()


# A whole deployment in one document, in the shape the domain half has:
# the settings in their DOMAIN shape (`devices` as a MAC holding its
# agents, `default_agent` a name) rather than the shape their own write
# routes take, because a document is the configuration and not a batch
# of requests.
DEPLOYMENT: dict[str, object] = {
    "providers": {
        "llm": {"claude": {"type": "anthropic", "model": "claude-sonnet-5"}},
        "asr": {"whisper": {"type": "faster_whisper", "model": "small"}},
        "tts": {"voice": {"type": "piper", "model": "es"}},
        "vad": {"silero": {"type": "silero"}},
    },
    "mcp_servers": {"home": {"transport": "stdio", "command": "uvx"}},
    "prompt_fragments": {"household": {"text": "The bins go out on Tuesday."}},
    "agent_defaults": {
        "llm": "claude",
        "asr": "whisper",
        "tts": "voice",
        "vad": "silero",
        "mcp": ["home"],
    },
    "agents": {"sam": {"prompt": "You are Sam.", "prompt_includes": ["household"]}},
    "devices": {"AA-BB-CC-DD-EE-FF": ["sam"]},
    "default_agent": "sam",
}


def test_one_document_writes_a_whole_deployment(store: ConfigStore) -> None:
    """The acceptance case, and the one a loop of single writes could
    not do: the agent, the device bound to it and the default agent
    naming it all arrive together, and every intermediate state on the
    way would have been refused by a per-entity check."""
    applied = store.apply(DEPLOYMENT)

    assert all(one.wrote for one in applied)
    snapshot = store.load()
    assert snapshot.domain.providers.llm["claude"].type == "anthropic"
    assert snapshot.domain.mcp_servers["home"].command == "uvx"
    assert snapshot.domain.prompt_fragments["household"].text.startswith("The bins")
    assert snapshot.domain.agent_defaults.mcp == ["home"]
    assert snapshot.domain.agents["sam"].prompt == "You are Sam."
    assert snapshot.domain.devices == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert snapshot.domain.default_agent == "sam"


def test_the_outcome_listing_comes_out_in_the_document_order(store: ConfigStore) -> None:
    """`DOMAIN_KEYS` is already documented as the write, read and
    creation order, and apply iterates it rather than a second list of
    it. The listing is the only order an apply observably has, which is
    what makes it the thing to assert: the check runs once against the
    finished candidate, so a complete document resolves whichever order
    its entries were staged in.

    The document below is written in the reverse of that order, so a
    listing that came out in the caller's order rather than the
    configuration's would fail here.
    """
    reversed_document = {key: DEPLOYMENT[key] for key in reversed(DOMAIN_KEYS)}

    sections = [one.section for one in store.apply(reversed_document)]

    assert sections == [
        "providers",
        "providers",
        "providers",
        "providers",
        "mcp_servers",
        "prompt_fragments",
        "agent_defaults",
        "agents",
        "devices",
        "default_agent",
    ]
    # And the sections themselves are `DOMAIN_KEYS` in its own order,
    # read off it rather than written down twice.
    assert list(dict.fromkeys(sections)) == list(DOMAIN_KEYS)


def test_a_provider_is_named_by_its_stage_and_name_together(store: ConfigStore) -> None:
    """A provider is addressed by two parameters everywhere else, and
    an outcome names it the same way, so a listing can be read against
    the document it came from."""
    applied = store.apply({"providers": {"llm": {"claude": {"type": "mock"}}}})

    assert [(one.section, one.identity) for one in applied] == [("providers", "llm.claude")]


def test_a_name_holding_a_dot_is_still_one_name(store: ConfigStore) -> None:
    """A provider's identity is its stage and its name joined by a dot,
    and nothing about a name forbids one, so the identity is split back
    into the parameters the kind is addressed by rather than at every
    dot it happens to hold."""
    applied = store.apply({"providers": {"llm": {"claude.v2": {"type": "mock"}}}})

    assert [(one.section, one.identity) for one in applied] == [
        ("providers", "llm.claude.v2")
    ]
    assert store.read_provider("llm", "claude.v2").entry.type == "mock"


# Idempotence


def test_the_same_document_twice_is_a_no_op(store: ConfigStore) -> None:
    """Comparison rather than blind rewrite: the second apply writes
    nothing, because every entry already describes the configuration
    that is there."""
    store.apply(DEPLOYMENT)

    again = store.apply(DEPLOYMENT)

    assert [one.wrote for one in again] == [False] * len(again)
    assert store.load().domain.agents["sam"].prompt == "You are Sam."


def test_only_the_entries_that_differ_are_written(store: ConfigStore) -> None:
    store.apply(DEPLOYMENT)
    changed = {**DEPLOYMENT, "agents": {"sam": {"prompt": "You are someone else."}}}

    applied = store.apply(changed)

    assert {(one.section, one.identity) for one in applied if one.wrote} == {
        ("agents", "sam")
    }
    assert store.load().domain.agents["sam"].prompt == "You are someone else."


def test_an_unchanged_singleton_is_reported_rather_than_rewritten(
    store: ConfigStore,
) -> None:
    """The agent defaults are a section that IS a body rather than a
    mapping of entries, so an empty one is the empty entry and an empty
    store already holds it."""
    applied = store.apply({"agent_defaults": {}})

    assert [(one.section, one.wrote) for one in applied] == [("agent_defaults", False)]


def test_a_body_spelling_a_default_it_holds_is_unchanged(store: ConfigStore) -> None:
    """The comparison is between the two entries, not between the two
    row bodies, and this is the case that decides it: a display shows a
    default that is a real value, so an exported body carries fields the
    write that created the entry never spelled. Comparing what a write
    would store would report every such entry as changed, and an export
    applied back onto its own store would rewrite most of itself.
    """
    store.set_mcp_server("home", {"transport": "stdio", "command": "uvx"})
    spelled = {
        "transport": "stdio",
        "command": "uvx",
        "tool_timeout_s": 15.0,
        "use_server_instructions": False,
    }

    applied = store.apply({"mcp_servers": {"home": spelled}})

    assert [one.wrote for one in applied] == [False]
    # And the values really are the entry's defaults, which is what
    # makes the two the same configuration.
    entry = store.read_mcp_server("home").entry
    assert (entry.tool_timeout_s, entry.use_server_instructions) == (15.0, False)


def test_a_value_the_display_would_mask_is_still_compared(store: ConfigStore) -> None:
    """The other comparison that was tried and rejected. A lowercase
    environment name in an `*_env` option is a value a write accepts and
    a read refuses to show, so comparing the two masked displays would
    call every such value equal to every other and skip the rotation
    silently."""
    store.set_provider("llm", "claude", {"type": "mock", "api_key_env": "first_name"})

    applied = store.apply(
        {"providers": {"llm": {"claude": {"type": "mock", "api_key_env": "second_name"}}}}
    )

    assert [one.wrote for one in applied] == [True]
    assert store.read_provider("llm", "claude").entry.api_key_env == "second_name"


# Additive


def test_a_section_the_document_does_not_name_is_untouched(store: ConfigStore) -> None:
    """Omission is additive, which is the rule that makes a partial
    document safe to apply: what is not mentioned is not an instruction
    to remove it."""
    store.apply(DEPLOYMENT)

    store.apply({"prompt_fragments": {"house_style": {"text": "Be brief."}}})

    snapshot = store.load()
    assert sorted(snapshot.domain.prompt_fragments) == ["house_style", "household"]
    assert snapshot.domain.agents["sam"].prompt == "You are Sam."
    assert snapshot.domain.devices == {"aa:bb:cc:dd:ee:ff": ["sam"]}
    assert snapshot.domain.default_agent == "sam"


def test_an_empty_document_and_an_empty_section_add_nothing(store: ConfigStore) -> None:
    store.apply(DEPLOYMENT)

    assert store.apply({}) == ()
    assert store.apply({"agents": {}, "providers": {}}) == ()

    assert sorted(store.load().domain.agents) == ["sam"]


def test_an_explicit_null_default_agent_clears_it(store: ConfigStore) -> None:
    """The one entry of a document that takes something away, and it is
    explicit: leaving the key out says nothing about the setting at
    all."""
    store.apply(DEPLOYMENT)

    applied = store.apply({"default_agent": None})

    assert [(one.section, one.wrote) for one in applied] == [("default_agent", True)]
    assert store.read_default_agent() is None
    # And clearing what is already clear is the unchanged outcome.
    assert [one.wrote for one in store.apply({"default_agent": None})] == [False]


def test_an_absent_default_agent_is_left_alone(store: ConfigStore) -> None:
    store.apply(DEPLOYMENT)

    applied = store.apply({"agents": {"sam": {"prompt": "You are Sam."}}})

    assert [one.section for one in applied] == ["agents"]
    assert store.read_default_agent() == "sam"


# Refused whole


def test_an_unresolvable_document_is_refused_whole_and_writes_nothing(
    store: ConfigStore,
) -> None:
    """The rollback, proven by reading the store back rather than by
    trusting the transaction: the provider and the fragment in this
    document are perfectly good, and neither of them lands, because the
    agent beside them names an engine nothing defines."""
    document = {
        "providers": {"llm": {"claude": {"type": "mock"}}},
        "prompt_fragments": {"household": {"text": "The bins go out on Tuesday."}},
        "agents": {"sam": {"prompt": "You are Sam.", "asr": "ghost"}},
    }

    with pytest.raises(ConfigError) as caught:
        store.apply(document)

    assert "agents.sam.asr: names no asr provider that exists" in str(caught.value)
    snapshot = store.load()
    assert snapshot.domain.providers.llm == {}
    assert snapshot.domain.prompt_fragments == {}
    assert snapshot.domain.agents == {}


def test_a_reference_refusal_is_the_sentence_a_single_write_earns(
    store: ConfigStore,
) -> None:
    """The wording is the single write's, unchanged: one reference pass
    produces one refusal whichever verb asked for it, so an operator
    reading an apply reads the sentence they would have read from a
    `set`."""
    fragment = {"prompt": "You are Sam.", "llm": "ghost"}

    with pytest.raises(ConfigError) as applied:
        store.apply({"agents": {"sam": fragment}})
    with pytest.raises(ConfigError) as written:
        store.set_agent("sam", fragment)

    assert str(applied.value) == str(written.value)


def test_every_bad_fragment_is_reported_at_once(store: ConfigStore) -> None:
    """A document is refused whole, so it is reported whole: an operator
    correcting one would otherwise need as many attempts as their file
    has mistakes. Each line is the sentence that single write earns, and
    the field problems travel beside them."""
    with pytest.raises(ConfigError) as caught:
        store.apply({"providers": {"llm": {"first": {}, "second": {}}}})

    message = str(caught.value)
    assert message.splitlines()[0] == "the document was refused whole and nothing was changed:"
    assert "invalid providers.llm.first:" in message
    assert "invalid providers.llm.second:" in message
    assert [problem.path for problem in caught.value.problems] == ["/type", "/type"]


def test_a_refused_document_never_quotes_what_it_was_sent(store: ConfigStore) -> None:
    """A document is a file an operator wrote, so a fragment inside one
    carries a pasted credential exactly as a fragment sent on its own
    does. Nothing on the refusal, and nothing behind it."""
    document = {
        "providers": {"llm": {"claude": {"type": "anthropic", "api_key": SECRET}}},
        "agents": {"sam": {"prompt": SECRET, "llm": SECRET}},
    }

    with pytest.raises(ConfigError) as caught:
        store.apply(document)

    assert SECRET not in _chain(caught.value)
    assert SECRET not in str(caught.value.problems)


def test_a_mask_with_nothing_behind_it_is_refused_in_a_document(
    store: ConfigStore,
) -> None:
    """The unchanged-value marker reaches an applied document because
    apply runs the same phases a single write runs, so a mask on an
    entity a document creates has nothing to keep, exactly as it has
    nothing to keep on a PUT that creates one."""
    with pytest.raises(ConfigError) as caught:
        store.apply({"providers": {"llm": {"fresh": {"type": "mock", "api_key_env": MASK}}}})

    assert "api_key_env" in str(caught.value)
    assert store.load().domain.providers.llm == {}


def test_a_mask_in_a_document_keeps_what_is_stored(store: ConfigStore) -> None:
    """And the other half: a document holding a read's own body keeps
    the value the display would not show, which is what makes an
    exported document applicable back onto the store it came from."""
    store.set_provider("llm", "claude", {"type": "anthropic", "api_key_env": "lowercase_name"})

    applied = store.apply(
        {"providers": {"llm": {"claude": {"type": "anthropic", "api_key_env": MASK}}}}
    )

    assert [one.wrote for one in applied] == [False]
    assert store.read_provider("llm", "claude").entry.api_key_env == "lowercase_name"


# The shape of the document itself


DOCUMENTS_REFUSED = [
    ("a document that is not a mapping", ["providers"], "document:"),
    ("a top-level key that is not a section", {"provider": {}}, "document:"),
    ("a section that is not a mapping", {"agents": ["sam"]}, "agents:"),
    ("a provider stage holding a list", {"providers": {"llm": ["claude"]}}, "providers:"),
    ("a stage that is not a stage", {"providers": {"ghost": {"a": {}}}}, "providers:"),
    ("a binding that is not a list", {"devices": {"aa:bb:cc:dd:ee:ff": "sam"}}, "devices:"),
    ("a default agent that is not a name", {"default_agent": 7}, "default_agent:"),
]


@pytest.mark.parametrize(
    ("document", "named"),
    [(document, named) for _, document, named in DOCUMENTS_REFUSED],
    ids=[what for what, _, _ in DOCUMENTS_REFUSED],
)
def test_a_document_of_the_wrong_shape_names_where_and_nothing_else(
    store: ConfigStore, document: object, named: str
) -> None:
    """One line naming where the shape is wrong and nothing of what was
    written there. A refusal about the document as a whole stands alone;
    one about a single entry is a line of the aggregate, because a
    document is reported whole."""
    with pytest.raises(ConfigError) as caught:
        store.apply(document)

    assert any(line.startswith(named) for line in str(caught.value).splitlines())
    assert store.load().domain.agents == {}


# Two entries addressing one thing
#
# A mapping cannot hold one key twice, so a document's own syntax rules
# out the obvious duplicate and rules out nothing else: a name is made
# canonical on the way in, and two keys that differ before that are one
# key after it. Left alone, both entries would be staged, both would be
# answered with an outcome, and the row would hold whichever was written
# last, which is a result the operator did not choose.

CANONICAL_DUPLICATES = [
    (
        "two spellings of one MAC",
        {"devices": {"AA-BB-CC-DD-EE-FF": ["sam"], "aa:bb:cc:dd:ee:ff": ["other"]}},
    ),
    (
        "one name with and without space",
        {"agents": {"fresh": {"prompt": "first"}, " fresh ": {"prompt": "second"}}},
    ),
    (
        "one provider name with and without space",
        {"providers": {"llm": {"c": {"type": "mock"}, " c ": {"type": "mock"}}}},
    ),
]


@pytest.mark.parametrize(
    "document",
    [document for _, document in CANONICAL_DUPLICATES],
    ids=[what for what, _ in CANONICAL_DUPLICATES],
)
def test_two_entries_addressing_one_thing_are_refused(
    store: ConfigStore, document: object
) -> None:
    """Refused rather than merged, for the reason a claim by code is:
    the two entries say different things about one thing and only
    whoever wrote them knows which is meant."""
    store.apply({"agents": {"sam": {"prompt": "p"}, "other": {"prompt": "p"}}})

    with pytest.raises(ConfigError) as caught:
        store.apply(document)

    assert str(caught.value).startswith("document:")
    assert "canonical" in str(caught.value)
    snapshot = store.load()
    assert snapshot.domain.devices == {}
    assert sorted(snapshot.domain.agents) == ["other", "sam"]
    assert snapshot.domain.providers.llm == {}


def test_the_same_entry_written_once_is_not_a_duplicate(store: ConfigStore) -> None:
    """The guard on the case above: what is refused is two entries
    addressing one thing, not one entry whose name needed normalizing."""
    applied = store.apply(
        {"devices": {"AA-BB-CC-DD-EE-FF": ["sam"]}, "agents": {" sam ": {"prompt": "p"}}}
    )

    assert [(one.section, one.identity) for one in applied] == [
        ("agents", "sam"),
        ("devices", "aa:bb:cc:dd:ee:ff"),
    ]


def test_every_malformed_section_is_reported_at_once(store: ConfigStore) -> None:
    """The structural phase aggregates like the two after it, and for
    the same reason: a document is refused whole, so a document with
    four malformed sections is one whose operator should be told about
    four. The providers section aggregates its own stage groups inside
    that, and the whole of it comes out under one headline rather than
    under a headline per level."""
    with pytest.raises(ConfigError) as caught:
        store.apply(
            {
                "providers": {"llm": ["nope"], "asr": "nope", "ghost": {"x": {}}},
                "prompt_fragments": "nope",
                "agents": ["nope"],
                "devices": ["nope"],
            }
        )

    lines = str(caught.value).splitlines()
    assert lines[0] == "the document was refused whole and nothing was changed:"
    assert lines.count(lines[0]) == 1
    # Two malformed stage groups, one word that is not a stage at all,
    # and one line per malformed section, in the document's own order.
    assert lines[1:] == [
        "providers: each stage holds a mapping of provider entries by name",
        "providers: each stage holds a mapping of provider entries by name",
        "providers: the stage has to be one of asr, llm, tts, vad",
        "prompt_fragments: this section has to be a mapping of entries by name",
        "agents: this section has to be a mapping of entries by name",
        "devices: this section has to be a mapping of entries by name",
    ]
    assert store.load().domain.agents == {}


def test_a_document_naming_more_entries_than_the_limit_is_refused_unmutated(
    store: ConfigStore,
) -> None:
    """Request hygiene, refused before anything is prepared: an applied
    document is one transaction, and one transaction that never ends is
    what a generated file can ask for by accident. The sentence names
    the limit and quotes nothing."""
    store.apply({"providers": {"llm": {"claude": {"type": "mock"}}}})
    over = {
        "agents": {f"agent-{number}": {"prompt": "p"} for number in range(APPLY_LIMIT + 1)}
    }

    with pytest.raises(ConfigError) as caught:
        store.apply(over)

    assert str(caught.value).startswith("document:")
    assert str(APPLY_LIMIT) in str(caught.value)
    assert store.load().domain.agents == {}
    # And exactly the limit goes through, so the bound is the one the
    # sentence names.
    at_limit = {"agents": {f"agent-{number}": {"prompt": "p"} for number in range(APPLY_LIMIT)}}
    assert len(store.apply(at_limit)) == APPLY_LIMIT


# The creation order, where it is observable
#
# An apply cannot demonstrate it: the check runs once against the
# finished candidate state, so a complete document resolves whichever
# order its entries were staged in, and a test that applied a document
# in the wrong order would pass. Where the order bites is a sequence of
# single writes, which is what an operator without a document does, and
# what `DOMAIN_KEYS` documents as the creation order. One case per
# reference edge, each asserting that the write before the referent
# exists is refused and the write after it is not.

REFERENCE_EDGES = [
    (
        "an agent's provider",
        lambda store: store.set_agent("sam", {"prompt": "p", "llm": "claude"}),
        lambda store: store.set_provider("llm", "claude", {"type": "mock"}),
    ),
    (
        "an agent's MCP server",
        lambda store: store.set_agent("sam", {"prompt": "p", "mcp": ["home"]}),
        lambda store: store.set_mcp_server("home", {"transport": "stdio", "command": "uvx"}),
    ),
    (
        "an agent's prompt fragment",
        lambda store: store.set_agent("sam", {"prompt": "p", "prompt_includes": ["household"]}),
        lambda store: store.set_prompt_fragment("household", {"text": "The bins."}),
    ),
    (
        "a device's agent",
        lambda store: store.bind_device("aa:bb:cc:dd:ee:ff", ["sam"]),
        lambda store: store.set_agent("sam", {"prompt": "p"}),
    ),
    (
        "the default agent",
        lambda store: store.set_default_agent("sam"),
        lambda store: store.set_agent("sam", {"prompt": "p"}),
    ),
]


@pytest.mark.parametrize(
    ("write", "referent"),
    [(write, referent) for _, write, referent in REFERENCE_EDGES],
    ids=[what for what, _, _ in REFERENCE_EDGES],
)
def test_a_single_write_is_refused_before_what_it_references_exists(
    store: ConfigStore, write, referent
) -> None:
    with pytest.raises(ConfigError):
        write(store)

    referent(store)
    write(store)


def _chain(exc: BaseException) -> str:
    """Everything an exception carries, including what a chain walker
    would find behind it."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def test_a_secret_stored_on_an_entity_survives_applying_it_again(
    store: ConfigStore,
) -> None:
    """An apply writes the model-shaped half and never the secrets
    column, exactly as a `set` does: a document reapplied over an entity
    that has a credential stored on it leaves the credential where it
    is."""
    store.apply({"providers": {"llm": {"claude": {"type": "mock"}}}})
    where = SecretLocation.provider("llm", "claude", "api_key")
    store.set_secret(where, SECRET)

    store.apply({"providers": {"llm": {"claude": {"type": "mock", "model": "m"}}}})

    assert store.load().secrets.secret(where) == SECRET


# Every reference edge, with a credential where the name goes
#
# The refusals a broken reference earns are the ones a document is most
# likely to earn: a document is written by hand, an entity is written
# beside its references, and a reference is a bare word, which is what a
# credential is too. So each edge is driven here with a planted
# credential as the name that will not resolve, and the fragment holding
# it is otherwise VALID: a fragment refused in preparation never reaches
# the reference pass at all, which is how the first version of this
# suite tested nothing.
#
# The path and the names that do exist are what the refusals may carry,
# because a deployment wrote both. The name that did not resolve is what
# they may not, and it is what these look for.

REFERENCE_LEAKS = [
    ("an agent's provider", {"agents": {"a": {"prompt": "p", "llm": SECRET}}}),
    ("an agent's MCP server", {"agents": {"a": {"prompt": "p", "mcp": [SECRET]}}}),
    (
        "an agent's MCP grant",
        {"agents": {"a": {"prompt": "p", "mcp": [{"server": SECRET, "tools": ["t"]}]}}},
    ),
    (
        "an agent's prompt fragment",
        {"agents": {"a": {"prompt": "p", "prompt_includes": [SECRET]}}},
    ),
    ("the agent defaults' provider", {"agent_defaults": {"tts": SECRET}}),
    ("a device's agent", {"devices": {"aa:bb:cc:dd:ee:ff": [SECRET]}}),
    ("the default agent", {"default_agent": SECRET}),
]


@pytest.fixture
def populated(store: ConfigStore) -> ConfigStore:
    """A store with one of everything, so that the hint a refusal
    carries has something to list and the fragments below are valid."""
    store.apply(DEPLOYMENT)
    return store


@pytest.mark.parametrize(
    "document",
    [document for _, document in REFERENCE_LEAKS],
    ids=[what for what, _ in REFERENCE_LEAKS],
)
def test_an_unresolved_reference_never_quotes_the_name(
    populated: ConfigStore, document: object
) -> None:
    with pytest.raises(ConfigError) as caught:
        populated.apply(document)

    message = str(caught.value)
    assert "names no" in message
    assert "not quoted back" in message
    assert SECRET not in _chain(caught.value)
    assert SECRET not in str(caught.value.problems)


@pytest.mark.parametrize(
    "document",
    [document for _, document in REFERENCE_LEAKS],
    ids=[what for what, _ in REFERENCE_LEAKS],
)
def test_the_reference_pass_is_reached_at_all(
    populated: ConfigStore, document: object
) -> None:
    """The guard on the two cases above and on the surfaces beside them.
    A fragment that fails its model is refused in preparation, and its
    refusal is a different sentence with a different rule about what it
    may quote, so a case that never reached the reference pass would be
    asserting the wrong boundary's promise."""
    with pytest.raises(ConfigError) as caught:
        populated.apply(document)

    assert str(caught.value).startswith("the change was refused")


# The two validators a document reaches that are not reference checks
#
# A binding that names one agent twice and an MCP entry name that is not
# a usable tool prefix are both refused by the models rather than by the
# reference pass, and both used to quote what they rejected. Both are
# reachable from a document exactly as they are from a single write, so
# both are driven here with a credential in the rejected position.

VALIDATOR_LEAKS = [
    (
        "a binding naming one agent twice",
        {"devices": {"aa:bb:cc:dd:ee:ff": ["sam", "sam"]}},
        "more than one position",
    ),
    (
        "an entry name that is not a tool prefix",
        {"mcp_servers": {f"{SECRET}.pasted": {"transport": "stdio", "command": "uvx"}}},
        "must match [A-Za-z0-9_-]+",
    ),
]


@pytest.mark.parametrize(
    ("document", "rule"),
    [(document, rule) for _, document, rule in VALIDATOR_LEAKS],
    ids=[what for what, _, _ in VALIDATOR_LEAKS],
)
def test_a_refused_validator_names_the_rule_and_not_the_value(
    populated: ConfigStore, document: object, rule: str
) -> None:
    with pytest.raises(ConfigError) as caught:
        populated.apply(document)

    assert rule in str(caught.value)
    assert SECRET not in _chain(caught.value)
    assert SECRET not in str(caught.value.problems)


def test_a_binding_naming_one_agent_twice_never_prints_it(
    populated: ConfigStore,
) -> None:
    """The planted value in the position the old sentence quoted: the
    duplicate itself, which the refusal now names by position."""
    with pytest.raises(ConfigError) as caught:
        populated.apply({"devices": {"aa:bb:cc:dd:ee:ff": [SECRET, SECRET]}})

    assert "more than one position (1, 2)" in str(caught.value)
    assert SECRET not in _chain(caught.value)


def test_a_binding_naming_one_agent_twice_with_different_spacing_is_still_twice(
    populated: ConfigStore,
) -> None:
    """The names are compared as they will be stored, which is trimmed,
    so the two spellings of one name are the one name they become rather
    than a binding that silently holds it once."""
    with pytest.raises(ConfigError) as caught:
        populated.apply({"devices": {"aa:bb:cc:dd:ee:ff": ["sam", "  sam  "]}})

    assert "more than one position (1, 2)" in str(caught.value)
