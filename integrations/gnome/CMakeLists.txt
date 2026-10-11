# SPDX-FileCopyrightText: 2026 Dawson <dawson@funkemunky.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

find_package(Threads REQUIRED)
find_program(GLIB_COMPILE_SCHEMAS glib-compile-schemas REQUIRED)
find_program(GJS_EXECUTABLE gjs REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(CMAKE_INSTALL_DEFAULT_COMPONENT_NAME GNOMEIntegration)
set(extension_uuid "btrfs-backup@btrfsbackup.github.io")
set(extension_build "${CMAKE_CURRENT_BINARY_DIR}/extension")
set(extension_install "${CMAKE_INSTALL_DATADIR}/gnome-shell/extensions/${extension_uuid}")
file(MAKE_DIRECTORY "${extension_build}/schemas")
foreach(source extension.js manager.js model.js prefs.js stylesheet.css)
    configure_file("extension/${source}" "${extension_build}/${source}" COPYONLY)
endforeach()
configure_file(extension/metadata.json.in "${extension_build}/metadata.json" @ONLY)
configure_file(extension/schemas/io.github.btrfsbackup.gnome.gschema.xml
    "${extension_build}/schemas/io.github.btrfsbackup.gnome.gschema.xml" COPYONLY)
add_custom_command(
    OUTPUT "${extension_build}/schemas/gschemas.compiled"
    COMMAND "${GLIB_COMPILE_SCHEMAS}" --strict "${extension_build}/schemas"
    DEPENDS "${extension_build}/schemas/io.github.btrfsbackup.gnome.gschema.xml"
    VERBATIM
)
add_custom_target(btrfs-backup-gnome-schemas
    DEPENDS "${extension_build}/schemas/gschemas.compiled")
add_custom_target(btrfs-backup-gnome-extension
    COMMAND ${CMAKE_COMMAND} -E tar cf
        "${CMAKE_CURRENT_BINARY_DIR}/${extension_uuid}.shell-extension.zip" --format=zip
        extension.js manager.js metadata.json model.js prefs.js schemas stylesheet.css
    WORKING_DIRECTORY "${extension_build}"
    DEPENDS btrfs-backup-gnome-schemas
    VERBATIM
)

add_executable(btrfs-backup-gnome-restore restore/main.cpp)
target_compile_features(btrfs-backup-gnome-restore PRIVATE cxx_std_23)
target_link_libraries(btrfs-backup-gnome-restore PRIVATE
    btrfsbackup-platform-linux-restore
    btrfsbackup-restore
    nlohmann_json::nlohmann_json
    Threads::Threads
)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(btrfs-backup-gnome-restore PRIVATE -Wall -Wextra -Wpedantic)
endif()
add_custom_target(btrfs-backup-gnome ALL
    DEPENDS btrfs-backup-gnome-extension btrfs-backup-gnome-restore)

configure_file(btrfs-backup-gnome.in btrfs-backup-gnome @ONLY)
configure_file("${PROJECT_SOURCE_DIR}/data/dbus/io.github.btrfsbackup.Manager1.xml"
    Manager1.xml COPYONLY)
install(TARGETS btrfs-backup-gnome-restore RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/btrfs-backup-gnome"
    DESTINATION "${CMAKE_INSTALL_BINDIR}")
install(DIRECTORY app/ DESTINATION "${CMAKE_INSTALL_DATADIR}/btrfs-backup/gnome"
    FILES_MATCHING PATTERN "*.py" PATTERN "__pycache__" EXCLUDE)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/Manager1.xml"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/btrfs-backup/gnome")
install(FILES io.github.btrfsbackup.Gnome.desktop DESTINATION "${CMAKE_INSTALL_DATADIR}/applications")
install(FILES nautilus/previous_versions.py
    DESTINATION "${CMAKE_INSTALL_DATADIR}/nautilus-python/extensions")
install(DIRECTORY "${extension_build}/" DESTINATION "${extension_install}")

if(BUILD_TESTING)
    find_program(DBUS_RUN_SESSION dbus-run-session REQUIRED)
    add_test(NAME gnome-model-contract COMMAND "${GJS_EXECUTABLE}" -m
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/model.js")
    add_test(NAME gnome-client-contract COMMAND "${DBUS_RUN_SESSION}" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/client.py" "${CMAKE_CURRENT_BINARY_DIR}")
    add_test(NAME gnome-restore-contract COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/restore.py" "$<TARGET_FILE:btrfs-backup-gnome-restore>")
    set_tests_properties(gnome-model-contract gnome-client-contract gnome-restore-contract
        PROPERTIES LABELS gnome TIMEOUT 60 ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    option(BTRFSBACKUP_GNOME_SHELL_TESTS "Run GNOME tests in a disposable headless compositor" OFF)
    if(BTRFSBACKUP_GNOME_SHELL_TESTS)
        find_program(GNOME_SHELL_TEST_TOOL gnome-shell-test-tool REQUIRED)
        add_test(NAME gnome-shell-lifecycle COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/run_shell.py"
            "${CMAKE_CURRENT_BINARY_DIR}/${extension_uuid}.shell-extension.zip")
        set_tests_properties(gnome-shell-lifecycle PROPERTIES LABELS gnome TIMEOUT 60
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1")
    endif()
endif()
