"""What the image this process runs from says about itself.

Two facts, both of them the image's own, both arriving the only way an
image can state one: a build argument or a plain `ENV` that becomes an
environment variable, because a process cannot read its own image's
metadata and there is no file in it that answers either question.

`__version__` is the package version, hardcoded since the skeleton and
answering a different question: what this is. The revision answers which
one, and the two are kept separate rather than one being made to stand
in for the other.

The revision matters because field sessions on hardware are how
behaviour gets investigated, and the loop is record, change, deploy,
record again. Without a revision on each recording, two sessions that
behaved differently are indistinguishable from one code change and two
different rooms, and that confound lands on exactly the evidence that is
expensive to collect. It is also what a rollback wants: a container
reporting only `0.1.0` cannot be matched to the image tag that produced
it without going and asking the cluster.

Resolved in three steps, first answer wins:

1. `VINGA_REVISION`, which the image build bakes in. OCI labels carry
   the same information but a process cannot read its own image's
   metadata, so the build argument becomes an environment variable.
2. `git describe --always --dirty`, which covers running from a working
   tree, the case the environment variable does not reach. A tree with
   uncommitted changes says so, because that is when knowing matters.
3. `unknown`. A build with neither is a build that runs and says it does
   not know, never one that fails to start.

`in_container` is the second fact, and it is a marker rather than a
detection (#340). Whether this process is inside a container decides
whether `localhost` in a provider's `base_url` is a warning worth
saying, and the image is the one thing that knows for certain: it sets
the variable in its own `ENV`, so the answer is a fact somebody stated
rather than one inferred from `/.dockerenv` or from a cgroup path,
which differ by runtime and cannot be set by a test. Absent, the answer
is no, which is the honest reading for anything the image did not
build: a checkout, a wheel, a `uv run`.
"""

import functools
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

REVISION_ENV = "VINGA_REVISION"

CONTAINER_ENV = "VINGA_CONTAINER"

# What a server says when it has no way to know. Deliberately a value
# rather than None: every consumer here reports it, and a string that
# reads as an answer keeps the field's type honest at the collector.
UNKNOWN_REVISION = "unknown"

# The repository root, if this package is being run out of a checkout.
# Resolved from the module rather than the process's cwd, which is not
# the server's to assume. Two levels up past src/ in the checkout;
# inside site-packages the path is meaningless and git answers nothing,
# which the caller treats as the ordinary no-checkout case.
_CHECKOUT = Path(__file__).resolve().parents[2]

# Long enough for git on a cold cache, short enough that a wedged one
# cannot hold up a boot. Nothing here is worth delaying a server for.
_GIT_TIMEOUT_S = 5.0


def _from_git() -> str | None:
    """What `git describe` says about this checkout, or None if there is
    no checkout, no git, or nothing to describe.

    Every failure is None rather than an exception: this runs inside an
    image where git is not installed and there is no `.git` to read, and
    that is an ordinary case, not an error."""
    try:
        described = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=_CHECKOUT,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if described.returncode != 0:
        return None
    return described.stdout.strip() or None


@functools.cache
def revision() -> str:
    """This build's revision, resolved once per process.

    Cached because the git branch spawns a subprocess and the answer
    cannot change while the process lives. A test that wants a different
    answer clears the cache with `revision.cache_clear()`.
    """
    from_env = os.environ.get(REVISION_ENV, "").strip()
    if from_env:
        return from_env
    from_git = _from_git()
    if from_git is not None:
        return from_git
    logger.debug(
        "no %s in the environment and no git checkout to describe: "
        "reporting revision as %s",
        REVISION_ENV,
        UNKNOWN_REVISION,
    )
    return UNKNOWN_REVISION


def in_container() -> bool:
    """Whether this process is running inside the vinga image.

    Read on every provider build rather than cached, unlike the revision
    above: it is one environment lookup with no subprocess behind it,
    and a value a test sets between two builds has to be the value the
    second one sees.

    Any non-blank value is yes. The variable exists to be set by the
    image's own `ENV` and by a test that wants to be one, so what it
    holds carries no meaning beyond being there; an empty string is a
    variable that was cleared rather than an answer, exactly as it is
    for the revision.
    """
    return bool(os.environ.get(CONTAINER_ENV, "").strip())
