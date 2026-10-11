# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Files context action. The manager validates coverage from an O_PATH fd."""

from gi.repository import Gio, GObject, Nautilus


class PreviousVersions(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        if len(files) != 1:
            return []
        location = files[0].get_location()
        path = location.get_path()
        if path is None or files[0].is_gone():
            return []
        item = Nautilus.MenuItem(name="BtrfsBackup::PreviousVersions",
            label="Previous Backup Versions…", tip="Browse and restore previous backup versions")
        item.connect("activate", lambda _item: Gio.Subprocess.new(
            ["btrfs-backup-gnome", "--previous-versions", path], Gio.SubprocessFlags.NONE))
        return [item]
