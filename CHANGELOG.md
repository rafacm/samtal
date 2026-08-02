# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
using dates (`## YYYY-MM-DD`) as section headers instead of version numbers.

## 2026-08-02

### Added

- samtal-server device OTA/config endpoint (M2) at `/xiaozhi/ota/`: a device
  POSTs its system info and receives the WebSocket URL, an (as yet empty)
  token, the binary protocol version to speak, and the wall clock. The
  firmware section always answers "up to date" because samtal-server serves
  no images, and no activation section is ever sent. The `Device-Id` MAC
  resolves to an agent through the config, falling back to `default_agent`.
  A `GET` on the same path reports where devices are being sent. New
  `server` keys: `websocket_url` (defaults to the address the device reached
  the OTA endpoint on), `protocol_version`, and `timezone_offset_minutes`.
- samtal-server configuration layer (M1), built on pydantic-settings:
  models for `server`, `providers`, `agents`, `devices`, and
  `default_agent`, loaded from one YAML file (`--config` or
  `SAMTAL_CONFIG`). Any key is overridable via `SAMTAL_`-prefixed
  environment variables (nested keys joined with `__`, e.g.
  `SAMTAL_SERVER__PORT`), and a `.env` file is read at startup with
  environment variables taking priority. Secrets are referenced by
  environment variable name only, and validation reports every problem
  with its location. A documented `config.example.yaml` ships with the
  server.
- `docs/README.md` as an index of the research notes, plans, and feature
  docs, linked from the root README's project layout.
- samtal-server README sections on transports (WebSocket only for v1, with
  upstream's MQTT+UDP as the additive alternative; WebRTC is not an upstream
  transport) and on ports and topology, covering the single-port choice, its
  tradeoffs, and what a reverse proxy in front of it has to get right.
- Waveshare ESP32-S3-Touch-AMOLED-2.16 (480×480 AMOLED, dual-mic AEC) listed
  as a planned target board.
- samtal-server stack decision: Python 3.12 + FastAPI (uv-managed), with the
  xiaozhi-sdk device simulator for hardware-free integration tests.
- samtal-server v1 plan (`docs/plans/2026-08-02-samtal-server-v1.md`):
  architecture, milestones M0 to M7 with device checkpoints, folder-scoped
  GitHub Actions CI, and instance-config separation.
- Workflow and documentation conventions in `AGENTS.md`: feature branches
  with rebase-only PRs for code work, dated plan files in `docs/plans/`,
  feature docs in `docs/features/`, and `gh` API tips.
- M0 skeleton for samtal-server: uv-managed Python 3.12 package with FastAPI
  app and `/healthz`, unit and integration test lanes, ruff, and the
  folder-scoped GitHub Actions workflow.

### Changed

- Hardware tables (root and samtal-esp32 READMEs) now list the e-paper
  board first, link each board name to its product page, and keep a single
  "wiki" link in the Links column.
- samtal-server now logs its own work: the CLI gives the root logger a
  handler, which uvicorn does not do, so messages from samtal-server no
  longer vanish while uvicorn's request lines appear.
- Updated logo artwork (`assets/samtal-logo.png`), same concept: the person
  and the device sharing one waveform.
- Hardware tables now link each board's product page and technical
  documentation ("doc").
- The logo is a single transparent PNG of the original artwork
  (`assets/samtal-logo.png`); the traced SVG variant is removed.

## 2026-08-01

### Changed

- New logo: a person and the device sharing one waveform, echoing the
  etymology of samtal (together + speech).
- Logo rebuilt as vector art: `assets/samtal-logo.svg` is now the source of
  truth, and `assets/samtal-logo.png` is rendered from it with a transparent
  background (fixes white edge pixels on dark pages).
- README header now shows the project logo (`assets/samtal-logo.png`).
- README rewritten in the style of clew.nvim: etymology header, early-development
  warning with 🚧 markers, feature bullets, hardware table.

### Fixed

- The vector trace of the logo had flattened the original color gradations;
  the SVG now uses real linear gradients on the orange and blue regions so it
  matches the raster original on both light and dark backgrounds.

### Added

- `AGENTS.md` with project conventions for coding agents, and `CLAUDE.md`
  referencing it.

- Project scaffold: `samtal-esp32/` (device firmware) and `samtal-server/`
  (conversation server) subprojects.
- `docs/xiaozhi-notes.md`: research notes on the upstream xiaozhi firmware and
  server, covering architecture, device↔server protocol, configuration, and the
  procedure used for the first working end-to-end demo on a Waveshare
  ESP32-S3-Touch-LCD-1.54.
- MIT license and third-party license notices for the upstream projects
  ([78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32),
  [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)).
