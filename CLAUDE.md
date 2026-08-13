# CLAUDE.md

## Project

Tonal — a GTK4/libadwaita Python desktop app for PipeWire audio equalization and routing. Currently targets the RØDECaster Pro II; migrating to device-agnostic multi-channel support.

## Architecture invariants

- Python 3.12+, GTK4/libadwaita via GI bindings. GStreamer + PipeWire are the audio backend.
- Zero-pip: stdlib + system packages via apt only. No new third-party dependencies without asking first. Prefer extending what exists over rebuilding or adding libraries.
- `constants.py` is the single source of truth for cross-module constants.
- Reusable GTK components live in `widgets/`; views live in `pages/`. Never create `components/` directories.
- Functions return `(ok, result)` tuples.
- All audio processing stays host-side in PipeWire. Never add USB vendor-protocol control, on-device DSP control, or per-device reverse engineering (no GoXLR onboard DSP/faders/lighting; no ALSA vendor mixer controls).

## Conventions

- Custom SVG icons follow `tonal-*-symbolic` naming, loaded through the existing `load_themed_svg_icon` helper.
- CSS lives in `data/style.css`, never inline in Python.
- Less is more: no new UI, abstraction, or dependency unless required.

## Packaging & updates

- Debian `.deb` built by `build-deb.sh`, which reads `APP_VERSION` from `constants.py`. Privileged install via `pkexec dpkg -i`.
- GitHub Releases API is the sole update channel.
- Test builds use the Debian tilde convention (e.g. `1.0.5~rc1`) so they sort below the release.

## Testing & diagnostics

- Headless UI tests: `xvfb-run -a`, call `Adw.init()` before constructing any widget, and drive state machines through internal callbacks rather than a live main loop.
- Audio tests use PipeWire virtual devices (null sinks / `pw-loopback`) — no hardware attached.
- Network tests use a local mock server (`python3 -m http.server`) with module-level substitution (e.g. `updater.UPDATE_API_URL`); keep production code free of test hooks.
- Common diagnostics: `wpctl status`, `pw-dump`, `dpkg-deb --fsys-tarfile`.

## Working style

- Review the relevant modules before starting feature work; match existing patterns.
- Implement incrementally and confirm at each step before proceeding.
- Ask before destructive changes to profile schemas or package structure.
