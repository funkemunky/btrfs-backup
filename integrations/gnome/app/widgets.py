# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

from gi.repository import Adw, Gtk


def button(label, callback, destructive=False):
    widget = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    if destructive:
        widget.add_css_class("destructive-action")
    widget.connect("clicked", lambda _button: callback())
    return widget


def group(container, title, description=None):
    widget = Adw.PreferencesGroup(title=title, description=description or "")
    container.append(widget)
    return widget


def row(container, title, subtitle="", callback=None):
    widget = Adw.ActionRow(title=str(title), subtitle=str(subtitle), use_markup=False)
    if callback:
        widget.set_activatable(True)
        widget.connect("activated", lambda _row: callback())
        widget.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
    container.add(widget)
    return widget


def entry(container, title, value="", password=False):
    widget = (Adw.PasswordEntryRow if password else Adw.EntryRow)(title=title, text=str(value))
    container.add(widget)
    return widget


def spin(container, title, value, minimum=0, maximum=100000):
    widget = Adw.SpinRow(title=title,
        adjustment=Gtk.Adjustment(value=value, lower=minimum, upper=maximum, step_increment=1))
    container.add(widget)
    return widget


def switch(container, title, value):
    widget = Adw.SwitchRow(title=title, active=bool(value))
    container.add(widget)
    return widget


def choose(container, title, labels):
    widget = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
    container.add(widget)
    return widget


def page(title):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                  margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
    clamp = Adw.Clamp(child=box, maximum_size=950)
    scroll = Gtk.ScrolledWindow(child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
    return Adw.NavigationPage(title=title, child=scroll), box


def confirm(window, title, description, callback, label="Confirm", destructive=False):
    dialog = Adw.AlertDialog(heading=title, body=description)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("confirm", label)
    dialog.set_close_response("cancel")
    dialog.set_default_response("cancel" if destructive else "confirm")
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE
        if destructive else Adw.ResponseAppearance.SUGGESTED)
    dialog.connect("response", lambda _dialog, response: callback() if response == "confirm" else None)
    dialog.present(window)
