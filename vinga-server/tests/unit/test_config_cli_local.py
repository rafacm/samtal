"""The break-glass path: what it is for, and that it says what the API says.

`--local` is the way in when the server will not start. It is exactly
four commands (`show`, `delete`, `set-secret`, `clear-secret`) run
against the database with nothing to ask, and the conditions it exists
to repair are the ones where asking is not available: a row the loader
refuses, a master key that will not load, a name written before the
addressability rule existed and unreachable over a URL path.

Two claims are held here, and they are the same claim from two sides.

The first is the subset itself: which commands it covers and what the
refusal names when a command is outside it, that a recovery run reaches
no server at all, that only storing a secret needs a usable key, and
that the preamble printed before every local invocation makes no timing
claim of its own, since it is printed ahead of acts a running server
applies without a restart.

The second is that one act run both ways is one act. Since #139 both
paths are one row in the CLI's dispatch table, and what these tests hold
is the claim that row makes: for every act with a break-glass path, the
acknowledgement and the notice printed are the ones the API answers the
same act with, and a read prints the same document either way.

That comparison is not of whole invocations, and cannot be. Every
`--local` run prints the preamble on stderr first, by design, since
there is no reliable way to tell whether a server is running against the
same file and saying what this path is, is the honest substitute. So the
preamble is peeled off and everything after it has to match the other
path exactly.
"""

from pathlib import Path

import pytest
from sqlalchemy import update

from tests.support.config_cli import (
    FRAGMENT_INPUT,
    FRAGMENT_TEXT,
    OTHER_SECRET,
    SECRET,
    runner,
)
from tests.support.config_cli import document as _document
from vinga_server.config import cli
from vinga_server.config.entities import NO_SUCH_PROVIDER
from vinga_server.config.secrets import MASK, MASTER_KEY_ENV
from vinga_server.config.writes import BINDING_NOTICE
from vinga_server.db import open_database, schema

# A credential shaped like a variable name: it gets past the models'
# paste check, which only asks that a reference look like a name, and is
# what the display path's own rule has to catch.
PASTED = "sk_test_4f8b2c9e_never_a_real_credential"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(tmp_path, monkeypatch)


# The preamble, spelled out rather than read from the module under test.
# Comparing production against itself would let the retired sentence
# back in: restoring "a running server will not observe a change made
# this way until its next start" would move both sides of that
# comparison together and pass, while printing a timing claim the write
# under it contradicts. The neutral sentence is therefore a literal
# here, and the one place the two are tied together is the assertion
# just below.
LOCAL_PREAMBLE = (
    "--local is the break-glass path: it reads and writes the database directly, "
    "bypassing the configuration API. Each write says separately when it takes "
    "effect, the same answer the API gives for the same act."
)

# How restart timing has been written on this path, in the words it has
# been written in: the two halves of RESTART_NOTICE, and the clause the
# retired preamble made the claim with. An act a reload applies may
# carry none of them, whichever line they turn up on. Kept as phrases
# rather than as the word "restart", which RELOAD_NOTICE uses
# legitimately to say that none is needed.
RESTART_TIMING = (
    "until its next start",
    "at the next server start",
    "read once at boot",
)


def test_the_local_preamble_makes_no_timing_claim_of_its_own() -> None:
    """Every --local invocation prints this before the command runs, so
    a timing claim in it is a timing claim about every act, including
    the ones a running server applies without a restart. It says what
    the path is and leaves when to the write."""
    assert cli.LOCAL_NOTICE == LOCAL_PREAMBLE
    for phrasing in RESTART_TIMING:
        assert phrasing not in cli.LOCAL_NOTICE, phrasing


# What each act needs in the database before it can be run


def _a_provider(run) -> None:
    run(
        "set",
        "provider",
        "llm",
        "claude",
        "-f",
        "-",
        stdin="type: anthropic\nmodel: m\napi_key_env: ANTHROPIC_API_KEY\n",
    )


def _an_mcp_server(run) -> None:
    run("set", "mcp-server", "home", "-f", "-", stdin="transport: stdio\ncommand: uvx\n")


def _a_prompt_fragment(run) -> None:
    run("set", "prompt-fragment", "household", "-f", "-", stdin="text: The bins go out.\n")


