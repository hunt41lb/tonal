"""Tonal state management — auto-detects hardware, no hardcoded defaults."""

import json
import os
import copy
import subprocess
import logging
from constants import (
    STATE_DIR, STATE_FILE, EXPANDED_CHANNEL_MAP, DEFAULT_EQ_BANDS,
    TARGET_EXPANDED, TARGET_USB1_CHAT, TARGET_USB2,
)

log = logging.getLogger("tonal.state")

def load_state():
    """Load state from disk, then always refresh hardware detection."""
    state = None

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
            log.info("Loaded state from %s", STATE_FILE)
        except (json.JSONDecodeError, IOError) as e:
            log.warning("Failed to load state (%s), re-detecting", e)

    if state is None:
        log.info("First run — detecting hardware and building initial state")
        state = build_state_from_hardware()
        save_state(state)
        return state

    # Migrate older saved states (e.g. an empty Default profile) up to current
    # expectations before using them.
    _migrate_state(state)

    # Always refresh hardware detection so status is current
    hw = detect_hardware()
    state["hardware"] = hw
    log.info("Hardware detection refreshed")
    return state


def _migrate_state(state):
    """In-place upgrades for states saved by earlier versions."""
    # Seed the Default profile with flat bands if it was saved empty, so the
    # user always has 7 sliders ready to move instead of a blank EQ.
    profiles = state.get("eq", {}).get("profiles", {})
    default = profiles.get("Default")
    if default is not None and not default.get("bands"):
        default["bands"] = copy.deepcopy(DEFAULT_EQ_BANDS)
        log.info("Migrated empty 'Default' profile → %d flat bands", len(DEFAULT_EQ_BANDS))


