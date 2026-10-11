// SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
// SPDX-License-Identifier: GPL-3.0-or-later

export const BUS_NAME = 'io.github.btrfsbackup.Manager1';
export const OBJECT_PATH = '/io/github/btrfsbackup/Manager1';
export const active = state => ['starting', 'running', 'validating'].includes(state);

export function capabilities(document) {
    if (document.schemaVersion !== 1 || document.interface !== BUS_NAME ||
        document.apiMajor !== 1 || document.apiMinor !== 0 || document.readOnly !== false ||
        !['profileSchemaVersion', 'publicStatusSchemaVersion', 'historySchemaVersion',
            'deviceStateSchemaVersion'].every(key => document[key] === 1) ||
        !Array.isArray(document.features) || !document.features.every(f => typeof f === 'string'))
        throw new Error('The installed backup manager is incompatible');
    return new Set(document.features);
}

export function profiles(document) {
    if (!Array.isArray(document) || document.some(p => p.schemaVersion !== 1 ||
        typeof p.profileId !== 'string' || !p.profileId || typeof p.name !== 'string' ||
        typeof p.enabled !== 'boolean' || !Array.isArray(p.sources)) ||
        new Set(document.map(p => p.profileId)).size !== document.length)
        throw new Error('Invalid profile list');
    return document.map(p => ({...p, id: p.profileId}));
}

export function status(document) {
    const strings = ['runId', 'operationKind', 'state', 'phase', 'activity', 'errorCode',
        'sourceName', 'targetName', 'progressAccuracy', 'lastSuccessAt', 'lastAttemptAt',
        'lastAttemptState', 'startedAt', 'updatedAt'];
    const percentages = ['sourceProgress', 'overallProgress'];
    if (document.schemaVersion !== 1 || !strings.every(k => typeof document[k] === 'string') ||
        typeof document.canCancel !== 'boolean' ||
        !percentages.every(k => Number.isInteger(document[k]) && document[k] >= -1 && document[k] <= 100) ||
        !['sourceIndex', 'sourceCount'].every(k => Number.isInteger(document[k]) && document[k] >= 0) ||
        document.sourceIndex > document.sourceCount ||
        !['exact', 'estimated', 'indeterminate'].includes(document.progressAccuracy) ||
        !Number.isInteger(document.etaSeconds) || document.etaSeconds < -1 ||
        (active(document.state) && !document.runId) ||
        (document.canCancel && (!active(document.state) || !document.runId)))
        throw new Error('Invalid backup status');
    for (const key of ['bytesProcessed', 'bytesTotalEstimated', 'speedBps']) {
        if (document[key] !== undefined && (!Number.isInteger(document[key]) || document[key] < 0))
            throw new Error('Invalid transfer measurement');
    }
    const states = ['idle', 'starting', 'running', 'validating', 'succeeded', 'failed', 'cancelled', 'exited', 'unavailable'];
    const expected = document.state === 'failed' ? 'backup.failed' : document.state === 'cancelled' ? 'backup.cancelled' : '';
    if (states.includes(document.state) && document.errorCode !== expected)
        throw new Error('Backup error code does not match its state');
    return document;
}

export function device(document, storageSupported) {
    if (document.schemaVersion !== 1 || typeof document.state !== 'string' ||
        !['connected', 'unlocked', 'mounted', 'safeToRemove'].every(k => typeof document[k] === 'boolean'))
        throw new Error('Invalid target state');
    const s = document.storage;
    if (!storageSupported || !s || s.schemaVersion !== 1 || typeof s.live !== 'boolean' ||
        typeof s.measuredAt !== 'string' || !Number.isInteger(s.usagePercent) ||
        s.usagePercent < 0 || s.usagePercent > 100 ||
        !['capacityBytes', 'usedBytes', 'availableBytes'].every(k => Number.isFinite(s[k]) && s[k] >= 0) ||
        !['normal', 'below-configured-minimum'].includes(s.spaceState))
        return {...document, storage: undefined};
    return document;
}

export function actions(profile, run, target, features, ready, pending = false) {
    const available = ready && !pending && !!run && !!target && profile.configurationValid !== false;
    const running = active(run?.state);
    return {
        start: available && features.has('start-backup') && !running && target?.connected === true,
        cancel: available && features.has('cancel-backup') && running && run?.canCancel === true && !!run.runId,
        browse: available && features.has('browse-backups') && target?.connected === true,
        eject: available && features.has('eject-target') && !running && target?.connected === true &&
            (target.mounted || target.unlocked),
        automatic: ready && !pending && profile.configurationValid !== false && features.has('profile-activation'),
    };
}

export function formatBytes(value = 0) {
    let unit = 0;
    while (value >= 1024 && unit < 4) {
        value /= 1024;
        unit++;
    }
    return `${value.toFixed(unit ? 1 : 0)} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][unit]}`;
}

// Keep an invalidation received during a read: every dirty key gets a follow-up.
export class RefreshQueue {
    constructor(read, publish, failed) {
        this._read = read;
        this._publish = publish;
        this._failed = failed;
        this._pending = new Map();
        this._stopped = false;
    }

    async refresh(key) {
        if (this._stopped)
            return;
        if (this._pending.has(key)) {
            this._pending.set(key, true);
            return;
        }
        this._pending.set(key, false);
        do {
            this._pending.set(key, false);
            try {
                const value = await this._read(key);
                if (!this._stopped)
                    this._publish(key, value);
            } catch (error) {
                if (!this._stopped)
                    this._failed(key, error);
            }
        } while (!this._stopped && this._pending.get(key));
        this._pending.delete(key);
    }

    stop() {
        this._stopped = true;
    }
}

export function reminder(profile, run, warningDays, criticalDays, now = Date.now()) {
    if (!profile.enabled || active(run?.state))
        return null;
    const baseline = run?.lastSuccessAt || run?.lastAttemptAt;
    const date = Date.parse(baseline);
    if (!Number.isFinite(date) || date > now)
        return null;
    const days = Math.floor((now - date) / 86400000);
    const level = days >= criticalDays ? 'critical' : days >= warningDays ? 'warning' : '';
    return level ? {key: `${profile.id}:${baseline}:${level}`, days, level} : null;
}
