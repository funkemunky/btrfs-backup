// SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
// SPDX-License-Identifier: GPL-3.0-or-later

import Gio from 'gi://Gio';
import GioUnix from 'gi://GioUnix';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Manager} from './manager.js';
import {active, actions, formatBytes, reminder} from './model.js';

class BackupSearch {
    constructor(extension) {
        this._extension = extension;
        this.id = extension.uuid;
        this.appInfo = GioUnix.DesktopAppInfo.new('io.github.btrfsbackup.Gnome.desktop');
        this.canLaunchSearch = true;
    }

    async getInitialResultSet(terms) {
        const query = terms.join(' ').toLowerCase();
        const manager = this._extension._manager;
        if (!manager.ready || !query)
            return [];
        const results = [];
        for (const p of manager.profiles) {
            const allowed = actions(p, manager.runs.get(p.id), manager.targets.get(p.id),
                manager.features, manager.ready, manager.pending.has(p.id));
            for (const kind of ['status', 'start', 'browse', 'eject']) {
                const name = `${kind} backup ${p.name} ${p.id}`.toLowerCase();
                if ((kind === 'status' || allowed[kind]) && terms.every(t => name.includes(t.toLowerCase())))
                    results.push(`${kind}:${p.id}`);
            }
        }
        if (query.startsWith('versions /'))
            results.push(`versions:${terms.join(' ').slice(9)}`);
        return results;
    }

    getSubsearchResultSet(_previous, terms) {
        return this.getInitialResultSet(terms);
    }

    filterResults(results, maximum) {
        return results.slice(0, maximum);
    }

    async getResultMetas(ids) {
        return ids.map(id => {
            const separator = id.indexOf(':');
            const kind = id.slice(0, separator);
            const argument = id.slice(separator + 1);
            const profile = this._extension._manager.profiles.find(p => p.id === argument);
            return {id, name: `${kind[0].toUpperCase()}${kind.slice(1)} backup: ${profile?.name ?? argument}`,
                description: 'Btrfs Backup',
                createIcon: size => new St.Icon({icon_name: 'drive-harddisk-symbolic', icon_size: size})};
        });
    }

    activateResult(id) {
        const separator = id.indexOf(':');
        const kind = id.slice(0, separator);
        const argument = id.slice(separator + 1);
        const profile = this._extension._manager.profiles.find(p => p.id === argument);
        if (kind === 'versions')
            this._extension._launch(['--previous-versions', argument]);
        else if (profile) {
            const manager = this._extension._manager;
            const allowed = actions(profile, manager.runs.get(profile.id), manager.targets.get(profile.id),
                manager.features, manager.ready, manager.pending.has(profile.id));
            if (kind === 'start' && allowed.start)
                this._extension._operate(profile, 'StartBackup');
            else if (kind === 'eject' && allowed.eject)
                this._extension._operate(profile, 'EjectTarget');
            else if (kind === 'browse' && allowed.browse)
                this._extension._launch(['--browse', profile.id]);
            else if (kind === 'status')
                this._extension._launch(['--profile', profile.id]);
        }
        Main.overview.hide();
    }

    launchSearch() {
        this._extension._launch();
    }
}

