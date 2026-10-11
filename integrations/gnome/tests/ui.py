# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK surfaces with presentation fixtures; all storage mutations stay fake."""

import json
from pathlib import Path
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import window as window_module
from browser import BrowserView, PreviousVersionsView
from credentials import CredentialsView
from provisioning import ProvisioningView

Adw.init()
calls = []
profile = {"schemaVersion": 1, "profileId": "home", "name": "Home", "enabled": True,
           "targetName": "Backup disk", "sources": [{"id": "home", "name": "Home"}]}
snapshot = {"snapshotId": "snapshot", "profileId": "home", "sourceId": "home", "relativePath": "snapshots/home",
            "createdAt": "2026-10-01T00:00:00Z"}
layout = {"partitionTableType": "gpt", "sizeBytes": 100000000, "logicalSectorSize": 512,
          "regions": [{"kind": "existing-partition", "partitionNumber": 1, "sectorCount": 10000,
                       "dataWillBeErased": True, "geometryExact": True}]}


class FakeManager:
    def __init__(self, changed):
        self.ready = True
        self.owner = "fake"
        self.error = ""
        self.features = {"profiles", "status", "sanitized-history", "device-state", "target-storage-usage",
            "start-backup", "cancel-backup", "validate-target", "eject-target", "change-signals",
            "browse-backups", "profile-administration", "profile-details", "profile-activation",
            "target-credentials", "device-provisioning"}
        self.profiles = [profile]
        self.runs = {"home": {"state": "running", "runId": "run-123", "canCancel": True,
            "overallProgress": 50, "progressAccuracy": "exact", "phase": "transferring", "sourceName": "Home"}}
        self.targets = {"home": {"state": "mounted", "connected": True, "unlocked": True,
            "mounted": True, "safeToRemove": False}}

    def call(self, method, *arguments):
        calls.append((method, arguments))
        if method == "GetProfileDetails":
            return {"schemaVersion": 3, "generation": "generation-a", "fingerprint": "fingerprint-a",
                "document": {"name": "Home", "settings": {"dailyLimit": True, "autoEject": True},
                    "sources": [{"id": "home", "name": "Home", "subvolume": "/home", "localRetention": 3, "remoteRetention": 30}],
                    "target": {"activation": {"mode": "keyFile"}}}}
        if method in {"GetHistorySanitized", "ListTargetCredentials"}:
            return []
        if method == "OpenBrowseSession":
            return {"sessionId": "session", "profileId": "home", "readOnly": True}
        if method == "BeginBrowseOperation":
            return {"leaseId": "lease"}
        if method == "InspectBrowseRepository":
            return {"snapshots": [snapshot]}
        if method == "ListBrowseDirectoryPage":
            return {"entries": [{"name": "file.txt", "kind": "file", "size": 100}], "continuationToken": ""}
        if method == "ResolveBackupCoverageByFd":
            return [{"profileId": "home", "sourceId": "home", "relativePath": "."}]
        if method == "InspectStorageTopology":
            return {"generation": "topology-a", "devices": [{"candidateId": "disk", "displayIndex": 1,
                "transport": "usb", "sizeBytes": 100000000, "logicalSectorSize": 512,
                "systemDevice": False, "readOnly": False, "mounted": False, "blockers": [], "regions": []}]}
        if method == "ListSourceCandidates":
            return [{"id": "source-candidate", "path": "/home"}]
        if method == "BuildDevicePreparationPlan":
            return {"planId": "plan-a", "mode": "erase-whole-device", "before": layout, "after": layout,
                    "destructiveScope": "whole-device", "operations": ["format-luks2"], "warnings": []}
        return {"schemaVersion": 1, "accepted": True}

    def submit(self, work, success, failure=None):
        try:
            success(work())
        except Exception as error:
            if failure:
                failure(str(error))
            raise

    def request(self, method, arguments, success, failure=None):
        self.submit(lambda: self.call(method, *arguments), success, failure)

    def refresh(self, *_arguments):
        pass

    def close(self):
        pass


window_module.Manager = FakeManager
application = Adw.Application(application_id="io.github.btrfsbackup.Gnome.UiTest", flags=Gio.ApplicationFlags.NON_UNIQUE)
application.register(None)
window = window_module.BackupWindow(application)
window.present()
window.open_profile(profile)
view = window.profile_view
assert view.buttons["CancelBackup"].get_sensitive()
assert not view.buttons["EjectTarget"].get_sensitive()
view.operate("CancelBackup")
assert ("CancelBackup", ("home", "run-123")) in calls
view.name.set_text("New profile name")
view.save_settings()
write = next(args for method, args in calls if method == "UpdateProfileSettings")
assert write[:3] == ("home", "generation-a", "fingerprint-a")
assert json.loads(write[3])["name"] == "New profile name"
browser = BrowserView(window, profile)
window.push(browser)
browser.open_snapshot(snapshot)
assert any(method == "ListBrowseDirectoryPage" for method, _args in calls)
assert any(method == "EndBrowseOperation" for method, _args in calls)
credentials = CredentialsView(window, "home")
window.push(credentials)
credentials.authorization.set_text("secret")
credentials.label.set_text("Recovery key")
credentials.mutate("GenerateTargetKey")
assert not credentials.authorization.get_text()
wizard = ProvisioningView(window)
window.push(wizard)
wizard.build_plan()
assert wizard.plan["planId"] == "plan-a" and wizard.start_button.get_sensitive()
wizard.mode.set_selected(1)
assert wizard.plan is None and not wizard.start_button.get_sensitive(), "Selection changes must invalidate destructive plans"
versions = PreviousVersionsView(window, str(Path(__file__).parent))
window.push(versions)
context = GLib.MainContext.default()
for _iteration in range(30):
    while context.pending():
        context.iteration(False)
for item in list(window.views):
    item.close()
assert any(method == "CloseBrowseSession" for method, _args in calls)
window.manager.close()
window.destroy()
print("GTK profile, browse, credential, provisioning and previous-version checks passed")
