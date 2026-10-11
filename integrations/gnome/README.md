# GNOME 50 integration

This optional integration consists of a GNOME Shell extension, a GTK 4 and
libadwaita companion window, an unprivileged restore helper and a Files context
action. The base runtime has no GNOME, GTK, GJS or Python dependency.

The extension UUID is `btrfs-backup@btrfsbackup.github.io`. Its metadata targets
Shell 50, and its entry points use ES modules. GTK runs in the companion process;
it is never imported into Shell.

See [the GNOME integration guide](../../docs/gnome-integration.md) for the feature
mapping, installation, package inventory, architecture and validation commands.
