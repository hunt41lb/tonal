"""Shared widget helpers — reusable UI utilities for Tonal pages and widgets."""

import os

from gi.repository import Gtk, Adw, Gdk, GdkPixbuf


# ── Scroll Helpers ──────────────────────────────────────────────────────────

def scrollable_wrap(child):
    """Wrap a widget in a vertical-only ScrolledWindow.

    Previously duplicated as _scrollable() in channels.py, routing.py, and status.py.
    """
    sw = Gtk.ScrolledWindow(vexpand=True)
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.set_child(child)
    return sw


def block_scroll(widget):
    """Prevent scroll events from propagating through a widget (e.g. sliders inside scrollable areas).

    Previously duplicated as _add_scroll_block() in equalizer.py and eq_sliders.py.
    """
    controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
    controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    controller.connect("scroll", lambda _c, _dx, _dy: True)
    widget.add_controller(controller)


# ── Icon Loading ────────────────────────────────────────────────────────────

# Resolve icon directory once at import time
_ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                          "data", "icons", "scalable", "actions")


def load_themed_svg_icon(icon_name, size=24):
    """Load an SVG icon recolored to match the current GTK theme (light/dark).

    Args:
        icon_name: SVG filename without path or extension
                   (e.g. "tonal-peak-filter-symbolic").
        size: Pixel size for the rendered icon.

    Returns:
        Gtk.Image widget with the themed icon, or a fallback missing-image icon.

    Previously duplicated as _load_filter_icon() in eq_sliders.py
    and _icon_button() (partially) in equalizer.py.
    """
    path = os.path.join(_ICON_DIR, f"{icon_name}.svg")
    if not os.path.exists(path):
        return Gtk.Image.new_from_icon_name("image-missing-symbolic")

    style_manager = Adw.StyleManager.get_default()
    fg_color = "#ffffff" if style_manager.get_dark() else "#000000"

    with open(path, "r") as f:
        svg_data = f.read()

    # Handle both possible source color formats
    svg_data = svg_data.replace('stroke="currentColor"', f'stroke="{fg_color}"')
    svg_data = svg_data.replace('stroke="#000000"', f'stroke="{fg_color}"')

    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size, size)
    loader.write(svg_data.encode("utf-8"))
    loader.close()

    pixbuf = loader.get_pixbuf()
    texture = Gdk.Texture.new_for_pixbuf(pixbuf)
    image = Gtk.Image.new_from_paintable(texture)
    image.set_pixel_size(size)
    return image


def icon_button(icon_name, tooltip, size=16, css_classes=None):
    """Create a button with a themed SVG icon.

    Args:
        icon_name: SVG filename without path or extension.
        tooltip: Tooltip text for the button.
        size: Icon pixel size (default 16).
        css_classes: List of CSS classes (default ["flat"]).

    Returns:
        Gtk.Button with the themed icon as its child.

    Previously defined as _icon_button() in equalizer.py.
    """
    css = css_classes or ["flat"]
    image = load_themed_svg_icon(icon_name, size=size)
    return Gtk.Button(child=image, tooltip_text=tooltip, css_classes=css)
