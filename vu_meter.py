"""Real-time VU meter widget monitoring all PipeWire hardware outputs."""

import struct
import subprocess
import threading
import math
import logging

from gi.repository import Gtk, Gdk, GLib

log = logging.getLogger("tonal.vu_meter")

METER_MIN_DB = -60.0
METER_MAX_DB = 6.0
METER_RANGE = METER_MAX_DB - METER_MIN_DB

FALLOFF_DB_PER_FRAME = 1.2
PEAK_HOLD_FRAMES = 60

# Gradient base color (teal — matches spectrum analyzer)
BASE_R, BASE_G, BASE_B = 0.11, 0.62, 0.46


def _db_from_sample(peak_value):
    if peak_value < 1e-10:
        return METER_MIN_DB
    return 20.0 * math.log10(peak_value)


def _db_to_fraction(db):
    return max(0.0, min(1.0, (db - METER_MIN_DB) / METER_RANGE))


def _find_all_hardware_monitors():
    """Find every RODECaster output monitor so the VU meter reflects all channels.

    Captures the expanded USB-1 sink AND the raw USB-1 Chat / USB-2 outputs, so
    audio on any channel — including USB-2 — registers on the meter. (The metering
    capture was briefly narrowed to only the expanded sink while chasing the
    dropout; that turned out to be an EQ denormal issue, not the meters, so we
    watch all outputs again.)
    """
    monitors = []
    try:
        r = subprocess.run(["pactl", "list", "sinks", "short"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return [("@DEFAULT_MONITOR@", 2)]

        for line in r.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            name = parts[1].strip()
            fmt_str = parts[3].strip()

            channels = 2
            for token in fmt_str.split():
                if token.endswith("ch"):
                    try:
                        channels = int(token[:-2])
                    except ValueError:
                        pass

            if name == "rodecaster_expanded":
                monitors.append((f"{name}.monitor", channels))
            elif ("RODECaster" in name or "R__DECaster" in name) and "alsa_output" in name:
                monitors.append((f"{name}.monitor", channels))

    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if not monitors:
        return [("@DEFAULT_MONITOR@", 2)]

    log.info("VU meter: monitoring %d output(s)", len(monitors))
    return monitors


class _SourceMonitor:
    """Monitors a single PipeWire sink via parec."""

    def __init__(self, monitor_name, channels):
        self.monitor_name = monitor_name
        self.channels = channels
        self.peak_db = METER_MIN_DB
        self.process = None
        self.running = False
        self._lock = threading.Lock()

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False
        if self.process:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            self.process = None

    def get_peak(self):
        with self._lock:
            return self.peak_db

    def _loop(self):
        try:
            self.process = subprocess.Popen(
                ["parec", "--format=s16le", f"--channels={self.channels}",
                 "--rate=48000", f"--device={self.monitor_name}",
                 "--raw", "--latency-msec=15"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.running = False
            return

        chunk_size = int(48000 * self.channels * 2 * 0.015)
        chunk_size = chunk_size - (chunk_size % 2)

        while self.running and self.process and self.process.poll() is None:
            try:
                data = self.process.stdout.read(chunk_size)
                if not data or len(data) < 4:
                    break
                num_samples = len(data) // 2
                samples = struct.unpack(f"<{num_samples}h", data)
                peak = max(abs(s) for s in samples)
                with self._lock:
                    self.peak_db = _db_from_sample(peak / 32768.0)
            except (IOError, ValueError, struct.error):
                break
        self.running = False


class VuMeter(Gtk.DrawingArea):
    """Real-time VU meter monitoring all PipeWire hardware outputs.

    Renders as a single DrawingArea — no side labels.
    Peak info is drawn directly on the canvas.
    """

    def __init__(self, eq_peak_db=0.0):
        super().__init__()

        self.set_hexpand(True)
        self.set_content_height(16)
        self.set_draw_func(self._draw_meter)

        self._display_db = METER_MIN_DB
        self._peak_hold_db = METER_MIN_DB
        self._peak_hold_countdown = 0
        self._eq_peak_db = eq_peak_db
        self._monitors = []
        self._timer_id = None

        # Auto-start
        GLib.idle_add(self._auto_start)

    def _auto_start(self):
        self.start_monitoring()
        return False

    def set_eq_peak(self, db):
        """Update the theoretical EQ peak marker."""
        self._eq_peak_db = db
        self.queue_draw()

    def reset_peak(self):
        """Reset the peak hold — call on profile save/switch/undo."""
        self._peak_hold_db = METER_MIN_DB
        self._peak_hold_countdown = 0
        self.queue_draw()

    def start_monitoring(self):
        if self._monitors:
            return
        for name, channels in _find_all_hardware_monitors():
            sm = _SourceMonitor(name, channels)
            sm.start()
            self._monitors.append(sm)
        self._timer_id = GLib.timeout_add(30, self._refresh_ui)

    def stop_monitoring(self):
        for sm in self._monitors:
            sm.stop()
        self._monitors.clear()
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self._display_db = METER_MIN_DB
        self.queue_draw()

    def cleanup(self):
        self.stop_monitoring()

    # ── UI refresh ──────────────────────────────────────────────────────

    def _refresh_ui(self):
        if not self._monitors:
            return False

        raw_db = max(sm.get_peak() for sm in self._monitors)

        # Smooth: instant rise, gradual fall
        if raw_db >= self._display_db:
            self._display_db = raw_db
        else:
            self._display_db = max(raw_db, self._display_db - FALLOFF_DB_PER_FRAME)

        # Peak hold
        if raw_db > self._peak_hold_db:
            self._peak_hold_db = raw_db
            self._peak_hold_countdown = PEAK_HOLD_FRAMES
        elif self._peak_hold_countdown > 0:
            self._peak_hold_countdown -= 1
        else:
            self._peak_hold_db = max(raw_db, self._peak_hold_db - 0.3)

        self.queue_draw()
        return True

    # ── Drawing ─────────────────────────────────────────────────────────

    def _draw_meter(self, area, cr, width, height):
        if width < 10 or height < 4:
            return

        # Rounded clip region
        radius = 3
        cr.new_sub_path()
        cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
        cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
        cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.clip()

        # Background
        cr.set_source_rgba(0.08, 0.08, 0.08, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        # Level bar with gradient (teal → white based on level)
        level_frac = _db_to_fraction(self._display_db)
        bar_width = level_frac * width

        if bar_width > 1:
            # Draw gradient segments across the bar
            num_segments = max(1, int(bar_width))
            seg_w = bar_width / num_segments
            for i in range(num_segments):
                seg_frac = (i + 0.5) / (width if width > 0 else 1)
                r = BASE_R + (1.0 - BASE_R) * seg_frac
                g = BASE_G + (1.0 - BASE_G) * seg_frac
                b = BASE_B + (1.0 - BASE_B) * seg_frac
                alpha = 0.5 + seg_frac * 0.4
                cr.set_source_rgba(r, g, b, alpha)
                cr.rectangle(i * seg_w, 0, seg_w + 0.5, height)
                cr.fill()

        # 0 dB reference
        zero_x = _db_to_fraction(0.0) * width
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.2)
        cr.set_line_width(1.0)
        cr.move_to(zero_x, 0)
        cr.line_to(zero_x, height)
        cr.stroke()

        # Peak hold marker
        if self._peak_hold_db > METER_MIN_DB + 1:
            peak_x = _db_to_fraction(self._peak_hold_db) * width
            cr.set_source_rgba(0.94, 0.59, 0.59, 0.9)
            cr.set_line_width(2.0)
            cr.move_to(peak_x, 0)
            cr.line_to(peak_x, height)
            cr.stroke()

        # Peak text overlay (top-right, fixed position)
        cr.select_font_face("monospace", 0, 0)
        cr.set_font_size(9)
        if self._peak_hold_db > METER_MIN_DB + 1:
            peak_text = f"Peak: {self._peak_hold_db:+.1f} dB"
        else:
            peak_text = "Peak: —"
        extents = cr.text_extents(peak_text)
        tx = width - extents.width - 6
        ty = height / 2 + extents.height / 2
        # Text shadow for readability
        cr.set_source_rgba(0, 0, 0, 0.6)
        cr.move_to(tx + 0.5, ty + 0.5)
        cr.show_text(peak_text)
        cr.set_source_rgba(1, 1, 1, 0.6)
        cr.move_to(tx, ty)
        cr.show_text(peak_text)

        # Subtle scale ticks
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.1)
        cr.set_line_width(0.5)
        for db in [-48, -36, -24, -12, -6, 0, 3, 6]:
            x = _db_to_fraction(db) * width
            cr.move_to(x, height - 2)
            cr.line_to(x, height)
            cr.stroke()
