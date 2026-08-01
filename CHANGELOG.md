# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
using dates (`## YYYY-MM-DD`) as section headers instead of version numbers.

## 2026-08-01

### Changed

- New logo: a person and the device sharing one waveform, echoing the
  etymology of samtal (together + speech).

- README header now shows the project logo (`assets/samtal-logo.png`).

- README rewritten in the style of clew.nvim: etymology header, early-development
  warning with 🚧 markers, feature bullets, hardware table.

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
