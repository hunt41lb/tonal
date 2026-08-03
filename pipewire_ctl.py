"""PipeWire system interaction — restart, query, move streams."""

import subprocess
import os
import time
import logging
import threading

from constants import TARGET_EXPANDED
from eq_math import clamp_bands

log = logging.getLogger("tonal.pipewire")


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("Command failed: %s — %s", cmd, e)
        return False, str(e)


def restart_pipewire():
    log.info("Restarting PipeWire...")
    ok, out = _run(["systemctl", "--user", "restart", "pipewire", "pipewire-pulse", "wireplumber"])
    if ok:
        log.info("PipeWire restart command sent")
        return True, "PipeWire restarted"
    log.error("Restart failed: %s", out)
    return False, f"Failed to restart: {out}"


def _wait_for_nodes(node_names, timeout=8):
    """Poll until all specified PipeWire nodes appear (or timeout)."""
    start = time.time()
    while time.time() - start < timeout:
        ok, out = _run(["pw-cli", "list-objects", "Node"], timeout=3)
        if ok:
            found = sum(1 for name in node_names if f'"{name}"' in out)
            if found >= len(node_names):
                elapsed = time.time() - start
                log.info("All %d nodes ready in %.1fs", len(node_names), elapsed)
                return True
            log.info("Waiting for nodes: %d/%d ready...", found, len(node_names))
        time.sleep(0.3)
    log.warning("Timeout waiting for nodes after %.1fs", timeout)
    return False


def _reapply_routing_loop(state):
    """Background loop: poll for reconnecting streams and re-route them as they appear."""
    ch_map = {ch["name"]: ch["node"] for ch in state["channels"]}

    # Build app/binary → node mapping from routing rules
    route_map = {}
    for rule in state["routing"]["rules"]:
        node = ch_map.get(rule["channel"])
        if node:
            if rule.get("binary"):
                route_map[rule["binary"]] = node
            route_map[rule["app"]] = node

    if not route_map:
        log.info("No routing rules configured — skipping stream re-routing")
        return

    moved_ids = set()
    log.info("Background re-routing started — watching for streams to reconnect...")

    for attempt in range(12):
        time.sleep(1.0)

        streams = get_active_streams()
        if not streams:
            continue

        sinks = get_sink_list()
        sink_map = {s["name"]: s["id"] for s in sinks}

        for stream in streams:
            sid = stream["stream_id"]
            if sid in moved_ids:
                continue
            target_node = route_map.get(stream["app"])
            if target_node and target_node in sink_map:
                target_sink = sink_map[target_node]
                if target_sink != stream.get("sink_id"):
                    ok, _ = move_stream(sid, target_sink)
                    if ok:
                        moved_ids.add(sid)
                        log.info("Re-routed '%s' (stream %s) → %s", stream["app"], sid, target_node)

    log.info("Background re-routing done: moved %d stream(s) over 12s", len(moved_ids))


def set_default_sink(node_name):
    log.info("Setting default sink: %s", node_name)
    ok, out = _run(["pw-metadata", "0", "default.configured.audio.sink", f'{{"name":"{node_name}"}}'])
    return (True, f"Default: {node_name}") if ok else (False, f"Failed: {out}")


def get_pipewire_version():
    ok, out = _run(["pw-cli", "info", "0"])
    if ok:
        for line in out.split("\n"):
            if "version" in line.lower() and ":" in line:
                return line.split(":")[1].strip()
    return "unknown"


def is_service_running(service):
    ok, _ = _run(["systemctl", "--user", "is-active", service])
    return ok


