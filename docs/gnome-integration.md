# GNOME 50 integration

The optional `btrfs-backup-gnome` integration exposes the same manager and restore
engine as the Plasma integration through GNOME desktop surfaces.

| Plasma surface | GNOME surface | Features |
| --- | --- | --- |
| Plasmoid | Top bar extension | Per-profile status, source position, transfer progress, byte rate, ETA, transfer chart, last successful backup, target lifecycle and live or cached storage usage |
| Plasmoid controls | Profile menu | Start, run-scoped cancel, automatic activation, browse and safe eject |
| Session monitor | Extension notifications | Resident progress with cancellation, terminal results, overdue reminders and low-space warnings with configurable thresholds and persistent deduplication |
| System Settings KCM | Backup Manager window | Profile details, diagnostics, paged history, name and behavior settings, source selection and retention, source removal, profile deletion and unsupported profile retirement |
| KCM target setup | Device setup window | Whole-disk preparation, partition reformatting, free-space partition creation, compatible-repository adoption, before/after plans, source selection, progress and cancellation before destructive work |
| KCM unlocking | Target unlocking window | List keyslots, add recovery passphrases, import or generate keys, choose automatic unlocking and remove eligible managed credentials |
| KIO repository browser | Backup browser window | Authorized read-only browsing, paged directory listings, regular-file previews and guided file or directory restore |
| Dolphin previous versions | Files context action | Resolve coverage from the selected local entry, select a covered profile/source, list paged versions, browse and restore |
| KRunner | Overview search | `status backup home`, `start backup home`, `browse backup home`, `eject backup home`, and `versions /absolute/path` |

The GNOME browser presents repository contents in its own window. It does not
install a GVfs URI backend. Files opens the previous-version browser through
the optional Nautilus Python adapter. Both provide the browsing and restore
operations through the same authorized manager sessions.

## Build and install

Build dependencies are the base project's dependencies, GJS, GLib's
`glib-compile-schemas`, Python 3 and Python GObject bindings. Runtime dependencies
include GNOME Shell 50, GTK 4.10 or later and libadwaita 1.5 or later. Install
`nautilus-python` (Fedora/Arch) or `python3-nautilus` (Debian) for the Files action.
The system manager must be installed at the matching project version.

```bash
cmake --preset gnome-debug
cmake --build --preset gnome-debug --target btrfs-backup-gnome --parallel
ctest --preset gnome
cmake --install build/gnome-debug --prefix "$HOME/.local" --component GNOMEIntegration
gnome-extensions enable btrfs-backup@btrfsbackup.github.io
```

Log out and back in after installing the extension for the first time. Shell
must discover the installed extension before `gnome-extensions enable` can use
it. Put the installation's `bin` directory on the graphical session's `PATH`.
Restart Files after installing its Python adapter.

For a Fedora 44 RPM, install the GNOME build dependencies and run the maintained
builder. It builds the Release component under `/usr`, verifies RPM metadata and
runtime requirements, and adds the artifact to `dist/fedora/SHA256SUMS`:

```bash
sudo dnf install gjs glib2-devel gtk4 libadwaita python3-gobject nautilus-python
python3 tools/build_fedora_gnome_rpm.py
```

Install the GNOME component with its matching base RPM so DNF can resolve the
manager dependency:

```bash
version=$(cat VERSION)
sudo dnf install \
  "dist/fedora/btrfs-backup-${version}-1.fc44.$(uname -m).rpm" \
  "dist/fedora/btrfs-backup-gnome-${version}-1.fc44.$(uname -m).rpm"
```

RPM and Debian components have their own GNOME runtime dependencies and require
the matching base package. The base package remains independent of GTK and GJS.
The generated extension archive is
`build/gnome-debug/integrations/gnome/btrfs-backup@btrfsbackup.github.io.shell-extension.zip`.
That archive packages only the extension; install the companion component to
use profile management, Files integration and restore.

The installed component contains:

- `bin/btrfs-backup-gnome` and `bin/btrfs-backup-gnome-restore`;
- `share/btrfs-backup/gnome/*.py` and the canonical `Manager1.xml`;
- `share/applications/io.github.btrfsbackup.Gnome.desktop`;
- `share/gnome-shell/extensions/btrfs-backup@btrfsbackup.github.io/`, including the compiled local settings schema;
- `share/nautilus-python/extensions/previous_versions.py`.

## Boundaries and lifecycle

All manager calls are asynchronous from the UI's perspective. The Shell client
uses GIO callbacks; the companion uses bounded worker threads and delivers
results on the GTK main context. Both negotiate API and schema compatibility,
subscribe to change signals, coalesce invalidations during reads, discard old
results after service replacement, and disable controls when required state or
capabilities are unavailable. Device connectivity and safe removal come from
`GetDeviceState`, independently of backup success.

Operational controls use the existing polkit actions. Administrative changes
carry generation and fingerprint preconditions and stay in the system manager.
The desktop never writes privileged configuration. Secret bytes travel only in
private anonymous file descriptors; source selection and local coverage use
`O_PATH` descriptors. Device setup submits caller-bound topology candidates and
a reviewed plan, and requires explicit confirmation before starting it.

Browse sessions retain one D-Bus caller identity and renew their TTL before
work. Each query, preview or restore holds an identified operation lease. The
restore helper receives an authorized, pinned source descriptor and uses the
shared `RestorePlanner`, `RestoreExecutor` and `PosixRestoreOperations`. It runs
as the desktop user, stages and verifies copies, and uses the engine's existing
replacement, rollback and cancellation rules. Replacement requires a separate
confirmation. SIGTERM requests cooperative cancellation instead of interrupting
the engine's cleanup. A closing browser cancels its restore and releases its
session after cleanup; the main window waits for active restores to finish
cancellation before exiting.

Disabling the extension removes its status button, search provider, notifications,
bus subscriptions, pending reads, settings subscription and reminder timer.
It follows the default user session mode, so it is disabled on the lock screen.

## Validation

The `gnome` CTest label includes presentation and refresh queue contracts,
isolated GIO D-Bus tests with Unix descriptors, and ordinary-file restore tests
covering copy, replacement refusal, confirmed replacement, traversal and symlink
rejection. None of these tests requires real backup storage or root.

The optional Shell check installs the zip into temporary XDG directories and
runs a separate GNOME compositor and session bus. Its fake manager exercises
progress notifications, search, disable/re-enable, profile write preconditions,
the GTK screens and destructive-plan invalidation:

```bash
cmake --preset gnome-debug -DBTRFSBACKUP_GNOME_SHELL_TESTS=ON
cmake --build --preset gnome-debug --target btrfs-backup-gnome --parallel
ctest --preset gnome
```

This check needs `gnome-shell-test-tool` and a working headless compositor.
For manual checks, use a disposable storage target to exercise polkit prompts,
real backup transfer, Files selection, restore cancellation, preparation and
adoption. Never use a production target for destructive testing.

GNOME extension API references: [GNOME 50 porting guide](https://gjs.guide/extensions/upgrading/gnome-shell-50.html),
[notifications](https://gjs.guide/extensions/topics/notifications.html),
[search providers](https://gjs.guide/extensions/topics/search-provider.html).
