#!/bin/sh
# The domain half of the slim image's boot check: every network stage on
# an external provider, which is what the slim variant exists for. This
# configuration loads no local engine, so it has to come up in an image
# that carries none.
#
# The keys are throwaway and nothing here talks to a vendor: what this
# proves is that the server builds its providers and serves both
# endpoints, not that a conversation runs. The seeding server this writes
# through needs none of them: with an empty domain half there is no
# provider to build. What it does need is what a server needs,
# SAMTAL_AUTH_SECRET and SAMTAL_API_SECRET. The conversation is the
# mock-provider smoke suite's job, and it runs against both variants.
#
# silero is the exception and stays a local engine deliberately: it is a
# core dependency rather than an optional extra, so it is present in
# both variants and runs on every audio frame whichever ASR is
# configured.
set -eu

# The server this writes through, started here and stopped on the way
# out: `samtal-server config` is a client of the configuration API now,
# so seeding a database means having a server to write to. serve.sh says
# how, and its exit trap prints the server's log if anything here fails.
. "$(dirname "$0")/serve.sh"
trap on_exit EXIT INT TERM
start_server

samtal-server config set provider llm claude -f - <<'YAML'
type: anthropic
api_key_env: ANTHROPIC_API_KEY
model: claude-sonnet-4-5
YAML

samtal-server config set provider asr cloud -f - <<'YAML'
type: openai
api_key_env: OPENAI_API_KEY
YAML

samtal-server config set provider tts cloud -f - <<'YAML'
type: openai
api_key_env: OPENAI_API_KEY
voice: alloy
YAML

samtal-server config set provider vad silero -f - <<'YAML'
type: silero
YAML

samtal-server config set agent-defaults -f - <<'YAML'
llm: claude
asr: cloud
tts: cloud
vad: silero
YAML

samtal-server config set agent assistant -f - <<'YAML'
prompt: A slim-image boot check.
YAML

samtal-server config set-default-agent assistant

stop_server