def get_pipewire_nodes(state=None):
    """Report status for the PipeWire nodes Tonal actually manages.

    The node names are read live from the current channel configuration in
    `state` — never hardcoded — so the Status page always reflects the channels
    that were really generated (system_eq, game_eq, chat_eq, ...) rather than a
    stale wishlist. The shared expanded ALSA adapter is included whenever any
    enabled channel routes through it.
    """
    channels = (state or {}).get("channels", [])
    targets = []
    if any(ch.get("target") == TARGET_EXPANDED for ch in channels if ch.get("enabled")):
        targets.append(TARGET_EXPANDED)
    targets.extend(ch["node"] for ch in channels if ch.get("enabled"))

    if not targets:
        # No configured channels yet (device not detected) — keep the row list
        # non-empty so the page renders, but don't invent channel names.
        targets = [TARGET_EXPANDED]

    ok, out = _run(["pw-cli", "list-objects", "Node"])
    if not ok:
        return [{"name": n, "status": "unknown"} for n in targets]
    return [{"name": n, "status": "running" if f'"{n}"' in out else "missing"} for n in targets]


def check_usb_connected():
    try:
        with open("/proc/asound/cards", "r") as f:
            cards = f.read()
        return {"usb1": "RODECaster Pro II" in cards, "usb2": "RØDECaster Pro II" in cards}
    except IOError:
        return {"usb1": False, "usb2": False}


def get_active_streams():
    ok, out = _run(["pactl", "list", "sink-inputs"])
    if not ok:
        return []
    streams, cur = [], {}
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("Sink Input #"):
            if cur.get("app"):
                streams.append(cur)
            cur = {"stream_id": line.replace("Sink Input #", "").strip(), "app": "", "sink_id": ""}
        elif line.startswith("Sink:"):
            cur["sink_id"] = line.split(":")[1].strip()
        elif line.startswith("application.name"):
            cur["app"] = line.split("=")[1].strip().strip('"')
    if cur.get("app"):
        streams.append(cur)
    return [s for s in streams if s["app"] and not s["app"].startswith("PipeWire")]


def get_sink_list():
    ok, out = _run(["pactl", "list", "sinks", "short"])
    if not ok:
        return []
    sinks = []
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2:
            sinks.append({"id": parts[0].strip(), "name": parts[1].strip()})
    return sinks


def move_stream(stream_id, sink_id):
    ok, out = _run(["pactl", "move-sink-input", str(stream_id), str(sink_id)])
    return (True, "Stream moved") if ok else (False, f"Failed: {out}")

def _find_node_id(node_name):
    """Find PipeWire object ID by node.name."""
    ok, out = _run(["pw-cli", "list-objects", "Node"])
    if not ok:
        return None
    current_id = None
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("id ") and "type PipeWire:Interface:Node" in line:
            current_id = line.split(",")[0].replace("id ", "").strip()
        elif f'node.name = "{node_name}"' in line and current_id:
            return current_id
        elif line.startswith("id "):
            current_id = None
    return None


def _build_eq_params(preamp_db, bands):
    """Build the SPA JSON params array for all EQ controls."""
    parts = [f'"preamp:Gain" {preamp_db}']
    for i, band in enumerate(bands):
        name = f"eq{i:02d}"
        parts.append(f'"{name}:Freq" {band["freq"]}')
        parts.append(f'"{name}:Q" {band["q"]}')
        parts.append(f'"{name}:Gain" {band["gain"]}')
    return "{ params = [ " + " ".join(parts) + " ] }"


