# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep compositor sockets, settings and extension installation disposable."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="btrfs-backup-gnome-shell-") as temporary:
    runtime = Path(temporary) / "runtime"
    runtime.mkdir(mode=0o700)
    environment = os.environ.copy()
    environment.update(XDG_RUNTIME_DIR=str(runtime), GIO_USE_VFS="local", GTK_A11Y="none")
    result = subprocess.run(["dbus-run-session", "--", "gnome-shell-test-tool", "--headless",
        "--disable-animations", "--extension", str(Path(sys.argv[1]).resolve()),
        str(Path(__file__).with_name("shell.js"))], env=environment,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
    print(result.stdout if result.returncode else "\n".join(
        line for line in result.stdout.splitlines() if "checks passed" in line))
    raise SystemExit(result.returncode)
