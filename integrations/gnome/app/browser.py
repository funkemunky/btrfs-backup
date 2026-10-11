# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

import contextlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import uuid

from gi.repository import Adw, Gio, GLib, Gtk

from manager import path_fd
from protocol import confined_path, format_bytes
from widgets import button, confirm, entry, group, page, row


class BrowseSession:
    def __init__(self, manager, profile_id):
        self.manager = manager
        self.profile_id = profile_id
        self.session_id = None
        self.lock = threading.RLock()
        self.owner = None

    @contextlib.contextmanager
    def operation(self):
        with self.lock:
            if not self.session_id:
                value = self.manager.call("OpenBrowseSession", self.profile_id)
                if (value.get("profileId") != self.profile_id or value.get("readOnly") is not True
                        or not value.get("sessionId")):
                    raise ValueError("Invalid authorized browse session")
                self.session_id = value["sessionId"]
                self.owner = self.manager.owner
            if self.owner != self.manager.owner:
                raise RuntimeError("Backup manager restarted; reopen the browser")
            self.manager.call("RenewBrowseSession", self.session_id)
            lease = self.manager.call("BeginBrowseOperation", self.session_id)["leaseId"]
            try:
                yield self.session_id
            finally:
                self.manager.call("EndBrowseOperation", self.session_id, lease)

    def close(self):
        with self.lock:
            if self.session_id and self.owner == self.manager.owner:
                try:
                    self.manager.call("CloseBrowseSession", self.session_id)
                finally:
                    self.session_id = None


