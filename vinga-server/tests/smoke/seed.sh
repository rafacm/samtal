#!/bin/sh
# The domain half the smoke lane's server runs on, written through the
# CLI into the database the VINGA_DB_* variables name.
#
# Run from the image itself before the container starts, so the seeding
# exercises the shipped artifact rather than a checkout, and against the
# same database the server is given. Mock providers throughout: the
# conversation needs no model downloads, no keys, and no network.
#
# It starts a server of its own to write through, so the container it
# runs in needs what a server needs: VINGA_AUTH_SECRET, and
# VINGA_API_SECRET for the API the CLI writes to.
#
# The order is the one the write-time reference checks require:
# providers, then the agent defaults, then the agent, then the default
# agent that names it.
set -eu

# The server this writes through, started here and stopped on the way
# out: `vinga-server config` is a client of the configuration API now,
# so seeding a database means having a server to write to. serve.sh says
# how, and its exit trap prints the server's log if anything here fails.
. "$(dirname "$0")/serve.sh"
trap on_exit EXIT
trap on_interrupt INT
trap on_terminate TERM
start_server

vinga-server config provider set llm mock -f - <<'YAML'
type: mock
reply: "You said {text}."
YAML

vinga-server config provider set asr mock -f - <<'YAML'
type: mock
text: hello
YAML

vinga-server config provider set tts mock -f - <<'YAML'
type: mock
YAML

vinga-server config provider set vad mock -f - <<'YAML'
type: mock
YAML

vinga-server config agent-defaults set -f - <<'YAML'
llm: mock
asr: mock
tts: mock
vad: mock
YAML

vinga-server config agent set assistant -f - <<'YAML'
prompt: A smoke test assistant.
YAML

vinga-server config default-agent set assistant

stop_server
