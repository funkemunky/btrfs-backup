# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

import json

from gi.repository import Adw, Gio, Gtk

from manager import Manager, path_fd
from protocol import ACTIVE, actions, format_bytes, storage_block
from widgets import button, confirm, entry, group, page, row, spin, switch


class BackupWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="Btrfs Backup",
                         default_width=900, default_height=760)
        self.overlay = Adw.ToastOverlay()
        self.navigation = Adw.NavigationView()
        toolbar = Adw.ToolbarView(content=self.navigation)
        toolbar.add_top_bar(Adw.HeaderBar())
        self.overlay.set_child(toolbar)
        self.set_content(self.overlay)
        self.home, self.home_box = page("Btrfs Backup")
        self.navigation.add(self.home)
        self.profile_view = None
        self.views = []
        self.command = None
        self.manager = Manager(self.changed)
        self.navigation.connect("popped", self._popped)
        self.connect("close-request", self._close)
        self.refresh_home()

    def toast(self, message):
        self.overlay.add_toast(Adw.Toast(title=str(message), timeout=8))

    def push(self, view):
        self.views.append(view)
        self.navigation.push(view.page)

    def _popped(self, _navigation, popped):
        for view in list(self.views):
            if view.page == popped:
                view.close()
                self.views.remove(view)
        if self.profile_view and self.profile_view.page == popped:
            self.profile_view = None

    def _close(self, _window):
        for view in self.views:
            if getattr(view, "restoring", False):
                view.cancel_restore()
                self.toast("Cancelling restore safely. Close the window when it finishes.")
                return True
        for view in list(self.views):
            view.close()
        self.manager.close()
        self.get_application().window = None
        return False

    def changed(self, kind, profile_id):
        if kind in {"profiles", "connection", "error"}:
            self.refresh_home()
        if self.profile_view:
            if kind == "configuration":
                self.profile_view.configuration_changed()
            elif kind in {"status", "device", "profiles", "connection", "error"}:
                self.profile_view.update_status()
            elif kind == "history" and profile_id == self.profile_view.profile_id:
                self.profile_view.load_history()
        if self.command and self.manager.ready and kind == "profiles":
            command, self.command = self.command, None
            self.open_command(command)

    def refresh_home(self):
        while self.home_box.get_first_child():
            self.home_box.remove(self.home_box.get_first_child())
        profiles = group(self.home_box, "Backup profiles", self.manager.error or
            "Profiles are managed by the system service. Backup data remains on the target when a profile is removed.")
        for profile in self.manager.profiles:
            subtitle = profile.get("configurationErrorCode") or profile.get("targetName", "")
            row(profiles, profile["name"], subtitle, lambda p=profile: self.open_profile(p))
        if not self.manager.profiles:
            row(profiles, "No profiles available")
        create = button("Set up a backup device", self.new_profile)
        create.set_sensitive(self.manager.ready and "device-provisioning" in self.manager.features)
        self.home_box.append(create)
        self.home_box.append(button("Notification preferences", lambda: Gio.Subprocess.new(
            ["gnome-extensions", "prefs", "btrfs-backup@btrfsbackup.github.io"], Gio.SubprocessFlags.NONE)))

    def open_command(self, arguments):
        if not self.manager.ready:
            self.command = arguments
            return
        if arguments.previous_versions:
            from browser import PreviousVersionsView
            self.push(PreviousVersionsView(self, arguments.previous_versions))
        elif arguments.profile or arguments.browse:
            profile = next((p for p in self.manager.profiles
                            if p["profileId"] == (arguments.profile or arguments.browse)), None)
            if not profile:
                self.toast("The requested profile no longer exists")
            elif arguments.browse:
                self.browse(profile)
            else:
                self.open_profile(profile)

    def open_profile(self, profile):
        self.navigation.pop_to_page(self.home)
        self.profile_view = ProfileView(self, profile)
        self.push(self.profile_view)

    def browse(self, profile):
        from browser import BrowserView
        self.push(BrowserView(self, profile))

    def new_profile(self):
        from provisioning import ProvisioningView
        self.push(ProvisioningView(self))


