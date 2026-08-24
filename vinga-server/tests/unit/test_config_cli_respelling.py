"""What every respelled command did, pinned before its words moved.

The #223 re-cut turns the grammar around: `set provider llm claude`
becomes `provider set llm claude`, and twelve more rows move with it.
Nothing else is meant to change. That claim is worth exactly as much as
what checks it, and the characterization suites around this one check
behavior per command rather than the whole run, so a respelling that
quietly dropped a notice, reordered two streams or changed an exit code
could pass all of them.

So this is a differential. Every row whose words move is driven in one
fixed order against one store, and what each of them printed on stdout,
printed on stderr and exited with is recorded in the committed
transcript beside this file (`data/cli-respelling.txt`), with the store
read back at the end. The transcript was captured on the commit BEFORE
the rename, from the old spellings, and it does not move with them: the
command lines below are respelled and the transcript is not, which is
what makes "the respelling changed nothing" a test rather than a claim.

Capturing it is this same test with `VINGA_CAPTURE_RESPELLING=1` set,
so the fixture and the check are one piece of code rather than two that
happen to agree. Capturing it again after the rename would prove
nothing, so it is done once and the file is read in review rather than
trusted.

`RESPELLINGS` is the one licensed difference. Some of what these
commands print quotes a command back at the operator: an export's header
says how to reproduce a deployment, its foot lists a `set-secret` per
stored slot, a read's secrets heading names the command that fills one,
and the reload notice names the command that applies a write. Those move
with the grammar, deliberately, and the table below is the complete list
of the substitutions that licenses. It is applied to the TRANSCRIPT
before the comparison, so a difference the table does not explain is a
failure, and the table itself is short enough to read.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.support.config_cli import SECRET, registered, runner, showing

TRANSCRIPT = Path(__file__).parent / "data" / "cli-respelling.txt"

# Set to capture the transcript instead of checking against it. Named
# rather than a flag, because the capture is a one-off on the commit
# before the rename and nothing in CI ever sets it.
CAPTURE_ENV = "VINGA_CAPTURE_RESPELLING"

# The variable a stored credential is read from, so no secret is ever an
# argument even here.
SECRET_ENV = "RESPELLING_SECRET"

# The board every device row addresses. It arrives waiting with an
# activation code, is claimed by that code, is rebound by its MAC and is
# unbound again, which is the whole of the device grammar in one board.
# The code is minted per run and is never printed into an answer: what a
# claim is acknowledged with is the MAC it bound.
MAC = "aa:bb:cc:dd:ee:ff"

# What the transcript licenses to move, and the whole of it. Empty until
# the rename fills it, because before the rename there is nothing that
# has moved.
RESPELLINGS: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Step:
    """One command of the transcript: what it is called here, what was
    typed, and what was piped into it."""

    # The name the transcript records it under, which is what stays
    # fixed while the words move.
    name: str

    # The command line, in the grammar of the day. This is the half the
    # rename edits.
    argv: tuple[str, ...]

    stdin: str | None = None

    # Whether the activation code minted for this run is substituted
    # into the command line, for the one row addressed by one.
    claims: bool = False


# The fragments the writes carry, one per kind and no larger than the
# kind needs to be written at all.
_LLM = "type: anthropic\nmodel: m\n"

_ASR = "type: mock\n"

_MCP = "transport: stdio\ncommand: uvx\negress: false\n"

_FRAGMENT = "text: The bins go out on Tuesday.\n"

_AGENT = "prompt: You are Sam.\nprompt_includes: [house]\n"

_DEFAULTS = "llm: claude\nasr: ears\ntts: voice\nvad: sensor\nmcp: [home]\n"


def _step(name: str, *argv: str, stdin: str | None = None, claims: bool = False) -> Step:
    return Step(name=name, argv=argv, stdin=stdin, claims=claims)


# The transcript's own order: everything written, then read, then
# credentialed, then bound, then taken apart again, because a delete of
# a referenced entity is refused and a read of a missing one is a
# different answer. Every row whose words the re-cut moves is here.
STEPS: tuple[Step, ...] = (
    _step("provider-set", "set", "provider", "llm", "claude", "-f", "-", stdin=_LLM),
    _step("provider-set-asr", "set", "provider", "asr", "ears", "-f", "-", stdin=_ASR),
    _step("provider-set-tts", "set", "provider", "tts", "voice", "-f", "-", stdin=_ASR),
    _step("provider-set-vad", "set", "provider", "vad", "sensor", "-f", "-", stdin=_ASR),
    _step("mcp-server-set", "set", "mcp-server", "home", "-f", "-", stdin=_MCP),
    _step(
        "prompt-fragment-set", "set", "prompt-fragment", "house", "-f", "-", stdin=_FRAGMENT
    ),
    _step("agent-defaults-set", "set", "agent-defaults", "-f", "-", stdin=_DEFAULTS),
    _step("agent-set", "set", "agent", "sam", "-f", "-", stdin=_AGENT),
    _step("agent-set-inline", "set", "agent", "guest", "prompt=You are a guest."),
    # The waiting board is claimed by its code first, because binding a
    # board by its MAC is what retires the code it was showing.
    _step("device-pending-claim", "add-device", "CODE", "guest", claims=True),
    _step("provider-show", "show", "provider", "llm", "claude"),
    _step("mcp-server-show", "show", "mcp-server", "home"),
    _step("prompt-fragment-show", "show", "prompt-fragment", "house"),
    _step("agent-show", "show", "agent", "sam"),
    _step("agent-defaults-show", "show", "agent-defaults"),
    _step("provider-export", "export", "provider", "llm", "claude"),
    _step("mcp-server-export", "export", "mcp-server", "home"),
    _step("prompt-fragment-export", "export", "prompt-fragment", "house"),
    _step("agent-export", "export", "agent", "sam"),
    _step("agent-defaults-export", "export", "agent-defaults"),
    _step(
        "provider-secret-set",
        "set-secret", "provider", "llm", "claude", "api_key", "--from-env", SECRET_ENV,
    ),
    _step(
        "mcp-server-secret-set",
        "set-secret", "mcp-server", "home", "env.MCP_TOKEN",
        "--from-env", SECRET_ENV,
    ),
    _step("default-agent-set", "set-default-agent", "sam"),
    _step("device-bind", "bind-device", "AA-BB-CC-DD-EE-FF", "sam"),
    _step("device-show", "show", "device", MAC),
    # The one running-server read whose words move. Driven without a
    # server around the API, which is the answer that needs no runtime
    # and is still this act's own refusal rather than a usage error.
    _step("agent-preview", "prompt", "sam"),
    _step("device-delete", "delete", "device", MAC),
    _step("default-agent-clear", "clear-default-agent"),
    _step("provider-secret-clear", "clear-secret", "provider", "llm", "claude", "api_key"),
    _step(
        "mcp-server-secret-clear",
        "clear-secret", "mcp-server", "home", "env.MCP_TOKEN",
    ),
    _step("agent-delete", "delete", "agent", "sam"),
    _step("agent-delete-guest", "delete", "agent", "guest"),
    # The singleton has no delete, so the layer that still references
    # the entries below is replaced by an empty one instead.
    _step("agent-defaults-reset", "set", "agent-defaults", "-f", "-", stdin="{}\n"),
    _step("prompt-fragment-delete", "delete", "prompt-fragment", "house"),
    _step("mcp-server-delete", "delete", "mcp-server", "home"),
    _step("provider-delete", "delete", "provider", "llm", "claude"),
    # And the store read back, which is the third surface the
    # differential covers: what the sequence left behind.
    _step("store-after", "export"),
)


@dataclass
class Recorded:
    """One run of the whole transcript."""

    parts: list[str] = field(default_factory=list)

    def add(self, step: Step, code: int, out: str, err: str) -> None:
        self.parts += [
            f"== {step.name}",
            f"exit {code}",
            "-- stdout",
            out,
            "-- stderr",
            err,
        ]

    def rendered(self) -> str:
        return "\n".join(self.parts).rstrip("\n") + "\n"


def _argv(step: Step, code: str) -> tuple[str, ...]:
    """One step's command line, with the run's own activation code put
    where the claim row addresses one."""
    if not step.claims:
        return step.argv
    return tuple(code if word == "CODE" else word for word in step.argv)


def drive(run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> str:
    """The whole transcript, run once."""
    monkeypatch.setenv(SECRET_ENV, SECRET)
    code = showing(run, MAC)
    capsys.readouterr()
    recorded = Recorded()
    for step in STEPS:
        exit_code = run(*_argv(step, code), stdin=step.stdin)
        captured = capsys.readouterr()
        recorded.add(step, exit_code, captured.out, captured.err)
    return recorded.rendered()


def expected() -> str:
    """The committed transcript, with the spelling the rename licensed
    to move substituted into it."""
    text = TRANSCRIPT.read_text(encoding="utf-8")
    for before, after in RESPELLINGS:
        text = text.replace(before, after)
    return text


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return runner(tmp_path, monkeypatch)


def test_the_respelled_grammar_behaves_as_the_old_one_did(
    run, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The differential.

    Every stream of every respelled command, and the store afterwards,
    against what the same sequence answered before its words moved. A
    difference the substitution table does not explain is a behavior
    change the rename was not meant to make.
    """
    recorded = drive(run, capsys, monkeypatch)
    if os.environ.get(CAPTURE_ENV):
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT.write_text(recorded, encoding="utf-8")
        return
    assert recorded == expected()


def test_every_step_is_a_row_of_the_grammar() -> None:
    """The transcript, held to the tree.

    A step naming words no row has would be a step this differential
    silently stopped covering, which is the failure a transcript that
    passes by never running anything looks like.
    """
    unknown = [step.name for step in STEPS if registered(step.argv) is None]
    assert unknown == []


__all__ = ["RESPELLINGS", "STEPS", "Step", "drive", "expected"]
