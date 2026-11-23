# Wyrd V1 scope

Wyrd is a GUI for managing Steam compatibility and the Skyrim Special Edition + Mod Organizer 2 workflow.

## Core architecture

- Wyrd manages detection, Proton selection, setup, launch orchestration, diagnostics, and compatibility options.
- MO2 manages mods, profiles, load order, modlist content, and SKSE launch configuration.
- Steam/Proton manages the game compatibility runtime and game execution.
- Skyrim's AppID 489830 Steam compatdata prefix is the working baseline for MO2 and Skyrim launches.

## V1 requirements

1. Install MO2 inside Skyrim's Steam compatdata prefix (AppID 489830), not Wyrd's separate MO2 prefix.
2. Detect and verify MO2 installation and startup. Do not report success merely because an installer or launch command returned success. Capture early crashes and useful logs.
3. Mods and SKSE are managed through MO2; Wyrd does not implement mod management.
4. Support MO2-managed modlists as the preferred workflow.
5. Investigate standalone modlist installers that require paths or command-line configuration. Support path configuration where reliable; diagnose failures and allow at most 2–3 retries only for recoverable failures. Never blindly retry potentially destructive or partially completed installs.
6. Preserve Skyrim files, Steam compatdata, saves, MO2 instances, and mod data. No destructive reset without explicit confirmation.
7. Keep an optional XAudio2 compatibility override as a checkbox, disabled by default. Persist the preference and apply it to the Wyrd launch chain only when enabled. The override is `WINEDLLOVERRIDES=xaudio2_7=n,b`.

## Known-good launch baseline

Launching MO2 installed in Skyrim's prefix with:

- `STEAM_COMPAT_DATA_PATH=~/.local/share/Steam/steamapps/compatdata/489830`
- `STEAM_COMPAT_CLIENT_INSTALL_PATH=~/.local/share/Steam`
- `SteamAppId=489830`
- `SteamGameId=489830`

restores full Skyrim audio (music, dialogue, and sound effects). The Wyrd-specific MO2 prefix did not. This is an acceptance-test baseline.

## Out of scope for V1

- Building a mod manager or load-order manager.
- Replacing MO2, SKSE, Steam, or Proton.
- Universal automatic modlist installation.
- Global or unconditional DLL overrides.
- Unverified success states.
