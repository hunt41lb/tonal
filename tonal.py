#!/usr/bin/env python3
"""Tonal — Audio routing and EQ manager for RODECaster Pro II on Linux."""

import sys
import os
import logging
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tonal")

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, Gdk, Gio, GLib
except (ImportError, ValueError) as e:
    print(f"Missing dependency: {e}")
    print("Install: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1")
    sys.exit(1)

from state import load_state, backup_profile_before_apply
from pages.channels import ChannelsPage
from pages.equalizer import EqualizerPage
from pages.routing import RoutingPage
from pages.status import StatusPage
import pipewire_ctl


class TonalWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Tonal", default_width=900, default_height=680)

        log.info("Loading state...")
        self.state = load_state()
        self.pending_changes = False
        self.restart_needed = False

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(outer)

        hdr = Adw.HeaderBar()
        outer.append(hdr)

        # Global Save & Apply
        self.apply_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.apply_spinner = Gtk.Spinner()
        self.apply_box.append(self.apply_spinner)
        self.apply_label = Gtk.Label(label="Save & Apply")
        self.apply_box.append(self.apply_label)

        self.apply_btn = Gtk.Button(css_classes=["suggested-action"],
                                     tooltip_text="Apply all pending changes")
        self.apply_btn.set_child(self.apply_box)
        self.apply_btn.set_sensitive(False)
        self.apply_btn.connect("clicked", self._on_apply)
        hdr.pack_end(self.apply_btn)

        # Pages
        self.stack = Adw.ViewStack(vexpand=True)

        self.channels_page = ChannelsPage(self.state, self.toast_overlay, self.mark_dirty)
        self.eq_page = EqualizerPage(self.state, self.toast_overlay, self.mark_dirty, self.mark_clean)
        self.routing_page = RoutingPage(self.state, self.toast_overlay, self.mark_dirty)
        self.status_page = StatusPage(self.state, self.toast_overlay)

        self.stack.add_titled_with_icon(self.channels_page, "channels", "Channels", "audio-card-symbolic")
        self.stack.add_titled_with_icon(self.eq_page, "equalizer", "Equalizer", "multimedia-equalizer-symbolic")
        self.stack.add_titled_with_icon(self.routing_page, "routing", "Routing", "network-transmit-receive-symbolic")
        self.stack.add_titled_with_icon(self.status_page, "status", "Status", "emblem-system-symbolic")

        hdr.set_title_widget(Adw.ViewSwitcherTitle(stack=self.stack))
        outer.append(self.stack)

        css = Gtk.CssProvider()
        css.load_from_string("""
            .monospace   { font-family: monospace; font-size: 12px; }
            .heading     { font-weight: bold; font-size: 14px; }
            .caption     { font-size: 11px; }
            .dot-ok      { background: #1D9E75; border-radius: 50%; min-width: 10px; min-height: 10px; }
            .dot-err     { background: #E24B4A; border-radius: 50%; min-width: 10px; min-height: 10px; }
            .eq-band-col { border-right: 1px solid alpha(currentColor, 0.08); padding: 4px 2px; }
            .eq-type-dd  { font-size: 10px; }
            .switch-off:not(:checked) slider { background: #E24B4A; }
            .switch-off:not(:checked) { background: alpha(#E24B4A, 0.3); }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.connect("close-request", self._on_close_request)
        log.info("Window ready")

    def mark_dirty(self, requires_restart=False):
        """Called by pages. EQ changes don't require restart; structural changes do."""
        self.pending_changes = True
        if requires_restart:
            self.restart_needed = True
        self.apply_btn.set_sensitive(True)
        if self.restart_needed:
            self.apply_label.set_text("Save & Apply * (restart)")
        else:
            self.apply_label.set_text("Save & Apply *")

    def mark_clean(self):
        """Called when changes are undone/reverted — resets the dirty state."""
        self.pending_changes = False
        self.restart_needed = False
        self.apply_btn.set_sensitive(False)
        self.apply_label.set_text("Save & Apply")

    def _on_apply(self, btn):
        # If on the Equalizer page, show Save As dialog first
        if self.stack.get_visible_child() == self.eq_page:
            self.eq_page.save_to_state_interactive(self._do_apply_after_save)
        else:
            self._do_apply_after_save()

    def _do_apply_after_save(self):
        backup_profile_before_apply(self.state)

        self.apply_btn.set_sensitive(False)
        self.apply_spinner.start()

        if self.restart_needed:
            self.apply_label.set_text("Restarting PipeWire...")
            log.info("Structural changes detected — full restart")
            threading.Thread(target=self._do_full_apply, daemon=True).start()
        else:
            self.apply_label.set_text("Updating EQ...")
            log.info("EQ-only changes — live update (no restart)")
            threading.Thread(target=self._do_live_eq, daemon=True).start()

    def _do_full_apply(self):
        ok, msg = pipewire_ctl.apply_config(self.state)
        GLib.idle_add(self._apply_done, ok, msg)

    def _do_live_eq(self):
        ok, msg = pipewire_ctl.apply_eq_live(self.state)
        GLib.idle_add(self._apply_done, ok, msg)

    def _apply_done(self, ok, msg):
        self.apply_spinner.stop()
        self.pending_changes = False
        self.restart_needed = False
        self.apply_label.set_text("Save & Apply")
        self.apply_btn.set_sensitive(False)

        toast = Adw.Toast(title=msg, timeout=3)
        if not ok:
            toast.set_title(f"Error: {msg}")
            toast.set_timeout(5)
            self.apply_btn.set_sensitive(True)
            log.error("Apply failed: %s", msg)
        else:
            log.info("Apply succeeded: %s", msg)
        self.toast_overlay.add_toast(toast)

    def _on_close_request(self, window):
        # Stop VU meter monitoring before closing
        if hasattr(self.eq_page, 'vu_meter'):
            self.eq_page.vu_meter.cleanup()

        if self.pending_changes:
            dialog = Adw.AlertDialog(
                heading="Unsaved changes",
                body="You have changes that haven't been applied.",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("discard", "Discard")
            dialog.add_response("save", "Save & Apply")
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("save")
            dialog.set_close_response("cancel")
            dialog.connect("response", self._on_close_response)
            dialog.present(self)
            return True
        return False

    def _on_close_response(self, dialog, response):
        if response == "discard":
            self.pending_changes = False
            self.close()
        elif response == "save":
            self._on_apply(None)
            GLib.timeout_add(3000, self.close)


class TonalApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.tonal.app", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        TonalWindow(self).present()


if __name__ == "__main__":
    log.info("Tonal starting")
    TonalApp().run(sys.argv)