#!/usr/bin/env python3
"""Step 2 — GStreamer spectrum pipeline test (fixed GstValueList parsing).

Prints live frequency band magnitudes to the terminal.
Play audio while this runs to see the spectrum data.

Usage:
    python3 test_spectrum.py

Press Ctrl+C to stop.
"""

import re

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)

BANDS = 31
INTERVAL = 50_000_000  # 50ms in nanoseconds (20fps)

# Build pipeline: pulsesrc → spectrum → fakesink
pipeline = Gst.parse_launch(
    f"pulsesrc ! "
    f"spectrum bands={BANDS} interval={INTERVAL} "
    f"post-messages=true message-magnitude=true threshold=-80 ! "
    f"fakesink"
)

# Frequency labels for 31-band (1/3 octave)
CENTER_FREQS = [
    "20", "25", "31", "40", "50", "63", "80", "100", "125", "160",
    "200", "250", "315", "400", "500", "630", "800", "1k", "1.2k", "1.6k",
    "2k", "2.5k", "3.1k", "4k", "5k", "6.3k", "8k", "10k", "12k", "16k", "20k",
]

# Regex to extract magnitude values from the structure string
# Format: magnitude=(float){ -60.0, -55.2, -48.1, ... }
MAG_PATTERN = re.compile(r"magnitude=\(float\)\{([^}]+)\}")

frame_count = 0


def parse_magnitudes(structure):
    """Extract magnitude values by parsing the structure's string representation.

    The GstValueList type is not accessible through Python GI bindings,
    so we parse the string form instead.
    """
    s = structure.to_string()
    match = MAG_PATTERN.search(s)
    if not match:
        return None

    try:
        values = [float(v.strip()) for v in match.group(1).split(",")]
        return values
    except ValueError:
        return None


def on_message(bus, message):
    """Handle GStreamer bus messages — extract spectrum magnitude data."""
    global frame_count

    if message.type != Gst.MessageType.ELEMENT:
        return True

    structure = message.get_structure()
    if structure is None or structure.get_name() != "spectrum":
        return True

    magnitudes = parse_magnitudes(structure)
    if magnitudes is None:
        print("Warning: could not parse magnitude data")
        return True

    frame_count += 1

    # Print a simple bar visualization every 4th frame (~200ms)
    if frame_count % 4 == 0:
        print(f"\n── Frame {frame_count} ──")
        for i, mag in enumerate(magnitudes[:BANDS]):
            bar_len = max(0, int((mag + 80) / 80 * 40))
            bar = "█" * bar_len
            label = CENTER_FREQS[i] if i < len(CENTER_FREQS) else f"b{i}"
            print(f"  {label:>5s} │ {mag:6.1f} dB │{bar}")


# Connect to the bus
bus = pipeline.get_bus()
bus.add_signal_watch()
bus.connect("message::element", on_message)


# Handle errors
def on_error(bus, message):
    err, debug = message.parse_error()
    print(f"Error: {err.message}")
    print(f"Debug: {debug}")
    loop.quit()

bus.connect("message::error", on_error)

# Start
print(f"Starting spectrum analyzer test ({BANDS} bands, {INTERVAL // 1_000_000}ms interval)")
print("Play audio to see spectrum data. Press Ctrl+C to stop.\n")

pipeline.set_state(Gst.State.PLAYING)

loop = GLib.MainLoop()
try:
    loop.run()
except KeyboardInterrupt:
    print("\nStopping...")
finally:
    pipeline.set_state(Gst.State.NULL)
    print("Done.")