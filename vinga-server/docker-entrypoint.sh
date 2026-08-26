#!/bin/sh
# The image's entrypoint, and the whole of what it is for: naming the
# mounted configuration file when there is one, and saying nothing when
# there is not.
#
# The image used to set VINGA_CONFIG=/config/config.yaml unconditionally,
# which made the mount mandatory rather than optional. A named file that
# is not there is refused by the loader, deliberately: an operator who
# passes --config or sets VINGA_CONFIG has said which file they mean, and
# silently ignoring a typo in it would serve a configuration nobody
# wrote. The image was making that statement on the operator's behalf, so
# `docker run` with no mount refused to start on a file the operator had
# never named.
#
# The server half needs no file: every key of it has a default, and every
# one of them is overridable with a VINGA_SERVER__* variable. So the file
# is named here only when it exists, and the refusal keeps its meaning:
# mount nothing and the server boots on the environment, mount something
# and a mistake in it is still an error rather than a shrug.
#
# An explicit VINGA_CONFIG always wins, mounted file or not. Somebody who
# named a path meant that path, including when it is wrong.
#
# There is deliberately no wait loop for the database either. A database
# the server cannot reach is a boot that refuses with a fixed sentence,
# which is the contract; restart policy belongs to the orchestrator, and
# a shell script retrying here would turn a misconfiguration that says
# what is wrong into a container that looks busy. The development loop
# is `docker compose up -d --wait`, which waits where waiting belongs.
#
# `exec`, so the server replaces this shell as PID 1 and SIGTERM reaches
# it: that is what lets `docker stop` run the shutdown drain rather than
# killing the process part-way through.
set -eu

DEFAULT_CONFIG=/config/config.yaml

if [ -z "${VINGA_CONFIG:-}" ] && [ -f "$DEFAULT_CONFIG" ]; then
    VINGA_CONFIG="$DEFAULT_CONFIG"
    export VINGA_CONFIG
fi

exec vinga-server "$@"
