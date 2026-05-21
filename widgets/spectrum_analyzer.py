"""Real-time spectrum analyzer widget using GStreamer's spectrum element."""

import re
import math
import logging

from gi.repository import Gtk, Adw, GLib

log = logging.getLogger("tonal.spectrum")

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    GST_AVAILABLE = True
except (ImportError, ValueError):
    GST_AVAILABLE = False
    log.warning("GStreamer not available — spectrum analyzer disabled")


# ── Constants ───────────────────────────────────────────────────────────────

FFT_BANDS = 512
SAMPLE_RATE = 48000
NYQUIST = SAMPLE_RATE / 2
INTERVAL_NS = 50_000_000
THRESHOLD_DB = -80.0

DISPLAY_BANDS = 31
CENTER_FREQS = [
    20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500,
    630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000,
    10000, 12500, 16000, 20000,
]

FREQ_LABELS = [
    "20", "25", "31", "40", "50", "63", "80", "100", "125", "160",
    "200", "250", "315", "400", "500", "630", "800", "1k", "1.2k", "1.6k",
    "2k", "2.5k", "3.1k", "4k", "5k", "6.3k", "8k", "10k", "12k", "16k", "20k",
]

MIN_DB = -80.0
MAX_DB = 0.0
DB_RANGE = MAX_DB - MIN_DB

FALLOFF_DB_PER_FRAME = 1.5
PEAK_HOLD_FRAMES = 50
PEAK_FALL_DB = 0.4

LABEL_INDICES = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30]

_MAG_PATTERN = re.compile(r"magnitude=\(float\)\{([^}]+)\}")

# Gradient base color (teal)
BASE_R, BASE_G, BASE_B = 0.36, 0.79, 0.65


# ── FFT → 1/3 octave mapping ───────────────────────────────────────────────

def _build_band_mapping():
    hz_per_bin = NYQUIST / FFT_BANDS
    factor = 2 ** (1.0 / 6.0)
    mapping = []
    for fc in CENTER_FREQS:
        lower = fc / factor
        upper = fc * factor
        bins = []
        for i in range(FFT_BANDS):
            bin_center = (i + 0.5) * hz_per_bin
            if lower <= bin_center <= upper:
                bins.append(i)
        if not bins:
            nearest = round(fc / hz_per_bin)
            nearest = max(0, min(FFT_BANDS - 1, nearest))
            bins = [nearest]
        mapping.append(bins)
    return mapping


_BAND_MAP = _build_band_mapping()


def _map_fft_to_octaves(fft_magnitudes):
    result = []
    for bins in _BAND_MAP:
        peak = MIN_DB
        for b in bins:
            if b < len(fft_magnitudes) and fft_magnitudes[b] > peak:
                peak = fft_magnitudes[b]
        result.append(peak)
    return result


# ── Widget ──────────────────────────────────────────────────────────────────

