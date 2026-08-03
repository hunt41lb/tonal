"""Peace-style vertical EQ sliders widget with editable parameters and filter icons."""

import os

from gi.repository import Gtk, Adw
from constants import (
    FILTER_TYPES, FILTER_ICON_NAMES, FILTER_FULL_NAMES,
    MIN_BAND_FREQ, MAX_BAND_FREQ,
)
from widgets.helpers import block_scroll, load_themed_svg_icon


# ── Helpers ─────────────────────────────────────────────────────────────────

def _freq_display(freq):
    """Format frequency for display."""
    if freq >= 1000:
        return f"{freq / 1000:.1f}k"
    return f"{freq:.0f}"


def _make_entry(text, width_chars=6, tooltip=None):
    """Create a small numeric entry."""
    entry = Gtk.Entry(
        text=text,
        width_chars=width_chars,
        max_width_chars=width_chars,
        xalign=0.5,
        css_classes=["monospace", "caption"],
    )
    entry.set_hexpand(False)
    if tooltip:
        entry.set_tooltip_text(tooltip)
    return entry


def _parse_freq(text):
    """Parse frequency input — supports '1k' or '1.5k' shorthand and raw Hz."""
    text = text.strip().lower().replace(",", "")
    if text.endswith("k"):
        try:
            return float(text[:-1]) * 1000
        except ValueError:
            return None
    elif text.endswith("hz"):
        text = text[:-2].strip()
    try:
        return float(text)
    except ValueError:
        return None


# ── Widget ──────────────────────────────────────────────────────────────────

