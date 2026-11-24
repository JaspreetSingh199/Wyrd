# Wyrd V1 completion checklist

This document is the handoff ledger for the `v1/Please-god` branch. It deliberately distinguishes implemented source paths from features that still need implementation or real Linux/Proton verification.

## Current baseline

- The MO2 registry tracks installations, instances, games and Proton runtimes independently.
- Discovery is read-only; registration is explicit and metadata-only.
- The UI supports selecting a preferred instance, editing associations, safe unregister, INI backup/restore and storage-path configuration.
- Storage-path configuration updates MO2 INI references; it does **not** move files.
- Skyrim AppID 489830 uses its dedicated Skyrim/SKSE launch path. Other registered games use the generic configured-executable path.
- Launch monitoring attributes processes to a Wyrd launch using process identity, ancestry and/or launch token plus prefix.
- External MO2 installations are read-only. Unregistering registry records never deletes installations or instance data.
- Wyrd-only instance display names are supported; this does not rename MO2 folders or profiles.

## Remaining implementation

### P0 — Installation lifecycle

- [ ] Separate installation manager for registry-managed MO2 installations from the legacy first-run installer.
- [ ] Explicit Install, Update, Repair and Uninstall actions with ownership confirmation.
- [ ] Update through staging and verification; retain a recoverable prior version until the new executable is verified.
- [ ] Repair only Wyrd-owned resources whose ownership markers and canonical paths validate.
- [ ] Uninstall removes only explicitly selected Wyrd-owned application files; never remove shared prefixes, game data, saves, mods, profiles or downloads implicitly.
- [ ] Refuse symlinks, path escapes, ambiguous ownership, active processes and incomplete registry state.

### P0 — Instance lifecycle

- [ ] Create a new MO2 instance using MO2-supported mechanisms and validate its INI, game name and storage paths before registration.
- [ ] Clone an instance with explicit decisions for profiles, mods, downloads, overwrite and cache; never silently share mutable directories.
- [ ] Rename MO2 instance metadata only through a supported MO2 operation. Wyrd's display-name field is not a substitute.
- [ ] Delete/archive only Wyrd-owned instances after a preview, confirmation and dependency check. External instances remain read-only.
- [ ] Reconcile stale registry records without silently adopting unknown files.

### P1 — Storage migration

- [ ] Add a preview/dry-run showing source, destination, estimated bytes, free space and collisions.
- [ ] Copy to a staging destination, verify file inventory and sizes, then update the INI atomically.
- [ ] Preserve the source until the new configuration has been re-opened and verified.
- [ ] Roll back INI changes on failure and provide recovery instructions for interrupted copy operations.
- [ ] Require explicit per-category handling of Mods/Downloads sharing. Never migrate Profiles/Overwrite into another instance's directories.

### P1 — Game and runtime management

- [ ] Re-scan Steam libraries and recover changed game paths without silently changing associations.
- [ ] Improve non-Steam game identity and launch-target editing.
- [ ] Detect unavailable/moved Proton runtimes and offer explicit reassignment.
- [ ] Validate game association against MO2's configured game before every launch.

### P1 — Launch and diagnostics

- [ ] Persist launch history with timestamps, selected instance/game/runtime, wrapper PID identity, target process identities, final state and log location.
- [ ] Provide per-launch diagnostics and a clear failure summary without leaking environment secrets.
- [ ] Add recovery guidance for common missing runtime, prefix, executable and MO2 configuration failures.
- [ ] Confirm monitor behavior when wrappers exit, processes re-parent, and games exit.

### P2 — Product polish

- [ ] Dashboard for installations, instances, games, runtimes, storage health and last launch.
- [ ] Clear ownership/read-only indicators and stale/missing resource states.
- [ ] Search/filter and useful per-instance details.
- [ ] Accessible progress, cancellation semantics and actionable error dialogs.

## Verification gate (deferred until feature work is frozen)

Do not claim V1 complete until the following are exercised on a real Linux desktop with Steam/Proton:

1. Fresh Wyrd-managed install, repeat install, update, repair and uninstall.
2. Multiple MO2 installations and multiple instances across at least two Steam libraries.
3. Existing external MO2 discovery/registration without modifying its files.
4. Skyrim/SKSE launch and audio baseline, plus one non-Skyrim Steam game and one non-Steam game.
5. Wrapper exit, process re-parenting, game exit and relaunch monitoring.
6. Storage dry-run, successful migration, collision refusal, low-space refusal and interrupted-operation recovery.
7. Backup restore and registry-save rollback.
8. Re-run Python tests, Qt smoke tests and CI after the feature freeze.

## Safety invariants

- Never work on `main`; all V1 work stays on `v1/Please-god`.
- Never delete or reset Steam compatdata, game files, saves or external MO2 data as a side effect.
- Never infer ownership from a path name alone. Require validated Wyrd markers and canonical path checks.
- Never report success from a subprocess exit code alone; verify the resulting resource.
- Never implement migration as an INI-only path switch.
- Do not enable tests/CI as a substitute for completing the remaining feature work; run them after feature freeze as requested.
