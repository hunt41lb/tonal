# Tonal

<p align="center">
  <img src="data/tonal-logo.png" alt="Tonal" width="400">
</p>

**Audio routing and parametric EQ manager for the RØDECaster Pro II on Linux.**

Tonal is a GTK4/libadwaita desktop application that turns the RØDECaster Pro II's multi-channel USB output into a fully routable audio system on Linux. It generates PipeWire filter chain configurations, giving you per-channel parametric EQ and per-application audio routing — all managed through a native GNOME-style interface.

## Why Tonal?

The RØDECaster Pro II exposes up to 10 channels over USB when Expanded mode is enabled, but Linux has no native tool to take advantage of them. Tonal bridges that gap by creating individual PipeWire filter chain sinks for each channel (System, Game, Music, etc.), applying shared parametric EQ across all of them, and routing applications to the correct channel automatically.

## Features

- **Multi-channel routing** — Supports the RØDECaster Pro II's Expanded mode (10-channel USB 1), standard stereo mode, USB 1 Chat, and USB 2 secondary output.
- **Parametric EQ** — Full biquad filter engine supporting peak, low shelf, high shelf, low pass, high pass, band pass, notch, and all pass filters. Add as many bands as you need.
- **Live EQ updates** — EQ changes are applied to running PipeWire filter chains in real time via `pw-cli set-param`, with no audio interruption. Structural changes (renaming channels, toggling channels on/off) trigger a PipeWire restart with automatic stream reconnection.
- **Per-app routing rules** — Assign applications to specific channels by name or binary. Rules are written to PipeWire-Pulse config and persist across restarts.
- **Profile management** — Save and switch between EQ profiles. Profiles are automatically backed up with a date stamp before each apply.
- **Auto-gain** — One-click pre-amp adjustment to prevent clipping based on the current EQ curve.
- **Hardware auto-detection** — Scans ALSA cards and PipeWire nodes on startup. No manual configuration of device names or card numbers required.
- **Background stream re-routing** — After a PipeWire restart, Tonal polls for 12 seconds and re-routes reconnecting audio streams to their assigned channels.

## Requirements

- **Linux** with PipeWire and WirePlumber (Ubuntu 24.04+, Fedora 40+, Arch, etc.)
- **Python 3.12+**
- **GTK 4** and **libadwaita 1.x**
- **RØDECaster Pro II** connected via USB

### System dependencies (Ubuntu/Debian)

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pipewire pipewire-pulse wireplumber
```

### System dependencies (Arch)

```bash
sudo pacman -S python-gobject gtk4 libadwaita pipewire pipewire-pulse wireplumber
```

No Python packages beyond the standard library are required. All audio interaction is done through PipeWire's CLI tools (`pw-cli`, `pw-metadata`, `pactl`) and generated configuration files.

## Installation

Clone the repository and run directly — no build step required:

```bash
git clone https://github.com/hunt41lb/tonal.git ~/Projects/tonal
cd ~/Projects/tonal
python3 tonal.py
```

### Desktop launcher (optional)

Update the `Exec` and `Icon` paths in `tonal.desktop` to match your install location, then install:

```bash
cp tonal.desktop ~/.local/share/applications/
```

Tonal will appear in your application launcher as a standard GNOME app.

## Usage

Launch the application and navigate between the four pages using the tab bar:

**Channels** — View detected hardware and audio channels. Toggle channels on/off, rename them, and see which ALSA positions each channel maps to. The default channel receives all audio that doesn't match a routing rule.

**Equalizer** — Add and adjust parametric EQ bands using vertical sliders. Changes are applied live without restarting PipeWire. Use the profile dropdown to switch between saved EQ curves, and the auto-gain button to set pre-amp automatically.

**Routing** — View active audio streams and their current sink assignments. Create persistent routing rules that map applications to specific channels by name or binary path.

**Status** — Hardware detection status, PipeWire/WirePlumber service health, filter chain node status, and manual restart/export/reset actions.

Click **Save & Apply** in the header bar to write configuration and apply changes. The button indicates whether a full PipeWire restart is needed or if changes can be applied live.

## How It Works

Tonal generates two PipeWire configuration files on each apply:

- `~/.config/pipewire/pipewire.conf.d/20-rodecaster-routing.conf` — Defines filter chain modules (one per enabled channel) with the current EQ bands, and an ALSA adapter for the expanded 10-channel interface when applicable.
- `~/.config/pipewire/pipewire-pulse.conf.d/50-app-routing.conf` — Defines PulseAudio-compatible routing rules that match applications to their assigned channel sinks.

Application state (channel configuration, EQ profiles, routing rules) is stored in `~/.config/tonal/state.json`.

Each channel appears as a selectable audio output device in your system sound settings. Applications routed to a channel have their audio processed through the shared EQ filter chain before being sent to the appropriate RØDECaster fader.

## Project Structure

```
tonal/
├── tonal.py                          # App entry point, main window
├── state.py                          # State management, hardware detection
├── config_gen.py                     # PipeWire config file generation
├── pipewire_ctl.py                   # PipeWire interaction (restart, live EQ, streams)
├── eq_math.py                        # Biquad filter math and frequency response
├── pages/
│   ├── __init__.py                   # Empty package init
│   ├── channels.py                   # Channel management page
│   ├── equalizer.py                  # Parametric EQ page
│   ├── routing.py                    # App routing rules page
│   └── status.py                     # System status and actions page
├── widgets/
│   ├── __init__.py                   # Empty package init
│   └── eq_sliders.py                 # Vertical EQ slider widget
├── bin/
│   └── tonal                         # Launcher script (→ /usr/bin/tonal)
├── data/
│   ├── tonal-logo.png                # Full logo lockup for README
│   ├── tonal.desktop                 # System .desktop entry (used by .deb)
│   └── icons/
│       └── com.tonal.app.svg         # App icon (transparent background)
├── debian/
│   ├── control                       # Package metadata and dependencies
│   ├── changelog                     # Version history
│   └── copyright                     # MIT license (Debian format)
├── build-deb.sh                      # Script to build the .deb package
├── tonal.desktop                     # Dev .desktop entry (hardcoded paths)
├── pyrightconfig.json                # Pylance/Pyright config
├── .gitignore
├── LICENSE
└── README.md
```

## RØDECaster Pro II Setup

For full multi-channel support, enable **Expanded mode** on the RØDECaster Pro II:

1. On the RØDECaster, go to **Settings → Outputs → USB 1** and set the output mode to **Expanded**.
2. Connect USB 1 to your Linux machine. Tonal will detect the 10-channel interface automatically.
3. Optionally connect USB 2 for a secondary stereo output.

In Expanded mode, the 10 ALSA channels map to five stereo pairs on the RØDECaster's faders: System (FL/FR), Game (FC/LFE), Music (RL/RR), A (FLC/FRC), and B (RC/SL). Tonal creates a PipeWire filter chain sink for each pair so they appear as individual output devices.

## License

[MIT](LICENSE)