def _an_unreferenced_agent(run) -> None:
    """Nothing names it, so the delete is not refused for a reason that
    has nothing to do with what it would then say."""
    run("set", "agent", "sam", "-f", "-", stdin="prompt: You are Sam.\n")


def _the_agent_defaults(run) -> None:
    _a_provider(run)
    run("set", "agent-defaults", "-f", "-", stdin="llm: claude\n")


def _a_bound_device(run) -> None:
    _an_unreferenced_agent(run)
    run("bind-device", "aa:bb:cc:dd:ee:ff", "sam")


def _a_provider_secret(run) -> None:
    _a_provider(run)
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)


def _an_mcp_secret(run) -> None:
    _an_mcp_server(run)
    run("set-secret", "mcp-server", "home", "env.API_TOKEN", stdin=OTHER_SECRET)


def _everything(run) -> None:
    _a_provider_secret(run)
    _an_mcp_secret(run)
    _a_prompt_fragment(run)
    run("set", "agent-defaults", "-f", "-", stdin="llm: claude\n")
    _a_bound_device(run)
    run("set-default-agent", "sam")


# The mutating half: five deletes, and each secret command on each kind
# of entity a secret lives on, which is the whole `--local` write subset
# as the grammar stands. Each case is what the act needs in the database,
# the act itself, and whether it is one a running server applies by
# reloading, which is the column only the second of the two proofs below
# reads.
MUTATIONS = [
    (_a_provider, ("delete", "provider", "llm", "claude"), False),
    (_an_mcp_server, ("delete", "mcp-server", "home"), True),
    (_a_prompt_fragment, ("delete", "prompt-fragment", "household"), False),
    (_an_unreferenced_agent, ("delete", "agent", "sam"), False),
    (_a_bound_device, ("delete", "device", "aa:bb:cc:dd:ee:ff"), False),
    (_a_provider, ("set-secret", "provider", "llm", "claude", "api_key"), False),
    (_an_mcp_server, ("set-secret", "mcp-server", "home", "env.API_TOKEN"), True),
    (_a_provider_secret, ("clear-secret", "provider", "llm", "claude", "api_key"), False),
    (_an_mcp_secret, ("clear-secret", "mcp-server", "home", "env.API_TOKEN"), True),
]


@pytest.mark.parametrize(
    ("seed", "argv"),
    [(seed, argv) for seed, argv, _ in MUTATIONS],
    ids=[" ".join(argv) for _, argv, _ in MUTATIONS],
)
def test_a_local_write_acknowledges_what_the_api_acknowledges(
    run, capsys: pytest.CaptureFixture[str], seed, argv: tuple[str, ...]
) -> None:
    """The act run both ways against equivalent state: the line on
    stdout is the same line, and the notice under it is the same notice,
    with the preamble the only thing between them.

    Equivalent is established rather than assumed. A write naming only
    an entity's model-shaped columns leaves its stored secrets where
    they were, so seeding a provider again after a set-secret would make
    the second run a rotation where the first was a creation; the entity
    is taken out and seeded again between the runs. A delete has already
    left nothing behind.
    """
    typed = SECRET if argv[0] == "set-secret" else None

    seed(run)
    capsys.readouterr()
    assert run(*argv, stdin=typed) == 0
    answered = capsys.readouterr()

    if argv[0] != "delete":
        assert run("delete", *argv[1:-1]) == 0

    seed(run)
    capsys.readouterr()
    assert run("--local", *argv, stdin=typed) == 0

    said = capsys.readouterr()
    assert said.out == answered.out
    assert said.err.splitlines() == [LOCAL_PREAMBLE, *answered.err.splitlines()]


# What a local write says it did, against what the API says for the same
# act
#
# The two paths write the same rows, so they may not describe one act
# differently, and a sentence copied into the break-glass branch by hand
# is a sentence that drifts. The expected value here is therefore not a
# constant but the ordinary path's own answer for the same act, captured
# a moment earlier against state put back to what the local run then
# meets, so a change to either side that the other does not follow fails
# rather than passing quietly.
#
# The nine cases are the whole `--local` mutating subset as the grammar
# stands: five deletes, and set-secret and clear-secret on each kind of
# entity a secret lives on. That completeness is kept by review, not by
# machinery: the grammar is imperative parser construction, so a new
# `local_ok=True` command that skips this list fails nothing.


