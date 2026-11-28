# Wyrd V2 — Multi-Game Modding Environment

> V2 direction and scope. This document describes the intended product, visual system, and expansion boundaries. It is a plan, not a claim that multi-game support is already implemented.

## Product direction

Wyrd V1 proved the core workflow with Skyrim Special Edition, Steam, Proton, and Mod Organizer 2 (MO2). V2 expands that foundation into a **multi-game Bethesda modding environment** while keeping MO2 responsible for mod management.

Wyrd should help a user choose a supported game, inspect that game's environment, find its MO2 instances, and launch through the appropriate verified game/runtime context. It should not become a replacement for MO2.

**V2 scope rule:** support Bethesda games that are represented as supported game entries in the MO2 game mapping currently bundled in misc/mo2games.txt. Do not broaden this milestone to non-Bethesda games.

## Supported-game scope

The initial V2 game catalog is limited to these Bethesda titles represented in the repository's MO2 game mapping:

| Game | Steam AppID(s) represented in the mapping |
|---|---:|
| The Elder Scrolls III: Morrowind | 22320 |
| The Elder Scrolls IV: Oblivion | 22330 |
| The Elder Scrolls V: Skyrim (Legendary / classic) | 72850 |
| The Elder Scrolls V: Skyrim Special Edition | 489830 |
| The Elder Scrolls V: Skyrim VR | 611670 |
| Fallout 3 | 22300, 22370 |
| Fallout: New Vegas | 22380 |
| Fallout 4 | 377160 |
| Fallout 4 VR | 611660 |
| Starfield | 1716740 |

The table reflects entries in Wyrd's current misc/mo2games.txt mapping; it is the initial implementation target, not a guarantee that every game/runtime combination has identical MO2 functionality. Game-specific support must be verified against the installed MO2 version and its game plugin/extension.

**Not included in this V2 scope:** non-Bethesda titles in the mapping, unsupported Bethesda titles not present in the mapping, and treating Tale of Two Wastelands (TTW) as a standalone game. TTW is a New Vegas-based total conversion entry, not a separate Bethesda game target.

## Experience and visual direction

V2 should carry forward the visual language in the two approved Wyrd reference screens:

1. **Current functional screen:** retain its readable information hierarchy and compact workflow — game/environment status, Proton/runtime selection, MO2 instance list, profile selection, modded launch, and unmodded launch.
2. **Skyrim-inspired visual concept:** use the stronger game-world presentation — prominent landscape artwork, game emblem, textured/dark panels, Nordic ornamentation, confident serif display headings, warm brass highlights, and a branded left navigation rail.

The intent is to combine the concept's visual confidence with the current screen's practical density. Avoid making the interface so ornamental that important status, paths, profiles, or actions become harder to scan.

### Game-specific artwork and styling

Each supported game should have a distinct visual identity within one consistent Wyrd design system:

- **Hero/banner artwork:** a wide, game-specific landscape or atmospheric scene at the top of that game's workspace.
- **Game emblem or mark:** a compact visual identifier used in the sidebar, game selector, and environment summary.
- **Instance artwork:** optional small game-specific thumbnails or cards that help distinguish instances without replacing their actual MO2 names.
- **Accent palette:** a restrained secondary accent derived from the game's atmosphere. Keep shared surfaces, typography, spacing, borders, and interaction states consistent across games.
- **Game-aware copy:** titles, labels, executable names, paths, and status messages must reflect the selected game rather than retaining Skyrim-specific wording.

Artwork must be replaceable independently of application logic. Prefer local, versioned assets with clear provenance/licensing; provide a neutral fallback when an image is missing. Do not depend on network image downloads for the application to render.

### Shared design system

- Keep the Nordic/Skyrim reference as the visual starting point, not as a Skyrim-only hard-coded theme.
- Use consistent panel geometry, corner treatment, spacing, button heights, icon sizing, and typography across Launch, Maintenance, and future game screens.
- Use brass/gold for primary actions and focus accents; reserve green/amber/red for readiness and warning states.
- Make hover/focus/pressed/disabled states deliberate and consistent. Transitions should be subtle, short, and never block launch or discovery workflows.
- Preserve a compact, information-dense layout. Game artwork should frame the workflow, not push core controls below the fold.
- Ensure text contrast and scaling remain usable at smaller window sizes.

