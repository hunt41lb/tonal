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

FFT_BANDS = 512          # Linear FFT bins from GStreamer (high resolution)
SAMPLE_RATE = 48000
NYQUIST = SAMPLE_RATE / 2
INTERVAL_NS = 50_000_000  # 50ms → ~20fps
THRESHOLD_DB = -80.0

# 1/3 octave center frequencies (ISO) — what we display
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

# Display range
MIN_DB = -80.0
MAX_DB = 0.0
DB_RANGE = MAX_DB - MIN_DB

# Animation
FALLOFF_DB_PER_FRAME = 1.5
PEAK_HOLD_FRAMES = 50
PEAK_FALL_DB = 0.4

# Label ticks (subset shown on x-axis)
LABEL_INDICES = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30]

# Regex to extract magnitude values from GStreamer structure string
_MAG_PATTERN = re.compile(r"magnitude=\(float\)\{([^}]+)\}")


# ── FFT → 1/3 octave mapping ───────────────────────────────────────────────

def _build_band_mapping():
    """Pre-compute which linear FFT bins map to each 1/3 octave display band.

    GStreamer's spectrum element produces FFT_BANDS linearly-spaced bins
    covering 0 Hz to Nyquist. Each bin i covers:
        freq_low  = i * (Nyquist / FFT_BANDS)
        freq_high = (i + 1) * (Nyquist / FFT_BANDS)

    Each 1/3 octave band has edges:
        lower = center_freq / 2^(1/6)
        upper = center_freq * 2^(1/6)

    We find all FFT bins whose center frequency falls within each octave band.
    """
    hz_per_bin = NYQUIST / FFT_BANDS
    factor = 2 ** (1.0 / 6.0)  # ~1.122 for 1/3 octave
    mapping = []

    for fc in CENTER_FREQS:
        lower = fc / factor
        upper = fc * factor
        bins = []
        for i in range(FFT_BANDS):
            bin_center = (i + 0.5) * hz_per_bin
            if lower <= bin_center <= upper:
                bins.append(i)
        # Ensure at least one bin per band (nearest to center freq)
        if not bins:
            nearest = round(fc / hz_per_bin)
            nearest = max(0, min(FFT_BANDS - 1, nearest))
            bins = [nearest]
        mapping.append(bins)

    return mapping


_BAND_MAP = _build_band_mapping()


def _map_fft_to_octaves(fft_magnitudes):
    """Convert linear FFT bin magnitudes to 1/3 octave band magnitudes.

    Takes the peak (maximum) magnitude from all FFT bins within each
    octave band's frequency range.
    """
    result = []
    for bins in _BAND_MAP:
        peak = MIN_DB
        for b in bins:
            if b < len(fft_magnitudes) and fft_magnitudes[b] > peak:
                peak = fft_magnitudes[b]
        result.append(peak)
    return result


# ── Widget ──────────────────────────────────────────────────────────────────

class SpectrumAnalyzer(Gtk.Box):
    """Real-time spectrum analyzer monitoring PipeWire audio output."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self._fft_magnitudes = [MIN_DB] * FFT_BANDS
        self._display = [MIN_DB] * DISPLAY_BANDS
        self._peaks = [MIN_DB] * DISPLAY_BANDS
        self._peak_age = [0] * DISPLAY_BANDS
        self._pipeline = None
        self._timer_id = None
        self._running = False

        # Header row
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self._title_label = Gtk.Label(
            label="Spectrum", width_chars=7, xalign=1.0,
            css_classes=["dim-label"],
        )
        header.append(self._title_label)

        # Drawing area in a frame — matches VU meter style
        frame = Gtk.Frame(css_classes=["view"], margin_start=10, margin_end=10)
        self._drawing_area = Gtk.DrawingArea()
        self._drawing_area.set_hexpand(True)
        self._drawing_area.set_content_height(120)
        self._drawing_area.set_draw_func(self._draw)
        frame.set_child(self._drawing_area)
        header.append(frame)

        # Status label (right side, matches VU meter peak label position)
        self._status_label = Gtk.Label(
            label="— dBFS", width_chars=14, xalign=1.0,
            css_classes=["monospace", "caption", "dim-label"],
            tooltip_text="Peak frequency band level",
        )
        header.append(self._status_label)

        self.append(header)

        # Auto-start
        GLib.idle_add(self._auto_start)

    def _auto_start(self):
        self.start()
        return False

    # ── Lifecycle ───────────────────────────────────────────────────────

    def start(self):
        """Start the GStreamer pipeline and UI refresh timer."""
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
        """Stop the GStreamer pipeline and UI refresh timer."""
        self._running = False

        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

        # Reset display
        self._fft_magnitudes = [MIN_DB] * FFT_BANDS
        self._display = [MIN_DB] * DISPLAY_BANDS
        self._peaks = [MIN_DB] * DISPLAY_BANDS
        self._peak_age = [0] * DISPLAY_BANDS
        self._status_label.set_text("— dBFS")
        self._drawing_area.queue_draw()
        log.info("Spectrum analyzer stopped")

    def cleanup(self):
        """Clean shutdown — call before window close."""
        self.stop()

    # ── GStreamer callbacks ─────────────────────────────────────────────

    def _on_gst_message(self, bus, message):
        """Extract spectrum magnitudes from GStreamer bus messages."""
        if message.type != Gst.MessageType.ELEMENT:
            return True

        structure = message.get_structure()
        if structure is None or structure.get_name() != "spectrum":
            return True

        # Parse magnitudes from structure string (GstValueList workaround)
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

        # Map linear FFT bins → 1/3 octave display bands
        octave_mags = _map_fft_to_octaves(self._fft_magnitudes)
        peak_band_db = MIN_DB

        for i in range(DISPLAY_BANDS):
            target = octave_mags[i] if i < len(octave_mags) else MIN_DB

            # Smooth: instant rise, gradual fall
            if target > self._display[i]:
                self._display[i] = target
            else:
                self._display[i] = max(target, self._display[i] - FALLOFF_DB_PER_FRAME)

            # Peak hold
            if self._display[i] > self._peaks[i] - 0.5:
                self._peaks[i] = self._display[i]
                self._peak_age[i] = 0
            else:
                self._peak_age[i] += 1
                if self._peak_age[i] > PEAK_HOLD_FRAMES:
                    self._peaks[i] = max(MIN_DB, self._peaks[i] - PEAK_FALL_DB)

            if self._display[i] > peak_band_db:
                peak_band_db = self._display[i]

        # Update status label with highest band level
        if peak_band_db > MIN_DB + 1:
            self._status_label.set_text(f"Peak: {peak_band_db:.1f} dBFS")
        else:
            self._status_label.set_text("— dBFS")

        self._drawing_area.queue_draw()
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

        # Bars
        bar_gap = 1.0
        bar_w = max(1.0, (graph_w / DISPLAY_BANDS) - bar_gap)

        for i in range(DISPLAY_BANDS):
            db = self._display[i]
            frac = max(0.0, (db - MIN_DB) / DB_RANGE)
            bar_h = frac * graph_h
            x = pad_l + (i / DISPLAY_BANDS) * graph_w + bar_gap / 2
            y = pad_t + graph_h - bar_h

            if bar_h > 0.5:
                # Color: teal with alpha based on level
                alpha = 0.35 + frac * 0.55
                if is_dark:
                    cr.set_source_rgba(0.36, 0.79, 0.65, alpha)
                else:
                    cr.set_source_rgba(0.11, 0.62, 0.46, alpha)

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
