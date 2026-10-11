// SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
// SPDX-License-Identifier: GPL-3.0-or-later

import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class BackupPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const page = new Adw.PreferencesPage({title: 'Btrfs Backup', icon_name: 'drive-harddisk-symbolic'});
        window.add(page);
        const manager = new Adw.PreferencesGroup({title: 'Profiles and backups'});
        const row = new Adw.ActionRow({title: 'Open Backup Manager', subtitle: 'Configure devices and sources, browse history and restore files'});
        const button = new Gtk.Button({label: 'Open', valign: Gtk.Align.CENTER});
        button.connect('clicked', () => {
            try {
                Gio.Subprocess.new(['btrfs-backup-gnome'], Gio.SubprocessFlags.NONE);
            } catch (error) {
                row.subtitle = error.message;
            }
        });
        row.add_suffix(button);
        manager.add(row);
        page.add(manager);
        const notifications = new Adw.PreferencesGroup({title: 'Notifications'});
        page.add(notifications);
        for (const [key, title] of [
            ['terminal-notifications', 'Completed and failed runs'],
            ['progress-notifications', 'Live backup progress'],
            ['reminders', 'Overdue backup reminders'],
            ['storage-notifications', 'Low target space'],
        ]) {
            const toggle = new Adw.SwitchRow({title});
            settings.bind(key, toggle, 'active', Gio.SettingsBindFlags.DEFAULT);
            notifications.add(toggle);
        }
        for (const [key, title, maximum] of [
            ['warning-days', 'Warn after days', 3650],
            ['critical-days', 'Critical after days', 3650],
            ['storage-warning-percent', 'Warn below free space (%)', 100],
            ['storage-critical-percent', 'Critical below free space (%)', 100],
        ]) {
            const spin = new Adw.SpinRow({title, adjustment: new Gtk.Adjustment({lower: maximum === 100 ? 0 : 1, upper: maximum, step_increment: 1})});
            settings.bind(key, spin, 'value', Gio.SettingsBindFlags.DEFAULT);
            notifications.add(spin);
        }
    }
}
