// SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
// SPDX-License-Identifier: GPL-3.0-or-later

import {actions, capabilities, device, profiles, reminder, RefreshQueue, status} from '../extension/model.js';

function assert(condition, message) {
    if (!condition)
        throw new Error(message);
}

function rejects(callback) {
    try {
        callback();
    } catch {
        return;
    }
    throw new Error('Invalid document was accepted');
}

const capability = {schemaVersion: 1, interface: 'io.github.btrfsbackup.Manager1',
    apiMajor: 1, apiMinor: 0, profileSchemaVersion: 1, publicStatusSchemaVersion: 1,
    historySchemaVersion: 1, deviceStateSchemaVersion: 1, readOnly: false,
    features: ['start-backup', 'cancel-backup', 'eject-target', 'browse-backups', 'profile-activation']};
const features = capabilities(capability);
rejects(() => capabilities({...capability, apiMajor: 2}));
rejects(() => capabilities({...capability, readOnly: true}));
rejects(() => capabilities({...capability, publicStatusSchemaVersion: 4}));
const profile = profiles([{schemaVersion: 1, profileId: 'home', name: 'Home', enabled: true, sources: []}])[0];
assert(profile.id === 'home', 'Public profile identifiers must be preserved');
rejects(() => profiles([{...profile, schemaVersion: 8}]));
const run = {schemaVersion: 1, state: 'running', operationKind: 'backup', runId: 'run-one',
    phase: 'transferring', activity: 'transferring', errorCode: '', sourceName: 'Home', targetName: 'Disk',
    canCancel: true, progressAccuracy: 'exact', sourceProgress: 50, overallProgress: 50,
    sourceIndex: 1, sourceCount: 1, startedAt: '', updatedAt: '', lastSuccessAt: '',
    lastAttemptAt: '', lastAttemptState: '', etaSeconds: 1};
status(run);
rejects(() => status({...run, overallProgress: 101}));
rejects(() => status({...run, canCancel: true, runId: ''}));
rejects(() => status({...run, state: 'succeeded', canCancel: true}));
const target = {schemaVersion: 1, state: 'mounted', connected: true, unlocked: true, mounted: true, safeToRemove: false};
assert(device({...target, storage: {schemaVersion: 8}}, true).mounted, 'Malformed storage must preserve target lifecycle');
assert(!device({...target, storage: {schemaVersion: 8}}, true).storage, 'Malformed storage must be ignored');
assert(!actions(profile, run, target, features, true).eject, 'Active backups must disable eject');
assert(actions(profile, run, target, features, true).cancel, 'Active cancellable run must allow cancellation');
assert(!actions(profile, run, target, features, false).cancel, 'Disconnection must disable cancellation');
assert(!actions(profile, run, target, features, true, true).cancel, 'Pending operations must disable cancellation');
assert(!actions(profile, {}, {...target, connected: false}, features, true).start, 'Manager availability is not device connectivity');
assert(!actions({...profile, configurationValid: false}, {}, target, features, true).start, 'Invalid configurations must disable start');
assert(!actions(profile, {}, target, new Set(), true).browse, 'Missing capability must disable browsing');
assert(!actions(profile, undefined, target, features, true).start, 'Missing run state must disable start');
assert(actions(profile, {state: 'succeeded'}, {...target, safeToRemove: false}, features, true).eject,
    'Backup success must not imply safe target removal');
const now = Date.parse('2026-10-01T00:00:00Z');
assert(reminder(profile, {lastSuccessAt: '2026-09-01T00:00:00Z'}, 7, 30, now).level === 'critical', 'Overdue backups must escalate');
assert(!reminder({...profile, enabled: false}, {lastSuccessAt: '2026-09-01T00:00:00Z'}, 7, 30, now), 'Disabled profiles must not be reminded');
assert(!reminder(profile, {...run, lastSuccessAt: '2026-09-01T00:00:00Z'}, 7, 30, now), 'Active backups must not be reminded');

let complete;
let reads = 0;
const values = [];
const queue = new RefreshQueue(() => {
    reads++;
    return reads === 1 ? new Promise(resolve => { complete = resolve; }) : Promise.resolve(2);
}, (_key, value) => values.push(value), (_key, error) => { throw error; });
const first = queue.refresh('status:home');
await queue.refresh('status:home');
await queue.refresh('status:home');
complete(1);
await first;
assert(reads === 2 && values.join(',') === '1,2', 'Changes during reads must schedule one coalesced follow-up');
let finish;
const stopped = new RefreshQueue(() => new Promise(resolve => { finish = resolve; }),
    () => { throw new Error('A disabled client published stale data'); }, () => {});
const pending = stopped.refresh('profiles');
stopped.stop();
finish([]);
await pending;
print('GNOME model contracts passed');
