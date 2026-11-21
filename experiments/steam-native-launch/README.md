# Steam-Native Launch Experiment

**Branch:** `experiment/steam-native-launch`  
**Base:** `main`  
**Status:** Research / diagnostics only

This branch is isolated from Wyrd's stable launch flow. Do not merge into `main` unless the experiment demonstrates a reproducible improvement and passes the existing test suite.

## Goal

Investigate why Skyrim Special Edition NPC dialogue voices are silent when launched through Wyrd's Proton/MO2 handoff, while other audio works. Test whether using Steam's native compatibility launch pipeline changes the result.

## Guardrails

- No changes to `main`.
- Do not delete/reset Steam compatdata, prefixes, game files, saves, MO2 instances, profiles, or settings.
- Preserve the existing Wyrd → MO2 → Skyrim launch path.
- Diagnostic steps should be read-only by default.
- Do not claim a fix until NPC dialogue audio is confirmed in-game.

## Baseline and comparisons

Record these separately:

1. Normal Steam launch: game version, selected Proton, prefix, audio result.
2. Wyrd launch: selected Proton, Wyrd-managed MO2 prefix, audio result.
3. Experimental Steam-native launch/interception: launch route, process ancestry, selected prefix/runtime, audio result.

Compare the actual process environment and ancestry where possible. Avoid guessing which environment variable is missing.

## Success criteria

- Skyrim launches repeatably.
- MO2 integration remains intact.
- NPC dialogue voice audio is audible.
- The experiment identifies a meaningful launch/runtime difference or produces a reproducible test result.

## Next implementation steps

1. Add a non-destructive diagnostic capture for launch metadata and process ancestry.
2. Investigate a Steam-native launch entry/hand-off that keeps Steam responsible for compatibility initialization.
3. Keep interception minimal and reversible; document exact changes and rollback.
4. Test with a cleanly documented baseline and compare results.
5. Run tests and review the diff before proposing anything for `main`.