def apply_eq_live(state):
    """Update EQ parameters on running filter chains — no PipeWire restart needed."""
    from state import save_state

    save_state(state)

    eq = state["eq"]
    profile = eq["profiles"].get(eq["active_profile"], {})
    # Clamp before pushing to running nodes — a live Freq update could otherwise
    # drive a band below the safe floor. See constants.MIN_BAND_FREQ.
    profile_bands = clamp_bands(profile.get("bands", []))

    if eq["enabled"]:
        preamp_db = profile.get("preamp_db", 0.0)
        bands = profile_bands
    else:
        # EQ disabled — send bands with gain zeroed so running filter chain
        # nodes are explicitly flattened. Without this, the band nodes keep
        # their last-applied gain values since pw-cli set-param is per-property.
        preamp_db = 0.0
        bands = [{"freq": b["freq"], "q": b["q"], "gain": 0.0} for b in profile_bands]

    params_str = _build_eq_params(preamp_db, bands)
    updated = 0
    failed = 0

    for ch in state["channels"]:
        if not ch["enabled"]:
            continue

        node_id = _find_node_id(ch["node"])
        if not node_id:
            log.warning("Node '%s' not found — skipping live update", ch["node"])
            failed += 1
            continue

        ok, out = _run(["pw-cli", "set-param", node_id, "Props", params_str])
        if ok:
            updated += 1
            log.info("Live EQ update on '%s' (id %s) succeeded", ch["node"], node_id)
        else:
            failed += 1
            log.error("Live EQ update on '%s' failed: %s", ch["node"], out)

    if failed > 0 and updated == 0:
        return False, f"Live EQ update failed on all {failed} node(s) — try full restart"
    elif failed > 0:
        return True, f"EQ updated on {updated} node(s), {failed} failed"
    else:
        return True, f"EQ updated live on {updated} channel(s) — no restart needed"

def ensure_expanded_ready(state):
    """Recover from the boot-time race where PipeWire starts before the RODECaster's
    USB card is enumerated.

    Symptom (seen in the journal): 'hw:...,1 playback open failed: No such device'
    → the filter-chain daemon exits and the expanded adapter never appears until a
    manual audio-server restart. If an expanded channel is configured and its ALSA
    card is now present but the 'rodecaster_expanded' node is missing, restart
    PipeWire ONCE to bring it up. No-ops on a healthy launch.
    """
    hw = state.get("hardware", {})
    needs_expanded = any(ch.get("target") == TARGET_EXPANDED
                         for ch in state.get("channels", []) if ch.get("enabled"))
    if not needs_expanded or not hw.get("usb1_detected"):
        return False, "no expanded channel or card not present"

    ok, out = _run(["pw-cli", "list-objects", "Node"], timeout=3)
    if ok and '"rodecaster_expanded"' in out:
        return False, "expanded adapter already running"

    log.warning("Expanded adapter missing though card is present — "
                "restarting PipeWire to recover from boot-time race")
    restart_pipewire()
    time.sleep(1.0)
    recovered = _wait_for_nodes(["rodecaster_expanded"], timeout=6)
    return recovered, ("recovered" if recovered else "restart did not bring up adapter")


def apply_config(state, default_node="system_eq"):
    """Full apply: save → write → restart → wait for nodes → set default → background re-route."""
    from config_gen import write_configs
    from state import save_state

    save_state(state)
    log.info("State saved")

    try:
        write_configs(state)
        log.info("Configs written (with backups)")
    except Exception as e:
        log.error("Config write failed: %s", e)
        return False, f"Failed to write configs: {e}"

    # Clear stale stream state
    sp = os.path.expanduser("~/.local/state/wireplumber/stream-properties")
    if os.path.exists(sp):
        try:
            os.remove(sp)
        except IOError:
            pass

    # Restart PipeWire
    ok, msg = restart_pipewire()
    if not ok:
        return False, msg

    # Wait for filter chain nodes to appear
    enabled_nodes = [ch["node"] for ch in state["channels"] if ch["enabled"]]
    if enabled_nodes:
        _wait_for_nodes(enabled_nodes)

    # Set default sink
    default_ch = next((ch for ch in state["channels"] if ch.get("is_default")), None)
    if default_ch:
        default_node = default_ch["node"]
    ok, msg = set_default_sink(default_node)
    if not ok:
        return False, f"Restarted but default sink failed: {msg}"

    # Start background re-routing — polls for 12 seconds as apps reconnect
    threading.Thread(target=_reapply_routing_loop, args=(state,), daemon=True).start()

    log.info("Apply complete — background re-routing started")
    return True, "Applied — audio streams will reconnect automatically"
