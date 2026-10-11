# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

from window import BackupWindow


class BackupApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.btrfsbackup.Gnome",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = BackupWindow(self)
        self.window.present()

    def do_command_line(self, command_line):
        parser = argparse.ArgumentParser(description="GNOME backup manager")
        selection = parser.add_mutually_exclusive_group()
        selection.add_argument("--profile")
        selection.add_argument("--browse")
        selection.add_argument("--previous-versions")
        try:
            arguments = parser.parse_args(command_line.get_arguments()[1:])
        except SystemExit as error:
            return error.code
        self.activate()
        self.window.open_command(arguments)
        return 0


if __name__ == "__main__":
    raise SystemExit(BackupApplication().run(sys.argv))
