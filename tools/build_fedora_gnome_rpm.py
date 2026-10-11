#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the optional Fedora GNOME integration RPM with CPack."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from release_common import copy_source_tree, sha256, source_date_epoch


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist" / "fedora")
    arguments = parser.parse_args()
    dist = arguments.dist_dir.resolve()
    dist.mkdir(parents=True, exist_ok=True)
    version = (ROOT / "VERSION").read_text().strip()
    epoch = source_date_epoch(ROOT)
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(epoch)

    with tempfile.TemporaryDirectory(prefix="btrfs-backup-fedora-gnome.", dir="/tmp") as temporary:
        top = Path(temporary)
        source = top / f"btrfs-backup-{version}"
        copy_source_tree(ROOT, source, epoch)
        build = top / "build"
        configure = [
            "cmake", "-S", str(source), "-B", str(build),
            "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_INSTALL_PREFIX=/usr",
            "-DCMAKE_INSTALL_LIBDIR=lib64", "-DCMAKE_INSTALL_SYSCONFDIR=/etc",
            "-DBTRFSBACKUP_SYSTEMD_UNIT_DIR=/usr/lib/systemd/system",
            "-DBTRFSBACKUP_TMPFILES_DIR=/usr/lib/tmpfiles.d",
            "-DBUILD_SYSTEM_MANAGER=ON", "-DBUILD_TESTING=OFF",
            "-DBUILD_GNOME_INTEGRATION=ON", "-DBTRFSBACKUP_ARCHITECTURE_TESTS=OFF",
        ]
        if shutil.which("ninja"):
            configure.extend(("-G", "Ninja"))
        subprocess.run(configure, check=True, env=environment)
        subprocess.run(["cmake", "--build", str(build), "--parallel",
                        str(max(1, os.cpu_count() or 2))], check=True, env=environment)
        subprocess.run([
            "cpack", "--config", str(build / "CPackConfig.cmake"), "-G", "RPM",
            "-D", "CPACK_COMPONENTS_ALL=GNOMEIntegration",
            "-D", "CPACK_RPM_FILE_NAME=RPM-DEFAULT",
            "-D", "CPACK_RPM_PACKAGE_RELEASE=1.fc44",
            "-D", "CPACK_RPM_GNOMEINTEGRATION_PACKAGE_NAME=btrfs-backup-gnome",
            "-D", "CPACK_RPM_GNOMEINTEGRATION_PACKAGE_RELEASE=1.fc44",
            "-D", "CPACK_RPM_GNOMEINTEGRATION_PACKAGE_RELEASE=1.fc44",
            "-B", str(top / "rpm"),
        ], check=True, env=environment)
        matches = sorted((top / "rpm").glob("btrfs-backup-gnome-*.rpm"))
        if len(matches) != 1:
            raise RuntimeError(f"expected one GNOME RPM, found {len(matches)}")
        destination = dist / f"btrfs-backup-gnome-{version}-1.fc44.{os.uname().machine}.rpm"
        shutil.copy2(matches[0], destination)

        metadata = subprocess.run(
            ["rpm", "-qp", "--qf", "%{NAME} %{VERSION}-%{RELEASE}\n", str(destination)],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if metadata != f"btrfs-backup-gnome {version}-1.fc44":
            raise RuntimeError(f"unexpected GNOME RPM metadata: {metadata}")
        dependencies = subprocess.run(
            ["rpm", "-qp", "--requires", str(destination)],
            check=True, capture_output=True, text=True,
        ).stdout
        expected = (f"btrfs-backup >= {version}", "gnome-shell >= 50", "gjs",
                    "python3-gobject", "gtk4", "libadwaita")
        missing = [requirement for requirement in expected if requirement not in dependencies]
        if missing:
            raise RuntimeError(f"GNOME RPM is missing dependencies: {', '.join(missing)}")

        checksums = dist / "SHA256SUMS"
        entries = {}
        if checksums.exists():
            for line in checksums.read_text().splitlines():
                digest, name = line.split("  ", maxsplit=1)
                if (dist / name).is_file():
                    entries[name] = digest
        entries[destination.name] = sha256(destination)
        checksums.write_text("".join(
            f"{digest}  {name}\n" for name, digest in sorted(entries.items())
        ))
        print(f"Fedora GNOME RPM: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
