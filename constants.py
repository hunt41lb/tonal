"""Tonal constants — single source of truth for all cross-module values."""

import os


# ── Audio ───────────────────────────────────────────────────────────────────

SAMPLE_RATE = 48000


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

# ── Application ─────────────────────────────────────────────────────────────

APP_VERSION = "1.0.4-u3"
