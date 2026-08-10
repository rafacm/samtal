# Agent guidance for samtal

samtal is a self-hostable voice assistant: ESP32-S3 devices (mic, speaker,
display) talk to a Python conversation server over WebSocket. It builds on
78/xiaozhi-esp32 (device firmware) and xinnan-tech/xiaozhi-esp32-server
(server), both MIT.

## Repository layout

- `samtal-server/`: the conversation server (Python). OTA/config HTTP endpoint,
  WebSocket audio channel, VAD → ASR → LLM → TTS pipeline with pluggable
  providers (LLM, voice, MCP tools).
- `samtal-esp32/`: thin firmware customization on top of upstream
  xiaozhi-esp32 (ESP-IDF v6.0.x, target `esp32s3`).
- `docs/xiaozhi-notes.md`: research notes on the upstream architecture, the
  device↔server protocol, ports, configuration keys, and the validated
  end-to-end demo procedure. Read this first for anything protocol-related.
- `docs/architecture/principles.md`: the standing architecture principles
  (identity, boundaries, anti-goals), each with an example and a
  counterexample. Read this before designing a feature or deciding
  direction.
- `vendor/`: reference clones of the upstream repos. Not committed; recreate
  with the clone commands at the top of `docs/xiaozhi-notes.md`.

## Commands

All samtal-server commands run from the `samtal-server/` directory. Use `uv`
for Python; never `pip install` directly.

```bash
uv sync                          # Install/update dependencies
uv run samtal-server             # Run the server (--config or SAMTAL_CONFIG)
uv run pytest tests/unit -q      # Unit tests
uv run pytest tests/integration -q  # Integration tests
uv run ruff check .              # Lint
```

CI (`.github/workflows/samtal-server.yml`) runs the same lint, unit, and
integration steps, and only triggers on changes under `samtal-server/` or to
the workflow file itself.

### Restoring a file mid-experiment

Two traps, neither guessable, both of which have already cost a session.

- **Do not restore with `git checkout <file>`.** It restores the committed
  version, which silently discards unrelated uncommitted edits to that file.
  Copy the file aside first and copy it back.
- **After restoring a file, `touch` it.** A cached `.pyc` records the source's
  size and its mtime in whole seconds, and CPython accepts the cache when both
  still match. Restoring carries the backup's mtime rather than the current
  time, which can land back on the second the cache was compiled on, so the
  interpreter keeps running the pre-restore version. The test suite writes no
  bytecode and clears the caches it finds (`tests/conftest.py`), so pytest is
  safe, but anything run outside it is not. Export
  `PYTHONDONTWRITEBYTECODE=1` for those, or clear `__pycache__`.

The same shape bites a revert-run-restore cycle that checks a regression test
really fails without its fix: swapping two statements preserves the byte count,
a scripted cycle finishes inside one second, and the `.pyc` validation looks at
nothing else. If a result ever contradicts the source you are reading, suspect
this before suspecting the code.

## Workflow

- Before beginning any new work: verify the current branch is `main`
  (`git branch --show-current`), pull latest changes (`git pull --rebase`),
  and stop to ask for guidance if either step has problems.
- All code work happens on a dedicated branch off `main` with a descriptive
  name (e.g. `feature/ota-endpoint`, `fix/opus-framing`), merged back via
  pull request. Never commit code directly to `main`. Documentation-only
  changes may go straight to `main`.
- The repository allows rebase merges only; squash and merge commits are
  disabled.
- Commit in small, human-digestible units: one logical change per commit
  (e.g. package skeleton, tests, and CI workflow are three commits, not
  one). Every commit has an imperative title of roughly 50 characters and a
  body explaining the what and the why.

## Documentation process

- When a plan is accepted, commit it to `docs/plans/` as one Markdown file
  with a `YYYY-MM-DD-` date prefix (e.g. `2026-08-02-samtal-server-v1.md`).
- Each plan has a companion implementation doc, same filename with an
  `-implementation` suffix (e.g.
  `2026-08-02-samtal-server-v1-implementation.md`), with one section per
  milestone appended in the same change that ticks the milestone checklist.
  It records deviations from the plan, resolutions of the plan's open
  questions, and discoveries; a milestone with no deviations says so
  explicitly.
