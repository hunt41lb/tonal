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


# Number of channels the RODECaster exposes in Expanded mode. Derived from the
# channel map so the two can never drift apart.
EXPANDED_CHANNELS = sum(len(c["position"]) for c in EXPANDED_CHANNEL_MAP)


def playback_channels(stream_path):
    """Highest playback channel count advertised in a /proc/asound/cardN/streamM file.

    The file lists a Playback: block then a Capture: block, each possibly with
    several altsets. Only the Playback side matters for a sink, and we take the
    widest altset. Returns 0 if the file is unreadable or has no playback side.
    """
    try:
        with open(stream_path, "r") as f:
            text = f.read()
    except IOError:
        return 0

    if "Playback:" not in text:
        return 0
    block = text.split("Playback:", 1)[1]
    if "Capture:" in block:
        block = block.split("Capture:", 1)[0]

    best = 0
    for line in block.split("\n"):
        line = line.strip()
        if line.startswith("Channels:"):
            try:
                best = max(best, int(line.split(":", 1)[1].strip()))
            except ValueError:
                continue
    return best


def find_expanded_pcm(card_num, min_channels=EXPANDED_CHANNELS):
    """Locate a playback PCM on this card wide enough for Expanded mode.

    Returns (dev_index, channels) for the first match, or (None, widest_seen).
    Probing every streamN rather than assuming stream1 means a firmware or
    kernel change that renumbers the PCMs doesn't silently break detection.
    """
    card_dir = f"/proc/asound/card{card_num}"
    try:
        entries = sorted(os.listdir(card_dir))
    except OSError:
        return None, 0

    widest = 0
    for entry in entries:
        if not entry.startswith("stream"):
            continue
        try:
            dev = int(entry[len("stream"):])
        except ValueError:
            continue
        ch = playback_channels(os.path.join(card_dir, entry))
        widest = max(widest, ch)
        if ch >= min_channels:
            return dev, ch
    return None, widest


def list_rodecaster_cards():
    """Every RODECaster card currently registered with ALSA.

    Returns a list of dicts: {"card": "1", "id": "II", "name": "RODECaster Pro II"}.
    Matched on "CASTER" case-insensitively so both the ASCII and the Ø spelling
    of the product string are caught — which spelling a given USB port reports
    is a property of the device descriptor, not something we should depend on.
    """
    try:
        with open("/proc/asound/cards", "r") as f:
            cards_text = f.read()
    except IOError:
        log.warning("Could not read /proc/asound/cards")
        return []

    found = []
    for line in cards_text.split("\n"):
        stripped = line.strip()
        if "[" not in stripped or "]" not in stripped or "CASTER" not in stripped.upper():
            continue
        try:
            card_num = stripped.split()[0]
            int(card_num)  # the index column; skip continuation lines
            card_id = stripped.split("[", 1)[1].split("]", 1)[0].strip()
            name = stripped.split("]:", 1)[1].strip() if "]:" in stripped else ""
        except (IndexError, ValueError):
            continue
        found.append({"card": card_num, "id": card_id, "name": name})
    return found


def resolve_expanded_target():
    """Find the card and PCM device that actually carries the Expanded stream.

    Selection is by capability, not by product string or card index: whichever
    RODECaster card exposes a playback PCM with EXPANDED_CHANNELS channels wins.
    Both USB connections present a "RODECaster Pro II" descriptor (one with Ø),
    and their ALSA card numbers are assigned by USB enumeration order, so
    neither name nor index identifies the right cable on its own.

    Returns (card_num, dev_index, channels) or None when no card qualifies —
    e.g. the RODECaster is in stereo mode, or USB 1 is unplugged.
    """
    for card in list_rodecaster_cards():
        dev, ch = find_expanded_pcm(card["card"])
        if dev is not None:
            log.info("Expanded PCM: card %s (%s) device %d, %d channels",
                     card["card"], card["id"], dev, ch)
            return card["card"], dev, ch
    return None


def detect_hardware():
    """Scan system for RODECaster Pro II USB devices and PipeWire nodes."""
    hw = {
        "usb1_detected": False,
        "usb2_detected": False,
        "expanded_mode": False,
        "expanded_channels": 0,
        "usb1_alsa_name": "",
        "usb1_alsa_card": "",
        "usb1_expanded_dev": None,
        "usb2_alsa_name": "",
        "usb1_chat_node": "",
        "usb2_node": "",
    }

    # ── Detect ALSA cards ───────────────────────────────────────────────
    # USB 1 is defined as the connection carrying the Expanded stream, found by
    # probing for a 10-channel playback PCM. Anything else that looks like a
    # RODECaster is USB 2. If nothing is expanded (stereo mode), fall back to
    # the first card so status reporting and the stereo paths still work.
    cards = list_rodecaster_cards()
    expanded = resolve_expanded_target()

    if expanded is not None:
        card_num, dev, ch = expanded
        hw["usb1_detected"] = True
        hw["usb1_alsa_card"] = card_num
        hw["usb1_expanded_dev"] = dev
        hw["expanded_channels"] = ch
        hw["expanded_mode"] = True
        hw["usb1_alsa_name"] = next(
            (c["id"] for c in cards if c["card"] == card_num), "")
        log.info("USB 1 (expanded): ALSA card %s (%s) device %d, %d channels",
                 card_num, hw["usb1_alsa_name"], dev, ch)
    elif cards:
        primary = cards[0]
        hw["usb1_detected"] = True
        hw["usb1_alsa_card"] = primary["card"]
        hw["usb1_alsa_name"] = primary["id"]
        _, widest = find_expanded_pcm(primary["card"])
        hw["expanded_channels"] = widest
        log.info("USB 1 (stereo): ALSA card %s (%s), widest playback %d ch — "
                 "enable Expanded mode on the RODECaster for multi-channel",
                 primary["card"], primary["id"], widest)

    for card in cards:
        if card["card"] == hw["usb1_alsa_card"]:
            continue
        hw["usb2_detected"] = True
        hw["usb2_alsa_name"] = card["id"]
        log.info("USB 2 detected: ALSA card %s (%s)", card["card"], card["id"])
        break

    if not cards:
        log.info("No RODECaster cards found in /proc/asound/cards")

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
