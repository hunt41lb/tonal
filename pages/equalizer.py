"""Equalizer page — EQ sliders, profile management. Uses global Save & Apply."""

import copy
import math
import logging

from gi.repository import Gtk, Adw

from eq_math import find_peak, FILTER_TYPES, FILTER_SHORT
from state import get_active_bands, get_active_preamp, save_profile_bands
from widgets.eq_sliders import EqVerticalSliders

log = logging.getLogger("tonal.eq")


def _add_scroll_block(w):
    c = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
    c.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    c.connect("scroll", lambda *a: True)
    w.add_controller(c)


class EqualizerPage(Gtk.Box):
    def __init__(self, state, toast_overlay, mark_dirty):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True,
                        margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.state = state
        self.toast_overlay = toast_overlay
        self.mark_dirty = mark_dirty
        self.bands = [b.copy() for b in get_active_bands(state)]
        self.preamp_db = get_active_preamp(state)

        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, vexpand=True)
        self.append(self.vbox)
        self._build_top_bar()
        self._build_preamp()
        self._build_sliders()

    def _build_top_bar(self):
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        # Profile selector
        pb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        pb.append(Gtk.Label(label="Profile", xalign=0, css_classes=["dim-label"]))
        self._build_profile_dropdown(pb)
        h.append(pb)

        # Profile management buttons
        bb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, valign=Gtk.Align.END)

        new_btn = Gtk.Button(icon_name="document-new-symbolic", tooltip_text="New profile",
                              css_classes=["flat"])
        new_btn.connect("clicked", self._on_new_profile)
        bb.append(new_btn)

        save_btn = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Save profile",
                               css_classes=["flat"])
        save_btn.connect("clicked", self._on_save_profile)
        bb.append(save_btn)

        delete_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete profile",
                                 css_classes=["flat"])
        delete_btn.connect("clicked", self._on_delete_profile)
        bb.append(delete_btn)

        import_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Import .peace",
                                 css_classes=["flat"])
        bb.append(import_btn)

        h.append(bb)

        h.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=8, margin_end=8))

        # EQ toggle
        eq_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.END)
        eq_box.append(Gtk.Label(label="EQ", css_classes=["heading"]))
        self.eq_switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=self.state["eq"]["enabled"])
        self.eq_switch.connect("state-set", self._on_toggle)
        eq_box.append(self.eq_switch)
        h.append(eq_box)

        h.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=8, margin_end=8))

        # Auto-gain
        ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.END)
        self.clip_btn = Gtk.Button(label="Auto-gain", icon_name="dialog-warning-symbolic",
                                    tooltip_text="Set pre-amp to prevent clipping")
        self.clip_btn.connect("clicked", self._on_auto_gain)
        ctrl.append(self.clip_btn)
        h.append(ctrl)

        self.vbox.append(h)

    def _build_profile_dropdown(self, parent):
        """Build or rebuild the profile dropdown. Removes old one if present."""
        if hasattr(self, "profile_dd") and self.profile_dd.get_parent():
            self.profile_dd.get_parent().remove(self.profile_dd)

        profile_names = list(self.state["eq"]["profiles"].keys())
        self.profile_dd = Gtk.DropDown.new_from_strings(profile_names)
        active_idx = profile_names.index(self.state["eq"]["active_profile"]) \
            if self.state["eq"]["active_profile"] in profile_names else 0
        self.profile_dd.set_selected(active_idx)
        self.profile_dd.connect("notify::selected", self._on_profile_changed)
        parent.append(self.profile_dd)

    def _rebuild_profile_dropdown(self):
        """Rebuild the dropdown in place after profiles change."""
        parent = self.profile_dd.get_parent()
        if parent:
            self._build_profile_dropdown(parent)

    def _build_preamp(self):
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        h.append(Gtk.Label(label="Pre Amp", width_chars=7, xalign=1.0, css_classes=["dim-label"]))
        self.pa_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -30, 10, 0.5)
        self.pa_scale.set_value(self.preamp_db)
        self.pa_scale.set_hexpand(True)
        self.pa_scale.set_draw_value(False)
        self.pa_scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
        self.pa_scale.connect("value-changed", self._on_preamp)
        _add_scroll_block(self.pa_scale)
        h.append(self.pa_scale)
        self.pa_label = Gtk.Label(label=f"{self.preamp_db:+.1f}", width_chars=6, xalign=1.0,
                                   css_classes=["monospace"])
        h.append(self.pa_label)
        h.append(Gtk.Label(label="dB", css_classes=["dim-label"]))
        self.vbox.append(h)

    def _build_sliders(self):
        self.slider_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.slider_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.sliders = EqVerticalSliders(self.bands, on_change=self._on_change,
                                          on_delete=self._on_delete_request)
        self.slider_scroll.set_child(self.sliders)
        self.vbox.append(self.slider_scroll)

    # ── Public method for window to call before apply ───────────────────────

    def save_to_state(self):
        """Write current EQ bands and preamp into the state (called by window on Apply)."""
        save_profile_bands(self.state, self.bands, self.preamp_db)

    # ── Profile management ──────────────────────────────────────────────────

    def _on_new_profile(self, btn):
        dialog = Adw.AlertDialog(
            heading="New EQ profile",
            body="Enter a name for the new profile:",
        )

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        entry = Gtk.Entry(placeholder_text="Profile name", hexpand=True)
        entry.set_activates_default(True)
        content.append(entry)

        copy_check = Gtk.CheckButton(label="Copy bands from current profile", active=True)
        content.append(copy_check)

        dialog.set_extra_child(content)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: self._do_new_profile(
            r, entry.get_text(), copy_check.get_active()))
        dialog.present(self.get_root())

    def _do_new_profile(self, response, name, copy_current):
        if response != "create":
            return
        name = name.strip()
        if not name:
            self.toast_overlay.add_toast(Adw.Toast(title="Profile name cannot be empty", timeout=2))
            return
        if name in self.state["eq"]["profiles"]:
            self.toast_overlay.add_toast(Adw.Toast(title=f'Profile "{name}" already exists', timeout=2))
            return

        if copy_current:
            new_profile = {
                "preamp_db": self.preamp_db,
                "bands": copy.deepcopy(self.bands),
            }
        else:
            new_profile = {
                "preamp_db": 0.0,
                "bands": [],
            }

        self.state["eq"]["profiles"][name] = new_profile
        self.state["eq"]["active_profile"] = name

        # Load the new profile into the UI
        self.bands = [b.copy() for b in new_profile["bands"]]
        self.preamp_db = new_profile["preamp_db"]
        self.pa_scale.set_value(self.preamp_db)
        self.pa_label.set_text(f"{self.preamp_db:+.1f}")
        self.sliders.bands = self.bands
        self.sliders.rebuild()

        self._rebuild_profile_dropdown()
        self.mark_dirty()
        log.info("Created new profile '%s' (copied=%s)", name, copy_current)
        self.toast_overlay.add_toast(Adw.Toast(title=f'Created profile "{name}"', timeout=2))

    def _on_save_profile(self, btn):
        """Explicitly save current bands/preamp to the active profile."""
        save_profile_bands(self.state, self.bands, self.preamp_db)
        name = self.state["eq"]["active_profile"]
        log.info("Saved profile '%s'", name)
        self.toast_overlay.add_toast(Adw.Toast(title=f'Profile "{name}" saved', timeout=2))

    def _on_delete_profile(self, btn):
        name = self.state["eq"]["active_profile"]
        profiles = self.state["eq"]["profiles"]

        if len(profiles) <= 1:
            self.toast_overlay.add_toast(
                Adw.Toast(title="Cannot delete the only profile", timeout=2))
            return

        dialog = Adw.AlertDialog(
            heading="Delete profile?",
            body=f'Permanently delete "{name}"? This cannot be undone.',
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: self._do_delete_profile(r, name))
        dialog.present(self.get_root())

    def _do_delete_profile(self, response, name):
        if response != "delete":
            return
        profiles = self.state["eq"]["profiles"]
        if name not in profiles or len(profiles) <= 1:
            return

        del profiles[name]

        # Switch to the first remaining profile
        first_name = next(iter(profiles))
        self.state["eq"]["active_profile"] = first_name
        first_profile = profiles[first_name]

        self.bands = [b.copy() for b in first_profile.get("bands", [])]
        self.preamp_db = first_profile.get("preamp_db", 0.0)
        self.pa_scale.set_value(self.preamp_db)
        self.pa_label.set_text(f"{self.preamp_db:+.1f}")
        self.sliders.bands = self.bands
        self.sliders.rebuild()

        self._rebuild_profile_dropdown()
        self.mark_dirty()
        log.info("Deleted profile '%s', switched to '%s'", name, first_name)
        self.toast_overlay.add_toast(Adw.Toast(title=f'Deleted "{name}"', timeout=2))

    # ── Event handlers ──────────────────────────────────────────────────────

    def _on_change(self):
        self.mark_dirty()
        self._check_clip()

    def _on_toggle(self, sw, state_val):
        self.state["eq"]["enabled"] = state_val
        self.mark_dirty()

    def _on_preamp(self, scale):
        self.preamp_db = round(scale.get_value() * 2) / 2
        self.pa_label.set_text(f"{self.preamp_db:+.1f}")
        self.mark_dirty()
        self._check_clip()

    def _on_profile_changed(self, dropdown, param):
        profiles = list(self.state["eq"]["profiles"].keys())
        idx = dropdown.get_selected()
        if 0 <= idx < len(profiles):
            name = profiles[idx]
            self.state["eq"]["active_profile"] = name
            profile = self.state["eq"]["profiles"][name]
            self.bands = [b.copy() for b in profile.get("bands", [])]
            self.preamp_db = profile.get("preamp_db", 0.0)
            self.pa_scale.set_value(self.preamp_db)
            self.pa_label.set_text(f"{self.preamp_db:+.1f}")
            self.sliders.bands = self.bands
            self.sliders.rebuild()
            self.mark_dirty()

    def _on_delete_request(self, idx):
        if idx < 0 or idx >= len(self.bands):
            return
        band = self.bands[idx]
        freq = band["freq"]
        ftxt = f"{freq / 1000:.1f}k Hz" if freq >= 1000 else f"{freq:.0f} Hz"

        dialog = Adw.AlertDialog(
            heading="Delete band?",
            body=f"Remove the {ftxt} band ({band['gain']:+.1f} dB)?",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r, i=idx: self._confirm_delete(r, i))
        dialog.present(self.get_root())

    def _confirm_delete(self, response, idx):
        if response == "delete" and 0 <= idx < len(self.bands):
            self.bands.pop(idx)
            self.sliders.bands = self.bands
            self.sliders.rebuild()
            self.mark_dirty()
            log.info("Deleted band %d", idx)

    def _on_auto_gain(self, btn):
        peak = find_peak(self.bands, preamp_db=0.0)
        if peak > 0:
            new_pa = -math.ceil(peak * 2) / 2
            self.preamp_db = new_pa
            self.pa_scale.set_value(new_pa)
            self.pa_label.set_text(f"{new_pa:+.1f}")
            self.mark_dirty()
            log.info("Auto-gain: peak %.1f dB → preamp %.1f dB", peak, new_pa)

    def _check_clip(self):
        peak = find_peak(self.bands, preamp_db=self.preamp_db)
        if peak > 0:
            self.clip_btn.add_css_class("destructive-action")
            self.clip_btn.set_tooltip_text(f"Peak: +{peak:.1f} dB — click to fix")
        else:
            self.clip_btn.remove_css_class("destructive-action")
            self.clip_btn.set_tooltip_text("No clipping detected")