- Significant changes outside any active plan get a feature doc in
  `docs/features/`, same date-prefix naming, covering: Problem, Changes,
  Key parameters, Verification, and Files modified. Milestone work under a
  plan is documented by the implementation doc and the PR instead. No
  session transcripts are kept in this repository.
- Active plans keep a milestone checklist that doubles as the milestone
  descriptions (one annotated checkbox item per milestone, no separate
  status list). Tick the milestone (with its PR number) in the same change
  that completes it, and turn its name into a link to its section in the
  implementation doc, so a fresh session can resume from the repository
  alone.
- PR descriptions include a Verification section as a task list. Check a box
  only when that step was actually carried out; leave it unchecked with a
  short note when it cannot be verified yet. Unchecked boxes are
  information, never decoration.
- Keep in sync: the hardware tables in `README.md` and
  `samtal-esp32/README.md` list the same boards and must move together. When
  the samtal-server config schema changes, update `config.example.yaml` in
  the same change.

## Writing conventions

- Never use em-dashes anywhere: docs, commit messages, code comments.
  Rephrase with commas, colons, semicolons, parentheses, or sentence breaks.
- `CHANGELOG.md` follows Keep a Changelog 1.1.0, but with dates
  (`## YYYY-MM-DD`) as section headers instead of version numbers. Group
  entries under `### Added`, `### Changed`, `### Deprecated`, `### Removed`,
  `### Fixed`, `### Security`. Update it with every notable change.
- Describe deployment generically (a container image, your own
  infrastructure). Do not name specific hosting providers or platforms in
  documentation.
- README style follows clew.nvim conventions: centered header with logo and
  etymology, early-development warning, 🚧 marks for unimplemented features,
  honest status reporting.

## Licensing rules

- The project is MIT. When copying or deriving from the upstream repos, keep
  their license notices intact (see `THIRD_PARTY_LICENSES.md`).
- Keep TTS engines as optional pluggable providers; the `edge-tts` Python
  package is GPL-3.0 and must not become a hard dependency of the core
  server.
- Model weights (SenseVoice, Silero, ESP-SR wake words) are downloaded at
  deploy time, never committed or redistributed.

## GitHub API (`gh`) tips

- **Always pass `--repo rafacm/samtal`.** `gh` infers the repository from
  the working directory's git remote, and `vendor/` holds clones of the
  upstream projects, so a `gh` command run from `vendor/xiaozhi-esp32`
  targets `78/xiaozhi-esp32` instead. A `cd` into a vendor clone to read
  firmware source is an ordinary thing to do mid-task, and it silently
  redirects every `gh` call after it. This has already happened once: an
  `issue edit` went to the upstream repository and failed only because
  the account has no write access there. A `gh issue comment` would have
  posted to a stranger's tracker instead of failing.
  `export GH_REPO=rafacm/samtal` at the start of a session overrides the
  inference for `issue`, `pr` and `api`, and is worth doing as well, not
  instead: the flag is what makes the intent visible in the command that
  gets reviewed. Do not check that it worked with `gh repo view`, which
  reports the working directory's repository whatever `GH_REPO` says;
  `gh issue list` is the honest test.
- Wrap request bodies containing backticks in a `$(cat <<'EOF' ... EOF)`
  heredoc; bare backticks in `-f body="..."` are interpreted by zsh.
- Reply to a PR review comment by POSTing to
  `repos/OWNER/REPO/pulls/PR/comments` with `-F in_reply_to=COMMENT_ID`;
  there is no `/replies` sub-endpoint (it returns 404).
- PR review comments live at `pulls/PR/comments`; general PR comments at
  `issues/PR/comments` (PRs are issues).

## Hardware context

Primary test device: Waveshare ESP32-S3-Touch-LCD-1.54 (ESP32-S3, 16 MB
flash, 8 MB PSRAM). Flashing uses esptool with merged binaries at offset
`0x0`; the device's backend URL lives in NVS (namespace `wifi`, key
`ota_url`, partition at `0x9000`). Details and gotchas are in
`docs/xiaozhi-notes.md`: the reply-language configuration trap, and how
to reset the board, read its boot log, and dump its NVS over serial,
which is what a device checkpoint runs on.