@pytest.mark.parametrize(
    ("seed", "argv", "reloadable"),
    MUTATIONS,
    ids=[" ".join(argv) for _, argv, _ in MUTATIONS],
)
def test_a_local_write_says_what_the_api_says_for_the_same_act(
    run, capsys: pytest.CaptureFixture[str], seed, argv: tuple[str, ...], reloadable: bool
) -> None:
    """Run one act both ways against equivalent state, and pin the whole
    shape of what the break-glass path printed: what it is, then exactly
    the sentence the ordinary path answered, and nothing else.

    Equivalent is established between the runs rather than assumed, by
    taking the entity out and seeding it again; the comment below says
    what re-seeding alone would have left behind.

    Not the last line alone. The contradiction this exists to catch is a
    preamble that reasserts restart timing ahead of a reload notice,
    which a last-line comparison would step straight over."""
    typed = SECRET if argv[0] == "set-secret" else None

    seed(run)
    capsys.readouterr()
    assert run(*argv, stdin=typed) == 0
    answered = capsys.readouterr().err.rstrip("\n")

    # Back to nothing before the second run, because re-seeding is not
    # by itself a reset: a write that names only an entity's
    # model-shaped columns leaves the rest as it was, the secrets column
    # above all, so seeding a provider again after a set-secret leaves
    # the credential the act just stored. The second run would then be
    # rotating a secret where the first created one, which is a
    # different act from the one being compared. A delete takes the row
    # and its stored secrets together; the acts that are deletes have
    # already left nothing behind, and there is nothing to address.
    if argv[0] != "delete":
        assert run("delete", *argv[1:-1]) == 0

    seed(run)
    capsys.readouterr()
    assert run("--local", *argv, stdin=typed) == 0

    said = capsys.readouterr().err
    assert said.splitlines() == [LOCAL_PREAMBLE, answered]
    if reloadable:
        # Said out loud for the acts the reload applies: the restart
        # sentence must not appear anywhere in this invocation, preamble
        # included, and neither must the phrasings a differently worded
        # restart claim would be made in.
        assert cli.RESTART_NOTICE not in said
        for phrasing in RESTART_TIMING:
            assert phrasing not in said, phrasing


def test_a_local_device_delete_says_the_same_thing(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The break-glass path writes the same row, so it says the same
    sentence: the two paths must not describe one act differently."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set", "agent", "sam", "-f", "-", stdin="llm: claude\n")
    run("bind-device", "aa:bb:cc:dd:ee:ff", "sam")
    capsys.readouterr()

    assert run("--local", "delete", "device", "aa:bb:cc:dd:ee:ff") == 0

    assert BINDING_NOTICE in capsys.readouterr().err


# The reading half, which has no acknowledgement: a read's whole output
# is its answer, so what the two paths have to agree on is the document.
READS = [
    (_a_provider_secret, ("show", "provider", "llm", "claude")),
    (_an_mcp_secret, ("show", "mcp-server", "home")),
    (_a_prompt_fragment, ("show", "prompt-fragment", "household")),
    (_an_unreferenced_agent, ("show", "agent", "sam")),
    (_the_agent_defaults, ("show", "agent-defaults")),
    (_a_bound_device, ("show", "device", "aa:bb:cc:dd:ee:ff")),
    (_everything, ("show",)),
]


@pytest.mark.parametrize(("seed", "argv"), READS, ids=[" ".join(argv) for _, argv in READS])
def test_a_local_read_shows_what_the_api_shows(
    run, capsys: pytest.CaptureFixture[str], seed, argv: tuple[str, ...]
) -> None:
    """One entity masked by the view the API answers with, and the whole
    configuration the same way. Nothing is re-seeded between the runs,
    because neither run changes anything: that a read leaves the
    database as it found it is part of what is being said."""
    seed(run)
    capsys.readouterr()

    assert run(*argv) == 0
    answered = capsys.readouterr()

    assert run("--local", *argv) == 0

    said = capsys.readouterr()
    assert said.out == answered.out
    # A read makes no claim about when anything applies, so the preamble
    # is the whole of what the break-glass path adds.
    assert said.err.splitlines() == [LOCAL_PREAMBLE]
    assert answered.err == ""


