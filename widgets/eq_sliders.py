"""Peace-style vertical EQ sliders widget."""

from gi.repository import Gtk
from eq_math import FILTER_TYPES, FILTER_SHORT


def _block_scroll(_c, _dx, _dy):
    return True

def _add_scroll_block(w):
    c = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
    c.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    c.connect("scroll", _block_scroll)
    w.add_controller(c)


class EqVerticalSliders(Gtk.Box):
    """Vertical sliders — one column per band, fills width and height."""

    def __init__(self, bands, on_change=None, on_delete=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0,
                         hexpand=True, vexpand=True)
        self.bands = bands
        self.on_change = on_change
        self.on_delete = on_delete
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

        freq = band["freq"]
        ftxt = f"{freq / 1000:.1f}k" if freq >= 1000 else f"{freq:.0f}"
        col.append(Gtk.Label(label=ftxt, css_classes=["caption", "dim-label"]))

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -20, 15, 0.5)
        scale.set_inverted(True)
        scale.set_value(band["gain"])
        scale.set_vexpand(True)
        scale.set_draw_value(False)
        scale.add_mark(0, Gtk.PositionType.RIGHT, None)
        _add_scroll_block(scale)
        col.append(scale)

        gl = Gtk.Label(label=f"{band['gain']:+.1f}", css_classes=["monospace", "caption"])
        col.append(gl)

        short_labels = [FILTER_SHORT[t] for t in FILTER_TYPES]
        td = Gtk.DropDown.new_from_strings(short_labels)
        td.set_selected(FILTER_TYPES.index(band["type"]) if band["type"] in FILTER_TYPES else 0)
        td.add_css_class("eq-type-dd")
        td.connect("notify::selected", lambda d, p, b=band: self._type_changed(d, b))
        col.append(td)

        col.append(Gtk.Label(label=f"Q{band['q']:.1f}", css_classes=["caption", "dim-label"]))

        del_btn = Gtk.Button(icon_name="edit-delete-symbolic",
                             css_classes=["flat", "circular"], tooltip_text="Remove band")
        del_btn.connect("clicked", lambda b, i=idx: self._on_delete(i))
        col.append(del_btn)

        scale.connect("value-changed", lambda s, b=band, l=gl: self._gain_changed(s, b, l))
        return col

    def _gain_changed(self, scale, band, label):
        v = round(scale.get_value() * 2) / 2
        band["gain"] = v
        label.set_text(f"{v:+.1f}")
        if self.on_change:
            self.on_change()

    def _type_changed(self, dropdown, band):
        band["type"] = FILTER_TYPES[dropdown.get_selected()]
        if self.on_change:
            self.on_change()

    def _on_delete(self, idx):
        if self.on_delete:
            self.on_delete(idx)

    def _on_add(self, btn):
        self.bands.append({"freq": 1000.0, "gain": 0.0, "q": 1.41, "type": "peak"})
        self._build()
        if self.on_change:
            self.on_change()

    def rebuild(self):
        self._build()
