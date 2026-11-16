"""Generate PipeWire configuration files from Tonal state."""

import os
import re
import shutil
import datetime
import logging
from typing import Any
from constants import (
    PIPEWIRE_CONF_DIR, PULSE_CONF_DIR, ROUTING_CONF, PULSE_CONF,
    TYPE_TO_PIPEWIRE, TARGET_EXPANDED, TARGET_USB1_CHAT, TARGET_USB2,
    PREAMP_SHELF_FREQ,
)
from eq_math import clamp_bands

log = logging.getLogger("tonal.config")

def _backup(path):
    if os.path.exists(path):
        bak = path + ".bak"
        shutil.copy2(path, bak)
        log.info("Backed up %s → %s", path, bak)


def _eq_nodes_and_links(preamp_db, bands):
    nodes = [f'                    {{ type = builtin name = preamp label = bq_highshelf '
             f'control = {{ "Freq" = {PREAMP_SHELF_FREQ} "Q" = 0.707 "Gain" = {preamp_db} }} }}']
    links = []
    prev = "preamp"
    for i, band in enumerate(bands):
        name = f"eq{i:02d}"
        label = TYPE_TO_PIPEWIRE.get(band["type"], "bq_peaking")
        nodes.append(f'                    {{ type = builtin name = {name} label = {label} '
                     f'control = {{ "Freq" = {band["freq"]} "Q" = {band["q"]} "Gain" = {band["gain"]} }} }}')
        links.append(f'                    {{ output = "{prev}:Out" input = "{name}:In" }}')
        prev = name
    return "\n".join(nodes), "\n".join(links)


def _filter_chain(description, node_name, target, position, preamp_db, bands, dont_remix=False):
    nodes_str, links_str = _eq_nodes_and_links(preamp_db, bands)
    pos_str = "[ " + " ".join(position) + " ]"
    remix = "\n                stream.dont-remix = true" if dont_remix else ""

    # flags = [ nofail ]: a filter chain that fails to build must not abort
    # PipeWire startup. Without it, one bad graph takes down the whole daemon —
    # and with it xdg-desktop-portal, which makes every GTK app hang on startup.
    return f"""    {{ name = libpipewire-module-filter-chain
        flags = [ nofail ]
        args = {{
            node.description = "{description}"
            media.name       = "{description}"
            filter.graph = {{
                nodes = [
{nodes_str}
                ]
                links = [
{links_str}
                ]
            }}
            capture.props = {{
                node.name        = "{node_name}"
                media.class      = Audio/Sink
                audio.channels   = 2
                audio.position   = [ FL FR ]
            }}
            playback.props = {{
                node.name        = "{node_name}_out"
                target.object    = "{target}"
                audio.channels   = 2
                audio.position   = {pos_str}{remix}
                node.passive     = true
            }}
        }}
    }}"""


# What each hardware target is called in user-facing messages
_TARGET_LABELS = {
    TARGET_EXPANDED: "USB 1 Expanded",
    TARGET_USB1_CHAT: "USB 1 Chat",
    TARGET_USB2: "USB 2",
}

_ALSA_PATH_RE = re.compile(r'api\.alsa\.path\s*=\s*"([^"]*)"')