class BrowserView:
    def __init__(self, window, profile, coverage=None):
        self.window = window
        self.manager = window.manager
        self.profile = profile
        self.coverage = coverage
        self.page, self.box = page(f"Backups: {profile['name']}")
        self.session = BrowseSession(self.manager, profile["profileId"])
        self.closed = False
        self.restoring = False
        self.process = None
        self.cancelled = threading.Event()
        self.catalog = None
        self.snapshot = None
        self.path = "."
        self.revision = 0
        self.token = ""
        self.preview_root = tempfile.TemporaryDirectory(prefix="btrfs-backup-preview-")
        self.progress_group = group(self.box, "Restore")
        self.progress_row = row(self.progress_group, "Choose an entry to restore")
        self.progress_row.add_suffix(button("Cancel restore", self.cancel_restore))
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.box.append(self.content)
        self.window.manager.submit(self.load_catalog, self.show_catalog, self.window.toast)

    def load_catalog(self):
        with self.session.operation() as session:
            return self.manager.call("InspectBrowseRepository", session)

    def clear(self):
        while self.content.get_first_child():
            self.content.remove(self.content.get_first_child())

    def show_catalog(self, value):
        if self.closed:
            return
        self.catalog = value
        for snapshot in value["snapshots"]:
            confined_path(snapshot["relativePath"])
        if self.coverage:
            self.load_versions()
            return
        self.clear()
        snapshots = group(self.content, "Snapshots")
        for snapshot in sorted(value["snapshots"], key=lambda s: s["createdAt"], reverse=True):
            if snapshot["profileId"] == self.profile["profileId"]:
                row(snapshots, snapshot["createdAt"], snapshot["sourceId"],
                    lambda s=snapshot: self.open_snapshot(s))

    def open_snapshot(self, snapshot, relative="."):
        self.snapshot = snapshot
        confined_path(relative)
        self.path = snapshot["relativePath"] + ("/" + relative if relative != "." else "")
        self.load_directory()

    def load_versions(self, token=""):
        coverage = self.coverage
        self.revision += 1
        revision = self.revision

        def read():
            with self.session.operation() as session:
                return self.manager.call("ListPreviousVersions", session, coverage["profileId"],
                    coverage["sourceId"], confined_path(coverage["relativePath"]), token, 100)

        def show(value):
            if self.closed or revision != self.revision:
                return
            self.clear()
            versions = group(self.content, "Previous versions", coverage["relativePath"])
            for version in value["entries"]:
                snapshot = next((s for s in self.catalog["snapshots"] if s["snapshotId"] == version["snapshotId"]), None)
                if snapshot:
                    row(versions, version["createdAt"], f"{version['kind']} · {format_bytes(version.get('size'))}",
                        lambda s=snapshot, v=version: self.open_version(s, v))
            if not value["entries"]:
                row(versions, "No previous versions found")
            if token:
                self.content.append(button("First page", self.load_versions))
            if value.get("continuationToken"):
                self.content.append(button("More versions", lambda: self.load_versions(value["continuationToken"])))

        self.manager.submit(read, show, self.window.toast)

    def open_version(self, snapshot, version):
        relative = confined_path(self.coverage["relativePath"])
        self.snapshot = snapshot
        path = snapshot["relativePath"] + ("/" + relative if relative != "." else "")
        if version["kind"] == "directory":
            self.path = path
            self.load_directory()
        else:
            self.restore_dialog(path)

    def load_directory(self, token=""):
        self.revision += 1
        revision = self.revision
        path = confined_path(self.path)

        def read():
            with self.session.operation() as session:
                return self.manager.call("ListBrowseDirectoryPage", session, path, token, 100)

        def show(value):
            if self.closed or revision != self.revision:
                return
            self.clear()
            controls = Gtk.Box(spacing=6)
            controls.append(button("Snapshots" if not self.coverage else "Versions",
                lambda: self.show_catalog(self.catalog)))
            if path != self.snapshot["relativePath"]:
                controls.append(button("Parent folder", lambda: self.parent_directory(path)))
            controls.append(button("Restore this folder", lambda: self.restore_dialog(path)))
            self.content.append(controls)
            entries = group(self.content, "Files", path)
            for item in value["entries"]:
                name = confined_path(item["name"])
                if "/" in name or name == ".":
                    raise ValueError("Invalid directory entry")
                child_path = path + "/" + name
                widget = row(entries, name, f"{item['kind']} · {format_bytes(item.get('size'))}")
                if item["kind"] == "directory":
                    widget.add_suffix(button("Browse", lambda p=child_path: self.child_directory(p)))
                elif item["kind"] == "file":
                    widget.add_suffix(button("Open", lambda p=child_path: self.preview(p)))
                widget.add_suffix(button("Restore", lambda p=child_path: self.restore_dialog(p)))
            if token:
                self.content.append(button("First page", self.load_directory))
            if value.get("continuationToken"):
                self.content.append(button("More entries", lambda: self.load_directory(value["continuationToken"])))

        self.manager.submit(read, show, self.window.toast)

    def child_directory(self, path):
        self.path = path
        self.load_directory()

    def parent_directory(self, path):
        self.path = path.rsplit("/", 1)[0]
        self.load_directory()

    def preview(self, path):
        def read():
            with self.session.operation() as session:
                fd = self.manager.call("OpenBrowseFile", session, confined_path(path))
                with os.fdopen(fd, "rb") as source:
                    if os.fstat(source.fileno()).st_size > 64 * 1024 * 1024:
                        raise ValueError("Restore this file to open it; previews are limited to 64 MiB")
                    directory = Path(tempfile.mkdtemp(dir=self.preview_root.name))
                    destination = directory / Path(path).name
                    with destination.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    destination.chmod(0o400)
                    return destination.as_uri()

        self.manager.submit(read, lambda uri: Gio.AppInfo.launch_default_for_uri(uri, None), self.window.toast)

    def restore_dialog(self, path):
        if self.restoring:
            return
        path = confined_path(path)
        dialog = Adw.AlertDialog(heading="Restore backup", body="Choose the full destination path. Existing files are preserved unless you confirm replacement.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        fields = group(box, "Destination")
        name = Path(path).name
        destination = entry(fields, "Full path", str(Path.home() / "Downloads" / name))
        box.append(button("Choose parent folder", lambda: self.choose_destination(destination, name)))
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.SUGGESTED)
        snapshot = self.snapshot

        def response(_dialog, answer):
            if answer != "restore":
                return
            target = destination.get_text()
            if not os.path.isabs(target):
                self.window.toast("The restore destination must be an absolute path")
                return
            if os.path.lexists(target):
                confirm(self.window, "Replace the existing destination?", target,
                    lambda: self.restore(snapshot, path, target, True), "Replace", True)
            else:
                self.restore(snapshot, path, target, False)

        dialog.connect("response", response)
        dialog.present(self.window)

    def choose_destination(self, destination, name):
        chooser = Gtk.FileDialog(title="Choose the restore destination folder")

        def selected(_chooser, result):
            try:
                folder = chooser.select_folder_finish(result).get_path()
                destination.set_text(str(Path(folder) / name))
            except Exception:
                pass

        chooser.select_folder(self.window, None, selected)

    def restore(self, snapshot, path, destination, replace):
        if self.closed or self.restoring:
            return
        self.restoring = True
        self.cancelled.clear()
        self.progress_row.set_title("Checking space and restoring…")
        relative = path[len(snapshot["relativePath"]):].lstrip("/") or "."
        request = {"catalog": self.catalog, "snapshotId": snapshot["snapshotId"],
                   "relativePath": relative, "destination": destination,
                   "replace": replace, "transactionId": str(uuid.uuid4())}

        def progress(value):
            if not self.closed:
                self.progress_row.set_subtitle(f"{value.get('phase', '')} · "
                    f"{format_bytes(value.get('bytes'))} · {value.get('files', 0)} files · "
                    f"{value.get('name', '')}")
            return GLib.SOURCE_REMOVE

        def execute():
            with self.session.operation() as session:
                fd = self.manager.call("OpenBrowseEntry", session, path)
                try:
                    request["sourceFd"] = fd
                    helper = os.environ.get("BTRFS_BACKUP_GNOME_RESTORE_HELPER", "btrfs-backup-gnome-restore")
                    process = subprocess.Popen([helper], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, pass_fds=(fd,), text=True)
                    self.process = process
                    process.stdin.write(json.dumps(request) + "\n")
                    process.stdin.close()
                    if self.cancelled.is_set():
                        process.send_signal(signal.SIGTERM)
                    result = None
                    for line in process.stdout:
                        value = json.loads(line)
                        if value.get("event") == "result":
                            result = value
                        else:
                            GLib.idle_add(progress, value)
                    process.wait()
                    if not result or not result.get("committed"):
                        raise RuntimeError((result or {}).get("error", "Restore helper failed"))
                    return result
                finally:
                    self.process = None
                    os.close(fd)

        def finished(result=None, error=None):
            self.restoring = False
            if self.closed:
                return
            self.progress_row.set_title("Restore failed" if error else "Restore completed")
            self.progress_row.set_subtitle(error or f"{result['files']} files · {format_bytes(result['bytes'])} · {destination}")
            self.window.toast(error or "Restore completed")
            if not error:
                notification = Gio.Notification.new("Restore completed")
                notification.set_body(f"Restored to {destination}")
                self.window.get_application().send_notification("restore", notification)
                self.progress_row.add_suffix(button("Open location", lambda: Gio.AppInfo.launch_default_for_uri(
                    Path(destination if os.path.isdir(destination) else os.path.dirname(destination)).as_uri(), None)))

        self.manager.submit(execute, finished, lambda error: finished(error=error))

    def cancel_restore(self):
        self.cancelled.set()
        process = self.process
        if process and process.poll() is None:
            try:
                process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass

    def close(self):
        self.closed = True
        self.cancel_restore()
        self.manager.submit(self.session.close, lambda _value: None)
        self.preview_root.cleanup()


class PreviousVersionsView:
    def __init__(self, window, path):
        self.window = window
        self.page, self.box = page("Previous backup versions")
        self.closed = False

        def resolve():
            with path_fd(path) as fd:
                return window.manager.call("ResolveBackupCoverageByFd", fd)

        def show(coverage):
            if self.closed:
                return
            profiles = group(self.box, "Backup coverage", path)
            for item in coverage:
                profile = next((p for p in window.manager.profiles if p["profileId"] == item["profileId"]), None)
                if profile:
                    confined_path(item["relativePath"])
                    row(profiles, profile["name"], item["sourceId"],
                        lambda p=profile, c=item: window.push(BrowserView(window, p, c)))
            if not coverage:
                row(profiles, "This entry is not covered by a backup profile")

        window.manager.submit(resolve, show, window.toast)

    def close(self):
        self.closed = True
