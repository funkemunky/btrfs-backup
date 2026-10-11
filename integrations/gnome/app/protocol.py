# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Presentation rules for the public manager contract; no storage policy."""

BUS_NAME = "io.github.btrfsbackup.Manager1"
OBJECT_PATH = "/io/github/btrfsbackup/Manager1"
ACTIVE = {"starting", "running", "validating"}
PROFILE_ENVELOPES = {"GetProfileDetails", "UpdateProfileSettings", "AddProfileSource",
                     "UpdateProfileSource", "RemoveProfileSource"}


def validate_capabilities(value):
    if (value.get("schemaVersion") != 1 or value.get("interface") != BUS_NAME
            or value.get("apiMajor") != 1 or value.get("apiMinor") != 0
            or value.get("readOnly") is not False
            or any(value.get(key) != 1 for key in (
                "profileSchemaVersion", "publicStatusSchemaVersion",
                "historySchemaVersion", "deviceStateSchemaVersion"))
            or not isinstance(value.get("features"), list)
            or not all(isinstance(f, str) for f in value["features"])):
        raise ValueError("The installed backup manager is incompatible")
    return set(value["features"])


def validate_document(method, value):
    if method in {"ListSourceCandidates", "ResolveBackupCoverageByFd"}:
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError(f"Invalid {method} response")
    elif method in {"ListProfiles", "GetHistorySanitized", "ListTargetCredentials"}:
        if not isinstance(value, list) or any(
                not isinstance(row, dict) or row.get("schemaVersion") != 1 for row in value):
            raise ValueError(f"Invalid {method} response")
        if method == "ListProfiles" and (any(not isinstance(p.get("profileId"), str)
                or not p["profileId"] or not isinstance(p.get("sources"), list)
                or type(p.get("enabled")) is not bool for p in value)
                or len({p["profileId"] for p in value}) != len(value)):
            raise ValueError("Invalid profile list")
    elif not isinstance(value, dict) or value.get("schemaVersion") != (
            3 if method in PROFILE_ENVELOPES else 1):
        raise ValueError(f"Unsupported {method} response")
    if method in PROFILE_ENVELOPES and (not isinstance(value.get("document"), dict)
            or not all(isinstance(value.get(k), str) and value[k] for k in ("generation", "fingerprint"))):
        raise ValueError("Invalid profile revision")
    if method == "GetStatus":
        validate_status(value)
    if method == "GetDeviceState" and (not isinstance(value.get("state"), str)
            or any(type(value.get(k)) is not bool for k in ("connected", "unlocked", "mounted", "safeToRemove"))):
        raise ValueError("Invalid target lifecycle")
    return value


def validate_status(value):
    strings = ("runId", "operationKind", "state", "phase", "activity", "errorCode", "sourceName",
               "targetName", "progressAccuracy", "lastSuccessAt", "lastAttemptAt", "lastAttemptState",
               "startedAt", "updatedAt")
    if (any(not isinstance(value.get(k), str) for k in strings)
            or type(value.get("canCancel")) is not bool
            or any(type(value.get(k)) is not int or not -1 <= value[k] <= 100
                   for k in ("sourceProgress", "overallProgress"))
            or any(type(value.get(k)) is not int or value[k] < 0 for k in ("sourceIndex", "sourceCount"))
            or value["sourceIndex"] > value["sourceCount"]
            or type(value.get("etaSeconds")) is not int or value["etaSeconds"] < -1
            or value["progressAccuracy"] not in {"exact", "estimated", "indeterminate"}
            or (value["state"] in ACTIVE and not value["runId"])
            or (value["canCancel"] and value["state"] not in ACTIVE)):
        raise ValueError("Invalid backup status")
    for key in ("bytesProcessed", "bytesTotalEstimated", "speedBps"):
        if key in value and (type(value[key]) is not int or value[key] < 0):
            raise ValueError("Invalid transfer measurement")
    expected = {"failed": "backup.failed", "cancelled": "backup.cancelled"}.get(value["state"], "")
    if value["state"] in ACTIVE | {"idle", "succeeded", "failed", "cancelled", "exited", "unavailable"}:
        if value["errorCode"] != expected:
            raise ValueError("Backup error code does not match its state")


def actions(profile, run, target, features, pending=False):
    available = not pending and bool(run) and bool(target) and profile.get("configurationValid", True)
    running = run.get("state") in ACTIVE
    connected = target.get("connected") is True
    return {
        "StartBackup": available and "start-backup" in features and not running and connected,
        "CancelBackup": available and "cancel-backup" in features and running
            and run.get("canCancel") is True and bool(run.get("runId")),
        "ValidateTarget": available and "validate-target" in features and not running and connected,
        "EjectTarget": available and "eject-target" in features and not running and connected
            and (target.get("mounted") is True or target.get("unlocked") is True),
        "browse": available and "browse-backups" in features and connected,
        "SetProfileEnabled": available and "profile-activation" in features,
    }


def storage_block(target, features):
    value = target.get("storage")
    if ("target-storage-usage" not in features or not isinstance(value, dict)
            or value.get("schemaVersion") != 1 or type(value.get("live")) is not bool
            or not isinstance(value.get("measuredAt"), str)
            or type(value.get("usagePercent")) is not int or not 0 <= value["usagePercent"] <= 100
            or any(type(value.get(key)) is not int or value[key] < 0
                   for key in ("capacityBytes", "usedBytes", "availableBytes"))
            or value.get("spaceState") not in {"normal", "below-configured-minimum"}):
        return None
    return value


def format_bytes(value):
    value = max(0, value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


def confined_path(value):
    if not isinstance(value, str) or value.startswith("/") or "\0" in value:
        raise ValueError("Invalid repository path")
    if value != "." and any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Invalid repository path")
    return value