def resolve_targets(state, hw=None) -> tuple[bool, Any]:
    """Resolve every enabled channel to the PipeWire node its EQ chain feeds.

    `hw` is a hardware snapshot from state.detect_hardware(); it defaults to
    state["hardware"]. Callers that are about to write or verify a config should
    pass a fresh one — card numbers and node names change with USB enumeration.

    Returns (True, {"adapter": (card, dev, channels) | None,
                    "targets": {channel_node: (target_node, dont_remix)}})
    or (False, message) naming the hardware that is missing and the channels
    that need it. All-or-nothing on purpose: a config with a chain pointing at a
    device that isn't there silently sends that channel's apps to the default
    sink, which is far harder to diagnose than a refused apply.
    """
    hw = state.get("hardware", {}) if hw is None else hw
    adapter = None
    targets = {}
    missing = {}

    for ch in state["channels"]:
        if not ch["enabled"]:
            continue
        kind = ch["target"]

        if kind == TARGET_EXPANDED:
            if hw.get("expanded_mode") and hw.get("usb1_expanded_dev") is not None:
                adapter = (hw["usb1_alsa_card"], hw["usb1_expanded_dev"],
                           hw["expanded_channels"])
                targets[ch["node"]] = (TARGET_EXPANDED, ch["position"] != ["FL", "FR"])
                continue
        elif kind == TARGET_USB1_CHAT:
            if hw.get("usb1_chat_node"):
                targets[ch["node"]] = (hw["usb1_chat_node"], False)
                continue
        elif kind == TARGET_USB2:
            if hw.get("usb2_node"):
                targets[ch["node"]] = (hw["usb2_node"], False)
                continue
        else:
            targets[ch["node"]] = (kind, False)
            continue

        missing.setdefault(_TARGET_LABELS[kind], []).append(ch["name"])

    if missing:
        parts = [f"{dev} not found for {', '.join(names)}" for dev, names in missing.items()]
        msg = "; ".join(parts) + " — reconnect, or disable those channels"
        log.warning("Cannot resolve channel targets: %s", msg)
        return False, msg

    return True, {"adapter": adapter, "targets": targets}


def check_config_current(state, hw=None) -> tuple[bool, Any]:
    """Verify the routing config on disk matches the hardware that is present now.

    Only targets are compared — channel nodes, the hardware each one feeds, and
    the expanded adapter's ALSA path. EQ values are deliberately ignored: live
    EQ updates change state without regenerating the file.

    Returns (True, resolved) — the resolve_targets() result — or (False, message).
    """
    ok, resolved = resolve_targets(state, hw)
    if not ok:
        return False, resolved

    try:
        with open(ROUTING_CONF, "r") as f:
            text = f.read()
    except FileNotFoundError:
        if not resolved["targets"]:
            return True, resolved
        return False, "No routing config yet — Save & Apply to create it"
    except IOError as e:
        return False, f"Could not read routing config: {e}"

    stale = []
    adapter = resolved["adapter"]
    if adapter:
        want = f"hw:{adapter[0]},{adapter[1]}"
        m = _ALSA_PATH_RE.search(text)
        have = m.group(1) if m else None
        if have != want:
            stale.append(f"expanded adapter is {have or 'missing'}, device is now {want}")

    for node, (target, _remix) in resolved["targets"].items():
        m = re.search(rf'node\.name\s*=\s*"{re.escape(node)}_out"\s*'
                      rf'target\.object\s*=\s*"([^"]*)"', text)
        if not m:
            stale.append(f"no chain for {node}")
        elif m.group(1) != target:
            stale.append(f"{node} → {m.group(1)}, should be {target}")

    if stale:
        log.warning("Routing config is out of date: %s", "; ".join(stale))
        return False, "Routing is out of date — Save & Apply to fix"
    return True, resolved


