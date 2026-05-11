"""Channels management page."""

import logging
from gi.repository import Gtk, Adw

log = logging.getLogger("tonal.channels")


def _scrollable(child):
    sw = Gtk.ScrolledWindow(vexpand=True)
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.set_child(child)
    return sw


def _target_label(ch):
    target = ch.get("target", "")
    pos = ", ".join(ch.get("position", []))
    if target == "rodecaster_expanded":
        return f"USB 1 Expanded  •  Channels: {pos}"
    elif target == "usb1_chat":
        return "USB 1 Chat  •  Stereo"
    elif target == "usb2":
        return "USB 2 Secondary  •  Stereo"
    return f"{target}  •  {pos}"


class ChannelsPage(Gtk.Box):
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

        # Hardware status
        hw = self.state.get("hardware", {})
        hw_group = Adw.PreferencesGroup(title="Detected hardware")
        vbox.append(hw_group)

        usb1_ok = hw.get("usb1_detected", False)
        usb1_mode = " • Expanded" if hw.get("expanded_mode") else " • Standard"
        usb1_row = Adw.ActionRow(title="USB 1 (Primary)",
                                  subtitle=f'{"Connected" if usb1_ok else "Not detected"}{usb1_mode if usb1_ok else ""}')
        usb1_row.add_prefix(self._dot(usb1_ok))
        hw_group.add(usb1_row)

        usb2_ok = hw.get("usb2_detected", False)
        usb2_row = Adw.ActionRow(title="USB 2 (Secondary)",
                                  subtitle="Connected" if usb2_ok else "Not detected")
        usb2_row.add_prefix(self._dot(usb2_ok))
        hw_group.add(usb2_row)

        # Channels
        channels = self.state.get("channels", [])
        if channels:
            group = Adw.PreferencesGroup(
                title="Audio channels",
                description="Each channel appears as a selectable output device. "
                            "Assign each to a fader on the RODECaster.",
            )
            vbox.append(group)

            for i, ch in enumerate(channels):
                title = ch["name"]
                if ch.get("is_default"):
                    title += " (Default)"
                row = Adw.ActionRow(title=title, subtitle=_target_label(ch))

                # Edit button
                edit_btn = Gtk.Button(icon_name="document-edit-symbolic",
                                      valign=Gtk.Align.CENTER, css_classes=["flat"],
                                      tooltip_text="Rename channel")
                edit_btn.connect("clicked", lambda b, idx=i: self._on_rename(idx))
                row.add_suffix(edit_btn)

                # On/Off label
                status_label = Gtk.Label(
                    label="On" if ch["enabled"] else "Off",
                    css_classes=["caption"],
                    valign=Gtk.Align.CENTER,
                    margin_end=4,
                )
                row.add_suffix(status_label)

                # Toggle (far right)
                switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=ch["enabled"])
                if not ch["enabled"]:
                    switch.add_css_class("switch-off")
                switch.connect("state-set", lambda sw, state, idx=i, lbl=status_label:
                               self._on_toggle(sw, state, idx, lbl))
                row.add_suffix(switch)

                row.set_activatable_widget(switch)
                group.add(row)
        else:
            empty_group = Adw.PreferencesGroup(title="No channels detected")
            vbox.append(empty_group)
            empty_group.add(Adw.ActionRow(
                title="RODECaster not found",
                subtitle="Connect the RODECaster via USB and restart Tonal",
            ))

        # Default output
        default_ch = next((ch["name"] for ch in channels if ch.get("is_default")), None)
        if default_ch:
            dg = Adw.PreferencesGroup(title="Default output"); vbox.append(dg)
            dr = Adw.ActionRow(title=default_ch,
                                subtitle="All unmatched applications route here")
            dr.add_prefix(Gtk.Image.new_from_icon_name("emblem-default-symbolic"))
            dg.add(dr)

        self.append(_scrollable(inner))

    def _dot(self, ok):
        dot = Gtk.Box()
        dot.set_size_request(8, 8)
        dot.set_valign(Gtk.Align.CENTER); dot.set_margin_end(8)
        dot.add_css_class("dot-ok" if ok else "dot-err")
        return dot

    def _on_toggle(self, switch, state_val, idx, label):
        if 0 <= idx < len(self.state["channels"]):
            self.state["channels"][idx]["enabled"] = state_val
            label.set_text("On" if state_val else "Off")
            if state_val:
                switch.remove_css_class("switch-off")
            else:
                switch.add_css_class("switch-off")
            self.mark_dirty(requires_restart=True)
            log.info("Channel '%s' %s", self.state["channels"][idx]["name"],
                     "enabled" if state_val else "disabled")

    def _on_rename(self, idx):
        if idx < 0 or idx >= len(self.state["channels"]):
            return
        ch = self.state["channels"][idx]
        old_name = ch["name"]

        dialog = Adw.AlertDialog(
            heading="Rename channel",
            body=f'Enter a new name for "{old_name}":',
        )
        entry = Gtk.Entry(text=old_name, hexpand=True)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Rename")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: self._do_rename(r, idx, entry.get_text()))
        dialog.present(self.get_root())

    def _do_rename(self, response, idx, new_name):
        if response != "save":
            return
        new_name = new_name.strip()
        if not new_name:
            return
        old_name = self.state["channels"][idx]["name"]
        if new_name == old_name:
            return

        self.state["channels"][idx]["name"] = new_name
        for rule in self.state["routing"]["rules"]:
            if rule["channel"] == old_name:
                rule["channel"] = new_name
                log.info("Updated routing rule '%s' → channel '%s'", rule["app"], new_name)

        self.mark_dirty(requires_restart=True)
        log.info("Renamed channel '%s' → '%s'", old_name, new_name)
        self.toast_overlay.add_toast(Adw.Toast(title=f'Renamed to "{new_name}"', timeout=2))
        self._build()
