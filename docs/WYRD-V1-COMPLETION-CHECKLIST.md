# Wyrd V1 completion checklist

This checklist describes the active feature/mo2-environments architecture. It is a work ledger, not a claim that all items have been verified on a real Linux desktop.

## Architecture baseline

- Live read-only filesystem discovery is the source of truth for MO2 instance availability and normal launch readiness.
- MO2 instances can be selected and launched without Wyrd registration.
- Optional legacy registry metadata may support advanced maintenance features, but it must not gate discovery, selection or launch.
- Steam owns game installation and compatdata; Proton owns the compatibility runtime; MO2 owns mod management.
- Skyrim AppID 489830 is the known-good Steam-prefix launch context.
- External MO2 installations and custom storage are read-only unless the user explicitly requests a supported maintenance operation.

## P0 — Discovery and launch

- [ ] Discover Steam libraries and Skyrim SE from manifests.
- [ ] Discover Proton runtimes without silently changing user selection.
- [ ] Discover MO2 instances from supported global and portable layouts.
- [ ] Parse MO2 INI configuration and verify the Skyrim game path through existing Proton drive mappings.
- [ ] Keep the main instance picker, advanced inventory and readiness checklist backed by the same scan result.
- [ ] Preserve MO2's native instance picker when no unique instance is selected.
- [ ] Revalidate the selected instance and configured executable immediately before launch.
- [ ] Confirm Skyrim/SKSE process detection and audio behaviour on a real desktop.

## P1 — Simplification and diagnostics

- [ ] Keep one shared Steam-root discovery implementation.
- [ ] Keep one MO2 instance inventory/parser implementation; adapters may supply prefix and ownership policy.
- [ ] Remove obsolete UI wrappers and dead launch/registration paths after checking external callers.
- [ ] Move environment and launch orchestration out of the main Qt window incrementally.
- [ ] Keep diagnostics tied to a specific launch session and avoid claiming success from wrapper exit status alone.
- [ ] Update all documentation when implementation changes.

## P1 — Maintenance lifecycle

- [ ] Explicit Install, Update, Repair and Uninstall actions with ownership confirmation.
- [ ] Only modify Wyrd-owned resources whose ownership markers and canonical paths validate.
- [ ] Preserve legacy registry/settings files during migration; never silently delete user metadata.
- [ ] External instances remain read-only by default.
- [ ] Storage changes require a preview, explicit confirmation, backup and rollback; an INI-only path switch is not a migration.

## Verification gate

Do not claim V1 complete until exercised on a real Linux desktop with Steam/Proton:

1. Fresh Wyrd setup and repeat application launch.
2. Multiple Steam libraries and multiple MO2 instances.
3. External MO2 discovery and native instance picker.
4. Skyrim/SKSE launch and audio baseline.
5. Wrapper exit, process re-parenting, game exit and relaunch monitoring.
6. Recovery when game, prefix, runtime, instance or executable paths become unavailable.
7. Python unit tests, Qt offscreen smoke tests and release bundle.

## Safety invariants

- Never work on main.
- Never reset or modify Steam compatdata/489830, Skyrim saves or game files without explicit approval.
- Never migrate, copy or symlink saves.
- Never infer ownership from a path name alone.
- Never automatically change the selected Proton runtime.
- Fail closed on symlinks, path escapes, ambiguous ownership or unverified launch targets.
