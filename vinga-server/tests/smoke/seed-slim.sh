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
# VINGA_AUTH_SECRET and VINGA_API_SECRET. The conversation is the
# mock-provider smoke suite's job, and it runs against both variants.
#
# silero is the exception and stays a local engine deliberately: it is a
# core dependency rather than an optional extra, so it is present in
# both variants and runs on every audio frame whichever ASR is
# configured.
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

vinga-server config provider set llm claude -f - <<'YAML'
type: anthropic
api_key_env: ANTHROPIC_API_KEY
model: claude-sonnet-4-5
YAML

vinga-server config provider set asr cloud -f - <<'YAML'
type: openai
api_key_env: OPENAI_API_KEY
YAML

vinga-server config provider set tts cloud -f - <<'YAML'
type: openai
api_key_env: OPENAI_API_KEY
voice: alloy
YAML

vinga-server config provider set vad silero -f - <<'YAML'
type: silero
YAML

vinga-server config agent-defaults set -f - <<'YAML'
llm: claude
asr: cloud
tts: cloud
vad: silero
YAML

vinga-server config agent set assistant -f - <<'YAML'
prompt: A slim-image boot check.
YAML

vinga-server config default-agent set assistant

stop_server
