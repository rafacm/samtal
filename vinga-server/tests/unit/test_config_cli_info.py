"""`vinga info`: what it prints, and what it must never print anywhere
else.

The command answers one question, "what server am I talking to", and it
answers it from three places at once: what this CLI knows before it
asks (the address it is about to contact), what the running server
answers (its build, and the URL a board is onboarded at), and what the
store holds (how much of each kind). Two acts and a preamble, so the
first half of this file is about the composition and the order.

The second half is about the one value in it that is a credential. The
onboarding URL's last segment is a key derived from the device-auth
secret and it stands in front of the token issuer, which is why the
startup banner deliberately prints the origin without it. Serving it
over an authenticated read is this issue's decision and the second
recorded exception to the cli-guide's "a credential never travels in a
read"; what makes the exception safe is a list of places the value must
not reach, and a list is worth nothing unless something checks it.

So the sentinel is the real thing. The URL is derived here by calling
`onboarding.origin.onboarding_url` with a device-auth secret in the
environment, exactly as a deployment's composition root does, and every
case below hunts for the whole URL and for its key segment on its own:
on stderr, in the refusals, in the log records of the whole invocation
rendered both ways a deployment keeps them, and along the exception
chain a failure leaves behind.
"""

import os
from pathlib import Path

import pytest

from tests.support.config_cli import chain, logged, runner
from tests.support.events import both_formats
from vinga_server.config import cli
from vinga_server.config.loader import ConfigError
from vinga_server.config.models import ServerConfig
from vinga_server.config.responses import RuntimeInfo
from vinga_server.onboarding.origin import onboarding_url

# Not a real secret, and shaped so a substring check for what is derived
# from it cannot match by accident.
DEVICE_SECRET = "dev-test-1c4a9f2b-never-a-real-secret"

ORIGIN = "https://vinga.test.invalid"

# A value shaped like a credential in a query string, which is the form
# the transport policy accepts and `Address.shown` takes out: userinfo
# is refused outright, and `?token=...` is what vendors accept instead.
QUERY_TOKEN = "tok-test-9d3e1a75-never-a-real-credential"

# What a body that is not the declared shape carries, so a refusal that
# quoted any of it would be caught.
ANSWERED = "ans-test-6b2f4c08-never-a-real-value"


