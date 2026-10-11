#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Kamil Piwowarski <kapiwko@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the Fedora RPM definition from the current working source tree."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from release_common import copy_source_tree, deterministic_tar_gz, sha256, source_date_epoch
from release_packaging import render


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--srpm-only", action="store_true",
                        help="prepare an SRPM without compiling or requiring build dependencies")
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist" / "fedora")
    arguments = parser.parse_args()
    dist = arguments.dist_dir.resolve()
    dist.mkdir(parents=True, exist_ok=True)
    version = (ROOT / "VERSION").read_text().strip()
    epoch = source_date_epoch(ROOT)
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(epoch)
    with tempfile.TemporaryDirectory(prefix="btrfs-backup-fedora.", dir="/tmp") as temporary:
        top = Path(temporary)
        for name in ("SOURCES", "SPECS", "BUILD", "BUILDROOT", "RPMS", "SRPMS"):
            (top / name).mkdir()
        source = top / f"btrfs-backup-{version}"
        copy_source_tree(ROOT, source, epoch)
        archive = top / "SOURCES" / f"{source.name}.tar.gz"
        deterministic_tar_gz(source, archive, epoch)
        spec = top / "SPECS" / "btrfs-backup.spec"
        render(ROOT / "packaging/rpm/btrfs-backup.spec.in", spec, {"VERSION": version})
        subprocess.run([
            "rpmbuild", "-bs" if arguments.srpm_only else "-ba",
            "--define", f"_topdir {top}", str(spec),
        ], check=True, env=environment)
        artifacts = [archive, spec, *sorted((top / "SRPMS").glob("*.rpm")),
                     *sorted((top / "RPMS").rglob("*.rpm"))]
        for artifact in artifacts:
            shutil.copy2(artifact, dist / artifact.name)
        (dist / "SHA256SUMS").write_text("".join(
            f"{sha256(artifact)}  {artifact.name}\n" for artifact in artifacts
        ))
    print(f"Fedora artifacts: {dist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