## V2 functional scope

### 1. Game library and selection

- Discover installed supported Bethesda games from Steam libraries.
- Present discovered games in a game selector/library view with game-specific artwork and identity.
- Clearly distinguish detected, missing, unsupported, and ambiguous installations.
- Allow a user to select among multiple installed supported games.
- Keep the selected game explicit; never silently switch games because another installation was found.

### 2. Per-game environment

- Resolve each game's install directory, Steam AppID, compatibility prefix, and compatible Proton runtime context.
- Keep per-game saved selections separate (game installation, Proton choice, MO2 instance, and profile).
- Reuse the V1 safety principle: inspect first, verify before acting, and fail closed when paths or ownership are ambiguous.
- Do not reset or repurpose an existing game's Steam prefix as part of discovery or setup.

### 3. MO2 integration

- Discover MO2 installations and instances relevant to the selected game.
- Show only instances that MO2 identifies/configures for that game; do not imply that every instance can launch every game.
- Preserve MO2 ownership of profiles, mods, downloads, load order, and virtual filesystem.
- Keep instance/profile selection independent per game.
- Verify each configured executable against the selected game's actual installation before enabling launch.

### 4. Launch workflows

- Use a game-specific, verified launch adapter rather than assuming Skyrim's AppID or SteamCompat implementation applies to every title.
- Preserve the known-good Skyrim V1 launch path unchanged while adding and validating other game adapters individually.
- Support modded launch through MO2 where the installed MO2 game support and runtime path are verified.
- Offer an unmodded Steam launch only where a safe, explicit Steam launch target is available.
- Monitor the selected game's process and MO2 session without confusing processes from another supported game.

### 5. Maintenance and diagnostics

- Show diagnostics for the currently selected game and its MO2 environment.
- Keep destructive actions narrowly scoped and ownership-verified.
- Never delete instance data, profiles, mods, downloads, saves, or Steam prefixes as a side effect of changing the selected game or removing Wyrd-managed application files.

## Architecture principles

- **Game catalog:** one metadata source for display name, supported AppIDs, executable identifiers, artwork keys, and theme key.
- **Game discovery adapters:** isolate Steam library and installation detection per game.
- **Environment model:** represent the selected game, install, prefix, runtime, MO2 installation, instance, and profile explicitly.
- **MO2 integration boundary:** keep MO2-specific discovery and launch configuration behind a shared adapter, with game-specific validation where required.
- **Launch adapters:** isolate game-specific Steam/Proton launch details. Do not generalize the Skyrim AppID 489830 launch path into a universal implementation.
- **Presentation layer:** load per-game art and palette from metadata; UI components remain shared and reusable.
- **Incremental rollout:** add one game adapter at a time, validate its discovery and launch path, then mark it supported in the UI.

## Suggested delivery order

1. **Foundation:** define the game catalog and explicit per-game selection/state model without changing V1 Skyrim behaviour.
2. **Visual shell:** introduce the branded game library, game-specific hero artwork, emblems, and restrained accent themes using the approved references.
3. **Discovery:** add read-only Steam discovery for the remaining catalog games and clearly report unsupported/ambiguous cases.
4. **MO2 mapping:** verify per-game MO2 instance association, executable targets, and profile persistence.
5. **Launch adapters:** add and test launch support one game at a time; keep Skyrim's working path as a regression baseline.
6. **Polish:** refine spacing, artwork fallbacks, responsive layout, transitions, and game-specific diagnostics.

## Explicit non-goals

- Supporting every game MO2 can manage; V2 is Bethesda-only.
- Replacing MO2's instance manager, mod list, load order, downloads, or virtual filesystem.
- Automatically installing modlists or migrating user mod setups between games.
- Automatically selecting or changing a Proton runtime without user consent.
- Rewriting the working Skyrim V1 launch integration before an equivalent tested replacement exists.
- Deleting, migrating, copying, or symlinking game saves or Steam prefixes.

## V1 compatibility promise

V1's working Skyrim Special Edition workflow remains the baseline and must not regress while V2 grows. In particular, preserve the existing Skyrim AppID 489830 Steam compatibility context and the known-good qt-ui/steamcompat.py launch integration. Never reset that prefix or touch saves as part of V2 discovery, styling, or game selection.
