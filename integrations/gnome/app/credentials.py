# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

import os

from gi.repository import Gtk

from manager import secret_fd
from widgets import button, confirm, entry, group, page, row, switch


class CredentialsView:
    def __init__(self, window, profile_id):
        self.window = window
        self.manager = window.manager
        self.profile_id = profile_id
        self.page, self.box = page("Target unlocking")
        self.closed = False
        self.pending = False
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.append(self.list_box)
        fields = group(self.box, "Credential authorization")
        self.authorization = entry(fields, "Existing recovery passphrase", password=True)
        self.label = entry(fields, "New credential label")
        self.automatic = switch(fields, "Use new key for automatic unlocking", False)
        self.passphrase = entry(fields, "New recovery passphrase", password=True)
        self.confirmation = entry(fields, "Confirm new passphrase", password=True)
        self.key_path = entry(fields, "Key file path")
        self.box.append(button("Choose key file", self.choose_key))
        self.controls = Gtk.Box(spacing=6)
        self.controls.append(button("Add passphrase", lambda: self.mutate("AddTargetPassphrase")))
        self.controls.append(button("Import key", lambda: self.mutate("AddTargetKey")))
        self.controls.append(button("Generate key", lambda: self.mutate("GenerateTargetKey")))
        self.box.append(self.controls)
        self.refresh()

    def refresh(self):
        self.manager.request("ListTargetCredentials", (self.profile_id,), self.show, self.window.toast)

    def show(self, credentials):
        if self.closed:
            return
        while self.list_box.get_first_child():
            self.list_box.remove(self.list_box.get_first_child())
        rows = group(self.list_box, "LUKS credentials", "Automatic keys are kept by the system service. Key bytes are never returned to this window.")
        for credential in credentials:
            widget = row(rows, credential.get("label") or "Unmanaged credential",
                f"Keyslot {credential['keyslot']} · {credential['type']}" +
                (" · automatic" if credential["automatic"] else ""))
            if credential["managed"] and not credential["automatic"] and len(credentials) > 1:
                widget.add_suffix(button("Remove", lambda c=credential: confirm(self.window,
                    "Remove this credential?", f"Keyslot {c['keyslot']}",
                    lambda: self.mutate("RemoveTargetCredential", c["id"]), destructive=True), True))

    def choose_key(self):
        chooser = Gtk.FileDialog(title="Choose an existing key file")

        def selected(_chooser, result):
            try:
                self.key_path.set_text(chooser.open_finish(result).get_path())
            except Exception:
                pass

        chooser.open(self.window, None, selected)

    def mutate(self, method, credential_id=None):
        if self.pending:
            return
        authorization = self.authorization.get_text()
        secret = self.passphrase.get_text()
        if method == "AddTargetPassphrase" and secret != self.confirmation.get_text():
            self.window.toast("The new passphrases do not match")
            return
        label = self.label.get_text().strip()
        automatic = self.automatic.get_active()
        key_path = self.key_path.get_text()
        self.authorization.set_text("")
        self.passphrase.set_text("")
        self.confirmation.set_text("")
        self.pending = True
        self.controls.set_sensitive(False)

        def work():
            with secret_fd(authorization) as auth:
                if method == "AddTargetPassphrase":
                    with secret_fd(secret) as added:
                        return self.manager.call(method, self.profile_id, auth, added, label)
                if method == "AddTargetKey":
                    fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                    try:
                        return self.manager.call(method, self.profile_id, auth, fd, label, automatic)
                    finally:
                        os.close(fd)
                if method == "GenerateTargetKey":
                    return self.manager.call(method, self.profile_id, auth, label, automatic)
                return self.manager.call(method, self.profile_id, credential_id, auth)

        def finished(value=None, error=None):
            self.pending = False
            if self.closed:
                return
            self.controls.set_sensitive(True)
            self.window.toast(error or ("Credential updated" if value.get("accepted") else "Credential operation rejected"))
            self.refresh()

        self.manager.submit(work, finished, lambda error: finished(error=error))

    def close(self):
        self.closed = True
        self.authorization.set_text("")
        self.passphrase.set_text("")
        self.confirmation.set_text("")
