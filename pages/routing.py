"""Routing page — app routing rules and active streams."""

from gi.repository import Gtk, Adw
import pipewire_ctl


def _scrollable(child):
    sw = Gtk.ScrolledWindow(vexpand=True)
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.set_child(child)
    return sw


class RoutingPage(Gtk.Box):
    def __init__(self, state):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        clamp = Adw.Clamp(maximum_size=700); inner.append(clamp)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16); clamp.set_child(vbox)

        # Active streams (live)
        ag = Adw.PreferencesGroup(title="Active streams",
            description="Currently playing audio and where it's routed.")
        vbox.append(ag)
        streams = pipewire_ctl.get_active_streams()
        if streams:
            for s in streams:
                r = Adw.ActionRow(title=s["app"],
                                   subtitle=f'Stream #{s["stream_id"]}  →  Sink {s["sink_id"]}')
                r.add_prefix(Gtk.Image.new_from_icon_name("audio-volume-high-symbolic"))
                ag.add(r)
        else:
            ag.add(Adw.ActionRow(title="No active streams",
                                  subtitle="Play audio in an application to see it here"))

        # Routing rules
        rg = Adw.PreferencesGroup(title="Routing rules",
            description="Persistent rules applied when a stream starts.")
        vbox.append(rg)
        ch_names = [ch["name"] for ch in state["channels"]]
        for rule in state["routing"]["rules"]:
            r = Adw.ActionRow(title=rule["app"], subtitle=f'Binary: {rule.get("binary", "—")}')
            c = Gtk.DropDown.new_from_strings(ch_names)
            if rule["channel"] in ch_names:
                c.set_selected(ch_names.index(rule["channel"]))
            c.set_valign(Gtk.Align.CENTER); r.add_suffix(c)
            r.add_suffix(Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER,
                                     css_classes=["flat"]))
            rg.add(r)

        vbox.append(Gtk.Button(label="Add rule", icon_name="list-add-symbolic",
                                halign=Gtk.Align.START, css_classes=["pill"]))

        dg = Adw.PreferencesGroup(title="Discover apps"); vbox.append(dg)
        dr = Adw.ActionRow(title="Scan for running audio apps",
                            subtitle="Find apps playing audio and assign them to channels")
        dr.add_suffix(Gtk.Button(icon_name="system-search-symbolic", valign=Gtk.Align.CENTER,
                                  css_classes=["flat"]))
        dg.add(dr)
        self.append(_scrollable(inner))
