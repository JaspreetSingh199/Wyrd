# Wyrd V1 known limitations and troubleshooting

This guide covers the current V1 scope and the first checks to make when a workflow does not complete. Wyrd's application and launch logs are available from its Logs page.

## Known limitations and scope

- **Linux x86_64:** the published standalone artifact targets x86_64 Linux with glibc. Other architectures and non-Linux platforms are not supported by this release bundle.
- **Steam-owned environment:** Steam must be installed and configured for the current user. Skyrim must be installed through Steam, and its AppID `489830` Proton prefix must already be initialized by launching the game through Steam once.
- **Proton:** Wyrd discovers installed runtimes; it does not download Proton or silently switch the user's selected runtime.
- **MO2:** Wyrd's primary supported setup is its Wyrd-managed MO2 installation in Skyrim's existing Steam prefix. Arbitrary external MO2 layouts and custom storage arrangements are not a V1 compatibility guarantee.
- **MO2 configuration:** a Skyrim Special Edition MO2 instance and at least one valid configured Skyrim/SKSE executable are required for modded launch. Wyrd does not create mod profiles, install SKSE, or configure a user's mod list on their behalf.
- **Mod compatibility:** Wyrd verifies paths and launch configuration; it cannot guarantee that third-party mods, plugins, SKSE versions, or load orders are mutually compatible.
- **Network:** first-run source setup needs package download access. MO2 installation downloads the official MO2 2.5.2 Windows installer.
- **Host libraries:** the standalone build bundles Python and Qt but still relies on compatible host graphics, display, glibc, and system libraries.
- **No save migration:** Wyrd does not copy, move, or symlink Skyrim saves.

## Troubleshooting

### Wyrd does not start from source

1. Run `bash qt-ui/run.sh` from the repository checkout.
2. Confirm Python 3, `venv`, and `pip` are installed.
3. If dependency installation fails, check network access and retry.
4. If Qt reports a missing shared library, install the corresponding host package. See [Dependencies](DEPENDENCIES.md).
5. Run `qt-ui/.venv/bin/python -c 'import PySide6'` to check the Qt binding import.

### Skyrim is not detected

1. Confirm Skyrim Special Edition is installed through Steam.
2. Open Steam and check that the game appears in the Library.
3. Confirm the Steam library's `steamapps/appmanifest_489830.acf` and game directory exist.
4. If Skyrim is in a secondary Steam library, confirm that library is registered in Steam's `steamapps/libraryfolders.vdf`.
5. Refresh Wyrd's environment scan.

Wyrd does not guess a game path when Steam's manifest/library metadata cannot verify it.

### No compatible Proton runtime appears

1. Install a Proton version from Steam's Compatibility/Tools area.
2. For a custom compatibility tool, ensure it is installed in a Steam-discovered `compatibilitytools.d` location.
3. Refresh Wyrd.
4. If the selected runtime is missing or invalid, choose an installed runtime manually. Wyrd does not silently replace the saved selection.

### Skyrim's Steam prefix is missing or uninitialized

Launch Skyrim once through Steam itself, allow the first Proton setup to complete, then exit the game and refresh Wyrd.

Do not delete or reset `steamapps/compatdata/489830` as a troubleshooting shortcut. Wyrd relies on that existing prefix for MO2 integration.

### MO2 is not detected or no Skyrim instance appears

1. Confirm the Wyrd-managed MO2 installation completed and `ModOrganizer.exe` exists.
2. Open MO2 and confirm a Skyrim Special Edition instance exists.
3. Confirm the instance has a Skyrim Special Edition profile.
4. Refresh Wyrd so it rebuilds the live instance inventory.
5. If the instance uses a non-standard storage layout, note that arbitrary external/custom layouts are not guaranteed in V1.

Do not manually move instance folders or change storage paths merely to make discovery pass.

### Skyrim or SKSE is missing from the launch picker

1. Open the selected MO2 instance.
2. In MO2's executable configuration, verify the intended Skyrim or SKSE executable is configured.
3. Confirm SKSE was extracted completely and that `skse64_loader.exe` is a real, non-empty file in the game directory.
4. Confirm the executable's binary path points to the detected Skyrim installation.
5. Close MO2 and refresh Wyrd.

Wyrd only offers configured targets that it can resolve and verify. It does not automatically add SKSE to MO2.

### Steam does not become ready

1. Open Steam manually and wait for the Library/client UI to finish loading.
2. Retry the Wyrd action.
3. If Steam is already running but not ready, allow its WebHelper processes to settle.
4. Review the launch log for the readiness/timeout message.

If Steam is already fully ready and minimized, Wyrd is designed to preserve that window state rather than restore the Steam UI.

### MO2 opens but Skyrim/SKSE does not

1. Check that MO2's selected instance/profile is correct.
2. Confirm the selected executable is configured in MO2 and points to the game directory.
3. Check MO2's own log and Wyrd's Skyrim launch log.
4. Confirm the chosen Proton runtime is still installed.
5. Retry only after the previous launch session has ended.

The Wyrd launch loader waits for process detection; a wrapper starting does not by itself prove the game launched successfully.

### Audio is missing or incorrect

- Check the Wyrd audio compatibility option and retry the same launch path.
- Compare with a direct unmodded Steam launch if needed.
- Review the launch log for the selected audio mode and process diagnostics.

Do not replace the Wyrd SteamCompat launch path with a generic Proton command as an audio workaround.

### Desktop launcher does not appear

1. Run `bash qt-ui/install-desktop-launcher.sh` from the repository.
2. Confirm `~/.local/share/applications/wyrd.desktop` exists.
3. Keep the repository at the same path: the desktop entry launches that checkout's `qt-ui/run.sh`.
4. Refresh/reopen the desktop environment's application menu if needed.

### Standalone executable fails to start

1. Extract the complete ZIP; do not run the executable from inside the archive.
2. Confirm the host is x86_64 Linux with glibc.
3. Run `./Wyrd` from the extracted directory.
4. If the system reports a missing shared library, install the corresponding host package.
5. If it still fails, try the source launcher to distinguish a host runtime issue from an application issue.

## Logs and support information

Use Wyrd's Logs page to inspect application and launch logs. When reporting an issue, include:

- Wyrd version/commit
- Linux distribution and architecture
- Selected Proton runtime
- Whether Steam was already running
- The selected MO2 instance/profile and executable title
- The relevant error text from Wyrd and MO2 logs

Do not post passwords, account tokens, private paths you do not wish to disclose, or save files. Do not delete Steam compatdata or MO2 instance data as a first troubleshooting step.