def test_the_masked_values_are_masked_on_both_paths(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control for the comparison above: two paths printing the same
    document prove nothing if the document is empty of the thing worth
    checking. Every read compared here is of an entity holding a stored
    secret, and this says what that looks like."""
    _a_provider_secret(run)
    capsys.readouterr()

    assert run("--local", "show", "provider", "llm", "claude") == 0

    shown = capsys.readouterr().out
    assert "api_key: ********" in shown
    assert "used instead of api_key_env: ANTHROPIC_API_KEY" in shown
    assert SECRET not in shown


def test_an_agent_is_printed_prompt_first(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The printed document, byte for byte, which is the other half of
    the same claim the API's exact-bytes pin makes: YAML keeps the order
    the mapping was built in, so an agent read on either path opens with
    the prompt that makes it that agent and then says what it
    overrides."""
    _a_provider(run)
    run(
        "set",
        "agent",
        "sam",
        "-f",
        "-",
        stdin="llm: claude\nprompt: You are Sam.\n",
    )
    capsys.readouterr()

    assert run("--local", "show", "agent", "sam") == 0

    assert capsys.readouterr().out == "prompt: You are Sam.\nllm: claude\n"


def test_a_credential_nested_in_an_option_is_masked_in_the_rendered_document(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The masking is the view's and the rendering is the CLI's, so the
    depth the view masks at is the depth the printed YAML masks at. An
    option can be a structure, a reference key one level down accepts
    anything shaped like a variable name, and this is the command an
    operator runs when they suspect they pasted one."""
    run(
        "set",
        "provider",
        "llm",
        "claude",
        "-f",
        "-",
        stdin=f"type: anthropic\nconnection:\n  api_key_env: {PASTED}\n  host: example\n",
    )
    capsys.readouterr()

    assert run("--local", "show", "provider", "llm", "claude") == 0

    shown = capsys.readouterr().out
    assert MASK in shown
    assert "host: example" in shown
    assert PASTED not in shown


# The recovery subset


def test_every_local_invocation_says_what_it_is(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """There is no reliable way to tell whether a server is running
    against the same file, so the honest substitute for a refusal is
    saying what this path is, every time, reads included."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()

    assert run("--local", "show") == 0
    assert "bypassing the configuration API" in capsys.readouterr().err

    assert run("--local", "delete", "provider", "llm", "claude") == 0
    assert "bypassing the configuration API" in capsys.readouterr().err


def test_the_recovery_subset_needs_no_server(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The situation --local exists for: the four commands run against
    the database with nothing to ask, which is what `reached` staying
    empty says."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run.reached.clear()
    capsys.readouterr()

    assert run("--local", "set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 0
    assert run("--local", "show", "provider", "llm", "claude") == 0
    shown = capsys.readouterr().out
    assert f"api_key: {MASK}" in shown
    assert SECRET not in shown

    assert run("--local", "clear-secret", "provider", "llm", "claude", "api_key") == 0
    assert run("--local", "delete", "provider", "llm", "claude") == 0
    assert run.reached == []


def test_the_recovery_subset_works_with_a_key_that_will_not_load(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `VINGA_MASTER_KEY` that is not a Fernet key is one of the exact
    conditions --local exists to repair: it refuses the boot, so there is
    no server to ask, and reading the keys eagerly would refuse the
    recovery tool for the same reason.

    Reading, deleting and clearing all treat ciphertext as opaque, so
    none of them needs a key at all."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET)
    run.reached.clear()
    capsys.readouterr()
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-fernet-key")

    assert run("--local", "show") == 0
    whole = capsys.readouterr().out
    assert MASK in whole
    assert SECRET not in whole

    assert run("--local", "show", "provider", "llm", "claude") == 0
    assert f"api_key: {MASK}" in capsys.readouterr().out

    assert run("--local", "clear-secret", "provider", "llm", "claude", "api_key") == 0
    capsys.readouterr()
    assert run("--local", "delete", "provider", "llm", "claude") == 0
    capsys.readouterr()
    assert run.reached == []


def test_storing_a_secret_locally_still_needs_a_usable_key(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one recovery command that cannot work without one, because it
    encrypts. It names the variable and never the material."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    capsys.readouterr()
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-fernet-key")

    assert run("--local", "set-secret", "provider", "llm", "claude", "api_key", stdin=SECRET) == 1

    captured = capsys.readouterr()
    assert MASTER_KEY_ENV in captured.err
    assert "not-a-fernet-key" not in captured.err
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


def test_a_prompt_fragment_reads_and_deletes_through_the_recovery_path(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--local` is the way in when the server will not start, and it
    covers this section the way it covers the others."""
    run("set", "prompt-fragment", "household", "-f", "-", stdin=FRAGMENT_INPUT)
    capsys.readouterr()

    assert run("--local", "show", "prompt-fragment", "household") == 0
    assert _document(capsys.readouterr().out) == {"text": FRAGMENT_TEXT}

    assert run("--local", "delete", "prompt-fragment", "household") == 0
    assert capsys.readouterr().out == "wrote prompt-fragment household deleted\n"

    assert run("show", "prompt-fragment", "household") == 1
    assert "no prompt fragment of that name exists" in capsys.readouterr().err


def test_local_delete_removes_the_row_that_is_keeping_the_server_down(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of the break-glass path, end to end: a row the
    loader refuses is the row stopping the boot, so it is the one that
    has to come out, and every reading command refuses it on the way."""
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run("set", "provider", "asr", "whisper", "-f", "-", stdin="type: mock\n")
    capsys.readouterr()
    engine = open_database(tmp_path / "db")
    try:
        with engine.begin() as connection:
            connection.execute(
                update(schema.providers)
                .where(schema.providers.c.name == "claude")
                .values(body="not json at all")
            )
    finally:
        engine.dispose()
    # Nothing can read it, which is the state a server meets at boot.
    assert run("--local", "show") == 1
    assert "providers.llm.claude" in capsys.readouterr().err

    assert run("--local", "delete", "provider", "llm", "claude") == 0
    capsys.readouterr()

    # And with it gone the configuration reads again.
    assert run("--local", "show") == 0
    assert "whisper" in capsys.readouterr().out


def test_a_command_outside_the_subset_is_refused_naming_it(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv in (
        ("--local", "list"),
        ("--local", "set", "agent", "sam", "-f", "-"),
        ("--local", "bind-device", "aa:bb:cc:dd:ee:ff", "sam"),
        ("--local", "set-default-agent", "sam"),
        ("--local", "clear-default-agent"),
    ):
        assert run(*argv, stdin="prompt: x\n") == 1, argv
        captured = capsys.readouterr()
        assert "show, delete, clear-secret and set-secret" in captured.err, argv
        assert captured.out == ""
    assert run.reached == []


def test_the_flag_is_accepted_after_its_command_too(
    run, capsys: pytest.CaptureFixture[str]
) -> None:
    run("set", "provider", "llm", "claude", "-f", "-", stdin="type: anthropic\nmodel: m\n")
    run.reached.clear()
    capsys.readouterr()

    assert run("show", "provider", "llm", "claude", "--local") == 0
    assert run.reached == []


def test_local_show_reaches_a_name_no_new_write_could_create(
    run, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason the recovery subset goes by membership rather than by
    the write-time addressability rule: a row written before that rule
    existed has to stay readable and removable, and it cannot be reached
    over a URL path at all."""
    run("list")
    capsys.readouterr()
    engine = open_database(tmp_path / "db")
    try:
        with engine.begin() as connection:
            connection.execute(
                schema.providers.insert().values(
                    stage="llm", name="a/b", body='{"type": "mock"}', secrets={}
                )
            )
    finally:
        engine.dispose()

    assert run("--local", "show", "provider", "llm", "a/b") == 0
    assert "type: mock" in capsys.readouterr().out

    assert run("--local", "delete", "provider", "llm", "a/b") == 0
    capsys.readouterr()
    assert run("--local", "show", "provider", "llm", "a/b") == 1
    assert NO_SUCH_PROVIDER in capsys.readouterr().err