export default class BackupExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._observed = new Map();
        this._progress = new Map();
        this._samples = new Map();
        this._button = new PanelMenu.Button(0.0, 'Btrfs Backup');
        this._icon = new St.Icon({icon_name: 'drive-harddisk-symbolic', style_class: 'system-status-icon'});
        this._button.add_child(this._icon);
        Main.panel.addToStatusArea(this.uuid, this._button);
        this._manager = new Manager(() => this._render());
        this._provider = new BackupSearch(this);
        Main.overview.searchController.addProvider(this._provider);
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 60, () => {
            this._render();
            return GLib.SOURCE_CONTINUE;
        });
        this._settingsSignal = this._settings.connect('changed', (_settings, key) => {
            if (key !== 'notification-keys')
                this._render();
        });
        this._render();
    }

    _launch(args = []) {
        try {
            Gio.Subprocess.new(['btrfs-backup-gnome', ...args], Gio.SubprocessFlags.NONE);
        } catch (error) {
            this._notify('Could not open Backup Manager', error.message);
        }
    }

    async _operate(profile, method, runId = '') {
        try {
            await this._manager.operate(profile, method, runId);
        } catch (error) {
            if (this._button)
                this._notify('Backup operation failed', error.message);
        }
    }

    _sourceForNotifications() {
        if (!this._source) {
            this._source = new MessageTray.Source({title: 'Btrfs Backup', iconName: 'drive-harddisk-symbolic'});
            this._source.connect('destroy', () => {
                this._source = null;
                this._progress.clear();
            });
            Main.messageTray.add(this._source);
        }
        return this._source;
    }

    _notify(title, body, key = '', critical = false) {
        const keys = this._settings.get_strv('notification-keys');
        if (key && keys.includes(key))
            return;
        const source = this._sourceForNotifications();
        const notification = new MessageTray.Notification({source, title, body,
            urgency: critical ? MessageTray.Urgency.HIGH : MessageTray.Urgency.NORMAL});
        notification.addAction('Open Backup Manager', () => this._launch());
        source.addNotification(notification);
        if (key)
            this._settings.set_strv('notification-keys', [...keys, key].slice(-128));
    }

    _monitor(profile, run, target) {
        const previous = this._observed.get(profile.id);
        if (run) {
            if (previous && previous.runId === run.runId && active(previous.state) && !active(run.state) &&
                ['succeeded', 'validated', 'failed', 'cancelled', 'skipped'].includes(run.state) &&
                this._settings.get_boolean('terminal-notifications'))
                this._notify(`${profile.name}: ${run.state}`, run.errorCode || 'Backup operation finished',
                    `terminal:${profile.id}:${run.runId}:${run.state}`, run.state === 'failed');
            this._observed.set(profile.id, {runId: run.runId, state: run.state});
        }
        const progress = this._progress.get(profile.id);
        if (active(run?.state) && this._settings.get_boolean('progress-notifications')) {
            const body = this._runText(run);
            if (progress && progress._runId !== run.runId)
                progress.destroy();
            const currentProgress = this._progress.get(profile.id);
            if (currentProgress) {
                currentProgress.title = `${profile.name}: backup in progress`;
                currentProgress.body = body;
            } else {
                const source = this._sourceForNotifications();
                const notification = new MessageTray.Notification({source,
                    title: `${profile.name}: backup in progress`, body, resident: true});
                const runId = run.runId;
                notification._runId = runId;
                if (run.canCancel) {
                    notification.addAction('Cancel', () => {
                        const current = this._manager.runs.get(profile.id);
                        if (current?.runId === runId && current.canCancel)
                            this._operate(profile, 'CancelBackup', runId);
                    });
                }
                notification.connect('destroy', () => this._progress.delete(profile.id));
                this._progress.set(profile.id, notification);
                source.addNotification(notification);
            }
        } else if (progress) {
            progress.destroy();
        }
        if (this._settings.get_boolean('reminders')) {
            const warning = this._settings.get_int('warning-days');
            const result = reminder(profile, run, warning,
                Math.max(warning, this._settings.get_int('critical-days')));
            if (result)
                this._notify(`${profile.name}: backup overdue`, `${result.days} days since the last backup`,
                    `reminder:${result.key}`, result.level === 'critical');
        }
        const storage = target?.storage;
        if (profile.enabled && storage && this._settings.get_boolean('storage-notifications')) {
            const free = 100 - storage.usagePercent;
            const warning = this._settings.get_int('storage-warning-percent');
            const critical = storage.spaceState === 'below-configured-minimum' ||
                free <= Math.min(warning, this._settings.get_int('storage-critical-percent'));
            if (critical || free <= warning)
                this._notify(`${profile.name}: low backup space`,
                    `${formatBytes(storage.availableBytes)} available (${free}% free)${storage.live ? '' : ' · last measurement'}`,
                    `storage:${profile.id}:${critical ? 'critical' : 'warning'}:${Math.floor(Date.now() / 86400000)}`, critical);
        }
    }

    _runText(run) {
        const percent = run.overallProgress >= 0 && run.progressAccuracy !== 'indeterminate'
            ? `${run.progressAccuracy === 'estimated' ? '~' : ''}${run.overallProgress}% · ` : '';
        const eta = run.etaSeconds >= 0 ? ` · ${run.etaSeconds}s remaining` : '';
        return `${percent}${run.activity || run.phase} · ${run.sourceName} (${run.sourceIndex}/${run.sourceCount}) · ` +
            `${formatBytes(run.bytesProcessed)} / ${formatBytes(run.bytesTotalEstimated)} · ${formatBytes(run.speedBps)}/s${eta}`;
    }

    _chart(menu, profileId, run) {
        let samples = this._samples.get(profileId);
        if (!samples || samples.runId !== run.runId)
            samples = {runId: run.runId, updatedAt: '', values: []};
        if (samples.updatedAt !== run.updatedAt) {
            samples.values.push(run.speedBps || 0);
            samples.values = samples.values.slice(-60);
            samples.updatedAt = run.updatedAt;
        }
        this._samples.set(profileId, samples);
        const area = new St.DrawingArea({style_class: 'btrfs-backup-chart'});
        area.connect('repaint', () => {
            const cr = area.get_context();
            const [width, height] = area.get_surface_size();
            const maximum = Math.max(1, ...samples.values);
            cr.setSourceRGBA(0.25, 0.65, 1, 1);
            cr.setLineWidth(2);
            samples.values.forEach((value, index) => {
                const x = index * width / 59;
                const y = height - value / maximum * (height - 4) - 2;
                if (index === 0)
                    cr.moveTo(x, y);
                else
                    cr.lineTo(x, y);
            });
            cr.stroke();
            cr.$dispose();
        });
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false});
        item.add_child(area);
        menu.addMenuItem(item);
    }

    _render() {
        const manager = this._manager;
        if (!manager || !this._button)
            return;
        // Signal updates preserve open profile menus instead of closing the popup.
        const open = new Set(this._button.menu._getMenuItems()
            .filter(item => item.menu?.isOpen).map(item => item._profileId));
        this._button.menu.removeAll();
        const ids = new Set(manager.profiles.map(p => p.id));
        for (const [id, notification] of this._progress) {
            if (!ids.has(id))
                notification.destroy();
        }
        for (const map of [this._observed, this._samples]) {
            for (const id of map.keys()) {
                if (!ids.has(id))
                    map.delete(id);
            }
        }
        if (manager.error || !manager.ready)
            this._button.menu.addMenuItem(new PopupMenu.PopupMenuItem(manager.error || 'Connecting to backup manager…', {reactive: false}));
        let attention = false;
        let running = false;
        for (const profile of manager.profiles) {
            const run = manager.runs.get(profile.id);
            const target = manager.targets.get(profile.id);
            attention ||= profile.configurationValid === false || run?.state === 'failed' ||
                target?.storage?.spaceState === 'below-configured-minimum';
            running ||= active(run?.state);
            const submenu = new PopupMenu.PopupSubMenuMenuItem(`${profile.name} · ${run?.state ?? 'unknown'} · ` +
                (target?.safeToRemove ? 'safe to remove' : target?.state ?? 'unknown'));
            submenu._profileId = profile.id;
            this._button.menu.addMenuItem(submenu);
            const menu = submenu.menu;
            if (run) {
                menu.addMenuItem(new PopupMenu.PopupMenuItem(this._runText(run), {reactive: false}));
                menu.addMenuItem(new PopupMenu.PopupMenuItem(`Last successful backup: ${run.lastSuccessAt || 'never'}`, {reactive: false}));
                if (active(run.state))
                    this._chart(menu, profile.id, run);
            }
            if (profile.configurationValid === false)
                menu.addMenuItem(new PopupMenu.PopupMenuItem(profile.configurationErrorCode || 'Invalid profile configuration', {reactive: false}));
            if (target?.storage) {
                const s = target.storage;
                menu.addMenuItem(new PopupMenu.PopupMenuItem(`${formatBytes(s.availableBytes)} free of ${formatBytes(s.capacityBytes)} · ` +
                    `${s.usagePercent}% used${s.live ? '' : ` · measured ${s.measuredAt}`}`, {reactive: false}));
            }
            const allowed = actions(profile, run, target, manager.features, manager.ready, manager.pending.has(profile.id));
            const add = (label, enabled, callback) => {
                const item = new PopupMenu.PopupMenuItem(label);
                item.sensitive = !!enabled;
                item.connect('activate', callback);
                menu.addMenuItem(item);
            };
            add('Start backup', allowed.start, () => this._operate(profile, 'StartBackup'));
            add('Cancel backup', allowed.cancel, () => this._operate(profile, 'CancelBackup', run.runId));
            add('Browse and restore backups', allowed.browse, () => this._launch(['--browse', profile.id]));
            add('Eject target', allowed.eject, () => this._operate(profile, 'EjectTarget'));
            const automatic = new PopupMenu.PopupSwitchMenuItem('Automatic backups', profile.enabled);
            automatic.sensitive = allowed.automatic;
            automatic.connect('toggled', () => this._operate(profile, 'SetProfileEnabled'));
            menu.addMenuItem(automatic);
            add('Profile settings and history', manager.ready, () => this._launch(['--profile', profile.id]));
            if (open.has(profile.id))
                menu.open();
            this._monitor(profile, run, target);
        }
        this._icon.icon_name = attention ? 'dialog-warning-symbolic' : running ? 'emblem-synchronizing-symbolic' : 'drive-harddisk-symbolic';
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const manage = new PopupMenu.PopupMenuItem('Open Backup Manager');
        manage.connect('activate', () => this._launch());
        this._button.menu.addMenuItem(manage);
        const preferences = new PopupMenu.PopupMenuItem('Notification preferences');
        preferences.connect('activate', () => this.openPreferences());
        this._button.menu.addMenuItem(preferences);
    }

    disable() {
        Main.overview.searchController.removeProvider(this._provider);
        this._provider = null;
        GLib.Source.remove(this._timer);
        this._timer = 0;
        this._settings.disconnect(this._settingsSignal);
        this._manager.destroy();
        this._manager = null;
        this._source?.destroy();
        this._source = null;
        this._button.destroy();
        this._button = null;
        this._settings = null;
        this._progress.clear();
        this._observed.clear();
        this._samples.clear();
    }
}
