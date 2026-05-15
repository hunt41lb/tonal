"""Status page — hardware, audio server, and node status."""

import threading

from gi.repository import Gtk, Adw, GLib
from widgets.helpers import scrollable_wrap
from constants import APP_VERSION
import pipewire_ctl


class StatusPage(Gtk.Box):
    def __init__(self, state, toast_overlay):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        self.toast_overlay = toast_overlay
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        clamp = Adw.Clamp(maximum_size=700); inner.append(clamp)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16); clamp.set_child(vbox)

        # App version
        vg = Adw.PreferencesGroup(title="Tonal")
        vbox.append(vg)
        vr = Adw.ActionRow(title="Version", subtitle=APP_VERSION)
        vr.add_prefix(Gtk.Image.new_from_icon_name("application-x-executable-symbolic"))
        vg.add(vr)

        # Hardware
        usb = pipewire_ctl.check_usb_connected()
        hw = Adw.PreferencesGroup(title="Hardware"); vbox.append(hw)
        self._row(hw, "USB 1 (Primary)", f'ALSA: {state["hardware"]["usb1_alsa_name"]}', usb["usb1"])
        self._row(hw, "USB 2 (Secondary)", "Stereo", usb["usb2"])

        # Audio server
        sw = Adw.PreferencesGroup(title="Audio server"); vbox.append(sw)
        pw_ver = pipewire_ctl.get_pipewire_version()
        self._row(sw, "PipeWire", f"Version {pw_ver}", pipewire_ctl.is_service_running("pipewire"))
        self._row(sw, "WirePlumber", "Session manager", pipewire_ctl.is_service_running("wireplumber"))

        # Nodes
        ng = Adw.PreferencesGroup(title="PipeWire nodes"); vbox.append(ng)
        for n in pipewire_ctl.get_pipewire_nodes():
            self._row(ng, n["name"], n["status"].capitalize(), n["status"] == "running")

        # Actions
        ag = Adw.PreferencesGroup(title="Actions"); vbox.append(ag)

        rr = Adw.ActionRow(title="Restart audio server",
                            subtitle="Restarts PipeWire, PipeWire-Pulse, and WirePlumber")
        rb = Gtk.Button(label="Restart", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        rb.connect("clicked", self._on_restart)
        rr.add_suffix(rb); rr.set_activatable_widget(rb); ag.add(rr)

        er = Adw.ActionRow(title="Export configuration", subtitle="Save all config files as a backup")
        er.add_suffix(Gtk.Button(icon_name="document-save-symbolic", valign=Gtk.Align.CENTER,
                                  css_classes=["flat"])); ag.add(er)

        xr = Adw.ActionRow(title="Reset to defaults", subtitle="Remove all Tonal configuration")
        xr.add_suffix(Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                                  css_classes=["flat"])); ag.add(xr)

        self.append(scrollable_wrap(inner))

    def _on_restart(self, btn):
        btn.set_sensitive(False)
        def do_restart():
            ok, msg = pipewire_ctl.restart_pipewire()
            if ok:
                default = next((ch["node"] for ch in self.state["channels"] if ch.get("is_default")), "system_eq")
                pipewire_ctl.set_default_sink(default)
            GLib.idle_add(lambda: (
                btn.set_sensitive(True),
                self.toast_overlay.add_toast(Adw.Toast(title=msg, timeout=3))
            ))
        threading.Thread(target=do_restart, daemon=True).start()

    def _row(self, group, title, subtitle, ok):
        r = Adw.ActionRow(title=title, subtitle=subtitle)
        dot = Gtk.Box()
        dot.set_size_request(10, 10)
        dot.set_valign(Gtk.Align.CENTER); dot.set_margin_end(8)
        dot.add_css_class("dot-ok" if ok else "dot-err")
        r.add_prefix(dot); group.add(r)