def save_state(state):
    """Save state to disk."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    log.info("State saved to %s", STATE_FILE)


def build_state_from_hardware():
    """Scan hardware and build a clean initial state. Nothing hardcoded."""
    hw = detect_hardware()
    channels = detect_channels(hw)

    return {
        "version": 1,
        "channels": channels,
        "eq": {
            "enabled": True,
            "preamp_db": 0.0,
            "active_profile": "Default",
            "profiles": {
                "Default": {
                    "preamp_db": 0.0,
                    "bands": copy.deepcopy(DEFAULT_EQ_BANDS),
                },
            },
        },
        "routing": {
            "rules": [],
        },
        "hardware": hw,
    }


def detect_hardware():
    """Scan system for RODECaster Pro II USB devices and PipeWire nodes."""
    hw = {
        "usb1_detected": False,
        "usb2_detected": False,
        "expanded_mode": False,
        "expanded_channels": 0,
        "usb1_alsa_name": "",
        "usb1_alsa_card": "",
        "usb2_alsa_name": "",
        "usb1_chat_node": "",
        "usb2_node": "",
    }

    # ── Detect ALSA cards ───────────────────────────────────────────────
    try:
        with open("/proc/asound/cards", "r") as f:
            cards_text = f.read()

        for line in cards_text.split("\n"):
            line = line.strip()
            if "RODECaster Pro II" in line and "[" in line:
                card_num = line.split()[0]
                card_name = line.split("[")[1].split("]")[0].strip()
                hw["usb1_detected"] = True
                hw["usb1_alsa_name"] = card_name
                hw["usb1_alsa_card"] = card_num
                log.info("USB 1 detected: ALSA card %s (%s)", card_num, card_name)
            elif "RØDECaster Pro II" in line and "[" in line:
                card_name = line.split("[")[1].split("]")[0].strip()
                hw["usb2_detected"] = True
                hw["usb2_alsa_name"] = card_name
                log.info("USB 2 detected: ALSA card %s", card_name)
    except IOError:
        log.warning("Could not read /proc/asound/cards")

    # ── Check expanded mode ─────────────────────────────────────────────
    if hw["usb1_detected"] and hw["usb1_alsa_card"]:
        stream1_path = f'/proc/asound/card{hw["usb1_alsa_card"]}/stream1'
        try:
            with open(stream1_path, "r") as f:
                stream_text = f.read()
            for line in stream_text.split("\n"):
                if "Channels:" in line:
                    ch_count = int(line.split(":")[1].strip())
                    hw["expanded_channels"] = ch_count
                    hw["expanded_mode"] = (ch_count == 10)
                    log.info("USB 1 stream1: %d channels (expanded=%s)", ch_count, hw["expanded_mode"])
                    break
        except (IOError, ValueError):
            log.warning("Could not read %s", stream1_path)

    # ── Detect PipeWire node names ──────────────────────────────────────
    try:
        r = subprocess.run(["pw-cli", "list-objects", "Node"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for line in r.stdout.split("\n"):
                if "node.name" in line and "alsa_output" in line and "=" in line:
                    node = line.split("=")[1].strip().strip('"')
                    if "RODECaster_Pro_II" in node:
                        hw["usb1_chat_node"] = node
                        log.info("USB 1 Chat PipeWire node: %s", node)
                    elif "R__DECaster_Pro_II" in node:
                        hw["usb2_node"] = node
                        log.info("USB 2 PipeWire node: %s", node)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("Could not query PipeWire nodes")

    return hw


def hardware_fingerprint():
    """A signature of the RODECaster-relevant ALSA topology.

    Captures each RODECaster card line from /proc/asound/cards, including its
    index and USB path, so the signature changes when a RODECaster connection is
    added, removed, or re-enumerated on a different USB port. Callers poll this
    to detect hotplug/port-move events without a restart.
    """
    try:
        with open("/proc/asound/cards", "r") as f:
            text = f.read()
    except IOError:
        return ""
    return "\n".join(line.strip() for line in text.split("\n")
                     if "CASTER" in line.upper())


def detect_channels(hw):
    """Build channel list from detected hardware."""
    channels = []

    if hw["usb1_detected"] and hw["expanded_mode"]:
        # Expanded mode: System + Game + Music + A + B on the 10-channel interface
        for i, ch_def in enumerate(EXPANDED_CHANNEL_MAP):
            channels.append({
                "name": ch_def["device_name"],
                "node": ch_def["device_name"].lower().replace(" ", "_") + "_eq",
                "target": ch_def["target"],
                "position": ch_def["position"],
                "enabled": True,
                "is_default": (i == 0),  # First channel (System) is default
            })
            log.info("Channel: %s → %s %s", ch_def["device_name"],
                     ch_def["target"], ch_def["position"])

        # Chat is on USB 1's separate stereo PCM device
        if hw["usb1_chat_node"]:
            channels.append({
                "name": "Chat",
                "node": "chat_eq",
                "target": TARGET_USB1_CHAT,
                "position": ["FL", "FR"],
                "enabled": True,
                "is_default": False,
            })
            log.info("Channel: Chat → USB 1 Chat")

    elif hw["usb1_detected"]:
        # Standard mode: just Main and Chat
        channels.append({
            "name": "Main",
            "node": "main_eq",
            "target": TARGET_USB1_CHAT,
            "position": ["FL", "FR"],
            "enabled": True,
            "is_default": True,
        })
        log.info("Channel: Main (standard mode)")

    # USB 2 is always a separate stereo device
    if hw["usb2_detected"] and hw["usb2_node"]:
        channels.append({
            "name": "USB 2",
            "node": "usb2_eq",
            "target": TARGET_USB2,
            "position": ["FL", "FR"],
            "enabled": True,
            "is_default": False,
        })
        log.info("Channel: USB 2 → Secondary")

    if not channels:
        log.warning("No RODECaster channels detected — is the device connected?")

    return channels


# ── Profile helpers ─────────────────────────────────────────────────────────

def get_active_bands(state):
    name = state["eq"]["active_profile"]
    return state["eq"]["profiles"].get(name, {}).get("bands", [])


def get_active_preamp(state):
    name = state["eq"]["active_profile"]
    return state["eq"]["profiles"].get(name, {}).get("preamp_db", 0.0)


def save_profile_bands(state, bands, preamp_db):
    name = state["eq"]["active_profile"]
    if name in state["eq"]["profiles"]:
        state["eq"]["profiles"][name]["bands"] = copy.deepcopy(bands)
        state["eq"]["profiles"][name]["preamp_db"] = preamp_db
    state["eq"]["preamp_db"] = preamp_db
    log.info("Profile '%s' updated: %d bands, preamp %.1f dB", name, len(bands), preamp_db)

def backup_profile_before_apply(state):
    """Clone the active profile with today's date before applying changes."""
    from datetime import date
    name = state["eq"]["active_profile"]
    profile = state["eq"]["profiles"].get(name)

    # Only backup if the profile has actual bands
    if not profile or not profile.get("bands"):
        return

    today = date.today().strftime("%m-%d-%Y")
    backup_name = f"{name}-{today}"

    # Don't overwrite if already backed up today
    if backup_name in state["eq"]["profiles"]:
        log.info("Backup '%s' already exists for today", backup_name)
        return

    state["eq"]["profiles"][backup_name] = copy.deepcopy(profile)
    log.info("Backed up profile '%s' as '%s'", name, backup_name)
