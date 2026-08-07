"""Tonal constants — single source of truth for all cross-module values."""

import os


# ── Audio ───────────────────────────────────────────────────────────────────

SAMPLE_RATE = 48000


# ── EQ Safety Limits ────────────────────────────────────────────────────────
# Biquad filters below ~20 Hz at 48 kHz lose float32 coefficient precision and
# generate denormal states that can stall PipeWire's real-time thread on some
# CPUs (observed after an Intel → Ryzen 9000 upgrade: audio played ~1s then
# dropped for ~20s, RODECaster meter frozen). Every band frequency AND the
# preamp shelf are clamped into this range before they reach a filter, so no
# profile — imported, hand-edited, or legacy — can put a biquad below the floor.
# 20 Hz is the bottom of human hearing and was hardware-verified safe.
MIN_BAND_FREQ = 20.0
MAX_BAND_FREQ = 20000.0
PREAMP_SHELF_FREQ = 20.0


# ── Channel Routing Targets ─────────────────────────────────────────────────
# These identify which physical output a channel routes to.
# Used in state.py (channel detection), config_gen.py (config writing),
# and pages/channels.py (display labels).

TARGET_EXPANDED = "rodecaster_expanded"
TARGET_USB1_CHAT = "usb1_chat"
TARGET_USB2 = "usb2"


# ── File Paths ──────────────────────────────────────────────────────────────

STATE_DIR = os.path.expanduser("~/.config/tonal")
STATE_FILE = os.path.join(STATE_DIR, "state.json")

PIPEWIRE_CONF_DIR = os.path.expanduser("~/.config/pipewire/pipewire.conf.d")
PULSE_CONF_DIR = os.path.expanduser("~/.config/pipewire/pipewire-pulse.conf.d")
ROUTING_CONF = os.path.join(PIPEWIRE_CONF_DIR, "20-rodecaster-routing.conf")
PULSE_CONF = os.path.join(PULSE_CONF_DIR, "50-app-routing.conf")


# ── Expanded Channel Map ────────────────────────────────────────────────────
# Hardware-defined ALSA channel positions for each virtual device
# when the RØDECaster Pro II is in Expanded mode (10-channel USB 1).

EXPANDED_CHANNEL_MAP = [
    {"device_name": "System", "position": ["FL", "FR"],   "target": TARGET_EXPANDED},
    {"device_name": "Game",   "position": ["FC", "LFE"],  "target": TARGET_EXPANDED},
    {"device_name": "Music",  "position": ["RL", "RR"],   "target": TARGET_EXPANDED},
    {"device_name": "A",      "position": ["FLC", "FRC"], "target": TARGET_EXPANDED},
    {"device_name": "B",      "position": ["RC", "SL"],   "target": TARGET_EXPANDED},
]


# ── Filter Types ────────────────────────────────────────────────────────────
# Canonical list of all supported EQ filter types.

FILTER_TYPES = [
    "peak", "lowshelf", "highshelf",
    "lowpass", "highpass", "bandpass", "notch", "allpass",
]

# Human-readable labels (dropdowns, tooltips)
FILTER_LABELS = {
    "peak":      "Peak",
    "lowshelf":  "Low Shelf",
    "highshelf": "High Shelf",
    "lowpass":   "Low Pass",
    "highpass":  "High Pass",
    "bandpass":  "Band Pass",
    "notch":     "Notch",
    "allpass":   "All Pass",
}

# Two-letter abbreviations (compact display)
FILTER_SHORT = {
    "peak":      "PK",
    "lowshelf":  "LS",
    "highshelf": "HS",
    "lowpass":   "LP",
    "highpass":  "HP",
    "bandpass":  "BP",
    "notch":     "NO",
    "allpass":   "AP",
}

# Full display names (filter type popovers)
FILTER_FULL_NAMES = {
    "peak":      "Peak Filter",
    "lowshelf":  "Low Shelf Filter",
    "highshelf": "High Shelf Filter",
    "lowpass":   "Low Pass Filter",
    "highpass":  "High Pass Filter",
    "bandpass":  "Band Pass Filter",
    "notch":     "Notch Filter",
    "allpass":   "All Pass Filter",
}

# SVG icon filenames (without path or extension)
FILTER_ICON_NAMES = {
    "peak":      "tonal-peak-filter-symbolic",
    "lowshelf":  "tonal-low-shelf-filter-symbolic",
    "highshelf": "tonal-high-shelf-filter-symbolic",
    "lowpass":   "tonal-low-pass-filter-symbolic",
    "highpass":  "tonal-high-pass-filter-symbolic",
    "bandpass":  "tonal-band-pass-filter-symbolic",
    "notch":     "tonal-notch-filter-symbolic",
    "allpass":   "tonal-all-pass-filter-symbolic",
}

# Internal type → PipeWire biquad filter label (used in config generation)
TYPE_TO_PIPEWIRE = {
    "peak":      "bq_peaking",
    "lowshelf":  "bq_lowshelf",
    "highshelf": "bq_highshelf",
    "lowpass":   "bq_lowpass",
    "highpass":  "bq_highpass",
    "bandpass":  "bq_bandpass0",
    "notch":     "bq_notch",
    "allpass":   "bq_allpass",
}

# ── Default EQ Profile ──────────────────────────────────────────────────────
# Bands the "Default" profile ships with: 7 log-spaced peaking bands, all flat
# (0 dB) so a new user can grab any slider immediately instead of adding bands
# one at a time. Frequencies follow a musical, roughly ISO-spaced layout.

DEFAULT_EQ_BANDS = [
    {"freq": 60,    "q": 1.0, "gain": 0.0, "type": "peak"},
    {"freq": 150,   "q": 1.0, "gain": 0.0, "type": "peak"},
    {"freq": 400,   "q": 1.0, "gain": 0.0, "type": "peak"},
    {"freq": 1000,  "q": 1.0, "gain": 0.0, "type": "peak"},
    {"freq": 2400,  "q": 1.0, "gain": 0.0, "type": "peak"},
    {"freq": 6000,  "q": 1.0, "gain": 0.0, "type": "peak"},
    {"freq": 15000, "q": 1.0, "gain": 0.0, "type": "peak"},
]


# ── Application ─────────────────────────────────────────────────────────────

APP_VERSION = "1.0.5-rc1"

# ── Update Channel ──────────────────────────────────────────────────────────
# The updater talks to this repository's GitHub Releases feed and nowhere
# else. /releases/latest deliberately excludes drafts and pre-releases, so
# betas can be published for testers without being offered as updates.

GITHUB_REPO = "hunt41lb/tonal"
UPDATE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
