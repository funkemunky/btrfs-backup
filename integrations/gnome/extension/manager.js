// SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
// SPDX-License-Identifier: GPL-3.0-or-later

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {BUS_NAME, OBJECT_PATH, RefreshQueue, capabilities, profiles, status, device} from './model.js';

export class Manager {
    constructor(changed) {
        this.changed = changed;
        this.profiles = [];
        this.runs = new Map();
        this.targets = new Map();
        this.pending = new Set();
        this.features = new Set();
        this.ready = false;
        this.error = '';
        this._watch = Gio.bus_watch_name(Gio.BusType.SYSTEM, BUS_NAME,
            Gio.BusNameWatcherFlags.AUTO_START,
            (bus, _name, owner) => this._connect(bus, owner),
            () => this._disconnect('Backup manager unavailable'));
    }

    _disconnect(error = '') {
        this._queue?.stop();
        this._cancellable?.cancel();
        if (this._signal)
            this._bus.signal_unsubscribe(this._signal);
        this._signal = 0;
        this._owner = null;
        this.ready = false;
        this.error = error;
        this.profiles = [];
        this.runs.clear();
        this.targets.clear();
        this.pending.clear();
        this.changed();
    }

    async _connect(bus, owner) {
        this._disconnect();
        this._bus = bus;
        this._owner = owner;
        this._cancellable = new Gio.Cancellable();
        this._queue = new RefreshQueue(key => this._read(key),
            (key, value) => this._publish(key, value), (key, error) => {
                const [kind, id] = key.split(':');
                if (kind === 'status')
                    this.runs.delete(id);
                if (kind === 'device')
                    this.targets.delete(id);
                if (kind === 'profiles') {
                    this.profiles = [];
                    this.runs.clear();
                    this.targets.clear();
                }
                this.error = error.message;
                this.changed();
            });
        try {
            const features = capabilities(await this.call('GetCapabilities'));
            if (this._owner !== owner)
                return;
            if (!['profiles', 'status', 'device-state', 'change-signals'].every(f => features.has(f)))
                throw new Error('Backup manager lacks required desktop capabilities');
            this.features = features;
            this._signal = bus.signal_subscribe(owner, BUS_NAME, null, OBJECT_PATH, null,
                Gio.DBusSignalFlags.NONE, (_bus, _sender, _path, _iface, signal, params) => {
                    const [id] = params.deepUnpack();
                    if (signal === 'ProfilesChanged')
                        this._queue.refresh('profiles');
                    if (signal === 'StatusChanged')
                        this._queue.refresh(`status:${id}`);
                    if (signal === 'DeviceStateChanged')
                        this._queue.refresh(`device:${id}`);
                });
            this.ready = true;
            this._queue.refresh('profiles');
        } catch (error) {
            if (this._owner === owner)
                this._disconnect(error.message);
        }
    }

    call(method, signature = '()', args = []) {
        const owner = this._owner;
        return new Promise((resolve, reject) => {
            if (!owner) {
                reject(new Error('Backup manager unavailable'));
                return;
            }
            this._bus.call(owner, OBJECT_PATH, BUS_NAME, method, new GLib.Variant(signature, args),
                new GLib.VariantType('(s)'), Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
                120000, this._cancellable, (bus, result) => {
                    try {
                        const [payload] = bus.call_finish(result).deepUnpack();
                        if (this._owner !== owner)
                            throw new Error('Backup manager restarted');
                        resolve(JSON.parse(payload));
                    } catch (error) {
                        reject(error);
                    }
                });
        });
    }

    async _read(key) {
        const [kind, id] = key.split(':');
        if (kind === 'profiles')
            return profiles(await this.call('ListProfiles'));
        if (kind === 'status')
            return status(await this.call('GetStatus', '(s)', [id]));
        return device(await this.call('GetDeviceState', '(s)', [id]), this.features.has('target-storage-usage'));
    }

    _publish(key, value) {
        const [kind, id] = key.split(':');
        if (kind === 'profiles') {
            this.profiles = value;
            const ids = new Set(value.map(p => p.id));
            for (const map of [this.runs, this.targets]) {
                for (const old of map.keys()) {
                    if (!ids.has(old))
                        map.delete(old);
                }
            }
            for (const p of value) {
                if (p.configurationErrorCode !== 'configuration.unsupported-schema') {
                    this._queue.refresh(`status:${p.id}`);
                    this._queue.refresh(`device:${p.id}`);
                }
            }
        } else if (this.profiles.some(p => p.id === id)) {
            (kind === 'status' ? this.runs : this.targets).set(id, value);
        }
        this.error = '';
        this.changed();
    }

    async operate(profile, method, runId = '') {
        if (!this.ready || this.pending.has(profile.id))
            return;
        this.pending.add(profile.id);
        this.changed();
        try {
            const signature = method === 'CancelBackup' ? '(ss)' : method === 'SetProfileEnabled' ? '(sb)' : '(s)';
            const args = method === 'CancelBackup' ? [profile.id, runId] :
                method === 'SetProfileEnabled' ? [profile.id, !profile.enabled] : [profile.id];
            const result = await this.call(method, signature, args);
            if (result.schemaVersion !== 1 || result.accepted !== true)
                throw new Error('The backup manager did not accept the operation');
            this._queue?.refresh('profiles');
        } finally {
            this.pending.delete(profile.id);
            this.changed();
        }
    }

    destroy() {
        Gio.bus_unwatch_name(this._watch);
        this.changed = () => {};
        this._disconnect();
    }
}