def derived(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """The onboarding URL a deployment with a device-auth secret serves,
    and the provenance beside it.

    Derived rather than written out, which is the whole of what makes
    the assertions below sentinels: what is hunted for is the value this
    server would really answer, key segment and all, so a leak of the
    real thing cannot pass because the test was looking for a
    stand-in.
    """
    monkeypatch.setenv("VINGA_DEVICE_SECRET", DEVICE_SECRET)
    server = ServerConfig(public_url=ORIGIN, auth={"enabled": True})
    url, origin = onboarding_url(server, "unused")
    return url, origin.provenance


def key_of(url: str) -> str:
    """The last path segment of an onboarding URL, which is the key.

    Hunted for on its own as well as inside the whole URL, because the
    origin is public and the key is not: a line that printed the key
    with a different origin in front of it would still have leaked the
    only part that matters.
    """
    segment = url.rstrip("/").rsplit("/", 1)[-1]
    # Guarded, because an empty or one-character segment would make
    # every assertion below pass by matching nothing or everything: a
    # keyless deployment mounts at `/x/`, and this file's is not one.
    assert len(segment) >= 8, url
    return segment


def identity(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> RuntimeInfo:
    """One deployment's identity, as its composition root composes it."""
    url, provenance = derived(monkeypatch)
    return RuntimeInfo(
        **{
            "version": "0.1.0",
            "revision": "v0.1.0-3-gdeadbee",
            "onboarding_enabled": True,
            "onboarding_url": url,
            "onboarding_provenance": provenance,
        }
        | overrides
    )


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One command run the way the entry point runs it, against a server
    of this test's own."""
    return runner(monkeypatch)


def configured(run) -> None:
    """A deployment with something in every countable section, so that a
    count of zero cannot pass for a count that was rendered."""
    assert run("provider", "set", "llm", "brain", "type=mock") == 0
    assert run("provider", "set", "tts", "voice", "type=mock") == 0
    assert run("mcp-server", "set", "house", "transport=stdio", "command=/bin/true") == 0
    assert run("prompt-fragment", "set", "household", "text=The bins go out.") == 0
    assert run("agent", "set", "sam", "prompt=You are Sam.") == 0
    assert run("agent", "set", "kid", "prompt=You are a kid.") == 0
    assert run("device", "bind", "aa:bb:cc:dd:ee:ff", "sam") == 0
    assert run("default-agent", "set", "sam") == 0


def test_info_prints_the_deployment_from_end_to_end(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole render, in the order it is read: who is speaking, what
    this CLI reached, which build answered, what to type into a board,
    and how much is configured."""
    info = identity(monkeypatch)
    configured(run)
    run.runtime["identity"] = info
    capsys.readouterr()

    assert run("info") == 0

    printed = capsys.readouterr()
    lines = printed.out.splitlines()
    assert lines[0] == "vinga - Conversational AI. Sweded."
    assert lines[1].startswith("configuration API: http://127.0.0.1:")
    assert "server version: 0.1.0" in lines
    assert "server revision: v0.1.0-3-gdeadbee" in lines
    # The URL alone on its line, with its provenance on the label above
    # it: a terminal wraps a long line wherever it runs out, and this is
    # a value an operator retypes by hand.
    label = lines.index(
        f"the URL to type into a device's captive portal, {info.onboarding_provenance}:"
    )
    assert lines[label + 1] == info.onboarding_url
    # And the counts, which are the shape of the deployment rather than
    # its contents: `vinga list` prints the contents.
    assert "configured:" in lines
    assert "  providers: 2" in lines
    assert "  mcp_servers: 1" in lines
    assert "  prompt_fragments: 1" in lines
    assert "  agents: 2" in lines
    assert "  devices: 1" in lines
    assert "  default_agent: sam" in lines
    # Nothing about the run, because none of this is about the run.
    assert printed.err == ""


def test_info_says_which_switch_turned_onboarding_off(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A state a deployment is legitimately in, so the sentence names
    the switch rather than refusing. The path devices are configured at
    is `server.ota_path`, which is a deployment's secret, so it is named
    and never printed."""
    run.runtime["identity"] = identity(
        monkeypatch,
        onboarding_enabled=False,
        onboarding_url=None,
        onboarding_provenance=None,
    )
    capsys.readouterr()

    assert run("info") == 0

    printed = capsys.readouterr().out
    assert "server.onboarding.enabled is false" in printed
    assert "server.ota_path" in printed
    assert "the URL to type into" not in printed


def test_a_long_url_is_printed_whole_and_still_on_one_line(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`server.public_url` is an origin with an optional path prefix and
    nothing bounds its length, so a legal configuration composes an
    onboarding URL of any length at all.

    A truncated URL is not a shorter answer, it is a wrong one: it is
    typed into a captive portal by hand and fails there, with nothing on
    the terminal saying it was cut. So the whole of it is printed, and
    still on one line, which is the two halves of the promise this
    command makes about the value. The provenance goes the same way, and
    for the same reason: the fix is at the end of it.
    """
    monkeypatch.setenv("VINGA_DEVICE_SECRET", DEVICE_SECRET)
    server = ServerConfig(
        public_url=f"{ORIGIN}/{'behind-a-proxy' * 40}", auth={"enabled": True}
    )
    url, origin = onboarding_url(server, "unused")
    assert len(url) > 512, len(url)
    run.runtime["identity"] = RuntimeInfo(
        version="0.1.0",
        revision="v0.1.0-3-gdeadbee",
        onboarding_enabled=True,
        onboarding_url=url,
        onboarding_provenance=origin.provenance,
    )
    capsys.readouterr()

    assert run("info") == 0

    lines = capsys.readouterr().out.splitlines()
    assert url in lines
    assert lines[lines.index(url) - 1].endswith(f"{origin.provenance}:")


def test_a_server_that_answers_the_first_act_and_refuses_the_second(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The multi-act shape, which is the established behavior of a row
    with two acts: what was answered is rendered, and the refusal
    follows it. Both are visible, because an operator who has learned
    which server this is has learned something even if the store read
    then failed.
    """
    run.runtime["identity"] = identity(monkeypatch)
    refused(monkeypatch, ConfigError("the configuration API could not be reached"))
    capsys.readouterr()

    assert run("info") == 1

    printed = capsys.readouterr()
    assert "vinga - Conversational AI. Sweded." in printed.out
    assert "server version: 0.1.0" in printed.out
    assert "could not be reached" in printed.err
    assert "configured:" not in printed.out


# The second act's own answer
#
# The first act is what the cases above replace, so until these the
# counts renderer had never been handed a body it did not compose
# itself. It needs its own set: `ConfigDocument` declares the masked
# document as `dict[str, Any]` and stops there, so everything a count
# walks into is undeclared and arrives from wherever answered.


def answering_config(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    """A server that answers the identity read for real and the
    configuration read with this body.

    The two acts have to be told apart, because a stub that answered
    both would be refused at the first one and the second would never
    run, which is exactly the hole these cases close.
    """
    _second(monkeypatch, lambda: body)


def refused(monkeypatch: pytest.MonkeyPatch, problem: ConfigError) -> None:
    """A server that answers the identity read and refuses the
    configuration read."""

    def raise_it() -> object:
        raise problem

    _second(monkeypatch, raise_it)


def _second(monkeypatch: pytest.MonkeyPatch, answer) -> None:
    real = cli._call

    def call(reached, method, path, *rest, **kwargs):
        if path == "/config":
            return answer()
        return real(reached, method, path, *rest, **kwargs)

    monkeypatch.setattr(cli, "_call", call)


def document(**sections: object) -> dict[str, object]:
    """A whole-configuration answer whose outer shape is valid, so that
    what a case replaces is what a count walks into rather than what the
    act reads."""
    return {
        "config": {
            "providers": {"llm": {"brain": {"type": "mock"}}},
            "mcp_servers": {},
            "prompt_fragments": {},
            "agent_defaults": {},
            "agents": {},
            "devices": {},
            "default_agent": None,
        }
        | sections,
        "secrets": [],
    }


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(document(providers=1), id="section-is-a-number"),
        pytest.param(document(agents=[ANSWERED]), id="section-is-a-list"),
        pytest.param(document(providers={"llm": ANSWERED}), id="stage-is-not-a-mapping"),
        pytest.param(document(devices=ANSWERED), id="devices-is-not-a-mapping"),
        pytest.param(document(default_agent={"leak": ANSWERED}), id="default-agent-is-an-object"),
        pytest.param(document(default_agent=4), id="default-agent-is-a-number"),
        # A section the counts do not print at all. Since #351 the
        # document is read as its shapes once, by the step the tree
        # reads it with, so what is refused is a document this client
        # cannot read rather than the half of one a given command walks
        # into. Which is the honest reading: the sentence is about the
        # answer, not about the line.
        pytest.param(document(agent_defaults=[ANSWERED]), id="agent-defaults-is-a-list"),
        pytest.param({"config": {}, "secrets": []}, id="every-section-absent"),
    ],
)
def test_a_configuration_a_count_cannot_walk_is_quoted_nowhere(
    body: object,
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A body whose outer shape is the declared one and whose insides
    are not.

    The act reads `ConfigDocument`, which says the masked document is a
    mapping and says nothing about what is under it, so this is the body
    that gets past the act and reaches the renderer. What it must meet
    there is the same fixed sentence every other unreadable answer
    meets, and never a `TypeError` or a `KeyError` out of the boundary.
    """
    run.runtime["identity"] = identity(monkeypatch)
    answering_config(monkeypatch, body)
    capsys.readouterr()

    with caplog.at_level(0):
        assert run("info") == 1

    printed = capsys.readouterr()
    # The first act rendered, which is the multi-act contract.
    assert "server version: 0.1.0" in printed.out
    assert cli.UNRECOGNIZED_ANSWER in printed.err
    assert "Traceback" not in printed.err
    assert "configured:" not in printed.out
    for surface in (printed.out, printed.err, logged(caplog), both_formats(caplog)):
        assert ANSWERED not in surface


def test_a_count_refusal_leaves_nothing_on_the_chain() -> None:
    """Read through the act's own renderer, because that is where the
    nesting is read: a refusal built inside the handler would carry the
    body it refused as its `__context__` for anything walking the
    chain."""
    with pytest.raises(ConfigError) as caught:
        cli.COUNTS.render(cli.COUNTS.read(document(default_agent={"leak": ANSWERED})))

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert ANSWERED not in chain(caught.value)


def test_a_default_agent_is_bounded_and_made_printable(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one value in the counts that is not a number, so the one that
    can carry anything. It is a name the store answered, so it is
    printed, and it is printed the way every value an answer carries
    is: bounded, and with nothing in it that could steer a terminal."""
    run.runtime["identity"] = identity(monkeypatch)
    answering_config(monkeypatch, document(default_agent="sa\x1b[31mm" + "x" * 400))
    capsys.readouterr()

    assert run("info") == 0

    printed = capsys.readouterr().out
    assert "  default_agent: sa?[31mmxxx" in printed
    assert "\x1b" not in printed
    assert len(printed.splitlines()[-1]) < 200


# What the URL must not reach
#
# The list the plan's security design names, one case apiece: stdout is
# where it belongs, and stderr, the refusals, the log records and the
# exception chain are where it may not be. Every case derives the URL
# from a device-auth secret first, so what is hunted for is the value a
# deployment would really serve.


def test_the_url_is_on_stdout_and_on_nothing_else(
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    info = identity(monkeypatch)
    run.runtime["identity"] = info
    capsys.readouterr()

    with caplog.at_level(0):
        assert run("info") == 0

    printed = capsys.readouterr()
    url = info.onboarding_url
    assert url in printed.out
    assert url not in printed.err
    assert key_of(url) not in printed.err
    # Every record the invocation made, whoever made it, and the two
    # renderings a deployment keeps them in. The request client is held
    # quiet around the request for exactly this reason, and this is what
    # says the quieting is still there.
    for records in (logged(caplog), both_formats(caplog)):
        assert url not in records
        assert key_of(url) not in records


def test_an_unauthorized_read_carries_no_url_back(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate is the whole of what stands between this URL and anyone
    who can reach the port, so what it answers is read for the URL as
    well as for the exit code."""
    info = identity(monkeypatch)
    run.runtime["identity"] = info
    # The bearer this CLI resolves, made wrong. The server's own token
    # is fixed by the runner, so this is a real 401 rather than a
    # client that never sent anything.
    monkeypatch.setenv("VINGA_API_SECRET", "not-the-token-this-server-was-given")
    capsys.readouterr()

    assert run("info") == 1

    printed = capsys.readouterr()
    assert "bearer token" in printed.err
    for stream in (printed.out, printed.err):
        assert info.onboarding_url not in stream
        assert key_of(info.onboarding_url) not in stream


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"version": "0.1.0", "leak": ANSWERED}, id="unknown-field"),
        pytest.param(
            {
                "version": ANSWERED,
                "revision": 4,
                "onboarding_enabled": True,
                "onboarding_url": ANSWERED,
                "onboarding_provenance": ANSWERED,
            },
            id="wrong-types",
        ),
        pytest.param([ANSWERED], id="not-an-object"),
    ],
)
def test_a_body_that_is_not_the_declared_shape_is_quoted_nowhere(
    body: object,
    run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What a proxy or a captive portal answers is text nobody vouched
    for, and the fixed sentence says the status and no more."""
    monkeypatch.setattr(cli, "_call", lambda *_args, **_kwargs: body)
    capsys.readouterr()

    assert run("info") == 1

    printed = capsys.readouterr()
    assert cli.UNRECOGNIZED_ANSWER in printed.err
    assert ANSWERED not in printed.err + printed.out
    assert "Traceback" not in printed.err


@pytest.mark.parametrize(
    ("enabled", "url", "provenance"),
    [
        pytest.param(True, None, None, id="on-with-no-url"),
        pytest.param(True, "https://vinga.test.invalid/x/abcdefgh/", None, id="on-with-no-source"),
        pytest.param(True, None, "from server.public_url", id="on-with-a-source-and-no-url"),
        pytest.param(
            False,
            "https://vinga.test.invalid/x/abcdefgh/",
            "from server.public_url",
            id="off-with-a-url",
        ),
        pytest.param(
            False, "https://vinga.test.invalid/x/abcdefgh/", None, id="off-with-a-bare-url"
        ),
        pytest.param(False, None, "from server.public_url", id="off-with-a-source"),
    ],
)
def test_an_answer_that_says_two_things_about_onboarding_is_refused(
    enabled: bool, url: str | None, provenance: str | None
) -> None:
    """The flag and the two nullable fields are one fact, and a body
    that makes them two is one no reader can act on: there is no
    answering which half to believe, and this client's own renderer
    branched on one of them while the sentence came from another.

    Read through the act, which is the surface a command reads an answer
    through, so what is pinned is what a command would meet.
    """
    with pytest.raises(ConfigError) as caught:
        cli.IDENTITY.read(
            {
                "version": "0.1.0",
                "revision": "v0.1.0-3-gdeadbee",
                "onboarding_enabled": enabled,
                "onboarding_url": url,
                "onboarding_provenance": provenance,
            }
        )

    assert cli.UNRECOGNIZED_ANSWER in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    # And the value it was refused over is not in what it says about it.
    assert "abcdefgh" not in chain(caught.value)


def test_the_two_consistent_answers_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other side of the same pin: exactly two of the eight states
    are a deployment's, and both are read."""
    info = identity(monkeypatch)
    served = cli.IDENTITY.read(info.model_dump())
    off = cli.IDENTITY.read(
        info.model_dump()
        | {
            "onboarding_enabled": False,
            "onboarding_url": None,
            "onboarding_provenance": None,
        }
    )

    assert served["onboarding_url"] == info.onboarding_url
    assert off["onboarding_enabled"] is False


def test_an_identity_refusal_leaves_nothing_on_the_chain() -> None:
    """Read through the act, because that is what the command reads
    through: a refusal built inside a handler would carry the body it
    refused as its `__context__` for anything walking the chain."""
    with pytest.raises(ConfigError) as caught:
        cli.IDENTITY.read({"version": "0.1.0", "leak": ANSWERED})

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert ANSWERED not in chain(caught.value)


def test_both_acts_are_answered_by_the_address_the_banner_named(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The line this command opens with is a claim about where its
    answers come from, so it has to be one.

    Each request used to re-read the file half and resolve the address
    off it again, and the opener resolved a third time, so a file
    changing under a running command could put one endpoint on the
    banner and another behind the answers. The file half is read once
    now, which is what this pins: the read is counted, and a
    configuration that moves after it would send the later requests to a
    port nothing in this test serves.
    """
    run.runtime["identity"] = identity(monkeypatch)
    real = cli.load_file_config
    reads: list[object] = []

    def moving(path: str | None = None):
        config = real(path)
        reads.append(path)
        if len(reads) > 1:
            moved = config.server.model_copy(update={"port": 9999})
            return config.model_copy(update={"server": moved})
        return config

    monkeypatch.setattr(cli, "load_file_config", moving)
    capsys.readouterr()

    assert run("info") == 0

    printed = capsys.readouterr().out
    assert len(reads) == 1, reads
    # Both requests, and the line that named where they would go.
    assert len(run.reached) == 2, run.reached
    assert set(run.reached) == {run.reached[0]}
    assert f"configuration API: {run.reached[0]}" in printed


def test_the_address_line_never_shows_a_credential_in_the_query(
    run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The line names `Address.shown` and never what was typed. The
    transport policy refuses a credential in a URL's userinfo and says
    nothing about its query string, so an accepted address can still
    carry one, and the display form is the one with it taken out."""
    run.runtime["identity"] = identity(monkeypatch)
    port = os.environ.get("VINGA_SERVER__PORT", "8003")
    capsys.readouterr()

    code = run("--api-url", f"http://127.0.0.1:{port}/api?token={QUERY_TOKEN}", "info")

    printed = capsys.readouterr()
    assert code == 0, printed.err
    assert f"configuration API: http://127.0.0.1:{port}/api" in printed.out
    assert QUERY_TOKEN not in printed.out + printed.err
