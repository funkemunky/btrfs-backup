# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Real GIO D-Bus calls on an isolated session bus, including Unix descriptors."""

import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from gi.repository import Gio, GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from manager import Manager, path_fd, secret_fd
from protocol import BUS_NAME, OBJECT_PATH, actions, confined_path, storage_block, validate_document


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for D-Bus client")
        while context.pending():
            context.iteration(False)
        time.sleep(0.001)


capability = {"schemaVersion": 1, "interface": BUS_NAME, "apiMajor": 1, "apiMinor": 0,
    "profileSchemaVersion": 1, "publicStatusSchemaVersion": 1, "historySchemaVersion": 1,
    "deviceStateSchemaVersion": 1, "readOnly": False,
    "features": ["profiles", "status", "device-state", "change-signals", "start-backup", "cancel-backup"]}
connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
interface = Gio.DBusNodeInfo.new_for_xml((Path(sys.argv[1]) / "Manager1.xml").read_text()).interfaces[0]
calls = []
status_reads = 0
received_secrets = []
received_paths = []


def method_call(bus, sender, path, interface_name, method, parameters, invocation):
    global status_reads
    arguments = parameters.unpack()
    calls.append((method, arguments))
    if method == "GetCapabilities":
        value = capability
    elif method == "ListProfiles":
        value = [{"schemaVersion": 1, "profileId": "home", "name": "Home", "enabled": True, "sources": []}]
    elif method == "GetStatus":
        status_reads += 1
        value = {"schemaVersion": 1, "state": "running", "runId": f"run-{status_reads}",
            "operationKind": "backup", "phase": "transferring", "activity": "transferring", "errorCode": "",
            "sourceName": "Home", "targetName": "Disk", "progressAccuracy": "exact", "canCancel": True,
            "lastSuccessAt": "", "lastAttemptAt": "", "lastAttemptState": "", "startedAt": "", "updatedAt": "",
            "sourceProgress": 50, "overallProgress": 50, "sourceIndex": 1, "sourceCount": 1, "etaSeconds": -1}
        if status_reads == 1:
            bus.emit_signal(None, OBJECT_PATH, BUS_NAME, "StatusChanged", GLib.Variant("(s)", ("home",)))
    elif method == "GetDeviceState":
        value = {"schemaVersion": 1, "state": "connected", "connected": True,
            "mounted": False, "unlocked": False, "safeToRemove": False}
    elif method == "UpdateProfileSettings":
        value = {"schemaVersion": 3, "document": {}, "generation": "new", "fingerprint": "new-fingerprint"}
    elif method == "GenerateTargetKey":
        fd = invocation.get_message().get_unix_fd_list().get(arguments[1])
        try:
            received_secrets.append(os.read(fd, 4096))
        finally:
            os.close(fd)
        value = {"schemaVersion": 1, "accepted": True}
    elif method == "ResolveBackupCoverageByFd":
        fd = invocation.get_message().get_unix_fd_list().get(arguments[0])
        try:
            assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_PATH
            received_paths.append(os.fstat(fd).st_ino)
        finally:
            os.close(fd)
        value = [{"profileId": "home", "sourceId": "home", "relativePath": "."}]
    elif method == "OpenBrowseEntry":
        fd = os.memfd_create("fake-backup", os.MFD_CLOEXEC)
        os.write(fd, b"authorized contents")
        os.lseek(fd, 0, os.SEEK_SET)
        descriptors = Gio.UnixFDList.new()
        handle = descriptors.append(fd)
        os.close(fd)
        invocation.return_value_with_unix_fd_list(GLib.Variant("(h)", (handle,)), descriptors)
        return
    else:
        value = {"schemaVersion": 1, "accepted": True}
    invocation.return_value(GLib.Variant("(s)", (json.dumps(value),)))


registration = connection.register_object(OBJECT_PATH, interface, method_call, None, None)
owner = Gio.bus_own_name_on_connection(connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
client = Manager(lambda *_args: None, connection=connection)
try:
    wait_for(lambda: client.runs.get("home", {}).get("runId") == "run-2")
    assert status_reads == 2, "An invalidation during the first read must receive one follow-up"
    assert client.profiles[0]["profileId"] == "home"
    done = []
    client.request("CancelBackup", ("home", "run-2"), done.append)
    wait_for(lambda: bool(done))
    assert ("CancelBackup", ("home", "run-2")) in calls, "Cancellation must include the observed run ID"
    done.clear()
    client.request("UpdateProfileSettings", ("home", "generation", "fingerprint", '{"name":"Home"}'), done.append)
    wait_for(lambda: bool(done))
    assert done[0]["schemaVersion"] == 3, "Profile mutations return profile revision envelopes"

    def send_secret():
        with secret_fd("test recovery secret") as fd:
            return client.call("GenerateTargetKey", "home", fd, "Recovery key", False)

    done.clear()
    client.submit(send_secret, done.append)
    wait_for(lambda: bool(done))
    assert received_secrets == [b"test recovery secret"], "Secrets must cross D-Bus only through descriptors"

    with tempfile.TemporaryDirectory(prefix="gnome-coverage-test-") as temporary:
        def coverage():
            with path_fd(temporary) as fd:
                return client.call("ResolveBackupCoverageByFd", fd)
        done.clear()
        client.submit(coverage, done.append)
        wait_for(lambda: bool(done))
        assert received_paths == [os.stat(temporary).st_ino]

    def open_entry():
        fd = client.call("OpenBrowseEntry", "session", "snapshots/file")
        try:
            return os.read(fd, 4096)
        finally:
            os.close(fd)

    done.clear()
    client.submit(open_entry, done.append)
    wait_for(lambda: bool(done))
    assert done == [b"authorized contents"]
    Gio.bus_unown_name(owner)
    owner = None
    wait_for(lambda: not client.ready)
    assert not client.profiles and not client.runs and not client.targets
    capability["apiMajor"] = 2
    owner = Gio.bus_own_name_on_connection(connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
    wait_for(lambda: "incompatible" in client.error)
    assert not client.ready, "An incompatible replacement manager must disable operations"
finally:
    client.close()
    if owner is not None:
        Gio.bus_unown_name(owner)
    connection.unregister_object(registration)

profile = {"enabled": True}
target = {"connected": True, "mounted": True, "unlocked": True}
run = {"state": "running", "runId": "run-1", "canCancel": True}
assert actions(profile, run, target, {"cancel-backup", "eject-target"})["CancelBackup"]
assert not actions(profile, run, target, {"cancel-backup", "eject-target"})["EjectTarget"]
assert not storage_block({**target, "storage": {"schemaVersion": 5}}, {"target-storage-usage"})
validate_document("UpdateProfileSettings", {"schemaVersion": 3, "document": {}, "generation": "new", "fingerprint": "new-fingerprint"})
for path in ("../outside", "/absolute", "a/../b", "a//b", "a/./b"):
    try:
        confined_path(path)
    except ValueError:
        continue
    raise AssertionError("Unsafe repository path accepted")
print("GNOME client contracts passed")
