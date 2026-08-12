# Say agent, not persona

## Problem

The domain concepts page and the glossary record a terminology
decision: new writing says agent, because "persona" suggests the
differences between agents are cosmetic (a voice, a tone) when they
differ in capability and scope. The server's own text predated the
decision. `Field(description=...)` strings on the domain models are
rendered by docgen into four operator-facing surfaces (JSON Schema,
the committed domain reference, the OpenAPI document, and CLI
`--help`), so the stale word was not an internal matter: it was what
an operator read while configuring a deployment, and it contradicted
the vocabulary the concepts page asks users to learn.

## Changes

One word, replaced everywhere the server speaks with its own voice:

- `samtal_server/config/models.py`: the `AgentConfig` docstring, the
  prompt and agents-section descriptions, and the agent-defaults
  docstring.
- `samtal_server/config/docgen.py`: the agent entity's purpose line.
- `samtal_server/tools/memory.py`: the module docstring's keying
  rationale.
- `samtal_server/providers/registry.py`: a comment about
  language-locked agents.
- `config.example.yaml`: the memory section's comments.
- `README.md`, `samtal-server/README.md`: the configuration step, the
  prompt-table note, the ASR-prompt story, and the memory section.
- `samtal-server/examples/`: `agent.yaml`, `asr-openai.yaml`, and the
  fragment index.
- `docs/reference/domain-config.md` and
  `docs/reference/api-openapi.json`: regenerated from the models with
  the documented commands.

Two boundaries were drawn on purpose. Historical records (old
changelog entries, plans, closed issues, review-round notes) keep
their original wording, matching the glossary's own "older issues say
persona" note. Tests keep their internal persona identifiers
(`two_persona_config`, `test_two_personas.py`): they are not
operator-facing, and renaming test files is churn the sweep does not
need.

One sentence changed shape rather than one word: "The persona
instruction this agent replies under" became "The instruction this
agent replies under", rather than saying agent twice.

## Key parameters

None. No configuration key, default, or behavior changes; the diff is
docstrings, descriptions, comments, markdown, and the two generated
documents.

## Verification

- `grep -rni persona` over the repository, excluding tests, historical
  records, and the two pages that explain the terminology decision,
  returns nothing.
- Both reference documents regenerated with `samtal-server config
  reference` and `samtal-server config openapi`, so CI's
  regenerate-and-diff check sees them byte-identical.
- `uv run ruff check .` clean; `uv run pytest tests/unit -q` green
  (1130 passed, 15 skipped) after the package sweep. The second
  review-round commit touched only markdown and YAML comments; the CI
  lanes cover it on push.

## External review round

codex CLI, model gpt-5.6-sol, read-only, 2026-08-12, via the
external-review skill's self-posting PR runner, on the PR #108 diff.
Two findings, both P2, verdict "mergeable after the listed fixes":

1. The sweep left "persona" in current operator-facing documentation
   (both READMEs, three files under `examples/`). *Resolution*: all
   nine occurrences replaced, and the changelog entry widened to
   claim exactly that scope, in the commit titled "Sweep the READMEs
   and example fragments too". The verification grep above is the
   one that would have caught this before review.
2. A standalone notable change needs a feature document. *Resolution*:
   this document.

## Files modified

- `README.md`
- `samtal-server/README.md`
- `samtal-server/config.example.yaml`
- `samtal-server/examples/README.md`
- `samtal-server/examples/agent.yaml`
- `samtal-server/examples/asr-openai.yaml`
- `samtal-server/samtal_server/config/docgen.py`
- `samtal-server/samtal_server/config/models.py`
- `samtal-server/samtal_server/providers/registry.py`
- `samtal-server/samtal_server/tools/memory.py`
- `docs/reference/api-openapi.json`
- `docs/reference/domain-config.md`
- `docs/features/2026-08-12-agent-not-persona.md` (this document)
- `CHANGELOG.md`
