// SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
// SPDX-License-Identifier: GPL-3.0-or-later

#include <chrono>
#include <csignal>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

#include <core/Cancellation.hpp>
#include <core/RuntimeTime.hpp>
#include <platform/linux/restore/PosixRestoreOperations.hpp>
#include <restore/RepositoryCatalog.hpp>
#include <restore/RestoreEngine.hpp>
#include <restore/RestoreError.hpp>
#include <restore/RestorePlan.hpp>

namespace {

volatile std::sig_atomic_t interrupted = 0;

void cancel_restore(int) {
    interrupted = 1;
}

btrfsbackup::RuntimeTimePoint timestamp(const nlohmann::json& value) {
    const auto parsed = btrfsbackup::parse_utc_timestamp(value.get<std::string>());
    if (!parsed)
        throw std::invalid_argument("invalid repository timestamp");
    return *parsed;
}

btrfsbackup::restore::RepositoryCatalog decode_catalog(const nlohmann::json& document) {
    using namespace btrfsbackup::restore;
    if (document.at("schemaVersion") != 1)
        throw RestoreError(RestoreErrorCode::RepositoryFormatUnsupported, "unsupported repository catalog");
    std::vector<CatalogSnapshot> snapshots;
    for (const auto& snapshot : document.at("snapshots")) {
        snapshots.push_back({
            .snapshot_id = snapshot.at("snapshotId").get<std::string>(),
            .host_id = snapshot.at("hostId").get<std::string>(),
            .profile_id = snapshot.at("profileId").get<std::string>(),
            .source_id = snapshot.at("sourceId").get<std::string>(),
            .repository_path = RelativeRestorePath{snapshot.at("relativePath").get<std::string>()},
            .created_at = timestamp(snapshot.at("createdAt")),
            .uuid = snapshot.at("uuid").get<std::string>(),
            .received_uuid = snapshot.value("receivedUuid", ""),
            .parent_uuid = snapshot.value("parentUuid", ""),
            .verified = snapshot.at("verified").get<bool>(),
        });
    }
    return {
        "/",
        {
            .repository_id = document.at("repositoryId").get<std::string>(),
            .target_filesystem_uuid = document.at("targetFilesystemUuid").get<std::string>(),
            .created_at = timestamp(document.at("createdAt")),
            .features = document.at("features").get<std::vector<std::string>>(),
        },
        document.at("generation").get<std::uint64_t>(),
        std::move(snapshots),
    };
}

void publish(const nlohmann::json& event) {
    std::cout << event.dump() << '\n'
              << std::flush;
}

} // namespace

int main() {
    using namespace btrfsbackup::restore;
    std::signal(SIGINT, cancel_restore);
    std::signal(SIGTERM, cancel_restore);
    try {
        // This is an unprivileged helper. Its only backup access is an inherited,
        // already authorized descriptor; it cannot discover or mount repositories.
        std::string input;
        char byte = 0;
        while (std::cin.get(byte) && byte != '\n') {
            if (input.size() >= 16 * 1024 * 1024)
                throw std::invalid_argument("restore request is too large");
            input += byte;
        }
        const auto request = nlohmann::json::parse(input);
        const int fd = request.at("sourceFd").get<int>();
        if (fd < 3)
            throw std::invalid_argument("invalid inherited source descriptor");
        const auto catalog = decode_catalog(request.at("catalog"));
        const auto plan = RestorePlanner{}.plan_from_pinned_source(
            catalog,
            {
                .transaction_id = request.at("transactionId").get<std::string>(),
                .snapshot_id = request.at("snapshotId").get<std::string>(),
                .source_path = RelativeRestorePath{request.at("relativePath").get<std::string>()},
                .destination = request.at("destination").get<std::string>(),
                .kind = RestoreKind::Files,
                .existing_destination = request.at("replace").get<bool>()
                    ? ExistingDestinationPolicy::Replace
                    : ExistingDestinationPolicy::Fail,
            },
            std::filesystem::path{"/proc/self/fd"} / std::to_string(fd)
        );
        btrfsbackup::CancellationToken cancellation;
        std::jthread signal_monitor([&](std::stop_token stop) {
            while (!stop.stop_requested()) {
                if (interrupted) {
                    cancellation.request_cancel();
                    return;
                }
                std::this_thread::sleep_for(std::chrono::milliseconds{10});
            }
        });
        btrfsbackup::platform::linux::restore::PosixRestoreOperations operations;
        auto last_report = std::chrono::steady_clock::time_point{};
        const auto result = RestoreExecutor{operations}.execute(plan, cancellation, [&](const RestoreProgress& progress) {
                const auto now = std::chrono::steady_clock::now();
                if (now - last_report < std::chrono::milliseconds{200})
                    return;
                last_report = now;
                publish({{"event", "progress"}, {"files", progress.statistics.files},
                    {"bytes", progress.statistics.bytes}, {"name", progress.current_path.filename().string()}}); }, [](RestorePhase phase) { publish({{"event", "phase"}, {"phase", phase == RestorePhase::CheckingSpace ? "checking space" : "copying and verifying"}}); });
        publish({{"event", "result"}, {"committed", result.committed}, {"files", result.statistics.files}, {"bytes", result.statistics.bytes}});
        return 0;
    } catch (const RestoreError& error) {
        publish({{"event", "result"}, {"committed", false}, {"errorCode", restore_error_code_name(error.code())}, {"error", error.what()}});
    } catch (const std::exception& error) {
        publish({{"event", "result"}, {"committed", false}, {"errorCode", "restore-failed"}, {"error", error.what()}});
    }
    return 1;
}