def generate_routing_conf(state, resolved):
    """Render the routing config. `resolved` comes from resolve_targets()."""
    channels = state["channels"]
    eq = state["eq"]
    profile_name = eq["active_profile"]
    profile = eq["profiles"].get(profile_name, {"preamp_db": 0, "bands": []})
    preamp_db = profile["preamp_db"] if eq["enabled"] else 0.0
    if eq["enabled"]:
        bands = profile["bands"]
    else:
        # Keep band nodes in the filter chain but zero all gains, so the
        # node infrastructure stays in place for live EQ updates when re-enabled.
        bands = [{"freq": b["freq"], "q": b["q"], "gain": 0.0, "type": b["type"]}
                 for b in profile["bands"]]

    # Safety net: no biquad below the audible floor ever reaches PipeWire,
    # whatever the profile source. See constants.MIN_BAND_FREQ.
    bands = clamp_bands(bands)

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# RODECaster Pro II — Multi-Channel Routing with Per-Channel EQ",
        f"# Generated by Tonal on {ts}",
        f"# Profile: {profile_name} | Pre Amp: {preamp_db} dB | Bands: {len(bands)}",
        f"# DO NOT EDIT — regenerated by Tonal. Backup: .bak",
        f"# Every block below is flagged nofail: if a device is missing, that sink",
        f"# is skipped and PipeWire still starts normally.",
        f"",
    ]

    if resolved["adapter"]:
        # An unresolvable api.alsa.path is not a cosmetic problem: this block
        # lives in context.objects, which PipeWire builds during context
        # creation, so a miss makes the daemon exit -EINVAL before it loads a
        # single module. That takes xdg-desktop-portal with it and every GTK app
        # then blocks ~25s per settings lookup at startup. Hence two guards:
        # resolve_targets() only yields an adapter for a PCM that exists, and the
        # object is nofail so a later hardware change degrades to a missing sink
        # instead of no audio subsystem at all. Card numbers follow USB
        # enumeration order, so check_config_current() re-verifies this path on
        # every launch.
        alsa_card, alsa_dev, alsa_ch = resolved["adapter"]
        lines.extend([
            f"context.objects = [",
            f"    {{ factory = adapter",
            f"        flags = [ nofail ]",
            f"        args = {{",
            f'            factory.name           = api.alsa.pcm.sink',
            f'            node.name              = "{TARGET_EXPANDED}"',
            f'            node.description       = "RODECaster Expanded"',
            f'            media.class            = Audio/Sink',
            f'            api.alsa.path          = "hw:{alsa_card},{alsa_dev}"',
            f"            audio.channels         = {alsa_ch}",
            f"            audio.position         = [ FL FR FC LFE RL RR FLC FRC RC SL ]",
            f"            audio.format           = S32LE",
            f"            audio.rate             = 48000",
            f"            session.suspend-timeout-seconds = 0",
            f"        }}",
            f"    }}",
            f"]",
            f"",
        ])

    lines.append("context.modules = [")

    for ch in channels:
        if not ch["enabled"]:
            continue
        target, dont_remix = resolved["targets"][ch["node"]]
        lines.append(f"\n    # ── {ch['name']} ({' '.join(ch['position'])}) ──")
        lines.append(_filter_chain(ch["name"], ch["node"], target, ch["position"],
                                    preamp_db, bands, dont_remix))

    lines.append("]")
    return "\n".join(lines)


def generate_pulse_rules_conf(state):
    rules = state["routing"]["rules"]
    ch_map = {ch["name"]: ch["node"] for ch in state["channels"] if ch["enabled"]}
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# Per-App Routing Rules — Generated by Tonal on {ts}",
        f"# DO NOT EDIT — regenerated by Tonal. Backup: .bak",
        f"",
        f"pulse.rules = [",
    ]
    for rule in rules:
        node = ch_map.get(rule["channel"])
        if not node:
            # No silent fallback: an app pinned to a channel that is disabled or
            # gone simply follows the default sink, and the log says why.
            log.warning("Routing rule '%s' → '%s' skipped: channel is disabled or missing",
                        rule["app"], rule["channel"])
            continue
        key = "application.process.binary" if rule.get("binary") else "application.name"
        val = rule.get("binary", rule["app"])
        lines.append(f"    # {rule['app']} → {rule['channel']}")
        lines.append(f"    {{")
        lines.append(f'        matches = [ {{ {key} = "{val}" }} ]')
        lines.append(f'        actions = {{ update-props = {{ target.object = "{node}" }} }}')
        lines.append(f"    }}")
    lines.append("]")
    return "\n".join(lines)


def write_configs(state):
    """Write both config files, or neither.

    Returns (True, (routing_path, pulse_path)) or (False, message). When any
    enabled channel's hardware is missing nothing is touched — the last good
    config and its .bak stay in place.
    """
    ok, resolved = resolve_targets(state)
    if not ok:
        return False, resolved

    routing_text = generate_routing_conf(state, resolved)
    pulse_text = generate_pulse_rules_conf(state)

    os.makedirs(PIPEWIRE_CONF_DIR, exist_ok=True)
    os.makedirs(PULSE_CONF_DIR, exist_ok=True)
    _backup(ROUTING_CONF)
    _backup(PULSE_CONF)
    with open(ROUTING_CONF, "w") as f:
        f.write(routing_text)
    log.info("Wrote %s", ROUTING_CONF)
    with open(PULSE_CONF, "w") as f:
        f.write(pulse_text)
    log.info("Wrote %s", PULSE_CONF)
    return True, (ROUTING_CONF, PULSE_CONF)
