"""Routing page — app routing rules, active streams, and app discovery."""

import logging

from gi.repository import Gtk, Adw
from widgets.helpers import scrollable_wrap
import pipewire_ctl

log = logging.getLogger("tonal.routing")


class RoutingPage(Gtk.Box):
    def __init__(self, state, toast_overlay, mark_dirty):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        self.toast_overlay = toast_overlay
        self.mark_dirty = mark_dirty
        self._build()

    def _build(self):
        child = self.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        clamp = Adw.Clamp(maximum_size=700); inner.append(clamp)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16); clamp.set_child(vbox)

        # ── Active streams (live) ───────────────────────────────────────
        ag = Adw.PreferencesGroup(title="Active streams",
            description="Currently playing audio and where it's routed.")
        vbox.append(ag)

        streams = pipewire_ctl.get_active_streams()
        sinks = pipewire_ctl.get_sink_list()
        sink_names = {s["id"]: s["name"] for s in sinks}

        if streams:
            for s in streams:
                sink_label = sink_names.get(s["sink_id"], s["sink_id"])
                r = Adw.ActionRow(title=s["app"],
                                   subtitle=f'Stream #{s["stream_id"]}  →  {sink_label}')
                r.add_prefix(Gtk.Image.new_from_icon_name("audio-volume-high-symbolic"))
                ag.add(r)
        else:
            ag.add(Adw.ActionRow(title="No active streams",
                                  subtitle="Play audio in an application to see it here"))

        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh", icon_name="view-refresh-symbolic",
                                  halign=Gtk.Align.START, css_classes=["pill"])
        refresh_btn.connect("clicked", lambda b: self._build())
        vbox.append(refresh_btn)

        # ── Routing rules ───────────────────────────────────────────────
        rg = Adw.PreferencesGroup(title="Routing rules",
            description="Persistent rules applied when a stream starts.")
        vbox.append(rg)

        rules = self.state["routing"]["rules"]
        ch_names = [ch["name"] for ch in self.state["channels"]]

        if rules:
            for i, rule in enumerate(rules):
                r = Adw.ActionRow(title=rule["app"],
                                   subtitle=f'Binary: {rule.get("binary") or "—"}')
                r.add_prefix(Gtk.Image.new_from_icon_name("application-x-executable-symbolic"))

                # Channel dropdown
                dd = Gtk.DropDown.new_from_strings(ch_names)
                if rule["channel"] in ch_names:
                    dd.set_selected(ch_names.index(rule["channel"]))
                dd.set_valign(Gtk.Align.CENTER)
                dd.connect("notify::selected",
                            lambda d, p, idx=i: self._on_channel_changed(d, idx))
                r.add_suffix(dd)

                # Delete button
                del_btn = Gtk.Button(icon_name="edit-delete-symbolic",
                                      valign=Gtk.Align.CENTER, css_classes=["flat"],
                                      tooltip_text="Remove rule")
                del_btn.connect("clicked", lambda b, idx=i: self._on_delete_rule(idx))
                r.add_suffix(del_btn)

                rg.add(r)
        else:
            rg.add(Adw.ActionRow(title="No routing rules",
                                  subtitle="Add rules to control where apps send audio"))

        # Add rule button
        add_btn = Gtk.Button(label="Add rule", icon_name="list-add-symbolic",
                              halign=Gtk.Align.START, css_classes=["pill"])
        add_btn.connect("clicked", self._on_add_rule)
        vbox.append(add_btn)

        # ── Discover apps ───────────────────────────────────────────────
        dg = Adw.PreferencesGroup(title="Discover apps"); vbox.append(dg)
        dr = Adw.ActionRow(title="Scan for running audio apps",
                            subtitle="Find apps playing audio and create routing rules")
        scan_btn = Gtk.Button(icon_name="system-search-symbolic",
                               valign=Gtk.Align.CENTER, css_classes=["flat"],
                               tooltip_text="Scan now")
        scan_btn.connect("clicked", self._on_discover)
        dr.add_suffix(scan_btn)
        dr.set_activatable_widget(scan_btn)
        dg.add(dr)

        self.append(scrollable_wrap(inner))

    # ── Channel dropdown changed ────────────────────────────────────────

    def _on_channel_changed(self, dropdown, rule_idx):
        ch_names = [ch["name"] for ch in self.state["channels"]]
        selected = dropdown.get_selected()
        if 0 <= selected < len(ch_names) and rule_idx < len(self.state["routing"]["rules"]):
            old = self.state["routing"]["rules"][rule_idx]["channel"]
            new = ch_names[selected]
            if old != new:
                self.state["routing"]["rules"][rule_idx]["channel"] = new
                app = self.state["routing"]["rules"][rule_idx]["app"]
                self.mark_dirty(requires_restart=True)
                log.info("Rule '%s' → channel '%s'", app, new)

    # ── Delete rule ─────────────────────────────────────────────────────

    def _on_delete_rule(self, idx):
        rules = self.state["routing"]["rules"]
        if idx < 0 or idx >= len(rules):
            return
        rule = rules[idx]

        dialog = Adw.AlertDialog(
            heading="Delete routing rule?",
            body=f'Remove the rule for "{rule["app"]}"?',
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r, i=idx: self._do_delete_rule(r, i))
        dialog.present(self.get_root())

    def _do_delete_rule(self, response, idx):
        if response != "delete":
            return
        rules = self.state["routing"]["rules"]
        if 0 <= idx < len(rules):
            removed = rules.pop(idx)
            self.mark_dirty(requires_restart=True)
            log.info("Deleted rule for '%s'", removed["app"])
            self.toast_overlay.add_toast(
                Adw.Toast(title=f'Removed rule for "{removed["app"]}"', timeout=2))
            self._build()

    # ── Add rule dialog ─────────────────────────────────────────────────

    def _on_add_rule(self, btn):
        dialog = Adw.AlertDialog(
            heading="Add routing rule",
            body="Route an application's audio to a specific channel.",
        )

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # App name
        app_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        app_row.append(Gtk.Label(label="Application name", xalign=0, css_classes=["dim-label"]))
        app_entry = Gtk.Entry(placeholder_text="e.g. Firefox, Spotify, Discord",
                               hexpand=True)
        app_entry.set_activates_default(True)
        app_row.append(app_entry)
        content.append(app_row)

        # Binary (optional)
        bin_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bin_row.append(Gtk.Label(label="Binary name (optional — more reliable matching)",
                                  xalign=0, css_classes=["dim-label"]))
        bin_entry = Gtk.Entry(placeholder_text="e.g. firefox, spotify, Discord",
                               hexpand=True)
        bin_row.append(bin_entry)
        content.append(bin_row)

        # Channel dropdown
        ch_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ch_row.append(Gtk.Label(label="Route to channel", xalign=0, css_classes=["dim-label"]))
        ch_names = [ch["name"] for ch in self.state["channels"]]
        ch_dd = Gtk.DropDown.new_from_strings(ch_names)
        ch_dd.set_selected(0)
        ch_row.append(ch_dd)
        content.append(ch_row)

        dialog.set_extra_child(content)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add Rule")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: self._do_add_rule(
            r, app_entry.get_text(), bin_entry.get_text(), ch_dd.get_selected()))
        dialog.present(self.get_root())

    def _do_add_rule(self, response, app_name, binary, ch_idx):
        if response != "add":
            return
        app_name = app_name.strip()
        binary = binary.strip()
        if not app_name:
            self.toast_overlay.add_toast(
                Adw.Toast(title="Application name is required", timeout=2))
            return

        # Check for duplicate
        for rule in self.state["routing"]["rules"]:
            if rule["app"].lower() == app_name.lower():
                self.toast_overlay.add_toast(
                    Adw.Toast(title=f'Rule for "{app_name}" already exists', timeout=2))
                return

        ch_names = [ch["name"] for ch in self.state["channels"]]
        channel = ch_names[ch_idx] if 0 <= ch_idx < len(ch_names) else ch_names[0]

        rule = {"app": app_name, "channel": channel}
        if binary:
            rule["binary"] = binary

        self.state["routing"]["rules"].append(rule)
        self.mark_dirty(requires_restart=True)
        log.info("Added rule: '%s' → '%s' (binary: %s)", app_name, channel, binary or "—")
        self.toast_overlay.add_toast(
            Adw.Toast(title=f'Added: {app_name} → {channel}', timeout=2))
        self._build()

    # ── Discover running audio apps ─────────────────────────────────────

    def _on_discover(self, btn):
        streams = pipewire_ctl.get_active_streams()
        if not streams:
            self.toast_overlay.add_toast(
                Adw.Toast(title="No audio streams found — play something first", timeout=3))
            return

        # Filter out apps that already have rules
        existing = {r["app"].lower() for r in self.state["routing"]["rules"]}
        new_apps = []
        seen = set()
        for s in streams:
            name = s["app"]
            if name.lower() not in existing and name.lower() not in seen:
                new_apps.append(name)
                seen.add(name.lower())

        if not new_apps:
            self.toast_overlay.add_toast(
                Adw.Toast(title="All running apps already have routing rules", timeout=3))
            return

        # Build dialog with discovered apps
        dialog = Adw.AlertDialog(
            heading=f"Found {len(new_apps)} new app{'s' if len(new_apps) != 1 else ''}",
            body="Select a channel for each app you want to route:",
        )

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ch_names = [ch["name"] for ch in self.state["channels"]]
        app_widgets = []  # list of (app_name, check, dropdown)

        for app_name in new_apps:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            check = Gtk.CheckButton(active=True)
            row.append(check)

            label = Gtk.Label(label=app_name, hexpand=True, xalign=0, ellipsize=3)
            row.append(label)

            dd = Gtk.DropDown.new_from_strings(ch_names)
            dd.set_selected(0)
            dd.set_valign(Gtk.Align.CENTER)
            row.append(dd)

            content.append(row)
            app_widgets.append((app_name, check, dd))

        dialog.set_extra_child(content)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add Selected")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        dialog.connect("response",
                        lambda d, r: self._do_discover_add(r, app_widgets, ch_names))
        dialog.present(self.get_root())

    def _do_discover_add(self, response, app_widgets, ch_names):
        if response != "add":
            return

        added = 0
        for app_name, check, dd in app_widgets:
            if not check.get_active():
                continue
            ch_idx = dd.get_selected()
            channel = ch_names[ch_idx] if 0 <= ch_idx < len(ch_names) else ch_names[0]
            self.state["routing"]["rules"].append({
                "app": app_name,
                "channel": channel,
            })
            added += 1
            log.info("Discovered rule: '%s' → '%s'", app_name, channel)

        if added > 0:
            self.mark_dirty(requires_restart=True)
            self.toast_overlay.add_toast(
                Adw.Toast(title=f'Added {added} routing rule{"s" if added != 1 else ""}',
                          timeout=2))
            self._build()
        else:
            self.toast_overlay.add_toast(
                Adw.Toast(title="No apps selected", timeout=2))