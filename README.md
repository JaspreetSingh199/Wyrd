# Wyrd

Wyrd is a focused Qt6 Linux GUI for setting up and launching Skyrim Special Edition through Mod Organizer 2 and Proton.

## Scope

- Detect Skyrim Special Edition installations in Steam libraries.
- Discover compatible Proton runtimes.
- Install and verify official Mod Organizer 2.
- Configure Skyrim-specific MO2 integration safely.
- Launch MO2 and work toward launching Skyrim through MO2.

Wyrd is a standalone project and is not a SteamTinkerLaunch fork in its development direction.

## Safety

Wyrd avoids overwriting user-owned files, MO2 instances, prefixes, and Steam configuration. It uses ownership markers for managed resources and requires user confirmation for destructive actions.

## Status

The Qt6 GUI and setup tooling are under active development. Actual Skyrim/MO2/Proton runtime behavior must be tested on a Linux desktop.

## Development

See [qt-ui/README.md](qt-ui/README.md).
