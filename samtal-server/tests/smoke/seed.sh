#!/bin/sh
# The domain half the smoke lane's server runs on, written through the
# CLI into the database at server.database.dir.
#
# Run from the image itself before the container starts, so the seeding
# exercises the shipped artifact rather than a checkout, and against the
# same data volume the server is given. Mock providers throughout: the
# conversation needs no model downloads, no keys, and no network.
#
# The order is the one the write-time reference checks require:
# providers, then the agent defaults, then the agent, then the default
# agent that names it.
set -eu

samtal-server config set provider llm mock -f - <<'YAML'
type: mock
reply: "You said {text}."
YAML

samtal-server config set provider asr mock -f - <<'YAML'
type: mock
text: hello
YAML

samtal-server config set provider tts mock -f - <<'YAML'
type: mock
YAML

samtal-server config set provider vad mock -f - <<'YAML'
type: mock
YAML

samtal-server config set agent-defaults -f - <<'YAML'
llm: mock
asr: mock
tts: mock
vad: mock
YAML

samtal-server config set agent assistant -f - <<'YAML'
prompt: A smoke test assistant.
YAML

samtal-server config set-default-agent assistant
