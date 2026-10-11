# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

import concurrent.futures
import contextlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from gi.repository import Gio, GLib

from protocol import BUS_NAME, OBJECT_PATH, validate_capabilities, validate_document


@contextlib.contextmanager
def secret_fd(text):
    secret = text.encode("utf-8")
    if not 1 <= len(secret) <= 4096 or b"\0" in secret:
        raise ValueError("A passphrase must contain 1 to 4096 bytes")
    fd = os.memfd_create("btrfs-backup-secret", os.MFD_CLOEXEC)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, secret)
        os.lseek(fd, 0, os.SEEK_SET)
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def path_fd(path):
    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        yield fd
    finally:
        os.close(fd)


class Manager:
    """One caller identity for discovery, mutations, browse sessions and leases."""

    def __init__(self, changed, connection=None):
        self.changed = changed
        self.owner = None
        self.bus = connection or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        contract = Path(__file__).with_name("Manager1.xml")
        if not contract.exists():
            contract = Path(__file__).resolve().parents[3] / "data/dbus/io.github.btrfsbackup.Manager1.xml"
        interface = ET.parse(contract).getroot().find("interface")
        self.signatures = {}
        for method in interface.findall("method"):
            inputs = "".join(a.attrib["type"] for a in method.findall("arg") if a.get("direction") == "in")
            outputs = "".join(a.attrib["type"] for a in method.findall("arg") if a.get("direction") == "out")
            self.signatures[method.attrib["name"]] = (f"({inputs})", f"({outputs})")
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.features = set()
        self.ready = False
        self.closed = False
        self.epoch = 0
        self.profiles = []
        self.runs = {}
        self.targets = {}
        self.error = "Connecting to backup manager…"
        self.pending = set()
        self.dirty = set()
        self.signal = 0
        self.cancellable = Gio.Cancellable()
        self.watch = Gio.bus_watch_name_on_connection(self.bus, BUS_NAME,
            Gio.BusNameWatcherFlags.AUTO_START, self._appeared, self._vanished)

    def submit(self, work, success, failure=None):
        owner = self.owner
        epoch = self.epoch
        future = self.executor.submit(work)

        def completed(result):
            def deliver():
                if self.closed:
                    return GLib.SOURCE_REMOVE
                try:
                    value = result.result()
                    if owner != self.owner or epoch != self.epoch:
                        raise RuntimeError("Backup manager restarted; reload the current page")
                    success(value)
                except Exception as error:
                    if failure:
                        failure(str(error))
                    else:
                        self.error = str(error)
                        self.changed("error", None)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(deliver)

        future.add_done_callback(completed)
        return future

    def call(self, method, *arguments):
        if not self.owner:
            raise RuntimeError("Backup manager unavailable")
        owner = self.owner
        epoch = self.epoch
        inputs, outputs = self.signatures[method]
        fd_list = Gio.UnixFDList.new()
        encoded = [fd_list.append(value) if kind == "h" else value
                   for kind, value in zip(inputs[1:-1], arguments, strict=True)]
        result, received = self.bus.call_with_unix_fd_list_sync(
            owner, OBJECT_PATH, BUS_NAME, method, GLib.Variant(inputs, tuple(encoded)),
            GLib.VariantType.new(outputs), Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
            120000, fd_list, self.cancellable)
        if owner != self.owner or epoch != self.epoch:
            raise RuntimeError("Backup manager restarted")
        value = result.unpack()[0]
        if outputs == "(h)":
            return received.get(value)
        return validate_document(method, json.loads(value))

    def request(self, method, arguments, success, failure=None):
        return self.submit(lambda: self.call(method, *arguments), success, failure)

    def _appeared(self, _bus, _name, owner):
        self._reset()
        self.owner = owner
        self.cancellable = Gio.Cancellable()

        def connected(value):
            self.features = validate_capabilities(value)
            if not {"profiles", "status", "device-state", "change-signals"} <= self.features:
                raise ValueError("Backup manager lacks required desktop capabilities")
            self.ready = True
            self.signal = self.bus.signal_subscribe(owner, BUS_NAME, None, OBJECT_PATH, None,
                Gio.DBusSignalFlags.NONE, self._signal)
            self.refresh("profiles")

        self.request("GetCapabilities", (), connected)

    def _vanished(self, _bus, _name):
        self._reset()
        self.error = "Backup manager unavailable"
        self.changed("connection", None)

    def _reset(self):
        self.epoch += 1
        self.cancellable.cancel()
        if self.signal:
            self.bus.signal_unsubscribe(self.signal)
        self.signal = 0
        self.owner = None
        self.ready = False
        self.profiles = []
        self.runs.clear()
        self.targets.clear()
        self.pending.clear()
        self.dirty.clear()

    def _signal(self, _bus, _sender, _path, _interface, signal, params):
        arguments = params.unpack()
        if signal == "ProfilesChanged":
            self.refresh("profiles")
            self.changed("configuration", None)
        elif signal == "StatusChanged":
            self.refresh("status", arguments[0])
        elif signal == "DeviceStateChanged":
            self.refresh("device", arguments[0])
        elif signal == "HistoryChanged":
            self.changed("history", arguments[0])

    def refresh(self, kind, profile=None):
        if not self.ready:
            return
        key = (kind, profile)
        if key in self.pending:
            self.dirty.add(key)
            return
        self.pending.add(key)
        epoch = self.epoch
        method = {"profiles": "ListProfiles", "status": "GetStatus", "device": "GetDeviceState"}[kind]

        def finished(value=None, error=None):
            if epoch != self.epoch:
                return
            self.pending.discard(key)
            if error:
                self.error = error
                if kind in {"status", "device"}:
                    (self.runs if kind == "status" else self.targets).pop(profile, None)
                elif kind == "profiles":
                    self.profiles = []
                    self.runs.clear()
                    self.targets.clear()
            else:
                self.error = ""
                if kind == "profiles":
                    self.profiles = value
                    ids = {p["profileId"] for p in value}
                    self.runs = {k: v for k, v in self.runs.items() if k in ids}
                    self.targets = {k: v for k, v in self.targets.items() if k in ids}
                    for p in value:
                        if p.get("configurationErrorCode") != "configuration.unsupported-schema":
                            self.refresh("status", p["profileId"])
                            self.refresh("device", p["profileId"])
                elif any(p["profileId"] == profile for p in self.profiles):
                    (self.runs if kind == "status" else self.targets)[profile] = value
            self.changed(kind, profile)
            if key in self.dirty:
                self.dirty.discard(key)
                self.refresh(kind, profile)

        self.request(method, () if profile is None else (profile,), finished,
                     lambda message: finished(error=message))

    def close(self):
        self.closed = True
        Gio.bus_unwatch_name(self.watch)
        self._reset()
        self.executor.shutdown(wait=False, cancel_futures=True)
