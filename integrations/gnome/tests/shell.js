// SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
// SPDX-License-Identifier: GPL-3.0-or-later

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async', 'communicate_utf8_finish');

export const METRICS = {};

function assert(value, message) {
    if (!value)
        throw new Error(message);
}

export async function run() {
    await Scripting.sleep(500);
    const uuid = 'btrfs-backup@btrfsbackup.github.io';
    const loaded = Main.extensionManager.lookup(uuid);
    assert(loaded?.stateObj?._button, `Extension failed to load: ${loaded?.error ?? 'missing'}`);
    const extension = loaded.stateObj;
    extension._manager.destroy();
    const profile = {id: 'home', profileId: 'home', name: 'Home', enabled: true, sources: []};
    const run = {runId: 'shell-test', state: 'running', operationKind: 'backup', canCancel: true,
        activity: 'transferring', sourceName: 'Home', sourceIndex: 1, sourceCount: 1,
        overallProgress: 50, progressAccuracy: 'exact', bytesProcessed: 1024,
        bytesTotalEstimated: 2048, speedBps: 500, etaSeconds: 2, updatedAt: 'first', lastSuccessAt: ''};
    const manager = {ready: true, error: '', profiles: [profile], pending: new Set(),
        runs: new Map([['home', run]]), targets: new Map([['home', {connected: true,
            state: 'mounted', mounted: true, unlocked: true, safeToRemove: false}]]),
        features: new Set(['start-backup', 'cancel-backup', 'browse-backups', 'eject-target', 'profile-activation']),
        destroy() {}};
    extension._manager = manager;
    extension._render();
    extension._button.menu.open();
    extension._button.menu._getMenuItems()[0].menu.open();
    await Scripting.sleep(200);
    assert(extension._progress.size === 1, 'Live progress notification was not created');
    run.speedBps = 750;
    run.updatedAt = 'second';
    extension._render();
    assert(extension._progress.get('home').body.includes('750'), 'Live progress did not update');
    assert((await extension._provider.getInitialResultSet(['browse', 'home'])).includes('browse:home'), 'Search provider did not return browse');
    run.state = 'succeeded';
    run.canCancel = false;
    extension._render();
    assert(extension._progress.size === 0, 'Terminal run did not remove ongoing progress');
    assert(extension._settings.get_strv('notification-keys').includes('terminal:home:shell-test:succeeded'), 'Terminal notification was not persisted');
    extension.disable();
    assert(!extension._button && !extension._source && !extension._timer, 'Disable left Shell resources behind');
    extension.enable();
    assert(extension._button, 'Extension did not re-enable');

    // UI smoke checks run in this disposable compositor and use a fake manager.
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE});
    launcher.setenv('WAYLAND_DISPLAY', 'gnome-shell-test-display', true);
    launcher.setenv('GDK_BACKEND', 'wayland', true);
    launcher.setenv('GSK_RENDERER', 'cairo', true);
    const filename = GLib.filename_from_uri(import.meta.url)[0];
    const process = launcher.spawnv(['python3', GLib.build_filenamev([GLib.path_get_dirname(filename), 'ui.py'])]);
    const [stdout, stderr] = await process.communicate_utf8_async(null, null);
    assert(process.get_successful(), `GTK smoke test failed: ${stdout}\n${stderr}`);
    print(stdout);
    print('GNOME Shell lifecycle and progress checks passed');
}
