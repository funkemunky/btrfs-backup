# Development Workflow

CMake targets and presets are the source of truth for building and testing
native code. Python entry points orchestrate disposable containers, QEMU,
screenshots and release artifacts; they do not form part of the installed
backup runtime.

## Everyday Development

Configure, build and test the base project with GCC:

```bash
cmake --preset gcc-debug
cmake --build --preset gcc-debug --parallel
ctest --preset gcc-debug --parallel
```

Use a focused CTest while iterating:

```bash
ctest --test-dir build/gcc-debug --output-on-failure -R '<test-name>'
```

The optional Plasma integration has its own preset and test selection:

```bash
cmake --preset kde-debug
cmake --build --preset kde-debug --parallel
ctest --preset kde --parallel
```

For the GNOME 50 integration:

```bash
cmake --preset gnome-debug
cmake --build --preset gnome-debug --target btrfs-backup-gnome --parallel
ctest --preset gnome
```

Install GJS, Python GObject bindings, GTK 4 and libadwaita for this optional
component. See [GNOME integration](gnome-integration.md) for installation,
package inventory and disposable headless Shell checks.

## Fedora 44 Development

Fedora 44 is the baseline for Fedora builds. Install the base development and
quality-check tools on a conventional Fedora 44 system:

```bash
sudo dnf install \
  gcc-c++ clang clang-tools-extra cmake ninja-build make git python3 \
  pkgconf-pkg-config nlohmann-json-devel btrfs-progs btrfs-progs-devel \
  cryptsetup cryptsetup-devel libacl-devel libblkid-devel libmount-devel \
  libfdisk-devel systemd-devel systemd-udev polkit
```

Use the `gcc-debug`, `clang-debug`, `architecture`, and sanitizer presets above;
run `make quality` before submitting C++ changes. Tests run as an ordinary user.
For package builds, install `rpm-build`, `systemd-rpm-macros`, and `dnf5-plugins`
and follow the [Fedora RPM instructions](../packaging/rpm/README.md).

For optional Plasma development, additionally install:

```bash
sudo dnf install \
  extra-cmake-modules qt6-qtbase-devel qt6-qtdeclarative-devel \
  kf6-kconfig-devel kf6-kcoreaddons-devel kf6-ki18n-devel \
  kf6-kjobwidgets-devel kf6-kcmutils-devel kf6-knotifications-devel \
  kf6-kpackage-devel kf6-kio-devel kf6-krunner-devel \
  kf6-kservice-devel kf6-kwidgetsaddons-devel kf6-kirigami-devel libplasma-devel
```

Then use `kde-debug` and `ctest --preset kde`. Desktop smoke testing needs a
Plasma 6 session. The Fedora RPM workflow builds both the base runtime and optional KDE RPM.

Fedora uses `/usr/lib64` for native libraries and `/usr/lib` for systemd units
and tmpfiles declarations. When staging an installation manually, configure:

```bash
cmake --preset gcc-debug \
  -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib64 \
  -DCMAKE_INSTALL_SYSCONFDIR=/etc \
  -DBTRFSBACKUP_SYSTEMD_UNIT_DIR=/usr/lib/systemd/system \
  -DBTRFSBACKUP_TMPFILES_DIR=/usr/lib/tmpfiles.d
```

Use `DESTDIR` with `cmake --install` to inspect a staged tree. Install the RPM
with DNF for system testing. The `install-local` target installs Arch packages
with pacman; Fedora contributors should use the RPM workflow instead.
Keep SELinux enabled during runtime validation and include relevant AVC denials
in bug reports. The container installation check does not exercise a booted
systemd, SELinux policy, real backup devices, or restore recovery; use disposable
VMs and the [integration testing guide](testing.md) for those checks.

## Architecture And Quality Gates

Run the complete architecture contract with Clang:

```bash
cmake --preset architecture
ctest --preset architecture --parallel
```

This contract checks public headers, component dependencies and build side
channels. In particular, test executables must be owned by CMake, native code
must not be compiled ad hoc, and Python subprocesses must not use
`shell=True`.

Run formatting and clang-tidy checks before committing:

```bash
make quality
```

For faster feedback while editing tracked C++ implementation files, use
`make quality-changed`. Compiler warnings and sanitizer configurations remain
normal CMake workflows:

```bash
cmake --preset strict-warnings
cmake --build --preset strict-warnings --parallel
ctest --preset strict-warnings --parallel

cmake --preset asan-ubsan
cmake --build --preset asan-ubsan --parallel
ctest --preset asan-ubsan --parallel
```

## Privileged Integration Tests

The default suite does not need real block devices. Storage and provisioning
changes must additionally run against disposable environments:

```bash
cmake --build --preset gcc-debug --target real-btrfs-integration
cmake --build --preset gcc-debug --target qemu-hotplug-integration
```

The equivalent orchestration entry points are:

```bash
python3 tests/integration/docker/run_real_btrfs.py
python3 tests/qemu/run_hotplug.py
```

Both paths use privileged containers or root-equivalent virtualization
facilities. Access to the Docker daemon is itself root-equivalent. The tests
must operate only on their disposable loop devices, images and guests, never
on a real backup target or host configuration.

## Release Artifacts

Build the complete artifact set through the release orchestrator:

```bash
python3 tools/release.py --target all --static-tests
```

Before publication, run its full test mode and verify the artifacts in the
supported release containers:

```bash
python3 tools/release.py --target all --full-tests
python3 tests/integration/docker/run_release_matrix.py
```

The release path stages installation with CMake/CPack, then uses native package
definitions where a format needs them. Package lifecycle behavior is kept
declarative and audited as described in
[Packaging Scriptlets](packaging-scriptlets.md). Follow the complete
[release checklist](releasing.md) before tagging. The tag-triggered workflow
uploads the checksum-verified package directory and publishes exactly that
artifact after the real-Btrfs and QEMU gates pass; do not rebuild the files
between validation and publication.

## Deliberate Shell Boundaries

Repository automation must not add shell launchers or hidden compiler entry
points. The QEMU host runner and guest scenario are Python modules. The host
copies the guest module and a JSON configuration onto the disposable setup disk;
the cached systemd unit starts it directly with the guest Python interpreter.

Two C++ process tests also invoke `sh -c` as test input to exercise process
groups and pipelines. These exact call sites are allowlisted by the
architecture contract. They are not production execution paths and must not be
used as a model for new subprocess code.
