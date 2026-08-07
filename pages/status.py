"""Status page — hardware, audio server, node status, and self-update."""

import logging
import os
import sys
import threading

from gi.repository import Gtk, Adw, GLib

from widgets.helpers import scrollable_wrap
from constants import APP_VERSION
import pipewire_ctl
import updater

log = logging.getLogger("tonal.status")


class StatusPage(Gtk.Box):
    def __init__(self, state, toast_overlay):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        self.toast_overlay = toast_overlay

        # Update-row state machine: "check" → "install" → "restart".
        # One suffix button advances through the states, changing label and
        # action as it goes; errors fall back to the state they came from.
        self._update_state = "check"
        self._update_info = None

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        clamp = Adw.Clamp(maximum_size=700); inner.append(clamp)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16); clamp.set_child(vbox)

        # App version + updates
        vg = Adw.PreferencesGroup(title="Tonal")
        vbox.append(vg)
        vr = Adw.ActionRow(title="Version", subtitle=APP_VERSION)
        vr.add_prefix(Gtk.Image.new_from_icon_name("application-x-executable-symbolic"))
        vg.add(vr)
        vg.add(self._build_update_row())

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
        for n in pipewire_ctl.get_pipewire_nodes(self.state):
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

    # ── Updates row ─────────────────────────────────────────────────────

    def _build_update_row(self):
        self.update_row = Adw.ActionRow(
            title="Updates",
            subtitle="Check GitHub for a newer release",
        )
        self.update_row.add_prefix(
            Gtk.Image.new_from_icon_name("software-update-available-symbolic"))

        self.update_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER, visible=False)
        self.update_row.add_suffix(self.update_spinner)

        self.update_btn = Gtk.Button(label="Check for updates",
                                      valign=Gtk.Align.CENTER)
        self.update_btn.connect("clicked", self._on_update_clicked)
        self.update_row.add_suffix(self.update_btn)
        self.update_row.set_activatable_widget(self.update_btn)
        return self.update_row

    def _set_update_busy(self, subtitle):
        self.update_btn.set_sensitive(False)
        self.update_spinner.set_visible(True)
        self.update_spinner.start()
        self.update_row.set_subtitle(subtitle)

    def _set_update_idle(self):
        self.update_btn.set_sensitive(True)
        self.update_spinner.stop()
        self.update_spinner.set_visible(False)

    def _on_update_clicked(self, btn):
        if self._update_state == "install":
            self._confirm_install()
        elif self._update_state == "restart":
            self._restart_app()
        else:
            self._start_check()

    # ── Check ───────────────────────────────────────────────────────────

    def _start_check(self):
        self._set_update_busy("Checking GitHub…")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self):
        ok, res = updater.check_for_update()
        GLib.idle_add(self._check_done, ok, res)

    def _check_done(self, ok, res):
        self._set_update_idle()
        if not ok:
            self.update_row.set_subtitle("Couldn't check for updates")
            self.toast_overlay.add_toast(Adw.Toast(title=res, timeout=4))
            return False

        self._update_info = res
        v = res["version"]

        if not res["available"]:
            self.update_row.set_subtitle(f"You're up to date — version {res['current']}")
        elif not res["asset_url"]:
            self.update_row.set_subtitle(
                f"Version {v} is available, but no .deb is attached to the release")
        elif not updater.is_installed_copy():
            self.update_row.set_subtitle(
                f"Version {v} available — running from source, update via GitHub")
        else:
            size_mb = res["asset_size"] / (1024 * 1024)
            self.update_row.set_subtitle(f"Version {v} available — {size_mb:.1f} MB download")
            self.update_btn.set_label(f"Install v{v}")
            self.update_btn.add_css_class("suggested-action")
            self._update_state = "install"
        return False

    # ── Download + install ──────────────────────────────────────────────

    def _confirm_install(self):
        info = self._update_info
        dialog = Adw.AlertDialog(
            heading=f"Update to version {info['version']}?",
            body="Tonal will download the package and ask for authorization to install it.",
        )

        notes = info.get("notes", "")
        if notes:
            label = Gtk.Label(label=notes, wrap=True, xalign=0, selectable=True,
                              css_classes=["caption"],
                              margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_max_content_height(220)
            sw.set_propagate_natural_height(True)
            sw.set_child(label)
            dialog.set_extra_child(sw)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Download & Install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")
        dialog.connect("response",
                       lambda d, r: self._start_install() if r == "install" else None)
        dialog.present(self.get_root())

    def _start_install(self):
        self._set_update_busy(f"Downloading version {self._update_info['version']}…")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self):
        info = self._update_info
        ok, res = updater.download_update(info["asset_url"], info["asset_size"])
        if not ok:
            GLib.idle_add(self._install_done, False, res)
            return
        GLib.idle_add(self.update_row.set_subtitle,
                      "Installing — authorization may be required…")
        ok, msg = updater.install_update(res)
        GLib.idle_add(self._install_done, ok, msg)

    def _install_done(self, ok, msg):
        self._set_update_idle()
        info = self._update_info
        if ok:
            self._update_state = "restart"
            self.update_btn.set_label("Restart Tonal")
            self.update_row.set_subtitle(
                f"Updated to version {info['version']} — restart to finish")
            self.toast_overlay.add_toast(
                Adw.Toast(title=f"Updated to version {info['version']}", timeout=3))
            log.info("Update to %s installed", info["version"])
        else:
            # Stay in "install" state so a cancelled authorization can be retried
            self.update_row.set_subtitle(
                f"Version {info['version']} available — install didn't finish")
            self.toast_overlay.add_toast(Adw.Toast(title=msg, timeout=5))
            log.warning("Update install failed: %s", msg)
        return False

    # ── Restart after install ───────────────────────────────────────────

    def _restart_app(self):
        win = self.get_root()
        if getattr(win, "pending_changes", False):
            dialog = Adw.AlertDialog(
                heading="Discard unsaved changes?",
                body="Restarting now will discard changes that haven't been applied.",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("restart", "Restart Anyway")
            dialog.set_response_appearance("restart", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.connect("response",
                           lambda d, r: self._do_restart() if r == "restart" else None)
            dialog.present(win)
            return
        self._do_restart()

    def _do_restart(self):
        """Replace this process with a fresh launch of the installed copy.

        The capture subprocesses (parec) and the GStreamer pipeline are stopped
        first so nothing is orphaned across the exec. os.execv keeps the same
        PID, so GApplication uniqueness never sees two competing instances.
        """
        win = self.get_root()
        eq = getattr(win, "eq_page", None)
        if eq is not None:
            if hasattr(eq, "vu_meter"):
                eq.vu_meter.cleanup()
            if hasattr(eq, "spectrum"):
                eq.spectrum.cleanup()
        log.info("Restarting Tonal after update")

        launcher = "/usr/bin/tonal"
        if os.path.exists(launcher):
            os.execv(launcher, [launcher])
        # Source-tree fallback (not reachable via the update flow, but safe)
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tonal.py")
        os.execv(sys.executable, [sys.executable, script])

    # ── Audio server restart (existing) ─────────────────────────────────

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