class EqVerticalSliders(Gtk.Box):
    """Vertical sliders — one column per band, fills width and height."""

    def __init__(self, bands, on_change=None, on_delete=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0,
                         hexpand=True, vexpand=True)
        self.bands = bands
        self.on_change = on_change
        self.on_delete = on_delete
        self._updating_gain = False
        self._build()

    def _build(self):
        child = self.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt

        for i, band in enumerate(self.bands):
            self.append(self._col(i, band))

        add_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                          valign=Gtk.Align.CENTER, margin_start=8, margin_end=8)
        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add band",
                             css_classes=["circular"])
        add_btn.connect("clicked", self._on_add)
        add_col.append(add_btn)
        self.append(add_col)

    def _col(self, idx, band):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      hexpand=True, vexpand=True, css_classes=["eq-band-col"])

        # ── Frequency input ─────────────────────────────────────────────
        freq_entry = _make_entry(
            _freq_display(band["freq"]),
            width_chars=6,
            tooltip="Frequency (20–20000 Hz). Supports '1k' shorthand.",
        )
        freq_entry.connect("activate", lambda e, b=band: self._freq_committed(e, b))
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda f, e=freq_entry, b=band: self._freq_committed(e, b))
        freq_entry.add_controller(focus)
        col.append(freq_entry)

        # ── Gain slider ─────────────────────────────────────────────────
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -20, 15, 0.5)
        scale.set_inverted(True)
        scale.set_value(band["gain"])
        scale.set_vexpand(True)
        scale.set_draw_value(False)
        scale.add_mark(0, Gtk.PositionType.RIGHT, None)
        block_scroll(scale)
        col.append(scale)

        # ── Gain input ──────────────────────────────────────────────────
        gain_entry = _make_entry(
            f"{band['gain']:+.1f}",
            width_chars=6,
            tooltip="Gain in dB (-20.0 to +15.0)",
        )
        col.append(gain_entry)

        # Bidirectional sync: slider ↔ entry
        scale.connect("value-changed",
                       lambda s, b=band, e=gain_entry: self._gain_slider_changed(s, b, e))
        gain_entry.connect("activate",
                            lambda e, b=band, s=scale: self._gain_entry_committed(e, b, s))
        gain_focus = Gtk.EventControllerFocus()
        gain_focus.connect("leave",
                            lambda f, e=gain_entry, b=band, s=scale: self._gain_entry_committed(e, b, s))
        gain_entry.add_controller(gain_focus)

        # ── Filter type selector (icon button + popover) ────────────────
        filter_btn = self._build_filter_selector(idx, band)
        col.append(filter_btn)

        # ── Q input ─────────────────────────────────────────────────────
        q_entry = _make_entry(
            f"{band['q']:.2f}",
            width_chars=6,
            tooltip="Q factor (0.10–30.00)",
        )
        q_entry.connect("activate", lambda e, b=band: self._q_committed(e, b))
        q_focus = Gtk.EventControllerFocus()
        q_focus.connect("leave", lambda f, e=q_entry, b=band: self._q_committed(e, b))
        q_entry.add_controller(q_focus)
        col.append(q_entry)

        # ── Delete button ───────────────────────────────────────────────
        del_btn = Gtk.Button(icon_name="edit-delete-symbolic",
                             css_classes=["flat", "circular"], tooltip_text="Remove band")
        del_btn.connect("clicked", lambda b, i=idx: self._on_delete(i))
        col.append(del_btn)

        return col

    def _build_filter_selector(self, idx, band):
        """Build a MenuButton showing the current filter icon, with a popover for selection."""
        current_type = band["type"]
        full_name =  FILTER_FULL_NAMES.get(current_type, "Peak Filter")

        # Button shows the current filter's icon
        icon = load_themed_svg_icon(FILTER_ICON_NAMES.get(current_type, "tonal-peak-filter-symbolic"), size=24)
        menu_btn = Gtk.MenuButton(
            child=icon,
            css_classes=["flat"],
            tooltip_text=full_name,
        )

        # Popover with all filter types
        popover = Gtk.Popover()
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                               margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)

        for ftype in FILTER_TYPES:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            row_icon = load_themed_svg_icon(FILTER_ICON_NAMES.get(ftype, "tonal-peak-filter-symbolic"), size=14)
            row.append(row_icon)

            label = Gtk.Label(label= FILTER_FULL_NAMES.get(ftype, ftype),
                               xalign=0, hexpand=True)
            row.append(label)

            row_btn = Gtk.Button(child=row, css_classes=["flat"])
            if ftype == current_type:
                row_btn.add_css_class("suggested-action")
            row_btn.connect("clicked",
                             lambda b, ft=ftype, bd=band, mb=menu_btn, p=popover, i=idx:
                             self._on_filter_selected(ft, bd, mb, p, i))
            popover_box.append(row_btn)

        popover.set_child(popover_box)
        menu_btn.set_popover(popover)
        return menu_btn

    # ── Filter type selection ───────────────────────────────────────────

    def _on_filter_selected(self, filter_type, band, menu_btn, popover, idx):
        popover.popdown()
        band["type"] = filter_type

        # Update the button icon to reflect the new selection
        new_icon = load_themed_svg_icon(FILTER_ICON_NAMES.get(filter_type, "tonal-peak-filter-symbolic"), size=24)
        menu_btn.set_child(new_icon)
        menu_btn.set_tooltip_text( FILTER_FULL_NAMES.get(filter_type, filter_type))

        self._notify_change()

    # ── Frequency ───────────────────────────────────────────────────────

    def _freq_committed(self, entry, band):
        parsed = _parse_freq(entry.get_text())
        if parsed is not None:
            parsed = max(MIN_BAND_FREQ, min(MAX_BAND_FREQ, parsed))
            band["freq"] = round(parsed, 1)
            self._notify_change()
        # Always reset display to canonical format
        entry.set_text(_freq_display(band["freq"]))

    # ── Gain (slider → entry) ───────────────────────────────────────────

    def _gain_slider_changed(self, scale, band, gain_entry):
        if self._updating_gain:
            return
        v = round(scale.get_value() * 2) / 2
        band["gain"] = v
        gain_entry.set_text(f"{v:+.1f}")
        self._notify_change()

    # ── Gain (entry → slider) ───────────────────────────────────────────

    def _gain_entry_committed(self, entry, band, scale):
        text = entry.get_text().strip().replace("+", "")
        try:
            v = float(text)
            v = max(-20.0, min(15.0, round(v * 2) / 2))
            band["gain"] = v
            # Guard flag prevents the slider's value-changed from double-firing
            self._updating_gain = True
            scale.set_value(v)
            self._updating_gain = False
            self._notify_change()
        except ValueError:
            pass
        # Always reset display to canonical format
        entry.set_text(f"{band['gain']:+.1f}")

    # ── Q factor ────────────────────────────────────────────────────────

    def _q_committed(self, entry, band):
        text = entry.get_text().strip().lower().replace("q", "")
        try:
            v = float(text)
            v = max(0.10, min(30.0, round(v, 2)))
            band["q"] = v
            self._notify_change()
        except ValueError:
            pass
        entry.set_text(f"{band['q']:.2f}")

    # ── Add / Delete ────────────────────────────────────────────────────

    def _on_delete(self, idx):
        if self.on_delete:
            self.on_delete(idx)

    def _on_add(self, btn):
        self.bands.append({"freq": 1000.0, "gain": 0.0, "q": 1.41, "type": "peak"})
        self._build()
        self._notify_change()

    def _notify_change(self):
        if self.on_change:
            self.on_change()

    def rebuild(self):
        self._build()