class SpectrumAnalyzer(Gtk.DrawingArea):
    """Real-time spectrum analyzer monitoring PipeWire audio output.

    Renders as a single DrawingArea — no side labels.
    Peak info is drawn directly on the canvas.
    """

    def __init__(self):
        super().__init__()

        self.set_hexpand(True)
        self.set_content_height(120)
        self.set_draw_func(self._draw)

        self._fft_magnitudes = [MIN_DB] * FFT_BANDS
        self._display = [MIN_DB] * DISPLAY_BANDS
        self._peaks = [MIN_DB] * DISPLAY_BANDS
        self._peak_age = [0] * DISPLAY_BANDS
        self._peak_band_db = MIN_DB
        self._pipeline = None
        self._timer_id = None
        self._running = False

        # Auto-start
        GLib.idle_add(self._auto_start)

    def _auto_start(self):
        self.start()
        return False

    # ── Lifecycle ───────────────────────────────────────────────────────

    def start(self):
        if self._running or not GST_AVAILABLE:
            return
        try:
            self._pipeline = Gst.parse_launch(
                f"pulsesrc ! "
                f"spectrum bands={FFT_BANDS} interval={INTERVAL_NS} "
                f"post-messages=true message-magnitude=true "
                f"threshold={int(THRESHOLD_DB)} ! "
                f"fakesink"
            )
            bus = self._pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::element", self._on_gst_message)
            bus.connect("message::error", self._on_gst_error)
            self._pipeline.set_state(Gst.State.PLAYING)
            self._running = True
            self._timer_id = GLib.timeout_add(50, self._refresh_ui)
            log.info("Spectrum analyzer started (%d FFT bins → %d display bands)",
                     FFT_BANDS, DISPLAY_BANDS)
        except Exception as e:
            log.error("Failed to start spectrum analyzer: %s", e)
            self._running = False

    def stop(self):
        self._running = False
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._fft_magnitudes = [MIN_DB] * FFT_BANDS
        self._display = [MIN_DB] * DISPLAY_BANDS
        self._peaks = [MIN_DB] * DISPLAY_BANDS
        self._peak_age = [0] * DISPLAY_BANDS
        self._peak_band_db = MIN_DB
        self.queue_draw()
        log.info("Spectrum analyzer stopped")

    def cleanup(self):
        self.stop()

    # ── GStreamer callbacks ─────────────────────────────────────────────

    def _on_gst_message(self, bus, message):
        if message.type != Gst.MessageType.ELEMENT:
            return True
        structure = message.get_structure()
        if structure is None or structure.get_name() != "spectrum":
            return True
        s = structure.to_string()
        match = _MAG_PATTERN.search(s)
        if match:
            try:
                values = [float(v.strip()) for v in match.group(1).split(",")]
                self._fft_magnitudes = values[:FFT_BANDS]
            except ValueError:
                pass
        return True

    def _on_gst_error(self, bus, message):
        err, debug = message.parse_error()
        log.error("GStreamer error: %s (%s)", err.message, debug)
        GLib.idle_add(self.stop)

    # ── UI refresh ──────────────────────────────────────────────────────

    def _refresh_ui(self):
        if not self._running:
            return False
        octave_mags = _map_fft_to_octaves(self._fft_magnitudes)
        peak_band_db = MIN_DB
        for i in range(DISPLAY_BANDS):
            target = octave_mags[i] if i < len(octave_mags) else MIN_DB
            if target > self._display[i]:
                self._display[i] = target
            else:
                self._display[i] = max(target, self._display[i] - FALLOFF_DB_PER_FRAME)
            if self._display[i] > self._peaks[i] - 0.5:
                self._peaks[i] = self._display[i]
                self._peak_age[i] = 0
            else:
                self._peak_age[i] += 1
                if self._peak_age[i] > PEAK_HOLD_FRAMES:
                    self._peaks[i] = max(MIN_DB, self._peaks[i] - PEAK_FALL_DB)
            if self._display[i] > peak_band_db:
                peak_band_db = self._display[i]
        self._peak_band_db = peak_band_db
        self.queue_draw()
        return True

    # ── Cairo drawing ───────────────────────────────────────────────────

    def _draw(self, area, cr, width, height):
        if width < 10 or height < 10:
            return

        style_manager = Adw.StyleManager.get_default()
        is_dark = style_manager.get_dark()

        pad_l = 28
        pad_r = 4
        pad_t = 4
        pad_b = 16
        graph_w = width - pad_l - pad_r
        graph_h = height - pad_t - pad_b

        if graph_w < 10 or graph_h < 10:
            return

        # Background
        cr.set_source_rgba(0.08, 0.08, 0.08, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        # Grid lines (horizontal — dB)
        for db in range(int(MIN_DB), int(MAX_DB) + 1, 10):
            y = pad_t + (1.0 - (db - MIN_DB) / DB_RANGE) * graph_h
            if db == 0:
                cr.set_source_rgba(1, 1, 1, 0.12)
                cr.set_line_width(1.0)
            else:
                cr.set_source_rgba(1, 1, 1, 0.05)
                cr.set_line_width(0.5)
            cr.move_to(pad_l, y)
            cr.line_to(width - pad_r, y)
            cr.stroke()

            # dB labels
            cr.set_source_rgba(1, 1, 1, 0.3)
            cr.select_font_face("monospace", 0, 0)
            cr.set_font_size(9)
            label = str(db)
            extents = cr.text_extents(label)
            cr.move_to(pad_l - extents.width - 3, y + extents.height / 2)
            cr.show_text(label)

        # Frequency labels (x-axis)
        cr.set_source_rgba(1, 1, 1, 0.3)
        cr.set_font_size(8)
        for idx in LABEL_INDICES:
            if idx >= DISPLAY_BANDS:
                break
            x = pad_l + (idx + 0.5) / DISPLAY_BANDS * graph_w
            label = FREQ_LABELS[idx] if idx < len(FREQ_LABELS) else ""
            extents = cr.text_extents(label)
            lx = x - extents.width / 2
            if lx > pad_l - 4 and lx + extents.width < width - pad_r + 4:
                cr.move_to(lx, height - 2)
                cr.show_text(label)

        # Bars with gradient
        bar_gap = 1.0
        bar_w = max(1.0, (graph_w / DISPLAY_BANDS) - bar_gap)

        for i in range(DISPLAY_BANDS):
            db = self._display[i]
            frac = max(0.0, (db - MIN_DB) / DB_RANGE)
            bar_h = frac * graph_h
            x = pad_l + (i / DISPLAY_BANDS) * graph_w + bar_gap / 2
            y = pad_t + graph_h - bar_h

            if bar_h > 0.5:
                # Gradient: base color → white based on level
                r = BASE_R + (1.0 - BASE_R) * frac
                g = BASE_G + (1.0 - BASE_G) * frac
                b = BASE_B + (1.0 - BASE_B) * frac
                alpha = 0.4 + frac * 0.5
                cr.set_source_rgba(r, g, b, alpha)

                # Rounded top corners
                radius = min(2, bar_w / 2)
                cr.new_sub_path()
                cr.arc(x + bar_w - radius, y + radius, radius, -math.pi / 2, 0)
                cr.line_to(x + bar_w, pad_t + graph_h)
                cr.line_to(x, pad_t + graph_h)
                cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
                cr.close_path()
                cr.fill()

            # Peak hold marker
            peak_db = self._peaks[i]
            if peak_db > MIN_DB + 1:
                peak_frac = max(0.0, (peak_db - MIN_DB) / DB_RANGE)
                peak_y = pad_t + graph_h - peak_frac * graph_h
                cr.set_source_rgba(0.94, 0.59, 0.59, 0.85)
                cr.rectangle(x, peak_y - 1, bar_w, 2)
                cr.fill()

        # Peak text overlay (top-right, fixed position)
        cr.select_font_face("monospace", 0, 0)
        cr.set_font_size(9)
        if self._peak_band_db > MIN_DB + 1:
            peak_text = f"Peak: {self._peak_band_db:.1f} dBFS"
        else:
            peak_text = "Peak: — dBFS"
        extents = cr.text_extents(peak_text)
        tx = width - extents.width - 8
        ty = pad_t + extents.height + 2
        # Text shadow for readability
        cr.set_source_rgba(0, 0, 0, 0.6)
        cr.move_to(tx + 0.5, ty + 0.5)
        cr.show_text(peak_text)
        cr.set_source_rgba(1, 1, 1, 0.5)
        cr.move_to(tx, ty)
        cr.show_text(peak_text)