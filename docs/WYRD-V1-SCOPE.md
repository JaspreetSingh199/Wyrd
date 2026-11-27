# Wyrd V1 scope

Wyrd is a Qt6 desktop application for the Steam/Proton + Skyrim Special Edition + Mod Organizer 2 workflow.

## V1 architecture

- Steam owns game installation, Steam libraries, compatdata and Proton runtimes.
- Proton owns the Windows compatibility environment.
- MO2 owns instances, profiles, mods, downloads, load order and configured executables.
- Wyrd discovers Skyrim, Proton and MO2; verifies the selected environment; provides launch orchestration and diagnostics.
- The live filesystem scan is the source of truth for MO2 instance availability and normal launch readiness. A Wyrd registry record is not required to discover, select or launch an instance.
- Optional Wyrd metadata may retain user preferences and maintenance information, but it must never override a fresh filesystem/Proton verification.

## Discovery and selection

1. Detect Skyrim SE from Steam library manifests.
2. Discover compatible Proton runtimes without silently changing the user's selected runtime.
3. Inspect only the selected Skyrim AppID 489830 Proton prefix and explicitly supplied MO2 installation roots.
4. Discover MO2 instances from supported MO2 layouts and parse their ModOrganizer.ini files read-only.
5. Determine whether an instance targets the detected Skyrim installation using the existing Proton drive mappings; do not trust the game label alone.
6. Let the user select an instance directly from the discovered inventory. Do not require a registration step.
7. Revalidate the selected instance and executable immediately before launch.

Discovery must not recursively scan arbitrary disks or follow symlinks into unknown locations. Custom storage paths are inventory metadata only until explicitly verified.

## Launch and safety

- Use Skyrim's existing Steam AppID 489830 compatdata prefix as the known-good launch baseline.
- Never reset, replace or modify the Steam prefix as part of detection or ordinary launch.
- Never migrate, copy or symlink saves.
- Never overwrite existing MO2 instances, profiles, mods, downloads or custom storage.
- Never automatically change the Proton runtime.
- Fail closed when the game path, prefix mapping, instance or executable cannot be uniquely verified.
- Report process startup separately from confirmed game-process detection.
- Keep the optional XAudio2 compatibility override disabled by default and scoped to Wyrd's launch environment.

## Out of scope

- Reimplementing MO2's instance manager, mod manager or load-order manager.
- Replacing Steam, Proton, MO2 or SKSE.
- Universal automatic modlist installation.
- Unverified automatic adoption or registration of external MO2 data.
- Destructive prefix reset or data migration as a side effect of discovery.
