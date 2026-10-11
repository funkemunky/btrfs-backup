# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

import json

from gi.repository import GLib, Gtk

from manager import secret_fd
from protocol import format_bytes
from widgets import button, choose, confirm, entry, group, page, row, spin, switch


MODES = (
    ("erase-whole-device", "Erase a whole disk"),
    ("reformat-existing-partition", "Reformat an existing partition"),
    ("create-partition-in-unallocated-space", "Create a partition in free space"),
    ("adopt-existing-target", "Adopt a compatible backup repository"),
)


class ProvisioningView:
    def __init__(self, window):
        self.window = window
        self.manager = window.manager
        self.page, self.box = page("Set up a backup device")
        self.closed = False
        self.pending = False
        self.plan = None
        self.inspection = None
        self.operation = None
        self.topology = None
        self.candidates = []
        self.source_fields = []
        self.timer = 0
        fields = group(self.box, "Device preparation",
            "Select a disk, partition or free region, then review the exact changes before starting.")
        self.mode = choose(fields, "Mode", [label for _mode, label in MODES])
        self.selection = choose(fields, "Target", [])
        self.mode.connect("notify::selected", lambda *_args: self.update_candidates())
        self.selection.connect("notify::selected", lambda *_args: self.invalidate_plan())
        self.box.append(button("Refresh storage layout", self.refresh))
        profile = group(self.box, "New profile")
        self.profile_id = entry(profile, "Profile identifier")
        self.profile_name = entry(profile, "Profile name")
        self.passphrase = entry(profile, "Recovery passphrase", password=True)
        self.confirmation = entry(profile, "Confirm recovery passphrase", password=True)
        self.automatic_key = switch(profile, "Create a key for automatic unlocking", True)
        self.sources_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.append(self.sources_box)
        self.box.append(button("Review preparation plan", self.build_plan))
        self.plan_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.append(self.plan_box)
        self.start_button = button("Apply reviewed plan", self.start)
        self.start_button.set_sensitive(False)
        self.box.append(self.start_button)
        self.status_group = group(self.box, "Preparation status")
        self.status_row = row(self.status_group, "Not started")
        self.cancel_button = button("Cancel preparation", self.cancel, True)
        self.cancel_button.set_sensitive(False)
        self.box.append(self.cancel_button)
        self.refresh()

    def refresh(self):
        if self.pending or self.operation:
            return
        self.pending = True

        def work():
            return (self.manager.call("InspectStorageTopology"), self.manager.call("ListSourceCandidates"))

        def show(values):
            self.pending = False
            if self.closed:
                return
            self.topology, sources = values
            self.update_candidates()
            while self.sources_box.get_first_child():
                self.sources_box.remove(self.sources_box.get_first_child())
            self.source_fields = []
            for source in sources:
                fields = group(self.sources_box, source["path"])
                selected = switch(fields, "Back up this subvolume", False)
                name = entry(fields, "Source name", source["path"].strip("/").replace("/", "-") or "system")
                local = spin(fields, "Local retention", 3, 1)
                remote = spin(fields, "Target retention (0 keeps all)", 30)
                self.source_fields.append((source, selected, name, local, remote))

        self.manager.submit(work, show, self.failed)

    def invalidate_plan(self):
        self.plan = None
        self.inspection = None
        self.start_button.set_sensitive(False)
        while self.plan_box.get_first_child():
            self.plan_box.remove(self.plan_box.get_first_child())

    def update_candidates(self):
        self.invalidate_plan()
        self.candidates = []
        if not self.topology:
            return
        mode = MODES[self.mode.get_selected()][0]
        labels = []
        for disk in self.topology["devices"]:
            label = f"Disk {disk['displayIndex']} · {disk['transport']} · {format_bytes(disk['sizeBytes'])}"
            if mode == "erase-whole-device":
                if not disk["systemDevice"] and not disk["readOnly"] and not disk["mounted"] and not disk["blockers"]:
                    self.candidates.append(disk)
                    labels.append(label)
            else:
                field = {"reformat-existing-partition": "suitableForReformat",
                         "create-partition-in-unallocated-space": "suitableForBackupPartition",
                         "adopt-existing-target": "suitableForAdoption"}[mode]
                for region in disk["regions"]:
                    if region.get(field) and not region["blockers"]:
                        self.candidates.append(region)
                        labels.append(f"{label} · {region['kind']} {region.get('partitionNumber', '')} · "
                            f"{format_bytes(region['sectorCount'] * disk['logicalSectorSize'])}")
        self.selection.set_model(Gtk.StringList.new(labels))
        if not labels:
            self.window.toast("No eligible targets for this mode. Mounted, system and blocked storage cannot be prepared.")

    def build_plan(self):
        index = self.selection.get_selected()
        if self.pending or index >= len(self.candidates):
            return
        self.pending = True
        candidate = self.candidates[index]
        mode = MODES[self.mode.get_selected()][0]
        generation = self.topology["generation"]
        passphrase = self.passphrase.get_text()
        request = {"topologyGeneration": generation, "candidateId": candidate["candidateId"],
                   "mode": mode, "inspectionId": ""}

        def work():
            if mode == "adopt-existing-target":
                with secret_fd(passphrase) as secret:
                    inspection = self.manager.call("InspectExistingTarget", json.dumps({
                        "topologyGeneration": generation, "candidateId": candidate["candidateId"]}), secret)
                if (inspection.get("classification") != "compatible-repository"
                        or inspection.get("topologyGeneration") != generation
                        or inspection.get("partitionId") != candidate["candidateId"]):
                    raise ValueError("This partition does not contain a compatible repository: " +
                                     inspection.get("diagnosticCode", inspection.get("classification", "unknown")))
                request["inspectionId"] = inspection["inspectionId"]
            return self.manager.call("BuildDevicePreparationPlan", json.dumps(request))

        def show(value):
            self.pending = False
            if self.closed:
                return
            # A selection change during the request invalidates the returned plan.
            if self.selection.get_selected() != index or MODES[self.mode.get_selected()][0] != mode:
                return
            self.plan = value
            while self.plan_box.get_first_child():
                self.plan_box.remove(self.plan_box.get_first_child())
            for key, title in (("before", "Current layout"), ("after", "Planned layout")):
                layout = value[key]
                rows = group(self.plan_box, title,
                    f"{layout['partitionTableType']} · {format_bytes(layout['sizeBytes'])}")
                for region in layout["regions"]:
                    row(rows, f"{region['kind']} · partition {region['partitionNumber']}",
                        f"{format_bytes(region['sectorCount'] * layout['logicalSectorSize'])} · "
                        f"{'data will be erased' if region['dataWillBeErased'] else 'data retained'} · "
                        f"{'exact geometry' if region['geometryExact'] else 'estimated geometry'}")
            review = group(self.plan_box, "Operations", "Affected scope: " + value["destructiveScope"])
            for operation in value["operations"]:
                row(review, operation.replace("-", " "))
            for warning in value["warnings"]:
                row(review, "Warning", warning["code"])
            self.start_button.set_sensitive(True)

        self.manager.submit(work, show, self.failed)

    def start(self):
        if not self.plan or self.pending or self.operation:
            return
        if self.passphrase.get_text() != self.confirmation.get_text():
            self.window.toast("The recovery passphrases do not match")
            return
        sources = [{"candidateId": source["id"], "name": name.get_text().strip(),
                    "localRetention": int(local.get_value()), "remoteRetention": int(remote.get_value())}
                   for source, selected, name, local, remote in self.source_fields if selected.get_active()]
        if not sources:
            self.window.toast("Select at least one source subvolume")
            return
        plan = self.plan
        request = {"planId": plan["planId"], "profileId": self.profile_id.get_text().strip(),
                   "profileName": self.profile_name.get_text().strip(), "sources": sources,
                   "passphraseLabel": "Recovery passphrase", "createAutomaticKey": self.automatic_key.get_active()}
        secret = self.passphrase.get_text()

        def apply():
            if self.plan is not plan:
                self.window.toast("The selection changed. Review the new preparation plan.")
                return
            self.pending = True
            self.start_button.set_sensitive(False)
            self.passphrase.set_text("")
            self.confirmation.set_text("")

            def work():
                with secret_fd(secret) as fd:
                    return self.manager.call("StartDevicePreparation", json.dumps(request), fd)

            self.manager.submit(work, self.show_operation, self.failed)

        destructive = plan["mode"] != "adopt-existing-target"
        confirm(self.window, "Apply the reviewed storage plan?",
            "The selected storage will be changed as shown in the plan. Scope: " + plan["destructiveScope"],
            apply, "Prepare device" if destructive else "Adopt repository", destructive)

    def show_operation(self, value):
        self.pending = False
        if self.closed:
            return
        self.operation = value
        self.status_row.set_title(value["state"])
        self.status_row.set_subtitle(f"{value['phase']} · {value['errorCode']}\n"
            f"Last completed phase: {value['lastCompletedPhase']} · cleanup: {value['cleanupResult']}\n"
            f"Recovery: {value['recoveryAction']}")
        self.cancel_button.set_sensitive(value["canCancel"])
        if value["state"] in {"succeeded", "failed", "cancelled"}:
            self.manager.refresh("profiles")
            self.window.toast("Device preparation " + value["state"])
        elif not self.timer:
            self.timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, self.poll)

    def poll(self):
        self.timer = 0
        if not self.closed and not self.pending and self.operation:
            self.pending = True
            self.manager.request("GetDevicePreparation", (self.operation["operationId"],),
                                 self.show_operation, self.failed)
        return GLib.SOURCE_REMOVE

    def cancel(self):
        if self.operation and self.operation["canCancel"]:
            self.manager.request("CancelDevicePreparation", (self.operation["operationId"],),
                                 lambda _value: self.poll(), self.failed)

    def failed(self, error):
        self.pending = False
        if not self.closed:
            self.window.toast(error)

    def close(self):
        self.closed = True
        if self.timer:
            GLib.Source.remove(self.timer)
        self.passphrase.set_text("")
        self.confirmation.set_text("")