class ProfileView:
    def __init__(self, window, profile):
        self.window = window
        self.manager = window.manager
        self.profile = profile
        self.profile_id = profile["profileId"]
        self.page, self.box = page(profile["name"])
        self.closed = False
        self.pending = False
        self.envelope = None
        self.history_offset = 0
        self.history_pending = False
        self.history_dirty = False
        self.history_revision = 0
        self.status_group = group(self.box, "Current backup")
        self.run_row = row(self.status_group, "Loading status…")
        self.target_row = row(self.status_group, "Target")
        self.progress = Gtk.ProgressBar(show_text=True)
        self.box.append(self.progress)
        controls = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                               row_spacing=6, column_spacing=6)
        self.buttons = {}
        for method, label in (("StartBackup", "Start backup"), ("CancelBackup", "Cancel"),
                              ("ValidateTarget", "Validate target"), ("EjectTarget", "Eject")):
            widget = button(label, lambda m=method: self.operate(m))
            controls.insert(widget, -1)
            self.buttons[method] = widget
        browse = button("Browse and restore", lambda: self.window.browse(self.profile))
        controls.insert(browse, -1)
        self.buttons["browse"] = browse
        self.box.append(controls)
        activation = group(self.box, "Automatic activation")
        self.automatic = switch(activation, "Back up automatically when connected", profile["enabled"])
        self.automatic.connect("notify::active", self._automatic)
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.box.append(self.detail_box)
        self.history_group = group(self.box, "History")
        history_controls = Gtk.Box(spacing=6)
        history_controls.append(button("Previous page", lambda: self.history_page(-1)))
        history_controls.append(button("Next page", lambda: self.history_page(1)))
        self.box.append(history_controls)
        self.history_rows = []
        self.update_status()
        self.load_details()
        self.load_history()

    def update_status(self):
        current = next((p for p in self.manager.profiles if p["profileId"] == self.profile_id), None)
        if current:
            self.profile = current
        run = self.manager.runs.get(self.profile_id, {})
        target = self.manager.targets.get(self.profile_id, {})
        self.run_row.set_title(run.get("state", "Unknown"))
        self.run_row.set_subtitle(f"{run.get('phase', '')} · {run.get('sourceName', '')} "
            f"({run.get('sourceIndex', 0)}/{run.get('sourceCount', 0)}) · "
            f"Last success: {run.get('lastSuccessAt') or 'never'}\n"
            f"{format_bytes(run.get('bytesProcessed'))} / {format_bytes(run.get('bytesTotalEstimated'))} · "
            f"{format_bytes(run.get('speedBps'))}/s · ETA: {run.get('etaSeconds', -1)}s\n"
            f"{run.get('errorCode', '')}")
        self.target_row.set_title("Safe to remove" if target.get("safeToRemove") else target.get("state", "Unknown target state"))
        storage = storage_block(target, self.manager.features)
        self.target_row.set_subtitle(f"{format_bytes(storage['availableBytes'])} free of "
            f"{format_bytes(storage['capacityBytes'])} · {storage['usagePercent']}% used · "
            f"{'live' if storage['live'] else 'last measurement'}: {storage['measuredAt']}"
            if storage else "Storage measurement unavailable")
        percent = run.get("overallProgress", -1)
        determinate = percent >= 0 and run.get("progressAccuracy") != "indeterminate"
        self.progress.set_fraction(percent / 100 if determinate else 0)
        self.progress.set_text(f"{'~' if run.get('progressAccuracy') == 'estimated' else ''}{percent}%"
            if determinate else run.get("activity", "Waiting"))
        allowed = actions(self.profile, run, target, self.manager.features, self.pending or not self.manager.ready)
        for method, widget in self.buttons.items():
            widget.set_sensitive(bool(allowed[method]))
        self.automatic.freeze_notify()
        self.automatic.set_active(self.profile["enabled"])
        self.automatic.thaw_notify()
        self.automatic.set_sensitive(allowed["SetProfileEnabled"])

    def _automatic(self, _widget, _property):
        if self.automatic.get_active() != self.profile["enabled"] and not self.pending:
            self.operate("SetProfileEnabled")

    def operate(self, method):
        if self.pending:
            return
        run = self.manager.runs.get(self.profile_id, {})
        if not actions(self.profile, run, self.manager.targets.get(self.profile_id, {}),
                       self.manager.features, not self.manager.ready).get(method):
            return
        arguments = [self.profile_id]
        if method == "CancelBackup":
            arguments.append(run["runId"])
        elif method == "SetProfileEnabled":
            arguments.append(not self.profile["enabled"])
        self.pending = True
        self.update_status()

        def finished(value=None, error=None):
            self.pending = False
            if self.closed:
                return
            self.window.toast(error or ("Operation accepted" if value.get("accepted") else "Operation rejected"))
            self.manager.refresh("profiles")
            self.update_status()

        self.manager.request(method, arguments, finished, lambda error: finished(error=error))

    def configuration_changed(self):
        if self.envelope:
            self.window.toast("Profile configuration changed. Reload its settings before saving.")

    def load_details(self):
        if self.profile.get("configurationErrorCode") == "configuration.unsupported-schema":
            row(group(self.detail_box, "Unsupported profile"), "Unsupported schema",
                f"Detected: {self.profile.get('detectedSchemaVersion')}; supported: {self.profile.get('supportedSchemaVersion')}")
            self.detail_box.append(button("Retire unsupported profile", lambda: confirm(self.window,
                "Retire this profile?", "Managed configuration will be removed and old state quarantined. Backup data is retained.",
                lambda: self.manager.request("RetireUnsupportedProfile", (self.profile_id,),
                    lambda _value: self.window.navigation.pop()), destructive=True), True))
            return
        if "profile-details" not in self.manager.features:
            return
        if self.pending:
            return
        self.manager.request("GetProfileDetails", (self.profile_id,), self.show_details, self.window.toast)

    def show_details(self, envelope):
        if self.closed:
            return
        self.envelope = envelope
        while self.detail_box.get_first_child():
            self.detail_box.remove(self.detail_box.get_first_child())
        document = envelope["document"]
        settings = group(self.detail_box, "Profile settings")
        writable = self.manager.ready and "profile-administration" in self.manager.features
        settings.set_sensitive(writable)
        self.name = entry(settings, "Display name", document.get("name", self.profile["name"]))
        self.daily = switch(settings, "Daily limit", document["settings"]["dailyLimit"])
        self.auto_eject = switch(settings, "Eject after backup", document["settings"]["autoEject"])
        controls = Gtk.Box(spacing=6)
        controls.append(button("Save settings", self.save_settings))
        controls.append(button("Reload settings", self.load_details))
        controls.get_first_child().set_sensitive(writable)
        self.detail_box.append(controls)
        sources = group(self.detail_box, "Backup sources")
        sources.set_sensitive(writable)
        for source in document["sources"]:
            source_row = row(sources, source["name"], source.get("subvolume", ""))
            source_row.add_suffix(button("Edit", lambda s=source: self.edit_source(s)))
            source_row.add_suffix(button("Remove", lambda s=source: confirm(self.window,
                "Remove this source?", "Existing snapshots are retained.",
                lambda: self.mutate("RemoveProfileSource", s["id"])), True))
        add_source = button("Add source", self.add_source)
        add_source.set_sensitive(writable)
        self.detail_box.append(add_source)
        if "target-credentials" in self.manager.features:
            self.detail_box.append(button("Unlocking and credentials", self.credentials))
        technical = group(self.detail_box, "Technical details")
        for key in ("target", "identity", "layout"):
            if key in document:
                row(technical, key.capitalize(), json.dumps(document[key], indent=2))
        delete_profile = button("Delete profile", lambda: confirm(self.window, "Delete this profile?",
            "Managed configuration will be removed. Existing backup data is retained.",
            lambda: self.mutate("DeleteProfile"), destructive=True), True)
        delete_profile.set_sensitive(writable)
        self.detail_box.append(delete_profile)

    def save_settings(self):
        self.mutate("UpdateProfileSettings", payload={"name": self.name.get_text().strip(),
            "dailyLimit": self.daily.get_active(), "autoEject": self.auto_eject.get_active()})

    def mutate(self, method, source_id=None, payload=None):
        if self.pending or not self.envelope or not self.manager.ready or "profile-administration" not in self.manager.features:
            return
        arguments = [self.profile_id]
        if source_id is not None:
            arguments.append(source_id)
        arguments.extend((self.envelope["generation"], self.envelope["fingerprint"]))
        if payload is not None:
            arguments.append(json.dumps(payload))
        self.pending = True

        def finished(value=None, error=None):
            self.pending = False
            if self.closed:
                return
            self.window.toast(error or "Profile updated")
            if not error:
                self.manager.refresh("profiles")
                if method == "DeleteProfile":
                    self.window.navigation.pop()
                else:
                    self.load_details()
            self.update_status()

        self.manager.request(method, arguments, finished, lambda error: finished(error=error))

    def edit_source(self, source=None, candidate=None):
        dialog = Adw.AlertDialog(heading="Edit source" if source else "Add source")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        fields = group(box, "Source")
        name = entry(fields, "Name", (source or candidate or {}).get("name", ""))
        local = spin(fields, "Local retention", (source or {}).get("localRetention", 3), 1)
        remote = spin(fields, "Target retention (0 keeps all)", (source or {}).get("remoteRetention", 30))
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_close_response("cancel")

        def response(_dialog, answer):
            if answer == "save":
                payload = {"name": name.get_text().strip(), "localRetention": int(local.get_value()),
                           "remoteRetention": int(remote.get_value())}
                if candidate:
                    payload["candidateId"] = candidate["id"]
                self.mutate("UpdateProfileSource" if source else "AddProfileSource",
                            source["id"] if source else None, payload)

        dialog.connect("response", response)
        dialog.present(self.window)

    def add_source(self):
        dialog = Gtk.FileDialog(title="Select a Btrfs source subvolume")

        def selected(_dialog, result):
            try:
                path = dialog.select_folder_finish(result).get_path()
            except Exception:
                return

            def register():
                with path_fd(path) as fd:
                    return self.manager.call("RegisterProfileSourceCandidate", self.profile_id, fd)

            self.manager.submit(register, lambda candidate: self.edit_source(candidate=candidate), self.window.toast)

        dialog.select_folder(self.window, None, selected)

    def credentials(self):
        from credentials import CredentialsView
        self.window.push(CredentialsView(self.window, self.profile_id))

    def history_page(self, direction):
        self.history_offset = max(0, min(10000, self.history_offset + direction * 25))
        self.history_revision += 1
        self.load_history()

    def load_history(self):
        if self.closed or "sanitized-history" not in self.manager.features:
            return
        if self.history_pending:
            self.history_dirty = True
            return
        self.history_pending = True
        revision = self.history_revision

        def finished(value=None, error=None):
            self.history_pending = False
            if self.closed:
                return
            if error:
                self.window.toast(error)
            elif revision == self.history_revision:
                for widget in self.history_rows:
                    self.history_group.remove(widget)
                self.history_rows = [row(self.history_group, item.get("state", "Unknown"),
                    f"{item.get('finishedAt', '')} · {item.get('durationSeconds', -1)}s · "
                    f"{format_bytes(item.get('bytesTransferred'))} · {item.get('sourceCount', 0)} sources\n"
                    f"{item.get('sourceName', '')} → {item.get('targetName', '')} · {item.get('errorCode', '')}") for item in value]
            if self.history_dirty:
                self.history_dirty = False
                self.load_history()

        self.manager.request("GetHistorySanitized", (self.profile_id, self.history_offset, 25),
                             finished, lambda error: finished(error=error))

    def close(self):
        self.closed = True
