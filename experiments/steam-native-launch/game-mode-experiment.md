# Experimental STL-style Game Mode (MO2 in Skyrim prefix)

This experiment borrows the documented STL Game Mode concept: MO2 is started from a game launch and uses the game's Wine/Proton compatdata prefix, while standalone MO2 uses its own prefix. STL describes Game Mode as running MO2 in the game's compatdata with the same Proton version; its MO2 data is stored in MO2 compatdata and linked into the game's compatdata. This Wyrd experiment is a **minimal direct-Proton test**, not a full STL implementation (no symlink orchestration, installer, Steam launch interception, or runtime integration yet).

Reference: [STL MO2 Game Mode documentation](https://github.com/sonic2kk/steamtinkerlaunch/wiki/Mod-Organizer-2)

## Purpose

Test whether launching MO2 using Skyrim's Proton prefix changes the dialogue voice audio issue. It deliberately uses the already-installed MO2 executable but runs it under the selected Proton and Skyrim AppID prefix.

## Safety

- Branch: `experiment/steam-native-launch` only.
- Does not alter Steam launch options.
- Does not copy or install MO2 into the game prefix.
- Does not delete/reset compatdata or game files.
- **Proton may initialize or update the Skyrim prefix** when this command runs. Back up the prefix first if you want a pristine rollback.
- The script refuses a prefix that doesn't resolve to `.../compatdata/489830` and refuses a present but uninitialized prefix.
- It is experimental; MO2 may not be able to create/use a Skyrim instance correctly because STL's data-linking setup is not replicated.

## Run

From the Wyrd repository checkout:

```bash
bash experiments/steam-native-launch/launch-mo2-game-prefix.sh \
  --proton "/path/to/Proton/proton" \
  --mo2 "/path/to/Wyrd/ModOrganizer.exe" \
  --prefix "/path/to/SteamLibrary/steamapps/compatdata/489830" \
  --steam-root "/path/to/Steam"
```

The script writes a log under `$XDG_STATE_HOME/wyrd/steam-native-launch` (or `~/.local/state/wyrd/steam-native-launch`) and enables Proton logging. It launches MO2 only; once MO2 is open, select Skyrim and use MO2's Run button for the actual game test.

## Next STL parity work

1. Discover how STL links MO2's instance/config data into the game prefix and reproduce only the required Skyrim paths.
2. Add a Wyrd UI option to select the existing Steam Skyrim prefix and Proton build.
3. Capture MO2 and Skyrim process ancestry and environment.
4. Add cleanup/rollback for Wyrd-created links only; never remove unknown files.
5. Compare Steam, Wyrd standalone-prefix, and Wyrd Game Mode voice audio results.


## Ownership-safe link primitive (implemented)

Wyrd now has an isolated helper module, `qt-ui/mo2_game_mode_links.py`, for inspecting, creating, and rolling back MO2 data symlinks. It tracks exact source/target pairs in a Wyrd ownership manifest (`game-mode-links.json`). It refuses to overwrite real targets, foreign symlinks, unreadable manifests, and links that no longer match the recorded target during rollback. Unit tests were added in `qt-ui/tests/test_mo2_game_mode_links.py` for creation/rollback, occupied directories, foreign symlinks, and unowned rollback.


This helper is not yet connected to the GUI or launch script. Tests have been committed but not executed in a local runtime.
