# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exercise the installed helper against disposable ordinary files, without root."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def run(helper, source, destination, replace=False, relative="."):
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    try:
        request = {"sourceFd": fd, "transactionId": str(uuid.uuid4()), "snapshotId": "snapshot",
            "relativePath": relative, "destination": str(destination), "replace": replace,
            "catalog": {"schemaVersion": 1, "repositoryId": "repository", "targetFilesystemUuid": "filesystem",
                "createdAt": "2026-10-01T00:00:00Z", "features": [], "generation": 1,
                "snapshots": [{"snapshotId": "snapshot", "hostId": "host", "profileId": "home", "sourceId": "home",
                    "relativePath": "snapshots/snapshot", "createdAt": "2026-10-01T00:00:00Z", "uuid": "uuid", "verified": True}]}}
        result = subprocess.run([helper], input=json.dumps(request) + "\n", pass_fds=(fd,),
            capture_output=True, text=True, timeout=15)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        return result.returncode, events[-1]
    finally:
        os.close(fd)


with tempfile.TemporaryDirectory(prefix="gnome-restore-test-") as temporary:
    root = Path(temporary)
    source = root / "source"
    source.write_bytes(b"verified backup contents\0\xff")
    destination = root / "restored"
    code, result = run(sys.argv[1], source, destination)
    assert code == 0 and result["committed"] and destination.read_bytes() == source.read_bytes(), result
    destination.write_bytes(b"keep existing")
    code, result = run(sys.argv[1], source, destination)
    assert code != 0 and result["errorCode"] == "destination-exists" and destination.read_bytes() == b"keep existing", result
    code, result = run(sys.argv[1], source, destination, replace=True)
    assert code == 0 and destination.read_bytes() == source.read_bytes(), result
    code, result = run(sys.argv[1], source, root / "traversal", relative="../outside")
    assert code != 0 and result["errorCode"] == "path-traversal" and not (root / "traversal").exists(), result
    directory = root / "directory"
    directory.mkdir()
    (directory / "file").write_bytes(source.read_bytes())
    code, result = run(sys.argv[1], directory, root / "copied-directory")
    assert code == 0 and (root / "copied-directory/file").read_bytes() == source.read_bytes(), result
    (directory / "symlink").symlink_to(source)
    code, result = run(sys.argv[1], directory, root / "rejected-directory")
    assert code != 0 and result["errorCode"] == "symlink-rejected" and not (root / "rejected-directory").exists(), result
print("GNOME restore contracts passed")
