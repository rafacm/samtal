"""One subprocess, run with a deadline it cannot outlive.

A lane that shells out has two failure modes and only one of them is
loud. A command that answers wrongly fails an assertion that names
itself and prints what it got. A command that never answers stops the
run where it stands: the job is killed at the workflow's own limit, no
test is named, nothing the command wrote is kept, and the reader is left
with a log whose last line is the test BEFORE the one that hung.

That is not hypothetical. `test_tier_closure` ran `vinga-server` from a
built environment expecting a boot refusal, on the reasoning that a
server with nothing configured cannot start. Under Postgres it can: the
shipped connection defaults name a reachable instance, both lanes have
one, and an empty store boots. So the process bound a port and served,
`subprocess.run` had no `timeout=`, and the integration lane sat there
for seventy-three minutes.

So nothing here waits forever, and a wait that expires says what the
command had written by the time it did. A hung lane is worse than a red
one: a red lane names the test.

Two budgets, because the lane runs two kinds of thing. Both are ceilings
on a hang rather than expectations, and a run that approaches either is
already a bug worth reading about.
"""

import subprocess
from collections.abc import Sequence

# One command of a built grammar. The slowest of them import the whole
# server half and open a database, which is seconds.
COMMAND_SECONDS = 120

# One environment built with uv: a resolve, a fetch and an install, and
# on a cold cache in CI that is minutes rather than seconds.
BUILD_SECONDS = 900


def ran(
    argv: Sequence[str], *, seconds: int = COMMAND_SECONDS, **run: object
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run`, captured and decoded, that fails the test rather
    than outliving the job.

    The keyword arguments are `subprocess.run`'s own, minus the three
    this decides: the streams are captured and decoded, because the
    failure below has to be able to quote them.

    An expiry raises an `AssertionError` and not the library's
    `TimeoutExpired`, and severs the chain: what a reader needs is the
    command line and the two streams, which the message carries, and
    what the exception would add underneath is a second rendering of the
    same argv with none of the output.
    """
    try:
        return subprocess.run(
            list(argv), capture_output=True, text=True, timeout=seconds, **run
        )
    except subprocess.TimeoutExpired as expired:
        problem = (
            f"the command never finished, and this lane gave it {seconds}s: "
            f"{list(argv)}\n"
            f"--- what it had written to stdout ---\n{_written(expired.stdout)}\n"
            f"--- what it had written to stderr ---\n{_written(expired.stderr)}"
        )
    raise AssertionError(problem)


def _written(stream: str | bytes | None) -> str:
    """What a killed command left on one stream.

    `TimeoutExpired` carries whatever was read before the kill, and
    carries `None` when the stream was never captured. It is decoded
    here rather than trusted to be text: the exception's attributes are
    populated by the reader, and a partial read is exactly where an
    invalid byte sequence lands.
    """
    if stream is None:
        return "(nothing)"
    if isinstance(stream, bytes):
        return stream.decode("utf-8", "replace") or "(nothing)"
    return stream or "(nothing)"
