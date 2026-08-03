"""Equalizer page — EQ sliders, profile management. Uses global Save & Apply."""

import copy
import math
import logging
import os

from gi.repository import Gtk, Adw, Gdk, Gio, GLib

from eq_math import find_peak
from eq_import_export import import_profile, export_apo, export_easyeffects
from state import get_active_bands, get_active_preamp, save_profile_bands
from widgets.helpers import block_scroll, icon_button, load_themed_svg_icon
from widgets.eq_sliders import EqVerticalSliders
from vu_meter import VuMeter
from widgets.spectrum_analyzer import SpectrumAnalyzer

log = logging.getLogger("tonal.eq")

class EqualizerPage(Gtk.Box):
    def __init__(self, state, toast_overlay, mark_dirty, mark_clean=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True,
                        margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.state = state
        self.toast_overlay = toast_overlay
        self.mark_dirty = mark_dirty
        self.mark_clean = mark_clean
        self.bands = [b.copy() for b in get_active_bands(state)]
        self.preamp_db = get_active_preamp(state)

        # Delete button hover CSS — uses Adwaita's @error_color for theme consistency
        css = Gtk.CssProvider()
        css.load_from_string("""
            .profile-delete-btn:hover { color: @error_color; }
            .preamp-entry { font-family: monospace; font-size: 11px; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, vexpand=True)
        self.append(self.vbox)
        self._build_top_bar()
        self._build_metering()
        self._build_sliders()
        self._update_peak_meter()

    # ── Toolbar ─────────────────────────────────────────────────────────────

    def _build_top_bar(self):
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # ── Profile selector ────────────────────────────────────────────
        pb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, hexpand=True)
        self._build_profile_selector(pb)
        h.append(pb)

        # ── New profile ─────────────────────────────────────────────────
        new_btn = Gtk.Button(icon_name="list-add-symbolic",
                             tooltip_text="New profile", css_classes=["flat"])
        new_btn.connect("clicked", self._on_new_profile)
        h.append(new_btn)

        # ── Undo (disabled until changes are made) ──────────────────────
        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic",
                                    tooltip_text="Undo changes", css_classes=["flat"])
        self.undo_btn.set_sensitive(False)
        self.undo_btn.connect("clicked", self._on_undo)
        h.append(self.undo_btn)

        # ── File dropdown (Save / Import / Export) ──────────────────────
        self._build_file_menu(h)

        # ── Separator ───────────────────────────────────────────────────
        h.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ── EQ toggle ───────────────────────────────────────────────────
        h.append(Gtk.Label(label="EQ", css_classes=["dim-label"]))
        self.eq_switch = Gtk.Switch(valign=Gtk.Align.CENTER,
                                     active=self.state["eq"]["enabled"])
        self.eq_switch.connect("state-set", self._on_toggle)
        h.append(self.eq_switch)

        # ── Separator ───────────────────────────────────────────────────
        h.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ── Pre-amp (inline) ────────────────────────────────────────────
        h.append(Gtk.Label(label="Pre-amp", css_classes=["dim-label"]))

        self.pa_entry = Gtk.Entry(
            text=f"{self.preamp_db:.1f}",
            width_chars=5,
            max_width_chars=5,
            xalign=0.5,
            css_classes=["preamp-entry"],
            tooltip_text="Pre-amp gain in dB (-30.0 to +10.0)",
        )
        self.pa_entry.set_hexpand(False)
        self.pa_entry.connect("activate", self._on_preamp_entry_committed)
        pa_focus = Gtk.EventControllerFocus()
        pa_focus.connect("leave", lambda f: self._on_preamp_entry_committed(self.pa_entry))
        self.pa_entry.add_controller(pa_focus)
        h.append(self.pa_entry)

        h.append(Gtk.Label(label="dB", css_classes=["dim-label"]))

        # ── Auto-gain ───────────────────────────────────────────────────
        self.clip_btn = Gtk.Button(icon_name="dialog-warning-symbolic",
                                    tooltip_text="Set pre-amp to prevent clipping",
                                    css_classes=["flat"])
        self.clip_btn.connect("clicked", self._on_auto_gain)
        h.append(self.clip_btn)

        self.vbox.append(h)

    def _build_file_menu(self, parent):
        """Build the File dropdown menu (Save / Import / Export)."""
        self.file_menu_btn = Gtk.MenuButton(
            icon_name="pan-down-symbolic",
            tooltip_text="Save, import, or export profiles",
            css_classes=["flat"],
        )

        popover = Gtk.Popover()
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                               margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)

        # Save profile
        self.save_file_btn = Gtk.Button(css_classes=["flat"])
        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_row.append(load_themed_svg_icon("tonal-profile-save-symbolic", size=16))
        save_row.append(Gtk.Label(label="Save profile", xalign=0, hexpand=True))
        self.save_file_btn.set_child(save_row)
        self.save_file_btn.set_sensitive(False)
        self.save_file_btn.connect("clicked",
                                    lambda b: (popover.popdown(), self._on_save_profile(b)))
        popover_box.append(self.save_file_btn)

        # Import profile
        import_btn = Gtk.Button(css_classes=["flat"])
        import_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        import_row.append(load_themed_svg_icon("tonal-profile-import-symbolic", size=16))
        import_row.append(Gtk.Label(label="Import profile", xalign=0, hexpand=True))
        import_btn.set_child(import_row)
        import_btn.connect("clicked",
                            lambda b: (popover.popdown(), self._on_import_profile(b)))
        popover_box.append(import_btn)

        # Export profile
        export_btn = Gtk.Button(css_classes=["flat"])
        export_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        export_row.append(load_themed_svg_icon("tonal-profile-export-symbolic", size=16))
        export_row.append(Gtk.Label(label="Export profile", xalign=0, hexpand=True))
        export_btn.set_child(export_row)
        export_btn.connect("clicked",
                            lambda b: (popover.popdown(), self._on_export_profile(b)))
        popover_box.append(export_btn)

        popover.set_child(popover_box)
        self.file_menu_btn.set_popover(popover)
        parent.append(self.file_menu_btn)

    def _build_profile_selector(self, parent):
        """Build profile selector as a MenuButton with a popover listing profiles."""
        if hasattr(self, "profile_menu_btn") and self.profile_menu_btn.get_parent():
            self.profile_menu_btn.get_parent().remove(self.profile_menu_btn)

        active = self.state["eq"]["active_profile"]
        self.profile_menu_btn = Gtk.MenuButton(
            tooltip_text="Select or manage profiles",
            hexpand=True,
        )
        profile_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._profile_btn_label = Gtk.Label(label=active, xalign=0, hexpand=True)
        profile_content.append(self._profile_btn_label)
        profile_content.append(Gtk.Image.new_from_icon_name("pan-down-symbolic"))
        self.profile_menu_btn.set_child(profile_content)

        popover = Gtk.Popover()
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                               margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)

        profiles = list(self.state["eq"]["profiles"].keys())
        can_delete = len(profiles) > 1

        for name in profiles:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

            label_btn = Gtk.Button(label=name, hexpand=True, css_classes=["flat"])
            label_btn.set_halign(Gtk.Align.FILL)
            if name == active:
                label_btn.add_css_class("suggested-action")
            label_btn.connect("clicked",
                               lambda b, n=name, p=popover: self._on_profile_selected(n, p))
            row.append(label_btn)

            if can_delete:
                del_btn = icon_button("tonal-profile-delete-symbolic",
                                        f'Delete "{name}"',
                                        css_classes=["flat", "profile-delete-btn"])
                del_btn.connect("clicked",
                                 lambda b, n=name, p=popover: self._on_delete_profile_by_name(n, p))
                row.append(del_btn)

            popover_box.append(row)

        popover.set_child(popover_box)
        self.profile_menu_btn.set_popover(popover)
        parent.append(self.profile_menu_btn)

    def _rebuild_profile_selector(self):
        """Rebuild the profile selector in place after profiles change."""
        parent = self.profile_menu_btn.get_parent()
        if parent:
            self._build_profile_selector(parent)

    # ── Pre-amp helpers ─────────────────────────────────────────────────────

    def _update_preamp_display(self):
        """Set the pre-amp entry text from the current preamp_db value."""
        self.pa_entry.set_text(f"{self.preamp_db:.1f}")

    def _on_preamp_entry_committed(self, entry):
        """Parse and apply the pre-amp entry value."""
        text = entry.get_text().strip().lower().replace("db", "").replace("+", "")
        try:
            v = float(text)
            v = max(-30.0, min(10.0, round(v * 2) / 2))
            self.preamp_db = v
            self.mark_dirty()
            self._check_clip()
            self._update_peak_meter()
        except ValueError:
            pass
        # Always reset display to canonical format
        self._update_preamp_display()

    # ── Dirty / Clean state ─────────────────────────────────────────────────

    def _set_dirty_controls(self, dirty):
        """Enable or disable controls that depend on unsaved changes."""
        self.undo_btn.set_sensitive(dirty)
        self.save_file_btn.set_sensitive(dirty)

    # ── Metering ────────────────────────────────────────────────────────────

    def _build_metering(self):
        meter_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                             css_classes=["metering-card"])
        self.vu_meter = VuMeter(eq_peak_db=find_peak(self.bands, preamp_db=self.preamp_db))
        meter_box.append(self.vu_meter)
        self.spectrum = SpectrumAnalyzer()
        meter_box.append(self.spectrum)
        self.vbox.append(meter_box)

    def _update_peak_meter(self):
        """Update the EQ theoretical peak marker on the VU meter."""
        peak = find_peak(self.bands, preamp_db=self.preamp_db)
        self.vu_meter.set_eq_peak(peak)

    def _build_sliders(self):
        self.slider_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.slider_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.sliders = EqVerticalSliders(self.bands, on_change=self._on_change,
                                          on_delete=self._on_delete_request)
        self.slider_scroll.set_child(self.sliders)
        self.vbox.append(self.slider_scroll)

    # ── Public methods for window to call before apply ──────────────────────

    def save_to_state(self):
        """Write current EQ bands and preamp into the state (direct, no dialog)."""
        save_profile_bands(self.state, self.bands, self.preamp_db)

    def save_to_state_interactive(self, then_callback):
        """Show Save As dialog, then call the callback when done."""
        current_name = self.state["eq"]["active_profile"]

        dialog = Adw.AlertDialog(
            heading="Save & Apply profile as",
            body="Keep the same name to overwrite, or enter a new name to save a copy.",
        )

        entry = Gtk.Entry(text=current_name, hexpand=True)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save & Apply")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: self._do_save_interactive(
            r, entry.get_text(), current_name, then_callback))
        dialog.present(self.get_root())

    def _do_save_interactive(self, response, new_name, original_name, then_callback):
        if response != "save":
            return
        new_name = new_name.strip()
        if not new_name:
            self.toast_overlay.add_toast(Adw.Toast(title="Profile name cannot be empty", timeout=2))
            return

        profile_data = {
            "preamp_db": self.preamp_db,
            "bands": copy.deepcopy(self.bands),
        }
        self.state["eq"]["profiles"][new_name] = profile_data
        self.state["eq"]["active_profile"] = new_name

        if new_name != original_name:
            log.info("Save & Apply as '%s' (original '%s' unchanged)", new_name, original_name)
            self._rebuild_profile_selector()
        else:
            log.info("Save & Apply overwriting '%s'", new_name)

        then_callback()

    # ── Profile selector handlers ───────────────────────────────────────────

    def _on_profile_selected(self, name, popover):
        """User selected a profile from the popover."""
        popover.popdown()
        if name == self.state["eq"]["active_profile"]:
            return

        self.state["eq"]["active_profile"] = name
        profile = self.state["eq"]["profiles"][name]
        self.bands = [b.copy() for b in profile.get("bands", [])]
        self.preamp_db = profile.get("preamp_db", 0.0)
        self._update_preamp_display()
        self.sliders.bands = self.bands
        self.sliders.rebuild()
        self._rebuild_profile_selector()
        self.vu_meter.reset_peak()
        self.mark_dirty()
        self._set_dirty_controls(True)

    def _on_delete_profile_by_name(self, name, popover):
        """Delete a specific profile from the popover."""
        popover.popdown()
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

        was_active = (name == self.state["eq"]["active_profile"])
        del profiles[name]

        if was_active:
            first_name = next(iter(profiles))
            self.state["eq"]["active_profile"] = first_name
            first_profile = profiles[first_name]

            self.bands = [b.copy() for b in first_profile.get("bands", [])]
            self.preamp_db = first_profile.get("preamp_db", 0.0)
            self._update_preamp_display()
            self.sliders.bands = self.bands
            self.sliders.rebuild()

        self._rebuild_profile_selector()
        self.mark_dirty()
        self._set_dirty_controls(True)
        log.info("Deleted profile '%s'", name)
        self.toast_overlay.add_toast(Adw.Toast(title=f'Deleted "{name}"', timeout=2))

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

        self.bands = [b.copy() for b in new_profile["bands"]]
        self.preamp_db = new_profile["preamp_db"]
        self._update_preamp_display()
        self.sliders.bands = self.bands
        self.sliders.rebuild()

        self._rebuild_profile_selector()
        self.mark_dirty()
        self._set_dirty_controls(True)
        log.info("Created new profile '%s' (copied=%s)", name, copy_current)
        self.toast_overlay.add_toast(Adw.Toast(title=f'Created profile "{name}"', timeout=2))

    def _on_save_profile(self, btn):
        """Save As — dialog lets user keep the same name (overwrite) or rename."""
        current_name = self.state["eq"]["active_profile"]

        dialog = Adw.AlertDialog(
            heading="Save profile as",
            body="Keep the same name to overwrite, or enter a new name to save a copy.",
        )

        entry = Gtk.Entry(text=current_name, hexpand=True)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: self._do_save_as(
            r, entry.get_text(), current_name))
        dialog.present(self.get_root())

    def _do_save_as(self, response, new_name, original_name):
        if response != "save":
            return
        new_name = new_name.strip()
        if not new_name:
            self.toast_overlay.add_toast(Adw.Toast(title="Profile name cannot be empty", timeout=2))
            return

        profile_data = {
            "preamp_db": self.preamp_db,
            "bands": copy.deepcopy(self.bands),
        }
        self.state["eq"]["profiles"][new_name] = profile_data
        self.state["eq"]["active_profile"] = new_name

        if new_name == original_name:
            log.info("Overwritten profile '%s'", new_name)
            self.toast_overlay.add_toast(
                Adw.Toast(title=f'Profile "{new_name}" saved', timeout=2))
        else:
            log.info("Saved as new profile '%s' (original '%s' unchanged)", new_name, original_name)
            self.toast_overlay.add_toast(
                Adw.Toast(title=f'Saved as "{new_name}" — original unchanged', timeout=3))

        self._rebuild_profile_selector()
        self.vu_meter.reset_peak()
        self.mark_dirty()
        self._set_dirty_controls(True)

    def _on_undo(self, btn):
        """Revert all unsaved EQ changes back to the last saved state."""
        name = self.state["eq"]["active_profile"]
        profile = self.state["eq"]["profiles"].get(name, {})

        self.bands = [b.copy() for b in profile.get("bands", [])]
        self.preamp_db = profile.get("preamp_db", 0.0)

        self._update_preamp_display()
        self.sliders.bands = self.bands
        self.sliders.rebuild()
        self._update_peak_meter()
        self.vu_meter.reset_peak()
        self._set_dirty_controls(False)

        if self.mark_clean:
            self.mark_clean()

        log.info("Reverted to saved profile '%s'", name)
        self.toast_overlay.add_toast(Adw.Toast(title=f'Changes reverted to "{name}"', timeout=2))

    # ── Import / Export ─────────────────────────────────────────────────────

    def _on_import_profile(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Import EQ Profile")

        filters = Gio.ListStore.new(Gtk.FileFilter)

        f_all = Gtk.FileFilter()
        f_all.set_name("All EQ profiles (*.txt, *.json, *.peace)")
        f_all.add_pattern("*.txt")
        f_all.add_pattern("*.json")
        f_all.add_pattern("*.peace")
        f_all.add_pattern("*.apeq")
        filters.append(f_all)

        f_apo = Gtk.FileFilter()
        f_apo.set_name("EqualizerAPO (*.txt)")
        f_apo.add_pattern("*.txt")
        f_apo.add_pattern("*.apeq")
        filters.append(f_apo)

        f_peace = Gtk.FileFilter()
        f_peace.set_name("Peace (*.peace)")
        f_peace.add_pattern("*.peace")
        filters.append(f_peace)

        f_ee = Gtk.FileFilter()
        f_ee.set_name("EasyEffects (*.json)")
        f_ee.add_pattern("*.json")
        filters.append(f_ee)

        dialog.set_filters(filters)
        dialog.open(self.get_root(), None, self._on_import_file_chosen)

    def _on_import_file_chosen(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return

        path = file.get_path()
        preamp_db, bands, error = import_profile(path)

        if error:
            self.toast_overlay.add_toast(Adw.Toast(title=f"Import failed: {error}", timeout=4))
            return

        basename = os.path.splitext(os.path.basename(path))[0]

        name_dialog = Adw.AlertDialog(
            heading="Import EQ profile",
            body=f"Importing {len(bands)} band{'s' if len(bands) != 1 else ''} "
                 f"with {preamp_db:+.1f} dB preamp.\n\nSave as profile:",
        )

        entry = Gtk.Entry(text=basename, hexpand=True)
        entry.set_activates_default(True)
        name_dialog.set_extra_child(entry)
        name_dialog.add_response("cancel", "Cancel")
        name_dialog.add_response("import", "Import")
        name_dialog.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
        name_dialog.set_default_response("import")
        name_dialog.set_close_response("cancel")
        name_dialog.connect("response", lambda d, r: self._do_import(
            r, entry.get_text(), preamp_db, bands))
        name_dialog.present(self.get_root())

    def _do_import(self, response, name, preamp_db, bands):
        if response != "import":
            return
        name = name.strip()
        if not name:
            self.toast_overlay.add_toast(Adw.Toast(title="Profile name cannot be empty", timeout=2))
            return

        base_name = name
        counter = 1
        while name in self.state["eq"]["profiles"]:
            name = f"{base_name} ({counter})"
            counter += 1

        new_profile = {
            "preamp_db": preamp_db,
            "bands": copy.deepcopy(bands),
        }

        self.state["eq"]["profiles"][name] = new_profile
        self.state["eq"]["active_profile"] = name

        self.bands = [b.copy() for b in bands]
        self.preamp_db = preamp_db
        self._update_preamp_display()
        self.sliders.bands = self.bands
        self.sliders.rebuild()

        self._rebuild_profile_selector()
        self.vu_meter.reset_peak()
        self.mark_dirty()
        self._set_dirty_controls(True)
        log.info("Imported profile '%s': %d bands, preamp %.1f dB", name, len(bands), preamp_db)
        self.toast_overlay.add_toast(
            Adw.Toast(title=f'Imported "{name}" — {len(bands)} bands', timeout=3))

    def _on_export_profile(self, btn):
        if not self.bands:
            self.toast_overlay.add_toast(
                Adw.Toast(title="Nothing to export — add some EQ bands first", timeout=2))
            return

        dialog = Adw.AlertDialog(
            heading="Export EQ profile",
            body=f'Export "{self.state["eq"]["active_profile"]}" as:',
        )

        fmt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        apo_radio = Gtk.CheckButton(label="EqualizerAPO (.txt) — universal, works with AutoEQ/Peace/EasyEffects")
        apo_radio.set_active(True)
        fmt_box.append(apo_radio)

        ee_radio = Gtk.CheckButton(label="EasyEffects (.json) — native EasyEffects format")
        ee_radio.set_group(apo_radio)
        fmt_box.append(ee_radio)

        dialog.set_extra_child(fmt_box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("export", "Export")
        dialog.set_response_appearance("export", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("export")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: self._do_export_choose_file(
            r, "apo" if apo_radio.get_active() else "easyeffects"))
        dialog.present(self.get_root())

    def _do_export_choose_file(self, response, fmt):
        if response != "export":
            return

        profile_name = self.state["eq"]["active_profile"]
        safe_name = profile_name.replace("/", "_").replace(" ", "_")
        ext = ".txt" if fmt == "apo" else ".json"

        dialog = Gtk.FileDialog()
        dialog.set_title("Save EQ Profile")
        dialog.set_initial_name(f"{safe_name}{ext}")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        f = Gtk.FileFilter()
        if fmt == "apo":
            f.set_name("EqualizerAPO (*.txt)")
            f.add_pattern("*.txt")
        else:
            f.set_name("EasyEffects (*.json)")
            f.add_pattern("*.json")
        filters.append(f)
        dialog.set_filters(filters)

        dialog.save(self.get_root(), None,
                     lambda d, result, fm=fmt, pn=profile_name: self._on_export_file_chosen(d, result, fm, pn))

    def _on_export_file_chosen(self, dialog, result, fmt, profile_name):
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return

        path = file.get_path()

        if fmt == "apo":
            ok, error = export_apo(path, self.preamp_db, self.bands, profile_name)
        else:
            ok, error = export_easyeffects(path, self.preamp_db, self.bands, profile_name)

        if ok:
            self.toast_overlay.add_toast(
                Adw.Toast(title=f"Exported to {os.path.basename(path)}", timeout=3))
        else:
            self.toast_overlay.add_toast(
                Adw.Toast(title=f"Export failed: {error}", timeout=4))

    # ── Event handlers ──────────────────────────────────────────────────────

    def _on_change(self):
        self.mark_dirty()
        self._set_dirty_controls(True)
        self._check_clip()
        self._update_peak_meter()

    def _on_toggle(self, sw, state_val):
        self.state["eq"]["enabled"] = state_val
        self.mark_dirty()
        self._set_dirty_controls(True)

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
            self._set_dirty_controls(True)
            log.info("Deleted band %d", idx)

    def _on_auto_gain(self, btn):
        peak = find_peak(self.bands, preamp_db=0.0)
        if peak > 0:
            new_pa = -math.ceil(peak * 2) / 2
            self.preamp_db = new_pa
            self._update_preamp_display()
            self.mark_dirty()
            self._set_dirty_controls(True)
            log.info("Auto-gain: peak %.1f dB → preamp %.1f dB", peak, new_pa)

    def _check_clip(self):
        peak = find_peak(self.bands, preamp_db=self.preamp_db)
        if peak > 0:
            self.clip_btn.add_css_class("destructive-action")
            self.clip_btn.set_tooltip_text(f"Peak: +{peak:.1f} dB — click to fix")
        else:
            self.clip_btn.remove_css_class("destructive-action")
            self.clip_btn.set_tooltip_text("No clipping detected")
